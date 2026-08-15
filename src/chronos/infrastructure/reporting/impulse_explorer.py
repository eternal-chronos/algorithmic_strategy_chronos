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

from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.comparison import LostConfirmation
from chronos.application.entries.execution import ExecutionRun
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import (
    LateralizationStudy,
    TimeframeLateralization,
)
from chronos.application.structure.zones import ImpulseZones, TimeframeZones, ZonesRun
from chronos.domain.structure.enums import BreakKind, ContactKind, MachineState
from chronos.domain.structure.zones import Zone
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting import theme

ASSETS = Path(__file__).parent / "assets"
_MARKER = re.compile(r"__[A-Z][A-Z_]*__")

#: §5.3 fija los colores: verde alcista, rojo bajista. Se toman de la paleta del
#: proyecto para no introducir hexadecimales sueltos.
BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE
LIMBO_FILL = theme.INK_MUTED

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
    cascade: CascadeRun | None = None,
    execution: ExecutionRun | None = None,
    lost: Sequence[LostConfirmation] = (),
) -> str:
    """Devuelve el HTML completo del explorador."""
    generated_at = generated_at or SystemClock().now()
    payload = build_payload(
        run, max_bars, lateralization, variants, zones, cascade, execution, lost
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
    execution: ExecutionRun | None = None,
    lost: Sequence[LostConfirmation] = (),
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
            "anchorMode": run.config.rules.anchor_mode.value,
            "seedMode": run.config.rules.seed_mode.value,
            "dojiBreakMode": run.config.rules.doji_break_mode.value,
            "legStartMode": run.config.rules.leg_start_mode.value,
            #: Fase 2.1: qué regla de rotura produjo estos impulsos.
            "breakByZone": run.config.rules.break_by_zone,
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
            #: Fase 3.0 en replay: la operación abierta y la señal todavía en
            #: observación. Van en dos colores propios porque son estados que
            #: NO se pueden confundir con un desenlace: el verde y el rojo están
            #: reservados a lo que ya se sabe cómo acabó.
            "pending": theme.SERIES[3],
            "signal": theme.SERIES[0],
            "grid": theme.GRIDLINE,
            "surface": theme.SURFACE,
            "font": theme.FONT_FAMILY,
        },
        "charts": list(available),
        "layout": {chart: list(charts.overlays(chart)) for chart in available},
        "labels": {chart: _label(chart) for chart in {*available, *charts.detected}},
        "spans": _spans(run),
        "bars": bars,
        "impulses": {
            timeframe: _impulse_payload(analysis, contacts.get(timeframe), zoned.get(timeframe))
            for timeframe, analysis in run.analyses.items()
        },
        #: `False` cuando la corrida no llevaba zonas: el explorador esconde sus
        #: casillas en vez de ofrecer capas que no pueden dibujar nada.
        "hasZones": bool(zoned),
        #: Lo mismo para la capa de roturas evitadas de la fase 2.1: con la regla
        #: apagada no hay ni una y la casilla no se enseña.
        "hasAvoided": any(analysis.avoided for analysis in run.analyses.values()),
        #: Fase 3.0. Las operaciones, las señales descartadas y los rechazos de
        #: las tres definiciones. Van **fuera** de `impulses` a propósito: una
        #: operación es un hecho en el tiempo y en el precio, así que se dibuja
        #: igual sobre las cuatro temporalidades, que es lo que el §10 pide.
        "entries": _entries_payload(cascade, execution, lost),
        "hasEntries": bool(cascade is not None and cascade.enabled),
        #: Fase 3.1: qué vías confirmaron esta corrida y con qué orden, y cuántos
        #: turtle soup hay en TODA la serie de H1. El explorador dibuja los que la
        #: cascada miró; sin el censo, su capa daría a entender que el patrón sólo
        #: ocurre donde hay zona en observación.
        "confirm": (
            {
                "mode": cascade.config.confirm_mode.value,
                "priority": cascade.config.confirm_priority.value,
                "census": dict(cascade.turtle_census),
            }
            if cascade is not None and cascade.enabled
            else None
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


def _entries_payload(
    cascade: CascadeRun | None,
    execution: ExecutionRun | None,
    lost: Sequence[LostConfirmation] = (),
) -> dict[str, Any]:
    """La cascada navegable, con todo lo que la explica.

    Cinco capas y no una: las **operaciones**, las señales **descartadas** con el
    guardarraíl que las mató, los **rechazos** marcados según las tres
    definiciones —que en la 3.1 ya no confirman nada y siguen dibujándose porque
    siguen midiéndose—, los **turtle soup** detectados confirmen o no, y las
    señales que **la 3.0 tomaba y la 3.1 descarta**, con la vía por la que
    confirmaban antes. Las dos últimas son de la fase 3.1 y son lo primero que el
    propietario quiere auditar.

    Cada operación viaja con sus hitos —contacto, rotura, retesteo, confirmación,
    entrada y salida— para que el globo cuente la historia entera sin abrir el
    CSV, y con los tres precios que la deciden: entrada, stop y objetivo.
    """
    empty: dict[str, Any] = {
        "trades": [],
        "discarded": [],
        "rejections": [],
        "turtle": [],
        "lost": [],
    }
    if cascade is None or not cascade.enabled:
        return empty

    trades = execution.trades if execution is not None else ()
    dead = (*cascade.discarded, *(execution.discarded if execution is not None else ()))
    return {
        "trades": [_trade_record(trade) for trade in trades],
        "discarded": [_discarded_record(item) for item in dead],
        "rejections": [
            record
            for signal in cascade.signals
            if (record := _rejection_record(signal)) is not None
        ],
        "turtle": [_turtle_record(item) for item in cascade.turtle_soups],
        "lost": [_lost_record(item) for item in lost],
    }


def _turtle_record(item: Any) -> dict[str, Any]:
    """Un turtle soup mirado por la cascada, con lo que le pasó (fase 3.1).

    `y` es el extremo de la mecha de la primera vela: el nivel que la segunda va
    a buscar y no consigue superar con el cierre. Es el precio sobre el que tiene
    sentido dibujar la marca, y no el cierre ni el centro de la vela.
    """
    return {
        "x": _minute(pd.Timestamp(item.timestamp)),
        "y": round(item.extreme, DECIMALS),
        "d": item.direction.value,
        "r": item.reason.value,
        "id": item.id_num,
        "z": item.zone.value,
    }


def _lost_record(item: Any) -> dict[str, Any]:
    """Una observación que la 3.0 confirmaba y la 3.1 ya no (fase 3.1).

    Se dibuja en el minuto en que la 3.0 CONFIRMABA, que es el instante que el
    propietario quiere mirar: ahí es donde la fase anterior habría entrado.
    """
    return {
        "x": _minute(pd.Timestamp(item.ts_confirmation_v30)),
        "y": round(item.zone_inner, DECIMALS),
        "d": item.direction.value,
        "z": item.zone.value,
        "id": item.id_num,
        "oc": item.outcome.value,
        "v30": item.via_v30.value,
        "rail": None if item.rail_v31 is None else item.rail_v31.value,
        "xc": _minute(pd.Timestamp(item.ts_contact)),
        "zi": round(item.zone_inner, DECIMALS),
        "zo": round(item.zone_outer, DECIMALS),
    }


def _trade_record(trade: Any) -> dict[str, Any]:
    observation = trade.signal.observation
    return {
        "id": observation.id_num,
        "d": observation.direction.value,
        "z": observation.zone.value,
        "oc": observation.outcome.value,
        "dc": observation.daily.value,
        "tf": trade.entry_timeframe.value,
        "sz": trade.stop_zone.value,
        "cf": trade.signal.confirmation.kind.value,
        "xc": _minute(pd.Timestamp(observation.ts_contact)),
        "xk": (
            None if observation.ts_break is None else _minute(pd.Timestamp(observation.ts_break))
        ),
        "xr": (
            None
            if observation.ts_retest is None
            else _minute(pd.Timestamp(observation.ts_retest))
        ),
        "xf": _minute(pd.Timestamp(trade.signal.confirmation.timestamp)),
        "xe": _minute(pd.Timestamp(trade.ts_entry)),
        "xx": None if trade.ts_exit is None else _minute(pd.Timestamp(trade.ts_exit)),
        "pe": round(trade.entry_price, DECIMALS),
        "ps": round(trade.stop_price, DECIMALS),
        "pt": round(trade.target_price, DECIMALS),
        "zi": round(observation.zone_inner, DECIMALS),
        "zo": round(observation.zone_outer, DECIMALS),
        "out": trade.outcome.value,
        "win": trade.outcome.is_win,
        "gr": round(trade.gross_r, 3),
        "nr": round(trade.net_r, 3),
        "ru": round(trade.risk_usd, DECIMALS),
    }


def _discarded_record(item: Any) -> dict[str, Any]:
    observation = item.observation
    return {
        "id": observation.id_num,
        "d": observation.direction.value,
        "z": observation.zone.value,
        "dc": observation.daily.value,
        "rail": item.guard_rail.value,
        "xc": _minute(pd.Timestamp(observation.ts_contact)),
        "x": _minute(pd.Timestamp(item.timestamp)),
        "zi": round(observation.zone_inner, DECIMALS),
        "zo": round(observation.zone_outer, DECIMALS),
        "cf": None if item.confirmation is None else item.confirmation.kind.value,
        #: Cuándo confirmó, no sólo con qué: durante el replay la señal se dibuja
        #: mientras está viva y la confirmación tiene que aparecer en su minuto,
        #: no en el del contacto ni en el de la muerte.
        "xf": (
            None
            if item.confirmation is None
            else _minute(pd.Timestamp(item.confirmation.timestamp))
        ),
    }


def _rejection_record(signal: Any) -> dict[str, Any] | None:
    """Las **tres definiciones marcadas por separado** sobre la vela que confirmó.

    En la fase 3.1 ya no confirman nada: se siguen calculando y se siguen
    dibujando porque se siguen midiendo. Sólo salen las velas que confirmaron:
    son las únicas que la cascada llegó a evaluar, porque en cuanto una vela
    confirma la búsqueda se para.
    """
    if not signal.rejection_marks:
        return None
    return {
        "x": _minute(pd.Timestamp(signal.confirmation.timestamp)),
        "y": round(signal.observation.zone_inner, DECIMALS),
        "id": signal.observation.id_num,
        "d": signal.direction.value,
        "m": dict(signal.rejection_marks),
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
) -> dict[str, Any]:
    bars = analysis.bars
    last = pd.Timestamp(pd.DatetimeIndex(bars.index)[-1])
    impulses = [impulse for impulse in analysis.impulses if impulse.publishable]
    return {
        "count": len(impulses),
        "list": _impulse_list(impulses, last),
        "constitutions": _constitutions(impulses, bars),
        "breaks": _breaks(analysis),
        #: Fase 2.1. Vacío con la regla apagada, y entonces la casilla no se
        #: enseña: una capa que no puede dibujar nada sólo hace dudar.
        "avoided": _avoided(analysis),
        "limbo": _limbo_regions(analysis),
        "contacts": _contacts(measurement),
        "zones": _zones(zones, analysis, last),
        #: Velas de ancla de los ID que murieron sin OB. La zona no existe, pero
        #: el propietario necesita ver dónde estaba la candidata (§8).
        "obCandidates": _candidates(zones, analysis, last),
    }


def _zones(
    zones: TimeframeZones | None, analysis: TimeframeAnalysis, last: pd.Timestamp
) -> list[dict[str, Any]]:
    """Capas "Zonas UL" y "OB" (§8). Puramente visual: no interviene en nada.

    Cada zona viaja con dos tramos horizontales distintos, igual que las líneas
    del ID en B.1: el rectángulo lleno va del **nacimiento** al fin del ID —que
    es exactamente cuando la zona existe— y `xd` marca la vela que la define,
    para poder dibujar hasta ahí un contorno atenuado. Sin esa distinción el
    dibujo diría que la zona existía antes de tiempo.
    """
    if zones is None:
        return []
    ends = _impulse_ends(analysis, last)
    return [
        _zone_record(zoned, zone, ends[zoned.id_num])
        for zoned in zones.items
        for zone in zoned.zones()
    ]


def _zone_record(zoned: ImpulseZones, zone: Zone, end: pd.Timestamp) -> dict[str, Any]:
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
        "xc": (
            None
            if zone.ts_confirmation is None
            else _minute(pd.Timestamp(zone.ts_confirmation))
        ),
    }


def _candidates(
    zones: TimeframeZones | None, analysis: TimeframeAnalysis, last: pd.Timestamp
) -> list[dict[str, Any]]:
    """La vela del ancla de los ID sin OB confirmado, para dibujarla punteada."""
    if zones is None:
        return []
    ends = _impulse_ends(analysis, last)
    anchors = {impulse.id_num: impulse for impulse in analysis.impulses}
    bars = analysis.bars
    positions = {stamp: position for position, stamp in enumerate(pd.DatetimeIndex(bars.index))}
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)

    payload: list[dict[str, Any]] = []
    for zoned in zones.without_order_block:
        impulse = anchors.get(zoned.id_num)
        if impulse is None:
            continue
        position = positions.get(pd.Timestamp(impulse.ts_anchor))
        if position is None:
            continue
        payload.append(
            {
                "id": zoned.id_num,
                "d": zoned.direction.value,
                "xd": _minute(pd.Timestamp(impulse.ts_anchor)),
                "x0": _minute(pd.Timestamp(zoned.ts_constitution)),
                "x1": _minute(ends[zoned.id_num]),
                "lo": round(float(low[position]), DECIMALS),
                "hi": round(float(high[position]), DECIMALS),
            }
        )
    return payload


def _impulse_ends(analysis: TimeframeAnalysis, last: pd.Timestamp) -> dict[int, pd.Timestamp]:
    return {
        impulse.id_num: (
            pd.Timestamp(impulse.ts_end) if impulse.ts_end is not None else last
        )
        for impulse in analysis.impulses
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
    regla = "por ZONA (fase 2.1)" if run.config.rules.break_by_zone else "por línea"
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


