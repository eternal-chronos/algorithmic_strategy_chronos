"""Las reglas puras de la entrada, con números redondos escritos a mano."""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.entries.rules import (
    TradeOutcome,
    beyond_price,
    fills,
    first_close_beyond,
    nearest_stop,
    resolve,
    target_for,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA


# --- La rotura del PUL --------------------------------------------------------


def test_romper_el_pul_es_cerrar_mas_alla_de_su_borde_exterior() -> None:
    """ID alcista con el PUL en [92, 95]: rompe la vela que CIERRA por debajo de 92."""
    closes = np.array([100.0, 96.0, 93.0, 91.5, 90.0])

    assert first_close_beyond(closes, level=92.0, direction=DOWN, first=0, last=4) == 3


def test_cerrar_dentro_de_la_zona_no_rompe() -> None:
    closes = np.array([100.0, 94.0, 92.0, 93.0])

    assert first_close_beyond(closes, level=92.0, direction=DOWN, first=0, last=3) is None


def test_el_tramo_se_respeta_y_no_mira_fuera() -> None:
    closes = np.array([90.0, 100.0, 100.0, 90.0])

    assert first_close_beyond(closes, level=92.0, direction=DOWN, first=1, last=2) is None
    assert first_close_beyond(closes, level=92.0, direction=DOWN, first=2, last=1) is None


def test_un_tramo_fuera_de_la_serie_falla_ruidosamente() -> None:
    with pytest.raises(StructureError):
        first_close_beyond(np.array([1.0, 2.0]), level=1.0, direction=UP, first=0, last=5)


# --- Dónde va el stop ---------------------------------------------------------


def test_el_stop_es_el_mas_cercano_a_la_entrada() -> None:
    """Corto en 100: el PUL de H1 deja el stop en 103 y el de M15 en 101,5 → M15."""
    assert nearest_stop(100.0, DOWN, (103.0, 101.5)) == 101.5


def test_si_el_de_m15_queda_mas_lejos_se_deja_el_de_h1() -> None:
    """La imagen 4 del propietario: M15 más arriba que el PUL de H1 → el de H1."""
    assert nearest_stop(100.0, DOWN, (103.0, 105.0)) == 103.0


def test_un_nivel_que_no_protege_no_sirve_de_stop() -> None:
    """Un largo en 100 con la zona de M15 por encima: sólo vale el de H1."""
    assert nearest_stop(100.0, UP, (97.0, 104.0)) == 97.0
    assert nearest_stop(100.0, UP, (104.0, None)) is None


def test_el_objetivo_es_cuatro_veces_el_riesgo() -> None:
    assert target_for(100.0, 98.0, UP, 4.0) == pytest.approx(108.0)
    assert target_for(100.0, 102.0, DOWN, 4.0) == pytest.approx(92.0)


def test_un_stop_del_lado_equivocado_no_da_objetivo() -> None:
    with pytest.raises(StructureError):
        target_for(100.0, 102.0, UP, 4.0)


# --- El límite y la vela ------------------------------------------------------


def test_el_limite_se_pone_donde_el_precio_todavia_no_esta() -> None:
    assert beyond_price(101.0, DOWN, 100.0)
    assert not beyond_price(99.0, DOWN, 100.0)
    assert beyond_price(99.0, UP, 100.0)
    assert not beyond_price(101.0, UP, 100.0)


def test_la_vela_llena_el_limite_cuando_llega_a_el() -> None:
    assert fills(DOWN, 101.0, high=101.0, low=99.0)
    assert not fills(DOWN, 101.0, high=100.9, low=99.0)
    assert fills(UP, 99.0, high=101.0, low=99.0)
    assert not fills(UP, 99.0, high=101.0, low=99.1)


def test_el_stop_manda_cuando_la_vela_alcanza_los_dos() -> None:
    assert resolve(UP, stop=98.0, target=108.0, high=110.0, low=97.0) == (TradeOutcome.STOP, 98.0)
    assert resolve(DOWN, stop=102.0, target=92.0, high=103.0, low=90.0) == (
        TradeOutcome.STOP,
        102.0,
    )


def test_la_vela_que_llega_al_objetivo_sin_tocar_el_stop_cierra_ganando() -> None:
    assert resolve(UP, stop=98.0, target=108.0, high=108.0, low=99.0) == (
        TradeOutcome.OBJETIVO,
        108.0,
    )
    assert resolve(UP, stop=98.0, target=108.0, high=107.0, low=99.0) is None
