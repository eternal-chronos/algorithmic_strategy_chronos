"""Apoyo para los tests del impulso dominante.

Las series del módulo 1 se escriben como pares (open, close) porque sólo mira el
cuerpo. Las mechas no intervienen y añadirlas al fixture sólo escondería el
punto que cada test quiere fijar.

Las de la **fase 2.0** son al revés: el UL vive en la mecha y el PUL en el cuerpo
de una vela concreta, así que sus series traen las cuatro cifras y su propio
apoyo, al final del fichero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import pytest

from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import (
    AnchorMode,
    DojiBreakMode,
    LegStartMode,
    OverlapPriority,
    SeedMode,
)
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.synthetic_day import (
    SYNTHETIC_DAY,
    SYNTHETIC_DAY_GAP_AFTER,
    SYNTHETIC_DAY_START,
)
from chronos.domain.structure.synthetic_zones import Candle
from chronos.domain.structure.zone_break import ZoneBreakLevels
from chronos.domain.structure.zones import (
    CandleSeries,
    Zone,
    ZoneKind,
    against_zone,
    last_zone,
)

H4 = timedelta(hours=4)
START = SYNTHETIC_DAY_START  # viernes

__all__ = [
    "H4",
    "START",
    "SYNTHETIC_DAY",
    "SYNTHETIC_DAY_GAP_AFTER",
    "ZonedImpulse",
    "make_bars",
    "make_series",
    "run_break",
    "run_detector",
    "run_zones",
]


def make_bars(
    bodies: Sequence[tuple[float, float]],
    *,
    start: datetime = START,
    step: timedelta = H4,
    gap_after: int | None = None,
    #: Salto que lleva de la vela del sábado 00:00 a la reapertura del domingo 20:00.
    gap: timedelta = timedelta(days=1, hours=20),
) -> list[BodyBar]:
    """Serie de cuerpos consecutivos, con un hueco opcional tras `gap_after`."""
    bars: list[BodyBar] = []
    stamp = start
    for position, (open_, close) in enumerate(bodies):
        if gap_after is not None and position == gap_after + 1:
            stamp += gap
        bars.append(BodyBar(timestamp=stamp, open=open_, close=close))
        stamp += step
    return bars


def run_detector(
    bodies: Sequence[tuple[float, float]],
    *,
    anchor_mode: AnchorMode = AnchorMode.A2_FIRST_LEG_BAR,
    seed_mode: SeedMode = SeedMode.S1_FIRST_NON_DOJI,
    doji_break_mode: DojiBreakMode = DojiBreakMode.D1_NEUTRAL,
    leg_start_mode: LegStartMode = LegStartMode.L1_CURRENT,
    warmup_bars: int = 0,
    **kwargs: object,
) -> DominantImpulseDetector:
    detector = DominantImpulseDetector(
        timeframe="H4",
        anchor_mode=anchor_mode,
        seed_mode=seed_mode,
        doji_break_mode=doji_break_mode,
        leg_start_mode=leg_start_mode,
        warmup_bars=warmup_bars,
    )
    detector.process_all(make_bars(bodies, **kwargs))  # type: ignore[arg-type]
    return detector


@pytest.fixture
def synthetic_day() -> DominantImpulseDetector:
    return run_detector(SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER)


# --- Fase 2.0: series con mecha y sus zonas ---------------------------------


def make_series(candles: Sequence[Candle], *, start: datetime = START) -> CandleSeries:
    """Serie OHLC consecutiva de paso H4, sin huecos."""
    stamps = pd.date_range(start, periods=len(candles), freq=H4, tz="UTC")
    return CandleSeries(
        timestamps=pd.DatetimeIndex(stamps),
        open=pd.Series([candle[0] for candle in candles], dtype=float).to_numpy(),
        high=pd.Series([candle[1] for candle in candles], dtype=float).to_numpy(),
        low=pd.Series([candle[2] for candle in candles], dtype=float).to_numpy(),
        close=pd.Series([candle[3] for candle in candles], dtype=float).to_numpy(),
    )


@dataclass(frozen=True, slots=True)
class ZonedImpulse:
    """Un impulso con sus dos zonas, que es lo que se audita en la fase 2.0.

    En el lado en contra hay una y sólo una: el PUL, o el APUL cuando el ID nació
    tras una constitución abortada. `against` devuelve la que haya.
    """

    impulse: DominantImpulse
    last: Zone
    penultimate: Zone | None
    ante_penultimate: Zone | None = None

    @property
    def against(self) -> Zone | None:
        return self.penultimate if self.penultimate is not None else self.ante_penultimate

    def zones_tuple(self) -> tuple[Zone, ...]:
        against = self.against
        return (self.last,) if against is None else (self.last, against)


def run_zones(
    candles: Sequence[Candle],
    *,
    anchor_mode: AnchorMode = AnchorMode.A1_LAST_COUNTER_BODY,
    seed_mode: SeedMode = SeedMode.S2_FIRST_COUNTER_BAR,
    warmup_bars: int = 0,
    start: datetime = START,
) -> tuple[CandleSeries, list[ZonedImpulse]]:
    """Detecta los impulsos de una serie OHLC y calcula sus zonas.

    El ancla por defecto es la A1 del proyecto y la semilla la S2, para que el
    primer impulso tenga ancla y sea publicable: con S1 la pierna arranca en la
    primera vela y no hay contraria anterior de la que sacarla.
    """
    series = make_series(candles, start=start)
    detector = DominantImpulseDetector(
        timeframe="H4", anchor_mode=anchor_mode, seed_mode=seed_mode, warmup_bars=warmup_bars
    )
    detector.process_all(
        [
            BodyBar(
                timestamp=series.at(position),
                open=float(series.open[position]),
                close=float(series.close[position]),
            )
            for position in range(len(series))
        ]
    )

    zoned = [
        ZonedImpulse(
            impulse=impulse,
            last=last_zone(
                series,
                id_num=impulse.id_num,
                timeframe="H4",
                direction=impulse.direction,
                index_extreme=impulse.index_extreme,
                ts_constitution=impulse.ts_constitution,
            ),
            penultimate=_against(series, impulse, ZoneKind.PENULTIMATE),
            ante_penultimate=_against(series, impulse, ZoneKind.ANTE_PENULTIMATE),
        )
        for impulse in detector.impulses
    ]
    return series, zoned


def _against(
    series: CandleSeries, impulse: DominantImpulse, kind: ZoneKind
) -> Zone | None:
    """La zona en contra del impulso si es de ese tipo, como en `application/`."""
    lleva = (
        impulse.has_penultimate
        if kind is ZoneKind.PENULTIMATE
        else impulse.has_ante_penultimate
    )
    if not lleva:
        return None
    return against_zone(
        series,
        kind=kind,
        id_num=impulse.id_num,
        timeframe="H4",
        direction=impulse.direction,
        index_body=impulse.index_against,
        tip_window=impulse.against_tip_window,
        zone_direction=impulse.against_direction,
        ts_constitution=impulse.ts_constitution,
    )


# --- Fase 2.1: la rotura por zona --------------------------------------------


def run_break(
    candles: Sequence[Candle],
    *,
    break_by_zone: bool = True,
    break_against_by_zone: bool = True,
    overlap_priority: OverlapPriority = OverlapPriority.A_FAVOR_FIRST,
    anchor_mode: AnchorMode = AnchorMode.A1_LAST_COUNTER_BODY,
    seed_mode: SeedMode = SeedMode.S2_FIRST_COUNTER_BAR,
    doji_break_mode: DojiBreakMode = DojiBreakMode.D1_NEUTRAL,
    leg_start_mode: LegStartMode = LegStartMode.L1_CURRENT,
    warmup_bars: int = 0,
    start: datetime = START,
) -> tuple[CandleSeries, DominantImpulseDetector]:
    """Corre el detector sobre una serie OHLC con la regla de rotura elegida.

    Devuelve también la serie porque los tests de la fase 2.1 comprueban los
    bordes de las zonas contra las mechas, y volver a construirla aparte abriría
    la puerta a que las dos versiones se separasen.
    """
    series = make_series(candles, start=start)
    detector = DominantImpulseDetector(
        timeframe="H4",
        anchor_mode=anchor_mode,
        seed_mode=seed_mode,
        doji_break_mode=doji_break_mode,
        leg_start_mode=leg_start_mode,
        warmup_bars=warmup_bars,
        break_by_zone=break_by_zone,
        break_against_by_zone=break_against_by_zone,
        overlap_priority=overlap_priority,
        zone_levels=(
            ZoneBreakLevels(series, timeframe="H4") if break_by_zone else None
        ),
    )
    detector.process_all(
        [
            BodyBar(
                timestamp=series.at(position),
                open=float(series.open[position]),
                close=float(series.close[position]),
            )
            for position in range(len(series))
        ]
    )
    return series, detector
