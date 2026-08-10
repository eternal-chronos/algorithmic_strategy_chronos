"""Contrato que toda estrategia debe cumplir.

Es un puerto del dominio: el motor de backtest depende de esta abstracción, no
de ninguna estrategia concreta. Las estrategias viven en `chronos.strategies`
(capa externa) y pueden usar numpy/pandas libremente.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from chronos.domain.bar import MarketData
from chronos.domain.ports.market_view import MarketView
from chronos.domain.signal import StrategyAction
from chronos.domain.trade import Trade


class Strategy(ABC):
    """Estrategia dirigida por barras.

    Ciclo de vida por corrida:
        prepare(data)  -> una vez, para precalcular indicadores vectorizados
        on_bar(ctx)    -> una vez por barra, devuelve intenciones
        on_trade_closed(trade) -> tras cada cierre
        on_finish()    -> al terminar
    """

    name: ClassVar[str] = "unnamed"

    def __init__(self, **params: Any) -> None:
        self.params: Mapping[str, Any] = dict(params)

    # --- Ciclo de vida ------------------------------------------------------

    def prepare(self, data: MarketData) -> None:
        """Precalcula indicadores sobre toda la serie.

        Se ejecuta antes de la simulación. Calcular aquí de forma vectorizada es
        mucho más rápido que barra a barra; el motor garantiza que `on_bar` solo
        ve el índice actual, así que no introduce lookahead mientras los
        indicadores no dependan de datos futuros (nada de `shift(-1)`).
        """

    @abstractmethod
    def on_bar(self, ctx: MarketView) -> Sequence[StrategyAction]:
        """Decide qué hacer en la barra actual. Devolver `()` significa esperar."""

    def on_trade_closed(self, trade: Trade) -> None:
        """Notificación de cierre. Útil para estados tipo martingala o cooldown."""

    def on_finish(self) -> None:
        """Cierre de la corrida."""

    # --- Requisitos ---------------------------------------------------------

    @property
    def warmup_bars(self) -> int:
        """Barras iniciales que el motor debe saltar (indicadores sin valor)."""
        return 0

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "params": dict(self.params)}
