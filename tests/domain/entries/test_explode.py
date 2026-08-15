"""`explode`: partir una vela en trozos que vuelven a componerla exactamente.

Es la pieza que sostiene el día sintético del §8. Si la explosión no fuera
consistente, las cuatro temporalidades del sintético no serían la misma historia
y la prueba estaría midiendo la explosión en vez de la cascada.

El camino que reconstruye es el conservador de siempre —en una vela verde el
precio baja al `low`, sube al `high` y vuelve al cierre; en una roja, al revés—.
Es una elección declarada, no un dato: el OHLC de una vela no dice en qué orden
se visitaron sus extremos.
"""

from __future__ import annotations

import pytest

from chronos.domain.entries.synthetic_entries import explode, explode_all
from chronos.domain.structure.synthetic_break import (
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_ORDER_BLOCK_UP,
)
from chronos.domain.structure.synthetic_zones import Candle

Parts = tuple[Candle, ...]


def compose(parts: Parts) -> Candle:
    """Vuelve a juntar los trozos como haría cualquier agregador."""
    return (
        parts[0][0],
        max(part[1] for part in parts),
        min(part[2] for part in parts),
        parts[-1][3],
    )


CANDLES: tuple[Candle, ...] = (
    (2000.00, 2012.00, 1994.00, 2010.00),  # verde con las dos mechas
    (2010.00, 2011.00, 1990.00, 1995.00),  # roja con mechón inferior
    (2000.00, 2000.00, 1990.00, 1990.00),  # roja sin mechas
    (2000.00, 2005.00, 1995.00, 2000.00),  # doji con mechas a los dos lados
    (2000.00, 2000.00, 2000.00, 2000.00),  # los cuatro precios iguales
)


@pytest.mark.parametrize("candle", CANDLES)
@pytest.mark.parametrize("parts", [1, 2, 4, 16, 240])
def test_los_trozos_vuelven_a_componer_la_vela(candle: Candle, parts: int) -> None:
    exploded = explode(candle, parts)

    assert len(exploded) == parts
    assert compose(exploded) == pytest.approx(candle)


@pytest.mark.parametrize("candle", CANDLES)
def test_ningun_trozo_se_sale_de_la_vela(candle: Candle) -> None:
    _open, high, low, _close = candle
    for part in explode(candle, 16):
        assert low - 1e-9 <= part[2] <= part[1] <= high + 1e-9


@pytest.mark.parametrize("candle", CANDLES)
def test_cada_trozo_es_una_vela_valida(candle: Candle) -> None:
    """`low <= open, close <= high`, que es lo que valida el contrato de barras."""
    for part in explode(candle, 8):
        open_, high, low, close = part
        assert low <= open_ <= high
        assert low <= close <= high


def test_las_temporalidades_del_sintetico_son_la_misma_historia() -> None:
    """H1, M15 y M1 tienen que recomponer exactamente las velas de H4.

    Es la garantía que sustituye al agregador del proyecto en el sintético: sin
    ella, `application` tendría que importar `infrastructure` para probar esto.
    """
    for candles in (SYNTHETIC_BREAK_UP, SYNTHETIC_ORDER_BLOCK_UP):
        for size in (4, 16, 240):
            parts = explode_all(candles, size)
            assert len(parts) == len(candles) * size
            for position, candle in enumerate(candles):
                chunk = parts[position * size : (position + 1) * size]
                assert compose(chunk) == pytest.approx(candle)


def test_partir_en_menos_de_un_trozo_es_un_error() -> None:
    with pytest.raises(ValueError, match="al menos un trozo"):
        explode(CANDLES[0], 0)


def test_una_vela_sin_rango_produce_trozos_sin_rango() -> None:
    """No se inventa movimiento donde el OHLC dice que no lo hubo."""
    flat = (2000.00, 2000.00, 2000.00, 2000.00)
    assert explode(flat, 4) == (flat, flat, flat, flat)
