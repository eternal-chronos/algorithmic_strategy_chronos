"""Explorador visual del impulso dominante (§5.3).

Un único HTML autocontenido —datos, Plotly y lógica embebidos— que se abre con
doble clic y funciona sin conexión. Está pensado para una cosa concreta: poner
el gráfico del motor al lado de las capturas de TradingView del propietario y
comparar impulso a impulso.

Cada gráfico dibuja **su** impulso y el de la temporalidad superior que le
corresponda, según el reparto de `ChartsConfig`: el Diario sólo en su gráfico,
sobre H4 el de H4, y sobre H1, M15 y M5 el de H4 como contexto. El impulso
principal de cada gráfico lleva línea continua, sombreado de limbo y marcadores;
el de contexto va en trazo discontinuo y sin marcadores, para que no compitan.

Cada ID lleva su **marco** —las dos verticales son la vela que lo constituye y
la que lo mata, las dos horizontales su ancla y su extremo—, con el color de su
temporalidad.

Del payload sale todo lo que se puede derivar en el navegador: las etiquetas de
los puntos se componen en JavaScript y las marcas de tiempo viajan como minutos
desde la época. Con ocho años de M15 —doscientas mil velas— la diferencia entre
hacerlo así y mandar el texto ya montado son decenas de megabytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.offline as pyo

from chronos.application.structure.config import DAILY, H1, H4
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import (
    LateralizationStudy,
    TimeframeLateralization,
)
from chronos.domain.indicators import rsi
from chronos.domain.structure.enums import BreakKind, ContactKind, MachineState
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting import theme
from chronos.infrastructure.reporting.timezones import session_label

ASSETS = Path(__file__).parent / "assets"
_MARKER = re.compile(r"__[A-Z][A-Z_]*__")

#: §5.3 fija los colores: verde alcista, rojo bajista. Se toman de la paleta del
#: proyecto para no introducir hexadecimales sueltos.
BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE
LIMBO_FILL = theme.INK_MUTED

#: El MARCO de cada ID va por TEMPORALIDAD y no por dirección: en el mismo
#: gráfico hay marcos de dos —el de H4 sobre el gráfico de H1, el de H1 sobre
#: M15— y el color es lo que dice de quién es cada uno. La dirección sigue
#: leyéndose en la línea del ID y en el globo.
TIMEFRAME_COLORS: dict[str, str] = {
    DAILY: theme.VIOLET,
    H4: theme.SERIES[1],
    H1: theme.SERIES[0],
}

#: Los recuadros que dibuja el propietario a mano (I.3) y el color de cada uno.
#: No son zonas del motor —el motor no calcula ninguna—: son marcas a mano con
#: las que el propietario señala dónde ve un OB o un FVG, y por eso llevan
#: colores que no usa ninguna capa calculada. El nombre es lo que se dibuja al
#: lado del rectángulo.
HAND_RECTS: dict[str, str] = {
    "OB": theme.MAGENTA,
    "FVG": theme.CYAN,
}

#: Las líneas que traza el propietario a mano (I.4) y el color de cada una. Cada
#: una lleva el nombre de la TEMPORALIDAD que se está marcando con ella —el
#: nivel que se ve en el Diario, en H4 o en H1—, que es lo que se quiere decir
#: al señalarla; el color sólo sirve para distinguirlas en el gráfico.
#:
#: Los tonos son los de la mano —y NO los de `TIMEFRAME_COLORS`, que son los
#: del motor— justamente para que una línea de H4 no se pueda confundir con el
#: marco de H4 que dibuja el motor.
HAND_LINES: dict[str, str] = {
    DAILY: theme.MAGENTA,
    H4: theme.CYAN,
    H1: theme.OLIVE,
}

#: Las SESIONES que marca el motor (J.1): el alto y el bajo de Asia y los de
#: Londres, calculados cada día a las 7:58 del reloj de la pantalla. Un tono por sesión
#: —el alto y el bajo de la misma se distinguen por el nombre escrito al
#: lado— y ninguno de los dos lo usa la mano ni ninguna otra capa del motor.
SESSION_COLORS: dict[str, str] = {
    "asia": theme.BROWN,
    "london": theme.PURPLE,
}

#: El Fibonacci que traza el propietario a mano (I.5). Va en el gris de la tinta
#: secundaria y no en un color: no es una marca de nada —como la línea de un
#: nivel—, es una REGLA con la que medir, y en gris no le quita sitio a ninguna
#: capa ni se confunde con las marcas de la mano. Lo que lo identifica son sus
#: porcentajes escritos al lado.
HAND_FIB = theme.INK_SECONDARY

#: Los porcentajes que se dibujan, en el orden en que van del 0 al 100. Los pone
#: el propietario y viajan en el payload en vez de estar escritos en el
#: JavaScript: son SU lectura del retroceso, no una constante del explorador.
FIB_LEVELS: tuple[int, ...] = (0, 70, 80, 90, 100)

#: El RSI que se dibuja SIEMPRE bajo el precio, con el periodo y las tres
#: referencias que mira el propietario: 55 arriba, 50 en medio y 45 abajo. Es
#: DIBUJO —no decide nada, no entra en la detección y no abre ni cierra nada—,
#: pero lo calcula el motor sobre las velas de cada temporalidad y viaja ya
#: hecho: el explorador no calcula indicadores.
RSI_PERIOD = 21
RSI_BANDS: tuple[int, ...] = (55, 50, 45)

#: Dos decimales bastan para un índice de 0 a 100 y ahorran megabytes frente a
#: los cuatro del precio.
RSI_DECIMALS = 2

DECIMALS = 4

#: Minuto cero de la escala de tiempos del explorador.
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


@dataclass(frozen=True, slots=True)
class ModeVariant:
    """Una corrida completa del módulo con otro `LEG_START_MODE` (R-36).

    Las velas son las mismas en los tres modos —el modo no toca la agregación—,
    así que el explorador puede alternarlos sobre el mismo gráfico y el
    propietario compara sin cambiar de fichero ni de escala. Cada variante trae
    su corrida entera: sus impulsos, su limbo y sus contactos. Aquí no se recorta
    ni se recalcula nada, sólo se serializa lo que cada corrida ya produjo.
    """

    run: ImpulseRun
    lateralization: LateralizationStudy | None = None

    @property
    def mode(self) -> str:
        return self.run.config.rules.leg_start_mode.value

    @property
    def label(self) -> str:
        """Etiqueta corta para el botón: `L1`, `L2`, `L3`."""
        return self.mode.split("_")[0].upper()


def render_explorer(
    run: ImpulseRun,
    max_bars: int = 60_000,
    generated_at: datetime | None = None,
    lateralization: LateralizationStudy | None = None,
    variants: Sequence[ModeVariant] = (),
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(run, max_bars, lateralization, variants)
    # El JSON viaja dentro de un <script>: escapar `</` evita que un texto
    # cualquiera pueda cerrar la etiqueta antes de tiempo.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).replace(
        "</", "<\\/"
    )
    replacements = {
        "__TITLE__": f"Impulso dominante · {run.config.symbol}",
        "__HEADER__": f"{run.config.symbol} · impulso dominante · lado {run.config.structure_side}",
        "__SUBTITLE__": _subtitle(run),
        "__GENERATED__": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "__DATA__": data,
        "__PLOTLY__": pyo.get_plotlyjs(),
        "__EXPLORER_JS__": (ASSETS / "impulse_explorer.js").read_text(encoding="utf-8"),
    }
    template = (ASSETS / "impulse_explorer.html").read_text(encoding="utf-8")
    # Sustitución en una sola pasada: el contenido insertado (plotly.js incluido)
    # nunca se reescanea en busca de más marcadores.
    return _MARKER.sub(lambda match: replacements.get(match.group(0), match.group(0)), template)


def build_payload(
    run: ImpulseRun,
    max_bars: int = 60_000,
    lateralization: LateralizationStudy | None = None,
    variants: Sequence[ModeVariant] = (),
) -> dict[str, Any]:
    """Serializa la corrida a la estructura que consume el explorador.

    Con `variants` el payload lleva además los impulsos de los otros modos de
    R-36. Las velas van una sola vez —son idénticas en los tres— y lo que se
    repite son los impulsos, que al lado de ocho años de M15 no pesan nada.
    """
    charts = run.config.charts
    contacts = lateralization.per_timeframe if lateralization is not None else {}
    bars = {
        chart: _bars_payload(frame, max_bars) for chart, frame in run.chart_bars.items()
    }
    # Sólo se ofrecen los gráficos que tienen velas: si el histórico no daba
    # para construir M15 o M5, su pestaña no puede quedarse ahí esperando a que
    # alguien la pulse.
    available = tuple(chart for chart in charts.charts if chart in bars)
    return {
        "meta": {
            "symbol": run.config.symbol,
            "side": run.config.structure_side,
            "configHash": run.config_hash,
            "sessionTimezone": run.config.reporting.session_timezone,
            "sessionTimezoneLabel": session_label(run.config.reporting.session_timezone),
            "anchorMode": run.config.rules.anchor_mode.value,
            "seedMode": run.config.rules.seed_mode.value,
            "dojiBreakMode": run.config.rules.doji_break_mode.value,
            "legStartMode": run.config.rules.leg_start_mode.value,
            "h4OffsetHours": run.config.aggregation.h4_offset_hours,
            "dSessionStart": run.config.aggregation.d_session_start,
            "decimals": DECIMALS,
            #: Los porcentajes del Fibonacci a mano (I.5). El explorador los
            #: dibuja; quién es el 0 y quién el 100 lo dicen los dos clics.
            "fibLevels": list(FIB_LEVELS),
            #: El RSI del panel de abajo: con qué periodo está calculado y qué
            #: tres niveles se marcan. Sin esto el explorador tendría que
            #: escribir un 21 y un 55 que no ha decidido él.
            "rsiPeriod": RSI_PERIOD,
            "rsiBands": list(RSI_BANDS),
        },
        "colors": {
            "bullish": BULLISH,
            "bearish": BEARISH,
            "limbo": LIMBO_FILL,
            "ink": theme.INK_PRIMARY,
            "muted": theme.INK_MUTED,
            "grid": theme.GRIDLINE,
            "surface": theme.SURFACE,
            "font": theme.FONT_FAMILY,
            #: Un tono por temporalidad para el marco del ID: el de H4 sobre H1
            #: y el de H1 sobre M15 tienen que distinguirse de un vistazo.
            "timeframes": dict(TIMEFRAME_COLORS),
            #: Los recuadros que el propietario planta a mano. Ninguna capa del
            #: motor usa estos tonos: con ellos sólo se dibuja lo que ha puesto
            #: una mano.
            "rects": dict(HAND_RECTS),
            #: Las líneas que el propietario traza a mano para señalar lo que
            #: quiere explicar, una por temporalidad. Tonos de la mano, no los
            #: del motor.
            "lines": dict(HAND_LINES),
            #: Un tono por sesión para el alto y el bajo de Asia y de Londres,
            #: que sí son del motor.
            "sessions": dict(SESSION_COLORS),
            #: El Fibonacci a mano: gris de medir, no color de marcar.
            "fib": HAND_FIB,
            #: Y el RSI, que sí es del motor pero vive en su propio panel: no
            #: compite con ninguna capa del precio.
            "rsi": theme.SERIES[0],
        },
        "charts": list(available),
        "layout": {chart: list(charts.overlays(chart)) for chart in available},
        "labels": {chart: _label(chart) for chart in {*available, *charts.detected}},
        "spans": _spans(run),
        "bars": bars,
        "sessions": _sessions_payload(run),
        "impulses": {
            timeframe: _impulse_payload(analysis, contacts.get(timeframe))
            for timeframe, analysis in run.analyses.items()
        },
        "modes": [_mode_summary(variant) for variant in variants],
        # El modo activo ya viaja en `impulses` y repetirlo aquí costaba 4,5 MB
        # de fichero. El explorador lo lee de `impulses` por identidad del modo,
        # no por ausencia de datos: así una variante que faltara daría error en
        # vez de dibujar los impulsos de otro modo sin avisar.
        "byMode": {
            variant.mode: _mode_impulses(variant)
            for variant in variants
            if variant.mode != run.config.rules.leg_start_mode.value
        },
    }


def _mode_impulses(variant: ModeVariant) -> dict[str, Any]:
    contacts = (
        variant.lateralization.per_timeframe if variant.lateralization is not None else {}
    )
    return {
        timeframe: _impulse_payload(analysis, contacts.get(timeframe))
        for timeframe, analysis in variant.run.analyses.items()
    }


def _mode_summary(variant: ModeVariant) -> dict[str, Any]:
    """Lo que el explorador enseña de cada modo sin tener que contar nada.

    Los recuentos salen de la corrida correspondiente: el explorador los muestra,
    no los calcula.
    """
    analyses = variant.run.analyses.values()
    return {
        "id": variant.mode,
        "label": variant.label,
        "name": variant.mode,
        "hash": variant.run.config_hash,
        "impulses": sum(len(analysis.impulses) for analysis in analyses),
        "wrong": sum(
            1
            for analysis in analyses
            for impulse in analysis.impulses
            if impulse.extreme_on_counter_bar
        ),
    }


def _label(timeframe: str) -> str:
    return "Diario" if timeframe == "D" else timeframe


def _spans(run: ImpulseRun) -> dict[str, int]:
    """Duración de la vela de cada temporalidad, en minutos.

    Es lo que le falta al replay para saber **cuándo** se supo cada cosa. Las
    velas van etiquetadas al inicio del intervalo (§1.2), así que la vela de `t`
    no cierra hasta `t + span`: el impulso diario que nace el lunes no puede
    aparecer sobre el gráfico de H4 hasta que el lunes ha terminado, y sin este
    dato el replay lo pintaría veinticuatro horas antes de tiempo.

    Se mide sobre las propias velas —la moda de las diferencias— en vez de
    deducirla del nombre de la temporalidad: con ancla de sesión el diario no
    dura siempre lo mismo y el nombre mentiría.
    """
    frames: dict[str, pd.DataFrame] = {
        timeframe: analysis.bars for timeframe, analysis in run.analyses.items()
    }
    frames.update(run.chart_bars)
    return {
        timeframe: int(_bar_span(pd.DatetimeIndex(frame.index)) // pd.Timedelta(minutes=1))
        for timeframe, frame in frames.items()
    }


# --- Velas ------------------------------------------------------------------


def _bars_payload(frame: pd.DataFrame, max_bars: int) -> dict[str, Any]:
    total = len(frame)
    truncated = 0 < max_bars < total
    # El RSI se calcula sobre el histórico ENTERO y se recorta después. Al revés
    # —calcularlo sobre el tramo ya cortado— las primeras velas que se ven
    # saldrían con un indicador arrancado de cero en ese punto, que es un número
    # distinto del que tiene esa vela de verdad.
    momentum = rsi(frame["close"].to_numpy(dtype=float), RSI_PERIOD)
    if truncated:
        frame = frame.iloc[-max_bars:]
        momentum = momentum[-max_bars:]
    index = pd.DatetimeIndex(frame.index)
    return {
        "truncated": truncated,
        "total": total,
        "t": _epoch_minutes(index),
        "o": _round(frame["open"]),
        "h": _round(frame["high"]),
        "l": _round(frame["low"]),
        "c": _round(frame["close"]),
        #: Una lectura por vela, alineada con `t`. `None` en las velas del
        #: arranque, que no tienen variaciones suficientes: es un HUECO y se
        #: dibuja como tal, no como un cero.
        "rsi": _rsi_payload(momentum),
    }


def _rsi_payload(values: np.ndarray) -> list[float | None]:
    """El RSI listo para JSON: `NaN` no lo es, y `JSON.parse` se atraganta con él."""
    return [
        None if np.isnan(value) else round(float(value), RSI_DECIMALS)
        for value in values
    ]


def _epoch_minutes(index: pd.DatetimeIndex) -> list[int]:
    utc = index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
    return [int(value) for value in ((utc - _EPOCH) // pd.Timedelta(minutes=1)).to_numpy()]


def _minute(stamp: pd.Timestamp) -> int:
    utc = stamp.tz_convert("UTC") if stamp.tzinfo is not None else stamp.tz_localize("UTC")
    return int((utc - _EPOCH) // pd.Timedelta(minutes=1))


def _round(series: pd.Series) -> list[float]:
    return [round(float(value), DECIMALS) for value in series.to_numpy(dtype=float)]


# --- Sesiones ---------------------------------------------------------------


def _sessions_payload(run: ImpulseRun) -> dict[str, Any]:
    """Capa "Asia y Londres" (J.1): una marca por día con sus cuatro niveles.

    Cada marca lleva el minuto en que se pone (`m`, las 7:58 de NY en UTC), hasta
    cuándo se deja (`u`, las 17:00), y por sesión el alto y el bajo con la vela
    que fijó cada uno (`ahx`, `alx`, `lhx`, `llx`): el explorador dibuja
    punteado de esa vela a la marca y continuo de la marca en adelante, igual
    que hace con el ancla y el extremo del ID (B.1). Un nivel sin barras viaja
    como `None` y no se dibuja. Todo viene ya calculado del dominio.
    """
    if run.sessions is None:
        return {"rule": None, "days": []}
    frame = run.sessions
    return {
        "rule": run.session_rule.describe(session_label(run.session_rule.timezone)),
        "days": [
            {
                "m": _optional_minute(row.marked_at),
                "u": _optional_minute(row.until),
                "ah": _level(row.asia_high),
                "al": _level(row.asia_low),
                "ahx": _optional_minute(row.asia_high_at),
                "alx": _optional_minute(row.asia_low_at),
                "lh": _level(row.london_high),
                "ll": _level(row.london_low),
                "lhx": _optional_minute(row.london_high_at),
                "llx": _optional_minute(row.london_low_at),
            }
            for row in frame.itertuples(index=False)
        ],
    }


def _level(value: Any) -> float | None:
    return None if pd.isna(value) else round(float(value), DECIMALS)


def _optional_minute(stamp: Any) -> int | None:
    return None if pd.isna(stamp) else _minute(pd.Timestamp(stamp))


# --- Impulsos ---------------------------------------------------------------


def _impulse_payload(
    analysis: TimeframeAnalysis,
    measurement: TimeframeLateralization | None,
) -> dict[str, Any]:
    bars = analysis.bars
    last = pd.Timestamp(pd.DatetimeIndex(bars.index)[-1])
    impulses = [impulse for impulse in analysis.impulses if impulse.publishable]
    return {
        "count": len(impulses),
        "list": _impulse_list(impulses, last),
        "constitutions": _constitutions(impulses, bars),
        "breaks": _breaks(analysis),
        "limbo": _limbo_regions(analysis),
        "contacts": _contacts(measurement),
    }


def _contacts(measurement: TimeframeLateralization | None) -> list[dict[str, Any]]:
    """Capa "Contactos" (F.2): toques de mecha y roturas fallidas.

    Puramente visual: no interviene en ningún cálculo del módulo. Va apagada por
    defecto porque lo que se audita primero son los impulsos, no sus roces.
    """
    if measurement is None or measurement.contacts.empty:
        return []
    # La rotura real ya tiene su propio marcador: repetirla aquí sería ruido.
    frame = measurement.contacts
    frame = frame[frame["tipo_contacto"] != ContactKind.ROTURA_REAL.value]
    stamps = pd.DatetimeIndex(frame["timestamp"])
    kinds = frame["tipo_contacto"].tolist()
    sides = frame["limite"].tolist()
    ids = frame["id_num"].to_numpy(dtype=int)
    levels = frame["nivel"].to_numpy(dtype=float)
    closes = frame["cierre"].to_numpy(dtype=float)
    return [
        {
            "x": _minute(pd.Timestamp(stamps[position])),
            "y": round(float(levels[position]), DECIMALS),
            "k": "mecha" if kinds[position] == ContactKind.TOQUE_MECHA.value else "fallida",
            "s": sides[position],
            "id": int(ids[position]),
            "c": round(float(closes[position]), DECIMALS),
        }
        for position in range(len(frame))
    ]


def _impulse_list(impulses: list[Any], last: pd.Timestamp) -> list[dict[str, Any]]:
    """Un registro por impulso, con sus dos niveles y su tramo de vigencia.

    El explorador arma con esto las dos líneas horizontales que dibuja el
    propietario a mano. Va como lista y no como arrays planos porque la vista
    filtra por ventana de fechas y necesita descartar impulsos enteros.

    Cada nivel viaja además con la vela que lo **define** (`xa` para el ancla,
    `xe` para el extremo). El explorador dibuja punteado de ahí hasta la
    constitución y sólido a partir de ella (B.1): el nivel existía en el precio
    antes de que el ID existiera, y esa distinción es la que impide que el dibujo
    sugiera que el sistema conocía el nivel antes de tiempo. Son las marcas que
    el propio detector registró al constituir; aquí no se recalcula nada.
    """
    return [
        {
            "id": impulse.id_num,
            "d": impulse.direction.value,
            "x0": _minute(pd.Timestamp(impulse.ts_constitution)),
            "x1": _minute(
                pd.Timestamp(impulse.ts_end) if impulse.ts_end is not None else last
            ),
            "a": round(impulse.anchor, DECIMALS),
            "e": round(impulse.extreme, DECIMALS),
            "xa": _minute(pd.Timestamp(impulse.ts_anchor)),
            "xe": _minute(pd.Timestamp(impulse.ts_extreme)),
            #: Color del cuerpo que fijó el extremo y si contradice la regla del
            #: propietario (alcista -> verde, bajista -> roja). Lo decide el
            #: dominio al constituir; aquí sólo se transporta para poder marcarlo.
            "ec": impulse.extreme_bar_direction.value,
            "w": impulse.extreme_on_counter_bar,
            #: Si el ID sigue VIVO al final del histórico. `x1` es entonces la
            #: última vela y no la de su muerte: el marco llega al presente y
            #: tiene que poder decir por qué en vez de fechar una muerte que no
            #: ha ocurrido.
            "v": impulse.ts_end is None,
        }
        for impulse in impulses
    ]


def _constitutions(impulses: list[Any], bars: pd.DataFrame) -> list[dict[str, Any]]:
    """La vela contraria que da vida al ID: el evento clave del módulo."""
    payload: list[dict[str, Any]] = []
    positions = {stamp: position for position, stamp in enumerate(pd.DatetimeIndex(bars.index))}
    closes = bars["close"].to_numpy(dtype=float)
    for impulse in impulses:
        stamp = pd.Timestamp(impulse.ts_constitution)
        position = positions.get(stamp)
        if position is None:
            continue
        payload.append(
            {
                "id": impulse.id_num,
                "x": _minute(stamp),
                "y": round(float(closes[position]), DECIMALS),
                "d": impulse.direction.value,
                "a": round(impulse.anchor, DECIMALS),
                "e": round(impulse.extreme, DECIMALS),
                "a1": None if impulse.anchor_a1 is None else round(impulse.anchor_a1, DECIMALS),
                "a2": round(impulse.anchor_a2, DECIMALS),
                "body": round(impulse.constituting_body_size, DECIMALS),
                "limbo": impulse.limbo_bars,
            }
        )
    return payload


def _breaks(analysis: TimeframeAnalysis) -> list[dict[str, Any]]:
    """Roturas reales: el cierre que mató al ID y el nivel que cruzó."""
    return [
        {
            "x": _minute(pd.Timestamp(event.timestamp)),
            "y": round(event.close, DECIMALS),
            "k": "favor" if event.kind is BreakKind.A_FAVOR else "contra",
            "id": event.broken_id_num,
            "d": event.broken_direction.value,
            "lvl": round(event.level, DECIMALS),
            "next": event.new_leg_direction.value,
        }
        for event in analysis.events
    ]


def _limbo_regions(analysis: TimeframeAnalysis) -> list[list[int]]:
    """Tramos contiguos sin sesgo definido, como pares [inicio, fin]."""
    span = _bar_span(pd.DatetimeIndex(analysis.bars.index))
    regions: list[list[int]] = []
    start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for state in analysis.states:
        stamp = pd.Timestamp(state.timestamp)
        if state.state is MachineState.LIMBO:
            if start is None:
                start = stamp
            previous = stamp
        elif start is not None and previous is not None:
            regions.append([_minute(start), _minute(previous + span)])
            start = previous = None
    if start is not None and previous is not None:
        regions.append([_minute(start), _minute(previous + span)])
    return regions


def _bar_span(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 2:
        return pd.Timedelta(hours=4)
    deltas = index.to_series().diff().dropna()
    return pd.Timedelta(deltas.mode().iloc[0]) if not deltas.empty else pd.Timedelta(hours=4)


def _subtitle(run: ImpulseRun) -> str:
    charts = run.config.charts
    reparto = " · ".join(
        f"{_label(chart)}: {' + '.join(_label(tf) for tf in charts.overlays(chart))}"
        for chart in charts.charts
        if chart in run.chart_bars
    )
    published = sum(len(analysis.published) for analysis in run.analyses.values())
    return (
        f"{published:,} impulsos · {reparto} · ancla {run.config.rules.anchor_mode.value} · "
        f"arranque de pierna {run.config.rules.leg_start_mode.value} · "
        f"rotura por línea · hash {run.config_hash}"
    )


def payload_size(payload: dict[str, Any]) -> int:
    """Bytes del JSON embebido. Útil para vigilar que el fichero no se dispare."""
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def bar_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {chart: len(bars["t"]) for chart, bars in payload["bars"].items()}


