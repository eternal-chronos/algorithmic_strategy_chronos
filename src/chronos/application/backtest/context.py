"""Implementación del puerto `MarketView`.

Solo expone datos hasta la barra actual: los cortes se hacen con vistas de numpy
(coste O(1)), de modo que una estrategia no puede leer el futuro por accidente.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

import numpy as np

from chronos.application.backtest.broker import SimulatedBroker
from chronos.domain.bar import Bar, MarketData
from chronos.domain.enums import Side
from chronos.domain.errors import StrategyError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.position import Position

_COLUMNS = ("open", "high", "low", "close", "volume")


class BarContext:
    """Contexto de la barra en curso que recibe la estrategia."""

    __slots__ = ("_arrays", "_bar", "_broker", "_data", "_index", "_spec")

    def __init__(self, data: MarketData, broker: SimulatedBroker, spec: InstrumentSpec) -> None:
        self._data = data
        self._broker = broker
        self._spec = spec
        self._arrays = {name: np.asarray(getattr(data, name), dtype=float) for name in _COLUMNS}
        self._index = 0
        self._bar: Bar | None = None

    # --- Uso interno del motor ---------------------------------------------

    def move_to(self, index: int) -> None:
        self._index = index
        self._bar = None

    # --- MarketView ---------------------------------------------------------

    @property
    def index(self) -> int:
        return self._index

    @property
    def bar(self) -> Bar:
        if self._bar is None:
            self._bar = self._data.bar_at(self._index)
        return self._bar

    @property
    def now(self) -> datetime:
        return self._data.timestamps[self._index]

    @property
    def spec(self) -> InstrumentSpec:
        return self._spec

    @property
    def equity(self) -> float:
        return self._broker.account.equity

    @property
    def balance(self) -> float:
        return self._broker.account.balance

    @property
    def positions(self) -> Sequence[Position]:
        return tuple(self._broker.positions)

    def has_position(self, side: Side | None = None) -> bool:
        return self._broker.has_position(side)

    def history(self, column: str) -> Sequence[float]:
        """Columna recortada hasta la barra actual (incluida)."""
        return cast(Sequence[float], self._array(column)[: self._index + 1])

    def value(self, column: str, offset: int = 0) -> float:
        """Valor de la columna `offset` barras atrás (0 = barra actual)."""
        if offset < 0:
            raise StrategyError("No se puede leer una barra futura: offset debe ser >= 0")
        position = self._index - offset
        if position < 0:
            raise StrategyError(f"Histórico insuficiente: se pidió la barra {position}")
        return float(self._array(column)[position])

    def _array(self, column: str) -> np.ndarray:
        try:
            return self._arrays[column]
        except KeyError:
            raise StrategyError(
                f"Columna desconocida '{column}'. Disponibles: {', '.join(_COLUMNS)}"
            ) from None
