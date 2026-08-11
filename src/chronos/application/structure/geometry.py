"""Geometría de la vela en roturas y contactos (sección E).

**Se persiste y no la lee ninguna regla del dominio.** Vive en `application/` a
propósito: el dominio no importa este módulo, así que ninguna regla del módulo 1
puede leer estos campos ni aunque alguien lo intente por descuido. Sirve para
que más adelante se pueda estudiar si un intento de rotura rechazado con mechazo
es confirmación —o lo contrario— sin volver a recalcular ocho años de histórico.

Los cinco campos, tal como los pide el enunciado:

    cuerpo_pct          |close - open| / (high - low)
    mecha_sup_pct       (high - max(open, close)) / (high - low)
    mecha_inf_pct       (min(open, close) - low) / (high - low)
    cierre_mas_alla_usd  cuánto cerró la vela más allá del nivel, con signo
    cierre_mas_alla_atr  lo mismo en ATR previo

El signo de `cierre_mas_alla_*` es positivo cuando la vela cerró **fuera** del
límite y negativo cuando cerró dentro: así un `TOQUE_MECHA` se distingue de una
`ROTURA_REAL` sin mirar ninguna otra columna.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Nombres de las columnas que se escriben, en su orden.
GEOMETRY_COLUMNS = (
    "cuerpo_pct",
    "mecha_sup_pct",
    "mecha_inf_pct",
    "cierre_mas_alla_usd",
    "cierre_mas_alla_atr",
)


def atr_by_bar(table: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """ATR previo propagado a todas las barras, para normalizar el informe.

    La tabla de impulsos sólo trae el ATR de las constituciones, así que se
    arrastra hacia delante: el valor de una barra cualquiera es el del último ID
    constituido antes de ella. Es una normalización de informe y **nunca** una
    entrada de ninguna regla; por eso vive aquí y no en `causal.py`, que es el
    ATR con frontera de lectura que sí usa el módulo.
    """
    if table.empty:
        return np.full(len(index), np.nan)
    known = pd.Series(
        pd.to_numeric(table["atr_previo"], errors="coerce").to_numpy(dtype=float),
        index=pd.DatetimeIndex(table["ts_constitucion"]),
    ).sort_index()
    return known.reindex(index, method="ffill").to_numpy(dtype=float)


def geometry_of(
    bars: pd.DataFrame,
    indices: np.ndarray,
    *,
    levels: np.ndarray,
    beyond_upper: np.ndarray,
    atr: np.ndarray,
) -> pd.DataFrame:
    """Geometría de las barras `indices` respecto de sus `levels`, de una vez.

    `beyond_upper` dice, para cada fila, hacia dónde se mide "más allá": `True`
    para el límite alto del rango y `False` para el bajo.

    Las cuatro series se recorren enteras con numpy y no fila a fila: son ocho
    años de contactos y aquí no hay nada que justifique un bucle de Python.
    """
    indices = np.asarray(indices, dtype=int)
    if indices.size == 0:
        return pd.DataFrame(columns=list(GEOMETRY_COLUMNS))

    open_ = bars["open"].to_numpy(dtype=float)[indices]
    high = bars["high"].to_numpy(dtype=float)[indices]
    low = bars["low"].to_numpy(dtype=float)[indices]
    close = bars["close"].to_numpy(dtype=float)[indices]

    span = high - low
    # Una vela sin recorrido no tiene proporciones que repartir; devolver 0 o 1
    # sería inventarse una forma que el dato no da.
    with np.errstate(invalid="ignore", divide="ignore"):
        usable = span > 0
        body = np.where(usable, np.abs(close - open_) / span, np.nan)
        upper_wick = np.where(usable, (high - np.maximum(open_, close)) / span, np.nan)
        lower_wick = np.where(usable, (np.minimum(open_, close) - low) / span, np.nan)

        levels = np.asarray(levels, dtype=float)
        beyond_upper = np.asarray(beyond_upper, dtype=bool)
        signed = np.where(beyond_upper, close - levels, levels - close)

        atr = np.asarray(atr, dtype=float)
        in_atr = np.where(np.isfinite(atr) & (atr > 0), signed / atr, np.nan)

    return pd.DataFrame(
        {
            "cuerpo_pct": body,
            "mecha_sup_pct": upper_wick,
            "mecha_inf_pct": lower_wick,
            "cierre_mas_alla_usd": signed,
            "cierre_mas_alla_atr": in_atr,
        }
    )
