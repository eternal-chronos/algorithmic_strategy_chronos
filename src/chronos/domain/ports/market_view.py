"""Vista que el motor expone a la estrategia en cada barra.

Es un puerto: la implementación concreta vive en la capa de aplicación. Solo
ofrece datos hasta la barra actual, de modo que una estrategia bien escrita no
puede mirar al futuro.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from chronos.domain.bar import Bar
from chronos.domain.enums import Side
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.position import Position


@runtime_checkable
class MarketView(Protocol):
    """Contexto de solo lectura de la barra en curso."""

    @property
    def index(self) -> int:
        """Índice de la barra actual dentro de la serie."""
        ...

    @property
    def bar(self) -> Bar:
        """Barra actual, ya cerrada."""
        ...

    @property
    def now(self) -> datetime:
        """Marca de tiempo de la barra actual."""
        ...

    @property
    def spec(self) -> InstrumentSpec:
        ...

    @property
    def equity(self) -> float:
        ...

    @property
    def balance(self) -> float:
        ...

    @property
    def positions(self) -> Sequence[Position]:
        """Posiciones abiertas ahora mismo."""
        ...

    def has_position(self, side: Side | None = None) -> bool:
        """¿Hay posición abierta (opcionalmente de un lado concreto)?"""
        ...

    def history(self, column: str) -> Sequence[float]:
        """Columna OHLCV recortada hasta la barra actual, ambos extremos incluidos."""
        ...

    def value(self, column: str, offset: int = 0) -> float:
        """Valor de una columna OHLCV; `offset=1` es la barra anterior."""
        ...
