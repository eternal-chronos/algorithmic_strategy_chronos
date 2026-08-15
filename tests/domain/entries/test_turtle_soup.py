"""Vía 1 de la fase 3.1: el turtle soup, con los esperados escritos a mano.

Los cuatro primeros casos del día sintético de la fase, y sus espejos. Todas las
series arrancan con la misma primera vela —mecha inferior hasta 1995— para que lo
único que cambie entre los casos sea la segunda, que es la que decide.

    1. turtle soup limpio ....................... confirma
    2. la segunda supera la mecha con el cierre . NO confirma
    3. la segunda no llega a la mecha ........... NO confirma
    4. las dos velas no son consecutivas ........ NO confirma
   10. todos los anteriores en bajista .......... los espejos, sin escribir velas

No hay ningún parámetro que pasar: la relación entre las dos velas es la
definición completa. Si alguna vez hiciera falta un umbral aquí, sería que la
definición cambió.
"""

from __future__ import annotations

import pytest

from chronos.application.entries.synthetic_run import frame_of
from chronos.domain.entries.synthetic_entries import (
    H1_TURTLE_BREAKS_DOWN,
    H1_TURTLE_BREAKS_UP,
    H1_TURTLE_CLEAN_DOWN,
    H1_TURTLE_CLEAN_UP,
    H1_TURTLE_GAPPED_DOWN,
    H1_TURTLE_GAPPED_UP,
    H1_TURTLE_ONE_SIDED_UP,
    H1_TURTLE_SHORT_DOWN,
    H1_TURTLE_SHORT_UP,
    SYNTHETIC_ENTRY_START,
    TURTLE_EXTREME_DOWN,
    TURTLE_EXTREME_UP,
)
from chronos.domain.entries.turtle_soup import find_turtle_soup, wick_extreme
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import CandleSeries

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA


def series(candles: tuple[tuple[float, float, float, float], ...]) -> CandleSeries:
    return CandleSeries.of(frame_of(candles, SYNTHETIC_ENTRY_START, "1h"))


# --- Caso 1 del día sintético ------------------------------------------------


def test_turtle_soup_limpio_confirma() -> None:
    """La segunda baja a 1994,50 —alcanza 1995— y cierra en 1999, por encima."""
    found = find_turtle_soup(series(H1_TURTLE_CLEAN_UP), 1, UP)

    assert found is not None
    assert found.index == 1
    assert found.index_first == 0
    assert found.extreme == TURTLE_EXTREME_UP
    assert found.close == 1999.00


def test_el_extremo_es_el_de_la_mecha_de_la_primera_vela() -> None:
    assert wick_extreme(series(H1_TURTLE_CLEAN_UP), 0, UP) == TURTLE_EXTREME_UP


def test_sin_mecha_en_ese_lado_no_hay_extremo_que_rechazar() -> None:
    """La vela del medio de la serie con hueco cierra en su mínimo: no deja mecha."""
    assert wick_extreme(series(H1_TURTLE_GAPPED_UP), 1, UP) is None


# --- Caso 2 del día sintético ------------------------------------------------


def test_si_la_segunda_supera_el_extremo_con_el_cierre_no_confirma() -> None:
    """Cerrar en 1994, por debajo de 1995, es romper el nivel, no rechazarlo."""
    assert find_turtle_soup(series(H1_TURTLE_BREAKS_UP), 1, UP) is None


# --- Caso 3 del día sintético ------------------------------------------------


def test_si_la_segunda_no_llega_al_extremo_no_confirma() -> None:
    """1996 se queda por encima de 1995: el recorrido no alcanza la mecha."""
    assert find_turtle_soup(series(H1_TURTLE_SHORT_UP), 1, UP) is None


# --- Caso 4 del día sintético ------------------------------------------------


def test_con_una_vela_en_medio_no_hay_patron() -> None:
    """El patrón se evalúa sobre las velas 1 y 2, no sobre la 0 y la 2.

    La tercera vela sí baja a 1994 y sí cierra por encima de 1995, así que con la
    primera vela de la serie el patrón existiría. Con la de en medio no: la regla
    dice CONSECUTIVAS y eso está en la firma, no en una condición.
    """
    assert find_turtle_soup(series(H1_TURTLE_GAPPED_UP), 2, UP) is None


def test_y_con_las_dos_velas_juntas_el_mismo_precio_si_confirmaria() -> None:
    """La prueba de que el caso 4 falla por la vela de en medio y no por otra cosa."""
    juntas = (H1_TURTLE_GAPPED_UP[0], H1_TURTLE_GAPPED_UP[2])
    found = find_turtle_soup(series(juntas), 1, UP)

    assert found is not None
    assert found.extreme == TURTLE_EXTREME_UP


# --- Caso 10: los espejos ----------------------------------------------------


@pytest.mark.parametrize(
    ("candles", "index", "confirma"),
    [
        (H1_TURTLE_CLEAN_DOWN, 1, True),
        (H1_TURTLE_BREAKS_DOWN, 1, False),
        (H1_TURTLE_SHORT_DOWN, 1, False),
        (H1_TURTLE_GAPPED_DOWN, 2, False),
    ],
)
def test_el_espejo_produce_el_mismo_veredicto(
    candles: tuple[tuple[float, float, float, float], ...],
    index: int,
    confirma: bool,
) -> None:
    """Todas las desigualdades son estrictas y la reflexión las invierte a la vez."""
    found = find_turtle_soup(series(candles), index, DOWN)

    assert (found is not None) is confirma
    if found is not None:
        assert found.extreme == TURTLE_EXTREME_DOWN


def test_cada_direccion_mira_su_propia_mecha() -> None:
    """Con la primera vela cerrando en su máximo no hay nada que rechazar arriba.

    Es la comprobación de que el patrón mira la mecha del lado contrario a la
    dirección buscada y no la que tenga más a mano.
    """
    one_sided = series(H1_TURTLE_ONE_SIDED_UP)

    assert wick_extreme(one_sided, 0, UP) == TURTLE_EXTREME_UP
    assert wick_extreme(one_sided, 0, DOWN) is None
    assert find_turtle_soup(one_sided, 1, UP) is not None
    assert find_turtle_soup(one_sided, 1, DOWN) is None


def test_una_misma_pareja_puede_ser_turtle_soup_en_las_dos_direcciones() -> None:
    """Y no es un fallo: es lo que dice la definición.

    Si la primera vela deja mecha arriba y abajo y la segunda va a buscar las
    dos, el patrón existe en las dos direcciones. Quién lo usa lo decide la
    observación de H4, que sólo busca una. Se fija aquí para que nadie "corrija"
    el módulo añadiendo una exclusividad que nadie ha pedido.
    """
    both = series(H1_TURTLE_CLEAN_UP)

    assert find_turtle_soup(both, 1, UP) is not None
    assert find_turtle_soup(both, 1, DOWN) is not None


# --- Bordes de la serie ------------------------------------------------------


def test_la_primera_vela_de_la_serie_no_puede_ser_la_segunda_del_patron() -> None:
    assert find_turtle_soup(series(H1_TURTLE_CLEAN_UP), 0, UP) is None


def test_pedir_una_vela_que_no_existe_es_un_error_de_indice() -> None:
    with pytest.raises(StructureError):
        find_turtle_soup(series(H1_TURTLE_CLEAN_UP), 2, UP)
