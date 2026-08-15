"""Montaje del día sintético del §8 sobre el motor **de verdad**.

Las velas de H4 se escriben a mano y todo lo demás se deriva de ellas: se parten
en M1, en M15 y en H1, y se agrupan en el diario. Sobre esas velas corren el
detector de la fase 2.1 y el módulo de zonas de la fase 2.0 sin ninguna
adaptación: la cascada que se prueba aquí es exactamente la que corre sobre
2018-2025.

**Las velas se derivan y no se reagregan**, y no es un atajo: `explode` parte una
vela en trozos que vuelven a componerla exactamente —mismo `open` primero, mismo
`close` último, mismos máximo y mínimo—, así que las cuatro temporalidades del
sintético son consistentes por construcción. Hay un test que lo fija. Meter aquí
el agregador del proyecto habría hecho que `application` dependiera de
`infrastructure`, que es la única dirección que este proyecto no permite.

La rejilla del sintético es la neutra —H4 desde medianoche y día natural de UTC—
para que las velas coincidan con las escritas a mano. Es un cambio de rejilla, no
de reglas: ni una regla de la cascada depende de dónde se corte el día.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

import pandas as pd

from chronos.application.entries.cascade import CascadeRun, build_cascade
from chronos.application.entries.config import EntriesConfig
from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.entries.synthetic_entries import SYNTHETIC_ENTRY_START, explode_all
from chronos.domain.structure.synthetic_zones import Candle

#: En cuántos trozos se parte una vela de H4 para cada temporalidad menor.
M1_PER_H4 = 240
M15_PER_H4 = 16
H1_PER_H4 = 4
#: Y cuántas velas de H4 caben en un día natural de UTC.
H4_PER_DAY = 6

#: Rejilla neutra del sintético: sin desplazamiento en H4 y día natural de UTC.
SYNTHETIC_AGGREGATION = AggregationConfig(h4_offset_hours=0, d_session_start="00:00")

#: Reparto mínimo: las tres temporalidades con detector, más M15 como gráfico.
SYNTHETIC_CHARTS = ChartsConfig(
    layout={DAILY: (DAILY,), H4: (H4, DAILY), H1: (H1, H4), M15: (H1,)}
)


@dataclass(frozen=True, slots=True)
class SyntheticCascade:
    """El sintético montado: sus velas, su estructura y su cascada."""

    bars: dict[str, pd.DataFrame]
    m1: pd.DataFrame
    run: ImpulseRun
    zones: ZonesRun
    cascade: CascadeRun

    def watch(self, id_num: int, zone: str) -> object | None:
        """La observación de una zona concreta, para poder afirmar sobre ella."""
        for observation in self.cascade.observations:
            if observation.id_num == id_num and observation.zone.value == zone:
                return observation
        return None

    def rails(self, id_num: int, zone: str) -> tuple[str, ...]:
        """Los guardarraíles en que murieron las señales de esa zona."""
        return tuple(
            item.guard_rail.value
            for item in self.cascade.discarded
            if item.observation.id_num == id_num and item.observation.zone.value == zone
        )


def build_synthetic(
    candles: tuple[Candle, ...],
    config: EntriesConfig | None = None,
    *,
    start: datetime = SYNTHETIC_ENTRY_START,
) -> SyntheticCascade:
    """Monta la cascada entera desde unas velas de H4 escritas a mano."""
    m1 = frame_of(explode_all(candles, M1_PER_H4), start, "1min")
    bars = {
        DAILY: frame_of(_group(candles, H4_PER_DAY), start, "1D"),
        H4: frame_of(candles, start, "4h"),
        H1: frame_of(explode_all(candles, H1_PER_H4), start, "1h"),
        M15: frame_of(explode_all(candles, M15_PER_H4), start, "15min"),
    }
    structure = ImpulseConfig(
        symbol="SYNTH",
        aggregation=SYNTHETIC_AGGREGATION,
        charts=SYNTHETIC_CHARTS,
        rules=ImpulseRulesConfig(break_by_zone=True, warmup_bars=0),
        zones=ZonesConfig(enabled=True),
    )
    run = DetectDominantImpulses(structure).execute(
        {timeframe: bars[timeframe] for timeframe in structure.charts.detected}
    )
    zones = detect_zones(run)
    entries = config or EntriesConfig(enabled=True, allow_missing_ask=True)
    entries = replace(entries, enabled=True)
    return SyntheticCascade(
        bars=bars,
        m1=m1,
        run=run,
        zones=zones,
        cascade=build_cascade(run, zones, bars[M15], entries),
    )


def _group(candles: tuple[Candle, ...], size: int) -> tuple[Candle, ...]:
    """Agrupa velas contiguas en una mayor: primer open, último close, extremos.

    El último grupo puede quedar incompleto —el sintético son diez velas de H4 y
    el día natural lleva seis— y se conserva igualmente. El agregador del proyecto
    descartaría esa vela por no estar cerrada; aquí se conserva porque lo único
    que hace con ella la cascada es delimitar sesiones para el percentil de `R2`,
    y dejar el tramo final sin sesión sería peor que darle una corta.
    """
    grouped: list[Candle] = []
    for first in range(0, len(candles), size):
        chunk = candles[first : first + size]
        grouped.append(
            (
                chunk[0][0],
                max(candle[1] for candle in chunk),
                min(candle[2] for candle in chunk),
                chunk[-1][3],
            )
        )
    return tuple(grouped)


def frame_of(candles: tuple[Candle, ...], start: datetime, freq: str) -> pd.DataFrame:
    """Un frame OHLC directo desde velas escritas a mano, sin agregar nada.

    Sirve para los casos que no necesitan la cascada entera —el OB suelto de M15
    y el desenlace sobre M1—, donde meter el agregador de por medio sólo añadiría
    ruido entre la vela escrita y la regla que se está comprobando.
    """
    index = pd.date_range(start, periods=len(candles), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": [candle[0] for candle in candles],
            "high": [candle[1] for candle in candles],
            "low": [candle[2] for candle in candles],
            "close": [candle[3] for candle in candles],
            "volume": 0.0,
        },
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


__all__ = [
    "M1_PER_H4",
    "SYNTHETIC_AGGREGATION",
    "SYNTHETIC_CHARTS",
    "SyntheticCascade",
    "build_synthetic",
    "frame_of",
]
