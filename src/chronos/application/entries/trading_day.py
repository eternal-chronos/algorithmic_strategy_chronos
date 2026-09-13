"""El día de operativa, en el reloj de la plataforma.

Tres relojes, los tres del propietario (2026-09-13):

- **la franja**: sólo se buscan entradas —y sólo se llenan— de las 02:00 a las
  11:59. A las 12:00 en punto ya no se mira nada hasta el día siguiente, y un
  límite que siga puesto se quita;
- **el cierre**: una posición viva se cierra a las 16:00 al precio de esa vela,
  sin esperar al stop ni al objetivo;
- **el día**: una operación por día, contando por el día en que ENTRA.

La hora es la de la PLATAFORMA, no la de la plaza (primer ajuste, 2026-09-13).
cTrader dibuja en UTC-4 fijo todo el año —`Etc/GMT+4`, el mismo reloj con el
que el explorador escribe las horas— y el propietario dicta "las 2" mirando esa
pantalla. Leerlo como `America/New_York` daba, en invierno, una hora menos: su
corto del 16-01-2023 se arma a las 06:00 UTC, que en cTrader son las 02:00 y en
Nueva York la 01:00, y el motor no lo daba. Con el reloj fijo la franja es
06:00-15:59 UTC y el cierre las 20:00 UTC, todo el año. Esto es distinto del
ancla de sesión del corte diario (`NY_17:00`), que sí sigue a la plaza porque
así arma cTrader las velas.

Todo se responde para una serie entera de una vez: preguntar vela a vela sería
un bucle Python en el camino caliente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from chronos.application.structure.config import EntriesConfig
from chronos.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class TradingDay:
    """La franja, el cierre y el día, resueltos sobre una serie de velas."""

    timezone: str = "Etc/GMT+4"
    start: time = time(2, 0)
    end: time = time(12, 0)
    flat_at: time = time(16, 0)

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise DomainError(
                f"La franja de operativa debe empezar antes de acabar: "
                f"{self.start} >= {self.end}"
            )

    @classmethod
    def of(cls, config: EntriesConfig) -> TradingDay:
        return cls(
            timezone=config.timezone,
            start=_clock(config.window_start),
            end=_clock(config.window_end),
            flat_at=_clock(config.flat_at),
        )

    def describe(self, clock: str | None = None) -> str:
        """Cómo se escribe allí donde se lee. `clock` es el nombre del reloj que
        se imprime —`UTC-4`— cuando el de la zona se lee al revés (`Etc/GMT+4`).
        """
        return (
            f"{_text(self.start)} a {_text(self.end)} de {clock or self.timezone}, "
            f"cierre a las {_text(self.flat_at)}"
        )

    def in_window(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        """`True` en las velas que ABREN dentro de la franja."""
        minutes = self._local_minutes(timestamps)
        return np.asarray((minutes >= _minutes(self.start)) & (minutes < _minutes(self.end)))

    def flat(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        """`True` en la ÚLTIMA vela de cada día que abre antes del cierre.

        No se busca la vela de las 16:00 en punto: se busca la última que hay
        antes, para que el cierre caiga donde el histórico dice —con sus huecos—
        y no en una vela que puede no existir. Una vela que abre ya pasado el
        cierre no cierra nada: ahí no puede haber posición viva.
        """
        index = pd.DatetimeIndex(timestamps)
        if not len(index):
            return np.zeros(0, dtype=bool)
        minutes = self._local_minutes(index)
        days = self.days(index)
        before = minutes < _minutes(self.flat_at)
        # La siguiente vela ya no está antes del cierre, o es de otro día, o no
        # hay siguiente: ésta es la última del día antes del cierre.
        next_before = np.concatenate((before[1:], [False]))
        next_same_day = np.concatenate((days[1:] == days[:-1], [False]))
        return np.asarray(before & ~(next_before & next_same_day))

    def days(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        """El día de la plaza de cada vela, como ordinal: para contar una por día."""
        local = pd.DatetimeIndex(timestamps).tz_convert(self.timezone)
        naive = local.tz_localize(None).to_numpy(dtype="datetime64[D]")
        return naive.astype("int64")

    def _local_minutes(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        local = pd.DatetimeIndex(timestamps).tz_convert(self.timezone)
        return np.asarray(local.hour * 60 + local.minute)


def _clock(label: str) -> time:
    hour, minute = label.split(":")
    return time(int(hour), int(minute))


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def _text(moment: time) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"
