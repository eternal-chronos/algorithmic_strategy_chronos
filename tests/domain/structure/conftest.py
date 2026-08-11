"""Apoyo para los tests del impulso dominante.

Las series se escriben como pares (open, close) porque el módulo 1 sólo mira el
cuerpo. Las mechas no intervienen y añadirlas al fixture sólo escondería el
punto que cada test quiere fijar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import AnchorMode, DojiBreakMode, LegStartMode, SeedMode
from chronos.domain.structure.synthetic_day import (
    SYNTHETIC_DAY,
    SYNTHETIC_DAY_GAP_AFTER,
    SYNTHETIC_DAY_START,
)

H4 = timedelta(hours=4)
START = SYNTHETIC_DAY_START  # viernes

__all__ = ["H4", "START", "SYNTHETIC_DAY", "SYNTHETIC_DAY_GAP_AFTER", "make_bars", "run_detector"]


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
