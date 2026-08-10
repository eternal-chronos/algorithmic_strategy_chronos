"""Puerto de acceso a datos históricos.

El dominio declara qué necesita; la infraestructura decide si viene de parquet,
CSV, una API del bróker o un generador sintético.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from chronos.domain.bar import MarketData
from chronos.domain.enums import Timeframe


class MarketDataRepository(Protocol):
    """Fuente de barras históricas de un símbolo."""

    def load(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MarketData:
        """Devuelve las barras del rango pedido, ordenadas y sin duplicados."""
        ...
