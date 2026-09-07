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
    SYNTHETIC_ABORTED_DOWN,
    SYNTHETIC_ABORTED_UP,
    SYNTHETIC_BREAK_DOWN,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_COUNTER_EXTREME_DOWN,
    SYNTHETIC_COUNTER_EXTREME_UP,
    SYNTHETIC_INHERITED_DOWN,
    SYNTHETIC_INHERITED_UP,
    SYNTHETIC_OVERLAP_DOWN,
    SYNTHETIC_OVERLAP_UP,
    SYNTHETIC_PENULTIMATE_DOWN,
    SYNTHETIC_PENULTIMATE_UP,
    SYNTHETIC_SAME_DOWN,
    SYNTHETIC_SAME_UP,
)
from chronos.domain.structure.zone_break import AgainstZone, ZoneBreakLevels
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


# --- Casos 4, 5 y 6: el lado en contra sin zona ------------------------------


def test_caso_5_sin_zona_en_contra_el_primer_id_muere_por_linea() -> None:
    """El ID#1 no tiene ID anterior del que sacar zona: ese lado es la línea."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    first = detector.impulses[0]

    assert first.index_penultimate is None
    assert first.against_source is BreakLevelSource.LINE
    assert first.index_end == 5
    assert first.exit_break_kind is BreakKind.EN_CONTRA
    assert first.exit_level_source is BreakLevelSource.LINE


def test_caso_4_el_id_que_viene_del_reves_no_tiene_pul() -> None:
    """El ID#2 nace de la rotura EN CONTRA del #1, que iba al revés que él.

    Entonces el extremo del ID#1 no queda por detrás sino delante —es el ancla
    del #2— y no hay PUL. Lo que habría es el APUL heredado, pero el ID#1 no
    llevaba ninguna zona que prestar, así que este lado también se rompe por
    línea: la cadena se hereda vacía.
    """
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    first, second = detector.impulses[0], detector.impulses[1]

    assert second.direction is ImpulseDirection.ALCISTA
    assert first.direction is ImpulseDirection.BAJISTA
    # La vela del extremo anterior sigue apuntada: es el dato que explica por qué
    # este ID no lleva PUL, no un nivel.
    assert second.index_penultimate == 2 == first.index_extreme_at_constitution
    assert second.penultimate_direction is ImpulseDirection.BAJISTA
    assert not second.has_penultimate
    assert not second.has_ante_penultimate
    assert second.against_source is BreakLevelSource.LINE


def test_caso_6_sin_zona_heredada_el_id_muere_en_su_linea_del_ancla() -> None:
    """e8 cierra en 1977, bajo la línea 1978, y sin zona detrás eso lo mata."""
    _, detector = run_break(SYNTHETIC_PENULTIMATE_UP)
    second = detector.impulses[1]

    assert second.anchor == pytest.approx(1978.00)
    assert second.index_constitution == 7
    assert second.index_end == 8
    assert second.exit_break_kind is BreakKind.EN_CONTRA
    assert second.exit_level_source is BreakLevelSource.LINE
    assert detector.avoided_breaks == ()
    assert detector.diagnostics["roturas_en_contra_por_linea"] == 2
    assert detector.diagnostics["roturas_en_contra_por_zona"] == 0


def test_solo_lleva_pul_el_que_viene_de_un_id_del_mismo_sentido() -> None:
    """La regla en una línea, sobre las cuatro series que tienen ID encadenados."""
    for serie in (
        SYNTHETIC_PENULTIMATE_UP,
        SYNTHETIC_SAME_UP,
        SYNTHETIC_INHERITED_UP,
        SYNTHETIC_BREAK_UP,
    ):
        _, detector = run_break(serie)
        previos = [None, *detector.impulses[:-1]]
        for previo, impulso in zip(previos, detector.impulses, strict=True):
            mismo_sentido = previo is not None and previo.direction is impulso.direction
            assert impulso.has_penultimate is mismo_sentido or (
                # salvo cuando el retroceso de aquel ID trae un APUL propio
                impulso.has_ante_penultimate
            )
            if not mismo_sentido:
                assert not impulso.has_penultimate


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
        levels.against_level(
            direction=ImpulseDirection.ALCISTA,
            anchor=1978.00,
            against=AgainstZone(
                source=BreakLevelSource.PENULTIMATE,
                index=4,
                direction=ImpulseDirection.ALCISTA,
                tip_window=(4, 4),
            ),
            through=3,
        )


def test_lookahead_la_mecha_de_la_zona_antes_de_que_cierre_su_vela() -> None:
    """La punta se busca en una ventana, y la ventana tampoco puede ir por delante."""
    series, _ = run_break(SYNTHETIC_PENULTIMATE_UP)
    levels = ZoneBreakLevels(series, timeframe="H4")
    levels.advance(4)

    with pytest.raises(LookaheadError, match="la mecha de la vela del PUL"):
        levels.against_level(
            direction=ImpulseDirection.ALCISTA,
            anchor=1978.00,
            against=AgainstZone(
                source=BreakLevelSource.PENULTIMATE,
                index=2,
                direction=ImpulseDirection.ALCISTA,
                tip_window=(2, 4),
            ),
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

#: La contraria que constituiría el ID#3 cierra ENTRE el borde exterior de su PUL
#: y la línea del ancla. Con la zona el ID nacería roto y no llega a nacer; con la
#: línea nace igual que cualquier otro. Son las nueve primeras velas de
#: `SYNTHETIC_SAME_UP` con otra vela contraria al final. Escrito a mano:
#:   a0..a8   igual que en la serie del PUL de mecha; el ID#2 alcista muere A
#:            FAVOR en a7 y la pierna sigue subiendo
#:   a9 (2015, 2016, 2003.50, 2004) roja · contraria: constituiría el ID#3 con
#:            ancla 2003 y extremo 2015, pero 2004 < 2005, que es el borde
#:            exterior de su PUL —la mecha del extremo del ID#2, en a5—
BORN_BROKEN_ONLY_BY_ZONE: tuple[tuple[float, float, float, float], ...] = (
    *SYNTHETIC_SAME_UP[:9],
    (2015.00, 2016.00, 2003.50, 2004.00),
)


def test_la_zona_impide_nacer_al_id_que_la_linea_habria_dejado() -> None:
    """El PUL de mecha cae DENTRO del rango, así que también adelanta esto."""
    _, detector = run_break(BORN_BROKEN_ONLY_BY_ZONE)

    assert len(detector.impulses) == 2  # el ID#3 no llega a nacer
    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 1
    fallida = detector.aborted_constitutions[0]
    assert fallida.index == 9
    assert fallida.aborted_direction is ImpulseDirection.ALCISTA
    assert fallida.new_leg_direction is ImpulseDirection.BAJISTA
    assert fallida.level == pytest.approx(2005.00)
    assert fallida.level_source is BreakLevelSource.PENULTIMATE


def test_sin_la_regla_nueva_esa_misma_vela_si_constituye() -> None:
    """Las mismas velas por línea: 2004 todavía no pasa del ancla (2003)."""
    _, detector = run_break(BORN_BROKEN_ONLY_BY_ZONE, break_by_zone=False)

    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 0
    impulse = detector.impulses[2]
    assert impulse.direction is ImpulseDirection.ALCISTA
    assert impulse.index_constitution == 9
    assert impulse.anchor == pytest.approx(2003.00)


# --- Casos 11, 12 y 13: el PUL de MECHA --------------------------------------


def test_caso_11_solo_el_id_que_continua_tendencia_lleva_pul() -> None:
    """El PUL es el UL del ID anterior, y sólo vale si aquél iba igual.

    El ID#2 alcista nace de la rotura EN CONTRA del ID#1 bajista: aquel extremo
    es su ancla, no un nivel detrás, así que no tiene PUL —y el ID#1 no llevaba
    ninguna zona que prestarle—. El ID#3, en cambio, continúa al ID#2: su
    extremo quedó por detrás y su MECHA es el PUL del #3.
    """
    _, detector = run_break(SYNTHETIC_SAME_UP)
    primero, segundo, tercero = detector.impulses

    assert primero.direction is ImpulseDirection.BAJISTA
    assert segundo.direction is ImpulseDirection.ALCISTA
    assert tercero.direction is ImpulseDirection.ALCISTA

    assert primero.against_source is BreakLevelSource.LINE
    assert primero.index_penultimate is None
    assert primero.penultimate_direction is None

    assert segundo.against_source is BreakLevelSource.LINE
    assert not segundo.has_penultimate
    assert segundo.index_penultimate == 2
    assert segundo.penultimate_direction is ImpulseDirection.BAJISTA

    assert tercero.against_source is BreakLevelSource.PENULTIMATE
    # La vela del extremo del ID#2, no la de un ID más atrás.
    assert tercero.index_penultimate == 5
    assert tercero.index_against == 5
    assert tercero.penultimate_direction is ImpulseDirection.ALCISTA
    assert tercero.against_direction is ImpulseDirection.ALCISTA


def test_caso_11_el_borde_lo_decide_hacia_donde_mira_la_mecha() -> None:
    """La misma geometría leída desde los dos lados: la punta o el cuerpo.

    Es la mecha del extremo del ID#2 —a5, del cuerpo 2005 a la punta 2006—. Para
    un ID alcista que viene detrás, el precio que vuelve en contra encuentra
    antes la punta; para uno bajista, antes el cuerpo.
    """
    series, detector = run_break(SYNTHETIC_SAME_UP)
    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)
    zona = AgainstZone(
        source=BreakLevelSource.PENULTIMATE,
        index=5,
        direction=ImpulseDirection.ALCISTA,
        tip_window=(5, 5),
    )

    desde_arriba = niveles.against_level(
        direction=ImpulseDirection.ALCISTA, anchor=2003.00, against=zona, through=11
    )
    assert desde_arriba.source is BreakLevelSource.PENULTIMATE
    assert desde_arriba.inner == pytest.approx(2006.00)  # punta de la mecha de a5
    assert desde_arriba.price == pytest.approx(2005.00)  # borde alto del cuerpo
    assert desde_arriba.line == pytest.approx(2003.00)
    assert not desde_arriba.is_flat

    desde_abajo = niveles.against_level(
        direction=ImpulseDirection.BAJISTA, anchor=2020.00, against=zona, through=11
    )
    assert desde_abajo.inner == pytest.approx(2005.00)
    assert desde_abajo.price == pytest.approx(2006.00)

    assert detector.impulses[2].anchor == pytest.approx(2003.00)


def test_caso_12_cierre_dentro_del_pul_de_mecha_no_rompe() -> None:
    """a10 perfora el borde exterior con la mecha y cierra dentro: sobrevive."""
    _, detector = run_break(SYNTHETIC_SAME_UP)
    tercero = detector.impulses[2]

    # Muere una vela más tarde, no aquí.
    assert tercero.index_end == 11
    # Y no hay rotura EVITADA: la zona está POR ENCIMA de la línea del ancla
    # (2005 contra 2003), así que un cierre dentro de la zona tampoco había
    # pasado de la línea. Aquí la zona no salva ninguna rotura; la adelanta.
    assert not [item for item in detector.avoided_breaks if item.index == 10]


def test_caso_13_cierre_mas_alla_del_pul_de_mecha_mata_el_id() -> None:
    """a11 cierra en 2004: bajo el borde 2005 y todavía por encima del ancla."""
    _, detector = run_break(SYNTHETIC_SAME_UP)
    tercero = detector.impulses[2]

    assert tercero.index_end == 11
    assert tercero.exit_break_kind is BreakKind.EN_CONTRA
    assert tercero.exit_level_source is BreakLevelSource.PENULTIMATE

    rotura = detector.events[-1]
    assert rotura.broken_id_num == tercero.id_num
    assert rotura.level_source is BreakLevelSource.PENULTIMATE
    assert rotura.level == pytest.approx(2005.00)
    assert rotura.line == pytest.approx(2003.00)
    assert rotura.close == pytest.approx(2004.00)
    # El cierre NO pasa de la línea: sin la zona ese ID seguiría vivo. Es la
    # consecuencia de que el PUL de mecha caiga dentro del rango del ID.
    assert rotura.close > rotura.line


def test_la_rotura_por_zona_se_adelanta_a_la_de_linea_con_el_pul_de_mecha() -> None:
    """La misma serie con el interruptor apagado: el ID#3 no muere en a11."""
    _, por_linea = run_break(SYNTHETIC_SAME_UP, break_by_zone=False)

    assert por_linea.impulses[2].index_end != 11


def test_el_recuento_de_gobiernos_del_lado_en_contra_suma_los_impulsos() -> None:
    _, detector = run_break(SYNTHETIC_SAME_UP)
    diagnostico = detector.diagnostics

    assert diagnostico["impulsos_con_pul"] == 1
    assert diagnostico["impulsos_con_apul"] == 0
    assert diagnostico["impulsos_sin_zona_en_contra"] == 2
    total = (
        diagnostico["impulsos_con_pul"]
        + diagnostico["impulsos_con_apul"]
        + diagnostico["impulsos_sin_zona_en_contra"]
    )
    assert total == len(detector.impulses)


def test_el_pul_no_se_clasifica_distinto_con_las_zonas_apagadas() -> None:
    """`BREAK_BY_ZONE` decide si la zona MANDA, no cuál es.

    Con el interruptor apagado la rotura vuelve a ser por línea y la historia es
    otra, pero cada ID sigue sabiendo qué zona le tocaría: es lo que la fase 2.0
    mide y dibuja sin cambiar ni un impulso.
    """
    _, detector = run_break(SYNTHETIC_SAME_UP, break_by_zone=False)
    fuentes = [impulse.against_source for impulse in detector.impulses]

    assert BreakLevelSource.PENULTIMATE in fuentes
    assert all(
        impulse.exit_level_source in (None, BreakLevelSource.LINE)
        for impulse in detector.impulses
    )


# --- Casos 14, 15 y 16: el APUL tras una constitución abortada ---------------


def test_caso_14_el_id_nacido_tras_una_abortada_lleva_apul_y_no_pul() -> None:
    """f8 aborta la constitución del ID bajista y el ID#2 nace en el mismo sentido.

    El extremo del ID#1 queda entonces en el lado a FAVOR del #2, así que el PUL
    no vale: el nivel sale del último ID INTERIOR contrario de aquel retroceso,
    que fijó su extremo en f5.
    """
    _, detector = run_break(SYNTHETIC_ABORTED_UP)
    primero, segundo = detector.impulses

    assert primero.direction is ImpulseDirection.ALCISTA
    assert segundo.direction is ImpulseDirection.ALCISTA
    assert [item.index for item in detector.aborted_constitutions] == [8]

    assert primero.against_source is BreakLevelSource.LINE
    assert primero.index_ante_penultimate is None

    assert segundo.against_source is BreakLevelSource.ANTE_PENULTIMATE
    assert segundo.has_ante_penultimate
    assert not segundo.has_penultimate
    # La vela del extremo del ID interior bajista, no la del extremo del ID#1
    # (f2), que va en el mismo sentido que éste.
    assert segundo.index_ante_penultimate == 5
    assert segundo.index_against == 5
    assert segundo.against_direction is ImpulseDirection.BAJISTA
    # El PUL sigue apuntado —es la vela del extremo del ID#1— pero no gobierna.
    assert segundo.index_penultimate == 2


def test_caso_14_la_geometria_del_apul_es_el_ul_leido_del_otro_lado() -> None:
    """De la BASE DEL CUERPO a la PUNTA DE LA MECHA, hacia la rotura en contra."""
    series, _ = run_break(SYNTHETIC_ABORTED_UP)
    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)

    apul = niveles.against_level(
        direction=ImpulseDirection.ALCISTA,
        anchor=1991.00,
        against=AgainstZone(
            source=BreakLevelSource.ANTE_PENULTIMATE,
            index=5,
            direction=ImpulseDirection.BAJISTA,
            tip_window=(3, 6),
        ),
        through=12,
    )
    assert apul.source is BreakLevelSource.ANTE_PENULTIMATE
    assert apul.inner == pytest.approx(2010.00)  # cuerpo bajo de f5
    assert apul.price == pytest.approx(2009.00)  # punta de su mecha
    assert apul.line == pytest.approx(1991.00)
    assert not apul.is_flat


def test_caso_15_cierre_dentro_del_apul_no_rompe() -> None:
    """f11 perfora el borde exterior con la mecha (2008) y cierra en 2009.50."""
    _, detector = run_break(SYNTHETIC_ABORTED_UP)
    segundo = detector.impulses[1]

    assert segundo.index_end == 12
    # Como el PUL de mecha, el APUL cae por encima de la línea del ancla (1991),
    # así que un cierre dentro de la zona tampoco había pasado de la línea: aquí
    # la zona no salva ninguna rotura, la adelanta.
    assert not [item for item in detector.avoided_breaks if item.index == 11]


def test_caso_16_cierre_mas_alla_del_apul_mata_el_id() -> None:
    """f12 cierra en 2006: bajo el borde 2009 y muy por encima del ancla."""
    _, detector = run_break(SYNTHETIC_ABORTED_UP)
    segundo = detector.impulses[1]

    assert segundo.exit_break_kind is BreakKind.EN_CONTRA
    assert segundo.exit_level_source is BreakLevelSource.ANTE_PENULTIMATE

    rotura = detector.events[-1]
    assert rotura.broken_id_num == segundo.id_num
    assert rotura.level_source is BreakLevelSource.ANTE_PENULTIMATE
    assert rotura.level == pytest.approx(2009.00)
    assert rotura.line == pytest.approx(1991.00)
    assert rotura.close == pytest.approx(2006.00)
    assert rotura.close > rotura.line


def test_el_apul_no_se_clasifica_distinto_con_las_zonas_apagadas() -> None:
    """La estructura interior se lee igual con el interruptor puesto o quitado."""
    _, apagado = run_break(SYNTHETIC_ABORTED_UP, break_by_zone=False)
    segundo = apagado.impulses[1]

    assert segundo.against_source is BreakLevelSource.ANTE_PENULTIMATE
    assert segundo.index_ante_penultimate == 5
    # Y ahí la zona no manda: sin ella el ID sigue vivo al final de la serie.
    assert segundo.index_end is None
    assert apagado.diagnostics["impulsos_con_apul"] == 1


def test_una_continuacion_limpia_no_lleva_apul() -> None:
    """Sin abortada de por medio, dos ID del mismo sentido siguen con PUL.

    Es la diferencia entre `SYNTHETIC_SAME_UP` —el ID#2 muere A FAVOR y el #3
    nace más allá— y `SYNTHETIC_ABORTED_UP`, donde falta el ID de en medio.
    """
    _, detector = run_break(SYNTHETIC_SAME_UP)

    assert not detector.aborted_constitutions
    assert all(not impulse.has_ante_penultimate for impulse in detector.impulses)


def test_el_apul_bajista_es_el_espejo_del_alcista() -> None:
    """La reflexión invierte todas las desigualdades a la vez (§5.10)."""
    _, alcista = run_break(SYNTHETIC_ABORTED_UP)
    _, bajista = run_break(SYNTHETIC_ABORTED_DOWN)

    assert [impulse.direction for impulse in bajista.impulses] == [
        ImpulseDirection.BAJISTA,
        ImpulseDirection.BAJISTA,
    ]
    assert [
        impulse.against_source for impulse in bajista.impulses
    ] == [impulse.against_source for impulse in alcista.impulses]
    assert [
        impulse.index_ante_penultimate for impulse in bajista.impulses
    ] == [impulse.index_ante_penultimate for impulse in alcista.impulses]

    espejo, original = bajista.events[-1], alcista.events[-1]
    assert espejo.level == pytest.approx(_mirrored(original.level))
    assert espejo.close == pytest.approx(_mirrored(original.close))
    assert espejo.level_source is original.level_source


def test_el_espejo_del_pul_de_mecha_produce_la_historia_reflejada() -> None:
    """La serie bajista no se escribe a mano: es la alcista en el espejo."""
    _, alcista = run_break(SYNTHETIC_SAME_UP)
    _, bajista = run_break(SYNTHETIC_SAME_DOWN)

    assert len(alcista.impulses) == len(bajista.impulses)
    for arriba, abajo in zip(alcista.impulses, bajista.impulses, strict=True):
        assert abajo.direction is arriba.direction.opposite()
        assert abajo.against_source is arriba.against_source
        assert abajo.index_against == arriba.index_against
        assert abajo.anchor == pytest.approx(_mirrored(arriba.anchor))
        assert abajo.extreme == pytest.approx(_mirrored(arriba.extreme))
        assert abajo.index_end == arriba.index_end
        assert abajo.exit_level_source is arriba.exit_level_source


# --- Casos 17 a 20: el APUL HEREDADO y la punta de toda la vida --------------


def test_caso_17_la_punta_del_pul_se_lleva_la_mecha_de_toda_la_vida() -> None:
    """El UL#1 mide [1977, 1975]; su PUL, [1977, 1974].

    La diferencia es g4: una mecha que bajó a 1974 con el ID#1 todavía vivo y que
    la vela del extremo, g2, no cubría. La vela que lo rompió —g5, con su mecha
    en 1973— queda fuera: ésa ya no es mecha de este ID.
    """
    series, detector = run_break(SYNTHETIC_INHERITED_UP)
    primero, segundo = detector.impulses[0], detector.impulses[1]
    abajo = ImpulseDirection.BAJISTA

    # El UL del ID#1, que es el que juzgó su rotura a favor: sólo su vela.
    assert series.body_edge_towards(2, abajo) == pytest.approx(1977.00)
    assert series.wick_tip_towards(2, abajo) == pytest.approx(1975.00)
    assert primero.exit_break_kind is BreakKind.A_FAVOR
    assert detector.events[0].level == pytest.approx(1975.00)

    # Y la misma zona vista como PUL del ID#2: la ventana es la vida del ID#1.
    assert segundo.against_source is BreakLevelSource.PENULTIMATE
    assert segundo.index_against == 2
    assert segundo.against_tip_window == (1, 4)

    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)
    pul = niveles.against_level(
        direction=ImpulseDirection.BAJISTA,
        anchor=segundo.anchor,
        against=AgainstZone(
            source=BreakLevelSource.PENULTIMATE,
            index=2,
            direction=abajo,
            tip_window=(1, 4),
        ),
        through=13,
    )
    assert pul.inner == pytest.approx(1974.00)  # la mecha de g4
    assert pul.price == pytest.approx(1977.00)  # el borde del cuerpo no se mueve


def test_caso_18_el_id_que_viene_del_reves_hereda_la_zona_como_apul() -> None:
    """El ID#3 nace de la rotura EN CONTRA del #2: sin PUL, con el APUL de aquél."""
    _, detector = run_break(SYNTHETIC_INHERITED_UP)
    segundo, tercero = detector.impulses[1], detector.impulses[2]

    assert tercero.direction is ImpulseDirection.ALCISTA
    assert segundo.direction is ImpulseDirection.BAJISTA
    assert not tercero.has_penultimate
    assert tercero.has_ante_penultimate
    assert tercero.against_source is BreakLevelSource.ANTE_PENULTIMATE
    # Los mismos dos precios sobre las mismas velas que llevaba el ID#2.
    assert tercero.index_against == segundo.index_against == 2
    assert tercero.against_tip_window == segundo.against_tip_window == (1, 4)
    assert tercero.against_direction is ImpulseDirection.BAJISTA
    assert detector.diagnostics["impulsos_con_apul_heredado"] == 1


def test_caso_18_la_zona_heredada_se_lee_desde_el_otro_lado() -> None:
    """Los papeles se cambian: lo que era la punta pasa a ser el borde exterior."""
    series, detector = run_break(SYNTHETIC_INHERITED_UP)
    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)
    zona = AgainstZone(
        source=BreakLevelSource.ANTE_PENULTIMATE,
        index=2,
        direction=ImpulseDirection.BAJISTA,
        tip_window=(1, 4),
    )

    apul = niveles.against_level(
        direction=ImpulseDirection.ALCISTA,
        anchor=1967.00,
        against=zona,
        through=13,
    )
    assert apul.source is BreakLevelSource.ANTE_PENULTIMATE
    assert apul.inner == pytest.approx(1977.00)  # el borde del cuerpo de g2
    assert apul.price == pytest.approx(1974.00)  # la mecha de g4
    assert apul.line == pytest.approx(1967.00)
    assert detector.impulses[2].anchor == pytest.approx(1967.00)


def test_caso_19_cierre_dentro_del_apul_heredado_no_rompe() -> None:
    """g12 perfora el borde exterior con la mecha (1973.50) y cierra en 1975."""
    _, detector = run_break(SYNTHETIC_INHERITED_UP)
    tercero = detector.impulses[2]

    assert tercero.index_end == 13  # muere una vela más tarde, no aquí
    assert not [item for item in detector.avoided_breaks if item.index == 12]


def test_caso_20_cierre_mas_alla_del_apul_heredado_mata_el_id() -> None:
    """g13 cierra en 1973, bajo el borde 1974 y muy por encima del ancla."""
    _, detector = run_break(SYNTHETIC_INHERITED_UP)
    tercero = detector.impulses[2]

    assert tercero.exit_break_kind is BreakKind.EN_CONTRA
    assert tercero.exit_level_source is BreakLevelSource.ANTE_PENULTIMATE

    rotura = detector.events[-1]
    assert rotura.broken_id_num == tercero.id_num
    assert rotura.level == pytest.approx(1974.00)
    assert rotura.line == pytest.approx(1967.00)
    assert rotura.close == pytest.approx(1973.00)
    # Como el PUL de mecha: la zona heredada cae DENTRO del rango del ID, así que
    # ADELANTA la rotura en contra en vez de evitarla.
    assert rotura.close > rotura.line


def test_con_la_regla_vieja_el_id_heredero_no_muere_ahi() -> None:
    """El contraste sobre las mismas velas: por línea el ID#3 sigue vivo en g13."""
    _, por_linea = run_break(SYNTHETIC_INHERITED_UP, break_by_zone=False)

    assert por_linea.impulses[-1].index_end is None


def test_el_espejo_de_la_zona_heredada_produce_la_historia_reflejada() -> None:
    _, arriba = run_break(SYNTHETIC_INHERITED_UP)
    _, abajo = run_break(SYNTHETIC_INHERITED_DOWN)

    assert [item.index_end for item in abajo.impulses] == [
        item.index_end for item in arriba.impulses
    ]
    assert [item.against_source for item in abajo.impulses] == [
        item.against_source for item in arriba.impulses
    ]
    assert [item.against_tip_window for item in abajo.impulses] == [
        item.against_tip_window for item in arriba.impulses
    ]
    for uno, otro in zip(arriba.events, abajo.events, strict=True):
        assert otro.level == pytest.approx(_mirrored(uno.level), abs=TOLERANCE)
        assert otro.line == pytest.approx(_mirrored(uno.line), abs=TOLERANCE)


def test_el_lado_en_contra_por_ancla_no_toca_el_lado_a_favor() -> None:
    """La regla de la fase 3.0: el UL manda a favor y el ancla en contra.

    Sobre la misma serie, el ID#1 sigue muriendo A FAVOR en el borde exterior de
    su UL —eso no cambia— y el ID#3 deja de morir por su APUL heredado: espera a
    la línea del ancla, que en g13 todavía no ha llegado.
    """
    _, detector = run_break(SYNTHETIC_INHERITED_UP, break_against_by_zone=False)
    primero, tercero = detector.impulses[0], detector.impulses[2]

    assert primero.exit_break_kind is BreakKind.A_FAVOR
    assert primero.exit_level_source is BreakLevelSource.LAST
    assert detector.events[0].level == pytest.approx(1975.00)

    # La zona se sigue clasificando aunque no gobierne: es la que se dibuja.
    assert tercero.against_source is BreakLevelSource.ANTE_PENULTIMATE
    assert tercero.index_end is None
    assert all(
        event.level_source is not BreakLevelSource.ANTE_PENULTIMATE
        for event in detector.events
    )


# --- Casos 21, 22 y 23: el APUL del EXTREMO CONTRARIO -----------------------


def test_caso_21_el_extremo_del_id_contrario_que_queda_detras_es_el_apul() -> None:
    """El ID#2 no hereda: el extremo del ID#1 quedó por encima de su ancla."""
    _, detector = run_break(SYNTHETIC_COUNTER_EXTREME_UP)
    primero, segundo = detector.impulses[0], detector.impulses[1]

    assert primero.direction is ImpulseDirection.BAJISTA
    assert primero.exit_break_kind is BreakKind.A_FAVOR
    assert primero.extreme == pytest.approx(1977.00)
    assert segundo.direction is ImpulseDirection.ALCISTA
    assert segundo.anchor == pytest.approx(1971.00)  # el cuerpo bajo de h5

    assert not segundo.has_penultimate
    assert segundo.has_ante_penultimate
    assert not segundo.against_inherited
    # La vela del extremo del ID#1, con la ventana de su vida: h1 a h3, que es
    # hasta la anterior a la que lo rompió.
    assert segundo.index_against == 2
    assert segundo.against_tip_window == (1, 3)
    assert segundo.against_direction is ImpulseDirection.BAJISTA
    assert detector.diagnostics["impulsos_con_apul_del_extremo_contrario"] == 1
    assert detector.diagnostics["impulsos_con_apul_heredado"] == 0


def test_caso_21_ese_apul_se_lee_por_el_cuerpo_como_el_del_retroceso() -> None:
    """La mecha apunta al lado de la rotura, así que el cuerpo es el interior."""
    series, detector = run_break(SYNTHETIC_COUNTER_EXTREME_UP)
    niveles = ZoneBreakLevels(series, timeframe="H4")
    niveles.advance(len(series) - 1)

    apul = niveles.against_level(
        direction=ImpulseDirection.ALCISTA,
        anchor=1971.00,
        against=AgainstZone(
            source=BreakLevelSource.ANTE_PENULTIMATE,
            index=2,
            direction=ImpulseDirection.BAJISTA,
            tip_window=(1, 3),
        ),
        through=10,
    )
    assert apul.source is BreakLevelSource.ANTE_PENULTIMATE
    assert apul.inner == pytest.approx(1977.00)  # el borde del cuerpo de h2
    assert apul.price == pytest.approx(1975.00)  # su mecha, la más lejana del ID#1
    assert apul.line == pytest.approx(1971.00)
    assert detector.impulses[1].anchor == pytest.approx(1971.00)


def test_caso_22_cierre_dentro_de_ese_apul_no_rompe() -> None:
    """h9 perfora el borde exterior con la mecha (1974.50) y cierra en 1976."""
    _, detector = run_break(SYNTHETIC_COUNTER_EXTREME_UP)

    assert detector.impulses[1].index_end == 10  # muere una vela más tarde
    assert not [item for item in detector.avoided_breaks if item.index == 9]


def test_caso_23_cierre_mas_alla_de_ese_apul_mata_el_id() -> None:
    """h10 cierra en 1974, bajo el borde 1975 y muy por encima del ancla."""
    _, detector = run_break(SYNTHETIC_COUNTER_EXTREME_UP)
    segundo = detector.impulses[1]

    assert segundo.exit_break_kind is BreakKind.EN_CONTRA
    assert segundo.exit_level_source is BreakLevelSource.ANTE_PENULTIMATE

    rotura = detector.events[-1]
    assert rotura.broken_id_num == segundo.id_num
    assert rotura.level == pytest.approx(1975.00)
    assert rotura.line == pytest.approx(1971.00)
    assert rotura.close == pytest.approx(1974.00)


def test_caso_21_el_espejo_bajista_dice_lo_mismo() -> None:
    """Sin escribir una cifra: la reflexión invierte todas las desigualdades."""
    _, arriba = run_break(SYNTHETIC_COUNTER_EXTREME_UP)
    _, abajo = run_break(SYNTHETIC_COUNTER_EXTREME_DOWN)

    assert len(abajo.impulses) == len(arriba.impulses)
    for uno, otro in zip(arriba.impulses, abajo.impulses, strict=True):
        assert otro.direction is uno.direction.opposite()
        assert otro.against_source is uno.against_source
        assert otro.against_inherited == uno.against_inherited
        assert otro.index_against == uno.index_against
        assert otro.against_tip_window == uno.against_tip_window
        assert otro.anchor == pytest.approx(_mirrored(uno.anchor))
        assert otro.extreme == pytest.approx(_mirrored(uno.extreme))


def test_caso_21_la_zona_se_clasifica_igual_con_la_regla_de_la_fase_30() -> None:
    """Con el lado en contra por ancla la zona no manda, pero es la misma."""
    _, detector = run_break(
        SYNTHETIC_COUNTER_EXTREME_UP, break_against_by_zone=False
    )
    segundo = detector.impulses[1]

    assert segundo.has_ante_penultimate
    assert not segundo.against_inherited
    assert segundo.index_against == 2
    assert segundo.against_tip_window == (1, 3)
