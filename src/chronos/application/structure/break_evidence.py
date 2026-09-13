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
    BreakLevelSource,
    ImpulseDirection,
    OverlapPriority,
    SeedMode,
)
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.synthetic_break import (
    MIRROR_CENTRE,
    SYNTHETIC_ABORTED_UP,
    SYNTHETIC_BREAK_DOWN,
    SYNTHETIC_BREAK_START,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_COUNTER_EXTREME_UP,
    SYNTHETIC_INHERITED_UP,
    SYNTHETIC_OVERLAP_UP,
    SYNTHETIC_PENULTIMATE_UP,
)
from chronos.domain.structure.synthetic_zones import Candle
from chronos.domain.structure.zone_break import AgainstZone, ZoneBreakLevels
from chronos.domain.structure.zones import CandleSeries

STEP = pd.Timedelta(hours=4)
TITLE = "R. Evidencia de la fase 2.1 · rotura del ID por zona"


def collect(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> Evidence:
    """Ejecuta toda la evidencia de la fase 2.1 sobre las velas ya agregadas."""
    return Evidence(
        groups=(
            _last_zone_cases(),
            _penultimate_cases(),
            _ante_penultimate_cases(),
            _counter_extreme_cases(),
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
    """El lado en contra: el PUL, la cadena vacía y el APUL heredado."""
    _, vacia = _run(SYNTHETIC_PENULTIMATE_UP)
    sin_zona, heredero_vacio = vacia.impulses[0], vacia.impulses[1]

    _, cadena = _run(SYNTHETIC_INHERITED_UP)
    primero, segundo, tercero = cadena.impulses

    return CheckGroup(
        title="R.2 El lado en contra · el PUL y el APUL heredado (casos 4 a 6 y 17 a 20)",
        note=(
            "Dos series. En la primera la cadena está VACÍA: el ID#1 es el primero del "
            "histórico y no tiene nada detrás, y el ID#2 nace de su rotura EN CONTRA, "
            "así que el extremo del #1 es su ancla y tampoco lleva PUL; como el #1 no "
            "tenía zona que prestarle, los dos mueren por línea. En la segunda el ID#2 "
            "CONTINÚA al #1 —lleva su UL como PUL, con la punta estirada a la mecha de "
            "g4— y el ID#3 nace del revés y HEREDA esa misma zona como APUL."
        ),
        checks=(
            Check(
                "5. sin ninguna zona detrás, el ID#1 muere por línea contra su ancla",
                "sin zona, muere en b5 por ROTURA_EN_CONTRA con nivel linea",
                f"{'sin zona' if not sin_zona.has_penultimate else 'con PUL'}, "
                f"muere en b{sin_zona.index_end} por {_exit(sin_zona)}",
            ),
            Check(
                "4. el ID#2 viene del revés, así que no tiene PUL",
                "el extremo anterior está en b2 y su lado en contra es linea",
                f"el extremo anterior está en b{heredero_vacio.index_penultimate} y su "
                f"lado en contra es {heredero_vacio.against_source.value}",
            ),
            Check(
                "6. y sin zona heredada muere en su propia línea del ancla",
                "muere en b8 por ROTURA_EN_CONTRA con nivel linea, 0 roturas evitadas",
                f"muere en b{heredero_vacio.index_end} por {_exit(heredero_vacio)}, "
                f"{len(vacia.avoided_breaks)} roturas evitadas",
            ),
            Check(
                "11. el ID#2 de la otra serie continúa al #1, así que lleva su UL",
                "PUL sobre la vela b2, ventana de mecha (1, 4)",
                f"{segundo.against_source.value} sobre la vela b{segundo.index_against}, "
                f"ventana de mecha {segundo.against_tip_window}",
            ),
            Check(
                "17. y la punta se estira más allá de la mecha de esa vela",
                "el UL del ID#1 llegaba a 1975.00 y el PUL del #2, a 1974.00",
                f"el UL del ID#1 llegaba a {cadena.events[0].level:.2f} y el PUL del "
                f"#2, a {_zone_tip(segundo):.2f}",
            ),
            Check(
                "18. el ID#3 nace del revés y hereda esa zona como APUL",
                "APUL sobre la vela b2, ventana de mecha (1, 4)",
                f"{tercero.against_source.value} sobre la vela b{tercero.index_against}, "
                f"ventana de mecha {tercero.against_tip_window}",
            ),
            Check(
                "y son la misma zona, con los papeles de los bordes cambiados",
                "misma vela y misma ventana que el PUL del ID#2",
                "misma vela y misma ventana que el PUL del ID#2"
                if (
                    tercero.index_against == segundo.index_against
                    and tercero.against_tip_window == segundo.against_tip_window
                )
                else "otra vela u otra ventana",
            ),
            Check(
                "19 y 20. g12 la perfora con mecha y cierra dentro; g13 la atraviesa",
                "muere en b13 por ROTURA_EN_CONTRA con nivel APUL",
                f"muere en b{tercero.index_end} por {_exit(tercero)}",
            ),
            Check(
                "y esa rotura ADELANTA a la de la línea del ancla",
                "cierre 1973.00 más allá del nivel 1974.00, con el ancla en 1967.00",
                f"cierre {cadena.events[-1].close:.2f} más allá del nivel "
                f"{cadena.events[-1].level:.2f}, con el ancla en "
                f"{cadena.events[-1].line:.2f}",
            ),
            Check(
                "el primero de esa serie tampoco tiene zona, y muere A FAVOR por su UL",
                "sin zona, muere en b5 por ROTURA_A_FAVOR con nivel UL",
                f"{'sin zona' if not primero.has_penultimate else 'con PUL'}, "
                f"muere en b{primero.index_end} por {_exit(primero)}",
            ),
        ),
    )


def _zone_tip(impulse: DominantImpulse) -> float:
    """El borde INTERIOR de la zona en contra de un ID: su punta.

    Se lee por la misma puerta que la máquina —`ZoneBreakLevels`— y no se
    recalcula aquí: la evidencia enseña lo que el motor usó.
    """
    series = _series(SYNTHETIC_INHERITED_UP)
    levels = ZoneBreakLevels(series, timeframe="H4")
    levels.advance(len(series) - 1)
    level = levels.against_level(
        direction=impulse.direction,
        anchor=impulse.anchor,
        against=AgainstZone(
            source=impulse.against_source,
            index=impulse.index_against,
            direction=impulse.against_direction,
            tip_window=impulse.against_tip_window,
        ),
        through=len(series) - 1,
    )
    return level.inner


# --- Caso 8 -----------------------------------------------------------------


def _ante_penultimate_cases() -> CheckGroup:
    """El APUL: el lado en contra del ID nacido tras una constitución abortada."""
    _, run = _run(SYNTHETIC_ABORTED_UP)
    first, second = run.impulses[0], run.impulses[1]
    aborted = run.aborted_constitutions

    return CheckGroup(
        title="R.3 El lado en contra · el APUL (casos 14, 15 y 16 del §5)",
        note=(
            "El ID#1 muere por rotura EN CONTRA y el ID bajista que tenía que nacer de "
            "esa rotura no nace: f8 llega con fuerza y cierra ya más allá de su nivel, "
            "así que la constitución se ABORTA y la pierna gira otra vez. El ID#2 nace "
            "en el mismo sentido que el #1 sin que eso sea una continuación, y su PUL "
            "apuntaría al lado a FAVOR. El nivel se va a buscar donde estaba el ID que "
            "faltó: dentro del retroceso del ID#1, corriendo la misma máquina sobre las "
            "mismas velas. Ahí el último ID interior contrario fijó su extremo en f5, y "
            "su mecha es el APUL."
        ),
        checks=(
            Check(
                "14. la constitución del ID bajista se aborta en f8",
                "1 abortada, en b8",
                f"{len(aborted)} abortada, "
                + (f"en b{aborted[0].index}" if aborted else "ninguna"),
            ),
            Check(
                "los dos ID de la serie van en el mismo sentido",
                "alcista y alcista",
                f"{first.direction.value} y {second.direction.value}",
            ),
            Check(
                "14. el ID#2 lleva APUL y no PUL",
                "APUL, sobre la vela b5",
                f"{second.against_source.value}, sobre la vela "
                f"b{second.index_ante_penultimate}",
            ),
            Check(
                "y esa vela no es la del extremo del ID#1",
                "el extremo del ID#1 está en b2",
                f"el extremo del ID#1 está en b{first.index_extreme_at_constitution}",
            ),
            Check(
                "15. f11 perfora el APUL con mecha y cierra dentro",
                "sobrevive en b11",
                "sobrevive en b11"
                if second.index_end != 11
                else f"muere en b{second.index_end}",
            ),
            Check(
                "16. f12 atraviesa el APUL entero",
                "muere en b12 por ROTURA_EN_CONTRA con nivel APUL",
                f"muere en b{second.index_end} por {_exit(second)}",
            ),
            Check(
                "y muere ANTES de llegar a la línea de su ancla",
                "cierre 2006.00 sobre el ancla 1991.00",
                f"cierre {run.events[-1].close:.2f} sobre el ancla "
                f"{run.events[-1].line:.2f}",
            ),
            Check(
                "el recuento del lado en contra cuadra",
                "1 con APUL, 0 con PUL, 1 sin zona",
                f"{run.diagnostics['impulsos_con_apul']} con APUL, "
                f"{run.diagnostics['impulsos_con_pul']} con PUL, "
                f"{run.diagnostics['impulsos_sin_zona_en_contra']} sin zona",
            ),
        ),
    )


def _counter_extreme_cases() -> CheckGroup:
    """El APUL del extremo del ID contrario que quedó por detrás del ancla."""
    _, run = _run(SYNTHETIC_COUNTER_EXTREME_UP)
    first, second = run.impulses[0], run.impulses[1]

    return CheckGroup(
        title="R.4 El lado en contra · el APUL del extremo contrario (casos 21 a 23)",
        note=(
            "El ID#1 bajista NO muere de un giro: muere por rotura A FAVOR, el precio "
            "sigue bajando y la constitución del ID bajista que venía se aborta en h6. "
            "El ID#2 nace alcista con el ancla en 1971 —el cuerpo bajo de h5—, o sea "
            "POR DEBAJO del extremo del ID#1 (1977). Ese extremo, entonces, sí quedó "
            "por detrás: no hay nada que heredar y el nivel en contra es el UL del "
            "ID#1, leído desde este lado —interior el cuerpo, exterior la punta—."
        ),
        checks=(
            Check(
                "21. el ID#1 muere A FAVOR y el ID#2 nace del revés",
                "bajista muerto por ROTURA_A_FAVOR con nivel UL y alcista detrás",
                f"{first.direction.value} muerto por {_exit(first)} "
                f"y {second.direction.value} detrás",
            ),
            Check(
                "y el ancla del ID#2 queda por detrás del extremo del ID#1",
                "ancla 1971.00 por debajo del extremo 1977.00",
                f"ancla {second.anchor:.2f} por debajo del extremo {first.extreme:.2f}"
                if second.anchor < first.extreme
                else f"ancla {second.anchor:.2f} sobre el extremo {first.extreme:.2f}",
            ),
            Check(
                "21. así que su APUL es el UL del ID#1 y no se hereda nada",
                "APUL sobre la vela b2, ventana de mecha (1, 3), heredado: no",
                f"{second.against_source.value} sobre la vela b{second.index_against}, "
                f"ventana de mecha {second.against_tip_window}, heredado: "
                f"{'sí' if second.against_inherited else 'no'}",
            ),
            Check(
                "22. h9 perfora ese APUL con mecha y cierra dentro",
                "sobrevive en b9",
                "sobrevive en b9"
                if second.index_end != 9
                else f"muere en b{second.index_end}",
            ),
            Check(
                "23. h10 lo atraviesa entero",
                "muere en b10 por ROTURA_EN_CONTRA con nivel APUL",
                f"muere en b{second.index_end} por {_exit(second)}",
            ),
            Check(
                "el recuento separa los dos APUL del ID contrario",
                "1 del extremo contrario, 0 heredados",
                f"{run.diagnostics['impulsos_con_apul_del_extremo_contrario']} del "
                f"extremo contrario, {run.diagnostics['impulsos_con_apul_heredado']} "
                "heredados",
            ),
        ),
    )


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
            lambda: levels.against_level(
                direction=up,
                anchor=1995.00,
                against=AgainstZone(
                    source=BreakLevelSource.PENULTIMATE,
                    index=4,
                    direction=up,
                    tip_window=(4, 4),
                ),
                through=3,
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
