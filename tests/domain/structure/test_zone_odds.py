"""La probabilidad de zona en el dominio: recuentos, visitas a una franja y lo sabido a cada hora.

Casos escritos a mano con números redondos. Nada de esto entra en la detección:
el test de capas comprueba aparte que ningún módulo del motor lo importa.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zone_odds import Tally, band_visits, tallies_known_at

# --- Tally --------------------------------------------------------------------


def test_sin_casos_no_hay_porcentaje_ni_intervalo() -> None:
    vacio = Tally(0, 0)
    assert vacio.empty
    assert vacio.share is None
    assert vacio.interval() is None


def test_el_intervalo_de_wilson_es_el_de_los_libros() -> None:
    # 6 de 10: p = 0,6, Wilson 95 % ≈ [0,313, 0,832].
    lo, hi = Tally(10, 6).interval()
    assert lo == pytest.approx(0.3127, abs=1e-3)
    assert hi == pytest.approx(0.8318, abs=1e-3)
    # Con k = n no se colapsa a un punto: 3 de 3 sigue dejando hueco abajo.
    lo, hi = Tally(3, 3).interval()
    assert hi == 1.0 and 0.4 < lo < 0.5


def test_un_recuento_imposible_falla_ruidosamente() -> None:
    with pytest.raises(StructureError):
        Tally(3, 4)
    with pytest.raises(StructureError):
        Tally(-1, 0)


# --- band_visits --------------------------------------------------------------


def visits(bars: list[tuple[float, float, float]], *, lo: float, hi: float, until: int | None = None):
    """Cada barra es (high, low, close)."""
    array = np.array(bars, dtype=float).reshape(-1, 3)
    return band_visits(
        high=array[:, 0],
        low=array[:, 1],
        close=array[:, 2],
        low_edge=lo,
        high_edge=hi,
        until=len(bars) - 1 if until is None else until,
    )


def test_llegar_desde_arriba_y_salir_por_arriba_es_una_visita_que_rebota() -> None:
    bars = [
        (110, 105, 108),  # fuera, por arriba
        (107, 99, 98),    # entra en [95, 100] y cierra dentro
        (104, 100, 103),  # cierra por arriba: rebotó
    ]
    found = visits(bars, lo=95, hi=100)
    assert found.from_above == Tally(1, 1)
    assert found.from_below == Tally(0, 0)


def test_atravesar_en_la_misma_barra_es_una_visita_que_sale_por_abajo() -> None:
    bars = [
        (110, 105, 108),
        (106, 90, 92),  # entra desde arriba y cierra por debajo: atravesó
    ]
    found = visits(bars, lo=95, hi=100)
    assert found.from_above == Tally(1, 0)


def test_mientras_los_cierres_se_quedan_dentro_es_la_misma_visita() -> None:
    bars = [
        (110, 105, 108),
        (107, 96, 97),   # entra
        (99, 96, 98),    # sigue dentro: no es otra visita
        (99, 97, 96),    # sigue dentro
        (101, 97, 101),  # sale por arriba
        (103, 99, 102),  # toca de nuevo viniendo de arriba y cierra arriba: rebote en una barra
        (102, 93, 93),   # toca otra vez viniendo de arriba y sale por abajo
    ]
    found = visits(bars, lo=95, hi=100)
    assert found.from_above == Tally(3, 2)


def test_desde_abajo_es_la_otra_cuenta() -> None:
    bars = [
        (90, 85, 88),
        (97, 89, 96),    # entra desde abajo
        (102, 96, 101),  # sale por arriba: atravesó
        (103, 99, 100),  # toca desde arriba (cierre anterior 101 > 100) y cierra dentro
        (100, 92, 93),   # sale por abajo: atravesó desde arriba
    ]
    found = visits(bars, lo=95, hi=100)
    assert found.from_below == Tally(1, 1)
    assert found.from_above == Tally(1, 0)


def test_una_visita_sin_cerrar_no_cuenta_y_until_recorta_el_pasado() -> None:
    bars = [
        (110, 105, 108),
        (107, 96, 97),   # entra y se queda dentro
        (99, 96, 98),    # sigue dentro hasta el final: no se sabe cómo acaba
    ]
    assert visits(bars, lo=95, hi=100).from_above == Tally(0, 0)
    # Con una barra más que sale por arriba ya cuenta, pero sólo si `until` la incluye.
    bars.append((102, 98, 101))
    assert visits(bars, lo=95, hi=100).from_above == Tally(1, 1)
    assert visits(bars, lo=95, hi=100, until=2).from_above == Tally(0, 0)


def test_la_primera_barra_no_puede_empezar_una_visita() -> None:
    """Sin cierre anterior no se sabe de dónde viene el precio."""
    found = visits([(107, 96, 97), (102, 98, 101)], lo=95, hi=100)
    assert found.from_above == Tally(0, 0) and found.from_below == Tally(0, 0)


def test_sin_pasado_las_cuentas_salen_vacias() -> None:
    found = visits([(107, 96, 97)], lo=95, hi=100, until=-1)
    assert found.from_above.empty and found.from_below.empty
    empty = np.array([], dtype=float)
    found = band_visits(high=empty, low=empty, close=empty, low_edge=1.0, high_edge=2.0, until=-1)
    assert found.from_above.empty


def test_una_franja_plana_tambien_se_visita() -> None:
    bars = [(110, 105, 108), (106, 99, 100), (104, 100, 103)]
    assert visits(bars, lo=100, hi=100).from_above == Tally(1, 1)


def test_las_entradas_malas_fallan_ruidosamente() -> None:
    with pytest.raises(StructureError):
        visits([(1, 0, 0)], lo=2, hi=1)
    with pytest.raises(StructureError):
        visits([(1, 0, 0)], lo=0, hi=1, until=5)
    with pytest.raises(StructureError):
        band_visits(
            high=np.array([1.0, 2.0]), low=np.array([0.0]), close=np.array([1.0]),
            low_edge=0.0, high_edge=1.0, until=0,
        )


# --- tallies_known_at ---------------------------------------------------------


def test_solo_cuenta_lo_resuelto_hasta_cada_instante() -> None:
    resolved_at = np.array([10, 30, 20, 40], dtype=np.int64)
    favourable = np.array([True, False, True, True])
    n, k = tallies_known_at(
        resolved_at=resolved_at,
        favourable=favourable,
        moments=np.array([5, 10, 25, 100], dtype=np.int64),
    )
    # En 5 nada; en 10 el primero (resuelto justo entonces ya se sabe); en 25 los
    # de 10 y 20; en 100 todos.
    assert n.tolist() == [0, 1, 2, 4]
    assert k.tolist() == [0, 1, 2, 3]


def test_sin_casos_todo_es_cero() -> None:
    n, k = tallies_known_at(
        resolved_at=np.array([], dtype=np.int64),
        favourable=np.array([], dtype=bool),
        moments=np.array([1, 2], dtype=np.int64),
    )
    assert n.tolist() == [0, 0] and k.tolist() == [0, 0]


def test_los_casos_y_sus_desenlaces_tienen_que_medir_lo_mismo() -> None:
    with pytest.raises(StructureError):
        tallies_known_at(
            resolved_at=np.array([1, 2], dtype=np.int64),
            favourable=np.array([True]),
            moments=np.array([3], dtype=np.int64),
        )
