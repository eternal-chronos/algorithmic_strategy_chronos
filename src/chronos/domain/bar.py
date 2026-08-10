"""Barra OHLCV y colección de barras del dominio."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from chronos.domain.enums import Timeframe
from chronos.domain.errors import InvalidPrice

#: Serie numérica indexable. En ejecución siempre es un `np.ndarray`, pero el
#: dominio solo exige poder medirla e indexarla: así no depende de numpy.
FloatSeries = Sequence[float]


@dataclass(frozen=True, slots=True)
class Bar:
    """Una vela. Invariante: low <= open, close <= high."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float | None = None

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise InvalidPrice(f"Barra inválida en {self.timestamp}: low > high")
        if not (self.low <= self.open <= self.high):
            raise InvalidPrice(f"Barra inválida en {self.timestamp}: open fuera de [low, high]")
        if not (self.low <= self.close <= self.high):
            raise InvalidPrice(f"Barra inválida en {self.timestamp}: close fuera de [low, high]")

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open


@dataclass(frozen=True, slots=True)
class MarketData:
    """Serie de barras en formato columnar.

    Se expone como `Sequence[float]` para mantener el dominio libre de numpy y
    pandas; la infraestructura inyecta arrays de numpy, que son compatibles y
    permiten a las estrategias vectorizar sus indicadores.
    """

    symbol: str
    timeframe: Timeframe
    timestamps: Sequence[datetime]
    open: FloatSeries
    high: FloatSeries
    low: FloatSeries
    close: FloatSeries
    volume: FloatSeries
    spread: FloatSeries | None = None

    def __post_init__(self) -> None:
        n = len(self.timestamps)
        columns = {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        for name, column in columns.items():
            if len(column) != n:
                raise InvalidPrice(f"La columna '{name}' tiene {len(column)} filas, se esperaban {n}")
        if self.spread is not None and len(self.spread) != n:
            raise InvalidPrice("La columna 'spread' no está alineada con el resto")

    def __len__(self) -> int:
        return len(self.timestamps)

    def bar_at(self, index: int) -> Bar:
        return Bar(
            timestamp=self.timestamps[index],
            open=float(self.open[index]),
            high=float(self.high[index]),
            low=float(self.low[index]),
            close=float(self.close[index]),
            volume=float(self.volume[index]),
            spread=float(self.spread[index]) if self.spread is not None else None,
        )
