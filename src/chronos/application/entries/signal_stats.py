"""Qué valen las señales de la cascada. **Estadística, no entradas.**

La fase 3.0 dibuja marcas; esto las cuenta. Sigue sin haber orden, ni stop, ni
target, ni tamaño, ni curva de capital: lo único que se hace aquí es mirar qué
recorrió el precio a partir de cada marca y con qué frecuencia lo recorrió antes
de que la marca quedara desmentida. Es lo máximo que se puede afirmar sobre una
señal sin decidir todavía cómo se opera.

**La vara.** Cada señal trae su propia zona, y `R` es la distancia del precio de
la señal al borde **exterior** de esa zona: el sitio donde el precio ha
atravesado la zona entera y la señal deja de significar nada. No es un stop —no
hay stop— sino la unidad natural de esa señal, la que permite comparar un PUL de
H1 de tres dólares con un PUL de H4 de treinta. Cuando dos grupos miden en R
distinto se dice: cada tabla lleva la mediana de R **en dólares** al lado, y una
columna en ATR de H1 para poder compararlos con una vara común.

**El horizonte.** Se sigue cada señal hasta que una vela de H1 **cierra** más
allá del borde exterior —lo mismo que mata a un ID en la fase 2.1— o hasta un
tope de velas de H1, lo que llegue antes. Lo que se reporta es hasta dónde llegó
el precio a favor antes de eso.

**Causalidad.** La observación no empieza en la vela de la señal sino en la
siguiente: un toque fechado en una vela de M15 se mide desde que esa vela cierra,
y una confirmación que depende del cierre de una vela de H1 se mide desde que esa
vela de H1 cierra. Ninguna medida incluye el tramo de vela anterior al instante
en que la señal se supo, y por eso los números salen algo peores que si se
midiera desde el propio precio de contacto.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.entries.cascade import CascadeMark, CascadeRun, CascadeStep
from chronos.application.structure.config import DAILY, H1, TIMEFRAME_MINUTES
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zones import ZonesRun
from chronos.domain.entries.excursion import first_adverse_close, measure_excursion
from chronos.domain.strategies.indicators import atr
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import CandleSeries

#: Objetivos que se miden, en múltiplos de R.
TARGETS: tuple[float, ...] = (1.0, 2.0, 3.0)
#: Tope de la observación, en velas de H1. Cuatro días de mercado.
DEFAULT_CAP = 96
#: Frontera entre un toque «rápido» y uno «lento», en velas de H1 desde el de H4.
FAST_BARS = 12
#: Sorteos por cada señal real en la línea de azar. Más de uno para que el grupo
#: de control no dependa de la suerte de una sola tirada.
RANDOM_DRAWS = 5
#: Periodo del ATR de H1 con el que se compara entre grupos de R distinto.
ATR_PERIOD = 14

# --- Nombres de los grupos. Salen tal cual al informe -------------------------

G_H4 = "H4 · toque del PUL (sin bajar a H1)"
G_H4_CON = "H4 · toques que acabaron dando señal en H1"
G_H4_SIN = "H4 · toques que no la dieron"
G_H4_VETADO = "H4 · toques vetados por el Diario"
G_SENAL = "SEÑAL · toque del PUL de H1 (la del propietario)"
G_CONFIRMA = "H1 · confirmar y no esperar al toque"
G_CIERRE = "H1 · primer cierre a favor dentro del PUL de H4"
G_AZAR = "AZAR · mismo riesgo y dirección, instante al azar"


@dataclass(frozen=True, slots=True)
class SignalPoint:
    """Un punto medible: un instante, una dirección y una zona que lo desmiente."""

    group: str
    timestamp: datetime
    #: Desde cuándo se observa. Nunca antes de que la señal se supiera.
    observe_from: datetime
    direction: ImpulseDirection
    price: float
    #: Borde exterior de la zona de la señal: cerrar más allá la desmiente.
    outer: float
    tags: frozenset[str] = frozenset()

    @property
    def risk(self) -> float:
        return abs(self.price - self.outer)


@dataclass(frozen=True, slots=True)
class MeasuredSignal:
    """Un punto ya medido contra el histórico."""

    point: SignalPoint
    risk: float
    favor: float
    against: float
    favor_atr: float
    invalidated: bool
    #: Velas de H1 hasta cada objetivo, `None` si no se alcanzó.
    bars_to_target: tuple[float | None, ...]
    bars_seen: int

    @property
    def year(self) -> int:
        return self.point.timestamp.year


@dataclass(frozen=True, slots=True)
class GroupStats:
    """Una fila del informe: un grupo de señales resumido."""

    name: str
    n: int
    #: Fracción que alcanzó cada objetivo antes de quedar desmentida.
    hit: tuple[float, ...]
    #: Fracción desmentida sin haber llegado ni a 1R.
    dead: float
    favor_median: float
    against_median: float
    favor_atr_median: float
    risk_median: float
    bars_to_first_median: float


@dataclass(frozen=True, slots=True)
class StudySection:
    """Un bloque de la comparativa, con la pregunta que contesta."""

    title: str
    question: str
    groups: tuple[GroupStats, ...]


@dataclass(frozen=True, slots=True)
class Funnel:
    """Cuánto sobrevive de escalón a escalón."""

    touches: int
    vetoed: int
    searched: int
    confirmed: int
    signalled: int
    bars_to_confirmation: float
    bars_to_signal: float


@dataclass(frozen=True, slots=True)
class SignalStudy:
    """El estudio completo de una corrida."""

    enabled: bool
    cap_bars: int
    targets: tuple[float, ...]
    funnel: Funnel | None = None
    sections: tuple[StudySection, ...] = ()
    #: Señales que no se pudieron medir y por qué. No se esconden.
    discarded: dict[str, int] = field(default_factory=dict)
    span: tuple[datetime, datetime] | None = None


def study_signals(
    run: ImpulseRun,
    zones: ZonesRun | None,
    cascade: CascadeRun,
    *,
    cap_bars: int = DEFAULT_CAP,
    targets: tuple[float, ...] = TARGETS,
    seed: int = 20250828,
) -> SignalStudy:
    """El estudio de una corrida de la cascada. Sin cascada no hay nada que medir."""
    if not cascade.enabled or zones is None or not zones.enabled:
        return SignalStudy(enabled=False, cap_bars=cap_bars, targets=targets)
    return _Study(run, zones, cascade, cap_bars=cap_bars, targets=targets, seed=seed).build()


class _Study:
    """El recorrido del estudio. Toda la lectura de series vive aquí."""

    def __init__(
        self,
        run: ImpulseRun,
        zones: ZonesRun,
        cascade: CascadeRun,
        *,
        cap_bars: int,
        targets: tuple[float, ...],
        seed: int,
    ) -> None:
        self._cap = cap_bars
        self._targets = targets
        self._cascade = cascade
        self._rng = random.Random(seed)
        finest = min(run.chart_bars, key=lambda name: TIMEFRAME_MINUTES[name])
        self._fine = CandleSeries.of(run.chart_bars[finest])
        self._fine_minutes = TIMEFRAME_MINUTES[finest]
        self._h1 = CandleSeries.of(run.analyses[H1].bars)
        self._atr = _prior_bar_atr(self._h1, ATR_PERIOD)
        #: Color del cuerpo de cada vela de H1, de una vez: la confirmación
        #: tonta lo pregunta una vez por ventana y son miles de ventanas.
        self._h1_bullish = self._h1.close > self._h1.open
        self._h1_bearish = self._h1.close < self._h1.open
        self._hourly = {item.id_num: item for item in zones.per_timeframe[H1].items}
        self._daily = zones.per_timeframe[DAILY].items if DAILY in zones.per_timeframe else ()
        self._by_seq = {mark.seq: mark for mark in cascade.marks}
        self._children: dict[int, list[CascadeMark]] = defaultdict(list)
        for mark in cascade.marks:
            if mark.parent is not None:
                self._children[mark.parent].append(mark)
        self._discarded: dict[str, int] = defaultdict(int)

    # --- Construcción -------------------------------------------------------

    def build(self) -> SignalStudy:
        populations = self._populations()
        measured = {
            name: [m for m in (self._measure(p) for p in points) if m is not None]
            for name, points in populations.items()
        }
        signal = measured.get(G_SENAL, [])
        return SignalStudy(
            enabled=True,
            cap_bars=self._cap,
            targets=self._targets,
            funnel=self._funnel(),
            sections=(
                StudySection(
                    title="¿Aporta algo bajar a H1?",
                    question=(
                        "Todos medidos desde el toque del PUL de H4 y con el R del PUL "
                        "de H4: misma vara y mismo punto de partida, así que la única "
                        "diferencia entre las filas es el filtro."
                    ),
                    groups=tuple(
                        _summarize(name, measured.get(name, []), self._targets)
                        for name in (G_H4, G_H4_CON, G_H4_SIN, G_H4_VETADO)
                    ),
                ),
                StudySection(
                    title="Dónde confirmar en H1",
                    question=(
                        "Cada fila se mide desde su propio punto y con el R de su "
                        "propia zona: el R en dólares no es el mismo y por eso está en "
                        "la tabla. La columna en ATR de H1 es la vara común."
                    ),
                    groups=tuple(
                        _summarize(name, measured.get(name, []), self._targets)
                        for name in (G_SENAL, G_CONFIRMA, G_CIERRE, G_H4, G_AZAR)
                    ),
                ),
                StudySection(
                    title="Cortes de la señal del propietario",
                    question=(
                        "La misma población del toque del PUL de H1, partida en dos por "
                        "cada criterio. Todas las filas comparten vara."
                    ),
                    groups=self._splits(signal),
                ),
                StudySection(
                    title="La señal año a año",
                    question="Para ver si el resultado vive de un solo año.",
                    groups=self._by_year(signal),
                ),
            ),
            discarded=dict(self._discarded),
            span=self._span(),
        )

    def _span(self) -> tuple[datetime, datetime] | None:
        if not len(self._fine):
            return None
        return self._fine.at(0), self._fine.at(len(self._fine) - 1)

    def _funnel(self) -> Funnel:
        searched = self._of(CascadeStep.BUSCAR_H1)
        vetoed = self._of(CascadeStep.H4_DESCARTADO)
        confirmed = self._of(CascadeStep.CONFIRMA_PUL_H1)
        signalled = self._of(CascadeStep.TOQUE_PUL_H1)
        to_confirm: list[float] = []
        to_signal: list[float] = []
        for mark in confirmed:
            origin = self._by_seq.get(mark.parent) if mark.parent else None
            if origin is not None:
                to_confirm.append(self._hourly_distance(origin.timestamp, mark.timestamp))
        for mark in signalled:
            confirm = self._by_seq.get(mark.parent) if mark.parent else None
            origin = self._by_seq.get(confirm.parent) if confirm and confirm.parent else None
            if origin is not None:
                to_signal.append(self._hourly_distance(origin.timestamp, mark.timestamp))
        return Funnel(
            touches=len(searched) + len(vetoed),
            vetoed=len(vetoed),
            searched=len(searched),
            confirmed=len(confirmed),
            signalled=len(signalled),
            bars_to_confirmation=_median(to_confirm),
            bars_to_signal=_median(to_signal),
        )

    def _of(self, step: CascadeStep) -> tuple[CascadeMark, ...]:
        return tuple(mark for mark in self._cascade.marks if mark.step is step)

    # --- Poblaciones --------------------------------------------------------

    def _populations(self) -> dict[str, list[SignalPoint]]:
        groups: dict[str, list[SignalPoint]] = defaultdict(list)
        for mark in self._of(CascadeStep.H4_DESCARTADO):
            point = self._touch_point(mark, G_H4_VETADO)
            if point is not None:
                groups[G_H4_VETADO].append(point)

        for origin in self._of(CascadeStep.BUSCAR_H1):
            confirm = self._child(origin, CascadeStep.CONFIRMA_PUL_H1)
            touch = self._child(confirm, CascadeStep.TOQUE_PUL_H1) if confirm else None

            base = self._touch_point(origin, G_H4)
            if base is not None:
                groups[G_H4].append(base)
                groups[G_H4_CON if touch is not None else G_H4_SIN].append(
                    _renamed(base, G_H4_CON if touch is not None else G_H4_SIN)
                )

            close_point = self._first_hourly_close(origin)
            if close_point is not None:
                groups[G_CIERRE].append(close_point)

            if confirm is not None:
                point = self._close_point(confirm, G_CONFIRMA)
                if point is not None:
                    groups[G_CONFIRMA].append(point)
            if touch is not None and confirm is not None:
                point = self._touch_point(touch, G_SENAL, tags=self._tags(origin, confirm, touch))
                if point is not None:
                    groups[G_SENAL].append(point)

        groups[G_AZAR] = self._random_like(groups[G_SENAL])
        return dict(groups)

    def _child(self, mark: CascadeMark | None, step: CascadeStep) -> CascadeMark | None:
        if mark is None:
            return None
        for child in self._children.get(mark.seq, ()):
            if child.step is step:
                return child
        return None

    def _touch_point(
        self, mark: CascadeMark, group: str, *, tags: frozenset[str] = frozenset()
    ) -> SignalPoint | None:
        """Un toque, fechado en la vela fina. Se observa desde que ésa cierra."""
        if mark.low is None or mark.high is None:
            return None
        index = _bar_of(self._fine, mark.timestamp)
        if index < 0 or index + 1 >= len(self._fine):
            self._discarded["sin vela siguiente"] += 1
            return None
        return self._point(
            group=group,
            timestamp=mark.timestamp,
            observe_from=self._fine.at(index + 1),
            direction=mark.direction,
            price=mark.price,
            low=mark.low,
            high=mark.high,
            tags=tags,
        )

    def _close_point(self, mark: CascadeMark, group: str) -> SignalPoint | None:
        """Una confirmación que depende de un cierre de H1: se observa desde él."""
        if mark.low is None or mark.high is None:
            return None
        index = _bar_of(self._h1, mark.timestamp)
        if index < 0 or index + 1 >= len(self._h1):
            self._discarded["sin vela siguiente"] += 1
            return None
        return self._point(
            group=group,
            timestamp=mark.timestamp,
            observe_from=self._h1.at(index + 1),
            direction=mark.direction,
            price=mark.close,
            low=mark.low,
            high=mark.high,
        )

    def _point(
        self,
        *,
        group: str,
        timestamp: datetime,
        observe_from: datetime,
        direction: ImpulseDirection,
        price: float,
        low: float,
        high: float,
        tags: frozenset[str] = frozenset(),
    ) -> SignalPoint | None:
        outer = low if direction is ImpulseDirection.ALCISTA else high
        point = SignalPoint(
            group=group,
            timestamp=timestamp,
            observe_from=observe_from,
            direction=direction,
            price=price,
            outer=outer,
            tags=tags,
        )
        if point.risk <= 0:
            # El contacto cae justo en el borde exterior: la vela se comió la
            # zona entera de una pieza, o la zona no tenía altura. Sin distancia
            # al borde no hay R y no hay nada que medir.
            self._discarded[f"{group} · contacto en el borde exterior"] += 1
            return None
        beyond = (
            price < outer if direction is ImpulseDirection.ALCISTA else price > outer
        )
        if beyond:
            # El precio de la marca ya está al otro lado de la zona. Pasa en la
            # confirmación cuando cae en la vela que rompió el ID de H1, que la
            # cascada todavía admite: `index_end` es la vela que rompe.
            self._discarded[f"{group} · la marca ya está fuera de la zona"] += 1
            return None
        return point

    def _first_hourly_close(self, origin: CascadeMark) -> SignalPoint | None:
        """Primer cierre de H1 a favor dentro del PUL de H4, dentro de la ventana.

        La confirmación tonta: una vela y su color, sin estructura ninguna. Está
        para saber cuánto de lo que se ve en H1 lo daría cualquier cosa.
        """
        if origin.low is None or origin.high is None:
            return None
        first = max(_bar_of(self._h1, origin.timestamp), 0)
        last = (
            len(self._h1) - 1
            if origin.window_end is None
            else int(self._h1.timestamps.searchsorted(pd.Timestamp(origin.window_end), "left")) - 1
        )
        favour = (
            self._h1_bullish
            if origin.direction is ImpulseDirection.ALCISTA
            else self._h1_bearish
        )
        for index in range(first, min(last, len(self._h1) - 2) + 1):
            close = float(self._h1.close[index])
            if favour[index] and origin.low <= close <= origin.high:
                return self._point(
                    group=G_CIERRE,
                    timestamp=self._h1.at(index),
                    observe_from=self._h1.at(index + 1),
                    direction=origin.direction,
                    price=close,
                    low=origin.low,
                    high=origin.high,
                )
        return None

    def _tags(
        self, origin: CascadeMark, confirm: CascadeMark, touch: CascadeMark
    ) -> frozenset[str]:
        """Los cortes por los que se parte después la población de la señal."""
        tags: set[str] = set()
        hourly = self._hourly.get(confirm.id_num)
        born = hourly.ts_constitution if hourly is not None else None
        tags.add(
            "ID de H1 nace después del toque"
            if born is not None and born > origin.timestamp
            else "ID de H1 ya venía alineado"
        )
        overlap = (
            origin.low is not None
            and origin.high is not None
            and confirm.low is not None
            and confirm.high is not None
            and confirm.low <= origin.high
            and origin.low <= confirm.high
        )
        tags.add("PUL de H1 solapa el de H4" if overlap else "PUL de H1 fuera del de H4")
        waited = self._hourly_distance(origin.timestamp, touch.timestamp)
        tags.add(
            f"señal en ≤{FAST_BARS} velas H1"
            if waited <= FAST_BARS
            else f"señal en >{FAST_BARS} velas H1"
        )
        tags.add(
            "a favor del ID diario"
            if self._daily_direction(touch.timestamp) is touch.direction
            else "sin ID diario a favor"
        )
        tags.add("alcista" if touch.direction is ImpulseDirection.ALCISTA else "bajista")
        return frozenset(tags)

    def _daily_direction(self, moment: datetime) -> ImpulseDirection | None:
        """Dirección del ID diario vigente en ese instante, si hay alguno."""
        for item in self._daily:
            if item.ts_constitution <= moment and (item.ts_end is None or moment < item.ts_end):
                return item.direction
        return None

    def _random_like(self, points: list[SignalPoint]) -> list[SignalPoint]:
        """La línea de azar: mismo riesgo y misma dirección, instante al azar.

        Contesta la única pregunta que hace comparable cualquier número de arriba:
        qué habría dado tirar una moneda con esos mismos umbrales sobre este
        mismo histórico.
        """
        if not len(self._fine) or not points:
            return []
        top = len(self._fine) - 2
        drawn: list[SignalPoint] = []
        for point in points:
            for _ in range(RANDOM_DRAWS):
                index = self._rng.randint(0, top)
                price = float(self._fine.open[index])
                outer = (
                    price - point.risk
                    if point.direction is ImpulseDirection.ALCISTA
                    else price + point.risk
                )
                drawn.append(
                    SignalPoint(
                        group=G_AZAR,
                        timestamp=self._fine.at(index),
                        observe_from=self._fine.at(index),
                        direction=point.direction,
                        price=price,
                        outer=outer,
                    )
                )
        return drawn

    # --- Medida -------------------------------------------------------------

    def _measure(self, point: SignalPoint) -> MeasuredSignal | None:
        start = _bar_of(self._h1, point.observe_from)
        if start < 0:
            self._discarded["fuera del histórico de H1"] += 1
            return None
        cap = min(start + self._cap - 1, len(self._h1) - 1)
        window = self._h1.close[start : cap + 1]
        hit = first_adverse_close(window, outer=point.outer, direction=point.direction)
        last = start + hit if hit is not None else cap
        end = self._h1.at(last + 1) if last + 1 < len(self._h1) else None

        first_fine = int(
            self._fine.timestamps.searchsorted(pd.Timestamp(point.observe_from), "left")
        )
        last_fine = (
            len(self._fine)
            if end is None
            else int(self._fine.timestamps.searchsorted(pd.Timestamp(end), "left"))
        )
        if last_fine <= first_fine:
            self._discarded["sin tramo que observar"] += 1
            return None

        run = measure_excursion(
            high=self._fine.high[first_fine:last_fine],
            low=self._fine.low[first_fine:last_fine],
            price=point.price,
            risk=point.risk,
            direction=point.direction,
            targets=self._targets,
        )
        atr_value = float(self._atr[start])
        return MeasuredSignal(
            point=point,
            risk=point.risk,
            favor=run.favor,
            against=run.against,
            favor_atr=run.favor * point.risk / atr_value if atr_value > 0 else float("nan"),
            invalidated=hit is not None,
            bars_to_target=tuple(
                None if index is None else index * self._fine_minutes / 60
                for index in run.reached
            ),
            bars_seen=run.bars,
        )

    def _hourly_distance(self, start: datetime, end: datetime) -> float:
        """Velas de H1 entre dos instantes. Cuenta velas, no horas de reloj."""
        first = int(self._h1.timestamps.searchsorted(pd.Timestamp(start), "right"))
        last = int(self._h1.timestamps.searchsorted(pd.Timestamp(end), "right"))
        return float(max(last - first, 0))

    # --- Cortes -------------------------------------------------------------

    def _splits(self, signal: list[MeasuredSignal]) -> tuple[GroupStats, ...]:
        labels: list[str] = []
        for item in signal:
            for tag in item.point.tags:
                if tag not in labels:
                    labels.append(tag)
        return tuple(
            _summarize(
                label,
                [item for item in signal if label in item.point.tags],
                self._targets,
            )
            for label in sorted(labels)
        )

    def _by_year(self, signal: list[MeasuredSignal]) -> tuple[GroupStats, ...]:
        years = sorted({item.year for item in signal})
        return tuple(
            _summarize(
                str(year), [item for item in signal if item.year == year], self._targets
            )
            for year in years
        )


# --- Resumen -----------------------------------------------------------------


def _summarize(
    name: str, items: list[MeasuredSignal], targets: tuple[float, ...]
) -> GroupStats:
    total = len(items)
    if total == 0:
        return GroupStats(
            name=name,
            n=0,
            hit=(0.0,) * len(targets),
            dead=0.0,
            favor_median=float("nan"),
            against_median=float("nan"),
            favor_atr_median=float("nan"),
            risk_median=float("nan"),
            bars_to_first_median=float("nan"),
        )
    hit = tuple(
        sum(1 for item in items if item.bars_to_target[position] is not None) / total
        for position in range(len(targets))
    )
    dead = sum(
        1 for item in items if item.invalidated and item.bars_to_target[0] is None
    ) / total
    return GroupStats(
        name=name,
        n=total,
        hit=hit,
        dead=dead,
        favor_median=_median([item.favor for item in items]),
        against_median=_median([item.against for item in items]),
        favor_atr_median=_median(
            [item.favor_atr for item in items if not np.isnan(item.favor_atr)]
        ),
        risk_median=_median([item.risk for item in items]),
        bars_to_first_median=_median(
            [item.bars_to_target[0] for item in items if item.bars_to_target[0] is not None]
        ),
    )


def _median(values: list[float]) -> float:
    return float(np.median(values)) if values else float("nan")


def _renamed(point: SignalPoint, group: str) -> SignalPoint:
    return SignalPoint(
        group=group,
        timestamp=point.timestamp,
        observe_from=point.observe_from,
        direction=point.direction,
        price=point.price,
        outer=point.outer,
        tags=point.tags,
    )


def _prior_bar_atr(series: CandleSeries, period: int) -> np.ndarray:
    """ATR de la temporalidad, desplazado una vela: el de `i` cierra en `i-1`."""
    raw = atr(series.high, series.low, series.close, period)
    values = np.full(len(raw), np.nan, dtype=float)
    values[1:] = raw[:-1]
    return values


def _bar_of(series: CandleSeries, moment: datetime) -> int:
    """Posición de la vela que contiene ese instante; `-1` si es anterior a todas."""
    return int(series.timestamps.searchsorted(pd.Timestamp(moment), side="right")) - 1


__all__ = [
    "Funnel",
    "GroupStats",
    "MeasuredSignal",
    "SignalPoint",
    "SignalStudy",
    "StudySection",
    "study_signals",
]
