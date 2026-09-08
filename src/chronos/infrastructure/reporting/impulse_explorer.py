"""Explorador visual del impulso dominante (§5.3).

Un único HTML autocontenido —datos, Plotly y lógica embebidos— que se abre con
doble clic y funciona sin conexión. Está pensado para una cosa concreta: poner
el gráfico del motor al lado de las capturas de TradingView del propietario y
comparar impulso a impulso.

Cada gráfico dibuja **su** impulso y el de la temporalidad superior que le
corresponda, según el reparto de `ChartsConfig`: sobre H4 se ve el de H4 y el
diario, sobre H1 el de H1 y el de H4, y sobre M15 sólo el de H1. El impulso
principal de cada gráfico lleva línea continua, sombreado de limbo y marcadores;
el de contexto va en trazo discontinuo y sin marcadores, para que no compitan.

Las cajas de las zonas **ya no se dibujan**: tapaban el precio y decían de la zona
lo que no decían del ID. En su lugar cada ID lleva su **marco** —las dos
verticales son la vela que lo constituye y la que lo mata, las dos horizontales
su ancla y su extremo—, con el color de su temporalidad. Las zonas siguen en el
payload porque de ellas cuelgan las señales y la cascada.

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

import pandas as pd
import plotly.offline as pyo

from chronos.application.entries.cascade import CascadeMark, CascadeRun
from chronos.application.entries.trades import EntriesRun, Entry
from chronos.application.structure.config import DAILY, H1, H4
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import (
    LateralizationStudy,
    TimeframeLateralization,
)
from chronos.application.structure.zone_signals import (
    TimeframeSignals,
    detect_zone_signals,
)
from chronos.application.structure.zones import ImpulseZones, TimeframeZones, ZonesRun
from chronos.domain.structure.enums import BreakKind, ContactKind, MachineState
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

DECIMALS = 4

#: Con qué cuenta se audita la fase 3.1: 50 $ y el 17 % por operación, que son
#: 8,50 $ de riesgo y 25,50 $ de objetivo a 1:3. Lo fija el propietario y viaja
#: con la corrida que trae operaciones, no con el explorador: en una corrida sin
#: entradas la cuenta es una herramienta de mano y arranca donde arrancaba.
ENTRIES_ACCOUNT: dict[str, Any] = {"initial": 50.0, "mode": "percent", "risk": 17.0}

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
    cascade: CascadeRun | None = None,
    entries: EntriesRun | None = None,
    setup1: bool | None = None,
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(
        run, max_bars, lateralization, variants, zones, cascade, entries, setup1
    )
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
    cascade: CascadeRun | None = None,
    entries: EntriesRun | None = None,
    setup1: bool | None = None,
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
    bars = {
        chart: _bars_payload(frame, max_bars) for chart, frame in run.chart_bars.items()
    }
    # Sólo se ofrecen los gráficos que tienen velas: si el histórico no daba
    # para construir M15, su pestaña no puede quedarse ahí esperando a que
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
            #: El patrón de M15 que afina una entrada cuando dentro de la zona
            #: de H1 no había ninguno: va DETRÁS de ella y con su propio stop,
            #: así que no puede dibujarse con el mismo gris que los de dentro.
            "behind": theme.SERIES[3],
            #: Los recuadros que el propietario planta a mano para marcar un
            #: PUL, un UL o un APUL. Ninguna capa del motor usa estos tonos: con
            #: ellos sólo se dibuja lo que ha puesto una mano.
            "rects": dict(HAND_RECTS),
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
            )
            for timeframe, analysis in run.analyses.items()
        },
        #: `False` cuando la corrida no llevaba zonas. Las cajas de UL y PUL ya no
        #: se dibujan, pero de las zonas cuelgan las señales y la cascada: sin
        #: ellas esas dos capas no tienen nada que enseñar.
        "hasZones": bool(zoned),
        #: Lo mismo para la capa de roturas evitadas de la fase 2.1: con la regla
        #: apagada no hay ni una y la casilla no se enseña.
        "hasAvoided": any(analysis.avoided for analysis in run.analyses.values()),
        #: Capa de señales de zona: toques del PUL y rechazos/roturas del UL. Sólo
        #: dibujo, y sólo donde hay zonas. Sin una sola señal la casilla no se
        #: enseña, igual que las demás capas que no pueden pintar nada.
        "hasSignals": any(measurement.items for measurement in signals.values()),
        #: La cascada Diario → H4 → H1. Va **por gráfico** y no por temporalidad
        #: de ID: un mismo paso mezcla las dos —la confirmación cuelga de un toque
        #: de H4 y habla del ID de H1—, así que no cabe en `impulses`.
        "cascade": _cascade(cascade),
        #: La cadena entera por número de paso, para que una marca pueda contar de
        #: dónde viene aunque su padre se dibuje en otro gráfico.
        "cascadeChain": _cascade_chain(cascade),
        "hasCascade": cascade is not None and not cascade.empty,
        #: Fase 3.1: las operaciones. Van en una sola lista y no por gráfico —una
        #: operación es un tramo de precio y de tiempo, y se dibuja igual en los
        #: cuatro—, con el régimen de H4 aparte porque es el fondo sobre el que
        #: se leen: qué se estaba buscando en cada tramo y por qué.
        "entries": _entries(entries),
        "regimes": _regimes(entries),
        "hasEntries": entries is not None and not entries.empty,
        #: SETUP 1 —la cascada, el ID de H1, lo de M15 y las operaciones—: `True`
        #: encendido, `False` apagado y `None` en las corridas que no hablan de
        #: setups (las fases 1 y 2). Apagado no basta con que las capas no estén:
        #: el explorador lo tiene que DECIR, porque una capa ausente se lee como
        #: que ahí no pasó nada.
        "setup1": setup1,
        #: El R:R con el que se calcularon. El explorador lo escribe, no lo elige.
        "riskReward": entries.risk_reward if entries is not None else None,
        #: La holgura del límite del rechazo con OB: ese límite cae FUERA del
        #: patrón dibujado y sin este número no hay forma de auditarlo.
        "rejectionOffset": entries.rejection_offset if entries is not None else None,
        #: El cierre del viernes. Una operación que acaba sin tocar el stop ni el
        #: objetivo no se entiende sin saber a qué hora cierra la semana.
        "marketWeek": _market_week(entries),
        #: Y con qué cuenta se miran. Va aquí y no incrustado en el JavaScript
        #: porque es una decisión de esta fase, no del explorador.
        **({} if entries is None else {"account": dict(ENTRIES_ACCOUNT)}),
        #: La franja de operativa con la que se calculó la cascada. Sin ella un
        #: gráfico sin marcas de madrugada se leería como que la regla no
        #: encontró nada, cuando lo que pasa es que ahí no se mira.
        "tradingWindow": _trading_window(cascade),
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


def _bars_payload(frame: pd.DataFrame, max_bars: int) -> dict[str, Any]:
    total = len(frame)
    truncated = 0 < max_bars < total
    if truncated:
        frame = frame.iloc[-max_bars:]
    index = pd.DatetimeIndex(frame.index)
    return {
        "truncated": truncated,
        "total": total,
        "t": _epoch_minutes(index),
        "o": _round(frame["open"]),
        "h": _round(frame["high"]),
        "l": _round(frame["low"]),
        "c": _round(frame["close"]),
    }


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
    toque, al cerrar su vela de M15; el rechazo, al cerrar la de H4.
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


def _cascade(cascade: CascadeRun | None) -> dict[str, list[dict[str, Any]]]:
    """Capa "Cascada de entrada". Puramente visual: no abre ni cierra nada.

    Cada marca va en el gráfico en el que se mira ese paso —el toque diario en el
    Diario, el de H4 en H4, el PUL del ID de H1 en H1— porque es donde el
    propietario la busca. Van juntas en una lista por gráfico y el explorador las
    separa por `k`, igual que las señales de zona.

    `y` es el punto de contacto con la zona en los toques y el cierre de la vela
    en lo que ocurre en H1: son marcas sobre velas, no sobre niveles.
    """
    if cascade is None or cascade.empty:
        return {}
    return {
        chart: [_cascade_mark(mark) for mark in marks]
        for chart, marks in cascade.per_chart().items()
    }


def _cascade_mark(mark: CascadeMark) -> dict[str, Any]:
    record: dict[str, Any] = {
        "x": _minute(pd.Timestamp(mark.timestamp)),
        "y": round(mark.price, DECIMALS),
        "k": mark.step.value,
        "d": mark.direction.value,
        "tf": mark.timeframe,
        "id": mark.id_num,
        "src": mark.source,
        "seq": mark.seq,
        "lvl": round(mark.level, DECIMALS),
        "r": round(mark.reach, DECIMALS),
        "c": round(mark.close, DECIMALS),
    }
    if mark.parent is not None:
        record["p"] = mark.parent
    if mark.low is not None and mark.high is not None:
        record["lo"] = round(mark.low, DECIMALS)
        record["hi"] = round(mark.high, DECIMALS)
    if mark.window_end is not None:
        record["end"] = _minute(pd.Timestamp(mark.window_end))
    if mark.window_reason is not None:
        # Por qué se cerró la ventana. Es la regla que el propietario audita:
        # sin esto, «hasta aquí» no dice si fue el PUL, el UL o el ID.
        record["why"] = mark.window_reason.value
    if mark.zone_kind is not None:
        # Qué zona es la del paso. Viaja con la marca para que el globo no tenga
        # que suponerla a partir del nombre del paso.
        record["zk"] = mark.zone_kind.value
    if mark.zone_start is not None:
        # La zona del PUL de H1 se dibuja sobre la vela del extremo anterior, que
        # queda detrás de la marca: sin este extremo no hay rectángulo.
        record["x0"] = _minute(pd.Timestamp(mark.zone_start))
    return record


def _entries(entries: EntriesRun | None) -> list[dict[str, Any]]:
    """Capa "Entradas": el límite, su patrón de M15 y la operación que salió.

    Cada registro lleva las tres cosas que se auditan por separado y que el
    navegador no puede deducir: **dónde** estaba el límite (`e`, `s`, `t`),
    **cuándo** se puso y cuándo se fue (`x`, `xf`, `xc`) y **de qué cuelga** —la
    zona de H1 y el patrón de M15—. Lo que sí se deriva en el navegador es el
    dinero: depende del capital que haya escrito en la barra, no del motor.
    """
    if entries is None or not entries.entries:
        return []
    return [_entry_record(item) for item in entries.entries]


def _entry_record(item: Entry) -> dict[str, Any]:
    record: dict[str, Any] = {
        "n": item.seq,
        "d": item.direction.value,
        "f": item.form.value,
        "pat": item.pattern.value,
        "h4": item.h4_id,
        "h1": item.h1_id,
        "zk": item.zone_kind.value,
        "zlo": round(item.zone_low, DECIMALS),
        "zhi": round(item.zone_high, DECIMALS),
        "plo": round(item.pattern_low, DECIMALS),
        "phi": round(item.pattern_high, DECIMALS),
        #: Vela que define el patrón y vela en cuyo cierre se supo que existía.
        "xp": _minute(pd.Timestamp(item.ts_pattern)),
        "xk": _minute(pd.Timestamp(item.ts_pattern_known)),
        #: Vela de M15 en cuyo cierre se puso el límite: antes de ella no hay
        #: nada que dibujar, y el replay lo necesita para no adelantarse.
        "x": _minute(pd.Timestamp(item.ts_armed)),
        "e": round(item.entry, DECIMALS),
        "s": round(item.stop, DECIMALS),
        "t": round(item.target, DECIMALS),
    }
    if item.ts_filled is not None:
        record["xf"] = _minute(pd.Timestamp(item.ts_filled))
    if item.ts_closed is not None:
        record["xe"] = _minute(pd.Timestamp(item.ts_closed))
    if item.exit_price is not None:
        record["px"] = round(item.exit_price, DECIMALS)
    if item.outcome is not None:
        record["o"] = item.outcome.value
    if item.cancelled_by is not None and item.ts_cancelled is not None:
        record["why"] = item.cancelled_by.value
        record["xc"] = _minute(pd.Timestamp(item.ts_cancelled))
    return record


def _regimes(entries: EntriesRun | None) -> list[dict[str, Any]]:
    """Capa "Régimen de H4": qué se busca en cada tramo, y entre qué dos sitios.

    Es el fondo de la fase: una operación de compra en un tramo en el que se
    buscaban ventas es un error, y sin dibujar el tramo no hay forma de verlo.
    """
    if entries is None or not entries.regimes:
        return []
    return [
        {
            "seq": item.seq,
            "k": item.kind.value,
            #: Lo que se busca. Ausente en el tramo mudo del UL.
            **({} if item.direction is None else {"d": item.direction.value}),
            "h4": item.h4_id,
            "h4d": item.h4_direction.value,
            "x0": _minute(pd.Timestamp(item.start)),
            **(
                {}
                if item.end is None
                else {"x1": _minute(pd.Timestamp(item.end))}
            ),
            "a": item.opened_by.value,
            **({} if item.closed_by is None else {"b": item.closed_by.value}),
            "zlo": round(item.zone_low, DECIMALS),
            "zhi": round(item.zone_high, DECIMALS),
            "ulo": round(item.last_low, DECIMALS),
            "uhi": round(item.last_high, DECIMALS),
        }
        for item in entries.regimes
    ]


def _market_week(entries: EntriesRun | None) -> dict[str, str]:
    """El cierre semanal con el que se corrió, como dato y no como texto.

    Es la única regla de la fase que cierra una operación sin que el precio haya
    llegado a ningún sitio: sin escribir la hora, el punto de salida parece un
    fallo del motor.
    """
    if entries is None:
        return {}
    week = entries.market_week
    return {
        "tz": week.timezone,
        "at": f"{week.close.hour:02d}:{week.close.minute:02d}",
    }


def _trading_window(cascade: CascadeRun | None) -> dict[str, str]:
    """La franja en la que se opera, tal cual la usó la cascada.

    Va como dato y no como texto ya montado, igual que el resto del payload: el
    explorador la escribe donde toca.
    """
    if cascade is None:
        return {}
    window = cascade.window
    return {
        "tz": window.timezone,
        "from": f"{window.start.hour:02d}:{window.start.minute:02d}",
        "to": f"{window.end.hour:02d}:{window.end.minute:02d}",
    }


def _cascade_chain(cascade: CascadeRun | None) -> dict[str, list[Any]]:
    """Paso → `[minuto, tipo, temporalidad del ID, nº de ID]`, para remontar el árbol."""
    if cascade is None or cascade.empty:
        return {}
    return {
        str(mark.seq): [
            _minute(pd.Timestamp(mark.timestamp)),
            mark.step.value,
            mark.timeframe,
            mark.id_num,
        ]
        for mark in cascade.marks
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


