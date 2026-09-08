"""Los dos patrones de M15 con los que se afina la entrada: OB y FVG.

Geometría pura sobre arrays, igual que las zonas: aquí no hay orden, ni stop, ni
tamaño. Lo único que se produce es **un tramo de precio y la vela en la que se
supo que existía**; quién lo usa para poner un límite, y con qué stop, es la
capa de aplicación.

Los dos miran la vela ENTERA —mecha incluida—, no el cuerpo: son sitios a los
que el precio vuelve, y el precio vuelve a donde llegó.

**OB (order block)** — la última vela del color contrario antes de que el precio
se vaya. Para una venta: una vela **alcista** y, dentro de las
`displacement` velas siguientes, un **cierre por debajo de su mínimo**. La zona
es esa vela entera, `[low, high]`. Se sabe en la vela que cierra por debajo, no
antes: hasta entonces es una vela verde cualquiera.

**FVG (fair value gap)** — el hueco de tres velas. Para una venta: la vela `i+1`
no llega a tocar el mínimo de la `i-1`, o sea `high[i+1] < low[i-1]`. La zona es
ese hueco, `[high[i+1], low[i-1]]`, y se sabe al cerrar la vela `i+1`.

**Nada de esto mira al futuro.** Cada patrón lleva `index_known`, la vela en cuyo
CIERRE se conoce, y quien lo consuma no puede usarlo antes: la vela que lo
completa es siempre posterior o igual a la que lo define.

`near` y `far` significan lo mismo en los dos y en las dos direcciones: `near` es
el borde que el precio encuentra **primero** al volver a la zona —el de abajo en
un patrón bajista, al que se sube; el de arriba en uno alcista, al que se baja— y
`far` el que hay que atravesar para dejarla atrás. Un límite se pone en `near` y
el stop, detrás de `far`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError

#: Velas que se le dan al precio para irse tras el OB. **Parámetro abierto**: es
#: lo único que el patrón no fija por geometría, y el propietario no lo ha
#: cerrado. Con 1 el OB exige que la vela siguiente ya cierre al otro lado.
DEFAULT_DISPLACEMENT = 3


class PatternKind(StrEnum):
    """Los dos patrones. Los valores salen al payload del explorador."""

    ORDER_BLOCK = "OB"
    FAIR_VALUE_GAP = "FVG"


@dataclass(frozen=True, slots=True)
class PricePattern:
    """Un tramo de M15 al que el precio puede volver, con su fecha de nacimiento.

    `direction` es la dirección de la **operación** que habilita, no la del
    impulso: un OB bajista es una vela alcista, y es a propósito.
    """

    kind: PatternKind
    direction: ImpulseDirection
    #: Vela que define la zona: la del OB, o la de en medio del hueco.
    index_origin: int
    #: Vela en cuyo CIERRE se sabe que el patrón existe. Nunca anterior a
    #: `index_origin`.
    index_known: int
    low: float
    high: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise StructureError(
                f"Patrón {self.kind.value} con los bordes al revés: "
                f"[{self.low}, {self.high}]"
            )
        if self.index_known < self.index_origin:
            raise StructureError(
                f"Patrón {self.kind.value} conocido ({self.index_known}) antes de "
                f"la vela que lo define ({self.index_origin})"
            )

    @property
    def height(self) -> float:
        return self.high - self.low

    @property
    def near(self) -> float:
        """Borde que el precio encuentra primero al volver: donde va el límite."""
        return self.low if self.direction is ImpulseDirection.BAJISTA else self.high

    @property
    def far(self) -> float:
        """Borde que hay que atravesar para dejar la zona atrás: donde va el stop."""
        return self.high if self.direction is ImpulseDirection.BAJISTA else self.low

    def overlaps(self, low: float, high: float) -> bool:
        """`True` si el patrón corta ese tramo de precio, bordes incluidos."""
        return self.low <= high and low <= self.high


def order_blocks(
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: ImpulseDirection,
    displacement: int = DEFAULT_DISPLACEMENT,
) -> tuple[PricePattern, ...]:
    """Los OB que habilitan operaciones en `direction`, en orden cronológico.

    Para una venta (`BAJISTA`): vela con cuerpo **alcista** y, en las
    `displacement` velas siguientes, un cierre por debajo de su mínimo. La vela
    que cierra es la que lo hace existir, y es la que fecha el patrón.

    El doji no cuenta como vela contraria: no tiene dirección, igual que en el
    módulo 1.
    """
    _check_series(open_, high, low, close)
    if displacement < 1:
        raise StructureError(f"El desplazamiento del OB no puede ser {displacement}")
    bearish_trade = direction is ImpulseDirection.BAJISTA
    # La vela del OB va en contra de la operación: verde para vender.
    candidates = close > open_ if bearish_trade else close < open_
    total = len(close)
    # La primera de las `displacement` velas siguientes que cierra al otro lado,
    # buscada de una vez para todas las velas: un desplazamiento por pasada, no
    # una pasada por vela. `-1` es "no se fue".
    known = np.full(total, -1, dtype=np.int64)
    level = low[:] if bearish_trade else high[:]
    for step in range(1, displacement + 1):
        if step >= total:
            break
        shifted = close[step:]
        gone = shifted < level[:-step] if bearish_trade else shifted > level[:-step]
        pending = gone & (known[:-step] < 0) & candidates[:-step]
        known[:-step][pending] = np.flatnonzero(pending) + step
    return tuple(
        PricePattern(
            kind=PatternKind.ORDER_BLOCK,
            direction=direction,
            index_origin=int(origin),
            index_known=int(known[origin]),
            low=float(low[origin]),
            high=float(high[origin]),
        )
        for origin in np.flatnonzero(known >= 0)
    )


def fair_value_gaps(
    *,
    high: np.ndarray,
    low: np.ndarray,
    direction: ImpulseDirection,
) -> tuple[PricePattern, ...]:
    """Los FVG que habilitan operaciones en `direction`, en orden cronológico.

    Para una venta (`BAJISTA`): `high[i+1] < low[i-1]`, el hueco que dejó un
    tramo bajista. La zona es el hueco entero y se sabe al cerrar `i+1`.

    El hueco tiene que ser estricto: dos velas que se tocan exactamente en el
    mismo precio no dejan hueco, igual que "más allá" es estricto en la rotura.
    """
    if len(high) != len(low):
        raise StructureError("Las series de high y low no miden lo mismo")
    if len(high) < 3:
        return ()
    if direction is ImpulseDirection.BAJISTA:
        gap_low, gap_high = high[2:], low[:-2]
    else:
        gap_low, gap_high = high[:-2], low[2:]
    hits = np.flatnonzero(gap_low < gap_high)
    return tuple(
        PricePattern(
            kind=PatternKind.FAIR_VALUE_GAP,
            direction=direction,
            index_origin=int(offset) + 1,
            index_known=int(offset) + 2,
            low=float(gap_low[offset]),
            high=float(gap_high[offset]),
        )
        for offset in hits
    )


def patterns_of(
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: ImpulseDirection,
    displacement: int = DEFAULT_DISPLACEMENT,
) -> tuple[PricePattern, ...]:
    """OB y FVG juntos, ordenados por la vela en la que se supieron.

    Es lo que consume la capa de entradas: cuando el precio vuelve a la zona de
    H1, lo que se busca es "lo más cercano que haya ahí", sea un hueco o un
    bloque. El orden es el de conocimiento —`index_known`— y no el de la vela
    que los define: es el orden en que la máquina los pudo ver.
    """
    both = order_blocks(
        open_=open_,
        high=high,
        low=low,
        close=close,
        direction=direction,
        displacement=displacement,
    ) + fair_value_gaps(high=high, low=low, direction=direction)
    return tuple(
        sorted(both, key=lambda item: (item.index_known, item.index_origin, item.kind.value))
    )


def _check_series(*series: np.ndarray) -> None:
    sizes = {len(item) for item in series}
    if len(sizes) != 1:
        raise StructureError(f"Las series de la vela no miden lo mismo: {sorted(sizes)}")


__all__ = [
    "DEFAULT_DISPLACEMENT",
    "PatternKind",
    "PricePattern",
    "fair_value_gaps",
    "order_blocks",
    "patterns_of",
]
