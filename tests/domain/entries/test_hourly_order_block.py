"""El OB de H1, con los esperados escritos a mano.

Las velas van como `(open, high, low, close)` y todos los casos comparten la
misma vela de referencia —roja, con el máximo en 2010— para que lo único que
cambie sea la vela verde que viene detrás, que es la que decide.

    1. la primera verde supera .................... confirma en la 1ª
    2. la primera no llega, la segunda sí ......... confirma en la 2ª
    3. ninguna de las dos llega ................... desechado
    4. empate exacto con el máximo ................ NO supera
    5. sólo hay una verde antes del final ......... ni confirma ni se desecha
    6. la verde no tiene una roja justo detrás .... no hay intento
    7. el doji ni abre el intento ni es referencia
    8. todos los anteriores en bajista ............ el espejo

El espejo se construye con `p -> 2C - p` y `high`/`low` intercambiados. Todas las
desigualdades del módulo son estrictas y la reflexión las conserva, así que
cualquier asimetría que salga es un fallo del código y no de la serie.
"""

from __future__ import annotations

import pytest

from chronos.domain.entries.hourly_order_block import find_hourly_order_block
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from tests.domain.structure.conftest import make_series

ALCISTA = ImpulseDirection.ALCISTA
BAJISTA = ImpulseDirection.BAJISTA

#: Vela roja de referencia: cuerpo 2008 → 2002 y la mecha alta en 2010.
ROJA = (2008.0, 2010.0, 2000.0, 2002.0)

CENTRE = 2005.0


def mirror(candle: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """La misma vela reflejada: `p -> 2C - p`, con `high` y `low` intercambiados."""
    open_, high, low, close = candle
    return (2 * CENTRE - open_, 2 * CENTRE - low, 2 * CENTRE - high, 2 * CENTRE - close)


def block(candles, *, first=1, direction=ALCISTA, last=None):
    velas = candles if direction is ALCISTA else [mirror(candle) for candle in candles]
    series = make_series(velas)
    return find_hourly_order_block(
        series,
        first=first,
        last=len(velas) - 1 if last is None else last,
        direction=direction,
    )


@pytest.fixture(params=[ALCISTA, BAJISTA], ids=["alcista", "bajista"])
def direction(request: pytest.FixtureRequest) -> ImpulseDirection:
    return request.param


def test_la_primera_vela_a_favor_que_supera_confirma(direction: ImpulseDirection) -> None:
    # Verde con la mecha en 2012: pasa de los 2010 de la roja.
    found = block([ROJA, (2003.0, 2012.0, 2002.0, 2009.0)], direction=direction)

    assert found is not None
    assert found.confirmed
    assert found.attempt == 1
    assert found.index == 0
    assert found.index_confirmation == 1


def test_la_segunda_oportunidad_mide_contra_el_mismo_nivel(direction: ImpulseDirection) -> None:
    """La referencia no se mueve: la segunda verde vuelve a medirse contra 2010."""
    found = block(
        [
            ROJA,
            (2003.0, 2007.0, 2002.0, 2006.0),  # verde que se queda corta
            (2006.0, 2008.0, 2001.0, 2003.0),  # roja de por medio: no cambia nada
            (2003.0, 2011.0, 2002.0, 2010.0),  # segunda verde: supera los 2010
        ],
        direction=direction,
    )

    assert found is not None
    assert found.confirmed
    assert found.attempt == 2
    assert found.index == 0
    assert found.level == pytest.approx(2010.0 if direction is ALCISTA else 2000.0)
    assert found.index_confirmation == 3


def test_dos_velas_a_favor_sin_superar_desechan_la_via(direction: ImpulseDirection) -> None:
    found = block(
        [
            ROJA,
            (2003.0, 2007.0, 2002.0, 2006.0),
            (2004.0, 2009.0, 2003.0, 2008.0),
            (2008.0, 2009.5, 2007.0, 2007.5),  # tercera verde: ya no hay turno
        ],
        direction=direction,
    )

    assert found is not None
    assert not found.confirmed
    assert found.exhausted
    assert (found.index_first_attempt, found.index_second_attempt) == (1, 2)


def test_el_empate_con_la_mecha_no_supera(direction: ImpulseDirection) -> None:
    """Estricto, igual que la confirmación del OB del módulo 2."""
    found = block(
        [
            ROJA,
            (2003.0, 2010.0, 2002.0, 2009.0),  # llega justo a 2010 y no pasa
            (2003.0, 2010.0, 2002.0, 2009.5),
        ],
        direction=direction,
    )

    assert found is not None
    assert not found.confirmed
    assert found.exhausted


def test_una_sola_oportunidad_no_es_un_descarte(direction: ImpulseDirection) -> None:
    """Quedarse sin tramo no es que el patrón haya fallado: es que se acabó."""
    found = block([ROJA, (2003.0, 2007.0, 2002.0, 2006.0)], direction=direction)

    assert found is not None
    assert not found.confirmed
    assert not found.exhausted
    assert found.index_second_attempt is None


def test_sin_vela_contraria_detras_no_hay_intento(direction: ImpulseDirection) -> None:
    """Lo que se busca es el giro: dos verdes seguidas no lo son."""
    found = block(
        [
            (2000.0, 2004.0, 1999.0, 2003.0),  # verde
            (2003.0, 2012.0, 2002.0, 2011.0),  # verde que supera: no abre nada
        ],
        direction=direction,
    )

    assert found is None


def test_el_doji_ni_abre_el_intento_ni_hace_de_referencia(direction: ImpulseDirection) -> None:
    found = block(
        [
            ROJA,
            (2005.0, 2013.0, 2004.0, 2005.0),  # doji que supera: no confirma nada
            (2005.0, 2012.0, 2004.0, 2009.0),  # verde detrás de un doji: sin intento
        ],
        direction=direction,
    )

    assert found is None


def test_la_referencia_puede_ser_anterior_al_tramo(direction: ImpulseDirection) -> None:
    """La vela que trajo el precio a la zona suele estar antes del toque."""
    found = block(
        [ROJA, (2003.0, 2012.0, 2002.0, 2009.0)], first=1, direction=direction
    )

    assert found is not None and found.index == 0


def test_el_primer_intento_del_tramo_es_el_unico(direction: ImpulseDirection) -> None:
    """Un intento desechado cierra la vía: no se busca el siguiente OB."""
    found = block(
        [
            ROJA,
            (2003.0, 2007.0, 2002.0, 2006.0),
            (2006.0, 2007.0, 2001.0, 2002.0),
            (2002.0, 2006.0, 2001.0, 2005.0),
            (2005.0, 2020.0, 2004.0, 2019.0),  # superaría de sobra, pero ya no cuenta
        ],
        direction=direction,
    )

    assert found is not None
    assert found.index == 0
    assert not found.confirmed


def test_un_tramo_fuera_de_la_serie_es_un_error() -> None:
    series = make_series([ROJA, (2003.0, 2012.0, 2002.0, 2009.0)])

    with pytest.raises(StructureError):
        find_hourly_order_block(series, first=0, last=9, direction=ALCISTA)
