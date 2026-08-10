"""Fixtures compartidas."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from chronos.application.backtest.config import (
    AccountConfig,
    BacktestConfig,
    DataConfig,
    ExecutionConfig,
    RiskConfig,
)
from chronos.domain.account import Account
from chronos.domain.enums import Timeframe
from chronos.domain.instrument import CostModel, InstrumentSpec, SessionSpec, SwapModel


@pytest.fixture
def spec() -> InstrumentSpec:
    """XAUUSD con costes sencillos y redondos, para que las cuentas sean verificables."""
    return InstrumentSpec(
        symbol="XAUUSD",
        digits=2,
        tick_size=0.01,
        pip_size=0.01,
        contract_size=100.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        leverage=200.0,
        costs=CostModel(
            spread_mode="fixed",
            spread_points=20.0,  # 0.20 USD
            commission_per_lot_per_side=3.0,
            slippage_points=0.0,
        ),
        swap=SwapModel(long_points=-8.0, short_points=3.0, triple_swap_weekday=2),
        session=SessionSpec(timezone="UTC", break_start=None, break_end=None),
    )


@pytest.fixture
def no_cost_spec(spec: InstrumentSpec) -> InstrumentSpec:
    """Mismo símbolo sin fricción: aísla la lógica del motor de los costes."""
    return InstrumentSpec(
        symbol=spec.symbol,
        digits=spec.digits,
        tick_size=spec.tick_size,
        pip_size=spec.pip_size,
        contract_size=spec.contract_size,
        min_lot=spec.min_lot,
        max_lot=spec.max_lot,
        lot_step=spec.lot_step,
        leverage=spec.leverage,
        costs=CostModel(spread_points=0.0, commission_per_lot_per_side=0.0, slippage_points=0.0),
        swap=SwapModel(long_points=0.0, short_points=0.0),
        session=spec.session,
    )


@pytest.fixture
def account() -> Account:
    return Account(initial_balance=10_000.0, leverage=200.0)


@pytest.fixture
def execution() -> ExecutionConfig:
    return ExecutionConfig(fill_model="next_bar_open", intrabar_priority="worst", price_basis="bid")


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(
        account=AccountConfig(initial_balance=10_000.0),
        execution=ExecutionConfig(),
        risk=RiskConfig(sizing="fixed_lot", fixed_lot=0.10, max_daily_loss=None, max_drawdown_stop=None),
        data=DataConfig(timeframe=Timeframe.M15, strategy_timeframe=Timeframe.M15),
        strategy_name="test",
    )


@pytest.fixture
def moment() -> datetime:
    return datetime(2024, 3, 4, 10, 0, tzinfo=UTC)


def make_frame(rows: list[tuple[float, float, float, float]], start: str = "2024-03-04 00:00") -> pd.DataFrame:
    """DataFrame canónico a partir de tuplas (open, high, low, close)."""
    index = pd.date_range(start=start, periods=len(rows), freq="15min", tz="UTC")
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    frame["volume"] = 100.0
    frame.index.name = "timestamp"
    return frame
