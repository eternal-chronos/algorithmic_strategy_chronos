"""La franja en la que se opera: de 03:00 a 12:00 hora de Nueva York.

Es Londres y la primera mitad de Nueva York, que es donde el propietario opera.
**Fuera de esa franja no se hace nada**: no se abre búsqueda al tocar el PUL de
H4, no se marca el PUL de un ID de H1 y no salta ninguna señal. No es que valga
menos: es que no se mira, igual que el veto del Diario.

La hora es la de la plaza, no un desfase fijo en UTC: Nueva York abre a la misma
hora local todo el año y en UTC la franja se mueve una hora dos veces al año. Por
eso la ventana se resuelve convirtiendo a `America/New_York`, con el mismo
criterio que el ancla de sesión del corte diario (`NY_17:00`).

La franja es **semiabierta**: a las 03:00 en punto ya se opera, a las 12:00 en
punto ya no.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class TradingWindow:
    """Horas del día en las que la máquina puede mirar el mercado.

    No cruza la medianoche —`start < end`— y no hace falta que lo haga: la franja
    del propietario cabe entera dentro de un día de Nueva York.
    """

    #: Zona IANA con la que se resuelve el horario de verano de verdad.
    timezone: str = "America/New_York"
    start: time = time(3, 0)
    end: time = time(12, 0)

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise DomainError(
                f"La franja de operativa debe empezar antes de acabar: "
                f"{self.start} >= {self.end}"
            )

    @property
    def label(self) -> str:
        """Cómo se escribe la franja allí donde se lee: informes y explorador."""
        return (
            f"{self.start.hour:02d}:{self.start.minute:02d}"
            f" a {self.end.hour:02d}:{self.end.minute:02d} de {self.timezone}"
        )

    def contains(self, moment: datetime) -> bool:
        """Si ese instante cae dentro de la franja. Exige timestamp aware."""
        local = pd.Timestamp(moment).tz_convert(self.timezone)
        return self.start <= local.time() < self.end

    def mask(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        """La misma pregunta para una serie entera, de una vez.

        Se usa sobre las velas de H1, donde la cascada busca el ID que confirma:
        preguntar vela a vela con `contains` sería un bucle Python en el camino
        caliente.
        """
        local = pd.DatetimeIndex(timestamps).tz_convert(self.timezone)
        minutes = local.hour * 60 + local.minute
        first = self.start.hour * 60 + self.start.minute
        last = self.end.hour * 60 + self.end.minute
        return np.asarray((minutes >= first) & (minutes < last))


#: La franja decidida por el propietario: Londres y la media sesión de Nueva York.
TRADING_WINDOW = TradingWindow()


__all__ = ["TRADING_WINDOW", "TradingWindow"]
