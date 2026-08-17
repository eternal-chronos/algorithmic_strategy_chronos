"""Ejecución realista de las señales sobre M1 (§4).

    Entrada en el **open de la barra M1 siguiente** a la decisión.
    Longs al ask, shorts al bid. El stop de un long lo dispara el bid; el de un
    short, el ask.
    Slippage siempre en contra. Comisión por lote y por lado. Swap por noche
    cruzada, triple el día que corresponda.
    Si el stop y el objetivo se tocan en la misma barra, **gana el stop**.
    Sizing de investigación continuo, sin lote mínimo.

⚠️ **No hay fichero de ask en el proyecto.** Sólo está descargado el M1 del lado
bid. Por eso este módulo no se ejecuta si nadie lo ha autorizado explícitamente:
`EntriesConfig.allow_missing_ask` viene en `False` y el comando se detiene y
avisa. Con la autorización puesta se corre **usando el bid para los dos lados**,
que es lo que se declara en portada, en la salida de la CLI y en el explorador.
No se fabrica una serie de ask inventada en ningún caso.

La horquilla no desaparece por no tener el fichero: se cobra como coste, con el
valor `spread_points` marcado **VERIFICAR**, y se desglosa aparte para que se vea
cuánto del neto se lo lleva ella. Lo que no se puede es saber *cuándo* se abrió,
y eso se dice en vez de disimularlo.

**Por qué el deslizamiento se cobra como coste y no desplazando el precio.** Con
el precio desplazado el bruto ya no vale exactamente -1R o +3,3R y la diferencia
bruto/neto deja de leerse. El §6 pide justo esa diferencia como diagnóstico, así
que el deslizamiento va donde se puede leer: en la columna de costes, siempre en
contra y en los dos lados de la operación.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.config import EntriesConfig
from chronos.domain.entries.enums import EntryTimeframe, GuardRail, StopZone, TradeOutcome
from chronos.domain.entries.signal import (
    TARGET_R,
    DiscardedSignal,
    EntryZone,
    Signal,
    Trade,
)
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec

#: Bloque de barras M1 que se examina de una vez al buscar el desenlace. Con ocho
#: años de M1 recorrer hasta el final por cada operación sería cuadrático; con un
#: bloque fijo el coste es proporcional a lo que la operación duró de verdad.
_SCAN_BLOCK = 20_000

#: Hora del servidor a la que el bróker pasa el swap. Va con la ficha del
#: instrumento y no es un parámetro de la estrategia.
_ROLLOVER = pd.Timedelta(hours=0)


@dataclass(frozen=True, slots=True)
class ExecutionRun:
    """Las operaciones de una corrida, con lo que se cayó por el camino."""

    trades: tuple[Trade, ...]
    discarded: tuple[DiscardedSignal, ...]
    #: Declaración obligatoria de portada: de dónde salió cada lado del precio.
    price_side_note: str
    notes: tuple[str, ...]

    def by_configuration(
        self, entry: EntryTimeframe, stop: StopZone
    ) -> tuple[Trade, ...]:
        return tuple(
            trade
            for trade in self.trades
            if trade.entry_timeframe is entry and trade.stop_zone is stop
        )

    @property
    def resolved(self) -> tuple[Trade, ...]:
        return tuple(trade for trade in self.trades if trade.outcome.is_resolved)


class M1Executor:
    """Simula cada señal barra a barra de M1. Una instancia por corrida."""

    def __init__(
        self,
        m1_bars: pd.DataFrame,
        instrument: InstrumentSpec,
        config: EntriesConfig,
        *,
        has_ask: bool,
        atr_by_timeframe: dict[EntryTimeframe, pd.Series] | None = None,
    ) -> None:
        if m1_bars.empty:
            raise DomainError("No hay velas M1 con las que ejecutar nada")
        if not has_ask and not config.allow_missing_ask:
            raise DomainError(
                "PARADA (§4): no hay fichero de ask en el proyecto. Longs al ask y "
                "shorts al bid no se pueden ejecutar con un solo lado del precio. "
                "Ni se simula un ask inventado ni se usa el bid para los dos lados "
                "en silencio: para correr así hay que autorizarlo explícitamente "
                "con `entries.allow_missing_ask: true`, y entonces la asunción se "
                "declara en portada del informe."
            )
        self._index = pd.DatetimeIndex(m1_bars.index)
        self._open = m1_bars["open"].to_numpy(dtype=float)
        self._high = m1_bars["high"].to_numpy(dtype=float)
        self._low = m1_bars["low"].to_numpy(dtype=float)
        self._instrument = instrument
        self._config = config
        self._has_ask = has_ask
        self._atr = atr_by_timeframe or {}
        self._server_tz = instrument.session.timezone

    @property
    def price_side_note(self) -> str:
        if self._has_ask:
            return (
                "Longs al ask y shorts al bid, con los dos ficheros del histórico. "
                "El stop de un long lo dispara el bid; el de un short, el ask."
            )
        return (
            "⚠️ NO HAY FICHERO DE ASK. Todas las ejecuciones —entradas, stops y "
            "objetivos, largos y cortos— se han hecho sobre el **bid**. El §4 pide "
            "longs al ask y shorts al bid, y con un solo lado del precio eso no se "
            "puede hacer: la corrida está autorizada explícitamente (opción "
            "`--asumir-bid-en-los-dos-lados` o `entries.allow_missing_ask: true`, "
            "que viene apagada) y sin esa autorización el comando se detiene. No se "
            "ha fabricado ninguna serie de ask. La horquilla se cobra igualmente "
            "como coste (`spread_points`, VERIFICAR) pero NO se sabe cuándo se "
            "abrió, así que los desenlaces de las operaciones cuyo stop u objetivo "
            "quedan a menos de una horquilla del precio no son fiables."
        )

    def execute(self, cascade: CascadeRun) -> ExecutionRun:
        trades: list[Trade] = []
        discarded: list[DiscardedSignal] = []
        for signal in cascade.signals:
            for stop_zone, zone in signal.stop_options:
                outcome = self._execute_one(signal, stop_zone, zone)
                if isinstance(outcome, Trade):
                    trades.append(outcome)
                else:
                    discarded.append(outcome)
        trades.sort(key=lambda trade: (trade.ts_entry, trade.entry_timeframe.value))
        return ExecutionRun(
            trades=tuple(trades),
            discarded=tuple(discarded),
            price_side_note=self.price_side_note,
            notes=tuple(self._config.costs.describe()),
        )

    # --- Una señal ----------------------------------------------------------

    def _execute_one(
        self, signal: Signal, stop_zone: StopZone, zone: EntryZone
    ) -> Trade | DiscardedSignal:
        if zone.is_flat:
            return self._reject(signal, GuardRail.ZONA_DE_ENTRADA_PLANA)

        entry_index = int(
            self._index.searchsorted(pd.Timestamp(signal.ts_decision), side="right")
        )
        if entry_index >= len(self._index):
            return self._reject(signal, GuardRail.SIN_M1_PARA_EJECUTAR)

        entry_price = float(self._open[entry_index])
        stop_price = signal.stop_price(zone)
        risk = abs(entry_price - stop_price)
        long = signal.is_long
        # El precio ya estaba al otro lado del stop cuando tocaba ejecutar: la
        # operación nace muerta y no se "arregla" moviendo el stop, que sería
        # inventar una regla que el propietario no ha dado.
        if risk <= 0 or (long and stop_price >= entry_price) or (
            not long and stop_price <= entry_price
        ):
            return self._reject(signal, GuardRail.STOP_INVALIDO)

        target_price = signal.target_price(entry_price, stop_price)
        exit_index, outcome = self._resolve(entry_index, stop_price, target_price, long)
        exit_price = (
            None
            if exit_index is None
            else (stop_price if outcome is not TradeOutcome.OBJETIVO else target_price)
        )
        ts_exit = None if exit_index is None else self._index[exit_index].to_pydatetime()

        lots = self._lots(risk)
        nights = 0 if exit_index is None else self._nights(entry_index, exit_index)
        commission, slippage, swap, spread = self._costs(lots, nights, long)
        gross_r = _gross_r(outcome)
        # Todos los costes escalan con el lotaje y el lotaje escala con el riesgo,
        # así que el coste en R no depende del riesgo por operación. La métrica en
        # R es invariante al sizing, que es justo lo que el §4 busca.
        cost_r = (commission + slippage + spread - swap) / self._config.risk_per_trade_usd

        return Trade(
            signal=signal,
            entry_timeframe=signal.entry_zone.timeframe,
            stop_zone=stop_zone,
            index_entry_m1=entry_index,
            ts_entry=self._index[entry_index].to_pydatetime(),
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            lots=lots,
            risk_usd=risk,
            risk_atr=self._risk_in_atr(signal, risk),
            risk_pct_price=risk / abs(entry_price) if entry_price else float("nan"),
            outcome=outcome,
            ts_exit=ts_exit,
            exit_price=exit_price,
            index_exit_m1=exit_index,
            commission_usd=commission,
            slippage_usd=slippage,
            swap_usd=swap,
            nights=nights,
            gross_r=gross_r,
            net_r=gross_r - cost_r,
        )

    def _resolve(
        self, entry_index: int, stop: float, target: float, long: bool
    ) -> tuple[int | None, TradeOutcome]:
        """Primera barra M1 que toca el stop o el objetivo. Con las dos, el stop.

        La regla intra-barra conservadora del §4 no es una medición: es una
        convención, y se marca aparte (`stop_misma_barra`) para poder contar
        cuántas operaciones dependen de ella. Si fueran muchas, el resultado
        estaría diciendo más sobre la convención que sobre la estrategia.
        """
        total = len(self._index)
        start = entry_index
        while start < total:
            stop_block = min(start + _SCAN_BLOCK, total)
            high = self._high[start:stop_block]
            low = self._low[start:stop_block]
            if long:
                hit_stop = low <= stop
                hit_target = high >= target
            else:
                hit_stop = high >= stop
                hit_target = low <= target
            touched = hit_stop | hit_target
            if touched.any():
                offset = int(np.argmax(touched))
                index = start + offset
                if hit_stop[offset] and hit_target[offset]:
                    return index, TradeOutcome.STOP_MISMA_BARRA
                return index, (
                    TradeOutcome.STOP if hit_stop[offset] else TradeOutcome.OBJETIVO
                )
            start = stop_block
        return None, TradeOutcome.ABIERTA

    def _lots(self, risk: float) -> float:
        """Sizing de investigación **continuo**, sin lote mínimo (§4).

        No se normaliza al lot step ni se recorta al mínimo operable a propósito:
        con lotes reales, una señal con stop grande podría no caber y la población
        de señales dependería del camino del equity. Aquí no depende de nada.
        """
        return self._config.risk_per_trade_usd / (risk * self._instrument.contract_size)

    def _costs(
        self, lots: float, nights: int, long: bool
    ) -> tuple[float, float, float, float]:
        costs = self._config.costs
        per_point = self._instrument.value_per_point_per_lot * lots
        commission = costs.commission_per_lot_per_side * lots * 2
        slippage = costs.slippage_points * per_point * 2
        spread = costs.spread_points * per_point
        swap = 0.0
        if nights:
            base = costs.swap_long_points if long else costs.swap_short_points
            swap = base * per_point * nights
        return commission, slippage, swap, spread

    def _nights(self, entry_index: int, exit_index: int) -> int:
        """Noches cruzadas, con el triple del día que corresponda ya contado.

        Se cuenta en la hora del **servidor del bróker**, no en UTC: el swap lo
        pasa el bróker a su medianoche, y en verano eso no cae a la misma hora de
        UTC que en invierno.
        """
        costs = self._config.costs
        entry = self._index[entry_index].tz_convert(self._server_tz)
        exit_at = self._index[exit_index].tz_convert(self._server_tz)
        first = (entry.normalize() + pd.Timedelta(days=1)) + _ROLLOVER
        nights = 0
        moment = first
        while moment <= exit_at:
            nights += 3 if moment.weekday() == costs.triple_swap_weekday else 1
            moment += pd.Timedelta(days=1)
        return nights

    def _risk_in_atr(self, signal: Signal, risk: float) -> float:
        """1R en ATR de la temporalidad de la zona de entrada (§3).

        El oro pasó de ~1.200 a ~4.300 USD en el histórico: un stop de 12 dólares
        no significa lo mismo en 2018 que en 2025, y sin esta columna la
        distribución del 1R en USD no se puede leer.
        """
        series = self._atr.get(signal.entry_zone.timeframe)
        if series is None or series.empty:
            return float("nan")
        position = int(
            series.index.searchsorted(pd.Timestamp(signal.ts_decision), side="right")
        ) - 1
        if position < 0:
            return float("nan")
        value = float(series.iloc[position])
        return risk / value if np.isfinite(value) and value > 0 else float("nan")

    def _reject(self, signal: Signal, rail: GuardRail) -> DiscardedSignal:
        return DiscardedSignal(
            observation=signal.observation,
            guard_rail=rail,
            index=signal.entry_zone.index_confirmation,
            timestamp=signal.ts_decision,
            confirmation=signal.confirmation,
        )


def _gross_r(outcome: TradeOutcome) -> float:
    """El bruto es exactamente -1R o +3,3R: el desenlace lo fija el §3.

    No se recalcula desde los precios porque el stop y el objetivo son precios
    exactos y tocarlos es el desenlace; recalcular sólo introduciría ruido de
    coma flotante en una cifra que es una constante de la especificación.
    """
    if outcome is TradeOutcome.OBJETIVO:
        return TARGET_R
    if outcome is TradeOutcome.ABIERTA:
        return 0.0
    return -1.0


def atr_series(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR de Wilder desplazado una barra, listo para consultarse por fecha.

    Desplazado: el valor legible en la barra `t` es el que cerró en `t-1`. Sin el
    desplazamiento, normalizar el 1R con el ATR de la propia barra que produce la
    señal metería el rango de esa barra en su propia medida.
    """
    from chronos.domain.strategies.indicators import atr

    values = atr(
        frame["high"].to_numpy(dtype=float),
        frame["low"].to_numpy(dtype=float),
        frame["close"].to_numpy(dtype=float),
        period,
    )
    shifted = np.full(values.size, np.nan, dtype=float)
    shifted[1:] = values[:-1]
    return pd.Series(shifted, index=pd.DatetimeIndex(frame.index))


def trades_table(trades: Sequence[Trade]) -> pd.DataFrame:
    """Una fila por operación con todo lo que el §5 desglosa.

    Es la tabla de la que salen todas las métricas: si un desglose no se puede
    hacer desde aquí, es que falta una columna, no que haga falta otro cálculo.
    """
    rows = []
    for trade in trades:
        observation = trade.signal.observation
        zone = trade.signal.entry_zone
        row: dict[str, object] = {
            "ts_entrada": trade.ts_entry,
            "anio": trade.year,
            "direccion": trade.direction.value,
            "largo": trade.is_long,
            "id_h4": observation.id_num,
            "zona_h4": observation.zone.value,
            "desenlace_zona": trade.outcome_kind.value,
            #: Fase 3.2 — la rama del §6.2 en una sola etiqueta: `UL rechazo`,
            #: `UL rotura_y_retesteo` u `OB rechazo`.
            "rama": trade.branch,
            "direccion_id_h4": observation.direction.value,
            #: ⚠️ Fase 3.2, §6.4 — la operación va EN CONTRA del ID de H4. Es una
            #: población nueva y el informe la aísla.
            "contra_id": trade.against_the_id,
            "forma_rechazo": (
                None
                if observation.rejection_form is None
                else observation.rejection_form.value
            ),
            "formas_rechazo_disponibles": "|".join(
                form.value for form in observation.rejection_forms
            ),
            "ts_rechazo": observation.ts_rejection,
            #: ⚠️ Fase 3.2, §4 — la decisión y la ejecución están separadas por el
            #: cierre del fin de semana. NO se corrige: se marca y se reporta.
            "hueco_finde": trade.weekend_gap,
            "contexto_diario": trade.daily.value,
            "confirmacion": trade.signal.confirmation.kind.value,
            #: Fase 3.1 — **todas** las vías disponibles en la vela que confirmó,
            #: no sólo la que se tomó. Es lo que permite contar coincidencias y
            #: medir si `CONFIRM_PRIORITY` cambia algo sin recorrer H1 otra vez.
            "vias_disponibles": "|".join(
                kind.value for kind in trade.signal.confirmation.available
            ),
            "entrada_en": trade.entry_timeframe.value,
            "stop_en": trade.stop_zone.value,
            "ts_contacto": observation.ts_contact,
            "ts_confirmacion": trade.signal.confirmation.timestamp,
            "ts_decision": trade.signal.ts_decision,
            "ts_salida": trade.ts_exit,
            "zona_entrada_interior": zone.inner,
            "zona_entrada_exterior": zone.outer,
            "precio_entrada": trade.entry_price,
            "precio_stop": trade.stop_price,
            "precio_objetivo": trade.target_price,
            "lotes": trade.lots,
            "r_usd": trade.risk_usd,
            "r_atr": trade.risk_atr,
            "r_pct_precio": trade.risk_pct_price,
            "desenlace": trade.outcome.value,
            "bruto_r": trade.gross_r,
            "neto_r": trade.net_r,
            "coste_r": trade.cost_r,
            "comision_usd": trade.commission_usd,
            "deslizamiento_usd": trade.slippage_usd,
            "swap_usd": trade.swap_usd,
            "noches": trade.nights,
        }
        row.update(trade.signal.rejection_marks)
        rows.append(row)
    return pd.DataFrame(rows)


def discarded_table(discarded: Sequence[DiscardedSignal]) -> pd.DataFrame:
    """Una fila por señal descartada y el guardarraíl que la mató (§6 y §10)."""
    rows = [
        {
            "ts": item.timestamp,
            "anio": item.timestamp.year,
            "guardarrail": item.guard_rail.value,
            "id_h4": item.observation.id_num,
            "zona_h4": item.observation.zone.value,
            "direccion": item.observation.direction_of_trade.value,
            "direccion_id_h4": item.observation.direction.value,
            "contra_id": item.observation.against_the_id,
            "desenlace_zona": item.observation.outcome.value,
            "forma_rechazo": (
                None
                if item.observation.rejection_form is None
                else item.observation.rejection_form.value
            ),
            "contexto_diario": item.observation.daily.value,
            "ts_contacto": item.observation.ts_contact,
            "confirmacion": (
                None if item.confirmation is None else item.confirmation.kind.value
            ),
        }
        for item in discarded
    ]
    return pd.DataFrame(rows)


__all__ = [
    "ExecutionRun",
    "M1Executor",
    "atr_series",
    "discarded_table",
    "trades_table",
]
