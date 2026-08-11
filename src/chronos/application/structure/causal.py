"""Series rodantes con causalidad verificable (§3).

Un ATR usado para normalizar el rango de un impulso es una puerta de entrada
clásica al lookahead: basta con leer el valor de la barra actual, que ya incluye
su propio rango, para contaminar la medida. Aquí el contrato es doble:

1. el valor del índice `i` sólo depende de barras de `[0, i-1]`, y
2. leerlo en un índice que la máquina aún no ha alcanzado lanza `LookaheadError`
   en vez de devolver un número.
"""

from __future__ import annotations

import numpy as np

from chronos.domain.strategies.indicators import atr
from chronos.domain.structure.errors import LookaheadError, StructureError


class PriorBarAtr:
    """ATR de Wilder desplazado una barra, con frontera de lectura explícita."""

    def __init__(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        period: int = 14,
        *,
        label: str = "",
    ) -> None:
        if period < 1:
            raise StructureError("El periodo del ATR debe ser >= 1")
        raw = atr(high, low, close, period)
        values = np.full(len(raw), np.nan, dtype=float)
        values[1:] = raw[:-1]  # el valor de `i` es el ATR cerrado en `i-1`
        self._values = values
        self._frontier = -1
        self._label = label

    def __len__(self) -> int:
        return len(self._values)

    @property
    def frontier(self) -> int:
        """Último índice procesado. Nada más allá es legible."""
        return self._frontier

    def advance(self, index: int) -> None:
        """Declara que la barra `index` ya ha cerrado."""
        if index < self._frontier:
            raise StructureError("La frontera de una serie causal no retrocede")
        self._frontier = index

    def at(self, index: int) -> float:
        """Valor en `index`; `NaN` durante el calentamiento del ATR."""
        if index < 0 or index >= len(self._values):
            raise StructureError(f"Índice {index} fuera de la serie ({len(self._values)} barras)")
        if index > self._frontier:
            raise LookaheadError(
                f"{self._label or 'ATR'}: se pidió el índice {index} con la serie "
                f"procesada hasta {self._frontier}"
            )
        return float(self._values[index])
