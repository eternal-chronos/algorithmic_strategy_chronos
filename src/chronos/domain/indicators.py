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


#: Lectura del RSI por zonas, tal como lo mira el propietario: no es
#: sobrecompra/sobreventa sino **liquidez** y **nivel**. Por encima de la banda
#: alta hay volumen alcista; por debajo de la baja, bajista; y dentro de la
#: banda el 50 hace de soporte o de resistencia según de dónde venga el índice.
RSI_ABOVE = 1
RSI_INSIDE = 0
RSI_BELOW = -1


def rsi_zones(
    values: np.ndarray, *, upper: float, lower: float
) -> tuple[np.ndarray, np.ndarray]:
    """En qué zona está el RSI en cada vela y desde dónde entró en la banda.

    Devuelve dos arrays de enteros alineados con `values`:

    - **zona**: `RSI_ABOVE` (1) por encima de `upper`, `RSI_BELOW` (-1) por
      debajo de `lower` y `RSI_INSIDE` (0) en la banda. Estar justo en la línea
      es estar dentro: la banda incluye sus bordes;
    - **origen**: sólo tiene sentido dentro de la banda y dice de qué lado venía
      el índice cuando entró: `RSI_ABOVE` si bajó desde arriba —el 50 hace de
      **soporte** y lo que se espera es que rebote hacia arriba—, `RSI_BELOW` si
      subió desde abajo —el 50 hace de **resistencia**— y `RSI_INSIDE` cuando
      no ha salido nunca de la banda desde que hay lectura. Fuera de la banda
      vale `RSI_INSIDE`, que ahí no significa nada.

    Las velas sin lectura (`NaN`) salen con `RSI_INSIDE` en las dos y quien
    dibuje las trata como hueco mirando el propio RSI. El origen de la vela `t`
    sólo depende de las velas hasta `t`: es un estado que se arrastra hacia
    delante y nunca se corrige con lo que venga después.
    """
    if upper <= lower:
        raise ValueError(f"la banda alta ({upper}) tiene que quedar sobre la baja ({lower})")
    readings = np.asarray(values, dtype=float)
    zone = np.zeros(readings.size, dtype=int)
    known = ~np.isnan(readings)
    zone[known & (readings > upper)] = RSI_ABOVE
    zone[known & (readings < lower)] = RSI_BELOW

    # El origen es el último lado del que se salió: se arrastra vela a vela,
    # así que basta con propagar hacia delante la última zona distinta de la
    # banda. `np.maximum.accumulate` sobre las posiciones lo hace sin bucle.
    outside = np.flatnonzero(zone != RSI_INSIDE)
    origin = np.zeros(readings.size, dtype=int)
    if outside.size:
        last_outside = np.full(readings.size, -1)
        last_outside[outside] = outside
        last_outside = np.maximum.accumulate(last_outside)
        seen = last_outside >= 0
        origin[seen] = zone[last_outside[seen]]
    # Fuera de la banda el origen no dice nada: se deja en cero para que nadie
    # lo lea como «viene de arriba» cuando lo que pasa es que ESTÁ arriba.
    origin[zone != RSI_INSIDE] = RSI_INSIDE
    origin[~known] = RSI_INSIDE
    return zone, origin
