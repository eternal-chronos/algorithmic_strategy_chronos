"""El OB suelto de M15 (§1.4 y caso 9 del §8), con los esperados escritos a mano.

En M15 no se calculan impulsos, así que aquí no hay ID que valga: lo único que
define la zona es una vela de color contrario superada, mecha incluida, por una
vela del color de la dirección buscada.
"""

from __future__ import annotations

import pytest

from chronos.application.entries.synthetic_run import frame_of
from chronos.domain.entries.loose_order_block import find_loose_order_block
from chronos.domain.entries.synthetic_entries import (
    M15_LOOSE_DOWN,
    M15_LOOSE_UP,
    SYNTHETIC_ENTRY_START,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.zones import CandleSeries

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA


def series(candles: tuple[tuple[float, float, float, float], ...]) -> CandleSeries:
    return CandleSeries.of(frame_of(candles, SYNTHETIC_ENTRY_START, "15min"))


# --- Caso 9 del §8 -----------------------------------------------------------


def test_la_zona_es_la_vela_contraria_entera() -> None:
    """`m1` va de 1998 a 2003: la vela entera, mechas y cuerpo, como el OB del módulo 2."""
    block = find_loose_order_block(series(M15_LOOSE_UP), direction=UP, first=0, through=3)

    assert block is not None
    assert (block.low, block.high) == (1998.00, 2003.00)
    assert block.index_defining == 1
    assert block.index_confirmation == 3


def test_el_borde_interior_es_el_que_el_precio_encuentra_primero() -> None:
    """En una entrada larga el interior es el techo y el exterior el suelo: ahí va el stop."""
    block = find_loose_order_block(series(M15_LOOSE_UP), direction=UP, first=0, through=3)

    assert block is not None
    assert block.inner == 2003.00
    assert block.outer == 1998.00


def test_una_vela_del_color_que_no_supera_la_mecha_no_confirma() -> None:
    """`m2` es verde y se queda en 2000: el OB todavía no existe cuando cierra."""
    block = find_loose_order_block(series(M15_LOOSE_UP), direction=UP, first=0, through=2)
    assert block is None


def test_no_hace_falta_ningun_id_de_m15() -> None:
    """La prueba entera se hace sin detector: no hay ID que consultar ni que exigir."""
    block = find_loose_order_block(series(M15_LOOSE_UP), direction=UP, first=0, through=3)
    assert block is not None
    assert not hasattr(block, "id_num")


def test_el_espejo_produce_la_zona_espejo() -> None:
    block = find_loose_order_block(series(M15_LOOSE_DOWN), direction=DOWN, first=0, through=3)

    assert block is not None
    assert (block.low, block.high) == (1997.00, 2002.00)
    assert block.inner == 1997.00
    assert block.outer == 2002.00


# --- Causalidad (§7) ---------------------------------------------------------


def test_pedirlo_mas_alla_de_la_serie_es_lookahead() -> None:
    candles = series(M15_LOOSE_UP)
    with pytest.raises(LookaheadError):
        find_loose_order_block(candles, direction=UP, first=0, through=len(candles))


def test_la_vela_contraria_puede_ser_anterior_a_la_ventana() -> None:
    """Lo que la ventana acota es cuándo se SUPERA, que es cuando el OB existe.

    Lo normal es que la vela contraria sea anterior a la confirmación de H1: es
    la que dejó el hueco al que el precio vuelve. Exigirle estar dentro de la
    ventana no lo pide el §1.4 y dejaría fuera el caso corriente.
    """
    block = find_loose_order_block(series(M15_LOOSE_UP), direction=UP, first=3, through=3)

    assert block is not None
    assert block.index_defining == 1
    assert block.index_confirmation == 3


def test_el_ob_suelto_nunca_puede_salir_plano() -> None:
    """Una zona de altura cero es imposible aquí, y no por casualidad.

    La vela que define el OB es de color **contrario**, así que su cuerpo no es
    cero, y entonces `high > low` por fuerza: la zona siempre mide algo. El
    guardarraíl `zona_de_entrada_plana` existe igualmente como red y por eso sale
    a cero en el informe; que salga a cero es un dato, no un olvido.
    """
    candles = series(
        (
            (2000.00, 2000.10, 1999.90, 1999.95),  # roja, cuerpo minúsculo
            (1999.95, 2000.20, 1999.90, 2000.15),  # verde: supera 2000.10
        )
    )
    block = find_loose_order_block(candles, direction=UP, first=0, through=1)

    assert block is not None
    assert block.index_defining == 0
    assert block.is_flat is False
    assert block.height > 0.0


def test_se_devuelve_el_primero_confirmado_y_no_el_mejor() -> None:
    """El primero es el único que el propietario podría haber operado.

    Elegir "el mejor" de la ventana exigiría conocer la ventana entera, que es
    justo lo que no se sabe en el momento de decidir.
    """
    candles = series(
        (
            (2000.00, 2001.00, 1990.00, 1991.00),  # roja ancha
            (1991.00, 2002.00, 1990.00, 2001.00),  # verde: supera 2001 -> confirma
            (2001.00, 2002.00, 1995.00, 1996.00),  # roja estrecha
            (1996.00, 2003.00, 1995.00, 2002.00),  # verde: confirmaría la otra
        )
    )
    block = find_loose_order_block(candles, direction=UP, first=0, through=3)

    assert block is not None
    assert block.index_confirmation == 1
    assert block.index_defining == 0
