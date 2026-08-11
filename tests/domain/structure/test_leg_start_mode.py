"""R-36 — la vela que arranca la pierna, en sus tres lecturas.

La regla del propietario dice que en un impulso alcista el extremo cae sobre una
vela verde y en uno bajista sobre una roja. Dentro de la pierna se cumple sola:
una vela contraria constituye el ID en el acto y no llega a fijar nada. La
excepción es la primera vela, la de la rotura, que la máquina adoptaba sin mirar
su color. `LEG_START_MODE` enumera las tres lecturas de ese borde.

Aquí no se elige ninguna: se fija qué hace cada una, incluidos los cuatro casos
límite que el propietario pidió documentar.
"""

from __future__ import annotations

import pytest

from chronos.domain.structure.enums import (
    BodyDirection,
    DojiBreakMode,
    ImpulseDirection,
    LegStartMode,
    MachineState,
)
from tests.domain.structure.conftest import (
    SYNTHETIC_DAY,
    SYNTHETIC_DAY_GAP_AFTER,
    run_detector,
)

#: Rotura EN CONTRA con cuerpo contrario a la pierna que abre, y detrás dos velas
#: más del mismo color equivocado. Es el borde de R-36 en cinco velas:
#:
#:   b0 bajista  ┐ pierna bajista inicial
#:   b1 alcista  ┘ contraria -> ID#1 bajista [ancla 100, extremo 90]
#:   b2 (105→101) cierra sobre el ancla: ROTURA_EN_CONTRA, pierna ALCISTA...
#:                ...pero su cuerpo es BAJISTA, o sea contrario a esa pierna
#:   b3 (104→102) bajista
#:   b4 (103→102.5) bajista
COUNTER_BREAK = ((100.0, 90.0), (90.0, 95.0), (105.0, 101.0), (104.0, 102.0), (103.0, 102.5))

#: La vela que rompe es un DOJI y además arranca la pierna (la anterior es
#: contraria, así que la racha no llega más atrás). Necesita `D2` para que el
#: doji pueda romper; con `D1` —lo configurado— este caso no existe.
DOJI_BREAK = (
    (100.0, 90.0),   # b0 bajista
    (90.0, 95.0),    # b1 contraria -> ID#1 bajista [ancla 100, extremo 90]
    (95.0, 96.0),    # b2 alcista, retroceso dentro del rango
    (89.0, 89.0),    # b3 DOJI que cierra bajo el extremo -> rompe a favor
    (89.0, 89.5),    # b4 alcista
    (89.5, 90.2),    # b5 alcista
)


def modes(bodies: tuple[tuple[float, float], ...], **kwargs: object) -> dict:
    return {
        mode: run_detector(bodies, leg_start_mode=mode, **kwargs)  # type: ignore[arg-type]
        for mode in LegStartMode
    }


# --- L1: la línea base, con el defecto a la vista ---------------------------


def test_l1_deja_el_extremo_sobre_una_vela_del_color_contrario() -> None:
    """El ID#5 del día sintético es el caso: pierna alcista, extremo en b17 roja."""
    detector = run_detector(
        SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER, leg_start_mode=LegStartMode.L1_CURRENT
    )
    quinto = detector.impulses[4]

    assert quinto.direction is ImpulseDirection.ALCISTA
    assert quinto.index_leg_start == 17
    assert quinto.index_extreme == 17
    assert quinto.extreme_bar_direction is BodyDirection.BEARISH
    assert quinto.extreme_on_counter_bar is True
    assert quinto.extreme == pytest.approx(1960.00)


def test_l1_es_el_modo_por_defecto() -> None:
    """La línea base no se mueve sola: el parámetro nace en el comportamiento previo."""
    assert run_detector(SYNTHETIC_DAY).impulses == run_detector(
        SYNTHETIC_DAY, leg_start_mode=LegStartMode.L1_CURRENT
    ).impulses


def test_la_puerta_de_atras_se_cuenta_en_los_tres_modos() -> None:
    """El diagnóstico mide cuántas piernas arrancan en una vela contraria."""
    for detector in modes(SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER).values():
        assert detector.diagnostics["piernas_arrancadas_en_vela_contraria"] >= 1


# --- L2: la vela que rompe sólo rompe ---------------------------------------


def test_l2_arranca_la_pierna_en_la_vela_siguiente() -> None:
    detectors = modes(COUNTER_BREAK)
    l1 = detectors[LegStartMode.L1_CURRENT].impulses[1]
    l2 = detectors[LegStartMode.L2_NEXT_BAR].impulses[1]

    assert l1.index_leg_start == 2, "en L1 la pierna arranca en la propia vela de rotura"
    assert l2.index_leg_start == 3, "en L2 arranca en la siguiente"


def test_l2_no_saca_ni_ancla_ni_extremo_de_la_vela_que_rompio() -> None:
    """Ninguno de los tres niveles del ID puede tocar la barra de la rotura."""
    detector = run_detector(COUNTER_BREAK, leg_start_mode=LegStartMode.L2_NEXT_BAR)
    segundo = detector.impulses[1]

    # b2, la que rompió, tiene cuerpo [101, 105]: ni el ancla ni el extremo caen ahí.
    assert segundo.anchor_a2 == pytest.approx(102.00)  # cuerpo de b3
    assert segundo.extreme == pytest.approx(104.00)  # cuerpo de b3
    assert segundo.index_extreme == 3
    # El ancla A1 también la salta: la búsqueda hacia atrás empieza antes de b2,
    # que es contraria a la pierna y sería la candidata natural (101.00).
    assert segundo.anchor_a1 == pytest.approx(90.00)  # cuerpo de b0


def test_l2_no_elimina_el_defecto_si_la_vela_siguiente_tambien_es_contraria() -> None:
    """Caso límite declarado: en L2 el defecto se mueve una barra, no desaparece.

    b3 es bajista y abre una pierna alcista. Como la de la rotura en L1, se adopta
    sin comprobar su color, así que vuelve a fijar el extremo una vela del color
    equivocado. Es la razón por la que A.1 no da cero en este modo.
    """
    segundo = run_detector(COUNTER_BREAK, leg_start_mode=LegStartMode.L2_NEXT_BAR).impulses[1]

    assert segundo.direction is ImpulseDirection.ALCISTA
    assert segundo.extreme_bar_direction is BodyDirection.BEARISH
    assert segundo.extreme_on_counter_bar is True


def test_l2_la_vela_siguiente_no_constituye_aunque_sea_contraria() -> None:
    """Caso límite declarado: la primera vela de la pierna nunca constituye.

    Si constituyera, el ID nacería sin ninguna vela que encerrar. Se adopta como
    arranque —igual que la de la rotura en L1— y el ID lo constituye la siguiente
    contraria, que aquí es b4.
    """
    segundo = run_detector(COUNTER_BREAK, leg_start_mode=LegStartMode.L2_NEXT_BAR).impulses[1]
    assert segundo.index_leg_start == 3
    assert segundo.index_constitution == 4


def test_l2_conoce_la_direccion_de_la_pierna_desde_la_barra_de_la_rotura() -> None:
    """Entre la rotura y la vela siguiente el sesgo ya está decidido, y se publica."""
    detector = run_detector(COUNTER_BREAK, leg_start_mode=LegStartMode.L2_NEXT_BAR)
    en_la_rotura = detector.states[2]

    assert en_la_rotura.state is MachineState.LIMBO
    assert en_la_rotura.leg_direction is ImpulseDirection.ALCISTA


# --- L3: el extremo sólo lo fijan velas del color correcto -------------------


def test_l3_la_vela_contraria_pone_el_ancla_pero_no_el_extremo() -> None:
    """El ID#5 del día sintético, con el extremo ya limpio."""
    detector = run_detector(
        SYNTHETIC_DAY,
        gap_after=SYNTHETIC_DAY_GAP_AFTER,
        leg_start_mode=LegStartMode.L3_VALID_COLOUR_EXTREME,
    )
    quinto = detector.impulses[4]

    assert quinto.index_leg_start == 17, "la pierna sigue arrancando en la vela que rompe"
    assert quinto.anchor == pytest.approx(1955.00), "y de ella sale el ancla, como hoy"
    # b17 es bajista y la pierna alcista: no puede fijar el extremo. Lo fija b18.
    assert quinto.index_extreme == 18
    assert quinto.extreme == pytest.approx(1958.00)
    assert quinto.extreme_on_counter_bar is False
    assert detector.diagnostics["extremos_rechazados_por_color"] == 1


def test_l3_deja_el_ancla_sobre_la_vela_contraria() -> None:
    """Lo que L3 limpia es el extremo. El ancla sigue saliendo de esa misma vela."""
    detector = run_detector(
        SYNTHETIC_DAY,
        gap_after=SYNTHETIC_DAY_GAP_AFTER,
        leg_start_mode=LegStartMode.L3_VALID_COLOUR_EXTREME,
    )
    quinto = detector.impulses[4]
    assert quinto.index_anchor == quinto.index_leg_start == 17


def test_l3_sin_ninguna_vela_del_color_correcto_no_nace_el_id() -> None:
    """Caso límite declarado: la pierna no encierra nada, así que no hay ID.

    b2 abre la pierna alcista con cuerpo bajista (vetado), y b3 y b4 son también
    bajistas: son contrarias a la pierna, pero constituir con ellas exigiría fijar
    el extremo con una vela del color prohibido. Se dejan pasar como si fueran
    dojis y el limbo sigue, que es un estado legítimo del módulo.
    """
    detector = run_detector(COUNTER_BREAK, leg_start_mode=LegStartMode.L3_VALID_COLOUR_EXTREME)

    assert len(detector.impulses) == 1, "sólo sobrevive el ID previo a la rotura"
    assert detector.state is MachineState.LIMBO
    assert detector.diagnostics["constituciones_aplazadas_sin_extremo"] == 2
    assert detector.diagnostics["extremos_rechazados_por_color"] == 1


def test_l3_no_deja_ningun_extremo_sobre_vela_contraria() -> None:
    detector = run_detector(
        SYNTHETIC_DAY,
        gap_after=SYNTHETIC_DAY_GAP_AFTER,
        leg_start_mode=LegStartMode.L3_VALID_COLOUR_EXTREME,
    )
    assert not [i for i in detector.impulses if i.extreme_on_counter_bar]


# --- El doji, en los tres modos ---------------------------------------------


def test_el_doji_que_rompe_fija_el_extremo_en_l1_y_en_l3() -> None:
    """Caso límite declarado: un doji no es "color contrario", así que no se veta.

    §2.2 lo declara neutro en todo el módulo —ni constituye, ni corta rachas, ni
    (con `D1`) rompe—, y la regla del propietario habla de verde y de rojo. Hacer
    de L3 el único sitio donde un doji cuenta como contrario sería inventar una
    regla que nadie ha pedido.
    """
    detectors = modes(DOJI_BREAK, doji_break_mode=DojiBreakMode.D2_BREAKS)
    for mode in (LegStartMode.L1_CURRENT, LegStartMode.L3_VALID_COLOUR_EXTREME):
        segundo = detectors[mode].impulses[1]
        assert segundo.index_leg_start == 3, mode
        assert segundo.index_extreme == 3, mode
        assert segundo.extreme_bar_direction is BodyDirection.DOJI, mode
        assert segundo.extreme_on_counter_bar is False, mode
        # Cuerpo nulo: el ancla y el extremo del doji son el mismo punto y el ID
        # nace con rango cero. No se corrige en silencio, se cuenta.
        assert segundo.range_usd == pytest.approx(0.0), mode
        assert detectors[mode].diagnostics["impulsos_rango_no_positivo"] == 1


def test_en_l2_el_doji_que_rompe_tampoco_aporta_nada() -> None:
    """L2 no hace excepción por color: la vela que rompe sólo rompe, doji o no."""
    segundo = run_detector(
        DOJI_BREAK,
        leg_start_mode=LegStartMode.L2_NEXT_BAR,
        doji_break_mode=DojiBreakMode.D2_BREAKS,
    ).impulses[1]

    assert segundo.index_leg_start == 4, "la pierna arranca después del doji"
    assert segundo.index_extreme == 4
    assert segundo.extreme_bar_direction is not BodyDirection.DOJI


def test_con_d1_el_doji_no_llega_a_romper_en_ningun_modo() -> None:
    """Con la configuración de hoy este borde no existe: el doji no rompe nada.

    b3 cierra bajo el extremo del ID#1 pero es un doji, así que se le deja pasar
    y la rotura la firma b4. Ningún modo tiene entonces que decidir qué hacer con
    una vela sin color al frente de la pierna.
    """
    for detector in modes(DOJI_BREAK, doji_break_mode=DojiBreakMode.D1_NEUTRAL).values():
        assert detector.diagnostics["dojis_en_nivel_de_rotura"] == 1
        assert [event.index for event in detector.events] == [4]


# --- Invariantes comunes ----------------------------------------------------


def test_los_tres_modos_registran_la_vela_de_cada_nivel() -> None:
    """Ancla y extremo salen siempre de una vela concreta de la serie."""
    for mode, detector in modes(SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER).items():
        for impulse in detector.impulses:
            assert impulse.index_leg_start <= impulse.index_extreme, mode
            assert impulse.index_extreme < impulse.index_constitution, mode
            assert impulse.index_anchor <= impulse.index_leg_start, mode


def test_ningun_modo_mira_al_futuro() -> None:
    """El extremo se fija siempre antes de la vela que constituye."""
    for mode, detector in modes(SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER).items():
        for impulse in detector.impulses:
            assert impulse.ts_extreme < impulse.ts_constitution, mode
            assert impulse.ts_anchor < impulse.ts_constitution, mode
