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

Aquí vive también el otro reloj del mercado, el que no es del propietario sino
del oro: **el cierre del viernes**. `MarketWeek` dice cuál es la última vela de
cada semana, que es donde se cierra todo lo que quede vivo.
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


@dataclass(frozen=True, slots=True)
class MarketWeek:
    """El cierre del viernes: cuando el mercado cierra, no queda nada vivo.

    El oro no cotiza el fin de semana. Dejar una posición abierta o un límite
    puesto de viernes a domingo es apostar al hueco de la apertura, que no lo
    decide ninguna regla de esta estrategia: el propietario **cierra todo** al
    cerrar el mercado.

    La hora es la de la plaza, igual que la franja de operativa y que el ancla
    de sesión del corte diario: el viernes a las 17:00 de Nueva York.
    """

    #: Zona IANA con la que se resuelve el horario de verano de verdad.
    timezone: str = "America/New_York"
    #: Hora local del viernes a la que se acaba la semana de mercado.
    close: time = time(17, 0)

    @property
    def label(self) -> str:
        """Cómo se escribe allí donde se lee: informes y explorador."""
        return (
            f"viernes a las {self.close.hour:02d}:{self.close.minute:02d}"
            f" de {self.timezone}"
        )

    def closes(self, timestamps: pd.DatetimeIndex) -> np.ndarray:
        """Máscara con la ÚLTIMA vela de cada semana de mercado.

        No se busca la vela de las 17:00 en punto —el mercado ya está cerrado y
        esa vela no existe— sino **la última que hay antes del corte**: así el
        cierre cae donde el histórico dice que cayó, festivos y huecos incluidos.

        La última vela del histórico entero **no** cuenta como cierre semanal
        aunque caiga en viernes: ahí lo que se ha acabado son los datos, y eso ya
        tiene su propio motivo (`FIN_HISTORICO`, `ABIERTA`).
        """
        index = pd.DatetimeIndex(timestamps)
        if not len(index):
            return np.zeros(0, dtype=bool)
        local = index.tz_convert(self.timezone) - pd.Timedelta(
            hours=self.close.hour, minutes=self.close.minute
        )
        # Desplazado el reloj, el corte cae en la medianoche del viernes. Los
        # días se cuentan desde el 1970-01-02, que fue viernes: al dividir entre
        # siete cada semana de mercado queda en un bloque propio.
        days = local.tz_localize(None).to_numpy().astype("datetime64[D]").astype(np.int64)
        week = (days - 1) // 7
        mask = np.zeros(len(week), dtype=bool)
        mask[:-1] = week[1:] != week[:-1]
        return mask


#: La franja decidida por el propietario: Londres y la media sesión de Nueva York.
TRADING_WINDOW = TradingWindow()

#: El cierre de la semana de mercado: viernes a las 17:00 de Nueva York.
MARKET_WEEK = MarketWeek()


__all__ = ["MARKET_WEEK", "TRADING_WINDOW", "MarketWeek", "TradingWindow"]
