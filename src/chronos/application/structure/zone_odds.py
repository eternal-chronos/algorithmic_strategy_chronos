"""Probabilidad de zona de una corrida: qué pasó en el pasado al tocar zonas como ésta.

Capa fina sobre `domain/structure/zone_odds.py`: junta cada **toque** de zona
que la capa de señales ya marcó con **cómo murió** el ID tocado, y cuenta. Es
material de dibujo para el explorador —el porcentaje escrito encima de cada
zona— y **no decide nada**: no entra en la detección, no mueve una zona, no
abre ni cierra nada.

**El desenlace de un toque es la muerte del ID.** Un ID muere de una de dos
maneras y sólo de dos: por `ROTURA_A_FAVOR` —cerró más allá de su extremo, la
tendencia sigue y el ID siguiente va en el mismo sentido— o por
`ROTURA_EN_CONTRA` —cerró más allá de su ancla, el sesgo cambia y el siguiente
va al revés—. Así que:

- toque del **PUL o del APUL** y el ID muere a favor → la zona **aguantó y el
  ID siguió**; muere en contra → el precio **atravesó la zona y rompió el ID**;
- toque del **UL** (rechazo, sin cerrar más allá) y el ID muere a favor → acabó
  **rompiendo el UL y siguió**; muere en contra → el UL **lo frenó** y el ID se
  rompió por el otro lado.

En las dos lecturas «favorable» es lo mismo: **el ID murió a favor**. Un ID que
sigue vivo no tiene desenlace y no cuenta para nadie.

**Lo que se escribe sobre una zona es lo que se sabía cuando nació.** Los
toques que cuentan para la zona `Z` son los de ID de la misma temporalidad, el
mismo tipo de zona y la misma dirección que ya habían muerto en `Z.ts_birth`.
Sin eso, una zona de 2020 llevaría encima lo que pasó en 2024. Aparte viaja el
total del histórico por ordinal de toque —primero, segundo, tercero o más—,
que sí mira al futuro y el explorador lo dice.

**Y el precio a secas.** Independientemente de la estructura, cuántas veces
antes de nacer la zona el precio entró en su misma franja `[lo, hi]` y por dónde
salió. Se cuenta sobre las velas de la temporalidad del ID, viniendo del lado
por el que este ID busca la zona —el PUL desde el lado del extremo, el UL desde
el ancla— y también del otro, y se orienta: favorable es **salir a favor de este
ID**.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.structure.detect_impulses import TimeframeAnalysis
from chronos.application.structure.zone_signals import TimeframeSignals
from chronos.application.structure.zones import ImpulseZones, TimeframeZones
from chronos.domain.structure.enums import BreakKind, ImpulseDirection
from chronos.domain.structure.zone_odds import BandVisits, Tally, band_visits, tallies_known_at
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import Zone, ZoneKind

#: Cómo se agrupan los toques por su ordinal en el total del histórico. A
#: partir del tercero van juntos: más allá hay tan pocos que cada casilla
#: sería ruido.
ORDINAL_BUCKETS: tuple[str, ...] = ("1", "2", "3+")


def ordinal_bucket(ordinal: int) -> str:
    return ORDINAL_BUCKETS[min(ordinal, 3) - 1]


@dataclass(frozen=True, slots=True)
class ZoneOdds:
    """Los números de una zona: estructura sabida al nacer y precio a secas."""

    timeframe: str
    id_num: int
    kind: ZoneKind
    direction: ImpulseDirection
    ts_birth: datetime
    ts_end: datetime | None
    low: float
    high: float
    #: Toques de zonas como ésta cuyo ID ya había muerto al nacer ésta: todos
    #: los toques, y sólo los primeros toques de cada zona.
    known_all: Tally
    known_first: Tally
    #: Visitas del precio a la franja antes de nacer la zona, orientadas: `k`
    #: es salir **a favor de este ID**. `approach` es desde el lado por el que
    #: este ID busca la zona; `other` desde el contrario.
    price_approach: Tally
    price_other: Tally


@dataclass(frozen=True, slots=True)
class OddsGroup:
    """Total del histórico de un tipo de zona y una dirección, por ordinal."""

    kind: ZoneKind
    direction: ImpulseDirection
    all_touches: Tally
    by_ordinal: dict[str, Tally]


@dataclass(frozen=True, slots=True)
class TimeframeOdds:
    timeframe: str
    items: tuple[ZoneOdds, ...]
    totals: tuple[OddsGroup, ...]


@dataclass(frozen=True, slots=True)
class _Touch:
    kind: ZoneKind
    direction: ImpulseDirection
    ordinal: int
    #: Cuándo se supo el desenlace y cuál fue. Sólo toques de ID ya muertos.
    resolved_at: datetime
    favourable: bool


def zone_odds(
    analysis: TimeframeAnalysis, zones: TimeframeZones, signals: TimeframeSignals
) -> TimeframeOdds:
    """Los números de cada zona de la temporalidad, y los totales del histórico."""
    touches = _touches(zones, signals)
    stamps = pd.DatetimeIndex(analysis.bars.index)
    highs = analysis.bars["high"].to_numpy(dtype=float)
    lows = analysis.bars["low"].to_numpy(dtype=float)
    closes = analysis.bars["close"].to_numpy(dtype=float)

    # Cada zona con su ID, en el orden en que se dibujan; la cuenta de la
    # estructura se hace de una vez por grupo (tipo, dirección), no zona a zona.
    pairs = [(zoned, zone) for zoned in zones.items for zone in zoned.zones()]
    known_all, known_first = _known_by_group(pairs, touches)

    items = tuple(
        _odds_of(
            zoned,
            zone,
            known_all=known_all[position],
            known_first=known_first[position],
            visits=band_visits(
                high=highs,
                low=lows,
                close=closes,
                low_edge=zone.low,
                high_edge=zone.high,
                # El precio a secas: las velas cerradas hasta la que constituye
                # el ID, que es la que hace nacer la zona.
                until=min(zoned.index_constitution, len(stamps) - 1),
            ),
        )
        for position, (zoned, zone) in enumerate(pairs)
    )
    return TimeframeOdds(timeframe=zones.timeframe, items=items, totals=_totals(touches))


def _touches(zones: TimeframeZones, signals: TimeframeSignals) -> tuple[_Touch, ...]:
    """Cada toque con su desenlace. El toque del PUL/APUL es `TOQUE_PUL`; el del
    UL es `RECHAZO_UL`, la vela que llegó y no cerró más allá. La `ROTURA_UL`
    no es un toque sino el desenlace en sí, y no se cuenta dos veces."""
    by_id = {zoned.id_num: zoned for zoned in zones.items}
    found: list[_Touch] = []
    for item in signals.items:
        if item.kind not in (ZoneSignalKind.TOQUE_PUL, ZoneSignalKind.RECHAZO_UL):
            continue
        zoned = by_id.get(item.id_num)
        if zoned is None or zoned.exit_break is None or zoned.ts_end is None:
            continue
        found.append(
            _Touch(
                kind=item.zone,
                direction=zoned.direction,
                ordinal=item.signal.ordinal,
                resolved_at=zoned.ts_end,
                favourable=zoned.exit_break is BreakKind.A_FAVOR,
            )
        )
    return tuple(found)


def _known_by_group(
    pairs: list[tuple[ImpulseZones, Zone]], touches: tuple[_Touch, ...]
) -> tuple[list[Tally], list[Tally]]:
    """Lo sabido al nacer cada zona: todos los toques y sólo los primeros.

    Los toques que cuentan para una zona son los de zonas del mismo tipo y la
    misma dirección cuyo ID ya había muerto cuando ésta nació. Se agrupa por
    (tipo, dirección) y cada grupo se resuelve con una sola búsqueda ordenada
    sobre los nacimientos, que es lo que hace que ocho años de H1 no cuesten
    ocho años.
    """
    known_all: list[Tally] = [Tally(0, 0)] * len(pairs)
    known_first: list[Tally] = [Tally(0, 0)] * len(pairs)
    positions: dict[tuple[ZoneKind, ImpulseDirection], list[int]] = {}
    for position, (zoned, zone) in enumerate(pairs):
        positions.setdefault((zone.kind, zoned.direction), []).append(position)

    for (kind, direction), members in positions.items():
        alike = [t for t in touches if t.kind is kind and t.direction is direction]
        births = np.array([_nanos(pairs[i][1].ts_birth) for i in members], dtype=np.int64)
        for target, chosen in (
            (known_all, alike),
            (known_first, [t for t in alike if t.ordinal == 1]),
        ):
            counts, favourable = tallies_known_at(
                resolved_at=np.array([_nanos(t.resolved_at) for t in chosen], dtype=np.int64),
                favourable=np.array([t.favourable for t in chosen], dtype=bool),
                moments=births,
            )
            for offset, position in enumerate(members):
                target[position] = Tally(n=int(counts[offset]), k=int(favourable[offset]))
    return known_all, known_first


def _odds_of(
    zoned: ImpulseZones,
    zone: Zone,
    *,
    known_all: Tally,
    known_first: Tally,
    visits: BandVisits,
) -> ZoneOdds:
    approach, other = _oriented(visits, zone)
    return ZoneOdds(
        timeframe=zoned.timeframe,
        id_num=zoned.id_num,
        kind=zone.kind,
        direction=zoned.direction,
        ts_birth=zone.ts_birth,
        ts_end=zoned.ts_end,
        low=zone.low,
        high=zone.high,
        known_all=known_all,
        known_first=known_first,
        price_approach=approach,
        price_other=other,
    )


def _oriented(visits: BandVisits, zone: Zone) -> tuple[Tally, Tally]:
    """Las dos cuentas, con «favorable» = salir a favor del ID de la zona.

    El UL se busca en la dirección del ID y la zona en contra en la contraria:
    en un ID alcista se llega al UL desde abajo y al PUL desde arriba. Salir
    por arriba es a favor de un ID alcista y en contra de uno bajista.
    """
    towards = zone.direction if zone.kind is ZoneKind.LAST else zone.direction.opposite()
    approach, other = (
        (visits.from_below, visits.from_above)
        if towards is ImpulseDirection.ALCISTA
        else (visits.from_above, visits.from_below)
    )
    if zone.direction is ImpulseDirection.ALCISTA:
        return approach, other
    return _flipped(approach), _flipped(other)


def _flipped(tally: Tally) -> Tally:
    return Tally(n=tally.n, k=tally.n - tally.k)


def _totals(touches: tuple[_Touch, ...]) -> tuple[OddsGroup, ...]:
    groups: list[OddsGroup] = []
    for kind in ZoneKind:
        for direction in ImpulseDirection:
            alike = [t for t in touches if t.kind is kind and t.direction is direction]
            if not alike:
                continue
            groups.append(
                OddsGroup(
                    kind=kind,
                    direction=direction,
                    all_touches=_tally(alike),
                    by_ordinal={
                        bucket: _tally([t for t in alike if ordinal_bucket(t.ordinal) == bucket])
                        for bucket in ORDINAL_BUCKETS
                    },
                )
            )
    return tuple(groups)


def _tally(touches: list[_Touch]) -> Tally:
    return Tally(n=len(touches), k=sum(1 for touch in touches if touch.favourable))


def _nanos(moment: datetime) -> int:
    return int(pd.Timestamp(moment).value)
