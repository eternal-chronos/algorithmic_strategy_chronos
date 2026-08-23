"""Señales de zona de una corrida: toques del OB y rechazos/roturas del UL.

Capa fina sobre `domain/structure/zone_signals.py`: recorre los impulsos que la
fase 2.0 ya zonificó, le da a cada zona el tramo de barras en el que estuvo viva
y devuelve las señales con su marca de tiempo puesta.

**No decide nada.** No entra en la detección, no toca la regla de rotura, no
genera entradas ni cambia un solo número de las fases 1, 2.0 y 2.1: es material
de dibujo para la auditoría visual del explorador. Encenderla o apagarla no puede
mover un impulso porque no hay nada que mover — se calcula *después*, sobre el
resultado ya cerrado.

Sólo existen donde existen las zonas: Diario y H4, las dos temporalidades con
detector. En H1 y M15 no hay ID propio y por tanto tampoco señales propias.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import (
    ZoneSignal,
    ZoneSignalKind,
    classify_zone_signals,
)
from chronos.domain.structure.zones import Zone, ZoneKind


@dataclass(frozen=True, slots=True)
class ImpulseSignal:
    """Una señal, ya situada en el tiempo y atribuida a su ID."""

    timeframe: str
    id_num: int
    direction: ImpulseDirection
    timestamp: datetime
    signal: ZoneSignal

    @property
    def kind(self) -> ZoneSignalKind:
        return self.signal.kind

    @property
    def zone(self) -> ZoneKind:
        return self.signal.zone


@dataclass(frozen=True, slots=True)
class TimeframeSignals:
    """Señales de una temporalidad, en orden cronológico."""

    timeframe: str
    items: tuple[ImpulseSignal, ...]

    def counts(self) -> dict[str, int]:
        """Cuántas de cada tipo. El informe y las notas las enseñan, no las cuentan."""
        tally = Counter(item.kind.value for item in self.items)
        return {kind.value: tally.get(kind.value, 0) for kind in ZoneSignalKind}


@dataclass(frozen=True, slots=True)
class ZoneSignalsRun:
    """Señales de todas las temporalidades zonificadas de una corrida."""

    enabled: bool
    per_timeframe: dict[str, TimeframeSignals]

    @property
    def empty(self) -> bool:
        return not any(measurement.items for measurement in self.per_timeframe.values())


def detect_zone_signals(run: ImpulseRun, zones: ZonesRun | None) -> ZoneSignalsRun:
    """Señales de la corrida. Sin zonas no hay señales: no se emite nada."""
    if zones is None or not zones.enabled:
        return ZoneSignalsRun(enabled=False, per_timeframe={})

    return ZoneSignalsRun(
        enabled=True,
        per_timeframe={
            timeframe: _signals_of(run.analyses[timeframe], measurement.items)
            for timeframe, measurement in zones.per_timeframe.items()
            if timeframe in run.analyses
        },
    )


def _signals_of(
    analysis: TimeframeAnalysis, items: tuple[ImpulseZones, ...]
) -> TimeframeSignals:
    bars = analysis.bars
    index = pd.DatetimeIndex(bars.index)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    last_bar = len(index) - 1

    found: list[ImpulseSignal] = []
    for zoned in items:
        # Un ID vivo sigue produciendo señales hasta la última vela del histórico.
        end = zoned.index_end if zoned.index_end is not None else last_bar
        for zone in zoned.zones():
            for signal in classify_zone_signals(
                high=high,
                low=low,
                close=close,
                zone=zone,
                first=_first_bar(zoned, zone),
                last=end,
            ):
                found.append(
                    ImpulseSignal(
                        timeframe=analysis.timeframe,
                        id_num=zoned.id_num,
                        direction=zoned.direction,
                        timestamp=index[signal.index].to_pydatetime(),
                        signal=signal,
                    )
                )

    found.sort(key=lambda item: (item.signal.index, item.kind.value, item.id_num))
    return TimeframeSignals(timeframe=analysis.timeframe, items=tuple(found))


def _first_bar(zoned: ImpulseZones, zone: Zone) -> int:
    """Primera barra que puede llevar señal: la siguiente al nacimiento de la zona.

    El UL nace al constituirse el ID; el OB, cuando además se ha confirmado, que
    puede ser antes (dentro de la pierna) o después. Se toma el índice que la
    fase 2.0 ya registró en vez de volver a buscar la vela por su marca de
    tiempo: buscarla sería tener una segunda opinión sobre cuándo nació.
    """
    birth = zoned.index_constitution
    if zone.kind is ZoneKind.ORDER_BLOCK and zone.index_confirmation is not None:
        birth = max(birth, zone.index_confirmation)
    return birth + 1
