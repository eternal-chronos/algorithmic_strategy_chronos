"""La caja del 50 % del retroceso, tal como la mide el mentor del propietario.

**No es el 50 % del ID.** El nivel 50 % que ya dibuja el explorador es el punto
medio entre el ancla y el extremo del ID vigente. Éste es otro: cuando un ID se
rompe a favor y nace el siguiente en el mismo sentido, el mentor traza un
rectángulo que va del **ancla del ID nuevo** —el arranque de la pierna que
rompió— al **UL del ID anterior**, que es exactamente el PUL del nuevo. La
línea media de esa caja es el 50 % que él mira, y sólo existe cuando el ID
tiene PUL: sin ID anterior en el mismo sentido no hay retroceso que medir.

De esa caja cuelgan dos lecturas más, las dos suyas:

- **tendencia rápida o lenta.** En la rápida el retroceso se frena en el PUL y
  sigue; en la lenta atraviesa el PUL y va a buscar el 50 % de la caja. Qué
  tipo de tendencia hay decide **qué nivel esperar**. Se clasifica el ID vivo
  mirando sólo lo que hicieron los ID anteriores de la misma cadena —todos
  muertos antes de que éste naciera—, así que la etiqueta nunca mira adelante;
- **confluencia entre temporalidades.** El mentor mueve la caja de H1 hasta que
  "encaja dentro" del 50 % de H4. Aquí se mide sin mover nada: la caja de la
  temporalidad fina va marcada cuando su línea media cae dentro de la caja del
  ID que estaba vivo en la temporalidad superior al constituirse ella.

Todo esto es **medición para el dibujo**: no entra en la detección, no mueve un
ID y no abre ni cierra nada.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

import numpy as np
import pandas as pd

from chronos.application.structure.detect_impulses import TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, TimeframeZones
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.impulse import DominantImpulse

#: Cuántos ID anteriores de la cadena se miran para decir si la tendencia es
#: rápida o lenta. Tres es lo que cabe en una pantalla y lo que el mentor
#: enseña con sus dibujos: dos o tres retrocesos seguidos, no un histórico.
CHAIN_LOOKBACK = 3


class TrendPace(StrEnum):
    """Rápida respeta el PUL; lenta va al 50 % de la caja."""

    FAST = "rapida"
    SLOW = "lenta"


@dataclass(frozen=True, slots=True)
class Confluence:
    """La caja de la temporalidad superior dentro de la que cae este 50 %."""

    timeframe: str
    id_num: int
    low: float
    high: float
    mid: float


@dataclass(frozen=True, slots=True)
class PullbackBox:
    """La caja de un ID con PUL: del ancla al borde de cuerpo del PUL."""

    timeframe: str
    id_num: int
    direction: ImpulseDirection
    ts_constitution: datetime
    ts_end: datetime | None
    #: Los dos bordes de la caja y su línea media. `low <= high` siempre.
    low: float
    high: float
    mid: float
    #: La punta de la zona PUL: tocarla es «llegar al PUL».
    pul_inner: float
    #: Primera vela de la vida del ID en que la mecha llegó al PUL, y al 50 %.
    #: `None` si no llegó. Son hechos de la vida del ID y en el replay sólo se
    #: enseñan cuando el reloj los ha alcanzado.
    ts_pul_touch: datetime | None
    ts_mid_touch: datetime | None
    #: La pierna que constituyó el ID: cuánto recorrió y en cuántas velas.
    leg_range: float
    leg_bars: int
    #: Hasta dónde devolvió el precio en toda la vida del ID —el punto más
    #: lejano en contra desde el extremo de la constitución—, cuándo y en cuántas
    #: velas desde ese extremo. `retrace_range` es 0 si no devolvió nada.
    retrace_range: float
    retrace_bars: int
    ts_retrace: datetime | None
    #: Cuántos ID seguidos en el mismo sentido hay detrás de éste (la cadena).
    chain: int
    #: Rápida o lenta según los ID anteriores de la cadena; `None` sin historia.
    pace: TrendPace | None
    confluence: Confluence | None = None

    @property
    def reached_mid(self) -> bool:
        return self.ts_mid_touch is not None

    @property
    def retrace_share(self) -> float:
        """Qué parte de la pierna se ha devuelto, de 0 a 1 (puede pasar de 1)."""
        return self.retrace_range / self.leg_range if self.leg_range > 0 else 0.0


def pullback_boxes(analysis: TimeframeAnalysis, zones: TimeframeZones) -> tuple[PullbackBox, ...]:
    """Una caja por ID con PUL, con su carácter leído de la cadena anterior."""
    highs = analysis.bars["high"].to_numpy(dtype=float)
    lows = analysis.bars["low"].to_numpy(dtype=float)
    stamps = pd.DatetimeIndex(analysis.bars.index)
    impulses = {impulse.id_num: impulse for impulse in analysis.impulses}
    last = len(stamps) - 1

    boxes: list[PullbackBox] = []
    #: Los ID anteriores de la cadena con caja, por id_num, para leer el paso.
    by_id: dict[int, PullbackBox] = {}
    chain = 0
    for zoned in zones.items:
        impulse = impulses.get(zoned.id_num)
        # La cadena sigue mientras cada ID lleve PUL: eso es lo que significa
        # que el anterior iba en el mismo sentido y murió por rotura a favor.
        chain = chain + 1 if zoned.has_penultimate else 0
        if impulse is None or not impulse.publishable or zoned.penultimate is None:
            continue
        box = _box_of(
            zoned,
            impulse,
            highs,
            lows,
            stamps,
            last,
            chain=chain,
            pace=_pace_of(zoned.id_num, chain, by_id),
        )
        boxes.append(box)
        by_id[box.id_num] = box
    return tuple(boxes)


def _box_of(
    zoned: ImpulseZones,
    impulse: DominantImpulse,
    highs: np.ndarray,
    lows: np.ndarray,
    stamps: pd.DatetimeIndex,
    last: int,
    *,
    chain: int,
    pace: TrendPace | None,
) -> PullbackBox:
    assert zoned.penultimate is not None
    pul = zoned.penultimate
    bullish = zoned.direction is ImpulseDirection.ALCISTA
    low, high = sorted((impulse.anchor, pul.outer))
    mid = (low + high) / 2.0

    # La vida del ID: de la constitución a su muerte, o al final del histórico.
    start = impulse.index_constitution
    end = impulse.index_end if impulse.index_end is not None else last
    # El retroceso se mide contra el lado EN CONTRA: los mínimos en un ID
    # alcista, los máximos en uno bajista.
    against = lows[start : end + 1] if bullish else highs[start : end + 1]

    pul_touch = _first_reach(against, pul.inner, bullish)
    mid_touch = _first_reach(against, mid, bullish)
    extreme = impulse.extreme_at_constitution
    leg_bars = max(impulse.index_extreme_at_constitution - impulse.index_anchor, 0)
    # Lo más lejos que devolvió, desde el extremo de la constitución: en un ID
    # alcista el mínimo de la vida, en uno bajista el máximo.
    deepest = int(np.argmin(against)) if bullish else int(np.argmax(against))
    retrace = float(extreme - against[deepest]) if bullish else float(against[deepest] - extreme)
    if retrace <= 0.0:
        retrace_range, retrace_bars, ts_retrace = 0.0, 0, None
    else:
        retrace_range = retrace
        retrace_bars = max(start + deepest - impulse.index_extreme_at_constitution, 0)
        ts_retrace = _at(stamps, start + deepest)
    return PullbackBox(
        timeframe=zoned.timeframe,
        id_num=zoned.id_num,
        direction=zoned.direction,
        ts_constitution=zoned.ts_constitution,
        ts_end=zoned.ts_end,
        low=low,
        high=high,
        mid=mid,
        pul_inner=pul.inner,
        ts_pul_touch=None if pul_touch is None else _at(stamps, start + pul_touch),
        ts_mid_touch=None if mid_touch is None else _at(stamps, start + mid_touch),
        leg_range=abs(extreme - impulse.anchor),
        leg_bars=leg_bars,
        retrace_range=retrace_range,
        retrace_bars=retrace_bars,
        ts_retrace=ts_retrace,
        chain=chain,
        pace=pace,
    )


def _first_reach(against: np.ndarray, level: float, bullish: bool) -> int | None:
    """Primera posición del tramo en que la mecha llega al nivel, o `None`."""
    hits = np.flatnonzero(against <= level if bullish else against >= level)
    return int(hits[0]) if hits.size else None


def _pace_of(id_num: int, chain: int, by_id: Mapping[int, PullbackBox]) -> TrendPace | None:
    """Rápida si ningún ID anterior de la cadena llegó a su 50 %; lenta si alguno.

    Se miran como mucho `CHAIN_LOOKBACK` ID hacia atrás y sólo los que llevan
    caja, que son los que tienen un 50 % al que llegar. Sin ninguno, no hay
    historia y no se inventa un paso.
    """
    looked = 0
    slow = False
    for back in range(1, chain + 1):
        earlier = by_id.get(id_num - back)
        if earlier is None:
            continue
        looked += 1
        slow = slow or earlier.reached_mid
        if looked >= CHAIN_LOOKBACK:
            break
    if looked == 0:
        return None
    return TrendPace.SLOW if slow else TrendPace.FAST


def _at(stamps: pd.DatetimeIndex, index: int) -> datetime:
    return pd.Timestamp(stamps[index]).to_pydatetime()


def with_confluence(
    lower: Sequence[PullbackBox],
    upper: Sequence[PullbackBox],
    *,
    lower_span: timedelta,
    upper_span: timedelta,
) -> tuple[PullbackBox, ...]:
    """Marca en cada caja fina la caja superior dentro de la que cae su 50 %.

    La caja superior que cuenta es la del ID que estaba **vivo y ya conocido**
    cuando se constituyó la fina: su vela de constitución tenía que haber
    cerrado antes de que cerrara la de la fina, que es lo que evita que el
    dibujo enseñe una confluencia que a esa hora no se podía saber.
    """
    marked: list[PullbackBox] = []
    for box in lower:
        known_at = box.ts_constitution + lower_span
        candidate = None
        for above in upper:
            if above.ts_constitution + upper_span > known_at:
                continue
            if above.ts_end is not None and above.ts_end < box.ts_constitution:
                continue
            candidate = above
        if candidate is not None and candidate.low <= box.mid <= candidate.high:
            marked.append(
                replace(
                    box,
                    confluence=Confluence(
                        timeframe=candidate.timeframe,
                        id_num=candidate.id_num,
                        low=candidate.low,
                        high=candidate.high,
                        mid=candidate.mid,
                    ),
                )
            )
        else:
            marked.append(box)
    return tuple(marked)
