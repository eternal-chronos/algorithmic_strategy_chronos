"""Cuándo se supo cada cosa: cierres de vela y correspondencia entre temporalidades.

La fase 1 fijó que las velas van etiquetadas al **inicio** de su intervalo, así
que la vela de `t` no cierra hasta `t + duración`. Toda la cascada de la fase 3
cruza cuatro temporalidades, y sin esta distinción el cruce mentiría por
sistema: la señal de H4 de las 18:00 no se puede leer en la vela de H1 de las
18:00, porque a esa hora la de H4 acaba de abrir.

La duración se **mide** sobre las propias velas —la moda de las diferencias— en
vez de deducirla del nombre: con el corte anclado a la sesión de Nueva York el
diario no dura siempre lo mismo y el nombre mentiría dos veces al año.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.domain.structure.errors import StructureError


@dataclass(frozen=True, slots=True)
class BarClock:
    """Las marcas de apertura y de cierre de una temporalidad, ya alineadas."""

    timeframe: str
    #: Apertura de cada vela: el índice tal cual.
    opens: pd.DatetimeIndex
    #: Cierre de cada vela. La última se cierra con la duración modal.
    closes: pd.DatetimeIndex

    @classmethod
    def of(cls, frame: pd.DataFrame, timeframe: str) -> BarClock:
        index = pd.DatetimeIndex(frame.index)
        if len(index) == 0:
            raise StructureError(f"No hay velas de {timeframe} con las que fechar nada")
        span = _modal_span(index)
        closes = pd.DatetimeIndex(index[1:].append(pd.DatetimeIndex([index[-1] + span])))
        return cls(timeframe=timeframe, opens=index, closes=closes)

    def __len__(self) -> int:
        return len(self.opens)

    def index_of(self, timestamp: datetime) -> int:
        """Posición de la vela que **abre** exactamente en `timestamp`."""
        position = int(self.opens.searchsorted(pd.Timestamp(timestamp), side="left"))
        if position >= len(self.opens) or self.opens[position] != pd.Timestamp(timestamp):
            raise StructureError(
                f"[{self.timeframe}] No hay vela que abra en {timestamp}"
            )
        return int(position)

    def closed_at(self, timestamp: datetime) -> int:
        """Última vela **ya cerrada** en `timestamp`; `-1` si ninguna lo está.

        El corte es `<=`: una vela que cierra exactamente en ese instante ya es
        información disponible. Es la misma convención con la que el módulo 1
        juzga una rotura en el cierre de la barra que la produce.
        """
        return int(self.closes.searchsorted(pd.Timestamp(timestamp), side="right")) - 1

    def first_open_after(self, timestamp: datetime) -> int:
        """Primera vela que **abre** en `timestamp` o después; `len` si no hay."""
        return int(self.opens.searchsorted(pd.Timestamp(timestamp), side="left"))

    def first_open_strictly_after(self, timestamp: datetime) -> int:
        """Primera vela que abre **estrictamente** después de `timestamp`.

        Es lo que pide el §4 para la ejecución: la barra M1 *siguiente* a la
        decisión, nunca la que ya estaba abierta cuando se decidió.
        """
        return int(self.opens.searchsorted(pd.Timestamp(timestamp), side="right"))

    def close_of(self, index: int) -> pd.Timestamp:
        if not 0 <= index < len(self.closes):
            raise StructureError(
                f"[{self.timeframe}] Índice {index} fuera de la serie ({len(self)} velas)"
            )
        return pd.Timestamp(self.closes[index])

    def open_of(self, index: int) -> pd.Timestamp:
        if not 0 <= index < len(self.opens):
            raise StructureError(
                f"[{self.timeframe}] Índice {index} fuera de la serie ({len(self)} velas)"
            )
        return pd.Timestamp(self.opens[index])


def _modal_span(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 2:
        return pd.Timedelta(hours=4)
    deltas = index.to_series().diff().dropna()
    return pd.Timedelta(deltas.mode().iloc[0]) if not deltas.empty else pd.Timedelta(hours=4)


def session_ids(index: pd.DatetimeIndex, daily_opens: pd.DatetimeIndex) -> np.ndarray:
    """A qué sesión pertenece cada vela, según el corte diario del proyecto.

    `R2` promedia sobre "sesiones anteriores", y la sesión de este proyecto no es
    el día natural de UTC: es la que arranca en `D_SESSION_START` (18:00 de Nueva
    York, con DST real). Usar el día de UTC metería en la misma sesión las velas
    de dos jornadas distintas del propietario, y la distribución de `R2` dejaría
    de ser la de "esa temporalidad" para ser la de otra cosa.

    Las velas anteriores al primer corte quedan en la sesión `-1`, que existe y
    cuenta como sesión anterior de la primera de verdad.
    """
    return daily_opens.searchsorted(index, side="right").astype(np.int64) - 1


__all__ = ["BarClock", "session_ids"]
