"""Utilidades sobre el DataFrame canónico de barras.

Formato canónico:
    índice : DatetimeIndex tz-aware, ordenado, sin duplicados
    columnas: open, high, low, close, volume  (+ spread opcional, en precio)
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from datetime import datetime
from typing import Any, cast

import pandas as pd

from chronos.domain.bar import FloatSeries, MarketData
from chronos.domain.enums import Timeframe
from chronos.domain.errors import InvalidPrice

REQUIRED_COLUMNS = ("open", "high", "low", "close")
OPTIONAL_COLUMNS = ("volume", "spread")

_COLUMN_ALIASES = {
    "o": "open", "h": "high", "l": "low", "c": "close",
    "v": "volume", "vol": "volume", "tickvol": "volume", "tick_volume": "volume",
    "bid": "close", "price": "close",
    "date": "timestamp", "datetime": "timestamp", "time": "timestamp",
    "gmt time": "timestamp", "local time": "timestamp", "date_time": "timestamp",
    "<date>": "timestamp", "<open>": "open", "<high>": "high",
    "<low>": "low", "<close>": "close", "<tickvol>": "volume", "<spread>": "spread",
}


def normalize_frame(frame: pd.DataFrame, timezone: str = "UTC") -> pd.DataFrame:
    """Lleva un DataFrame arbitrario al formato canónico.

    Acepta la marca de tiempo como índice o como columna (`timestamp`, `date`,
    `time`, `Gmt time`...). Si es naíf, se interpreta en `timezone` y se
    convierte a UTC.
    """
    df = frame.copy()
    df.columns = [_COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]

    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=False, format="mixed")

    index = pd.DatetimeIndex(df.index)
    index = index.tz_localize(timezone) if index.tz is None else index.tz_convert("UTC")
    df.index = index.tz_convert("UTC") if index.tz is not None else index
    df.index.name = "timestamp"

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise InvalidPrice(f"Faltan columnas obligatorias: {', '.join(missing)}")

    keep = [*REQUIRED_COLUMNS, *(c for c in OPTIONAL_COLUMNS if c in df.columns)]
    df = df[keep].astype(float)
    if "volume" not in df.columns:
        df["volume"] = 0.0

    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=list(REQUIRED_COLUMNS))


def validate_frame(frame: pd.DataFrame) -> None:
    """Comprueba las invariantes del formato canónico. Lanza `InvalidPrice`."""
    if frame.empty:
        raise InvalidPrice("El conjunto de datos está vacío")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise InvalidPrice("El índice debe ser un DatetimeIndex")
    if frame.index.tz is None:
        raise InvalidPrice("El índice debe ser tz-aware (UTC)")
    if not frame.index.is_monotonic_increasing:
        raise InvalidPrice("El índice no está ordenado cronológicamente")
    if frame.index.has_duplicates:
        raise InvalidPrice("Hay marcas de tiempo duplicadas")

    bad_range = frame["high"] < frame["low"]
    if bool(bad_range.any()):
        first = frame.index[bad_range][0]
        raise InvalidPrice(f"Barras con high < low, la primera en {first}")

    outside = (
        (frame["open"] > frame["high"]) | (frame["open"] < frame["low"])
        | (frame["close"] > frame["high"]) | (frame["close"] < frame["low"])
    )
    if bool(outside.any()):
        first = frame.index[outside][0]
        raise InvalidPrice(f"Barras con open/close fuera del rango, la primera en {first}")

    if bool((frame[list(REQUIRED_COLUMNS)] <= 0).any().any()):
        raise InvalidPrice("Hay precios menores o iguales a cero")


def resample_frame(frame: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Reagrupa a un timeframe superior.

    Las barras se etiquetan con el inicio de su intervalo (convención de las
    plataformas de trading): la vela M15 de las 10:00 cubre [10:00, 10:15).
    """
    aggregation: dict[Hashable, Any] = {
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }
    if "spread" in frame.columns:
        aggregation["spread"] = "mean"

    resampled = frame.resample(timeframe.pandas_freq, label="left", closed="left").agg(aggregation)
    return resampled.dropna(subset=["open", "high", "low", "close"])


def to_market_data(frame: pd.DataFrame, symbol: str, timeframe: Timeframe) -> MarketData:
    """Convierte el DataFrame canónico en el objeto de dominio."""
    validate_frame(frame)
    def column(name: str) -> FloatSeries:
        return cast(FloatSeries, frame[name].to_numpy(dtype=float))

    return MarketData(
        symbol=symbol,
        timeframe=timeframe,
        # Un DatetimeIndex devuelve `pd.Timestamp` al indexar, que es un
        # `datetime`; el cast solo salva la varianza del tipo declarado.
        timestamps=cast(Sequence[datetime], pd.DatetimeIndex(frame.index)),
        open=column("open"),
        high=column("high"),
        low=column("low"),
        close=column("close"),
        volume=column("volume"),
        spread=column("spread") if "spread" in frame.columns else None,
    )


def slice_range(
    frame: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None
) -> pd.DataFrame:
    """Recorta por fechas, tolerando límites naíf (se asumen UTC)."""
    result = frame
    if start is not None:
        start_ts = pd.Timestamp(start)
        start_ts = start_ts.tz_localize("UTC") if start_ts.tz is None else start_ts.tz_convert("UTC")
        result = result[result.index >= start_ts]
    if end is not None:
        end_ts = pd.Timestamp(end)
        end_ts = end_ts.tz_localize("UTC") if end_ts.tz is None else end_ts.tz_convert("UTC")
        result = result[result.index <= end_ts]
    return result
