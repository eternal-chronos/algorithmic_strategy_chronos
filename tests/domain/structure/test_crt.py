"""La lectura CRT del Diario, con casos escritos a mano (números redondos).

Cada vela se lee contra la anterior y sólo hay tres respuestas: rango, objetivo o
nada. Aquí se comprueban las tres, cuándo deja de existir un rango y que la
lectura de una vela no depende de lo que venga después.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.structure.crt import (
    CrtRangeEnd,
    CrtReading,
    CrtReadingKind,
    read_crt,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError


def read(bars: list[tuple[float, float, float]]) -> tuple[CrtReading, ...]:
    """Cada barra es (high, low, close)."""
    array = np.array(bars, dtype=float)
    return read_crt(high=array[:, 0], low=array[:, 1], close=array[:, 2])


#: La vela de referencia de casi todos los casos: rango [90, 110].
BASE = (110.0, 90.0, 100.0)


# --- ¿Estamos en rango? -----------------------------------------------------


def test_barrer_el_bajo_y_cerrar_dentro_abre_un_rango_alcista() -> None:
    """El rango es la vela MANIPULADA, no la que manipula."""
    lecturas = read([BASE, (105.0, 85.0, 100.0)])

    assert len(lecturas) == 1
    rango = lecturas[0]
    assert rango.kind is CrtReadingKind.RANGO
    assert rango.direction is ImpulseDirection.ALCISTA
    assert (rango.low, rango.high) == (90.0, 110.0)
    # La lectura la produce la vela 1; los precios salen de la 0.
    assert (rango.index, rango.index_source) == (1, 0)
    assert rango.live


def test_barrer_el_alto_y_cerrar_dentro_abre_un_rango_bajista() -> None:
    """Espejo exacto del alcista."""
    lecturas = read([BASE, (115.0, 95.0, 100.0)])

    assert len(lecturas) == 1
    assert lecturas[0].kind is CrtReadingKind.RANGO
    assert lecturas[0].direction is ImpulseDirection.BAJISTA
    assert (lecturas[0].low, lecturas[0].high) == (90.0, 110.0)


def test_el_rango_se_parte_por_su_50_por_ciento() -> None:
    rango = read([BASE, (105.0, 85.0, 100.0)])[0]

    assert rango.mid == 100.0
    assert rango.manipulated == 90.0
    assert rango.target == 110.0


def test_barrer_los_dos_lados_no_dice_cual_es_el_manipulado() -> None:
    """Una vela envolvente que cierra dentro no produce rango."""
    assert read([BASE, (115.0, 85.0, 100.0)]) == ()


def test_una_vela_que_no_sale_de_la_anterior_no_lee_nada() -> None:
    assert read([BASE, (108.0, 92.0, 100.0)]) == ()


# --- Si no hay rango, ¿rompió? ----------------------------------------------


def test_cerrar_mas_alla_del_alto_marca_el_alto_de_la_vela_que_rompio() -> None:
    lecturas = read([BASE, (120.0, 100.0, 118.0)])

    assert len(lecturas) == 1
    objetivo = lecturas[0]
    assert objetivo.kind is CrtReadingKind.OBJETIVO
    assert objetivo.direction is ImpulseDirection.ALCISTA
    # El nivel es SU propio alto, no el que rompió.
    assert objetivo.target == 120.0
    # Es una línea, no un rango: los dos bordes son el mismo precio.
    assert (objetivo.low, objetivo.high) == (120.0, 120.0)
    assert (objetivo.index, objetivo.index_source) == (1, 1)


def test_cerrar_mas_alla_del_bajo_marca_el_bajo_de_la_vela_que_rompio() -> None:
    objetivo = read([BASE, (100.0, 70.0, 80.0)])[0]

    assert objetivo.kind is CrtReadingKind.OBJETIVO
    assert objetivo.direction is ImpulseDirection.BAJISTA
    assert objetivo.target == 70.0


def test_cerrar_justo_en_el_borde_no_es_romper() -> None:
    """«Más allá» es estricto, igual que en la rotura del ID: el borde es DENTRO.

    La vela le barre el alto a la anterior y vuelve a cerrar justo en él: eso es
    manipulación, no rotura.
    """
    lecturas = read([BASE, (115.0, 100.0, 110.0)])

    assert len(lecturas) == 1
    assert lecturas[0].kind is CrtReadingKind.RANGO
    assert lecturas[0].direction is ImpulseDirection.BAJISTA


# --- Hasta cuándo vive un rango ---------------------------------------------


def test_el_rango_se_completa_al_llegar_al_extremo_contrario() -> None:
    """Basta con alcanzarlo: es cosa de mechas, no de cierres."""
    lecturas = read([BASE, (105.0, 85.0, 100.0), (104.0, 95.0, 100.0), (112.0, 100.0, 103.0)])
    rango = lecturas[0]

    assert rango.end is CrtRangeEnd.COMPLETADO
    assert rango.index_end == 3
    assert not rango.live


def test_el_rango_se_rompe_si_una_vela_cierra_fuera_por_el_lado_manipulado() -> None:
    lecturas = read([BASE, (105.0, 85.0, 100.0), (100.0, 78.0, 80.0)])
    rango = lecturas[0]

    assert rango.end is CrtRangeEnd.ROTO
    assert rango.index_end == 2


def test_mientras_el_rango_vive_no_nace_ninguna_lectura_nueva() -> None:
    """La primera pregunta es «¿estamos en rango?» y la respuesta es que sí.

    La vela 2 manipularía la 1 —le barre el bajo y cierra dentro—, pero el rango
    abierto en la 1 sigue vivo y no se lee nada más.
    """
    lecturas = read([BASE, (105.0, 85.0, 100.0), (104.0, 80.0, 95.0)])

    assert len(lecturas) == 1
    assert lecturas[0].live


def test_la_vela_que_cierra_el_rango_se_vuelve_a_leer_en_el_acto() -> None:
    """Completa el rango y, contra su anterior, deja un objetivo."""
    lecturas = read([BASE, (105.0, 85.0, 100.0), (104.0, 95.0, 100.0), (112.0, 100.0, 111.0)])

    assert [item.kind for item in lecturas] == [
        CrtReadingKind.RANGO,
        CrtReadingKind.OBJETIVO,
    ]
    assert lecturas[0].end is CrtRangeEnd.COMPLETADO
    assert lecturas[1].index == 3
    assert lecturas[1].target == 112.0


def test_un_objetivo_deja_de_estar_vigente_cuando_nace_la_lectura_siguiente() -> None:
    lecturas = read([BASE, (120.0, 100.0, 118.0), (130.0, 115.0, 128.0)])

    assert len(lecturas) == 2
    assert lecturas[0].index_end == 2
    # Un objetivo no se completa ni se rompe: lo sustituye el siguiente.
    assert lecturas[0].end is None
    assert lecturas[1].live


# --- Casos límite -----------------------------------------------------------


def test_una_sola_vela_no_tiene_contra_que_leerse() -> None:
    assert read([BASE]) == ()


def test_una_serie_vacia_no_lee_nada() -> None:
    empty = np.array([], dtype=float)
    assert read_crt(high=empty, low=empty, close=empty) == ()


def test_las_series_tienen_que_medir_lo_mismo() -> None:
    with pytest.raises(StructureError):
        read_crt(
            high=np.array([1.0, 2.0]),
            low=np.array([1.0]),
            close=np.array([1.0, 2.0]),
        )


# --- Sin mirar al futuro ----------------------------------------------------


SERIE = [
    BASE,
    (105.0, 85.0, 100.0),   # manipula el bajo: abre rango
    (104.0, 95.0, 100.0),   # dentro: el rango sigue
    (112.0, 100.0, 111.0),  # completa el rango y rompe: objetivo en 112
    (120.0, 108.0, 118.0),  # vuelve a romper: objetivo en 120
    (119.0, 100.0, 115.0),  # barre el bajo y cierra dentro: rango [108, 120]
]


@pytest.mark.parametrize("hasta", range(1, len(SERIE) + 1))
def test_la_lectura_de_una_vela_no_depende_de_lo_que_venga_despues(hasta: int) -> None:
    """Truncar el histórico no cambia ni una lectura ya nacida."""
    completo = [
        (item.kind, item.direction, item.index, item.low, item.high)
        for item in read(SERIE)
        if item.index < hasta
    ]
    truncado = [
        (item.kind, item.direction, item.index, item.low, item.high)
        for item in read(SERIE[:hasta])
    ]

    assert truncado == completo
