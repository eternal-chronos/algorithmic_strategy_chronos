"""Zonas UL y OB de un impulso dominante (fase 2.0). **Sólo detección.**

El módulo 1 traza la estructura por **cuerpos**: `max(open, close)` y
`min(open, close)`. Estas dos zonas son lo contrario, un asunto de **mechas**, y
por eso viven en su propio fichero con su propia lectura de la vela: el
`BodyBar` del detector no tiene `high` ni `low` a propósito y no se le añaden.

Nada de aquí entra en la detección de impulsos. El detector no importa este
módulo, así que ninguna regla del módulo 1 puede leer una zona ni por descuido.
En la fase 2.1 las zonas pasarán a decidir la vida y la muerte de los impulsos;
hasta que el propietario audite lo que se dibuja aquí, no deciden nada.

Las dos zonas, tal como las define el propietario:

**UL (último)** — el extremo del ID, sobre la vela que fija `precio_extremo`.
Va del **borde del cuerpo** a la **punta de la mecha**, así que ocupa sólo el
tramo de mecha y nunca cubre el cuerpo. Su borde interior es exactamente la
línea del extremo del ID. Si la vela inmediatamente posterior tiene la mecha más
extrema en la misma dirección, la zona se estira hasta ella: **una vela de
margen, no más**.

**OB (order block)** — la vela donde arranca la pierna, la que fija
`precio_ancla`. Va de `low` a `high`: a diferencia del UL, **sí** cubre el
cuerpo. No existe hasta que una vela posterior **del color del impulso** la
supera incluyendo mecha; antes de eso el ID no tiene OB, que es un estado
legítimo y se registra.

`interior` y `exterior` significan lo mismo en las dos: el borde **interior** es
el que un precio que sale del rango encuentra primero, y el **exterior** el que
tiene que cruzar para dejar la zona atrás. En un ID alcista el UL se recorre
hacia arriba (cuerpo -> mecha) y el OB hacia abajo (`high` -> `low`), porque la
rotura a favor sube y la rotura en contra baja. Es la lectura que necesita la
fase 2.1 y aquí sólo se calcula.
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
    #: Vela donde arranca la pierna, entera y con mechas.
    ORDER_BLOCK = "OB"


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
    #: Vela que define la zona: la del extremo en el UL, la del ancla en el OB.
    index_defining: int
    ts_defining: datetime
    #: Color del cuerpo de esa vela. Va aquí y no se re-deriva más tarde por la
    #: misma razón que en la fase 1: quien audite no tiene que volver al OHLC, y
    #: es lo que permite contar los OB sobre doji sin buscar la vela otra vez.
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
    #: OB: la vela del color del impulso que lo confirmó. Sin ella no hay OB.
    index_confirmation: int | None = None
    ts_confirmation: datetime | None = None

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


def order_block_zone(
    series: CandleSeries,
    *,
    id_num: int,
    timeframe: str,
    direction: ImpulseDirection,
    index_anchor: int,
    ts_constitution: datetime,
    index_end: int | None,
) -> Zone | None:
    """Zona OB del ID, o `None` si nunca llegó a confirmarse.

    Devolver `None` no es un fallo: un ID sin OB confirmado es un estado legítimo
    y en la fase 2.1 esos impulsos se romperán por línea. Por eso no se sustituye
    por una zona vacía ni por la vela sin confirmar, que son cosas distintas.

    La confirmación es la primera vela **posterior** a la del OB que cumple dos
    cosas a la vez: su cuerpo es del color del impulso y su mecha supera la de la
    vela del OB. El doji no confirma —§2.2 lo declara neutro en todo el módulo— y
    la búsqueda termina donde muere el ID: una vela que superase el nivel con el
    ID ya roto no confirma nada suyo.
    """
    last = index_end if index_end is not None else len(series) - 1
    confirmation = _first_confirmation(series, direction, index_anchor, last)
    if confirmation is None:
        return None

    ts_confirmation = series.at(confirmation)
    # `high` y `low` de la vela entera. Cuál es interior y cuál exterior lo decide
    # el sentido en que se recorre: la rotura en contra de un ID alcista baja, así
    # que encuentra primero el `high` y tiene que cruzar el `low`.
    inner = series.wick_tip_towards(index_anchor, direction)
    outer = series.wick_tip_towards(index_anchor, direction.opposite())

    return Zone(
        kind=ZoneKind.ORDER_BLOCK,
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        index_defining=index_anchor,
        ts_defining=series.at(index_anchor),
        defining_body=series.direction_of(index_anchor),
        inner=inner,
        outer=outer,
        ts_outer_known=series.at(index_anchor),
        # Las dos condiciones a la vez: el ID constituido y el OB confirmado. La
        # confirmación suele caer dentro de la pierna, antes de la constitución.
        ts_birth=max(ts_constitution, ts_confirmation),
        index_confirmation=confirmation,
        ts_confirmation=ts_confirmation,
    )


def _first_confirmation(
    series: CandleSeries, direction: ImpulseDirection, index_anchor: int, last: int
) -> int | None:
    """Primera vela cronológica que confirma el OB. Con varias candidatas, la primera."""
    colour = (
        BodyDirection.BULLISH if direction is ImpulseDirection.ALCISTA else BodyDirection.BEARISH
    )
    level = series.wick_tip_towards(index_anchor, direction)
    for index in range(index_anchor + 1, min(last, len(series) - 1) + 1):
        if series.direction_of(index) is not colour:
            continue
        if _is_beyond(series.wick_tip_towards(index, direction), level, direction):
            return index
    return None


def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
    """"Más allá" es estricto, igual que en la rotura del módulo 1."""
    return price > level if direction is ImpulseDirection.ALCISTA else price < level


# --- Consulta ---------------------------------------------------------------


class ZoneBook:
    """Las zonas de una temporalidad, consultables sin poder mirar al futuro.

    Es la única puerta por la que la fase 2.1 leerá zonas, y por eso la garantía
    va aquí y no en quien pregunte: pedir una zona antes de que nazca —o un OB
    antes de que se confirme— lanza `LookaheadError` en vez de devolver algo.
    """

    def __init__(self, timeframe: str, zones: tuple[Zone, ...] = ()) -> None:
        self._timeframe = timeframe
        self._zones = tuple(sorted(zones, key=lambda zone: (zone.ts_birth, zone.id_num, zone.kind.value)))
        self._by_key: dict[tuple[int, ZoneKind], Zone] = {
            (zone.id_num, zone.kind): zone for zone in self._zones
        }
        #: ID cuyo OB se buscó y no llegó a confirmarse. Se guardan para poder
        #: distinguir "no lo he calculado" de "no existe", que en la fase 2.1 son
        #: dos cosas muy distintas: el segundo se rompe por línea.
        self._without_order_block: set[int] = set()

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def zones(self) -> tuple[Zone, ...]:
        return self._zones

    def record_missing_order_block(self, id_num: int) -> None:
        self._without_order_block.add(id_num)

    @property
    def without_order_block(self) -> frozenset[int]:
        return frozenset(self._without_order_block)

    def of(self, id_num: int, kind: ZoneKind, *, at: datetime) -> Zone:
        """La zona de un ID, si a esa hora existía."""
        _require_aware(at)
        zone = self._by_key.get((id_num, kind))
        if zone is None:
            if kind is ZoneKind.ORDER_BLOCK and id_num in self._without_order_block:
                raise LookaheadError(
                    f"[{self._timeframe}] El ID {id_num} no tiene OB: ninguna vela del color "
                    "del impulso llegó a superar la vela del ancla mientras estuvo vigente"
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
    "last_zone",
    "order_block_zone",
]
