"""Los tres parámetros abiertos del módulo, cada uno con sus dos opciones.

Ninguno lo elige el motor: aquí sólo se fija que las dos alternativas están
implementadas de verdad y que producen resultados distintos y comprobables.
"""

from __future__ import annotations

import pytest

from chronos.domain.structure.enums import AnchorMode, DojiBreakMode, ImpulseDirection, SeedMode
from tests.domain.structure.conftest import SYNTHETIC_DAY, run_detector

# --- ANCHOR_MODE (§2.5) -----------------------------------------------------


def test_los_dos_modos_de_ancla_dan_valores_distintos() -> None:
    """A1 usa la última contraria previa; A2, la primera vela de la pierna."""
    a1 = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    a2 = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A2_FIRST_LEG_BAR)

    # ID#2: la pierna arranca en b5 (BH 1997.00) y la última contraria previa es
    # b4 (BH 1998.00). Un dólar de diferencia, calculado a mano.
    assert a2.impulses[1].anchor == pytest.approx(1997.00)
    assert a1.impulses[1].anchor == pytest.approx(1998.00)

    # ID#4: pierna en b14 (BL 1945.50), contraria previa b13 (BL 1945.00).
    assert a2.impulses[3].anchor == pytest.approx(1945.50)
    assert a1.impulses[3].anchor == pytest.approx(1945.00)


def test_las_dos_candidaturas_se_guardan_siempre() -> None:
    """El informe compara A1 y A2 sea cual sea el modo activo."""
    detector = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A2_FIRST_LEG_BAR)
    segundo = detector.impulses[1]
    assert segundo.anchor_a1 == pytest.approx(1998.00)
    assert segundo.anchor_a2 == pytest.approx(1997.00)
    assert segundo.anchor == segundo.anchor_a2


def test_el_modo_de_ancla_no_cambia_ni_el_extremo_ni_el_momento() -> None:
    a1 = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    a2 = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A2_FIRST_LEG_BAR)
    assert [impulse.extreme for impulse in a1.impulses] == [
        impulse.extreme for impulse in a2.impulses
    ]
    assert [impulse.ts_constitution for impulse in a1.impulses] == [
        impulse.ts_constitution for impulse in a2.impulses
    ]


def test_sin_contraria_previa_el_modo_a1_no_publica_el_impulso() -> None:
    """No se inventa un ancla sustituta: el impulso existe pero no se publica."""
    detector = run_detector(SYNTHETIC_DAY, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    primero = detector.impulses[0]
    assert primero.anchor_a1 is None
    assert primero.publishable is False
    assert primero not in detector.published_impulses
    assert detector.diagnostics["impulsos_sin_ancla_a1"] == 1


# --- SEED_MODE --------------------------------------------------------------


def test_s1_abre_la_pierna_con_la_primera_vela_no_doji() -> None:
    detector = run_detector(SYNTHETIC_DAY, seed_mode=SeedMode.S1_FIRST_NON_DOJI)
    primero = detector.impulses[0]
    assert primero.index_leg_start == 0
    assert primero.direction is ImpulseDirection.BAJISTA
    assert primero.anchor_a2 == pytest.approx(2000.00)


def test_s2_descarta_el_tramo_inicial_y_arranca_en_la_primera_contraria() -> None:
    """El tramo inicial no tiene su arranque en los datos, así que se ignora."""
    detector = run_detector(SYNTHETIC_DAY, seed_mode=SeedMode.S2_FIRST_COUNTER_BAR)
    primero = detector.impulses[0]
    # La primera contraria al tramo bajista inicial es b3: ahí arranca la pierna
    # alcista, y el primer ID nace en la siguiente contraria (b5).
    assert primero.index_leg_start == 3
    assert primero.direction is ImpulseDirection.ALCISTA
    assert primero.index_constitution == 5
    assert primero.anchor_a1 is not None  # con S2 el ancla A1 siempre existe


def test_los_dos_arranques_dan_impulsos_iniciales_distintos() -> None:
    """La decisión sólo afecta al principio del histórico, pero lo cambia entero."""
    s1 = run_detector(SYNTHETIC_DAY, seed_mode=SeedMode.S1_FIRST_NON_DOJI)
    s2 = run_detector(SYNTHETIC_DAY, seed_mode=SeedMode.S2_FIRST_COUNTER_BAR)
    assert s1.impulses[0].direction is not s2.impulses[0].direction
    assert s1.impulses[0].index_constitution != s2.impulses[0].index_constitution


# --- DOJI_BREAK_MODE --------------------------------------------------------

#: ID bajista con ancla 2000.00 y extremo 1980.00; después, un doji que cierra
#: por debajo del extremo. Es el único caso donde las dos lecturas divergen.
DOJI_QUE_ROMPE = (
    (2000.00, 1990.00),
    (1990.00, 1980.00),
    (1980.00, 1985.00),  # contraria -> ID bajista (ancla 2000.00, extremo 1980.00)
    (1979.00, 1979.00),  # DOJI que cierra más allá del extremo
    (1979.00, 1975.00),  # vela bajista normal: rompe en cualquiera de los dos modos
)


def test_d1_el_doji_no_rompe() -> None:
    """Con la lectura de §2.2 la rotura se retrasa a la primera vela con cuerpo."""
    detector = run_detector(DOJI_QUE_ROMPE, doji_break_mode=DojiBreakMode.D1_NEUTRAL)
    assert [event.index for event in detector.events] == [4]
    assert detector.diagnostics["dojis_en_nivel_de_rotura"] == 1


def test_d2_el_doji_rompe_por_cierre() -> None:
    """Con la lectura de §2.6 la rotura ocurre una barra antes, en el propio doji."""
    detector = run_detector(DOJI_QUE_ROMPE, doji_break_mode=DojiBreakMode.D2_BREAKS)
    assert [event.index for event in detector.events] == [3]


def test_el_contador_de_divergencia_es_el_mismo_en_los_dos_modos() -> None:
    """El informe usa ese contador para que el propietario vea si importa."""
    d1 = run_detector(DOJI_QUE_ROMPE, doji_break_mode=DojiBreakMode.D1_NEUTRAL)
    assert d1.diagnostics["dojis_en_nivel_de_rotura"] == 1
    sin_dojis = run_detector(SYNTHETIC_DAY)
    assert sin_dojis.diagnostics["dojis_en_nivel_de_rotura"] == 0


# --- Calentamiento ----------------------------------------------------------


def test_el_calentamiento_no_publica_impulsos() -> None:
    detector = run_detector(SYNTHETIC_DAY, warmup_bars=15)
    assert len(detector.impulses) == 5
    assert [impulse.id_num for impulse in detector.published_impulses] == [4, 5]
    assert detector.diagnostics["impulsos_en_calentamiento"] == 3
