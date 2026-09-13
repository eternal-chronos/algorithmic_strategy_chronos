"""Zonas UL, PUL y APUL de un impulso dominante. **Sólo geometría.**

El módulo 1 traza la estructura por **cuerpos**: `max(open, close)` y
`min(open, close)`. Estas zonas son lo contrario, un asunto de **mechas**, y
por eso viven en su propio fichero con su propia lectura de la vela: el
`BodyBar` del detector no tiene `high` ni `low` a propósito y no se le añaden.

Nada de aquí entra en la detección de impulsos. El detector no importa este
módulo, así que ninguna regla del módulo 1 puede leer una zona ni por descuido:
las velas de las zonas se las pasa el impulso ya apuntadas y quien lee la mecha
es siempre `ZoneBreakLevels`.

Las zonas, tal como las define el propietario. Un ID tiene siempre UL en el lado
a favor, y en el lado en contra **una** de las otras dos, nunca las dos: el PUL,
o el APUL.

**Las tres son tramos de MECHA.** Una zona va del **borde del cuerpo** a la
**punta de la mecha** de la vela de un extremo, y nunca cubre el cuerpo. Lo único
que cambia entre ellas es de qué extremo salen y desde qué lado se leen.

**UL (último)** — el extremo del ID, sobre la vela que fija `precio_extremo`.
Su borde interior es exactamente la línea del extremo del ID. Si la vela
inmediatamente posterior tiene la mecha más extrema en la misma dirección, la
zona se estira hasta ella: **una vela de margen, no más**. El UL se marca al
constituirse el ID y **no se remarca** mientras vive, aunque el extremo se
estire: es el nivel contra el que se juzga su rotura a favor y no puede moverse
bajo los pies del ID vivo.

**PUL (penúltimo)** — el UL del ID **inmediatamente anterior** cuando aquel ID
iba **en el mismo sentido** que éste: murió por rotura a favor, éste nació más
allá y su extremo quedó por detrás, que es el lado en contra. La zona es esa
mecha, con interior la punta y exterior el borde del cuerpo.

**APUL (antes penúltimo)** — el nivel en contra cuando el PUL no existe, y son
tres casos:

- **el ID anterior iba al revés y su extremo es el ancla de éste.** No es un
  nivel al que volver, así que el nivel en contra es el que aquel ID llevaba: se
  **hereda su zona en contra** tal cual, los mismos dos precios, leídos desde
  este lado;
- **el ID anterior iba al revés pero su extremo quedó por detrás del ancla.**
  Aquel ID no murió de un giro sino por rotura a favor, y el giro lo trajo
  después una constitución abortada, así que el ancla de éste se fijó más allá de
  aquel extremo: su **UL** sigue siendo el nivel al que volver, exactamente como
  un PUL pero de un ID que iba al revés;
- **constitución abortada.** El ID anterior va en el mismo sentido pero no por
  continuación: en medio hubo un ID contrario que iba a constituirse y una vela
  lo mató antes de que naciera. El nivel se va a buscar donde estaba —corriendo
  la máquina dentro del retroceso del ID anterior— y sale del extremo del último
  ID interior contrario. Quién lo busca es el detector; aquí sólo se dibuja la
  vela que trae.

**La punta se estira a toda la vida del ID que la fijó.** El UL de un ID vivo
mide sólo su vela del extremo (más la de margen), pero cuando ese ID ha muerto y
su zona pasa a ser el PUL —o el APUL— del siguiente, la punta se lleva
la **mecha más lejana que el precio alcanzó mientras aquel ID estuvo vivo**:
desde el arranque de su pierna hasta la vela **anterior** a la que lo rompió. Es
mecha que el precio ya visitó con aquel ID en pie y que el borde de una sola
vela dejaba fuera. La vela que rompe no cuenta: es la que se llevó el nivel por
delante, no una mecha del ID. El borde del cuerpo no se mueve nunca.

Las velas de las que sale una zona en contra son todas anteriores a la
constitución del ID que la lleva, así que ni se confirma ni mira al futuro.

`interior` y `exterior` significan lo mismo en las tres: el borde **interior** es
el que un precio que sale del rango encuentra primero, y el **exterior** el que
tiene que cruzar para dejar la zona atrás. En un ID alcista el UL se recorre
hacia arriba (cuerpo -> mecha) y la zona en contra hacia abajo, porque la rotura
a favor sube y la rotura en contra baja. Qué borde es cada uno depende de hacia
dónde miraba la mecha: si apunta en el sentido de este ID —el PUL— el precio
encuentra antes la punta; si apunta al contrario —el APUL de un ID interior— el
precio encuentra antes el borde del cuerpo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError


class ZoneKind(StrEnum):
    """Las dos zonas de la fase 2.0. Los valores salen a CSV y al informe."""

    #: Extremo del ID: el tramo de mecha que va del cuerpo a la punta.
    LAST = "UL"
    #: El UL del ID **inmediatamente anterior**, cuando aquél iba en el mismo
    #: sentido que éste y su extremo quedó por detrás: la mecha vieja tal cual,
    #: con la punta estirada a lo que alcanzó aquel ID en toda su vida.
    PENULTIMATE = "PUL"
    #: El nivel en contra cuando no hay PUL: la zona en contra del ID anterior
    #: **heredada** —porque aquél iba al revés y su extremo es el ancla de éste—,
    #: el **UL** de ese mismo ID cuando su extremo sí quedó por detrás del ancla,
    #: o el extremo del último ID **interior** contrario del retroceso anterior
    #: cuando en medio se abortó una constitución. Sustituye al PUL, no lo
    #: acompaña.
    ANTE_PENULTIMATE = "APUL"


@dataclass(frozen=True, slots=True)
class CandleSeries:
    """Las velas de una temporalidad **con mecha**.

    El contrato es el mismo que el de las barras que entran a `application/`:
    índice UTC monótono sin duplicados y sin NaN. Se valida una vez al construir
    y no en cada función.
    """

    timestamps: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __post_init__(self) -> None:
        sizes = {len(self.timestamps), self.open.size, self.high.size, self.low.size, self.close.size}
        if len(sizes) != 1:
            raise StructureError(f"Las series de la vela no miden lo mismo: {sorted(sizes)}")
        if len(self.timestamps) and self.timestamps.tz is None:
            raise StructureError("El índice de las velas no es tz-aware")

    def __len__(self) -> int:
        return len(self.timestamps)

    @classmethod
    def of(cls, frame: pd.DataFrame) -> CandleSeries:
        """Construye la serie desde las barras ya agregadas de una temporalidad."""
        return cls(
            timestamps=pd.DatetimeIndex(frame.index),
            open=frame["open"].to_numpy(dtype=float),
            high=frame["high"].to_numpy(dtype=float),
            low=frame["low"].to_numpy(dtype=float),
            close=frame["close"].to_numpy(dtype=float),
        )

    def at(self, index: int) -> datetime:
        if not 0 <= index < len(self):
            raise StructureError(f"Índice {index} fuera de la serie ({len(self)} velas)")
        return self.timestamps[index].to_pydatetime()

    def direction_of(self, index: int) -> BodyDirection:
        """Color del cuerpo. La igualdad es exacta: no hay tolerancia inventada."""
        if self.close[index] > self.open[index]:
            return BodyDirection.BULLISH
        if self.close[index] < self.open[index]:
            return BodyDirection.BEARISH
        return BodyDirection.DOJI

    def body_edge_towards(self, index: int, direction: ImpulseDirection) -> float:
        """Borde del cuerpo más avanzado en `direction`: la línea del ID."""
        if direction is ImpulseDirection.ALCISTA:
            return float(max(self.open[index], self.close[index]))
        return float(min(self.open[index], self.close[index]))

    def wick_tip_towards(self, index: int, direction: ImpulseDirection) -> float:
        """Punta de la mecha en `direction`."""
        return float(
            self.high[index] if direction is ImpulseDirection.ALCISTA else self.low[index]
        )


@dataclass(frozen=True, slots=True)
class Zone:
    """Una zona de precio con identidad: de qué ID es y de qué tipo.

    Los dos bordes van con la marca de tiempo de la vela que los fija, igual que
    el ancla y el extremo del impulso: quien dibuje o audite no tiene que
    reconstruir buscando qué precio coincide con cuál, que con empates no da una
    respuesta única.
    """

    kind: ZoneKind
    id_num: int
    timeframe: str
    direction: ImpulseDirection
    #: Vela que define la zona: la del extremo del ID en el UL, la del extremo
    #: del ID anterior en el PUL.
    index_defining: int
    ts_defining: datetime
    #: Color del cuerpo de esa vela. Va aquí y no se re-deriva más tarde por la
    #: misma razón que en la fase 1: quien audite no tiene que volver al OHLC, y
    #: es lo que permite contar los PUL sobre doji sin buscar la vela otra vez.
    defining_body: BodyDirection
    #: Borde que un precio saliente encuentra primero.
    inner: float
    #: Borde que hay que cruzar para dejar la zona atrás.
    outer: float
    #: Cierre de la vela a partir del cual el borde exterior es legible. Es la
    #: propia vela definitoria salvo en un UL extendido, donde manda la de margen.
    ts_outer_known: datetime
    #: Instante en que la zona empieza a existir. Nunca antes de la constitución
    #: del ID: durante el limbo no hay ninguna zona.
    ts_birth: datetime
    #: UL: la vela siguiente tenía la mecha más extrema y la zona se estiró.
    extended: bool = False

    @property
    def low(self) -> float:
        return min(self.inner, self.outer)

    @property
    def high(self) -> float:
        return max(self.inner, self.outer)

    @property
    def height(self) -> float:
        """Altura en USD. Puede ser exactamente cero; no se corrige en silencio."""
        return self.high - self.low

    @property
    def is_flat(self) -> bool:
        """Zona de altura cero: la vela no tenía mecha en esa dirección.

        Se conserva como zona degenerada —los dos bordes en el mismo precio— en
        vez de descartarla. Descartarla diría que el ID no tiene UL, y sí lo
        tiene: lo que no tiene es mecha. El informe las cuenta aparte.
        """
        return self.outer == self.inner

    def contains(self, price: float) -> bool:
        """`True` si el precio cae dentro de la zona, bordes incluidos.

        Es lo único que la fase 2.0 necesita preguntarle a una zona: si un cierre
        se quedó dentro. "Atravesarla entera" —cerrar más allá del borde
        exterior— es la regla de la fase 2.1 y no se escribe aquí: la escribirá
        quien la vaya a usar, cuando el propietario haya auditado esto.
        """
        return self.low <= price <= self.high

    def borders_at(self, timestamp: datetime) -> tuple[float, float]:
        """Los dos bordes, si a esa hora se conocían.

        Preguntar por una zona antes de que nazca es mirar al futuro: durante el
        limbo la zona no existe, y el borde exterior de un UL extendido no se
        conoce hasta que cierra la vela de margen.
        """
        _require_aware(timestamp)
        if timestamp < self.ts_birth:
            raise LookaheadError(
                f"[{self.timeframe}] La zona {self.kind.value} del ID {self.id_num} no existe "
                f"en {timestamp}: nace en {self.ts_birth}"
            )
        return self.inner, self.outer_at(timestamp)

    def outer_at(self, timestamp: datetime) -> float:
        """Borde exterior, si a esa hora ya lo había fijado su vela."""
        _require_aware(timestamp)
        if timestamp < self.ts_outer_known:
            raise LookaheadError(
                f"[{self.timeframe}] El borde exterior de la zona {self.kind.value} del ID "
                f"{self.id_num} lo fija la vela de {self.ts_outer_known}"
                + (" (margen del UL)" if self.extended else "")
                + f": en {timestamp} todavía no había cerrado"
            )
        return self.outer


def _require_aware(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise StructureError(f"La marca de tiempo {timestamp!r} no es tz-aware")


# --- Construcción -----------------------------------------------------------


def last_zone(
    series: CandleSeries,
    *,
    id_num: int,
    timeframe: str,
    direction: ImpulseDirection,
    index_extreme: int,
    ts_constitution: datetime,
) -> Zone:
    """Zona UL del ID: el tramo de mecha de la vela que fijó el extremo.

    El borde interior es la línea del extremo y no se mueve nunca. El exterior es
    la punta de la mecha, salvo que la vela **inmediatamente** posterior llegue
    más lejos en la misma dirección: entonces se estira hasta ella y ahí se para.
    Una vela de margen, no más.

    La comparación es estricta, como la del extremo en el detector: con empate
    manda la primera vela que llegó al nivel y no hay extensión.
    """
    inner = series.body_edge_towards(index_extreme, direction)
    outer = series.wick_tip_towards(index_extreme, direction)
    ts_outer = series.at(index_extreme)

    extended = False
    margin = index_extreme + 1
    if margin < len(series):
        reach = series.wick_tip_towards(margin, direction)
        if _is_beyond(reach, outer, direction):
            outer, ts_outer, extended = reach, series.at(margin), True

    return Zone(
        kind=ZoneKind.LAST,
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        index_defining=index_extreme,
        ts_defining=series.at(index_extreme),
        defining_body=series.direction_of(index_extreme),
        inner=inner,
        outer=outer,
        ts_outer_known=ts_outer,
        # §2: la zona nace cuando se constituye el ID. Durante el limbo, nada.
        ts_birth=ts_constitution,
        extended=extended,
    )


def against_zone(
    series: CandleSeries,
    *,
    kind: ZoneKind,
    id_num: int,
    timeframe: str,
    direction: ImpulseDirection,
    index_body: int | None,
    tip_window: tuple[int, int] | None,
    zone_direction: ImpulseDirection | None,
    ts_constitution: datetime,
) -> Zone | None:
    """Zona del lado en contra —PUL o APUL—, o `None` si el ID no lleva ninguna.

    Las dos son lo mismo geométricamente: el **UL de un ID anterior**, del borde
    de su cuerpo a la punta de su mecha. Lo que las distingue es de qué ID salen
    —el inmediatamente anterior, el que le prestó su propia zona o el que se
    quedó dentro de un retroceso— y eso lo decide el detector, no esta función:
    aquí llegan ya la vela del cuerpo, la ventana en la que buscar la punta y
    hacia dónde miraba aquel extremo.

    `zone_direction` es el sentido del ID que fijó esa mecha, y es lo único que
    decide qué borde es cuál: si apunta en el sentido de este ID, el precio que
    vuelve en contra encuentra antes la **punta**; si apunta al contrario,
    encuentra antes el **borde del cuerpo**.

    Devolver `None` le pasa a los primeros ID de cada temporalidad, que no tienen
    ningún ID detrás del que sacar el nivel: ésos se rompen por línea en el lado
    en contra, que es un estado legítimo y no un fallo.

    No se estira a ninguna vela de margen: esa regla es la del extremo recién
    fijado. Lo que sí lleva es la mecha de toda la vida de aquel ID, que ya venía
    decidida en `tip_window`.
    """
    if index_body is None or zone_direction is None or tip_window is None:
        return None

    index_tip = furthest_wick_index(series, tip_window, zone_direction)
    inner, outer = against_edges(
        series,
        index_body=index_body,
        index_tip=index_tip,
        zone_direction=zone_direction,
        direction=direction,
    )
    # El borde exterior lo fija una vela u otra según hacia dónde mire la mecha,
    # y la garantía anti-lookahead se juzga contra ESA vela y no contra las dos.
    index_outer = index_body if zone_direction is direction else index_tip

    return Zone(
        kind=kind,
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        index_defining=index_body,
        ts_defining=series.at(index_body),
        defining_body=series.direction_of(index_body),
        inner=inner,
        outer=outer,
        ts_outer_known=series.at(index_outer),
        ts_birth=ts_constitution,
    )


def furthest_wick_index(
    series: CandleSeries, window: tuple[int, int], direction: ImpulseDirection
) -> int:
    """Vela de la mecha más lejana en `direction` dentro de `window`, inclusive.

    Es la punta de una zona en contra: el precio más allá del cuerpo al que llegó
    el ID que la fijó mientras estuvo vivo. La comparación es estricta, igual que
    la del extremo en el detector: con empate manda la primera vela que llegó al
    nivel.
    """
    first, last = window
    if not 0 <= first <= last < len(series):
        raise StructureError(
            f"Ventana de mecha {window} fuera de la serie ({len(series)} velas)"
        )
    best = first
    reach = series.wick_tip_towards(first, direction)
    for index in range(first + 1, last + 1):
        candidate = series.wick_tip_towards(index, direction)
        if _is_beyond(candidate, reach, direction):
            best, reach = index, candidate
    return best


def against_edges(
    series: CandleSeries,
    *,
    index_body: int,
    index_tip: int,
    zone_direction: ImpulseDirection,
    direction: ImpulseDirection,
) -> tuple[float, float]:
    """Los dos bordes de una zona en contra: interior primero, exterior después.

    Vive aquí y sólo aquí: lo usan la zona de la fase 2.0 y el nivel barra a
    barra de la 2.1, y con dos lecturas distintas de la misma mecha el dibujo y
    la rotura dirían cosas diferentes.
    """
    body = series.body_edge_towards(index_body, zone_direction)
    tip = series.wick_tip_towards(index_tip, zone_direction)
    if zone_direction is direction:
        # La mecha apunta hacia donde va este ID: el extremo viejo quedó por
        # detrás y lo primero que hay al volver es su punta.
        return tip, body
    # La mecha apunta al lado por el que este ID rompe en contra: lo primero que
    # hay al volver es el borde del cuerpo, y la punta es lo que hay que cruzar.
    return body, tip


def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
    """"Más allá" es estricto, igual que en la rotura del módulo 1."""
    return price > level if direction is ImpulseDirection.ALCISTA else price < level


# --- Consulta ---------------------------------------------------------------


class ZoneBook:
    """Las zonas de una temporalidad, consultables sin poder mirar al futuro.

    Es la única puerta por la que la fase 2.1 leerá zonas, y por eso la garantía
    va aquí y no en quien pregunte: pedir una zona antes de que nazca lanza
    `LookaheadError` en vez de devolver algo.
    """

    def __init__(self, timeframe: str, zones: tuple[Zone, ...] = ()) -> None:
        self._timeframe = timeframe
        self._zones = tuple(sorted(zones, key=lambda zone: (zone.ts_birth, zone.id_num, zone.kind.value)))
        self._by_key: dict[tuple[int, ZoneKind], Zone] = {
            (zone.id_num, zone.kind): zone for zone in self._zones
        }
        #: ID sin PUL: no había ID anterior del que sacarlo. Se guardan para
        #: poder distinguir "no lo he calculado" de "no existe", que en la fase
        #: 2.1 son dos cosas muy distintas: el segundo se rompe por línea.
        self._without_penultimate: set[int] = set()

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def zones(self) -> tuple[Zone, ...]:
        return self._zones

    def record_missing_penultimate(self, id_num: int) -> None:
        self._without_penultimate.add(id_num)

    @property
    def without_penultimate(self) -> frozenset[int]:
        return frozenset(self._without_penultimate)

    def of(self, id_num: int, kind: ZoneKind, *, at: datetime) -> Zone:
        """La zona de un ID, si a esa hora existía."""
        _require_aware(at)
        zone = self._by_key.get((id_num, kind))
        if zone is None:
            if kind is ZoneKind.PENULTIMATE and id_num in self._without_penultimate:
                raise LookaheadError(
                    f"[{self._timeframe}] El ID {id_num} no tiene PUL: no hay ID anterior "
                    "del que salga la vela del extremo penúltimo"
                )
            raise StructureError(
                f"[{self._timeframe}] No hay zona {kind.value} para el ID {id_num}"
            )
        zone.borders_at(at)  # la garantía vive en la zona; aquí sólo se invoca
        return zone

    def alive_at(self, timestamp: datetime) -> tuple[Zone, ...]:
        """Zonas ya nacidas en `timestamp`, en orden de nacimiento."""
        _require_aware(timestamp)
        return tuple(zone for zone in self._zones if zone.ts_birth <= timestamp)


__all__ = [
    "CandleSeries",
    "Zone",
    "ZoneBook",
    "ZoneKind",
    "against_edges",
    "against_zone",
    "furthest_wick_index",
    "last_zone",
]
