"""La lectura CRT del Diario de una corrida, ya fechada. **Sólo dibujo.**

Capa fina sobre `domain/structure/crt.py`: coge las velas del gráfico del Diario,
le pregunta qué leyó en cada una y devuelve las lecturas con su marca de tiempo
puesta.

**No decide nada.** No entra en la detección, no toca ninguna regla de rotura, no
cambia un solo número de las fases 1 y 2 y no abre nada. Es lo que el propietario
mira en el Diario del setup 2 —dónde hay rango y dónde hay un nivel que espera
ser manipulado y rechazado— en lugar del ID, que ahí ya no se lee.

Se calcula sobre las velas del **gráfico**, que son las que se dibujan: la
lectura y el dibujo tienen que hablar de las mismas velas.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from chronos.application.structure.config import DAILY
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.domain.structure.crt import CrtReading, CrtReadingKind, read_crt
from chronos.domain.structure.zones import CandleSeries


@dataclass(frozen=True, slots=True)
class DatedReading:
    """Una lectura del Diario, situada en el tiempo."""

    reading: CrtReading
    #: Vela que la produce. Antes de su cierre la lectura no existe.
    timestamp: datetime
    #: Vela de la que salen los precios: la manipulada en el rango, la que rompió
    #: en el objetivo.
    ts_source: datetime
    #: Vela en la que dejó de estar vigente, o `None` mientras lo siga.
    ts_end: datetime | None

    @property
    def kind(self) -> CrtReadingKind:
        return self.reading.kind


@dataclass(frozen=True, slots=True)
class CrtRun:
    """Las lecturas del Diario de una corrida, en orden cronológico."""

    enabled: bool
    timeframe: str
    items: tuple[DatedReading, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.items

    def counts(self) -> dict[str, int]:
        """Cuántas de cada tipo. Quien informa las enseña, no las cuenta."""
        tally = Counter(item.kind.value for item in self.items)
        return {kind.value: tally.get(kind.value, 0) for kind in CrtReadingKind}


def read_daily_crt(run: ImpulseRun, timeframe: str = DAILY) -> CrtRun:
    """Lee el CRT sobre las velas de ese gráfico. Sin velas no hay lectura."""
    frame = run.chart_bars.get(timeframe)
    if frame is None or len(frame) < 2:
        return CrtRun(enabled=False, timeframe=timeframe)

    series = CandleSeries.of(frame)
    readings = read_crt(high=series.high, low=series.low, close=series.close)
    return CrtRun(
        enabled=True,
        timeframe=timeframe,
        items=tuple(
            DatedReading(
                reading=reading,
                timestamp=series.at(reading.index),
                ts_source=series.at(reading.index_source),
                ts_end=(
                    None if reading.index_end is None else series.at(reading.index_end)
                ),
            )
            for reading in readings
        ),
    )
