"""Señales de zona de una corrida: toques del PUL y rechazos/roturas del UL.

Capa fina sobre `domain/structure/zone_signals.py`: recorre los impulsos que la
fase 2.0 ya zonificó, le da a cada zona el tramo de barras en el que estuvo viva
y devuelve las señales con su marca de tiempo puesta.

**No decide nada.** No entra en la detección, no toca la regla de rotura, no
genera entradas ni cambia un solo número de las fases 1, 2.0 y 2.1: es material
de dibujo para la auditoría visual del explorador. Encenderla o apagarla no puede
mover un impulso porque no hay nada que mover — se calcula *después*, sobre el
resultado ya cerrado.

Sólo existen donde existen las zonas: Diario y H4, las dos temporalidades con
detector. En H1 y M15 no hay ID propio y por tanto tampoco señales propias.

**El toque del PUL no espera al cierre de la vela grande.** Tocar es un asunto de
mechas: en cuanto el precio entra en la zona ya está tocada, y esperar cuatro
horas a que cierre la vela de H4 sería fechar la señal tarde. Pero **la vela del
ID sigue mandando**: es la que dice si el precio había salido de la zona y, por
tanto, si esto es una visita nueva o la misma de antes. Las dos cosas a la vez:

1. se clasifica en la temporalidad del ID, una señal por vela y sólo si el cierre
   anterior estaba fuera —si la vela grande cerró dentro del PUL, la siguiente
   *empieza dentro* y no toca nada—;
2. y esa señal se **re-fecha** en la vela de la serie más corta de la corrida
   —M15 en el reparto por defecto— en la que el precio entró en la zona, con los
   números de esa vela, que son los que se conocen en ese instante.

Así el toque se avisa en el minuto en que ocurre sin que un paseo de quince
minutos por encima del borde invente una visita nueva. El rechazo y la rotura del
UL están definidos por dónde **cierra** la vela y se quedan enteros en la
temporalidad del ID.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from chronos.application.structure.config import TIMEFRAME_MINUTES
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import (
    ZoneSignal,
    ZoneSignalKind,
    classify_zone_signals,
    moment_of_touch,
)
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind


@dataclass(frozen=True, slots=True)
class ImpulseSignal:
    """Una señal, ya situada en el tiempo y atribuida a su ID."""

    timeframe: str
    id_num: int
    direction: ImpulseDirection
    timestamp: datetime
    signal: ZoneSignal
    #: Temporalidad de la vela en la que se midió. Es la del ID salvo en el toque
    #: del PUL, que se mide en la serie fina para no esperar al cierre de la vela
    #: grande. El explorador la necesita para saber cuándo se supo la señal.
    source: str = ""

    @property
    def kind(self) -> ZoneSignalKind:
        return self.signal.kind

    @property
    def zone(self) -> ZoneKind:
        return self.signal.zone


@dataclass(frozen=True, slots=True)
class TimeframeSignals:
    """Señales de una temporalidad, en orden cronológico."""

    timeframe: str
    items: tuple[ImpulseSignal, ...]

    def counts(self) -> dict[str, int]:
        """Cuántas de cada tipo. El informe y las notas las enseñan, no las cuentan."""
        tally = Counter(item.kind.value for item in self.items)
        return {kind.value: tally.get(kind.value, 0) for kind in ZoneSignalKind}


@dataclass(frozen=True, slots=True)
class ZoneSignalsRun:
    """Señales de todas las temporalidades zonificadas de una corrida."""

    enabled: bool
    per_timeframe: dict[str, TimeframeSignals]

    @property
    def empty(self) -> bool:
        return not any(measurement.items for measurement in self.per_timeframe.values())


def detect_zone_signals(run: ImpulseRun, zones: ZonesRun | None) -> ZoneSignalsRun:
    """Señales de la corrida. Sin zonas no hay señales: no se emite nada."""
    if zones is None or not zones.enabled:
        return ZoneSignalsRun(enabled=False, per_timeframe={})

    fine = _finest_chart(run)
    return ZoneSignalsRun(
        enabled=True,
        per_timeframe={
            timeframe: _signals_of(run.analyses[timeframe], measurement.items, fine)
            for timeframe, measurement in zones.per_timeframe.items()
            if timeframe in run.analyses
        },
    )


def _finest_chart(run: ImpulseRun) -> tuple[str, CandleSeries] | None:
    """La serie de vela más corta de la corrida: donde se fecha el toque del PUL.

    Es uno de los gráficos que la corrida ya construyó —M15 en el reparto por
    defecto—, no un histórico aparte. Si el reparto no llevara ninguno más fino
    que el del ID, se devuelve `None` y el toque se mide en la propia
    temporalidad, como todo lo demás.
    """
    charts = [name for name in run.chart_bars if name in TIMEFRAME_MINUTES]
    if not charts:
        return None
    finest = min(charts, key=lambda name: TIMEFRAME_MINUTES[name])
    return finest, CandleSeries.of(run.chart_bars[finest])


def _signals_of(
    analysis: TimeframeAnalysis,
    items: tuple[ImpulseZones, ...],
    fine: tuple[str, CandleSeries] | None,
) -> TimeframeSignals:
    own = CandleSeries.of(analysis.bars)
    # El toque del PUL se fecha en la vela fina; si no hay ninguna más corta que
    # la del ID, se queda en la suya y todo funciona igual.
    touching_name, touching = (
        fine if fine is not None and len(fine[1]) > len(own) else (analysis.timeframe, own)
    )
    last_bar = len(own) - 1

    found: list[ImpulseSignal] = []
    for zoned in items:
        # Un ID vivo sigue produciendo señales hasta la última vela del histórico.
        end = zoned.index_end if zoned.index_end is not None else last_bar
        for zone in zoned.zones():
            first = _first_bar(zoned)
            if first > end:
                continue
            for signal in classify_zone_signals(
                high=own.high,
                low=own.low,
                close=own.close,
                zone=zone,
                first=first,
                last=end,
            ):
                moment = (
                    _at_the_moment(signal, zone, own, touching)
                    if zone.kind is ZoneKind.PENULTIMATE
                    else None
                )
                series, measured = (
                    (own, signal) if moment is None else (touching, moment)
                )
                found.append(
                    ImpulseSignal(
                        timeframe=analysis.timeframe,
                        id_num=zoned.id_num,
                        direction=zoned.direction,
                        timestamp=series.timestamps[measured.index].to_pydatetime(),
                        signal=measured,
                        source=analysis.timeframe if series is own else touching_name,
                    )
                )

    # Por marca de tiempo y no por índice: el toque del PUL viene de otra serie y
    # sus posiciones no son comparables con las de la temporalidad del ID.
    found.sort(key=lambda item: (item.timestamp, item.kind.value, item.id_num))
    return TimeframeSignals(timeframe=analysis.timeframe, items=tuple(found))


def _at_the_moment(
    signal: ZoneSignal, zone: Zone, own: CandleSeries, fine: CandleSeries
) -> ZoneSignal | None:
    """El toque de la vela `signal.index`, re-fechado dentro de ella.

    Se le pregunta a las velas finas que caen dentro de la grande cuál fue la
    primera que entró en la zona. `None` cuando no hay serie más corta o cuando
    ninguna de sus velas entra: el toque se queda entonces en la vela del ID.
    """
    if fine is own:
        return None
    span = _same_span(own, fine, signal.index, signal.index)
    if span is None:
        return None
    return moment_of_touch(
        high=fine.high,
        low=fine.low,
        close=fine.close,
        zone=zone,
        first=span[0],
        last=span[1],
        signal=signal,
    )


def _same_span(
    own: CandleSeries, fine: CandleSeries, first: int, last: int
) -> tuple[int, int] | None:
    """El tramo `[first, last]` de `own`, dicho en posiciones de `fine`.

    Las velas van fechadas en su **apertura**, así que el tramo va de la apertura
    de `first` a la de `last + 1` sin incluirla —o hasta el final de la serie si
    el ID sigue vivo—. Con eso, la última vela fina del tramo es la que cierra a
    la vez que la vela grande que mata al ID: ni una más.
    """
    start = fine.timestamps.searchsorted(own.timestamps[first], side="left")
    stop = (
        len(fine)
        if last + 1 >= len(own)
        else int(fine.timestamps.searchsorted(own.timestamps[last + 1], side="left"))
    )
    return (int(start), stop - 1) if start < stop else None


def _first_bar(zoned: ImpulseZones) -> int:
    """Primera barra que puede llevar señal: la siguiente al nacimiento de la zona.

    Las dos nacen a la vez, al constituirse el ID: el UL sobre la vela del
    extremo y el PUL sobre la del extremo anterior, que ya estaba cerrada. Se
    toma el índice que la fase 2.0 ya registró en vez de volver a buscar la vela
    por su marca de tiempo: buscarla sería tener una segunda opinión sobre
    cuándo nació.
    """
    return zoned.index_constitution + 1
