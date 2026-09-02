"""Evidencia ejecutable de la fase 2.1 (§8.4): la rotura del ID por zona.

"Los tests pasan" no es evidencia: evidencia es el valor esperado al lado del
obtenido, para cada caso, generado en la misma corrida que produce los informes.
Los esperados de este fichero están escritos a mano —igual que en
`tests/domain/structure/test_synthetic_break.py`— antes de correr el motor.

Reutiliza el `Check`/`CheckGroup`/`Evidence` de la fase 1 para que las tres
salidas se lean igual.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import pandas as pd

from chronos.application.structure.config import ImpulseConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.application.structure.evidence import (
    BASELINE_HASH,
    PHASE1_BASELINE,
    Check,
    CheckGroup,
    Evidence,
    format_counts,
)
from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import (
    AnchorMode,
    ImpulseDirection,
    OverlapPriority,
    SeedMode,
)
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.synthetic_break import (
    MIRROR_CENTRE,
    SYNTHETIC_BREAK_DOWN,
    SYNTHETIC_BREAK_START,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_OVERLAP_UP,
    SYNTHETIC_PENULTIMATE_UP,
)
from chronos.domain.structure.synthetic_zones import Candle
from chronos.domain.structure.zone_break import ZoneBreakLevels
from chronos.domain.structure.zones import CandleSeries

STEP = pd.Timedelta(hours=4)
TITLE = "R. Evidencia de la fase 2.1 · rotura del ID por zona"


def collect(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> Evidence:
    """Ejecuta toda la evidencia de la fase 2.1 sobre las velas ya agregadas."""
    return Evidence(
        groups=(
            _last_zone_cases(),
            _penultimate_cases(),
            _overlap_case(),
            _mirror_case(),
            _lookahead(),
            _switch_off(),
            _regression(config, series),
        )
    )


# --- Apoyo ------------------------------------------------------------------


def _run(
    candles: Sequence[Candle],
    *,
    break_by_zone: bool = True,
    overlap_priority: OverlapPriority = OverlapPriority.A_FAVOR_FIRST,
) -> tuple[CandleSeries, DominantImpulseDetector]:
    series = _series(candles)
    detector = DominantImpulseDetector(
        timeframe="H4",
        anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY,
        seed_mode=SeedMode.S2_FIRST_COUNTER_BAR,
        warmup_bars=0,
        break_by_zone=break_by_zone,
        overlap_priority=overlap_priority,
        zone_levels=ZoneBreakLevels(series, timeframe="H4") if break_by_zone else None,
    )
    detector.process_all(
        [
            BodyBar(
                timestamp=series.at(position),
                open=float(series.open[position]),
                close=float(series.close[position]),
            )
            for position in range(len(series))
        ]
    )
    return series, detector


def _series(candles: Sequence[Candle]) -> CandleSeries:
    stamps = pd.date_range(
        SYNTHETIC_BREAK_START, periods=len(candles), freq=STEP, tz="UTC"
    )
    return CandleSeries(
        timestamps=pd.DatetimeIndex(stamps),
        open=pd.Series([candle[0] for candle in candles], dtype=float).to_numpy(),
        high=pd.Series([candle[1] for candle in candles], dtype=float).to_numpy(),
        low=pd.Series([candle[2] for candle in candles], dtype=float).to_numpy(),
        close=pd.Series([candle[3] for candle in candles], dtype=float).to_numpy(),
    )


# --- Casos 1, 2, 3, 7 y 9 ---------------------------------------------------


def _last_zone_cases() -> CheckGroup:
    """El lado a favor: el UL manda, y al salvarse el extremo se estira."""
    _, detector = _run(SYNTHETIC_BREAK_UP)
    _, old = _run(SYNTHETIC_BREAK_UP, break_by_zone=False)
    first, second = detector.impulses
    avoided = detector.avoided_breaks

    return CheckGroup(
        title="R.1 El lado a favor · el UL (casos 1, 2, 3, 7 y 9 del §5)",
        note=(
            "Diez velas con dos impulsos alcistas. El ID#1 nace con el UL en "
            "[2010, 2012], sobrevive a dos velas que la fase 1 habría llamado rotura y "
            "muere en la tercera; el ID#2 nace con un UL de altura cero."
        ),
        checks=(
            Check("recuento", "2 impulsos alcistas", _count(detector)),
            Check(
                "2. b4 cierra dentro del UL",
                "cierre 2011.00 en [2010.00, 2012.00] -> sobrevive",
                f"cierre {avoided[0].close:.2f} en "
                f"[{avoided[0].zone_inner:.2f}, {avoided[0].zone_outer:.2f}] -> sobrevive",
            ),
            Check(
                "1. b5 perfora el UL con mecha y cierra dentro",
                "cierre 2011.50 en [2010.00, 2012.00] -> sobrevive",
                f"cierre {avoided[1].close:.2f} en "
                f"[{avoided[1].zone_inner:.2f}, {avoided[1].zone_outer:.2f}] -> sobrevive",
            ),
            Check(
                "3. b6 cierra más allá del borde exterior",
                "muere en b6 por ROTURA_A_FAVOR con nivel UL",
                f"muere en b{first.index_end} por {_exit(first)}",
            ),
            Check(
                "7. el extremo se extiende y el UL NO se remarca",
                "de 2010.00 a 2011.50 en 2 extensiones; ancla 1995.00 quieta",
                f"de {first.extreme_at_constitution:.2f} a {first.extreme:.2f} en "
                f"{first.extreme_extensions} extensiones; ancla {first.anchor:.2f} quieta",
            ),
            Check(
                "9. UL de altura cero",
                "ID#2 muere en b9 por ROTURA_A_FAVOR con nivel UL; 1 rotura sobre zona plana",
                f"ID#{second.id_num} muere en b{second.index_end} por {_exit(second)}; "
                f"{detector.diagnostics['roturas_con_zona_de_altura_cero']} rotura sobre zona plana",
            ),
            Check(
                "contraste: con la regla vieja el ID#1 muere antes",
                "muere en b4 con extremo 2010.00 y 0 extensiones",
                f"muere en b{old.impulses[0].index_end} con extremo "
                f"{old.impulses[0].extreme:.2f} y {old.impulses[0].extreme_extensions} extensiones",
            ),
            Check(
                "roturas evitadas",
                "2 a favor, 0 en contra, 2 extensiones",
                f"{detector.diagnostics['roturas_evitadas_a_favor']} a favor, "
                f"{detector.diagnostics['roturas_evitadas_en_contra']} en contra, "
                f"{detector.diagnostics['extremos_extendidos']} extensiones",
            ),
        ),
    )


# --- Casos 4, 5 y 6 ---------------------------------------------------------


def _penultimate_cases() -> CheckGroup:
    """El lado en contra: el PUL manda si el ID tiene uno, y si no manda la línea."""
    _, run = _run(SYNTHETIC_PENULTIMATE_UP)
    first, second = run.impulses[0], run.impulses[1]
    avoided = [item for item in run.avoided_breaks if item.id_num == second.id_num]

    return CheckGroup(
        title="R.2 El lado en contra · el PUL (casos 4, 5 y 6 del §5)",
        note=(
            "Una sola serie con los dos estados. El ID#1 es el primero del histórico y "
            "no tiene ID anterior del que sacar su PUL, así que muere por línea; el "
            "ID#2 hereda como PUL el cuerpo de e2 —la vela del extremo del ID#1, la de "
            "su UL— y ese cuerpo cubre la línea de su propio ancla."
        ),
        checks=(
            Check(
                "el PUL del ID#2 es el cuerpo de la vela del extremo del ID#1",
                "vela b2, zona [1976.00, 1986.00]",
                f"vela b{second.index_penultimate}, "
                f"zona [{avoided[0].zone_outer:.2f}, {avoided[0].zone_inner:.2f}]",
            ),
            Check(
                "4b. e8 cierra dentro del PUL",
                "cierre 1977.00 bajo la línea 1978.00, dentro de [1976.00, 1986.00]",
                f"cierre {avoided[0].close:.2f} bajo la línea {avoided[0].line:.2f}, "
                f"dentro de [{avoided[0].zone_outer:.2f}, {avoided[0].zone_inner:.2f}]",
            ),
            Check(
                "4a. e9 perfora el PUL con mecha y cierra dentro",
                "sobrevive en b9, sin mover el extremo",
                f"sobrevive en b{avoided[1].index}, "
                f"{'sin mover' if not avoided[1].extended_extreme else 'moviendo'} el extremo",
            ),
            Check(
                "4c. e10 atraviesa el PUL entero",
                "muere en b10 por ROTURA_EN_CONTRA con nivel PUL",
                f"muere en b{second.index_end} por {_exit(second)}",
            ),
            Check(
                "6. con PUL, dos velas que la fase 1 habría matado no matan",
                "2 roturas evitadas en el ID#2",
                f"{len(avoided)} roturas evitadas en el ID#{second.id_num}",
            ),
            Check(
                "5. sin PUL, el ID#1 muere por línea contra su ancla",
                "sin PUL, muere en b5 por ROTURA_EN_CONTRA con nivel linea",
                f"{'sin PUL' if first.index_penultimate is None else 'con PUL'}, "
                f"muere en b{first.index_end} por {_exit(first)}",
            ),
            Check(
                "y es el único de la serie sin PUL",
                "1 de 2",
                f"{sum(1 for item in run.impulses if item.index_penultimate is None)} "
                f"de {len(run.impulses)}",
            ),
            Check(
                "el ancla no se mueve al salvarse",
                "0 extensiones de extremo en toda la serie",
                f"{run.diagnostics['extremos_extendidos']} extensiones de extremo "
                "en toda la serie",
            ),
        ),
    )


# --- Caso 8 -----------------------------------------------------------------


def _overlap_case() -> CheckGroup:
    """La vela que cumple las dos condiciones, y por qué es tan rara."""
    series, favor = _run(
        SYNTHETIC_OVERLAP_UP, overlap_priority=OverlapPriority.A_FAVOR_FIRST
    )
    _, against = _run(
        SYNTHETIC_OVERLAP_UP, overlap_priority=OverlapPriority.EN_CONTRA_FIRST
    )
    up = ImpulseDirection.ALCISTA
    ul_high = series.wick_tip_towards(2, up)
    anchor_line = favor.impulses[0].anchor

    return CheckGroup(
        title="R.3 La vela que rompe por los dos lados (caso 8 del §5)",
        note=(
            "Un hueco a la baja se salta el ancla y deja un ID de rango NEGATIVO. Sólo "
            "ahí se invierten los dos niveles y una vela puede cerrar más allá de los "
            "dos. Solaparse en precio es lo contrario: unas zonas que se pisan tienen "
            "los bordes en el orden que hace el conflicto imposible."
        ),
        checks=(
            Check(
                "el ID tiene rango negativo",
                "rango -50.00 USD, 1 impulso de rango no positivo",
                f"rango {favor.impulses[0].range_usd:.2f} USD, "
                f"{favor.diagnostics['impulsos_rango_no_positivo']} impulso de rango no positivo",
            ),
            Check(
                "los dos niveles están invertidos",
                "UL hasta 2050.00 y línea del ancla en 2090.00: el de arriba es el de abajo",
                f"UL hasta {ul_high:.2f} y línea del ancla en {anchor_line:.2f}: "
                f"{'el de arriba es el de abajo' if ul_high < anchor_line else 'en su orden'}",
            ),
            Check(
                "d5 cumple las dos condiciones",
                "1 conflicto de solape",
                f"{favor.diagnostics['conflictos_de_solape']} conflicto de solape",
            ),
            Check(
                "a_favor_primero",
                "ROTURA_A_FAVOR con nivel UL en b5",
                f"{_exit(favor.impulses[0])} en b{favor.impulses[0].index_end}",
            ),
            Check(
                "en_contra_primero",
                "ROTURA_EN_CONTRA con nivel linea en b5",
                f"{_exit(against.impulses[0])} en b{against.impulses[0].index_end}",
            ),
        ),
    )


# --- Caso 10 ----------------------------------------------------------------


def _mirror_case() -> CheckGroup:
    """La versión bajista, que no se escribe a mano: es el espejo de la alcista."""
    _, straight = _run(SYNTHETIC_BREAK_UP)
    _, reflected = _run(SYNTHETIC_BREAK_DOWN)
    first_up = straight.impulses[0]
    first_down = reflected.impulses[0]
    expected = 2 * MIRROR_CENTRE - first_up.extreme

    return CheckGroup(
        title="R.4 La versión bajista (caso 10 del §5)",
        note=(
            "La serie bajista es la alcista reflejada en p -> 2C - p con las mechas "
            "cambiadas. Todas las desigualdades del módulo son estrictas y la reflexión "
            "las invierte a la vez, así que cualquier asimetría es un fallo del código."
        ),
        checks=(
            Check(
                "dirección",
                "bajista",
                first_down.direction.value,
            ),
            Check(
                "mismo número de impulsos",
                str(len(straight.impulses)),
                str(len(reflected.impulses)),
            ),
            Check(
                "extremo reflejado",
                f"{expected:.2f}",
                f"{first_down.extreme:.2f}",
            ),
            Check(
                "mismas barras de rotura",
                str([impulse.index_end for impulse in straight.impulses]),
                str([impulse.index_end for impulse in reflected.impulses]),
            ),
            Check(
                "mismas roturas evitadas",
                str([item.index for item in straight.avoided_breaks]),
                str([item.index for item in reflected.avoided_breaks]),
            ),
        ),
    )


# --- Las vías del LookaheadError (§4) ---------------------------------------


def _lookahead() -> CheckGroup:
    series, _ = _run(SYNTHETIC_BREAK_UP)
    up = ImpulseDirection.ALCISTA

    def frontier() -> str:
        levels = ZoneBreakLevels(series, timeframe="H4")
        levels.advance(3)
        return _raises(
            lambda: levels.last_level(
                direction=up, extreme=2010.00, index_extreme=2, through=4
            )
        )

    def updated_last() -> str:
        levels = ZoneBreakLevels(series, timeframe="H4")
        levels.advance(5)
        return _raises(
            lambda: levels.last_level(
                direction=up, extreme=2011.50, index_extreme=5, through=4
            )
        )

    def in_limbo() -> str:
        _, detector = _run(SYNTHETIC_BREAK_UP[:2])
        return _raises(detector.current_break_levels)

    def penultimate() -> str:
        levels = ZoneBreakLevels(series, timeframe="H4")
        levels.advance(4)
        return _raises(
            lambda: levels.penultimate_level(
                direction=up, anchor=1995.00, index_penultimate=4, through=3
            )
        )

    return CheckGroup(
        title="R.5 Garantía anti-lookahead: cuatro excepciones provocadas a propósito",
        note=(
            "La rotura sólo puede evaluarse contra zonas cuyas velas ya han cerrado. "
            "Pedir cualquiera de las dos antes de tiempo lanza LookaheadError en vez "
            "de devolver un número."
        ),
        checks=(
            Check("zona de una vela que aún no ha cerrado", "LookaheadError", frontier()),
            Check(
                "el UL de una vela del extremo que todavía no había cerrado",
                "LookaheadError",
                updated_last(),
            ),
            Check("los niveles de rotura estando en limbo", "LookaheadError", in_limbo()),
            Check(
                "el PUL antes de que cierre su vela",
                "LookaheadError",
                penultimate(),
            ),
        ),
    )


def _raises(action: object) -> str:
    try:
        action()  # type: ignore[operator]
    except LookaheadError:
        return "LookaheadError"
    except Exception as error:  # la evidencia dice qué salió, no relanza
        return f"{type(error).__name__}"
    return "no lanzó nada"


# --- El interruptor ---------------------------------------------------------


def _switch_off() -> CheckGroup:
    series = (
        SYNTHETIC_BREAK_UP,
        SYNTHETIC_PENULTIMATE_UP,
            SYNTHETIC_OVERLAP_UP,
    )
    avoided = 0
    extensions = 0
    zoned_breaks = 0
    for candles in series:
        _, detector = _run(candles, break_by_zone=False)
        avoided += len(detector.avoided_breaks)
        extensions += detector.diagnostics["extremos_extendidos"]
        zoned_breaks += (
            detector.diagnostics["roturas_a_favor_por_zona"]
            + detector.diagnostics["roturas_en_contra_por_zona"]
        )
    return CheckGroup(
        title="R.6 Con BREAK_BY_ZONE = false la regla no deja rastro",
        note=(
            "Las cuatro series sintéticas corridas con el interruptor apagado: ni una "
            "zona consultada, ni una extensión de extremo, ni un contador movido."
        ),
        checks=(
            Check("roturas evitadas", "0", str(avoided)),
            Check("extensiones de extremo", "0", str(extensions)),
            Check("roturas por zona", "0", str(zoned_breaks)),
        ),
    )


# --- La regresión que exige el §3 -------------------------------------------


def _regression(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """§3: con `BREAK_BY_ZONE = false` tiene que salir la línea base, exacta."""
    off = replace(config, rules=replace(config.rules, break_by_zone=False))
    run = DetectDominantImpulses(off).execute(dict(series))
    obtained = {
        timeframe: len(analysis.impulses) for timeframe, analysis in run.analyses.items()
    }
    expected = {
        timeframe: PHASE1_BASELINE[timeframe]
        for timeframe in obtained
        if timeframe in PHASE1_BASELINE
    }
    published = {
        timeframe: len(analysis.published) for timeframe, analysis in run.analyses.items()
    }
    return CheckGroup(
        title="R.7 Regresión: BREAK_BY_ZONE = false reproduce la línea base",
        note=(
            f"{format_counts(PHASE1_BASELINE)} detectados y "
            f"{format_counts(PHASE1_BASELINE_PUBLISHED)} publicados, con hash "
            f"{BASELINE_HASH}. Si no sale idéntico hay un bug en la refactorización."
        ),
        checks=(
            Check("impulsos detectados", format_counts(expected), format_counts(obtained)),
            Check(
                "impulsos publicados",
                format_counts(
                    {
                        timeframe: PHASE1_BASELINE_PUBLISHED[timeframe]
                        for timeframe in published
                        if timeframe in PHASE1_BASELINE_PUBLISHED
                    }
                ),
                format_counts(published),
            ),
            Check("config_hash", BASELINE_HASH, run.config_hash),
        ),
    )


#: Los publicados de la línea base, que el §3 exige comprobar al lado de los
#: detectados. Viven aquí y no en `evidence.py` porque es la fase 2.1 la que los
#: pide; el test de la línea base fija los mismos números.
PHASE1_BASELINE_PUBLISHED: dict[str, int] = {"D": 392, "H4": 2023}


def _count(detector: DominantImpulseDetector) -> str:
    directions = {impulse.direction.value for impulse in detector.impulses}
    label = directions.pop() if len(directions) == 1 else "mezclados"
    return f"{len(detector.impulses)} impulsos {label}s"


def _exit(impulse: object) -> str:
    kind = impulse.exit_break_kind  # type: ignore[attr-defined]
    source = impulse.exit_level_source  # type: ignore[attr-defined]
    if kind is None:
        return "sigue VIGENTE"
    return f"{kind.value} con nivel {source.value}"


__all__ = ["PHASE1_BASELINE_PUBLISHED", "TITLE", "collect"]
