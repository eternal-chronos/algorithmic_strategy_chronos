"""El día de operativa, en la hora de Nueva York.

Tres relojes, los tres del propietario (2026-09-13):

- **la franja**: sólo se buscan entradas —y sólo se llenan— de las 02:00 a las
  11:59 de Nueva York. A las 12:00 en punto ya no se mira nada hasta el día
  siguiente, y un límite que siga puesto se quita;
- **el cierre**: una posición viva se cierra a las 16:00 de Nueva York al precio
  de esa vela, sin esperar al stop ni al objetivo;
- **el día**: una operación por día, contando por el día de Nueva York en que
  ENTRA.

La hora es la de la plaza y no un desfase fijo en UTC: Nueva York abre a la
misma hora local todo el año, y en UTC la franja se mueve una hora dos veces al
año. Se resuelve convirtiendo a `America/New_York`, con el mismo criterio que
el ancla de sesión del corte diario (`NY_17:00`).

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

    timezone: str = "America/New_York"
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

    @property
    def label(self) -> str:
        """Cómo se escribe allí donde se lee: el explorador y el CSV."""
        return (
            f"{_text(self.start)} a {_text(self.end)} de {self.timezone}, "
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
