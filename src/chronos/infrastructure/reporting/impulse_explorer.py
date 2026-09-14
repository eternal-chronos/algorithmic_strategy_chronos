"""Explorador visual del impulso dominante (§5.3).

Un único HTML autocontenido —datos, Plotly y lógica embebidos— que se abre con
doble clic y funciona sin conexión. Está pensado para una cosa concreta: poner
el gráfico del motor al lado de las capturas de TradingView del propietario y
comparar impulso a impulso.

Cada gráfico dibuja **su** impulso y el de la temporalidad superior que le
corresponda, según el reparto de `ChartsConfig`: el Diario sólo en su gráfico,
sobre H4 el de H4, sobre H1 el de H1 y el de H4, y sobre M15 y M5 los de H1 y
H4 como contexto. El impulso
principal de cada gráfico lleva línea continua, sombreado de limbo y marcadores;
el de contexto va en trazo discontinuo y sin marcadores, para que no compitan.

Las cajas de las zonas **ya no se dibujan**: tapaban el precio y decían de la zona
lo que no decían del ID. En su lugar cada ID lleva su **marco** —las dos
verticales son la vela que lo constituye y la que lo mata, las dos horizontales
su ancla y su extremo—, con el color de su temporalidad. Las zonas siguen en el
payload porque de ellas cuelgan las señales.

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
from chronos.application.structure.pullback import (
    PullbackBox,
    pullback_boxes,
    with_confluence,
)
from chronos.application.structure.zone_odds import TimeframeOdds, zone_odds
from chronos.application.structure.zone_signals import (
    TimeframeSignals,
    detect_zone_signals,
)
from chronos.application.structure.zones import ImpulseZones, TimeframeZones, ZonesRun
from chronos.domain.indicators import rsi, rsi_zones
from chronos.domain.structure.enums import BreakKind, ContactKind, MachineState
from chronos.domain.structure.zone_odds import Tally
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import Zone
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
#: No son zonas del motor —las de la fase 2.0 se calculan y viajan en
#: `impulses`—: son marcas a mano, y por eso llevan colores que no usa ninguna
#: capa calculada. El nombre es lo que se dibuja al lado del rectángulo.
HAND_RECTS: dict[str, str] = {
    "PUL": theme.MAGENTA,
    "UL": theme.CYAN,
    "APUL": theme.OLIVE,
}

#: El RSI que se dibuja bajo el precio en el Diario, H4 y H1, con el periodo y
#: las tres referencias que mira el propietario: 55 arriba, 50 en medio y 45
#: abajo. No es sobrecompra/sobreventa: por encima de 55 hay liquidez alcista,
#: por debajo de 45 bajista, y dentro de la banda el 50 hace de soporte si el
#: índice bajó desde arriba y de resistencia si subió desde abajo. Es DIBUJO
#: —no decide nada, no entra en la detección y no abre ni cierra nada—, pero lo
#: calcula el motor sobre las velas de cada temporalidad y viaja ya leído: el
#: explorador no calcula indicadores ni interpreta zonas.
RSI_PERIOD = 21
RSI_BANDS: tuple[int, ...] = (55, 50, 45)
#: Dónde se mira: en M15 y M5 el propietario no lo usa —ahí rompe en falso— y
#: por eso esos gráficos no lo llevan, ni siquiera apagado.
RSI_CHARTS: tuple[str, ...] = (DAILY, H4, H1)

#: Dos decimales bastan para un índice de 0 a 100 y ahorran megabytes frente a
#: los cuatro del precio.
RSI_DECIMALS = 2

#: La capa de ACUMULACIÓN está APARCADA: el propietario la ha dejado apagada
#: hasta que se revise cómo se cuentan los toques (hoy cuatro velas seguidas
#: rozando el ancla son cuatro toques, sin separación mínima ni tope de
#: barras). Con esto en `False` el payload no lleva ni una y el explorador
#: esconde la casilla; la medición de contactos sigue calculándose igual.
ACCUMULATIONS_ENABLED = False

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
    zones: ZonesRun | None = None,
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(run, max_bars, lateralization, variants, zones)
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
    zones: ZonesRun | None = None,
) -> dict[str, Any]:
    """Serializa la corrida a la estructura que consume el explorador.

    Con `variants` el payload lleva además los impulsos de los otros modos de
    R-36. Las velas van una sola vez —son idénticas en los tres— y lo que se
    repite son los impulsos, que al lado de ocho años de M15 no pesan nada.

    Las zonas de la fase 2.0 viajan **sólo con el modo activo**: se calculan
    sobre los impulsos de esa corrida y superponerlas a los de otro modo
    enseñaría zonas de impulsos que en ese modo no existen. El explorador apaga
    sus capas al cambiar de modo y lo dice en las notas.
    """
    charts = run.config.charts
    contacts = lateralization.per_timeframe if lateralization is not None else {}
    zoned = zones.per_timeframe if zones is not None and zones.enabled else {}
    # Las señales de zona son una lectura de las zonas ya calculadas, sin
    # parámetros ni configuración propia: donde hay zonas hay señales y donde no,
    # no. Por eso se derivan aquí en vez de pedirse por argumento —no habría
    # ninguna corrida en la que tuviera sentido dar unas sin las otras— y el
    # cálculo sigue viviendo en `application`, no en el dibujo.
    signals = detect_zone_signals(run, zones).per_timeframe if zoned else {}
    pullbacks = _pullbacks(run, zoned)
    # La probabilidad de zona cuelga de las señales: cada toque con cómo murió
    # su ID, y las visitas del precio a la franja. Misma regla: sin zonas, nada.
    odds = {
        timeframe: zone_odds(run.analyses[timeframe], zoned[timeframe], signals[timeframe])
        for timeframe in zoned
        if timeframe in signals and timeframe in run.analyses
    }
    bars = {
        chart: _bars_payload(frame, max_bars, with_rsi=chart in RSI_CHARTS)
        for chart, frame in run.chart_bars.items()
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
            #: Fase 2.1: qué regla de rotura produjo estos impulsos.
            "breakByZone": run.config.rules.break_by_zone,
            #: Fase 3.0: con `false` el lado en contra se rompe por el ancla
            #: aunque las zonas manden el lado a favor.
            "breakAgainstByZone": run.config.rules.break_against_by_zone,
            "overlapPriority": run.config.rules.overlap_priority.value,
            "h4OffsetHours": run.config.aggregation.h4_offset_hours,
            "dSessionStart": run.config.aggregation.d_session_start,
            "decimals": DECIMALS,
            #: El RSI del panel de abajo: periodo, referencias y en qué gráficos
            #: va. Sin esto el explorador tendría que escribir un 21 y un 55 que
            #: no ha decidido él.
            "rsiPeriod": RSI_PERIOD,
            "rsiBands": list(RSI_BANDS),
            "rsiCharts": list(RSI_CHARTS),
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
            #: Los recuadros que el propietario planta a mano para marcar un
            #: PUL, un UL o un APUL. Ninguna capa del motor usa estos tonos: con
            #: ellos sólo se dibuja lo que ha puesto una mano.
            "rects": dict(HAND_RECTS),
            #: El RSI por zonas: verde con liquidez alcista (sobre 55), rojo
            #: con bajista (bajo 45) y tinta dentro de la banda. Son los
            #: colores de la dirección del ID: la lectura es la misma.
            "rsi": {"above": BULLISH, "below": BEARISH, "inside": theme.INK_PRIMARY},
            #: La caja del 50 % del retroceso y la acumulación llevan tono
            #: propio: no son zonas ni marcos y no pueden confundirse con ellos.
            "pullback": theme.SERIES[3],
            "accumulation": theme.VIOLET,
            #: El porcentaje escrito sobre cada zona: tinta, ni verde ni rojo,
            #: porque «sigue» es subir en un ID alcista y bajar en uno bajista.
            "odds": theme.INK_SECONDARY,
        },
        "charts": list(available),
        "layout": {chart: list(charts.overlays(chart)) for chart in available},
        "labels": {chart: _label(chart) for chart in {*available, *charts.detected}},
        "spans": _spans(run),
        "bars": bars,
        "impulses": {
            timeframe: _impulse_payload(
                analysis,
                contacts.get(timeframe),
                zoned.get(timeframe),
                signals.get(timeframe),
                pullbacks.get(timeframe, ()),
                odds.get(timeframe),
            )
            for timeframe, analysis in run.analyses.items()
        },
        #: La probabilidad de zona: sin una sola zona con números no hay nada
        #: que escribir y la casilla no se enseña.
        "hasOdds": any(measurement.items for measurement in odds.values()),
        #: La caja del 50 % del retroceso (ancla → PUL) y la acumulación: sin
        #: zonas no hay PUL del que sacar la caja, y sin contactos no hay firma.
        "hasPullbacks": any(pullbacks.values()),
        "hasAccumulations": ACCUMULATIONS_ENABLED
        and any(
            item.signature_index is not None
            for measurement in contacts.values()
            for item in measurement.impulses
        ),
        #: `False` cuando la corrida no llevaba zonas: sin ellas ni la capa de
        #: zonas ni la de señales tienen nada que enseñar.
        "hasZones": bool(zoned),
        #: Lo mismo para la capa de roturas evitadas de la fase 2.1: con la regla
        #: apagada no hay ni una y la casilla no se enseña.
        "hasAvoided": any(analysis.avoided for analysis in run.analyses.values()),
        #: Capa de señales de zona: toques del PUL y rechazos/roturas del UL. Sólo
        #: dibujo, y sólo donde hay zonas. Sin una sola señal la casilla no se
        #: enseña, igual que las demás capas que no pueden pintar nada.
        "hasSignals": any(measurement.items for measurement in signals.values()),
        #: Y para la escalera del extremo (§3.2): sin una sola extensión, todas
        #: las líneas son rectas y no hay ningún salto que marcar.
        "hasSteps": any(
            impulse.extreme_extensions
            for analysis in run.analyses.values()
            for impulse in analysis.published
        ),
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


def _bars_payload(frame: pd.DataFrame, max_bars: int, *, with_rsi: bool) -> dict[str, Any]:
    total = len(frame)
    truncated = 0 < max_bars < total
    # El RSI se calcula sobre el histórico ENTERO y se recorta después. Al revés
    # —calcularlo sobre el tramo ya cortado— las primeras velas que se ven
    # saldrían con un indicador arrancado de cero en ese punto, que es un número
    # distinto del que tiene esa vela de verdad.
    momentum = rsi(frame["close"].to_numpy(dtype=float), RSI_PERIOD) if with_rsi else None
    if truncated:
        frame = frame.iloc[-max_bars:]
        if momentum is not None:
            momentum = momentum[-max_bars:]
    index = pd.DatetimeIndex(frame.index)
    payload: dict[str, Any] = {
        "truncated": truncated,
        "total": total,
        "t": _epoch_minutes(index),
        "o": _round(frame["open"]),
        "h": _round(frame["high"]),
        "l": _round(frame["low"]),
        "c": _round(frame["close"]),
    }
    if momentum is not None:
        zone, origin = rsi_zones(momentum, upper=max(RSI_BANDS), lower=min(RSI_BANDS))
        #: Una lectura por vela, alineada con `t`. `None` en las velas del
        #: arranque, que no tienen variaciones suficientes: es un HUECO y se
        #: dibuja como tal, no como un cero. `rz` es la zona (1 arriba, 0 en la
        #: banda, -1 abajo) y `rf` de qué lado entró en la banda (1 desde
        #: arriba → el 50 hace de soporte; -1 desde abajo → resistencia; 0 sin
        #: haber salido). Las lee el motor: el explorador sólo las pinta.
        payload["rsi"] = _rsi_payload(momentum)
        payload["rz"] = [int(value) for value in zone]
        payload["rf"] = [int(value) for value in origin]
    return payload


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


# --- Impulsos ---------------------------------------------------------------


def _impulse_payload(
    analysis: TimeframeAnalysis,
    measurement: TimeframeLateralization | None,
    zones: TimeframeZones | None = None,
    signals: TimeframeSignals | None = None,
    pullbacks: Sequence[PullbackBox] = (),
    odds: TimeframeOdds | None = None,
) -> dict[str, Any]:
    bars = analysis.bars
    last = pd.Timestamp(pd.DatetimeIndex(bars.index)[-1])
    impulses = [impulse for impulse in analysis.impulses if impulse.publishable]
    return {
        "count": len(impulses),
        "list": _impulse_list(impulses, last),
        "constitutions": _constitutions(impulses, bars),
        #: Velas contrarias que no llegaron a constituir: el ID habría nacido ya
        #: roto. Van con las constituciones porque es donde el propietario busca
        #: el rombo que no está.
        "aborted": _aborted(analysis),
        "breaks": _breaks(analysis),
        #: Fase 2.1. Vacío con la regla apagada, y entonces la casilla no se
        #: enseña: una capa que no puede dibujar nada sólo hace dudar.
        "avoided": _avoided(analysis),
        "limbo": _limbo_regions(analysis),
        "contacts": _contacts(measurement),
        "zones": _zones(zones, analysis, last),
        #: Toques del PUL y rechazos/roturas del UL. Vacío sin zonas.
        "signals": _zone_signals(signals),
        #: La caja del 50 % del retroceso del mentor, con su paso y su
        #: confluencia. Vacío sin zonas: sin PUL no hay caja.
        "pullbacks": _pullback_records(pullbacks, last),
        #: La probabilidad de zona: lo que se sabía al nacer cada zona y las
        #: visitas del precio a su franja; y el total del histórico por ordinal.
        #: Vacío sin zonas.
        "odds": _odds_records(odds, last),
        "oddsTotal": _odds_totals(odds),
        #: Desde qué vela cada ID está en ACUMULACIÓN. Vacío sin contactos.
        "accumulations": _accumulations(measurement, analysis, last),
    }


def _zones(
    zones: TimeframeZones | None, analysis: TimeframeAnalysis, last: pd.Timestamp
) -> list[dict[str, Any]]:
    """Capas "Zona UL" y "Zona PUL" (§8). Puramente visual: no interviene en nada.

    Cada zona viaja con dos tramos horizontales distintos, igual que las líneas
    del ID en B.1: el rectángulo lleno va del **nacimiento** al fin del ID —que
    es exactamente cuando la zona existe— y `xd` marca la vela que la define,
    para poder dibujar hasta ahí un contorno atenuado. Sin esa distinción el
    dibujo diría que la zona existía antes de tiempo.

    El UL es **uno solo** por ID y no se remarca: lo fija la vela del extremo de
    la constitución y no se mueve aunque el extremo se estire después (fase 2.1,
    §3.2). Por eso su rectángulo va entero de la constitución al fin del ID,
    mientras la línea del extremo puede seguir subiendo en escalera por encima.

    La zona del lado en contra cuelga de una vela **anterior** al ID —la del
    extremo del ID de al lado, la que llevaba su UL; o una de dentro del
    retroceso de aquél cuando este ID nació tras una constitución abortada—, así
    que su `xd` queda siempre por detrás de `x0`. Es el PUL o el APUL, nunca las
    dos, y cada ID manda como mucho dos zonas: `k` dice cuál es cada una y `apu`,
    en el APUL, de dónde salió: heredado, del extremo del ID contrario anterior o
    del retroceso.
    """
    if zones is None:
        return []
    ends = _impulse_ends(analysis, last)
    records: list[dict[str, Any]] = []
    for zoned in zones.items:
        # Un UL por ID y sólo uno: no se remarca aunque el extremo se estire.
        records.append(_zone_record(zoned, zoned.last, ends[zoned.id_num]))
        against = zoned.against
        if against is not None:
            record = _zone_record(zoned, against, ends[zoned.id_num])
            origin = zoned.ante_penultimate_origin
            if origin is not None:
                # De dónde salió el APUL: heredado del ID anterior, el UL de aquel
                # ID contrario que dejó su extremo detrás, o el del ID interior de
                # su retroceso. El navegador no lo puede deducir de los bordes, y
                # son tres historias distintas.
                record["apu"] = origin.value
            records.append(record)
    return records


def _zone_record(
    zoned: ImpulseZones,
    zone: Zone,
    end: pd.Timestamp,
) -> dict[str, Any]:
    return {
        "id": zoned.id_num,
        "k": zone.kind.value,
        "d": zoned.direction.value,
        "x0": _minute(pd.Timestamp(zone.ts_birth)),
        "x1": _minute(end),
        "xd": _minute(pd.Timestamp(zone.ts_defining)),
        "lo": round(zone.low, DECIMALS),
        "hi": round(zone.high, DECIMALS),
        "i": round(zone.inner, DECIMALS),
        "o": round(zone.outer, DECIMALS),
        "ext": zone.extended,
        "flat": zone.is_flat,
    }


def _impulse_ends(analysis: TimeframeAnalysis, last: pd.Timestamp) -> dict[int, pd.Timestamp]:
    return {
        impulse.id_num: (
            pd.Timestamp(impulse.ts_end) if impulse.ts_end is not None else last
        )
        for impulse in analysis.impulses
    }


def _zone_signals(signals: TimeframeSignals | None) -> list[dict[str, Any]]:
    """Capa "Señales de zona". Puramente visual: no interviene en nada.

    Las tres señales viajan en una sola lista y el explorador las separa por `k`:
    son marcas del mismo tipo sobre las mismas velas y partirlas en tres arrays
    sólo repetiría los ID y las direcciones.

    `y` es dónde se planta el marcador: el punto de contacto con la zona en el
    toque y en el rechazo —recortado a sus bordes, para que la marca caiga sobre
    la zona y no flotando— y el cierre en la rotura, que por definición queda
    fuera. La cuenta la hace el motor; aquí sólo se elige el campo.

    `src` es la temporalidad de la vela en la que se midió: la del ID salvo en el
    toque del PUL, que se mide en la serie fina y por eso cae **dentro** de la
    vela grande. El replay lo necesita para saber cuándo se supo cada señal: el
    toque, al cerrar su vela fina (M5); el rechazo, al cerrar la de H4.
    """
    if signals is None:
        return []
    return [
        {
            "x": _minute(pd.Timestamp(item.timestamp)),
            "y": round(
                item.signal.close
                if item.kind is ZoneSignalKind.ROTURA_UL
                else item.signal.touch,
                DECIMALS,
            ),
            "k": item.kind.value,
            "z": item.zone.value,
            "id": item.id_num,
            "d": item.direction.value,
            "lvl": round(item.signal.level, DECIMALS),
            "r": round(item.signal.reach, DECIMALS),
            "c": round(item.signal.close, DECIMALS),
            "in": item.signal.inside,
            "n": item.signal.ordinal,
            "src": item.source,
        }
        for item in signals.items
    ]


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

    Con la rotura por zona (fase 2.1) el extremo **se mueve** estando el ID ya
    vigente, así que `e` y `xe` son los del final y con ellos solos la línea se
    dibujaría desde la constitución en un precio al que el mercado todavía no
    había llegado: el mismo lookahead que B.1 evita por el otro lado. Por eso los
    ID estirados llevan además `st`, la escalera entera —un par `[minuto, precio]`
    por tramo, el primero el de la constitución—, y el explorador dibuja un
    escalón por tramo. Los que nunca se estiraron no la llevan: son un solo
    tramo y con `e`/`xe` basta.
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
            #: Qué lleva el ID en su lado EN CONTRA: `PUL`, `APUL` o `linea`
            #: cuando no hay ID anterior del que sacarlo, y en el APUL `ah` dice
            #: cuál de los tres es. Lo decidió el detector al constituirlo y no se
            #: puede re-derivar en el navegador: haría falta la lista de impulsos.
            #: El globo del ID lo dice sin que haya que esperar a la rotura para
            #: enterarse.
            "az": impulse.against_source.value,
            "ah": (
                None
                if impulse.ante_penultimate_origin is None
                else impulse.ante_penultimate_origin.value
            ),
            #: Si el ID sigue VIVO al final del histórico. `x1` es entonces la
            #: última vela y no la de su muerte: el marco llega al presente y
            #: tiene que poder decir por qué en vez de fechar una muerte que no
            #: ha ocurrido.
            "v": impulse.ts_end is None,
            **_extreme_steps(impulse),
        }
        for impulse in impulses
    ]


def _extreme_steps(impulse: Any) -> dict[str, Any]:
    """La escalera del extremo, sólo cuando hubo alguna extensión (§3.2)."""
    steps = impulse.extreme_steps
    if len(steps) < 2:
        return {}
    return {
        "st": [
            [_minute(pd.Timestamp(step.timestamp)), round(step.price, DECIMALS)]
            for step in steps
        ]
    }


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
    """Roturas reales. `src` distingue la fase 2.1: por zona o por línea (§7).

    Con `break_by_zone: false` todas salen con `src = "linea"` y `lvl == ln`, así
    que el explorador dibuja exactamente lo que dibujaba en la fase 1.
    """
    return [
        {
            "x": _minute(pd.Timestamp(event.timestamp)),
            "y": round(event.close, DECIMALS),
            "k": "favor" if event.kind is BreakKind.A_FAVOR else "contra",
            "id": event.broken_id_num,
            "d": event.broken_direction.value,
            "lvl": round(event.level, DECIMALS),
            "next": event.new_leg_direction.value,
            "src": event.level_source.value,
            "ln": round(
                event.line if event.line is not None else event.level, DECIMALS
            ),
        }
        for event in analysis.events
    ]


def _aborted(analysis: TimeframeAnalysis) -> list[dict[str, Any]]:
    """Constituciones que no fueron (§8): la vela contraria que rompe y gira.

    Viaja con el nivel que su cierre ya había dejado atrás y con las dos
    direcciones —la del ID que no nació y la de la pierna que abre—, que es todo
    lo que hace falta para explicar en el globo por qué ahí no hay rombo.
    """
    return [
        {
            "x": _minute(pd.Timestamp(item.timestamp)),
            "y": round(item.close, DECIMALS),
            "d": item.aborted_direction.value,
            "next": item.new_leg_direction.value,
            "lvl": round(item.level, DECIMALS),
            "ln": round(item.line, DECIMALS),
            "src": item.level_source.value,
        }
        for item in analysis.aborted
    ]


def _avoided(analysis: TimeframeAnalysis) -> list[dict[str, Any]]:
    """Capa "Roturas evitadas" (§7): las velas que la zona ha salvado.

    Es lo primero que el propietario quiere auditar, así que viaja con todo lo
    que hace falta para juzgarla en el propio globo: el cierre, la línea que
    cruzó, los dos bordes de la zona que no llegó a atravesar y si además movió
    el extremo del ID. Vacío con `break_by_zone: false`.
    """
    return [
        {
            "x": _minute(pd.Timestamp(item.timestamp)),
            "y": round(item.close, DECIMALS),
            "k": "favor" if item.kind is BreakKind.A_FAVOR else "contra",
            "id": item.id_num,
            "d": item.direction.value,
            "ln": round(item.line, DECIMALS),
            "z": item.zone.value,
            "zi": round(item.zone_inner, DECIMALS),
            "zo": round(item.zone_outer, DECIMALS),
            "ext": item.extended_extreme,
        }
        for item in analysis.avoided
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
    # La regla de rotura va delante del hash: es lo que decide si lo que se está
    # mirando es la línea base de la fase 1 o la de la 2.1, y confundirlas sería
    # auditar una cosa creyendo que se audita la otra.
    rules = run.config.rules
    if not rules.break_by_zone:
        regla = "por línea"
    elif rules.break_against_by_zone:
        regla = "por ZONA (fase 2.1)"
    else:
        regla = "por el UL a favor y por línea del ancla en contra (fase 3.0)"
    return (
        f"{published:,} impulsos · {reparto} · ancla {run.config.rules.anchor_mode.value} · "
        f"arranque de pierna {run.config.rules.leg_start_mode.value} · "
        f"rotura {regla} · hash {run.config_hash}"
    )


def payload_size(payload: dict[str, Any]) -> int:
    """Bytes del JSON embebido. Útil para vigilar que el fichero no se dispare."""
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def bar_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {chart: len(bars["t"]) for chart, bars in payload["bars"].items()}




# --- La caja del 50 % del retroceso ------------------------------------------


def _pullbacks(run: ImpulseRun, zoned: dict[str, TimeframeZones]) -> dict[str, tuple[PullbackBox, ...]]:
    """Las cajas de cada temporalidad con zonas, con la confluencia hacia arriba.

    La confluencia se mide contra la temporalidad detectada inmediatamente
    superior —H1 contra H4, H4 contra el Diario—, que es la cadena que sigue el
    propietario. Los `spans` son los mismos que usa el replay para saber cuándo
    se supo cada cosa: la caja de H4 no se conoce hasta que cierra su vela.
    """
    detected = [tf for tf in run.config.charts.detected if tf in zoned and tf in run.analyses]
    boxes = {tf: pullback_boxes(run.analyses[tf], zoned[tf]) for tf in detected}
    spans = _spans(run)
    for position, timeframe in enumerate(detected):
        if position == 0:
            continue
        upper = detected[position - 1]
        boxes[timeframe] = with_confluence(
            boxes[timeframe],
            boxes[upper],
            lower_span=pd.Timedelta(minutes=spans[timeframe]).to_pytimedelta(),
            upper_span=pd.Timedelta(minutes=spans[upper]).to_pytimedelta(),
        )
    return boxes


def _pullback_records(boxes: Sequence[PullbackBox], last: pd.Timestamp) -> list[dict[str, Any]]:
    """Capa "Caja 50 % del retroceso". Puramente visual: no interviene en nada.

    La caja existe desde la constitución del ID hasta su muerte, igual que sus
    zonas. `xp` y `xm` son las velas en que la mecha llegó al PUL y al 50 %, o
    `None`; `xr` la del punto más lejano del retroceso. Son hechos de la vida
    entera del ID y el replay los esconde hasta que el reloj los alcanza: sin
    eso, una caja recién nacida ya diría hasta dónde va a devolver el precio.
    `pace` es el paso leído de los ID anteriores —rápida espera el PUL, lenta el
    50 %— y `c` la caja de la temporalidad superior dentro de la que cae este
    50 %, cuando cae.
    """
    return [
        {
            "id": box.id_num,
            "d": box.direction.value,
            "x0": _minute(pd.Timestamp(box.ts_constitution)),
            "x1": _minute(pd.Timestamp(box.ts_end) if box.ts_end is not None else last),
            "lo": round(box.low, DECIMALS),
            "hi": round(box.high, DECIMALS),
            "m": round(box.mid, DECIMALS),
            "pul": round(box.pul_inner, DECIMALS),
            "xp": None if box.ts_pul_touch is None else _minute(pd.Timestamp(box.ts_pul_touch)),
            "xm": None if box.ts_mid_touch is None else _minute(pd.Timestamp(box.ts_mid_touch)),
            "leg": [round(box.leg_range, DECIMALS), box.leg_bars],
            "ret": [round(box.retrace_range, DECIMALS), box.retrace_bars],
            "xr": None if box.ts_retrace is None else _minute(pd.Timestamp(box.ts_retrace)),
            "chain": box.chain,
            "pace": None if box.pace is None else box.pace.value,
            "c": (
                None
                if box.confluence is None
                else {
                    "tf": box.confluence.timeframe,
                    "id": box.confluence.id_num,
                    "lo": round(box.confluence.low, DECIMALS),
                    "hi": round(box.confluence.high, DECIMALS),
                    "m": round(box.confluence.mid, DECIMALS),
                }
            ),
            "v": box.ts_end is None,
        }
        for box in boxes
    ]


def _odds_records(odds: TimeframeOdds | None, last: pd.Timestamp) -> list[dict[str, Any]]:
    """Capa "Probabilidad de zona". Puramente visual: no interviene en nada.

    Un registro por zona, con la misma vida que ella —`x0` nace, `x1` muere—.
    `s` es la ESTRUCTURA sabida al nacer: de los toques de zonas del mismo tipo,
    temporalidad y dirección cuyo ID ya había muerto, cuántos acabaron con el
    ID muriendo a favor (`k` de `n`), contando todos los toques (`all`) y sólo
    los primeros (`first`). `p` es el PRECIO a secas: las visitas de la vela de
    esta temporalidad a la franja `[lo, hi]` antes de nacer la zona, viniendo
    del lado por el que este ID la busca (`in`) y del otro (`out`); `k` es
    salir a favor de este ID. Cada celda lleva ya el porcentaje y el intervalo
    de Wilson al 95 %: el explorador no calcula ni una división.
    """
    if odds is None:
        return []
    return [
        {
            "id": item.id_num,
            "k": item.kind.value,
            "d": item.direction.value,
            "x0": _minute(pd.Timestamp(item.ts_birth)),
            "x1": _minute(pd.Timestamp(item.ts_end) if item.ts_end is not None else last),
            "lo": round(item.low, DECIMALS),
            "hi": round(item.high, DECIMALS),
            "s": {"all": _cell(item.known_all), "first": _cell(item.known_first)},
            "p": {"in": _cell(item.price_approach), "out": _cell(item.price_other)},
        }
        for item in odds.items
    ]


def _odds_totals(odds: TimeframeOdds | None) -> list[dict[str, Any]]:
    """El total del histórico por tipo de zona, dirección y ordinal del toque.

    MIRA AL FUTURO respecto a cualquier zona concreta: es la cifra de todo el
    histórico cargado, para auditar la regla, y el explorador lo dice y la
    esconde en replay.
    """
    if odds is None:
        return []
    return [
        {
            "k": group.kind.value,
            "d": group.direction.value,
            "all": _cell(group.all_touches),
            "ord": {bucket: _cell(tally) for bucket, tally in group.by_ordinal.items()},
        }
        for group in odds.totals
    ]


def _cell(tally: Tally) -> dict[str, Any]:
    """`k` de `n`, el porcentaje y el intervalo, o `None` sin casos."""
    share = tally.share
    interval = tally.interval()
    return {
        "n": tally.n,
        "k": tally.k,
        "pct": None if share is None else round(share * 100.0, 1),
        "lo": None if interval is None else round(interval[0] * 100.0, 1),
        "hi": None if interval is None else round(interval[1] * 100.0, 1),
    }


def _accumulations(
    measurement: TimeframeLateralization | None,
    analysis: TimeframeAnalysis,
    last: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Capa "Acumulación". Puramente visual: no interviene en nada.

    Es la firma del propietario —dos toques arriba y dos abajo sin romper
    ninguno— leída en el momento en que se cumple: desde esa vela (`x`) hasta
    que el ID muere (`x1`), el precio está acumulando entre el ancla y el
    extremo. La cuenta la hace la medición de lateralización; aquí sólo se
    fecha el toque que la completa.

    Con `ACCUMULATIONS_ENABLED` en `False` no sale ni una: la capa está
    aparcada y el payload no puede llevar lo que el explorador no debe pintar.
    """
    if measurement is None or not ACCUMULATIONS_ENABLED:
        return []
    stamps = pd.DatetimeIndex(analysis.bars.index)
    ends = _impulse_ends(analysis, last)
    published = {impulse.id_num for impulse in analysis.published}
    records: list[dict[str, Any]] = []
    for item in measurement.impulses:
        onset = item.signature_index
        if onset is None or item.id_num not in published:
            continue
        records.append(
            {
                "id": item.id_num,
                "d": item.direction,
                "x": _minute(pd.Timestamp(stamps[onset])),
                "x1": _minute(ends[item.id_num]),
                "lo": round(item.lower, DECIMALS),
                "hi": round(item.upper, DECIMALS),
                "up": item.touches_upper,
                "dn": item.touches_lower,
            }
        )
    return records
