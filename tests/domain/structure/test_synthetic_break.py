"""El día sintético de la fase 2.1 (§5): la rotura del ID por zona.

**Los valores esperados de este fichero están escritos a mano antes de correr el
motor.** Salen de aplicar la regla a las velas de `synthetic_break.py` con lápiz
y papel; si el código no los reproduce, el que está mal es uno de los dos y hay
que decir cuál, no ajustar el número.

La versión bajista no repite ninguna cifra: se comprueba contra el espejo de la
alcista. Todas las desigualdades del módulo son estrictas y la reflexión las
invierte a la vez, así que cualquier asimetría es un fallo del código.
"""

from __future__ import annotations

import pytest

from chronos.domain.structure.enums import (
    BodyDirection,
    BreakKind,
    BreakLevelSource,
    ImpulseDirection,
    MachineState,
    OverlapPriority,
)
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.synthetic_break import (
    MIRROR_CENTRE,
    SYNTHETIC_ANTE_DOWN,
    SYNTHETIC_ANTE_UP,
    SYNTHETIC_BREAK_DOWN,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_OVERLAP_DOWN,
    SYNTHETIC_OVERLAP_UP,
    SYNTHETIC_PENULTIMATE_DOWN,
    SYNTHETIC_PENULTIMATE_UP,
)
from chronos.domain.structure.zone_break import ZoneBreakLevels
from chronos.domain.structure.zones import ZoneKind
from tests.domain.structure.conftest import run_break

TOLERANCE = 1e-9


def _mirrored(price: float) -> float:
    return 2 * MIRROR_CENTRE - price


# --- Casos 1, 2, 3, 7 y 9: el lado a favor y el UL ---------------------------


def test_dos_impulsos_alcistas_encadenados() -> None:
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    assert len(detector.impulses) == 2
    assert all(
        impulse.direction is ImpulseDirection.ALCISTA for impulse in detector.impulses
    )


def test_caso_2_cierre_dentro_del_ul_no_rompe_y_estira_el_extremo() -> None:
    """b4 cierra en 2011, más allá de la línea 2010 pero dentro del UL [2010, 2012]."""
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    avoided = detector.avoided_breaks

    assert avoided[0].index == 4
    assert avoided[0].kind is BreakKind.A_FAVOR
    assert avoided[0].zone is ZoneKind.LAST
    assert avoided[0].close == pytest.approx(2011.00)
    assert avoided[0].line == pytest.approx(2010.00)
    assert avoided[0].zone_inner == pytest.approx(2010.00)
    assert avoided[0].zone_outer == pytest.approx(2012.00)
    assert avoided[0].extended_extreme is True


def test_caso_1_mecha_que_perfora_el_ul_y_cierra_dentro_no_rompe() -> None:
    """b5 perfora con la mecha (2014) el borde 2012 y cierra dentro, en 2011.50."""
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    avoided = detector.avoided_breaks

    assert len(avoided) == 2
    assert avoided[1].index == 5
    assert avoided[1].close == pytest.approx(2011.50)
    # El UL sigue siendo el de la constitución: b4 estiró el extremo, no la zona.
    assert avoided[1].zone_inner == pytest.approx(2010.00)
    assert avoided[1].zone_outer == pytest.approx(2012.00)


def test_el_ul_no_se_remarca_en_los_dos_rechazos() -> None:
    """El mismo borde exterior juzga a b4, a b5 y a la vela que mata al ID.

    Es la regla nueva: el extremo se estira con cada rechazo, la zona no. Antes
    cada extensión marcaba un UL nuevo y el borde se iba con él —2012, luego
    2011.90, luego 2014—, así que cada rechazo alejaba la muerte del ID.
    """
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    avoided = [item for item in detector.avoided_breaks if item.id_num == 1]

    assert [item.zone_inner for item in avoided] == [pytest.approx(2010.00)] * 2
    assert [item.zone_outer for item in avoided] == [pytest.approx(2012.00)] * 2
    # Y la vela que rompe se juzga contra ese mismo borde, no contra 2014.
    assert detector.events[0].level == pytest.approx(2012.00)
    assert detector.events[0].index == 6


def test_caso_3_cierre_mas_alla_del_borde_exterior_mata_el_id() -> None:
    """b6 cierra en 2015, sobre el borde 2012 del UL de la constitución."""
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    first = detector.impulses[0]

    assert first.index_end == 6
    assert first.exit_break_kind is BreakKind.A_FAVOR
    assert first.exit_level_source is BreakLevelSource.LAST


def test_caso_7_el_extremo_se_extiende_pero_el_ul_se_queda() -> None:
    """El ID#1 nace con extremo 2010 y muere con 2011.50, tras dos extensiones."""
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    first = detector.impulses[0]

    assert first.extreme_at_constitution == pytest.approx(2010.00)
    assert first.extreme == pytest.approx(2011.50)
    assert first.index_extreme == 5
    assert first.extreme_extensions == 2
    # El ancla no se mueve nunca: la fija la vela del arranque de la pierna.
    assert first.anchor == pytest.approx(1995.00)
    assert first.index_anchor == 0


def test_caso_9_un_ul_de_altura_cero_se_comporta_como_la_linea() -> None:
    """b7 cierra en su propio máximo: el ID#2 nace con los dos bordes en 2018."""
    series, detector = run_break(SYNTHETIC_BREAK_UP)
    second = detector.impulses[1]

    assert second.index_extreme == 7
    assert series.body_edge_towards(7, ImpulseDirection.ALCISTA) == pytest.approx(2018.00)
    assert series.wick_tip_towards(7, ImpulseDirection.ALCISTA) == pytest.approx(2018.00)
    assert second.index_end == 9
    assert second.exit_break_kind is BreakKind.A_FAVOR
    assert second.exit_level_source is BreakLevelSource.LAST
    assert second.extreme_extensions == 0
    assert detector.diagnostics["roturas_con_zona_de_altura_cero"] == 1


def test_el_recuento_de_la_serie_a_favor() -> None:
    _, detector = run_break(SYNTHETIC_BREAK_UP)
    diagnostics = detector.diagnostics

    assert diagnostics["roturas_evitadas_a_favor"] == 2
    assert diagnostics["roturas_evitadas_en_contra"] == 0
    assert diagnostics["extremos_extendidos"] == 2
    assert diagnostics["roturas_a_favor_por_zona"] == 2
    assert diagnostics["roturas_a_favor_por_linea"] == 0
    assert diagnostics["roturas_en_contra_por_zona"] == 0
    assert diagnostics["roturas_en_contra_por_linea"] == 0
    assert diagnostics["conflictos_de_solape"] == 0


def test_con_la_regla_vieja_el_primer_id_muere_dos_barras_antes() -> None:
    """El contraste sobre las mismas velas: b4 mataba al ID#1 y ahora no."""
    _, old = run_break(SYNTHETIC_BREAK_UP, break_by_zone=False)

    assert old.impulses[0].index_end == 4
    assert old.impulses[0].extreme == pytest.approx(2010.00)
    assert old.impulses[0].extreme_extensions == 0
    assert old.avoided_breaks == ()
    # El segundo ID sale igual en las dos reglas: la pierna que lo trae arranca
    # en b4 tanto si el ID#1 murió ahí como si sobrevivió hasta b6.
    assert old.impulses[1].index_constitution == 4 + 4
    assert old.impulses[1].extreme == pytest.approx(2018.00)


# --- Casos 4, 5 y 6: el lado en contra y el PUL ------------------------------


def test_caso_4_las_tres_velas_contra_el_pul() -> None:
    """e8 cierra dentro, e9 perfora con mecha y cierra dentro, e10 atraviesa."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    second = detector.impulses[1]
    avoided = [item for item in detector.avoided_breaks if item.id_num == second.id_num]

    assert [item.index for item in avoided] == [8, 9]
    assert all(item.kind is BreakKind.EN_CONTRA for item in avoided)
    assert all(item.zone is ZoneKind.PENULTIMATE for item in avoided)
    assert avoided[0].line == pytest.approx(1978.00)
    assert avoided[0].zone_inner == pytest.approx(1986.00)
    assert avoided[0].zone_outer == pytest.approx(1976.00)
    # Salvarse por el lado en contra no mueve nada: el ancla la fija la vela del
    # arranque de la pierna y esa vela no cambia.
    assert all(item.extended_extreme is False for item in avoided)
    assert second.extreme_extensions == 0

    assert second.index_end == 10
    assert second.exit_break_kind is BreakKind.EN_CONTRA
    assert second.exit_level_source is BreakLevelSource.PENULTIMATE


def test_el_pul_es_el_cuerpo_de_la_vela_del_extremo_anterior() -> None:
    """La vela del PUL del ID#2 es e2, la misma que llevaba el UL del ID#1."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    first, second = detector.impulses[0], detector.impulses[1]

    assert second.index_penultimate == 2 == first.index_extreme_at_constitution
    assert first.index_penultimate is None


def test_caso_6_con_el_pul_el_id_ya_no_muere_por_linea() -> None:
    """e8 cierra en 1977, bajo la línea 1978, pero dentro del PUL [1976, 1986]."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    second = detector.impulses[1]
    saved = detector.avoided_breaks[0]

    assert second.direction is ImpulseDirection.ALCISTA
    assert second.anchor == pytest.approx(1978.00)
    assert second.index_constitution == 7

    assert saved.index == 8
    assert saved.id_num == second.id_num
    assert saved.kind is BreakKind.EN_CONTRA
    assert saved.close == pytest.approx(1977.00)
    assert saved.line == pytest.approx(1978.00)
    # Con la regla de la fase 1 esta vela habría matado al ID.
    assert saved.close < saved.line


def test_caso_5_sin_pul_el_primer_id_muere_por_linea() -> None:
    """El ID#1 no tiene ID anterior del que sacar zona: ese lado es la línea."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    first = detector.impulses[0]

    assert first.index_penultimate is None
    assert first.index_end == 5
    assert first.exit_break_kind is BreakKind.EN_CONTRA
    assert first.exit_level_source is BreakLevelSource.LINE
    assert detector.diagnostics["roturas_en_contra_por_linea"] == 1
    assert detector.diagnostics["roturas_en_contra_por_zona"] == 1


def test_solo_el_primer_id_se_queda_sin_pul() -> None:
    """Todos los demás heredan el UL del que murió antes."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)

    sin_pul = [item.id_num for item in detector.impulses if item.index_penultimate is None]
    assert sin_pul == [detector.impulses[0].id_num]


# --- Caso 8: la vela que cumple las dos condiciones a la vez -----------------


def test_caso_8_el_conflicto_lo_resuelve_overlap_priority() -> None:
    """d5 cierra en 2060: sobre el borde del UL (2050) y bajo la línea (2090)."""
    _, favor = run_break(
        SYNTHETIC_OVERLAP_UP, overlap_priority=OverlapPriority.A_FAVOR_FIRST
    )
    _, against = run_break(
        SYNTHETIC_OVERLAP_UP, overlap_priority=OverlapPriority.EN_CONTRA_FIRST
    )

    assert favor.diagnostics["conflictos_de_solape"] == 1
    assert against.diagnostics["conflictos_de_solape"] == 1

    assert favor.impulses[0].index_end == 5
    assert favor.impulses[0].exit_break_kind is BreakKind.A_FAVOR
    assert favor.impulses[0].exit_level_source is BreakLevelSource.LAST

    assert against.impulses[0].index_end == 5
    assert against.impulses[0].exit_break_kind is BreakKind.EN_CONTRA
    assert against.impulses[0].exit_level_source is BreakLevelSource.LINE


def test_caso_8_el_conflicto_exige_rango_no_positivo_y_niveles_separados() -> None:
    """Lo que invierte los niveles no es el solape: es el hueco que cruza el ancla."""
    series, detector = run_break(SYNTHETIC_OVERLAP_UP)
    impulse = detector.impulses[0]

    assert impulse.range_usd == pytest.approx(-50.00)
    assert detector.diagnostics["impulsos_rango_no_positivo"] == 1
    # El ID#1 es el primero del histórico: en el lado en contra manda la línea.
    assert impulse.index_penultimate is None

    up = ImpulseDirection.ALCISTA
    ul_low = series.body_edge_towards(2, up)
    ul_high = series.wick_tip_towards(2, up)
    assert (ul_low, ul_high) == pytest.approx((2040.00, 2050.00))
    assert impulse.anchor == pytest.approx(2090.00)
    # Los dos niveles están separados en precio y en el orden invertido: por eso
    # una misma vela puede cerrar más allá de los dos.
    assert ul_high < impulse.anchor


# --- Caso 10: lo mismo del revés --------------------------------------------


@pytest.mark.parametrize(
    ("up", "down"),
    [
        (SYNTHETIC_BREAK_UP, SYNTHETIC_BREAK_DOWN),
        (SYNTHETIC_PENULTIMATE_UP, SYNTHETIC_PENULTIMATE_DOWN),
        (SYNTHETIC_OVERLAP_UP, SYNTHETIC_OVERLAP_DOWN),
    ],
)
def test_la_version_bajista_es_el_espejo_exacto(
    up: tuple[tuple[float, float, float, float], ...],
    down: tuple[tuple[float, float, float, float], ...],
) -> None:
    _, straight = run_break(up)
    _, reflected = run_break(down)

    assert len(straight.impulses) == len(reflected.impulses)
    for left, right in zip(straight.impulses, reflected.impulses, strict=True):
        assert right.direction is left.direction.opposite()
        assert right.anchor == pytest.approx(_mirrored(left.anchor), abs=TOLERANCE)
        assert right.extreme == pytest.approx(_mirrored(left.extreme), abs=TOLERANCE)
        assert right.index_constitution == left.index_constitution
        assert right.index_extreme == left.index_extreme
        assert right.index_anchor == left.index_anchor
        assert right.index_end == left.index_end
        assert right.exit_break_kind is left.exit_break_kind
        assert right.exit_level_source is left.exit_level_source
        assert right.extreme_extensions == left.extreme_extensions

    assert len(reflected.avoided_breaks) == len(straight.avoided_breaks)
    for left_avoided, right_avoided in zip(
        straight.avoided_breaks, reflected.avoided_breaks, strict=True
    ):
        assert right_avoided.index == left_avoided.index
        assert right_avoided.kind is left_avoided.kind
        assert right_avoided.close == pytest.approx(
            _mirrored(left_avoided.close), abs=TOLERANCE
        )
        assert right_avoided.zone_outer == pytest.approx(
            _mirrored(left_avoided.zone_outer), abs=TOLERANCE
        )


# --- El interruptor ----------------------------------------------------------


@pytest.mark.parametrize(
    "candles",
    [
        SYNTHETIC_BREAK_UP,
        SYNTHETIC_PENULTIMATE_UP,
        SYNTHETIC_OVERLAP_UP,
    ],
)
def test_apagada_la_regla_no_deja_rastro(
    candles: tuple[tuple[float, float, float, float], ...],
) -> None:
    """Con `break_by_zone: false` ni una zona, ni una extensión, ni un contador."""
    _, detector = run_break(candles, break_by_zone=False)

    assert detector.break_by_zone is False
    assert detector.avoided_breaks == ()
    assert all(impulse.extreme_extensions == 0 for impulse in detector.impulses)
    assert all(
        impulse.exit_level_source in (None, BreakLevelSource.LINE)
        for impulse in detector.impulses
    )
    for key in (
        "roturas_a_favor_por_zona",
        "roturas_a_favor_por_linea",
        "roturas_en_contra_por_zona",
        "roturas_en_contra_por_linea",
        "roturas_evitadas_a_favor",
        "roturas_evitadas_en_contra",
        "extremos_extendidos",
        "roturas_con_zona_de_altura_cero",
    ):
        assert detector.diagnostics[key] == 0


def test_encender_la_regla_sin_mechas_es_un_error() -> None:
    from chronos.domain.structure.detector import DominantImpulseDetector
    from chronos.domain.structure.errors import StructureError

    with pytest.raises(StructureError, match="mechas"):
        DominantImpulseDetector(timeframe="H4", break_by_zone=True)


# --- Las tres vías del LookaheadError (§4) -----------------------------------


def test_lookahead_pedir_una_zona_de_una_vela_que_no_ha_cerrado() -> None:
    series, _ = run_break(SYNTHETIC_BREAK_UP)
    levels = ZoneBreakLevels(series, timeframe="H4")
    levels.advance(3)

    with pytest.raises(LookaheadError, match="todavía no ha cerrado"):
        levels.last_level(
            direction=ImpulseDirection.ALCISTA,
            extreme=2010.00,
            index_extreme=2,
            through=4,
        )


def test_lookahead_el_ul_actualizado_antes_de_que_cierre_su_vela() -> None:
    """§4: el UL de la vela que extiende el extremo no existe hasta que cierra."""
    series, _ = run_break(SYNTHETIC_BREAK_UP)
    levels = ZoneBreakLevels(series, timeframe="H4")
    levels.advance(5)

    # b5 extiende el extremo, así que su UL sólo puede leerse desde b6 en adelante.
    with pytest.raises(LookaheadError, match="la vela del extremo"):
        levels.last_level(
            direction=ImpulseDirection.ALCISTA,
            extreme=2011.50,
            index_extreme=5,
            through=4,
        )


def test_lookahead_preguntar_los_niveles_de_rotura_en_limbo() -> None:
    """En limbo no hay ID que romper: preguntar por sus niveles es mirar al futuro."""
    _, detector = run_break(SYNTHETIC_BREAK_UP[:2])

    assert detector.state is MachineState.LIMBO
    with pytest.raises(LookaheadError, match="niveles de rotura"):
        detector.current_break_levels()


def test_lookahead_el_pul_antes_de_que_su_vela_cierre() -> None:
    series, _ = run_break(SYNTHETIC_PENULTIMATE_UP)
    levels = ZoneBreakLevels(series, timeframe="H4")
    levels.advance(4)

    with pytest.raises(LookaheadError, match="la vela del PUL"):
        levels.penultimate_level(
            direction=ImpulseDirection.ALCISTA,
            anchor=1978.00,
            index_penultimate=4,
            through=3,
        )


# --- Bordes ------------------------------------------------------------------


def test_el_doji_no_rompe_ni_estira_el_extremo() -> None:
    """§2.2 con `D1`: el doji es neutro en todo el módulo, también aquí."""
    series, detector = run_break(SYNTHETIC_BREAK_UP)
    # La serie no trae dojis; lo que se fija es que el extremo de un ID vigente
    # sólo se mueve con velas que cerraron más allá de su línea.
    assert all(
        series.direction_of(impulse.index_extreme) is not BodyDirection.DOJI
        for impulse in detector.impulses
    )
    for item in detector.avoided_breaks:
        if item.extended_extreme:
            assert abs(item.close - item.line) > 0


# --- Nadie nace roto, también con la regla nueva ------------------------------

#: La contraria cierra ENTRE la línea del ancla y el borde exterior del PUL. Con
#: la línea el ID nacería roto y no llega a nacer; con la zona el PUL lo salva y
#: nace igual que cualquier otro. Escrito a mano:
#:   f0 (1990, 1996, 1989, 1995) verde · semilla; ancla del ID#1 = 1995
#:   f1 (1995, 1996, 1985, 1986) roja  · abre la pierna bajista
#:   f2 (1986, 1987, 1975, 1976) roja  · extremo del ID#1; su cuerpo será el PUL#2
#:   f3 (1976, 1981, 1975, 1980) verde · CONSTITUYE ID#1 bajista
#:   f4 (1980, 1981, 1977, 1978) roja  · retroceso; ancla A1 del ID#2 = 1978
#:   f5 (1978, 1998, 1977, 1997) verde · mata al ID#1 por línea y abre la alcista
#:   f6 (1997, 2000, 1996, 1999) verde · extremo del ID#2
#:   f7 (1999, 2000, 1976.50, 1977) roja · contraria: 1977 < 1978 (línea) y
#:                                         > 1976 (borde exterior del PUL)
BORN_BROKEN_ONLY_BY_LINE: tuple[tuple[float, float, float, float], ...] = (
    (1990.00, 1996.00, 1989.00, 1995.00),
    (1995.00, 1996.00, 1985.00, 1986.00),
    (1986.00, 1987.00, 1975.00, 1976.00),
    (1976.00, 1981.00, 1975.00, 1980.00),
    (1980.00, 1981.00, 1977.00, 1978.00),
    (1978.00, 1998.00, 1977.00, 1997.00),
    (1997.00, 2000.00, 1996.00, 1999.00),
    (1999.00, 2000.00, 1976.50, 1977.00),
)


def test_el_pul_deja_nacer_al_id_que_la_linea_habria_impedido() -> None:
    _, detector = run_break(BORN_BROKEN_ONLY_BY_LINE)

    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 0
    impulse = detector.impulses[1]
    assert impulse.direction is ImpulseDirection.ALCISTA
    assert impulse.index_constitution == 7
    assert impulse.anchor == pytest.approx(1978.00)
    assert impulse.index_penultimate == 2


def test_sin_la_regla_nueva_esa_misma_vela_no_constituye() -> None:
    """Las mismas velas por línea: 1977 ya está más allá del ancla, el ID#2 no nace."""
    _, detector = run_break(BORN_BROKEN_ONLY_BY_LINE, break_by_zone=False)

    assert len(detector.impulses) == 1  # sólo el ID#1, que sí nació
    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 1
    fallida = detector.aborted_constitutions[0]
    assert fallida.index == 7
    assert fallida.aborted_direction is ImpulseDirection.ALCISTA
    assert fallida.new_leg_direction is ImpulseDirection.BAJISTA
    assert fallida.level == pytest.approx(1978.00)
    assert fallida.level_source is BreakLevelSource.LINE


# --- Casos 11, 12 y 13: el APUL ----------------------------------------------


def test_caso_11_el_id_que_continua_tendencia_lleva_apul_y_no_pul() -> None:
    """El ID#2 nace de un giro y lleva PUL; el ID#3 continúa y lleva APUL.

    La regla es una sola: el nivel en contra es el último extremo del SENTIDO
    OPUESTO. Para el ID#2 alcista ése es el extremo del ID#1 bajista, que es el
    anterior, así que se llama PUL. Para el ID#3 alcista el anterior es el ID#2,
    también alcista, y su extremo (2005) cae arriba: hay que retroceder al ID#1.
    """
    _, detector = run_break(SYNTHETIC_ANTE_UP)
    primero, segundo, tercero = detector.impulses

    assert primero.direction is ImpulseDirection.BAJISTA
    assert segundo.direction is ImpulseDirection.ALCISTA
    assert tercero.direction is ImpulseDirection.ALCISTA

    assert primero.against_source is BreakLevelSource.LINE
    assert primero.index_penultimate is None
    assert primero.index_ante_penultimate is None

    assert segundo.against_source is BreakLevelSource.PENULTIMATE
    assert segundo.index_penultimate == 2
    assert segundo.index_ante_penultimate is None

    assert tercero.against_source is BreakLevelSource.ANTE_PENULTIMATE
    # La vela del APUL es la MISMA que la del PUL del ID#2 —el extremo del ID#1—,
    # leída del otro lado: el PUL toma el cuerpo y el APUL, la mecha.
    assert tercero.index_ante_penultimate == 2
    assert tercero.index_against == 2


def test_caso_11_el_apul_es_la_mecha_y_el_pul_de_la_misma_vela_es_el_cuerpo() -> None:
    """a2 vale para los dos, y cada zona lee de ella una cosa distinta."""
    series, detector = run_break(SYNTHETIC_ANTE_UP)
    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)

    pul = niveles.penultimate_level(
        direction=ImpulseDirection.ALCISTA,
        anchor=1976.00,
        index_penultimate=2,
        through=11,
    )
    assert pul.source is BreakLevelSource.PENULTIMATE
    assert pul.inner == pytest.approx(1986.00)  # cuerpo alto de a2
    assert pul.price == pytest.approx(1976.00)  # cuerpo bajo de a2

    apul = niveles.ante_penultimate_level(
        direction=ImpulseDirection.ALCISTA,
        anchor=2003.00,
        index_ante_penultimate=2,
        through=11,
    )
    assert apul.source is BreakLevelSource.ANTE_PENULTIMATE
    assert apul.inner == pytest.approx(1976.00)  # cuerpo bajo de a2: donde acaba el PUL
    assert apul.price == pytest.approx(1975.00)  # punta de la mecha de a2
    assert apul.line == pytest.approx(2003.00)
    assert not apul.is_flat

    assert detector.impulses[2].anchor == pytest.approx(2003.00)


def test_caso_12_cierre_bajo_la_linea_pero_dentro_del_apul_no_rompe() -> None:
    """a10 cierra en 1990: bajo el ancla 2003 y muy por encima del borde 1975."""
    _, detector = run_break(SYNTHETIC_ANTE_UP)
    evitadas = [item for item in detector.avoided_breaks if item.index == 10]

    assert len(evitadas) == 1
    evitada = evitadas[0]
    assert evitada.kind is BreakKind.EN_CONTRA
    assert evitada.zone is ZoneKind.ANTE_PENULTIMATE
    assert evitada.close == pytest.approx(1990.00)
    assert evitada.line == pytest.approx(2003.00)
    assert evitada.zone_inner == pytest.approx(1976.00)
    assert evitada.zone_outer == pytest.approx(1975.00)
    # El lado en contra no estira nada: el ancla la fija el arranque de la pierna.
    assert evitada.extended_extreme is False
    # Y el ID sigue vivo después de a10: muere una vela más tarde, no aquí.
    assert detector.impulses[2].index_end == 11


def test_caso_13_cierre_mas_alla_del_apul_mata_el_id() -> None:
    """a11 cierra en 1974.50, por debajo de la punta de la mecha de a2 (1975)."""
    _, detector = run_break(SYNTHETIC_ANTE_UP)
    tercero = detector.impulses[2]

    assert tercero.index_end == 11
    assert tercero.exit_break_kind is BreakKind.EN_CONTRA
    assert tercero.exit_level_source is BreakLevelSource.ANTE_PENULTIMATE

    rotura = detector.events[-1]
    assert rotura.broken_id_num == tercero.id_num
    assert rotura.level_source is BreakLevelSource.ANTE_PENULTIMATE
    assert rotura.level == pytest.approx(1975.00)
    assert rotura.line == pytest.approx(2003.00)
    assert rotura.close == pytest.approx(1974.50)


def test_el_recuento_de_gobiernos_del_lado_en_contra_suma_los_impulsos() -> None:
    _, detector = run_break(SYNTHETIC_ANTE_UP)
    diagnostico = detector.diagnostics

    assert diagnostico["impulsos_con_pul"] == 1
    assert diagnostico["impulsos_con_apul"] == 1
    assert diagnostico["impulsos_sin_zona_en_contra"] == 1
    total = (
        diagnostico["impulsos_con_pul"]
        + diagnostico["impulsos_con_apul"]
        + diagnostico["impulsos_sin_zona_en_contra"]
    )
    assert total == len(detector.impulses)


def test_el_apul_no_se_clasifica_distinto_con_las_zonas_apagadas() -> None:
    """`BREAK_BY_ZONE` decide si la zona MANDA, no cuál es.

    Con el interruptor apagado la rotura vuelve a ser por línea y la historia es
    otra, pero cada ID sigue sabiendo qué zona le tocaría: es lo que la fase 2.0
    mide y dibuja sin cambiar ni un impulso.
    """
    _, detector = run_break(SYNTHETIC_ANTE_UP, break_by_zone=False)
    fuentes = [impulse.against_source for impulse in detector.impulses]

    assert BreakLevelSource.ANTE_PENULTIMATE in fuentes
    assert all(
        impulse.exit_level_source in (None, BreakLevelSource.LINE)
        for impulse in detector.impulses
    )


def test_el_espejo_del_apul_produce_la_historia_reflejada() -> None:
    """La serie bajista no se escribe a mano: es la alcista en el espejo."""
    _, alcista = run_break(SYNTHETIC_ANTE_UP)
    _, bajista = run_break(SYNTHETIC_ANTE_DOWN)

    assert len(alcista.impulses) == len(bajista.impulses)
    for arriba, abajo in zip(alcista.impulses, bajista.impulses, strict=True):
        assert abajo.direction is arriba.direction.opposite()
        assert abajo.against_source is arriba.against_source
        assert abajo.index_against == arriba.index_against
        assert abajo.anchor == pytest.approx(_mirrored(arriba.anchor))
        assert abajo.extreme == pytest.approx(_mirrored(arriba.extreme))
        assert abajo.index_end == arriba.index_end
        assert abajo.exit_level_source is arriba.exit_level_source
