"""Adaptadores del puerto `MarketDataRepository`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from chronos.application.backtest.config import DataConfig
from chronos.domain.bar import MarketData
from chronos.domain.enums import Timeframe
from chronos.domain.errors import DomainError
from chronos.infrastructure.data.frames import (
    normalize_frame,
    resample_frame,
    slice_range,
    to_market_data,
)
from chronos.infrastructure.data.synthetic import generate_ohlcv


class FrameBarRepository:
    """Repositorio en memoria: la base de los demás adaptadores.

    Guarda el histórico en su timeframe nativo y resamplea bajo demanda.
    """

    def __init__(self, frame: pd.DataFrame, symbol: str, base_timeframe: Timeframe) -> None:
        self._frame = frame
        self._symbol = symbol
        self._base = base_timeframe

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def load(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> MarketData:
        if symbol.upper() != self._symbol.upper():
            raise DomainError(f"Este repositorio sirve {self._symbol}, no {symbol}")
        if timeframe.minutes < self._base.minutes:
            raise DomainError(
                f"No se puede pasar de {self._base.value} a {timeframe.value}: "
                "el timeframe pedido es más fino que el de los datos"
            )

        frame = slice_range(self._frame, start, end)  # type: ignore[arg-type]
        if timeframe is not self._base:
            frame = resample_frame(frame, timeframe)
        return to_market_data(frame, self._symbol, timeframe)


class ParquetBarRepository(FrameBarRepository):
    """Histórico en parquet: el formato de trabajo del proyecto."""

    def __init__(self, path: str | Path, symbol: str, base_timeframe: Timeframe) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise DomainError(
                f"No se encontró el histórico en {file_path}. "
                "Genera datos con `chronos data synth` o importa los tuyos con `chronos data import`."
            )
        super().__init__(normalize_frame(pd.read_parquet(file_path)), symbol, base_timeframe)


class CsvBarRepository(FrameBarRepository):
    """Histórico en CSV (exportaciones de cTrader, Dukascopy, MT5...)."""

    def __init__(
        self,
        path: str | Path,
        symbol: str,
        base_timeframe: Timeframe,
        timezone: str = "UTC",
    ) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            raise DomainError(f"No se encontró el CSV en {file_path}")
        frame = normalize_frame(pd.read_csv(file_path), timezone=timezone)
        super().__init__(frame, symbol, base_timeframe)


class SyntheticBarRepository(FrameBarRepository):
    """Datos generados. Solo para pruebas de humo del motor."""

    def __init__(
        self,
        symbol: str = "XAUUSD",
        base_timeframe: Timeframe = Timeframe.M1,
        periods: int = 50_000,
        seed: int = 42,
    ) -> None:
        frame = generate_ohlcv(
            periods=periods, freq=base_timeframe.pandas_freq, seed=seed, spread_points=20
        )
        super().__init__(frame, symbol, base_timeframe)


def build_repository(config: DataConfig, symbol: str) -> FrameBarRepository:
    """Fábrica dirigida por configuración."""
    match config.source:
        case "parquet":
            return ParquetBarRepository(config.path, symbol, config.timeframe)
        case "csv":
            return CsvBarRepository(config.path, symbol, config.timeframe, config.timezone)
        case "synthetic":
            return SyntheticBarRepository(symbol, config.timeframe)
        case _:
            raise DomainError(f"Fuente de datos desconocida: {config.source}")
