"""Caso de uso: ejecutar una estrategia sobre un histórico y evaluarla.

Recibe puertos ya construidos (repositorio y estrategia). Quién los construye —
YAML, CLI, notebook — es problema de la capa externa.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos.application.backtest.config import BacktestConfig
from chronos.application.backtest.engine import BacktestEngine
from chronos.application.backtest.result import BacktestResult
from chronos.application.metrics.performance import PerformanceReport, compute_performance
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.ports.market_data_repository import MarketDataRepository
from chronos.domain.strategy import Strategy


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """Corrida completa: qué pasó y cómo de bien lo hizo."""

    result: BacktestResult
    performance: PerformanceReport
    config: BacktestConfig
    spec: InstrumentSpec


class RunBacktest:
    """Orquesta carga de datos, simulación y evaluación."""

    def __init__(self, spec: InstrumentSpec, config: BacktestConfig) -> None:
        self._spec = spec
        self._config = config
        self._engine = BacktestEngine(spec, config)

    def execute(self, repository: MarketDataRepository, strategy: Strategy) -> BacktestRun:
        data_config = self._config.data
        data = repository.load(
            symbol=self._spec.symbol,
            timeframe=data_config.strategy_timeframe,
            start=data_config.start,
            end=data_config.end,
        )
        if len(data) <= strategy.warmup_bars:
            raise DomainError(
                f"Solo hay {len(data)} barras y la estrategia necesita "
                f"{strategy.warmup_bars} de calentamiento"
            )

        result = self._engine.run(data, strategy)
        return BacktestRun(
            result=result,
            performance=compute_performance(result),
            config=self._config,
            spec=self._spec,
        )
