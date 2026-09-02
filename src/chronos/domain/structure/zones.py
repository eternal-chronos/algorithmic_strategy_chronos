"""Zonas UL y PUL de un impulso dominante (fase 2.0). **Sólo detección.**

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

**PUL (penúltimo)** — el extremo del ID **anterior**, sobre la misma vela que
fijaba su UL: cuando un ID muere y nace el siguiente, el UL viejo se convierte
en el PUL del nuevo. A diferencia del UL, que es el tramo de mecha, el PUL es el
**cuerpo** de esa vela: va de un borde del cuerpo al otro y no cubre ninguna
mecha. Las dos zonas son complementarias sobre la misma vela —el UL toma la
punta, el PUL toma la base— y juntas van del `open` a la punta de la mecha.
El primer ID del histórico no tiene ID anterior y por tanto no tiene PUL, que es
un estado legítimo y se registra.

`interior` y `exterior` significan lo mismo en las dos: el borde **interior** es
el que un precio que sale del rango encuentra primero, y el **exterior** el que
tiene que cruzar para dejar la zona atrás. En un ID alcista el UL se recorre
hacia arriba (cuerpo -> mecha) y el PUL hacia abajo (borde alto del cuerpo ->
borde bajo), porque la rotura a favor sube y la rotura en contra baja. Es la
lectura que necesita la fase 2.1 y aquí sólo se calcula.
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
    #: Extremo del ID anterior: el cuerpo de la vela que fijaba su UL.
    PENULTIMATE = "PUL"


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


def penultimate_zone(
    series: CandleSeries,
    *,
    id_num: int,
    timeframe: str,
    direction: ImpulseDirection,
    index_previous_extreme: int | None,
    ts_constitution: datetime,
) -> Zone | None:
    """Zona PUL del ID, o `None` si no hay ID anterior del que salga.

    La vela es la misma que fijó el extremo del ID anterior —la de su UL—, y de
    ella se toma el **cuerpo**: el borde que el precio encuentra primero al
    volver en contra es el interior, y el que hay que cruzar para dejar la zona
    atrás es el otro. En un ID bajista, cuyo PUL es la vela verde del máximo
    anterior, la rotura en contra sube: el interior es el borde bajo del cuerpo
    y el exterior el alto.

    Devolver `None` sólo le pasa al primer ID del histórico, que no tiene ID
    anterior. No es un fallo: ese impulso se rompe por línea en el lado en
    contra, y no hay ninguna zona detrás que lo sustituya.

    El PUL no espera a nada: su vela cerró antes de que naciera el ID, así que
    la zona existe desde la constitución.
    """
    if index_previous_extreme is None:
        return None

    # El cuerpo entero de la vela, sin mechas. Cuál de los dos bordes es interior
    # lo decide el sentido en que se recorre: la rotura en contra de un ID
    # alcista baja, así que encuentra primero el borde alto del cuerpo y tiene
    # que cruzar el bajo.
    inner = series.body_edge_towards(index_previous_extreme, direction)
    outer = series.body_edge_towards(index_previous_extreme, direction.opposite())

    return Zone(
        kind=ZoneKind.PENULTIMATE,
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        index_defining=index_previous_extreme,
        ts_defining=series.at(index_previous_extreme),
        defining_body=series.direction_of(index_previous_extreme),
        inner=inner,
        outer=outer,
        ts_outer_known=series.at(index_previous_extreme),
        ts_birth=ts_constitution,
    )


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
    "last_zone",
    "penultimate_zone",
]
