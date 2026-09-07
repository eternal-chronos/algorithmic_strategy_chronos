"""Fase 3.1 — las ENTRADAS. H4 manda el sentido, H1 arma el setup, M15 afina.

Esto sí abre operaciones: hay límite, stop, objetivo y un resultado. Sigue sin
haber métricas —ni R esperada, ni aciertos, ni curva— a propósito: lo que se
entrega es el dibujo, para que el propietario mire operación a operación qué está
bien y qué está mal antes de que nadie mire un número.

**El Diario se queda fuera.** En esta fase sólo intervienen H4, H1 y M15.

## 1. H4 dice hacia dónde se busca

Un ID de H4 vive entre dos sitios: su **UL**, que es a donde iba, y su **zona en
contra** —el PUL o el APUL—, que es a donde vuelve. El precio va de uno a otro y
**siempre se busca en la dirección del sitio al que va**. Con un ID alcista de
H4 (el bajista es su espejo):

1. recién constituido, el precio todavía no ha vuelto: se buscan **ventas**,
   hasta que toque la zona en contra;
2. tocada la zona en contra, se buscan **compras**, hasta que llegue al UL;
3. llegado al UL se **espera al cierre de esa vela de H4**: no se busca nada. Si
   la vela cierra sin atravesar el UL entero, lo ha rechazado y se vuelve a
   buscar **ventas**; si cierra más allá, ha roto el ID y ahí se acaba todo hasta
   que nazca otro.

Tocar es un asunto de mechas y se mide en M15, igual que en la fase 3.0: no se
esperan cuatro horas para saber que el precio ya está en la zona. El cierre de la
vela de H4 sí es un cierre: es la única de las tres transiciones que espera.

## 2. H1 arma el setup, de dos maneras y sólo de dos

Con una dirección buscada —pongamos ventas— se mira el ID de H1:

- **Forma ZONA.** El ID de H1 va en la dirección que se busca (bajista) y trae su
  zona en contra —el PUL o el APUL, que en un ID bajista queda por encima—. Ahí
  es donde se pone el límite.
- **Forma RECHAZO_UL.** El ID de H1 va al revés (alcista) y por tanto no ofrece
  ninguna zona para vender... salvo que **rechace su propio UL**: la mecha entra
  en la zona y la vela cierra con el cuerpo fuera. Ese rechazo abre la búsqueda
  dentro del UL.

## 3. M15 afina la entrada

Dentro de esa zona de H1 se busca **un OB o un FVG** de M15
(`domain/entries/patterns.py`) y el límite se pone en su borde **cercano** —el
que el precio encuentra al volver— con el **stop detrás del borde lejano**, que
es el sitio más cercano en el que el patrón deja de valer. El objetivo es
**siempre 1:3**.

**El rechazo del UL con OB va aparte.** Ahí el límite NO se pone en el borde del
OB: se pone un poco por delante del **cierre de la vela que lo crea** —la que se
va y convierte a la anterior en OB—, a la holgura fija de la config. En una venta
es ese cierre más la holgura, y en una compra menos. Lo dijo el propietario
mirando la operación nº 855: el OB sobresalía por encima del UL y el límite
quedaba en un sitio al que el precio ya no volvía. El FVG y la forma ZONA siguen
con su borde cercano.

**Y ahí el stop tampoco va donde va siempre.** El stop es el borde **exterior**
de la zona de H1 en todos los casos menos en ése: en el rechazo del UL afinado
con un OB va al borde **interior**, donde arranca la mecha, que es la línea del
extremo del ID de H1. Lo pidió el propietario: cuando se baja a M15 a esperar el
OB, la punta de la mecha del UL queda tan arriba que el riesgo es casi todo mecha
vieja, y lo que hay que perder para que el rechazo deje de serlo es el borde de
dentro.

De los patrones disponibles se coge el **más reciente**: es "lo más cercano que
hay ahí". Un patrón que el precio ya ha atravesado —una vela posterior fue más
allá de su borde lejano— deja de valer y no se vuelve a mirar.

## 4. Una y sólo una

- **Una operación por ID de H1.** Entrada la que entra: hasta que no nazca otro
  ID de H1 no se busca nada más en él.
- **Nunca más de una operación viva.** Con una abierta no se arma ningún límite.
- **El límite se quita** cuando muere el ID de H1 del que cuelga o cuando cambia
  el régimen de H4: lo que se estaba buscando ya no se busca.
- La operación abierta la cierran el objetivo, el stop o **el cierre del
  viernes**. Que muera el ID de H1 o cambie el régimen no la toca.

## 4 bis. El viernes se cierra todo

Cuando cierra el mercado —el viernes a las 17:00 de Nueva York— no se queda nada
vivo: la posición abierta **se cierra al precio de la última vela de la semana**,
sin esperar al stop ni al objetivo, y el límite puesto **se quita**. El fin de
semana no se opera y el hueco de la apertura del domingo no lo decide ninguna
regla de esta estrategia.

El cierre semanal no manda sobre el stop: si esa misma vela tocó el stop o el
objetivo, la operación acabó ahí. Y la última vela del histórico no es un cierre
semanal aunque caiga en viernes: ahí lo que se acabó son los datos.

## 5. Lo que se supone y no está cerrado

Lo dice el propietario: *"pon el stop loss donde mejor lo veas y luego iremos
afinando"*. Queda declarado aquí y no escondido en el código:

- el **stop** es el borde lejano del patrón de M15, sin holgura;
- el **límite** es el borde cercano del patrón, no un precio dentro de él, salvo
  en el rechazo del UL con OB, donde es el cierre de la vela que lo crea más la
  holgura;
- la **holgura** es un número fijo en dólares y no un porcentaje de nada: 0,175,
  que es lo que salió de medir la caja que dibujó el propietario;
- si una misma vela de M15 toca el stop y el objetivo, manda el **stop**;
- una vela que llena el límite y alcanza el stop en el mismo cuarto de hora entra
  y sale perdiendo: es la lectura prudente, no la favorable;
- **la franja de operativa sigue puesta** (03:00 a 12:00 de Nueva York): fuera de
  ella no se arma ningún límite. Un límite ya puesto sí se puede llenar a
  cualquier hora **de la semana**, porque una orden en el mercado no mira el
  reloj —pero el viernes se quita, que es cuando el mercado sí lo mira—;
- el **cierre semanal** se resuelve sobre la última vela que hay antes del corte
  del viernes, no sobre una vela de las 17:00 que no existe: así el cierre cae
  donde el histórico dice, con sus festivos y sus huecos.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from chronos.application.entries.trading_window import (
    MARKET_WEEK,
    TRADING_WINDOW,
    MarketWeek,
    TradingWindow,
)
from chronos.application.structure.config import H1, H4, TIMEFRAME_MINUTES
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zone_signals import ZoneSignalsRun, detect_zone_signals
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.entries.patterns import (
    DEFAULT_DISPLACEMENT,
    PatternKind,
    PricePattern,
    patterns_of,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind

#: El R:R de todas las operaciones. Lo fija el propietario y no se negocia por
#: setup: 1:3 o nada.
RISK_REWARD = 3.0

#: Lo que el límite del rechazo se separa del cierre de la vela que crea el OB,
#: en dólares. Es un número fijo y medido, no una fórmula: sale de la caja que
#: dibujó el propietario sobre la operación nº 855.
REJECTION_OFFSET = 0.175


class RegimeKind(StrEnum):
    """Los tres estados de un ID de H4. Los valores salen al explorador."""

    #: El precio va hacia la zona en contra: se busca CONTRA el ID de H4.
    HACIA_ZONA = "HACIA_ZONA"
    #: Tocada la zona en contra, el precio va hacia el UL: se busca A FAVOR.
    HACIA_UL = "HACIA_UL"
    #: El precio está en el UL y se espera al cierre de la vela de H4. No se
    #: busca nada: es el único tramo mudo.
    EN_UL = "EN_UL"


class RegimeEdge(StrEnum):
    """Qué abrió o cerró un tramo. Es la regla que se audita."""

    CONSTITUCION_H4 = "CONSTITUCION_H4"
    TOQUE_ZONA = "TOQUE_ZONA"
    TOQUE_UL = "TOQUE_UL"
    RECHAZO_UL = "RECHAZO_UL"
    ROTURA_UL = "ROTURA_UL"
    MUERTE_ID_H4 = "MUERTE_ID_H4"
    FIN_HISTORICO = "FIN_HISTORICO"


class EntryForm(StrEnum):
    """Las dos maneras de armar un setup en H1."""

    #: El ID de H1 va en la dirección buscada: se mira su zona en contra.
    ZONA = "ZONA"
    #: El ID de H1 va al revés pero ha rechazado su UL: se mira ese UL.
    RECHAZO_UL = "RECHAZO_UL"


class OrderEnd(StrEnum):
    """Por qué se quitó un límite que no llegó a llenarse."""

    MUERTE_ID_H1 = "MUERTE_ID_H1"
    FIN_REGIMEN = "FIN_REGIMEN"
    #: Cerró el mercado el viernes: no se deja ninguna orden puesta el fin de
    #: semana.
    CIERRE_SEMANAL = "CIERRE_SEMANAL"
    FIN_HISTORICO = "FIN_HISTORICO"


class TradeOutcome(StrEnum):
    """Cómo acabó una operación que sí entró."""

    OBJETIVO = "OBJETIVO"
    STOP = "STOP"
    #: Cerró el mercado el viernes con la posición viva: se cierra al precio de
    #: esa vela, ni en ganancia entera ni en pérdida entera.
    CIERRE_SEMANAL = "CIERRE_SEMANAL"
    #: Se acabó el histórico con la posición abierta.
    ABIERTA = "ABIERTA"


@dataclass(frozen=True, slots=True)
class Regime:
    """Un tramo de un ID de H4 con lo que se busca dentro de él."""

    seq: int
    kind: RegimeKind
    #: Lo que se busca. `None` en `EN_UL`, que es el tramo en el que no se busca.
    direction: ImpulseDirection | None
    h4_id: int
    h4_direction: ImpulseDirection
    start: datetime
    #: `None` = seguía abierto al acabarse el histórico.
    end: datetime | None
    opened_by: RegimeEdge
    closed_by: RegimeEdge | None
    #: Los dos sitios entre los que se mueve el precio, para poder dibujarlos.
    zone_low: float
    zone_high: float
    last_low: float
    last_high: float


@dataclass(frozen=True, slots=True)
class Entry:
    """Un límite y, si llegó a llenarse, la operación que salió de él."""

    seq: int
    direction: ImpulseDirection
    form: EntryForm
    pattern: PatternKind
    h4_id: int
    h1_id: int
    #: Zona de H1 en la que se buscó: la de en contra o el UL.
    zone_kind: ZoneKind
    zone_low: float
    zone_high: float
    #: El patrón de M15 que afina la entrada.
    pattern_low: float
    pattern_high: float
    #: Vela de M15 que define el patrón y vela en cuyo cierre se supo que existía.
    ts_pattern: datetime
    ts_pattern_known: datetime
    #: Vela de M15 en cuyo cierre se puso el límite.
    ts_armed: datetime
    entry: float
    stop: float
    target: float
    #: `None` mientras no se llenó.
    ts_filled: datetime | None = None
    ts_closed: datetime | None = None
    exit_price: float | None = None
    outcome: TradeOutcome | None = None
    #: Por qué se quitó el límite. `None` si llegó a entrar.
    cancelled_by: OrderEnd | None = None
    ts_cancelled: datetime | None = None

    @property
    def risk(self) -> float:
        """La distancia del stop, en dólares. Es 1R."""
        return abs(self.entry - self.stop)

    @property
    def filled(self) -> bool:
        return self.ts_filled is not None


@dataclass(frozen=True, slots=True)
class EntriesRun:
    """Todas las operaciones de una corrida, en orden cronológico."""

    enabled: bool
    entries: tuple[Entry, ...] = ()
    regimes: tuple[Regime, ...] = ()
    window: TradingWindow = TRADING_WINDOW
    #: El cierre del viernes con el que se corrió: el explorador lo escribe,
    #: porque una operación que acaba sin tocar stop ni objetivo no se entiende
    #: sin saber a qué hora cierra la semana.
    market_week: MarketWeek = MARKET_WEEK
    risk_reward: float = RISK_REWARD
    #: La holgura con la que se armó el límite del rechazo con OB. Viaja con la
    #: corrida porque el explorador la escribe: un límite fuera del patrón no se
    #: puede auditar sin saber de dónde sale.
    rejection_offset: float = REJECTION_OFFSET

    @property
    def empty(self) -> bool:
        return not self.entries and not self.regimes

    @property
    def filled(self) -> tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.filled)

    #: Con qué se escriben los motivos de los límites que no entraron. El
    #: prefijo no es adorno: `CIERRE_SEMANAL` es a la vez un final de operación
    #: y un motivo de retirada, y sin él una fila se comería a la otra.
    CANCEL_PREFIX = "SIN ENTRAR · "

    def counts(self) -> dict[str, int]:
        """Cuántos límites, cuántos entraron y cómo acabaron. Sin porcentajes."""
        outcomes = Counter(
            entry.outcome.value for entry in self.entries if entry.outcome is not None
        )
        cancels = Counter(
            entry.cancelled_by.value
            for entry in self.entries
            if entry.cancelled_by is not None
        )
        tally = {
            "LIMITES": len(self.entries),
            "ENTRADAS": len(self.filled),
        }
        for outcome in TradeOutcome:
            tally[outcome.value] = outcomes.get(outcome.value, 0)
        for reason in OrderEnd:
            tally[self.CANCEL_PREFIX + reason.value] = cancels.get(reason.value, 0)
        return tally


@dataclass(frozen=True, slots=True)
class EntriesConfig:
    """Lo poco que esta fase tiene que elegir. Todo declarado, nada implícito."""

    #: Velas de M15 que se le dan al precio para irse tras el OB.
    displacement: int = DEFAULT_DISPLACEMENT
    #: R:R de todas las operaciones.
    risk_reward: float = RISK_REWARD
    #: Cuánto se separa el límite del rechazo del cierre de la vela que crea el
    #: OB, en dólares. Por delante del precio: arriba en venta y abajo en compra.
    rejection_offset: float = REJECTION_OFFSET


def detect_entries(
    run: ImpulseRun,
    zones: ZonesRun | None,
    *,
    window: TradingWindow = TRADING_WINDOW,
    market_week: MarketWeek = MARKET_WEEK,
    config: EntriesConfig | None = None,
) -> EntriesRun:
    """Las operaciones de una corrida. Hacen falta los ID de H4 y de H1 y las zonas.

    Sin zonas no hay ni régimen ni setup, y sin detector en H1 no hay segundo
    escalón: en los dos casos no se emite nada, que no es un error sino un
    reparto que no da para esta fase.
    """
    config = config or EntriesConfig()
    apagado = EntriesRun(enabled=False, window=window, market_week=market_week)
    if zones is None or not zones.enabled:
        return apagado
    if H4 not in run.analyses or H1 not in run.analyses:
        return apagado
    if H4 not in zones.per_timeframe or H1 not in zones.per_timeframe:
        return apagado

    builder = _Entries(
        run, zones, detect_zone_signals(run, zones), window, market_week, config
    )
    regimes, entries = builder.build()
    return EntriesRun(
        enabled=True,
        entries=entries,
        regimes=regimes,
        window=window,
        market_week=market_week,
        risk_reward=config.risk_reward,
        rejection_offset=config.rejection_offset,
    )


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Un patrón de M15 que puede armar un límite, con su ventana de validez."""

    pattern: PricePattern
    form: EntryForm
    direction: ImpulseDirection
    zone: Zone
    h1_index: int
    #: Primera vela de M15 en cuyo cierre se puede poner el límite.
    since: int
    #: Última vela en la que sigue valiendo: la anterior a que el precio
    #: atraviese el borde lejano, o la muerte del ID de H1.
    until: int


@dataclass(frozen=True, slots=True)
class _Order:
    """Un límite puesto: qué patrón lo arma, a qué precios y bajo qué régimen."""

    candidate: _Candidate
    #: Entrada, stop y objetivo, ya calculados: el R:R no se recalcula al llenar.
    levels: tuple[float, float, float]
    #: Vela de M15 en cuyo cierre se puso. No se puede llenar en ella misma.
    armed: int
    #: Tramo de régimen bajo el que se puso: si cambia, el límite se quita.
    regime: int
    h4_id: int


class _Entries:
    """El recorrido entero. Toda la lectura de series vive aquí."""

    def __init__(
        self,
        run: ImpulseRun,
        zones: ZonesRun,
        signals: ZoneSignalsRun,
        window: TradingWindow,
        market_week: MarketWeek,
        config: EntriesConfig,
    ) -> None:
        self._config = config
        self._window = window
        self._h4 = CandleSeries.of(run.analyses[H4].bars)
        self._h1 = CandleSeries.of(run.analyses[H1].bars)
        self._fine = _finest(run, self._h1)
        self._h4_items = zones.per_timeframe[H4].items
        self._h1_items = zones.per_timeframe[H1].items
        #: De cada vela de H4 y de H1, el último M15 que cierra con ella.
        self._h4_close = _closing_fine(self._fine, self._h4)
        self._h1_close = _closing_fine(self._fine, self._h1)
        self._in_window = window.mask(self._fine.timestamps)
        #: Las velas en las que cierra la semana: ahí no queda nada vivo.
        self._week_close = market_week.closes(self._fine.timestamps)
        #: Los patrones de M15 de cada dirección, ya ordenados por conocimiento.
        self._patterns = {
            direction: patterns_of(
                open_=self._fine.open,
                high=self._fine.high,
                low=self._fine.low,
                close=self._fine.close,
                direction=direction,
                displacement=config.displacement,
            )
            for direction in ImpulseDirection
        }
        #: Los mismos patrones indexados por la vela en la que se supieron: con
        #: ocho años de M15 hay decenas de miles, y recorrerlos enteros por cada
        #: ID de H1 sería un bucle sobre el producto de las dos listas.
        self._known = {
            direction: np.array(
                [item.index_known for item in found], dtype=np.int64
            )
            for direction, found in self._patterns.items()
        }
        self._rejections = self._hourly_rejections(signals)
        self._seq = 0

    # --- Régimen de H4 ------------------------------------------------------

    def build(self) -> tuple[tuple[Regime, ...], tuple[Entry, ...]]:
        regimes = self._regimes()
        return regimes, self._walk(regimes)

    def _regimes(self) -> tuple[Regime, ...]:
        """Los tramos de todos los ID de H4, en orden y sin solaparse.

        Un ID sin zona en contra no produce ninguno: sin el sitio al que el
        precio vuelve no hay hacia dónde buscar. Les pasa a los primeros ID del
        histórico y es un estado legítimo, no un fallo.
        """
        found: list[Regime] = []
        for zoned in self._h4_items:
            against = zoned.against
            if against is None:
                continue
            found.extend(self._regimes_of(zoned, against))
        found.sort(key=lambda item: (item.start, item.seq))
        return tuple(found)

    def _regimes_of(self, zoned: ImpulseZones, against: Zone) -> list[Regime]:
        """Los tramos de un ID de H4, recorriendo sus velas de M15.

        Cada tramo se cierra en la vela que lo cierra y el siguiente **empieza en
        esa misma vela**: lo que decide el cambio se sabe al cerrarla, y el
        límite se arma también al cierre, así que en esa vela ya manda lo nuevo.
        """
        first = self._known_at(self._h4_close, zoned.index_constitution)
        last = (
            len(self._fine) - 1
            if zoned.index_end is None
            else self._known_at(self._h4_close, zoned.index_end)
        )
        if first is None or last is None or last < first:
            return []

        found: list[Regime] = []
        kind = RegimeKind.HACIA_ZONA
        opened_by = RegimeEdge.CONSTITUCION_H4
        start = index = first
        ul_bar: int | None = None
        while index <= last:
            if kind is RegimeKind.HACIA_ZONA:
                hit = self._touches(against.low, against.high, index, last)
                if hit is None:
                    break
                at, edge, following = hit, RegimeEdge.TOQUE_ZONA, RegimeKind.HACIA_UL
            elif kind is RegimeKind.HACIA_UL:
                hit = self._reaches(zoned.last.inner, zoned.direction, index, last)
                if hit is None:
                    break
                at, edge, following = hit, RegimeEdge.TOQUE_UL, RegimeKind.EN_UL
                ul_bar = _bar_of(self._h4, self._fine.at(hit))
            else:
                # EN_UL: se espera al CIERRE de la vela de H4 del toque, y sólo
                # a ella. Es la única transición que no se resuelve con mechas.
                if ul_bar is None or not 0 <= ul_bar < len(self._h4):
                    break
                closing = int(self._h4_close[ul_bar])
                if closing < 0 or closing > last:
                    break
                broke = _beyond(
                    float(self._h4.close[ul_bar]), zoned.last.outer, zoned.direction
                )
                at = max(closing, start)
                edge = RegimeEdge.ROTURA_UL if broke else RegimeEdge.RECHAZO_UL
                following = RegimeKind.HACIA_ZONA
            found.append(self._regime(zoned, against, kind, opened_by, start, at, edge))
            if edge is RegimeEdge.ROTURA_UL:
                return found
            kind, opened_by, start, index = following, edge, at, at + 1
        if start <= last:
            closing_edge = (
                RegimeEdge.MUERTE_ID_H4 if zoned.index_end is not None else None
            )
            found.append(
                self._regime(
                    zoned,
                    against,
                    kind,
                    opened_by,
                    start,
                    None if closing_edge is None else last,
                    closing_edge,
                )
            )
        return found

    def _regime(
        self,
        zoned: ImpulseZones,
        against: Zone,
        kind: RegimeKind,
        opened_by: RegimeEdge,
        start: int,
        end: int | None,
        closed_by: RegimeEdge | None,
    ) -> Regime:
        self._seq += 1
        direction = {
            RegimeKind.HACIA_ZONA: zoned.direction.opposite(),
            RegimeKind.HACIA_UL: zoned.direction,
            RegimeKind.EN_UL: None,
        }[kind]
        return Regime(
            seq=self._seq,
            kind=kind,
            direction=direction,
            h4_id=zoned.id_num,
            h4_direction=zoned.direction,
            start=self._fine.at(start),
            end=None if end is None else self._fine.at(end),
            opened_by=opened_by,
            closed_by=closed_by,
            zone_low=against.low,
            zone_high=against.high,
            last_low=zoned.last.low,
            last_high=zoned.last.high,
        )

    # --- Setups de H1 -------------------------------------------------------

    def _hourly_rejections(self, signals: ZoneSignalsRun) -> dict[int, int]:
        """Por ID de H1, la vela de M15 en la que se supo que rechazó su UL.

        El rechazo es el `RECHAZO_UL` de la fase 2.0 —la mecha entra en el UL y
        la vela no cierra más allá de su borde exterior— con una condición más,
        que es la del propietario: **la vela tiene que cerrar con el cuerpo
        fuera de la zona**. Un cierre dentro del UL no ha rechazado nada, se ha
        quedado ahí metido.
        """
        measurement = signals.per_timeframe.get(H1)
        if measurement is None:
            return {}
        first: dict[int, int] = {}
        for item in measurement.items:
            if item.kind is not ZoneSignalKind.RECHAZO_UL or item.signal.inside:
                continue
            known = self._known_at(self._h1_close, item.signal.index)
            if known is not None:
                first.setdefault(item.id_num, known)
        return first

    def _candidates_of(self, position: int) -> tuple[_Candidate, ...]:
        """Los patrones de M15 que ese ID de H1 puede llegar a usar.

        Los dos setups a la vez, porque cuál de los dos vale depende del régimen
        de H4, que se resuelve al recorrer: el ID de H1 ofrece su **zona en
        contra** para operar en su misma dirección y, si rechazó su UL, ese UL
        para operar al revés.
        """
        zoned = self._h1_items[position]
        birth = self._known_at(self._h1_close, zoned.index_constitution)
        death = (
            len(self._fine) - 1
            if zoned.index_end is None
            else self._known_at(self._h1_close, zoned.index_end)
        )
        if birth is None or death is None or death < birth:
            return ()

        found: list[_Candidate] = []
        against = zoned.against
        if against is not None:
            found.extend(
                self._candidates_in(
                    zone=against,
                    form=EntryForm.ZONA,
                    direction=zoned.direction,
                    # La zona es esa mecha vieja: cuenta lo que se haya formado
                    # dentro de ella desde que la vela que la define existe.
                    since=_bar_of(self._fine, against.ts_defining),
                    armed=birth,
                    death=death,
                    position=position,
                )
            )
        rejection = self._rejections.get(zoned.id_num)
        if rejection is not None and birth <= rejection <= death:
            found.extend(
                self._candidates_in(
                    zone=zoned.last,
                    form=EntryForm.RECHAZO_UL,
                    direction=zoned.direction.opposite(),
                    # El OB del rechazo es el del rechazo: no vale uno de antes.
                    since=rejection,
                    armed=rejection,
                    death=death,
                    position=position,
                )
            )
        found.sort(key=lambda item: (item.since, item.pattern.index_origin))
        return tuple(found)

    def _candidates_in(
        self,
        *,
        zone: Zone,
        form: EntryForm,
        direction: ImpulseDirection,
        since: int,
        armed: int,
        death: int,
        position: int,
    ) -> list[_Candidate]:
        """Los patrones de una zona concreta, con su ventana de validez."""
        found: list[_Candidate] = []
        for pattern in self._between(direction, max(since, 0), death):
            if pattern.height <= 0 or not pattern.overlaps(zone.low, zone.high):
                continue
            # El LÍMITE tiene que quedar por delante del stop: uno pegado a él
            # no deja riesgo que medir y no es una operación. Se mide con los
            # dos precios que va a tener de verdad, que en el rechazo con OB no
            # son ni el borde del patrón ni el borde exterior de la zona.
            if not _risky(
                self._limit_price(pattern, form, direction),
                self._stop_price(pattern, form, zone),
                direction,
            ):
                continue
            start = max(pattern.index_known, armed)
            if start > death:
                continue
            found.append(
                _Candidate(
                    pattern=pattern,
                    form=form,
                    direction=direction,
                    zone=zone,
                    h1_index=position,
                    since=start,
                    until=self._mitigated(pattern, start, death),
                )
            )
        return found

    def _between(
        self, direction: ImpulseDirection, first: int, last: int
    ) -> tuple[PricePattern, ...]:
        """Los patrones de esa dirección que se supieron dentro de `[first, last]`.

        La lista viene ordenada por `index_known`, así que el tramo se recorta
        con dos búsquedas binarias en vez de recorrerla entera.
        """
        if last < first:
            return ()
        known = self._known[direction]
        start = int(np.searchsorted(known, first, side="left"))
        stop = int(np.searchsorted(known, last, side="right"))
        return self._patterns[direction][start:stop]

    def _mitigated(self, pattern: PricePattern, first: int, last: int) -> int:
        """Última vela en la que el patrón sigue en pie.

        Deja de valer cuando el precio **atraviesa su borde lejano**: ahí ya no
        es un sitio al que volver, es un nivel roto. Se mira desde la vela
        siguiente a la que lo completó.
        """
        if last <= first:
            return last
        window = slice(first + 1, last + 1)
        if pattern.direction is ImpulseDirection.BAJISTA:
            gone = self._fine.high[window] > pattern.far
        else:
            gone = self._fine.low[window] < pattern.far
        hits = np.flatnonzero(gone)
        return last if not hits.size else first + int(hits[0])

    # --- El recorrido -------------------------------------------------------

    def _walk(self, regimes: tuple[Regime, ...]) -> tuple[Entry, ...]:
        """Vela de M15 a vela de M15: se resuelve, se llena y se arma. En ese orden.

        El orden importa y es el de la vida real: primero lo que ya estaba en el
        mercado —la posición abierta y el límite puesto—, y sólo al CIERRE de la
        vela se decide si se pone uno nuevo. Así ningún límite se arma con lo que
        hizo la vela que iba a llenarlo.

        En la última vela de la semana se cierra el mercado y **no queda nada
        vivo**: la posición se cierra al precio de esa vela y el límite se quita.
        Va después de resolver y de llenar, porque el stop y el objetivo mandan
        sobre el reloj: si esa misma vela tocó el stop, la operación acabó en el
        stop y no en el viernes.
        """
        total = len(self._fine)
        seeking = self._seeking(regimes, total)
        hourly = self._hourly_at(total)
        candidates: dict[int, tuple[_Candidate, ...]] = {}

        found: list[Entry] = []
        open_at: int | None = None
        order: _Order | None = None
        traded: set[int] = set()

        for index in range(total):
            high, low = float(self._fine.high[index]), float(self._fine.low[index])
            if open_at is not None:
                closed = _resolve(found[open_at], high, low)
                if closed is not None:
                    outcome, price = closed
                    found[open_at] = _closed(
                        found[open_at], self._fine.at(index), price, outcome
                    )
                    open_at = None
            elif order is not None and index > order.armed and _fills(order, high, low):
                entry = self._record(order, len(found) + 1, filled=self._fine.at(index))
                traded.add(entry.h1_id)
                # La misma vela que llena puede llevarse la operación por delante:
                # se resuelve aquí, con el stop por delante del objetivo, que es
                # la lectura prudente.
                closed = _resolve(entry, high, low)
                if closed is None:
                    open_at = len(found)
                else:
                    outcome, price = closed
                    entry = _closed(entry, self._fine.at(index), price, outcome)
                found.append(entry)
                order = None

            if bool(self._week_close[index]):
                if open_at is not None:
                    found[open_at] = _closed(
                        found[open_at],
                        self._fine.at(index),
                        float(self._fine.close[index]),
                        TradeOutcome.CIERRE_SEMANAL,
                    )
                    open_at = None
                if order is not None:
                    found.append(
                        self._record(
                            order,
                            len(found) + 1,
                            cancelled=self._fine.at(index),
                            why=OrderEnd.CIERRE_SEMANAL,
                        )
                    )
                    order = None

            # Cierre de la vela: se quita el límite que ya no vale y se pone el
            # de esta vela, si toca.
            wanted = int(seeking[index])
            if order is not None:
                why = _cancels(order, index, wanted, hourly)
                if why is not None:
                    found.append(
                        self._record(
                            order, len(found) + 1, cancelled=self._fine.at(index), why=why
                        )
                    )
                    order = None

            if (
                order is None
                and open_at is None
                and wanted >= 0
                and hourly[index] >= 0
                and bool(self._in_window[index])
                # Con el mercado ya cerrado no se arma nada: sería dejar una
                # orden puesta el fin de semana justo después de haberla quitado.
                and not bool(self._week_close[index])
            ):
                position = int(hourly[index])
                zoned = self._h1_items[position]
                if zoned.id_num not in traded:
                    if position not in candidates:
                        candidates[position] = self._candidates_of(position)
                    picked = self._pick(
                        candidates[position],
                        index,
                        regimes[wanted].direction,
                        float(self._fine.close[index]),
                    )
                    if picked is not None:
                        order = _Order(
                            candidate=picked,
                            levels=self._levels(picked),
                            armed=index,
                            regime=wanted,
                            h4_id=regimes[wanted].h4_id,
                        )

        if order is not None:
            # El histórico se acabó con el límite puesto: se dibuja igual, con su
            # motivo, para que no parezca que alguien lo quitó.
            found.append(
                self._record(
                    order,
                    len(found) + 1,
                    cancelled=self._fine.at(total - 1),
                    why=OrderEnd.FIN_HISTORICO,
                )
            )

        found.sort(key=lambda item: (item.ts_armed, item.seq))
        return tuple(_renumbered(found))

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
        entry, stop, target = order.levels
        candidate = order.candidate
        return Entry(
            seq=seq,
            direction=candidate.direction,
            form=candidate.form,
            pattern=candidate.pattern.kind,
            h4_id=order.h4_id,
            h1_id=self._h1_items[candidate.h1_index].id_num,
            zone_kind=candidate.zone.kind,
            zone_low=candidate.zone.low,
            zone_high=candidate.zone.high,
            pattern_low=candidate.pattern.low,
            pattern_high=candidate.pattern.high,
            ts_pattern=self._fine.at(candidate.pattern.index_origin),
            ts_pattern_known=self._fine.at(candidate.pattern.index_known),
            ts_armed=self._fine.at(order.armed),
            entry=entry,
            stop=stop,
            target=target,
            ts_filled=filled,
            outcome=None if filled is None else TradeOutcome.ABIERTA,
            cancelled_by=why,
            ts_cancelled=cancelled,
        )

    def _pick(
        self,
        candidates: tuple[_Candidate, ...],
        index: int,
        direction: ImpulseDirection | None,
        close: float,
    ) -> _Candidate | None:
        """El patrón más reciente que sirve en esta vela, o `None`.

        Del más nuevo al más viejo: "lo más cercano que hay ahí". Tiene que estar
        vivo, ir en la dirección que se busca y quedar **al otro lado del
        precio**, porque un límite se pone donde el precio todavía no está.
        """
        if direction is None:
            return None
        for candidate in reversed(candidates):
            if candidate.direction is not direction:
                continue
            if not candidate.since <= index <= candidate.until:
                continue
            entry = self._levels(candidate)[0]
            if direction is ImpulseDirection.BAJISTA and entry <= close:
                continue
            if direction is ImpulseDirection.ALCISTA and entry >= close:
                continue
            return candidate
        return None

    def _limit_price(
        self, pattern: PricePattern, form: EntryForm, direction: ImpulseDirection
    ) -> float:
        """Dónde se pone el límite.

        Por defecto, el borde **cercano** del patrón: el primero que el precio
        encuentra al volver. La excepción es el **rechazo del UL afinado con un
        OB**, donde el límite sale del cierre de la vela que crea el OB —la que
        se va— separado la holgura de la config, por delante del precio.

        La razón es la operación nº 855: el OB sobresalía por encima del UL, su
        borde cercano quedaba pegado al stop y el límite acababa en un sitio al
        que el precio no volvía. El rechazo se vende en cuanto el precio se
        recupera un poco de la vela que lo tiró, no arriba del bloque entero.
        """
        if form is not EntryForm.RECHAZO_UL or pattern.kind is not PatternKind.ORDER_BLOCK:
            return pattern.near
        close = float(self._fine.close[pattern.index_known])
        offset = self._config.rejection_offset
        return close + offset if direction is ImpulseDirection.BAJISTA else close - offset

    def _stop_price(
        self, pattern: PricePattern, form: EntryForm, zone: Zone
    ) -> float:
        """Dónde va el stop.

        Por defecto, el borde **exterior** de la zona de H1: el sitio en el que
        el setup deja de existir. La excepción es el **rechazo del UL afinado
        con un OB**, donde va al borde **interior** —donde arranca la mecha, que
        es la línea del extremo del ID de H1—.

        Lo pidió el propietario: en ese setup se baja a M15 a esperar el OB, y
        para entonces la punta de la mecha del UL queda tan arriba que el riesgo
        es casi todo mecha vieja. El borde interior es lo que hay que perder para
        que el rechazo deje de ser un rechazo, y está mucho más cerca.
        """
        if form is EntryForm.RECHAZO_UL and pattern.kind is PatternKind.ORDER_BLOCK:
            return zone.inner
        return zone.outer

    def _levels(self, candidate: _Candidate) -> tuple[float, float, float]:
        """Entrada, stop y objetivo. El objetivo es siempre 1:3.

        La entrada la afina M15 —ver `_limit_price`— y el stop lo pone la
        ESTRUCTURA —ver `_stop_price`—: un borde de la zona de H1 en la que se
        está buscando, que es donde el setup deja de existir. Es lo que dijo el
        propietario para el rechazo —"el sitio más cercano, el UL de H1"— y vale
        igual para la zona en contra: atravesada entera, ahí no había nada.

        Ponerlo en el borde del patrón daría stops de céntimos —un FVG de M15
        mide a veces dos velas de nada— y con eso no se puede auditar un dibujo.
        """
        entry = self._limit_price(
            candidate.pattern, candidate.form, candidate.direction
        )
        stop = self._stop_price(
            candidate.pattern, candidate.form, candidate.zone
        )
        risk = abs(entry - stop)
        step = risk * self._config.risk_reward
        target = (
            entry - step
            if candidate.direction is ImpulseDirection.BAJISTA
            else entry + step
        )
        return entry, stop, target

    def _seeking(self, regimes: tuple[Regime, ...], total: int) -> np.ndarray:
        """Por cada vela de M15, el tramo de régimen vigente en su cierre, o `-1`."""
        at = np.full(total, -1, dtype=np.int64)
        for position, regime in enumerate(regimes):
            first = _bar_of(self._fine, regime.start)
            last = total - 1 if regime.end is None else _bar_of(self._fine, regime.end)
            if last < first:
                continue
            at[max(first, 0) : last + 1] = position
        return at

    def _hourly_at(self, total: int) -> np.ndarray:
        """Por cada vela de M15, el ID de H1 vigente y ya conocido, o `-1`."""
        at = np.full(total, -1, dtype=np.int64)
        for position, zoned in enumerate(self._h1_items):
            first = self._known_at(self._h1_close, zoned.index_constitution)
            last = (
                total - 1
                if zoned.index_end is None
                else self._known_at(self._h1_close, zoned.index_end)
            )
            if first is None or last is None or last < first:
                continue
            at[first : last + 1] = position
        return at

    # --- Utilidades ---------------------------------------------------------

    def _touches(self, low: float, high: float, first: int, last: int) -> int | None:
        """Primera vela de `[first, last]` cuyo rango corta ese tramo de precio."""
        if last < first:
            return None
        window = slice(first, last + 1)
        hits = np.flatnonzero(
            (self._fine.high[window] >= low) & (self._fine.low[window] <= high)
        )
        return first + int(hits[0]) if hits.size else None

    def _reaches(
        self, level: float, direction: ImpulseDirection, first: int, last: int
    ) -> int | None:
        """Primera vela de `[first, last]` que llega al nivel en esa dirección."""
        if last < first:
            return None
        window = slice(first, last + 1)
        if direction is ImpulseDirection.ALCISTA:
            hits = np.flatnonzero(self._fine.high[window] >= level)
        else:
            hits = np.flatnonzero(self._fine.low[window] <= level)
        return first + int(hits[0]) if hits.size else None

    @staticmethod
    def _known_at(closing: np.ndarray, index: int) -> int | None:
        """La vela fina en cuyo cierre se supo lo que pasó en la vela `index`."""
        if not 0 <= index < len(closing):
            return None
        value = int(closing[index])
        return None if value < 0 else value


# --- Aritmética de la operación ---------------------------------------------


def _resolve(entry: Entry, high: float, low: float) -> tuple[TradeOutcome, float] | None:
    """Cómo se cierra la operación con esta vela, o `None` si sigue abierta.

    **El stop manda**: cuando una misma vela de M15 alcanza los dos, no se sabe
    en qué orden ocurrió dentro del cuarto de hora y se cuenta la mala.
    """
    if entry.direction is ImpulseDirection.BAJISTA:
        if high >= entry.stop:
            return TradeOutcome.STOP, entry.stop
        if low <= entry.target:
            return TradeOutcome.OBJETIVO, entry.target
        return None
    if low <= entry.stop:
        return TradeOutcome.STOP, entry.stop
    if high >= entry.target:
        return TradeOutcome.OBJETIVO, entry.target
    return None


def _fills(order: _Order, high: float, low: float) -> bool:
    """`True` si esta vela llena el límite: el precio ha llegado hasta él."""
    entry = order.levels[0]
    if order.candidate.direction is ImpulseDirection.BAJISTA:
        return high >= entry
    return low <= entry


def _closed(
    entry: Entry, moment: datetime, price: float, outcome: TradeOutcome
) -> Entry:
    return Entry(
        seq=entry.seq,
        direction=entry.direction,
        form=entry.form,
        pattern=entry.pattern,
        h4_id=entry.h4_id,
        h1_id=entry.h1_id,
        zone_kind=entry.zone_kind,
        zone_low=entry.zone_low,
        zone_high=entry.zone_high,
        pattern_low=entry.pattern_low,
        pattern_high=entry.pattern_high,
        ts_pattern=entry.ts_pattern,
        ts_pattern_known=entry.ts_pattern_known,
        ts_armed=entry.ts_armed,
        entry=entry.entry,
        stop=entry.stop,
        target=entry.target,
        ts_filled=entry.ts_filled,
        ts_closed=moment,
        exit_price=price,
        outcome=outcome,
    )


def _renumbered(entries: list[Entry]) -> list[Entry]:
    """Los números en el orden en que se pusieron los límites, sin huecos."""
    return [
        Entry(
            seq=position + 1,
            direction=item.direction,
            form=item.form,
            pattern=item.pattern,
            h4_id=item.h4_id,
            h1_id=item.h1_id,
            zone_kind=item.zone_kind,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            pattern_low=item.pattern_low,
            pattern_high=item.pattern_high,
            ts_pattern=item.ts_pattern,
            ts_pattern_known=item.ts_pattern_known,
            ts_armed=item.ts_armed,
            entry=item.entry,
            stop=item.stop,
            target=item.target,
            ts_filled=item.ts_filled,
            ts_closed=item.ts_closed,
            exit_price=item.exit_price,
            outcome=item.outcome,
            cancelled_by=item.cancelled_by,
            ts_cancelled=item.ts_cancelled,
        )
        for position, item in enumerate(entries)
    ]


def _cancels(
    order: _Order, index: int, wanted: int, hourly: np.ndarray
) -> OrderEnd | None:
    """Por qué se quita este límite en esta vela, o `None` si sigue puesto.

    Las dos razones del propietario: que muera el ID de H1 del que cuelga —en
    cuanto la vela deja de estar bajo ese ID, sea por muerte o por limbo— y que
    cambie el régimen de H4, porque entonces ya no se busca lo que se buscaba.
    """
    if int(hourly[index]) != order.candidate.h1_index:
        return OrderEnd.MUERTE_ID_H1
    if wanted != order.regime:
        return OrderEnd.FIN_REGIMEN
    return None


def _bar_of(series: CandleSeries, moment: datetime) -> int:
    """Posición de la vela que **contiene** ese instante."""
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


def _finest(run: ImpulseRun, fallback: CandleSeries) -> CandleSeries:
    """La serie de vela más corta de la corrida —M15— o la del ID si no hay otra."""
    charts = [name for name in run.chart_bars if name in TIMEFRAME_MINUTES]
    if not charts:
        return fallback
    finest = min(charts, key=lambda name: TIMEFRAME_MINUTES[name])
    series = CandleSeries.of(run.chart_bars[finest])
    return series if len(series) > len(fallback) else fallback


def _risky(entry: float, stop: float, direction: ImpulseDirection) -> bool:
    """`True` si el stop deja riesgo que medir: está al otro lado de la entrada.

    En una venta el stop va por encima y en una compra por debajo. Con los dos
    en el mismo precio no hay operación, hay una división por cero.
    """
    if direction is ImpulseDirection.BAJISTA:
        return stop > entry
    return stop < entry


def _beyond(price: float, outer: float, direction: ImpulseDirection) -> bool:
    """El precio ha dejado la zona atrás por su borde exterior."""
    if direction is ImpulseDirection.ALCISTA:
        return price > outer
    return price < outer


__all__ = [
    "RISK_REWARD",
    "EntriesConfig",
    "EntriesRun",
    "Entry",
    "EntryForm",
    "OrderEnd",
    "Regime",
    "RegimeEdge",
    "RegimeKind",
    "TradeOutcome",
    "detect_entries",
]
