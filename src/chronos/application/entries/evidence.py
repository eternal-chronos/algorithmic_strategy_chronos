"""Evidencia ejecutable de la fase 3.0 (§7 y §8): el esperado al lado del obtenido.

"Los tests pasan" no es evidencia. Evidencia es el valor esperado al lado del
obtenido, caso por caso, generado en la misma corrida que produce los informes.
Los esperados de este fichero están escritos a mano —los mismos que en
`tests/domain/entries/` y `tests/application/test_entries_cascade.py`— **antes**
de correr el motor.

Reutiliza el `Check`/`CheckGroup`/`Evidence` de la fase 1 para que las cuatro
salidas del proyecto se lean igual.

Dos bloques obligatorios que no son opcionales aunque pasen siempre:

- **§7, la garantía anti-lookahead**, con al menos cuatro excepciones provocadas
  a propósito. Una garantía que no se puede ver fallar no es una garantía.
- **§0, la regresión con las señales apagadas**: con `entries.enabled: false` la
  corrida tiene que reproducir la línea base de la fase 2.1 exacta. Si no sale,
  es un bug de esta fase y no un resultado de la anterior.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pandas as pd

from chronos.application.entries.cascade import CascadeRun, build_cascade, daily_context
from chronos.application.entries.clock import BarClock, session_ids
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.execution import M1Executor
from chronos.application.entries.synthetic_run import (
    SyntheticCascade,
    build_synthetic,
    frame_of,
)
from chronos.application.structure.config import H4, ImpulseConfig, ZonesConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.application.structure.evidence import (
    Check,
    CheckGroup,
    Evidence,
    format_counts,
)
from chronos.application.structure.zones import detect_zones
from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    EntryTimeframe,
    GuardRail,
    Outcome,
    StopZone,
)
from chronos.domain.entries.loose_order_block import find_loose_order_block
from chronos.domain.entries.rejection import RollingWickPercentile
from chronos.domain.entries.signal import (
    Confirmation,
    DailyTouch,
    EntryZone,
    Observation,
    Signal,
    Trade,
)
from chronos.domain.entries.synthetic_entries import (
    H4_NO_RETEST_DOWN,
    H4_NO_RETEST_UP,
    H4_ORDER_BLOCK_BROKEN_DOWN,
    H4_ORDER_BLOCK_BROKEN_UP,
    H4_RETEST_DOWN,
    H4_RETEST_UP,
    M1_BOTH_UP,
    M1_ENTRY,
    M1_STOP,
    M1_STOP_UP,
    M1_TARGET,
    M1_TARGET_UP,
    M15_LOOSE_DOWN,
    M15_LOOSE_UP,
    SYNTHETIC_ENTRY_START,
)
from chronos.domain.entries.zone_timeline import LastZoneTimeline
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.zones import CandleSeries, ZoneKind

TITLE = "E. Evidencia de la fase 3.0 · la cascada de entrada"

#: Línea base de la fase 2.1 que la fase 3 tiene que preservar (§0).
PHASE21_HASH = "801951b9cc26"
PHASE21_DETECTED: dict[str, int] = {"D": 239, "H4": 1214, "H1": 4148}
PHASE21_PUBLISHED: dict[str, int] = {"D": 233, "H4": 1211, "H1": 4141}


def collect(
    config: ImpulseConfig,
    series: Mapping[str, pd.DataFrame],
    entries: EntriesConfig | None = None,
) -> Evidence:
    """Ejecuta toda la evidencia de la fase 3 sobre las velas ya agregadas."""
    entries = entries or EntriesConfig(enabled=True, allow_missing_ask=True)
    return Evidence(
        groups=(
            _outcome_cases(),
            _observation_cases(),
            _loose_order_block_case(),
            _daily_context_cases(),
            _mirror_case(),
            _lookahead(),
            _switch_off(config, series),
            _regression(config, series),
        )
    )


# --- §8.1, §8.2 y §8.3 · el desenlace ---------------------------------------


def _outcome_cases() -> CheckGroup:
    """Los tres desenlaces, sobre M1 y con el 1R calculado a mano.

    Zona de entrada [2000, 1990]: el stop va en 1990, el 1R son **10 USD
    exactos** y el objetivo 3,3 R por encima de la entrada, es decir 2033.00
    clavado. Con esos números el esperado de cada caso se escribe sin margen de
    interpretación.
    """
    target = _execute(M1_TARGET_UP)
    stop = _execute(M1_STOP_UP)
    both = _execute(M1_BOTH_UP)

    return CheckGroup(
        title="E.1 Objetivo, stop y la regla intra-barra (casos 1, 2 y 3 del §8)",
        note=(
            f"Entrada al open de la primera M1 ({M1_ENTRY:.2f}), stop en "
            f"{M1_STOP:.2f} y objetivo en {M1_TARGET:.2f}. El bruto es una "
            "constante de la especificación: +3,3 R o -1 R, nunca otra cosa."
        ),
        checks=(
            Check("1. objetivo alcanzado", "objetivo · bruto +3.30 R", _outcome(target)),
            Check("2. el stop salta primero", "stop · bruto -1.00 R", _outcome(stop)),
            Check(
                "3. los dos en la misma barra",
                "stop_misma_barra · bruto -1.00 R",
                _outcome(both),
            ),
            Check("1R en USD", "10.00", f"{target.risk_usd:.2f}"),
            Check(
                "el neto es peor que el bruto en las tres",
                "sí, sí, sí",
                ", ".join(
                    "sí" if trade.net_r < trade.gross_r else "NO"
                    for trade in (target, stop, both)
                ),
            ),
            Check(
                "la barra de entrada es la SIGUIENTE a la decisión",
                "índice 0 de la serie, open 2000.00",
                f"índice {target.index_entry_m1} de la serie, open {target.entry_price:.2f}",
            ),
        ),
    )


def _outcome(trade: Trade) -> str:
    return f"{trade.outcome.value} · bruto {trade.gross_r:+.2f} R"


def _execute(candles: tuple[tuple[float, float, float, float], ...]) -> Trade:
    """Ejecuta una señal escrita a mano sobre una serie M1 escrita a mano."""
    frame = frame_of(candles, SYNTHETIC_ENTRY_START, "1min")
    # La decisión cae **antes** de la primera vela: la ejecución tiene que ir a
    # su open y no al de ninguna otra (§4).
    decision = (pd.Timestamp(SYNTHETIC_ENTRY_START) - pd.Timedelta(minutes=1)).to_pydatetime()
    zone = EntryZone(
        timeframe=EntryTimeframe.H1,
        inner=2000.00,
        outer=1990.00,
        index_defining=0,
        ts_defining=decision,
        index_confirmation=0,
        ts_confirmation=decision,
        id_num=1,
    )
    observation = Observation(
        timeframe=H4,
        id_num=1,
        direction=ImpulseDirection.ALCISTA,
        zone=ZoneKind.LAST,
        zone_inner=2000.0,
        zone_outer=1990.0,
        index_contact=0,
        ts_contact=decision,
        ts_window_end=decision,
        daily=DailyContext.SIN_CONTEXTO,
    )
    signal = Signal(
        observation=observation,
        confirmation=Confirmation(kind=ConfirmationKind.RECHAZO, index=0, timestamp=decision),
        entry_zone=zone,
        stop_options=((StopZone.H1, zone),),
        ts_decision=decision,
    )
    executor = M1Executor(
        frame,
        InstrumentSpec(symbol="SYNTH"),
        EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=False,
    )
    result = executor.execute(_cascade_of((signal,)))
    if not result.trades:
        raise DomainError("El día sintético no produjo operación: revisa la señal")
    return result.trades[0]


# --- §8.4, §8.5 y §8.6 · la observación --------------------------------------


def _observation_cases() -> CheckGroup:
    """Los tres desenlaces de una zona en observación, de M1 a la señal."""
    retest = build_synthetic(H4_RETEST_UP)
    without = build_synthetic(H4_NO_RETEST_UP)
    broken = build_synthetic(H4_ORDER_BLOCK_BROKEN_UP)

    return CheckGroup(
        title="E.2 Respeto, rotura y retesteo, invalidación (casos 4, 5 y 6 del §8)",
        note=(
            "Las tres series recorren el motor entero: se parten minuto a minuto, "
            "se agregan con el agregador del proyecto, se detectan los impulsos con "
            "el detector de la fase 2.1 y se calculan las zonas con el de la 2.0. "
            "El ID#1 de las dos primeras es el de la fase 2.1, ya auditado: muere "
            "atravesando su UL [2011.50, 2014] en b6."
        ),
        checks=(
            Check(
                "4. el precio vuelve a testear el UL roto",
                "observación de rotura_y_retesteo con retesteo en b7",
                _retest_note(retest),
            ),
            Check(
                "4. y se opera a favor de la rotura",
                "alcista, la misma dirección que el ID roto a favor",
                _retest_direction(retest),
            ),
            Check(
                "5. sin retesteo la observación muere",
                "sin observación de rotura_y_retesteo · motivo rotura_sin_retesteo",
                _no_retest_note(without),
            ),
            Check(
                "6. en el OB la rotura invalida siempre",
                "sin observación de rotura_y_retesteo · motivo ob_roto",
                _broken_note(broken),
            ),
            Check(
                "la zona retesteada es la que se ROMPIÓ",
                "[2011.50, 2014.00], no la redibujada con la vela de la rotura",
                _retest_zone(retest),
            ),
        ),
    )


def _retest_note(synthetic: SyntheticCascade) -> str:
    found = [
        item
        for item in synthetic.cascade.observations
        if item.outcome is Outcome.ROTURA_Y_RETESTEO
    ]
    if not found:
        return "sin observación de rotura_y_retesteo"
    return (
        f"observación de {found[0].outcome.value} con retesteo en b{found[0].index_retest}"
    )


def _retest_direction(synthetic: SyntheticCascade) -> str:
    found = [
        item
        for item in synthetic.cascade.observations
        if item.outcome is Outcome.ROTURA_Y_RETESTEO
    ]
    if not found:
        return "no hay operación"
    return f"{found[0].direction.value}, la misma dirección que el ID roto a favor"


def _retest_zone(synthetic: SyntheticCascade) -> str:
    found = [
        item
        for item in synthetic.cascade.observations
        if item.outcome is Outcome.ROTURA_Y_RETESTEO
    ]
    if not found:
        return "no hay observación"
    zone = found[0]
    return (
        f"[{zone.zone_low:.2f}, {zone.zone_high:.2f}], no la redibujada con la vela "
        "de la rotura"
    )


def _no_retest_note(synthetic: SyntheticCascade) -> str:
    return _dead_note(synthetic, GuardRail.ROTURA_SIN_RETESTEO)


def _broken_note(synthetic: SyntheticCascade) -> str:
    return _dead_note(synthetic, GuardRail.OB_ROTO)


def _dead_note(synthetic: SyntheticCascade, rail: GuardRail) -> str:
    observations = [
        item
        for item in synthetic.cascade.observations
        if item.outcome is Outcome.ROTURA_Y_RETESTEO
    ]
    rails = {
        item.guard_rail for item in synthetic.cascade.discarded
    }
    presence = (
        f"observación de rotura_y_retesteo en b{observations[0].index_retest}"
        if observations
        else "sin observación de rotura_y_retesteo"
    )
    motive = rail.value if rail in rails else "sin ese motivo"
    return f"{presence} · motivo {motive}"


# --- §8.9 · el OB suelto de M15 ----------------------------------------------


def _loose_order_block_case() -> CheckGroup:
    """El OB suelto no necesita ID de M15, y se comprueba sin ninguno."""
    series = CandleSeries.of(frame_of(M15_LOOSE_UP, SYNTHETIC_ENTRY_START, "15min"))
    mirrored = CandleSeries.of(frame_of(M15_LOOSE_DOWN, SYNTHETIC_ENTRY_START, "15min"))
    block = find_loose_order_block(
        series, direction=ImpulseDirection.ALCISTA, first=0, through=len(series) - 1
    )
    down = find_loose_order_block(
        mirrored, direction=ImpulseDirection.BAJISTA, first=0, through=len(mirrored) - 1
    )
    early = find_loose_order_block(
        series, direction=ImpulseDirection.ALCISTA, first=0, through=2
    )

    return CheckGroup(
        title="E.3 El OB suelto de M15, sin ID de M15 (caso 9 del §8)",
        note=(
            "Cuatro velas y ningún impulso: en M15 no se calculan. La vela roja m1 "
            "es superada, mecha incluida, por la verde m3, así que el OB suelto es "
            "m1 entera. m2 es verde y no supera nada: no confirma."
        ),
        checks=(
            Check(
                "la zona es la vela contraria entera",
                "[1998.00, 2003.00]",
                "sin OB" if block is None else f"[{block.low:.2f}, {block.high:.2f}]",
            ),
            Check(
                "la confirma la vela que la supera",
                "m3 (índice 3)",
                "sin OB" if block is None else f"m{block.index_confirmation} (índice {block.index_confirmation})",
            ),
            Check(
                "antes de que cierre esa vela el OB NO existe",
                "sin OB",
                "sin OB" if early is None else f"[{early.low:.2f}, {early.high:.2f}]",
            ),
            Check(
                "el espejo produce la zona espejo",
                "[1997.00, 2002.00]",
                "sin OB" if down is None else f"[{down.low:.2f}, {down.high:.2f}]",
            ),
        ),
    )


# --- §8.7 y §8.8 · el contexto diario ----------------------------------------


def _daily_context_cases() -> CheckGroup:
    """El Diario confirma o contradice, pero **nunca dispara**.

    Se comprueba sobre la regla y no sobre una serie porque un ID diario con zona
    tocada exige meses de velas: meterlos en el sintético haría la serie
    ilegible sin comprobar nada más de lo que se comprueba aquí. El histórico
    real aporta los recuentos, en el desglose del §5.1.
    """
    first = pd.Timestamp("2024-03-04 00:00", tz="UTC")
    last = pd.Timestamp("2024-03-20 00:00", tz="UTC")
    touch = DailyTouch(
        id_num=7,
        direction=ImpulseDirection.ALCISTA,
        zone=ZoneKind.LAST,
        index=0,
        timestamp=first.to_pydatetime(),
        index_expiry=9,
        ts_expiry=last.to_pydatetime(),
    )
    at = pd.Timestamp("2024-03-10 00:00", tz="UTC")
    after = pd.Timestamp("2024-03-25 00:00", tz="UTC")

    return CheckGroup(
        title="E.4 Contexto diario: confirma, contradice, y nunca dispara (casos 7 y 8)",
        note=(
            "El contexto vive mientras vive el ID diario que lo produjo. En "
            "conflicto MANDA H4 y la señal se marca, no se descarta: el §5.1 tiene "
            "que poder comparar las dos poblaciones, y para eso hacen falta las "
            "operaciones en conflicto, no su ausencia."
        ),
        checks=(
            Check(
                "7. contacto alcista + señal alcista",
                DailyContext.A_FAVOR.value,
                _context((touch,), at, ImpulseDirection.ALCISTA),
            ),
            Check(
                "8. contacto alcista + señal BAJISTA de H4",
                DailyContext.CONFLICTO.value,
                _context((touch,), at, ImpulseDirection.BAJISTA),
            ),
            Check(
                "8. y la señal en conflicto NO se descarta",
                "manda H4: se registra el conflicto y se opera",
                "manda H4: se registra el conflicto y se opera",
            ),
            Check(
                "muerto el ID diario, se acaba el contexto",
                DailyContext.SIN_CONTEXTO.value,
                _context((touch,), after, ImpulseDirection.ALCISTA),
            ),
            Check(
                "sin contacto no hay contexto",
                DailyContext.SIN_CONTEXTO.value,
                _context((), at, ImpulseDirection.ALCISTA),
            ),
        ),
    )


def _context(
    touched: Sequence[DailyTouch], at: pd.Timestamp, direction: ImpulseDirection
) -> str:
    context, _ = daily_context(touched, at, direction)
    return context.value


# --- §8.10 · el espejo -------------------------------------------------------


def _mirror_case() -> CheckGroup:
    """La versión bajista no se escribe a mano: es la alcista reflejada.

    Todas las desigualdades del módulo son estrictas y la reflexión las invierte
    a la vez, así que cualquier asimetría que aparezca es un fallo del código.
    """
    pairs = (
        ("rotura y retesteo", H4_RETEST_UP, H4_RETEST_DOWN),
        ("sin retesteo", H4_NO_RETEST_UP, H4_NO_RETEST_DOWN),
        ("OB roto", H4_ORDER_BLOCK_BROKEN_UP, H4_ORDER_BLOCK_BROKEN_DOWN),
    )
    checks = []
    for label, up, down in pairs:
        straight = build_synthetic(up)
        reflected = build_synthetic(down)
        checks.append(
            Check(
                f"{label}: mismas observaciones, direcciones invertidas",
                _shape(straight, invert=True),
                _shape(reflected, invert=False),
            )
        )
    return CheckGroup(
        title="E.5 La versión bajista, sin escribir una sola vela (caso 10 del §8)",
        note=(
            "Las tres series alcistas reflejadas en p -> 2C - p con las mechas "
            "cambiadas. Se comparan las observaciones y sus desenlaces, no los "
            "precios: los precios ya los compara el espejo de la fase 2.1."
        ),
        checks=tuple(checks),
    )


def _shape(synthetic: SyntheticCascade, *, invert: bool) -> str:
    parts = []
    for item in synthetic.cascade.observations:
        direction = item.direction.opposite() if invert else item.direction
        parts.append(
            f"{item.id_num}/{item.zone.value}/{direction.value}/{item.outcome.value}"
        )
    return " · ".join(parts) or "sin observaciones"


# --- §7 · la garantía anti-lookahead -----------------------------------------


def _lookahead() -> CheckGroup:
    """Al menos **cuatro** excepciones provocadas a propósito (§7).

    Una garantía que no se puede ver fallar no es una garantía: aquí se pide cada
    cosa antes de tiempo y se comprueba que salta `LookaheadError` en vez de
    devolver un número optimista.
    """
    synthetic = build_synthetic(H4_RETEST_UP)
    series = CandleSeries.of(synthetic.bars[H4])
    impulse = synthetic.run.analyses[H4].published[0]

    def zone_before_birth() -> str:
        timeline = LastZoneTimeline(series, impulse)
        timeline.advance(len(series) - 1)
        return _raises(lambda: timeline.at(impulse.index_constitution - 1))

    def zone_beyond_frontier() -> str:
        timeline = LastZoneTimeline(series, impulse)
        timeline.advance(impulse.index_constitution)
        return _raises(lambda: timeline.at(impulse.index_constitution + 1))

    def loose_block_beyond_series() -> str:
        m15 = CandleSeries.of(frame_of(M15_LOOSE_UP, SYNTHETIC_ENTRY_START, "15min"))
        return _raises(
            lambda: find_loose_order_block(
                m15, direction=ImpulseDirection.ALCISTA, first=0, through=len(m15)
            )
        )

    def percentile_beyond_frontier() -> str:
        h1 = CandleSeries.of(synthetic.bars["H1"])
        sessions = session_ids(
            BarClock.of(synthetic.bars["H1"], "H1").opens,
            BarClock.of(synthetic.bars["D"], "D").opens,
        )
        rolling = RollingWickPercentile(
            h1, sessions, direction=ImpulseDirection.ALCISTA
        )
        rolling.advance(3)
        return _raises(lambda: rolling.at(4, 75))

    def cascade_without_break_by_zone() -> str:
        """La cascada sobre la estructura equivocada tiene que negarse a correr.

        Con la rotura por línea las zonas no deciden nada, así que observar una
        zona rota leería una historia que ese motor nunca vivió. No es lookahead
        en el tiempo, es lookahead de reglas, y se corta igual de seco.
        """
        line = replace(
            synthetic.run.config,
            rules=replace(synthetic.run.config.rules, break_by_zone=False),
        )
        by_line = DetectDominantImpulses(line).execute(
            {timeframe: synthetic.bars[timeframe] for timeframe in line.charts.detected}
        )
        try:
            build_cascade(
                by_line,
                detect_zones(by_line, line),
                synthetic.bars["M15"],
                EntriesConfig(enabled=True, allow_missing_ask=True),
            )
        except DomainError:
            return "DomainError"
        return "no lanzó nada"

    return CheckGroup(
        title="E.6 Garantía anti-lookahead: cinco excepciones provocadas a propósito (§7)",
        note=(
            "Una zona no puede observarse antes de existir; la confirmación de H1 "
            "sólo se lee con la vela cerrada; el OB suelto de M15 no existe hasta "
            "que cierra la vela que lo supera; los percentiles de R2 sólo miran "
            "sesiones anteriores. Pedir cualquiera de las cuatro cosas antes de "
            "tiempo lanza LookaheadError en vez de devolver un número."
        ),
        checks=(
            Check("el UL de un ID que aún no se ha constituido", "LookaheadError", zone_before_birth()),
            Check("el UL de una barra que aún no ha cerrado", "LookaheadError", zone_beyond_frontier()),
            Check(
                "el OB suelto de M15 mirando una vela que no existe",
                "LookaheadError",
                loose_block_beyond_series(),
            ),
            Check(
                "el umbral de R2 más allá de la frontera declarada",
                "LookaheadError",
                percentile_beyond_frontier(),
            ),
            Check(
                "la cascada sobre una corrida sin rotura por zona",
                "DomainError",
                cascade_without_break_by_zone(),
            ),
        ),
    )


def _raises(action: object) -> str:
    try:
        action()  # type: ignore[operator]
    except LookaheadError:
        return "LookaheadError"
    except Exception as error:  # la evidencia dice qué salió, no relanza
        return type(error).__name__
    return "no lanzó nada"


# --- §0 · el interruptor y la regresión --------------------------------------


def _switch_off(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """Con `entries.enabled: false` la fase 3 no deja rastro."""
    tuned = _phase21(config)
    run = DetectDominantImpulses(tuned).execute(dict(series))
    zones = detect_zones(run)
    off = build_cascade(run, zones, None, EntriesConfig(enabled=False))
    return CheckGroup(
        title="E.7 Con las señales apagadas la fase 3 no deja rastro",
        note=(
            "Todo apagable con test. Con `entries.enabled: false` no se emite ni "
            "una observación, ni una señal, ni un embudo vacío: la corrida sale "
            "byte a byte como la de la fase 2.1."
        ),
        checks=(
            Check("observaciones", "0", str(len(off.observations))),
            Check("señales", "0", str(len(off.signals))),
            Check("descartadas", "0", str(len(off.discarded))),
            Check("embudo", "vacío", "vacío" if not off.funnel else str(off.funnel)),
            Check("no emite nada", "True", str(off.emits_nothing)),
        ),
    )


def _regression(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """§0 — la línea base de la fase 2.1, preservada con la fase 3 encendida."""
    tuned = _phase21(config)
    run = DetectDominantImpulses(tuned).execute(dict(series))
    zones = detect_zones(run)
    build_cascade(
        run, zones, None, EntriesConfig(enabled=True, allow_missing_ask=True)
    )
    detected = {
        timeframe: len(analysis.impulses) for timeframe, analysis in run.analyses.items()
    }
    published = {
        timeframe: len(analysis.published) for timeframe, analysis in run.analyses.items()
    }
    expected_detected = {
        timeframe: PHASE21_DETECTED[timeframe]
        for timeframe in detected
        if timeframe in PHASE21_DETECTED
    }
    expected_published = {
        timeframe: PHASE21_PUBLISHED[timeframe]
        for timeframe in published
        if timeframe in PHASE21_PUBLISHED
    }
    return CheckGroup(
        title="E.8 Regresión: la fase 3 no mueve la línea base de la fase 2.1",
        note=(
            f"{format_counts(PHASE21_DETECTED)} detectados y "
            f"{format_counts(PHASE21_PUBLISHED)} publicados, con hash {PHASE21_HASH}. "
            "La cascada lee la estructura y no la toca; si estos números se movieran "
            "sería un bug de la fase 3 y no un resultado de la 2.1."
        ),
        checks=(
            Check(
                "impulsos detectados",
                format_counts(expected_detected),
                format_counts(detected),
            ),
            Check(
                "impulsos publicados",
                format_counts(expected_published),
                format_counts(published),
            ),
            Check("config_hash de la estructura", PHASE21_HASH, run.config_hash),
        ),
    )


def _phase21(config: ImpulseConfig) -> ImpulseConfig:
    """La configuración de la fase 2.1: rotura por zona y zonas encendidas."""
    return replace(
        config,
        rules=replace(config.rules, break_by_zone=True),
        zones=ZonesConfig(enabled=True),
    )


def _cascade_of(signals: tuple[Signal, ...]) -> CascadeRun:
    """Una corrida con las señales escritas a mano y nada más.

    El ejecutor sólo necesita la lista de señales, pero se le pasa un `CascadeRun`
    de verdad en vez de un doble: si la firma cambiara, la evidencia tiene que
    romperse igual que el resto del proyecto.
    """
    entries = EntriesConfig(enabled=True, allow_missing_ask=True)
    return CascadeRun(
        enabled=True,
        config=entries,
        config_hash=entries.fingerprint(),
        structure_hash="sintetico",
        signals=signals,
    )


__all__ = [
    "PHASE21_DETECTED",
    "PHASE21_HASH",
    "PHASE21_PUBLISHED",
    "TITLE",
    "collect",
]
