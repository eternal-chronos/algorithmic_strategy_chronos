"""El turtle soup, con los esperados escritos a mano.

Todas las series arrancan con la misma primera vela —mecha inferior hasta 1995—
para que lo único que cambie entre los casos sea la segunda, que es la que decide.

    1. turtle soup limpio ......................... confirma
    2. la segunda supera la mecha con el cierre ... NO confirma (eso es rotura)
    3. la segunda no llega a la mecha ............. NO confirma
    4. las dos velas no son consecutivas .......... NO confirma
    5. la primera no dejó mecha ................... NO confirma
    6. todos los anteriores en bajista ............ el espejo

No hay ningún parámetro que pasar: la relación entre las dos velas es la
definición completa. Si alguna vez hiciera falta un umbral aquí, sería que la
definición cambió.
"""

from __future__ import annotations

import pytest

from chronos.domain.entries.turtle_soup import find_turtle_soup, wick_extreme
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from tests.domain.entries.test_hourly_order_block import CENTRE, mirror
from tests.domain.structure.conftest import make_series

ALCISTA = ImpulseDirection.ALCISTA
BAJISTA = ImpulseDirection.BAJISTA

#: Vela que baja a 1995 de mecha y cierra en 2002: la que trae el precio.
PRIMERA = (2008.0, 2009.0, 1995.0, 2002.0)


def soup(candles, *, index=1, direction=ALCISTA):
    velas = candles if direction is ALCISTA else [mirror(candle) for candle in candles]
    return find_turtle_soup(make_series(velas), index, direction)


@pytest.fixture(params=[ALCISTA, BAJISTA], ids=["alcista", "bajista"])
def direction(request: pytest.FixtureRequest) -> ImpulseDirection:
    return request.param


def test_la_segunda_va_a_buscar_la_mecha_y_cierra_de_este_lado(
    direction: ImpulseDirection,
) -> None:
    found = soup([PRIMERA, (2002.0, 2006.0, 1994.0, 2005.0)], direction=direction)

    assert found is not None
    assert found.index == 1
    assert found.index_first == 0
    assert found.extreme == pytest.approx(1995.0 if direction is ALCISTA else 2 * CENTRE - 1995.0)


def test_superar_la_mecha_con_el_cierre_es_rotura_y_no_rechazo(
    direction: ImpulseDirection,
) -> None:
    assert soup([PRIMERA, (2002.0, 2003.0, 1990.0, 1993.0)], direction=direction) is None


def test_no_llegar_a_la_mecha_no_es_rechazar_nada(direction: ImpulseDirection) -> None:
    assert soup([PRIMERA, (2002.0, 2006.0, 1998.0, 2005.0)], direction=direction) is None


def test_con_una_vela_de_por_medio_el_patron_no_existe(direction: ImpulseDirection) -> None:
    """La tercera vela va a buscar la mecha de la PRIMERA, y eso no es el patrón.

    La de en medio no deja mecha —abre y cierra en su propio mínimo— así que no
    puede hacer de primera mitad: la única mecha del tramo es la de la vela 0, y
    la vela 2 no es consecutiva con ella.
    """
    velas = [PRIMERA, (2002.0, 2006.0, 2002.0, 2005.0), (2005.0, 2006.0, 1994.0, 2005.0)]

    assert soup(velas, index=2, direction=direction) is None


def test_una_vela_sin_mecha_no_puede_ser_la_primera(direction: ImpulseDirection) -> None:
    # Cierra en su propio mínimo: no deja mecha por abajo que rechazar.
    velas = [(2008.0, 2009.0, 2002.0, 2002.0), (2002.0, 2006.0, 2001.0, 2005.0)]

    assert soup(velas, index=1, direction=direction) is None


def test_sin_vela_anterior_no_hay_patron() -> None:
    assert soup([PRIMERA, (2002.0, 2006.0, 1994.0, 2005.0)], index=0) is None


def test_la_mecha_se_mide_del_lado_contrario_a_la_direccion_buscada() -> None:
    series = make_series([PRIMERA])

    assert wick_extreme(series, 0, ALCISTA) == pytest.approx(1995.0)
    # Por arriba el cuerpo llega a 2008 y la mecha a 2009: también la hay.
    assert wick_extreme(series, 0, BAJISTA) == pytest.approx(2009.0)


def test_un_indice_fuera_de_la_serie_es_un_error() -> None:
    series = make_series([PRIMERA])

    with pytest.raises(StructureError):
        find_turtle_soup(series, 9, ALCISTA)
