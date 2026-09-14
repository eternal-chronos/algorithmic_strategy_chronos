"""Probabilidad de zona: qué hizo el precio en el pasado en sitios como éste. **Sólo dibujo.**

Igual que `zone_signals.py` y `contacts.py`, esto **mide** y no decide: ninguna
regla del detector lo lee, ninguna zona se mueve por su existencia y no hay
entradas detrás. Es el número que el propietario quiere ver escrito sobre cada
zona del explorador: si el precio toca este PUL, ¿cuántas veces en el pasado
aguantó y el ID siguió, y cuántas lo atravesó y el ID rompió?

Dos cuentas, las dos hechas aquí sobre arrays y sin saber qué es un ID:

- **`Tally`** — `k` casos favorables de `n`, con su proporción y el intervalo de
  Wilson al 95 %. Un 70 % con tres casos y un 70 % con trescientos no son el
  mismo número, y el intervalo es lo que lo dice.
- **`band_visits`** — las visitas del precio a una franja de precio concreta:
  por qué lado llegó y por qué lado salió. Es la lectura «sólo de precio» que
  pide el propietario: independientemente de la estructura, cuántas veces el
  precio entró en `[lo, hi]` viniendo de arriba y volvió a salir por arriba.
- **`tallies_known_at`** — cuántos casos estaban resueltos en cada instante y
  cuántos de ellos fueron favorables. Es lo que hace que la cifra escrita sobre
  una zona sólo cuente lo que **ya había pasado** cuando nació: sin esto, una
  zona de 2020 llevaría encima desenlaces de 2024.

Nada de aquí mira adelante: cada función recibe hasta dónde puede mirar y no
se le da el resto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from chronos.domain.structure.errors import StructureError

#: Intervalo de Wilson al 95 %: el cuantil normal de dos colas.
WILSON_Z = 1.96


@dataclass(frozen=True, slots=True)
class Tally:
    """`k` casos favorables de `n`. Qué es «favorable» lo dice quien cuenta."""

    n: int
    k: int

    def __post_init__(self) -> None:
        if self.n < 0 or not 0 <= self.k <= self.n:
            raise StructureError(f"Recuento imposible: {self.k} favorables de {self.n}")

    @property
    def empty(self) -> bool:
        return self.n == 0

    @property
    def share(self) -> float | None:
        """Proporción favorable, o `None` sin casos: sin datos no hay porcentaje."""
        return None if self.n == 0 else self.k / self.n

    def interval(self, z: float = WILSON_Z) -> tuple[float, float] | None:
        """Intervalo de Wilson para la proporción, o `None` sin casos.

        Se prefiere al intervalo normal porque no se sale de `[0, 1]` ni se
        colapsa a cero con `k = 0` o `k = n`, que con pocos toques es lo
        habitual.
        """
        if self.n == 0:
            return None
        n, p = float(self.n), self.k / self.n
        z2 = z * z
        denominator = 1.0 + z2 / n
        centre = (p + z2 / (2.0 * n)) / denominator
        half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denominator
        return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True, slots=True)
class BandVisits:
    """Visitas del precio a una franja, por el lado del que llegó.

    En las dos cuentas «favorable» es **salir por arriba**. Leído desde cada
    lado: llegando desde arriba, salir por arriba es rebotar y salir por abajo
    atravesar; llegando desde abajo es al revés. Quien dibuja lo orienta según
    de dónde viene el precio en su caso.
    """

    from_above: Tally
    from_below: Tally


def band_visits(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    low_edge: float,
    high_edge: float,
    until: int,
) -> BandVisits:
    """Visitas a `[low_edge, high_edge]` en las barras `[0, until]`, resueltas ahí dentro.

    Una visita **empieza** en la barra cuyo rango entra en la franja —bordes
    incluidos, la misma lectura que el toque de zona— cuando el cierre de la
    barra anterior estaba fuera: ese cierre dice de qué lado llega. **Termina**
    en la primera barra, ella incluida, que cierra fuera de la franja, y por
    dónde cierra es por dónde salió. Mientras los cierres se quedan dentro no
    empieza ninguna visita nueva: es la misma.

    `until` es la última barra que se puede mirar. Una visita que a esa altura
    todavía no ha cerrado fuera no cuenta: no se sabe cómo acabó, y contarla
    como lo que sea sería inventarlo. Con `until < 0` no hay pasado y las dos
    cuentas salen vacías.
    """
    if not len(high) == len(low) == len(close):
        raise StructureError("Las series de high, low y close no miden lo mismo")
    if low_edge > high_edge:
        raise StructureError(f"Franja al revés: [{low_edge}, {high_edge}]")
    stop = until + 1
    if stop > len(close):
        raise StructureError(f"`until` {until} fuera de la serie ({len(close)} barras)")
    if stop <= 0:
        return BandVisits(from_above=Tally(0, 0), from_below=Tally(0, 0))

    highs, lows, closes = high[:stop], low[:stop], close[:stop]
    reaches = (highs >= low_edge) & (lows <= high_edge)
    above = closes > high_edge
    below = closes < low_edge
    outside = above | below
    # El cierre anterior es el que dice de dónde viene el precio; la primera
    # barra no tiene y por tanto no puede empezar una visita.
    arrived_above = reaches & np.concatenate(([False], above[:-1]))
    arrived_below = reaches & np.concatenate(([False], below[:-1]))

    # Primera barra en o después de cada posición que cierra fuera: es donde
    # termina la visita que empiece ahí. `size` marca que no hay ninguna.
    size = len(closes)
    candidates = np.where(outside, np.arange(size), size)
    exit_at = np.minimum.accumulate(candidates[::-1])[::-1]
    resolved = exit_at < size
    exit_up = np.zeros(size, dtype=bool)
    exit_up[resolved] = above[exit_at[resolved]]

    def tally(starts: np.ndarray) -> Tally:
        done = starts & resolved
        return Tally(n=int(done.sum()), k=int((done & exit_up).sum()))

    return BandVisits(from_above=tally(arrived_above), from_below=tally(arrived_below))


def tallies_known_at(
    *,
    resolved_at: np.ndarray,
    favourable: np.ndarray,
    moments: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Cuántos casos estaban resueltos en cada `moments[j]`, y cuántos fueron favorables.

    `resolved_at[i]` es el instante en que se supo el desenlace del caso `i`, en
    la misma escala entera que `moments`. Un caso cuenta en `moments[j]` si
    `resolved_at[i] <= moments[j]`: lo que se resolvió justo en ese instante ya
    se sabe, lo que se resolvió después no. Devuelve `(n, k)` alineados con
    `moments`, en el orden en que llegan.
    """
    if resolved_at.shape != favourable.shape:
        raise StructureError("`resolved_at` y `favourable` no miden lo mismo")
    if resolved_at.size == 0:
        zeros = np.zeros(moments.shape, dtype=np.int64)
        return zeros, zeros.copy()
    order = np.argsort(resolved_at, kind="stable")
    cumulative = np.cumsum(favourable[order].astype(np.int64))
    counts = np.searchsorted(resolved_at[order], moments, side="right")
    favourable_counts = np.where(counts > 0, cumulative[np.maximum(counts - 1, 0)], 0)
    return counts.astype(np.int64), favourable_counts.astype(np.int64)
