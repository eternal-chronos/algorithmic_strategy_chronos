"""LAS ENTRADAS, tal como las dictó el propietario el 2026-09-13. Primera forma.

No es la forma definitiva —se irá ajustando mirando el dibujo— pero es la idea
plasmada entera: el contexto del Diario y de H4, las dos maneras de entrar en H1
con M15 detrás, la franja de Nueva York y una operación al día a 1:4. **No hay
medición de rentabilidad** y es a propósito: lo que se entrega es el dibujo.

## 1. El contexto: el Diario manda sobre H4 y H4 sobre H1

Cada ID de temporalidad superior está en uno de estos estados (`ContextState`):

- **LIBRE** — recién constituido, todavía no ha tocado su zona en contra: abajo
  se acepta el ID que haya, alcista o bajista;
- **A_FAVOR** — el precio tocó su PUL o su APUL: abajo sólo se busca A FAVOR de
  este ID. Con el Diario alcista en su PUL, en H4 *sí o sí* se quiere ver un ID
  alcista, y mientras no lo haya no se busca nada; con H4 alcista en su PUL, en
  H1 sólo se buscan entradas alcistas;
- **EN_CONTRA** — sólo H4: una vela de H4 llegó a su UL y lo rechazó. En H1 se
  buscan entradas EN CONTRA de este ID;
- **AGOTADO** — sólo H4: ya se hizo esa entrada en contra. Desde ahí se acepta
  cualquier dirección en H1, y ni un nuevo rechazo del UL ni un nuevo toque del
  PUL cambian nada, hasta que el ID muera.

Salir de A_FAVOR: el rechazo del UL (H4 pasa a EN_CONTRA, el Diario vuelve a
LIBRE), la rotura del UL o la rotura del ID —en las dos el ID muere y el que
nace arranca LIBRE—. Tocar es la señal `TOQUE_PUL` de las zonas, fechada en la
vela fina; el rechazo es `RECHAZO_UL`, que espera al cierre de la vela del ID.

La dirección permitida en H1 es la intersección: lo que deja el Diario y lo que
deja H4. Sin ID en el Diario o en H4 —limbo— ese escalón no restringe nada.

SUPUESTOS declarados: (a) el filtro se aplica a la DIRECCIÓN DE LA ENTRADA, no a
la del ID de H1 —"buscar entradas alcistas"—; (b) con el Diario A_FAVOR sólo se
entra en su dirección, aunque H4 esté LIBRE; (c) el rechazo del UL del Diario no
abre EN_CONTRA, el propietario sólo lo dictó para H4; (d) un toque del PUL
estando EN_CONTRA vuelve a A_FAVOR.

## 2. Las dos entradas, en H1 y con M15 detrás

Las dos son un LÍMITE que se pone en una zona de H1 y se llena cuando el precio
llega. Para las dos, M15 tiene que llevar un ID EN LA DIRECCIÓN DE LA ENTRADA en
el momento de ponerlo y mientras esté puesto.

- **ROTURA_PUL** — el ID de H1 cierra más allá del borde exterior de SU PUL
  —sólo el PUL: no vale el APUL ni el UL— sin romper el ID. Se entra EN CONTRA
  del ID: el límite va en el borde exterior del PUL, que es lo primero que el
  precio encuentra al volver. Se arma en cuanto se sabe la rotura (el cierre de
  esa vela de H1) o cuando M15 se ponga a favor, mientras el precio no haya
  vuelto al PUL; una sola vez por ID de H1.
- **TOQUE_ZONA** — el ID de H1 tiene zona en contra —PUL o APUL— y el precio
  está fuera de ella. Se entra A FAVOR del ID: el límite va en el borde interior
  de la zona, el primero que el precio encuentra al volver.

El stop va al borde exterior de la zona de H1, o al de la zona en contra del ID
de M15 **si queda más cerca de la entrada**; si el de M15 queda más lejos, se
deja el de H1 (imagen 4 del propietario). El objetivo es siempre 1:4.

## 3. El reloj: Nueva York

Sólo se buscan y se llenan entradas de las 02:00 a las 11:59 de Nueva York; a
las 12:00 se quita el límite que siga puesto. Una posición viva se cierra a las
16:00 al precio de esa vela. **Una operación por día**, contando el día en que
entra. Si una vela llena el límite y alcanza el stop, manda el stop.

## 4. Qué se dibuja

Todo: cada límite puesto y quitado, cada operación con su riesgo y su objetivo,
cómo acabó, y de fondo QUÉ SE BUSCABA en cada momento y por qué. Sin un solo
porcentaje.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.entries.trading_day import TradingDay
from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M15,
    TIMEFRAME_MINUTES,
    EntriesConfig,
)
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zone_signals import ZoneSignalsRun, detect_zone_signals
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.entries.rules import (
    ContextState,
    EntryKind,
    OrderEnd,
    TradeOutcome,
    beyond_price,
    fills,
    first_close_beyond,
    nearest_stop,
    resolve,
    target_for,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind

#: De dónde sale el stop: la zona de H1 o la zona en contra del ID de M15.
STOP_FROM_H1 = "H1"
STOP_FROM_M15 = "M15"


@dataclass(frozen=True, slots=True)
class Entry:
    """Un límite y, si llegó a llenarse, la operación que salió de él."""

    seq: int
    kind: EntryKind
    direction: ImpulseDirection
    h1_id: int
    h1_direction: ImpulseDirection
    m15_id: int
    #: La zona de H1 en la que se puso el límite: el PUL o el APUL.
    zone_kind: ZoneKind
    zone_low: float
    zone_high: float
    #: La zona en contra del ID de M15 con la que se comparó el stop, si tenía.
    m15_zone_low: float | None
    m15_zone_high: float | None
    stop_source: str
    #: Lo que decían el Diario y H4 al armar, y con qué ID.
    daily_state: ContextState | None
    h4_state: ContextState | None
    context: str
    #: Vela fina en cuyo cierre se puso el límite.
    ts_armed: datetime
    entry: float
    stop: float
    target: float
    ts_filled: datetime | None = None
    ts_closed: datetime | None = None
    exit_price: float | None = None
    outcome: TradeOutcome | None = None
    cancelled_by: OrderEnd | None = None
    ts_cancelled: datetime | None = None

    @property
    def risk(self) -> float:
        """La distancia del stop, en dólares. Es 1R."""
        return abs(self.entry - self.stop)

    @property
    def filled(self) -> bool:
        return self.ts_filled is not None

    @property
    def r_multiple(self) -> float | None:
        """Lo que se llevó, en R, sacado de los PRECIOS. `None` si no cerró."""
        if self.exit_price is None or not self.risk:
            return None
        gain = (
            self.entry - self.exit_price
            if self.direction is ImpulseDirection.BAJISTA
            else self.exit_price - self.entry
        )
        return gain / self.risk


@dataclass(frozen=True, slots=True)
class Seeking:
    """Un tramo con lo que se buscaba en H1 y por qué. Es el fondo del dibujo."""

    seq: int
    #: Direcciones permitidas: las dos, una o ninguna.
    allowed: frozenset[ImpulseDirection]
    reason: str
    daily_id: int | None
    daily_state: ContextState | None
    h4_id: int | None
    h4_state: ContextState | None
    start: datetime
    #: `None` = seguía abierto al acabarse el histórico.
    end: datetime | None


@dataclass(frozen=True, slots=True)
class EntriesRun:
    """Todas las operaciones de una corrida, en orden cronológico."""

    enabled: bool
    entries: tuple[Entry, ...] = ()
    seeking: tuple[Seeking, ...] = ()
    #: La temporalidad fina en la que se llena y se resuelve todo.
    source: str = ""
    day: TradingDay = field(default_factory=TradingDay)
    risk_reward: float = 4.0

    @property
    def empty(self) -> bool:
        return not self.entries and not self.seeking

    @property
    def filled(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.filled)

    def counts(self) -> dict[str, int]:
        """Cuántos límites, cuántos entraron y cómo acabaron. Sin porcentajes."""
        outcomes = Counter(
            entry.outcome.value for entry in self.entries if entry.outcome is not None
        )
        cancels = Counter(
            entry.cancelled_by.value for entry in self.entries if entry.cancelled_by is not None
        )
        tally = {"LIMITES": len(self.entries), "ENTRADAS": len(self.filled)}
        for outcome in TradeOutcome:
            tally[outcome.value] = outcomes.get(outcome.value, 0)
        for reason in OrderEnd:
            tally["SIN ENTRAR · " + reason.value] = cancels.get(reason.value, 0)
        return tally


def detect_entries(
    run: ImpulseRun, zones: ZonesRun | None, config: EntriesConfig | None = None
) -> EntriesRun:
    """Las entradas de una corrida. Hacen falta las zonas y los ID de H1 y M15.

    Sin zonas no hay ni contexto ni sitio donde poner el límite, y sin detector
    en H1 o en M15 no hay entrada que armar: en los tres casos no se emite nada,
    que no es un error sino un reparto que no da para esto.
    """
    config = config or run.config.entries
    day = TradingDay.of(config)
    off = EntriesRun(enabled=False, day=day, risk_reward=config.risk_reward)
    if not config.enabled or zones is None or not zones.enabled:
        return off
    if any(timeframe not in run.analyses for timeframe in (H1, M15)):
        return off
    if any(timeframe not in zones.per_timeframe for timeframe in (H1, M15)):
        return off

    engine = _Engine(run, zones, detect_zone_signals(run, zones), day, config)
    entries, seeking = engine.walk()
    return EntriesRun(
        enabled=True,
        entries=entries,
        seeking=seeking,
        source=engine.source,
        day=day,
        risk_reward=config.risk_reward,
    )


# --- El contexto de una temporalidad superior ---------------------------------


class _Context:
    """El estado de los ID del Diario o de H4 vela fina a vela fina.

    Los eventos que lo mueven ya están calculados —el toque de la zona en contra
    y el rechazo del UL son señales de la fase 2.0— y aquí sólo se consumen en
    el orden en que se supieron. Nada se sabe antes de tiempo: el toque, al
    cerrar la vela fina en que ocurrió; el rechazo, al cerrar la vela del ID.
    """

    def __init__(
        self,
        timeframe: str,
        items: tuple[ImpulseZones, ...],
        known: np.ndarray,
        touches: dict[int, list[int]],
        rejections: dict[int, list[int]],
    ) -> None:
        self.timeframe = timeframe
        self.items = items
        self._known = known
        self._touches = touches
        self._rejections = rejections
        self.pos = -1
        self.state: ContextState = ContextState.LIBRE
        self._next_touch = 0
        self._next_reject = 0

    @property
    def item(self) -> ImpulseZones | None:
        return None if self.pos < 0 else self.items[self.pos]

    @property
    def direction(self) -> ImpulseDirection | None:
        item = self.item
        return None if item is None else item.direction

    def advance(self, index: int) -> None:
        """Deja el estado como se sabía al cierre de la vela fina `index`."""
        pos = int(self._known[index])
        if pos != self.pos:
            # Otro ID, o limbo: lo que pasó con el anterior no le afecta.
            self.pos, self.state = pos, ContextState.LIBRE
            self._next_touch = self._next_reject = 0
        if pos < 0:
            return
        id_num = self.items[pos].id_num
        touches = self._touches.get(id_num, [])
        rejections = self._rejections.get(id_num, [])
        while True:
            touch = touches[self._next_touch] if self._next_touch < len(touches) else None
            reject = (
                rejections[self._next_reject] if self._next_reject < len(rejections) else None
            )
            if touch is not None and touch <= index and (reject is None or touch <= reject):
                self._next_touch += 1
                self._on_touch()
            elif reject is not None and reject <= index:
                self._next_reject += 1
                self._on_reject()
            else:
                break

    def _on_touch(self) -> None:
        if self.state is not ContextState.AGOTADO:
            self.state = ContextState.A_FAVOR

    def _on_reject(self) -> None:
        if self.state is ContextState.AGOTADO:
            return
        # El propietario dictó el rechazo del UL para H4: ahí se busca en
        # contra. Del Diario no lo dijo, así que su rechazo sólo deja de exigir
        # la dirección (SUPUESTO c).
        self.state = (
            ContextState.EN_CONTRA if self.timeframe == H4 else ContextState.LIBRE
        )

    def exhaust(self) -> None:
        """Hecha la entrada en contra tras el rechazo del UL: ya no manda nada."""
        if self.state is ContextState.EN_CONTRA:
            self.state = ContextState.AGOTADO


@dataclass(frozen=True, slots=True)
class _Order:
    """Un límite puesto, con todo lo que hace falta para registrarlo."""

    kind: EntryKind
    direction: ImpulseDirection
    h1_pos: int
    m15_pos: int
    zone: Zone
    m15_zone: Zone | None
    stop_source: str
    entry: float
    stop: float
    target: float
    #: Vela fina en cuyo cierre se puso. No se puede llenar en ella misma.
    armed: int
    daily_state: ContextState | None
    h4_state: ContextState | None
    context: str


class _Engine:
    """El recorrido entero, vela fina a vela fina. Toda la lectura de series vive aquí."""

    def __init__(
        self,
        run: ImpulseRun,
        zones: ZonesRun,
        signals: ZoneSignalsRun,
        day: TradingDay,
        config: EntriesConfig,
    ) -> None:
        self._config = config
        self._day = day
        self.source, self._fine = _finest(run)
        self._series = {
            timeframe: CandleSeries.of(run.analyses[timeframe].bars)
            for timeframe in (DAILY, H4, H1, M15)
            if timeframe in run.analyses
        }
        self._items = {
            timeframe: zones.per_timeframe[timeframe].items
            for timeframe in self._series
            if timeframe in zones.per_timeframe
        }
        #: De cada vela de cada temporalidad, la última vela fina que cierra con ella.
        self._closing = {
            timeframe: _closing_fine(self._fine, series)
            for timeframe, series in self._series.items()
        }
        self._known = {
            timeframe: self._known_ids(timeframe) for timeframe in self._items
        }
        self._daily = self._context_of(DAILY, signals)
        self._h4 = self._context_of(H4, signals)
        self._pul_breaks = self._hourly_penultimate_breaks()
        self._in_window = day.in_window(self._fine.timestamps)
        self._flat = day.flat(self._fine.timestamps)
        self._days = day.days(self._fine.timestamps)

    # --- Preparación ----------------------------------------------------------

    def _known_ids(self, timeframe: str) -> np.ndarray:
        """Por cada vela fina, el ID de esa temporalidad vigente Y YA CONOCIDO, o -1."""
        total = len(self._fine)
        at = np.full(total, -1, dtype=np.int64)
        closing = self._closing[timeframe]
        for position, zoned in enumerate(self._items[timeframe]):
            first = _known_at(closing, zoned.index_constitution)
            last = (
                total - 1 if zoned.index_end is None else _known_at(closing, zoned.index_end)
            )
            if first is None or last is None or last < first:
                continue
            at[first : last + 1] = position
        return at

    def _context_of(self, timeframe: str, signals: ZoneSignalsRun) -> _Context | None:
        """El contexto de una temporalidad superior, o `None` si no lleva ID."""
        if timeframe not in self._items:
            return None
        touches: dict[int, list[int]] = {}
        rejections: dict[int, list[int]] = {}
        measurement = signals.per_timeframe.get(timeframe)
        closing = self._closing[timeframe]
        for item in measurement.items if measurement is not None else ():
            if item.kind is ZoneSignalKind.TOQUE_PUL:
                # El toque viene fechado en la vela fina: se sabe al cerrarla.
                if item.source == self.source:
                    known: int | None = _bar_of(self._fine, item.timestamp)
                else:
                    known = _known_at(closing, item.signal.index)
                if known is not None:
                    touches.setdefault(item.id_num, []).append(known)
            elif item.kind is ZoneSignalKind.RECHAZO_UL:
                known = _known_at(closing, item.signal.index)
                if known is not None:
                    rejections.setdefault(item.id_num, []).append(known)
        for found in (touches, rejections):
            for values in found.values():
                values.sort()
        return _Context(
            timeframe, self._items[timeframe], self._known[timeframe], touches, rejections
        )

    def _hourly_penultimate_breaks(self) -> dict[int, int]:
        """Por posición del ID de H1, la vela fina en que se supo que rompió su PUL.

        Sólo el PUL —no el APUL— y sólo con el ID vivo: el tramo acaba en la vela
        anterior a la que lo mata. Cerrar más allá del borde exterior es la
        rotura; la mecha no cuenta.
        """
        found: dict[int, int] = {}
        series = self._series[H1]
        closing = self._closing[H1]
        for position, zoned in enumerate(self._items[H1]):
            against = zoned.penultimate
            if against is None:
                continue
            last = len(series) - 1 if zoned.index_end is None else zoned.index_end - 1
            index = first_close_beyond(
                series.close,
                level=against.outer,
                direction=zoned.direction.opposite(),
                first=zoned.index_constitution + 1,
                last=min(last, len(series) - 1),
            )
            if index is None:
                continue
            known = _known_at(closing, index)
            if known is not None:
                found[position] = known
        return found

    # --- El contexto de cada vela ---------------------------------------------

    def _allowed(self) -> tuple[frozenset[ImpulseDirection], str]:
        """Qué direcciones deja buscar el contexto en esta vela, y por qué."""
        allowed = frozenset(ImpulseDirection)
        reasons: list[str] = []

        daily = self._daily
        h4 = self._h4
        if daily is not None and daily.item is not None:
            if daily.state is ContextState.A_FAVOR:
                assert daily.direction is not None
                wanted = daily.direction
                if h4 is None or h4.item is None or h4.direction is not wanted:
                    allowed = frozenset()
                    reasons.append(
                        f"Diario nº {daily.item.id_num} {wanted.value} tocó su zona en "
                        f"contra y H4 no lleva un ID {wanted.value}: no se busca nada"
                    )
                else:
                    allowed = frozenset({wanted})
                    reasons.append(
                        f"Diario nº {daily.item.id_num} {wanted.value} tocó su zona en "
                        f"contra: H4 va {wanted.value} y sólo se buscan entradas "
                        f"{wanted.value}s"
                    )
            else:
                reasons.append(
                    f"Diario nº {daily.item.id_num} {daily.item.direction.value} "
                    f"{daily.state.value}: no restringe"
                )
        else:
            reasons.append("Diario sin ID: no restringe")

        if h4 is not None and h4.item is not None:
            assert h4.direction is not None
            if h4.state is ContextState.A_FAVOR:
                allowed &= {h4.direction}
                reasons.append(
                    f"H4 nº {h4.item.id_num} {h4.direction.value} tocó su zona en "
                    f"contra: sólo entradas {h4.direction.value}s"
                )
            elif h4.state is ContextState.EN_CONTRA:
                allowed &= {h4.direction.opposite()}
                reasons.append(
                    f"H4 nº {h4.item.id_num} {h4.direction.value} rechazó su UL: sólo "
                    f"entradas {h4.direction.opposite().value}s"
                )
            else:
                reasons.append(
                    f"H4 nº {h4.item.id_num} {h4.direction.value} {h4.state.value}: "
                    "no restringe"
                )
        else:
            reasons.append("H4 sin ID: no restringe")
        return allowed, " · ".join(reasons)

    # --- El recorrido ---------------------------------------------------------

    def walk(self) -> tuple[tuple[Entry, ...], tuple[Seeking, ...]]:
        """Vela fina a vela fina: se resuelve, se llena, se cancela y se arma.

        El orden es el de la vida real: primero lo que ya estaba en el mercado
        —la posición viva y el límite puesto—, después el contexto tal como se
        sabe al cierre de la vela, y sólo entonces se decide si se pone un
        límite nuevo. Así ningún límite se arma con lo que hizo la vela que iba
        a llenarlo.
        """
        total = len(self._fine)
        high, low = self._fine.high, self._fine.low
        known_h1 = self._known[H1]
        known_m15 = self._known[M15]

        found: list[Entry] = []
        seeking: list[Seeking] = []
        open_at: int | None = None
        order: _Order | None = None
        traded: Counter[int] = Counter()
        used: set[int] = set()
        key: tuple[object, ...] | None = None
        allowed: frozenset[ImpulseDirection] = frozenset()
        reason = ""

        for index in range(total):
            hi, lo = float(high[index]), float(low[index])
            # 1. La posición viva: stop, objetivo o el cierre de las 16:00.
            if open_at is not None:
                found[open_at], still = self._settle(found[open_at], index, hi, lo)
                if not still:
                    open_at = None
            # 2. El límite puesto: se llena si el precio llega, dentro de la franja.
            elif (
                order is not None
                and index > order.armed
                and bool(self._in_window[index])
                and fills(order.direction, order.entry, hi, lo)
            ):
                entry = self._record(order, len(found) + 1, filled=self._fine.at(index))
                traded[int(self._days[index])] += 1
                if order.h4_state is ContextState.EN_CONTRA and self._h4 is not None:
                    self._h4.exhaust()
                entry, still = self._settle(entry, index, hi, lo)
                if still:
                    open_at = len(found)
                found.append(entry)
                order = None

            # 3. El contexto, como se sabe al cierre de esta vela.
            if self._daily is not None:
                self._daily.advance(index)
            if self._h4 is not None:
                self._h4.advance(index)
            state_key = (
                None if self._daily is None else (self._daily.pos, self._daily.state),
                None if self._h4 is None else (self._h4.pos, self._h4.state),
            )
            if state_key != key:
                key = state_key
                allowed, reason = self._allowed()
                if seeking and seeking[-1].allowed == allowed and seeking[-1].reason == reason:
                    pass
                else:
                    if seeking:
                        seeking[-1] = replace(seeking[-1], end=self._fine.at(index))
                    seeking.append(self._seeking(len(seeking) + 1, allowed, reason, index))

            # 4. El límite que ya no vale se quita.
            if order is not None:
                why = self._cancels(order, index, allowed, known_h1, known_m15)
                if why is not None:
                    found.append(
                        self._record(
                            order, len(found) + 1, cancelled=self._fine.at(index), why=why
                        )
                    )
                    order = None

            # 5. Y se pone el de esta vela, si toca.
            if (
                order is None
                and open_at is None
                and bool(self._in_window[index])
                and traded[int(self._days[index])] < self._config.trades_per_day
                and known_h1[index] >= 0
                and known_m15[index] >= 0
                and allowed
            ):
                order = self._arm(
                    index, int(known_h1[index]), int(known_m15[index]), allowed, reason, used
                )

        if order is not None:
            found.append(
                self._record(
                    order,
                    len(found) + 1,
                    cancelled=self._fine.at(total - 1),
                    why=OrderEnd.FIN_HISTORICO,
                )
            )
        found.sort(key=lambda item: (item.ts_armed, item.seq))
        return (
            tuple(replace(item, seq=position + 1) for position, item in enumerate(found)),
            tuple(seeking),
        )

    def _settle(self, entry: Entry, index: int, hi: float, lo: float) -> tuple[Entry, bool]:
        """La posición con esta vela: cerrada por stop, objetivo o reloj, o viva."""
        closed = resolve(entry.direction, stop=entry.stop, target=entry.target, high=hi, low=lo)
        if closed is not None:
            outcome, price = closed
            return _closed(entry, self._fine.at(index), price, outcome), False
        if bool(self._flat[index]):
            return (
                _closed(
                    entry,
                    self._fine.at(index),
                    float(self._fine.close[index]),
                    TradeOutcome.CIERRE_SESION,
                ),
                False,
            )
        return entry, True

    def _cancels(
        self,
        order: _Order,
        index: int,
        allowed: frozenset[ImpulseDirection],
        known_h1: np.ndarray,
        known_m15: np.ndarray,
    ) -> OrderEnd | None:
        """Por qué se quita este límite en esta vela, o `None` si sigue puesto."""
        if int(known_h1[index]) != order.h1_pos:
            return OrderEnd.MUERTE_ID_H1
        if not bool(self._in_window[index]):
            return OrderEnd.FIN_FRANJA
        if order.direction not in allowed:
            return OrderEnd.CAMBIO_CONTEXTO
        m15 = int(known_m15[index])
        if m15 < 0 or self._items[M15][m15].direction is not order.direction:
            return OrderEnd.CAMBIO_M15
        return None

    def _arm(
        self,
        index: int,
        h1_pos: int,
        m15_pos: int,
        allowed: frozenset[ImpulseDirection],
        reason: str,
        used: set[int],
    ) -> _Order | None:
        """El límite de esta vela, si el ID de H1 ofrece uno y M15 lo confirma."""
        zoned = self._items[H1][h1_pos]
        against = zoned.against
        if against is None:
            return None
        fifteen = self._items[M15][m15_pos]
        close = float(self._fine.close[index])

        # ROTURA_PUL: el precio ya cerró más allá del PUL y todavía no ha vuelto.
        broke = self._pul_breaks.get(h1_pos)
        if broke is not None and broke <= index and h1_pos not in used:
            direction = zoned.direction.opposite()
            if (
                direction in allowed
                and fifteen.direction is direction
                and beyond_price(against.outer, direction, close)
            ):
                order = self._order(
                    EntryKind.ROTURA_PUL, direction, against.outer, against,
                    fifteen, h1_pos, m15_pos, index, reason,
                )
                if order is not None:
                    used.add(h1_pos)
                    return order

        # TOQUE_ZONA: a favor del ID, en su zona en contra, con el precio fuera.
        direction = zoned.direction
        if (
            direction in allowed
            and fifteen.direction is direction
            and beyond_price(against.inner, direction, close)
        ):
            return self._order(
                EntryKind.TOQUE_ZONA, direction, against.inner, against,
                fifteen, h1_pos, m15_pos, index, reason,
            )
        return None

    def _order(
        self,
        kind: EntryKind,
        direction: ImpulseDirection,
        entry: float,
        zone: Zone,
        fifteen: ImpulseZones,
        h1_pos: int,
        m15_pos: int,
        index: int,
        reason: str,
    ) -> _Order | None:
        """Entrada, stop y objetivo. El stop es el más cercano entre H1 y M15."""
        m15_zone = fifteen.against
        # En la rotura del PUL el precio vuelve por el borde exterior y lo que
        # queda detrás es el interior; en el toque, al revés.
        h1_stop = zone.inner if kind is EntryKind.ROTURA_PUL else zone.outer
        m15_stop = None if m15_zone is None else m15_zone.outer
        stop = nearest_stop(entry, direction, (h1_stop, m15_stop))
        if stop is None:
            return None
        source = STOP_FROM_M15 if m15_stop is not None and stop == m15_stop else STOP_FROM_H1
        return _Order(
            kind=kind,
            direction=direction,
            h1_pos=h1_pos,
            m15_pos=m15_pos,
            zone=zone,
            m15_zone=m15_zone,
            stop_source=source,
            entry=entry,
            stop=stop,
            target=target_for(entry, stop, direction, self._config.risk_reward),
            armed=index,
            daily_state=None if self._daily is None or self._daily.item is None else self._daily.state,
            h4_state=None if self._h4 is None or self._h4.item is None else self._h4.state,
            context=reason,
        )

    def _record(
        self,
        order: _Order,
        seq: int,
        *,
        filled: datetime | None = None,
        cancelled: datetime | None = None,
        why: OrderEnd | None = None,
    ) -> Entry:
        """El límite, escrito como registro: llenado o quitado, nunca las dos."""
        h1 = self._items[H1][order.h1_pos]
        return Entry(
            seq=seq,
            kind=order.kind,
            direction=order.direction,
            h1_id=h1.id_num,
            h1_direction=h1.direction,
            m15_id=self._items[M15][order.m15_pos].id_num,
            zone_kind=order.zone.kind,
            zone_low=order.zone.low,
            zone_high=order.zone.high,
            m15_zone_low=None if order.m15_zone is None else order.m15_zone.low,
            m15_zone_high=None if order.m15_zone is None else order.m15_zone.high,
            stop_source=order.stop_source,
            daily_state=order.daily_state,
            h4_state=order.h4_state,
            context=order.context,
            ts_armed=self._fine.at(order.armed),
            entry=order.entry,
            stop=order.stop,
            target=order.target,
            ts_filled=filled,
            outcome=None if filled is None else TradeOutcome.ABIERTA,
            cancelled_by=why,
            ts_cancelled=cancelled,
        )

    def _seeking(
        self, seq: int, allowed: frozenset[ImpulseDirection], reason: str, index: int
    ) -> Seeking:
        daily = self._daily
        h4 = self._h4
        return Seeking(
            seq=seq,
            allowed=allowed,
            reason=reason,
            daily_id=None if daily is None or daily.item is None else daily.item.id_num,
            daily_state=None if daily is None or daily.item is None else daily.state,
            h4_id=None if h4 is None or h4.item is None else h4.item.id_num,
            h4_state=None if h4 is None or h4.item is None else h4.state,
            start=self._fine.at(index),
            end=None,
        )


# --- Apoyo ----------------------------------------------------------------------


def _closed(entry: Entry, moment: datetime, price: float, outcome: TradeOutcome) -> Entry:
    return replace(entry, ts_closed=moment, exit_price=price, outcome=outcome)


def _known_at(closing: np.ndarray, index: int) -> int | None:
    """La vela fina en cuyo cierre se supo lo que pasó en la vela `index`."""
    if not 0 <= index < len(closing):
        return None
    value = int(closing[index])
    return None if value < 0 else value


def _bar_of(series: CandleSeries, moment: datetime) -> int:
    """Posición de la vela que CONTIENE ese instante."""
    return int(series.timestamps.searchsorted(pd.Timestamp(moment), side="right")) - 1


def _closing_fine(fine: CandleSeries, coarse: CandleSeries) -> np.ndarray:
    """Por cada vela de `coarse`, la última de `fine` que cierra con ella.

    `-1` cuando la serie fina no llega a cubrirla. Las velas van fechadas en su
    apertura, así que el tramo de una vela grande acaba justo antes de la
    apertura de la siguiente.
    """
    if not len(coarse) or not len(fine):
        return np.full(len(coarse), -1, dtype=np.int64)
    edges = np.searchsorted(fine.timestamps, coarse.timestamps, side="left")
    closing = np.empty(len(coarse), dtype=np.int64)
    closing[:-1] = edges[1:] - 1
    closing[-1] = len(fine) - 1
    return np.minimum(closing, len(fine) - 1)


def _finest(run: ImpulseRun) -> tuple[str, CandleSeries]:
    """La serie de vela más corta de la corrida —M5— o la de M15 si no hay otra."""
    charts = [name for name in run.chart_bars if name in TIMEFRAME_MINUTES]
    finest = min(charts, key=lambda name: TIMEFRAME_MINUTES[name]) if charts else M15
    if finest not in run.chart_bars or TIMEFRAME_MINUTES[finest] > TIMEFRAME_MINUTES[M15]:
        return M15, CandleSeries.of(run.analyses[M15].bars)
    return finest, CandleSeries.of(run.chart_bars[finest])


__all__ = [
    "STOP_FROM_H1",
    "STOP_FROM_M15",
    "EntriesRun",
    "Entry",
    "Seeking",
    "detect_entries",
]
