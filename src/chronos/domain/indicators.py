"""Indicadores sobre arrays de precios. Funciones puras: ni red, ni disco, ni reloj.

Aquí no se decide nada. Un indicador es una lectura del histórico —un número por
vela— y quien lo interprete vive fuera. Todo lo que se calcula usa **sólo velas
ya cerradas hasta esa posición**: el valor en `t` no puede mirar a `t+1`, que es
lo que permite dibujarlo en el replay sin adelantarse al motor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(closes: np.ndarray, period: int) -> np.ndarray:
    """RSI de Wilder sobre `closes`, alineado con ellos y del mismo largo.

    Devuelve `NaN` en las primeras `period` posiciones: hasta la vela `period` no
    hay variaciones suficientes para la primera media y rellenar ese hueco con
    un número lo haría parecer un dato.

    Wilder, no la media simple: la primera media es la de las `period` primeras
    variaciones y a partir de ahí cada una arrastra la anterior con peso
    `(period - 1) / period`. Eso es exactamente un suavizado exponencial de
    `alpha = 1 / period` sembrado con esa media, así que se calcula con `ewm` en
    vez de con un bucle vela a vela.

    Sin variaciones a la baja en la ventana el índice es 100 —sólo hubo subidas—
    y con el precio completamente plano es 50: ni fuerza ni debilidad, en vez de
    la división por cero que da la fórmula.
    """
    if period < 1:
        raise ValueError("el periodo del RSI tiene que ser al menos 1")
    values = np.asarray(closes, dtype=float)
    out = np.full(values.size, np.nan)
    if values.size <= period:
        return out

    delta = np.diff(values)
    gains = np.where(delta > 0.0, delta, 0.0)
    losses = np.where(delta < 0.0, -delta, 0.0)
    average_gain = _wilder(gains, period)
    average_loss = _wilder(losses, period)

    # `np.errstate` calla la división por cero: los dos casos que la producen se
    # resuelven justo debajo, y con un aviso por medio el log del explorador se
    # llenaría de ruido en cada corrida.
    with np.errstate(divide="ignore", invalid="ignore"):
        strength = average_gain / average_loss
        index = 100.0 - 100.0 / (1.0 + strength)
    flat = (average_gain == 0.0) & (average_loss == 0.0)
    index = np.where(flat, 50.0, index)
    index = np.where(~flat & (average_loss == 0.0), 100.0, index)

    out[period:] = index
    return out


def _wilder(changes: np.ndarray, period: int) -> np.ndarray:
    """La media de Wilder de `changes`, desde la posición `period - 1`.

    `changes` son las variaciones —una menos que velas—, así que la primera
    media cae en la vela `period` y de ahí en adelante hay una por vela.
    """
    seeded = changes[period - 1 :].copy()
    seeded[0] = changes[:period].mean()
    smoothed = pd.Series(seeded).ewm(alpha=1.0 / period, adjust=False).mean()
    return smoothed.to_numpy(dtype=float)
