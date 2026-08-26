"""La cascada H4 → H1, con el Diario de **veto**. Todavía sólo señales.

Esto no abre nada. No hay orden, ni stop, ni target, ni tamaño, ni resultado en
R: lo único que produce son marcas sobre el gráfico para que el propietario mire
si la máquina está viendo lo que ve él. Las entradas vienen después, y sólo
cuando estas señales estén auditadas.

**La cascada son dos escalones, no tres.** El Diario no es un paso: no hay que
tocar el OB diario para poder mirar H4, y una búsqueda no «baja» del Diario a H4.
Lo que hace el Diario es prohibir, y sólo eso.

1. **El precio toca el OB de un ID de H4.** Basta con tocarlo: es un asunto de
   mechas y no se espera al cierre de la vela de H4, así que la señal se fecha en
   la vela fina en la que el precio entró. Es el `TOQUE_OB` que la fase 2.0 ya
   calcula y el propietario ya ha auditado; aquí no se vuelve a definir.
   → **BUSCAR_H1**: se abre la ventana de búsqueda en H1.

2. **Al tocar el OB de H4 se baja a H1**, y ahí confirma lo primero que aparezca
   de estas dos, que no son intercambiables sino dos formas distintas de ver lo
   mismo:
   - **turtle soup** (`domain/entries/turtle_soup.py`): dos velas, la segunda va a
     buscar la mecha de la primera y cierra sin superarla;
   - **OB de H1** (`domain/entries/hourly_order_block.py`): la vela verde que
     supera con mecha el máximo de la roja anterior, con dos oportunidades.
   → **CONFIRMA_TURTLE** o **CONFIRMA_OB_H1**. Empate en la misma vela: manda el
   turtle soup. **Una confirmación por ventana y ni una más**: la primera cierra
   la búsqueda, y lo que llegue después ya no se marca aunque el motor lo vea.
   La vía del OB puede además cerrarse sola cuando gasta sus dos oportunidades
   sin superar el nivel, y eso se marca (**OB_H1_DESECHADO**) porque el
   propietario quiere ver dónde miró la máquina y no le valió.

**El veto del Diario.** Mientras el precio esté dentro del OB de un ID diario
**no se mira ninguna confirmación que vaya en contra de él**: en el OB de un ID
diario alcista, todo toque bajista de H4 se desecha hasta que el precio salga de
esa zona. No es que valga menos, es que no se mira. El tramo del veto se marca en
el gráfico diario (**ZONA_DIARIA**) para que se vea de dónde viene cada descarte,
y arranca en el `TOQUE_OB` diario porque el precio no está dentro de la zona
hasta que entra en ella. Fuera de esos tramos, **todo toque de H4 vale, alcista o
bajista**: el Diario no autoriza nada, sólo prohíbe.

**Hasta cuándo se busca: mientras el precio no abandone la zona.** Lo decide el
propietario y es lo que ata la búsqueda al sitio en el que empezó. La ventana se
abre en el instante del toque y se cierra en la primera vela **de la temporalidad
de esa zona** que cierre fuera de ella —da igual por qué lado: irse hacia arriba
es quedarse sin entrada, e irse hacia abajo es romper el OB—, o con la muerte del
ID si llega antes. Cada visita nueva a la zona vuelve a armarla, porque un
`TOQUE_OB` exige venir de fuera y por tanto hay uno por visita. El mismo criterio
mide las dos cosas: la ventana de H4 que busca en H1 y el tramo diario que veta,
que por tanto se levanta con el cierre de una vela **diaria** fuera de la zona y
no antes.

**Causalidad.** Que la ventana se cierre en una vela futura no adelanta nada: para
saber si sigue abierta en el instante `t` sólo hacen falta los cierres anteriores
a `t`, que es exactamente lo que se comprueba. Las velas de H1 que se miran son
las que cierran después del toque de H4, y la vela de referencia del OB de H1
—la que trajo el precio— puede ser anterior, que es su definición.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import pandas as pd

from chronos.application.structure.config import DAILY, H1, H4
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zone_signals import (
    ImpulseSignal,
    ZoneSignalsRun,
    detect_zone_signals,
)
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.entries.hourly_order_block import HourlyOrderBlock, find_hourly_order_block
from chronos.domain.entries.turtle_soup import TurtleSoup, find_turtle_soup
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import CandleSeries, Zone


class CascadeStep(StrEnum):
    """Los pasos que la cascada marca. Los valores salen al explorador."""

    #: Tramo en el que el precio está dentro del OB de un ID diario: mientras
    #: dure, nada que vaya en contra de él se mira. No abre ninguna búsqueda.
    ZONA_DIARIA = "ZONA_DIARIA"
    #: Toque de un OB de H4 en contra de una zona diaria vigente. No se mira.
    H4_DESCARTADO = "H4_DESCARTADO"
    #: El precio toca el OB de un ID de H4: se baja a H1.
    BUSCAR_H1 = "BUSCAR_H1"
    #: Confirmación en H1 por turtle soup.
    CONFIRMA_TURTLE = "CONFIRMA_TURTLE"
    #: Confirmación en H1 por OB de H1.
    CONFIRMA_OB_H1 = "CONFIRMA_OB_H1"
    #: El OB de H1 gastó sus dos oportunidades sin superar el nivel.
    OB_H1_DESECHADO = "OB_H1_DESECHADO"


@dataclass(frozen=True, slots=True)
class CascadeMark:
    """Un paso de la cascada, situado en el tiempo y colgado del anterior.

    `parent` es lo que convierte una lista de marcas en un árbol: cada
    confirmación sabe de qué toque de H4 viene, y cada descarte de qué zona
    diaria le prohibió mirar. Sin eso, dos búsquedas solapadas serían
    indistinguibles sobre el gráfico.
    """

    step: CascadeStep
    #: Gráfico en el que se dibuja. No es la temporalidad del ID: la confirmación
    #: de H1 nace de un ID de H4 y se dibuja sobre las velas de H1.
    chart: str
    #: Temporalidad de la vela en la que se midió. El toque va en la vela fina;
    #: lo demás, en la vela de su propio gráfico.
    source: str
    timestamp: datetime
    #: Dónde se planta el marcador.
    price: float
    direction: ImpulseDirection
    #: Temporalidad y número del ID del que cuelga el paso.
    timeframe: str
    id_num: int
    seq: int
    parent: int | None
    #: Nivel al que se refiere el paso: el borde interior de la zona en los
    #: toques, el extremo rechazado en el turtle soup y el máximo a superar en el
    #: OB de H1.
    level: float
    reach: float
    close: float
    #: Bordes de la zona implicada, cuando el paso tiene una.
    low: float | None = None
    high: float | None = None
    #: Hasta cuándo quedó armada la ventana que abre este paso. `None` = seguía
    #: armada al final del histórico.
    window_end: datetime | None = None
    #: OB de H1: cuál de las dos oportunidades confirmó, o cuántas se gastaron.
    attempts: int | None = None
    #: Vela en la que empieza la zona del paso, cuando no es la de la marca: el
    #: OB de H1 se mide sobre su vela de referencia, que queda detrás.
    zone_start: datetime | None = None


@dataclass(frozen=True, slots=True)
class CascadeRun:
    """Todas las marcas de una corrida, en orden cronológico."""

    enabled: bool
    marks: tuple[CascadeMark, ...]

    @property
    def empty(self) -> bool:
        return not self.marks

    def per_chart(self) -> dict[str, tuple[CascadeMark, ...]]:
        """Marcas agrupadas por el gráfico en el que se dibujan."""
        grouped: dict[str, list[CascadeMark]] = {}
        for mark in self.marks:
            grouped.setdefault(mark.chart, []).append(mark)
        return {chart: tuple(items) for chart, items in grouped.items()}

    def counts(self) -> dict[str, int]:
        """Cuántas de cada paso. El comando las enseña, no las calcula."""
        tally = Counter(mark.step.value for mark in self.marks)
        return {step.value: tally.get(step.value, 0) for step in CascadeStep}


@dataclass(frozen=True, slots=True)
class _DailyVeto:
    """Un tramo dentro del OB de un ID diario: prohíbe lo contrario a `direction`."""

    start: datetime
    #: `None` = el precio no llegó a salir de la zona antes del fin del histórico.
    end: datetime | None
    direction: ImpulseDirection
    seq: int

    def forbids(self, moment: datetime, direction: ImpulseDirection) -> bool:
        """Cubre `[start, end)`: al cerrarse el tramo ya no prohíbe nada."""
        if direction is self.direction:
            return False
        return moment >= self.start and (self.end is None or moment < self.end)


def detect_cascade(run: ImpulseRun, zones: ZonesRun | None) -> CascadeRun:
    """La cascada de una corrida. Sin zonas no hay toques y no hay nada que buscar.

    Hacen falta el ID de H4 y las velas de H1: son los dos escalones. El Diario
    sólo aporta vetos, así que si el reparto de gráficos no lo trae la cascada
    corre igual y no prohíbe nada.
    """
    if zones is None or not zones.enabled:
        return CascadeRun(enabled=False, marks=())
    if H4 not in run.analyses or H1 not in run.chart_bars:
        return CascadeRun(enabled=False, marks=())

    signals = detect_zone_signals(run, zones)
    builder = _Cascade(run, zones, signals)
    return CascadeRun(enabled=True, marks=builder.build())


class _Cascade:
    """El recorrido de la cascada. Toda la lectura de series vive aquí."""

    def __init__(self, run: ImpulseRun, zones: ZonesRun, signals: ZoneSignalsRun) -> None:
        self._series = {
            H4: CandleSeries.of(run.analyses[H4].bars),
            H1: CandleSeries.of(run.chart_bars[H1]),
        }
        if DAILY in run.analyses:
            self._series[DAILY] = CandleSeries.of(run.analyses[DAILY].bars)
        self._zoned = {
            timeframe: {item.id_num: item for item in measurement.items}
            for timeframe, measurement in zones.per_timeframe.items()
        }
        self._touches = {
            timeframe: tuple(
                item
                for item in signals.per_timeframe[timeframe].items
                if item.kind is ZoneSignalKind.TOQUE_OB
            )
            if timeframe in signals.per_timeframe and timeframe in self._series
            else ()
            for timeframe in (DAILY, H4)
        }
        self._marks: list[CascadeMark] = []
        self._seq = 0

    # --- Recorrido ----------------------------------------------------------

    def build(self) -> tuple[CascadeMark, ...]:
        vetoes = self._daily_vetoes()
        for touch in self._touches[H4]:
            zoned = self._zoned.get(H4, {}).get(touch.id_num)
            if zoned is None or zoned.order_block is None:
                continue
            veto = self._veto_over(vetoes, touch)
            if veto is not None:
                # La regla del propietario: dentro de una zona diaria no se mira
                # nada que vaya en contra de ella, se vea lo que se vea en H4.
                self._mark(
                    step=CascadeStep.H4_DESCARTADO,
                    chart=H4,
                    touch=touch,
                    zone=zoned.order_block,
                    window_end=None,
                    parent=veto.seq,
                )
                continue
            self._hourly_window(touch, zoned, zoned.order_block)
        self._marks.sort(key=lambda mark: (mark.timestamp, mark.seq))
        return tuple(self._marks)

    def _daily_vetoes(self) -> tuple[_DailyVeto, ...]:
        """Los tramos en los que el precio está dentro del OB de un ID diario.

        Se marcan todos, hayan llegado a vetar algo o no: el propietario audita
        dónde estaba prohibido mirar, no sólo dónde se prohibió de hecho.
        """
        vetoes: list[_DailyVeto] = []
        for touch in self._touches[DAILY]:
            zoned = self._zoned.get(DAILY, {}).get(touch.id_num)
            if zoned is None or zoned.order_block is None:
                continue
            end = self._window_end(DAILY, zoned, zoned.order_block, touch.timestamp)
            seq = self._mark(
                step=CascadeStep.ZONA_DIARIA,
                chart=DAILY,
                touch=touch,
                zone=zoned.order_block,
                window_end=end,
                parent=None,
            )
            vetoes.append(
                _DailyVeto(
                    start=touch.timestamp, end=end, direction=touch.direction, seq=seq
                )
            )
        return tuple(vetoes)

    @staticmethod
    def _veto_over(
        vetoes: tuple[_DailyVeto, ...], touch: ImpulseSignal
    ) -> _DailyVeto | None:
        """El primer tramo diario que prohíbe este toque, o `None` si ninguno.

        Con dos tramos solapados basta uno en contra: la prohibición no se vota.
        """
        for veto in vetoes:
            if veto.forbids(touch.timestamp, touch.direction):
                return veto
        return None

    def _hourly_window(self, touch: ImpulseSignal, zoned: ImpulseZones, zone: Zone) -> None:
        """Se abre la ventana del toque de H4 y se busca la confirmación en H1."""
        end = self._window_end(H4, zoned, zone, touch.timestamp)
        origin = self._mark(
            step=CascadeStep.BUSCAR_H1,
            chart=H4,
            touch=touch,
            zone=zone,
            window_end=end,
            parent=None,
        )
        span = self._hourly_span(touch.timestamp, end)
        if span is None:
            return
        first, last = span
        hourly = self._series[H1]

        turtle = _first_turtle_soup(hourly, first, last, touch.direction)
        block = find_hourly_order_block(
            hourly, first=first, last=last, direction=touch.direction
        )
        confirmation = _winner(turtle, block)

        # El descarte del OB sólo se enseña si llegó a ocurrir: una confirmación
        # anterior cierra la búsqueda y con ella la vía del OB, que entonces no
        # se ha desechado sino que se ha quedado sin turno.
        if (
            block is not None
            and block.exhausted
            and block.index_second_attempt is not None
            and (confirmation is None or block.index_second_attempt <= confirmation)
        ):
            self._hourly_mark(
                step=CascadeStep.OB_H1_DESECHADO,
                index=block.index_second_attempt,
                direction=touch.direction,
                zoned=zoned,
                parent=origin,
                level=block.level,
                low=block.low,
                high=block.high,
                attempts=2,
                zone_start=hourly.at(block.index),
            )
        if confirmation is None:
            return
        if turtle is not None and turtle.index == confirmation:
            self._hourly_mark(
                step=CascadeStep.CONFIRMA_TURTLE,
                index=turtle.index,
                direction=touch.direction,
                zoned=zoned,
                parent=origin,
                level=turtle.extreme,
                zone_start=hourly.at(turtle.index_first),
            )
            return
        if block is not None and block.index_confirmation == confirmation:
            self._hourly_mark(
                step=CascadeStep.CONFIRMA_OB_H1,
                index=block.index_confirmation,
                direction=touch.direction,
                zoned=zoned,
                parent=origin,
                level=block.level,
                low=block.low,
                high=block.high,
                attempts=block.attempt,
                zone_start=hourly.at(block.index),
            )

    # --- Ventanas -----------------------------------------------------------

    def _window_end(
        self,
        timeframe: str,
        zoned: ImpulseZones,
        zone: Zone,
        touch: datetime,
    ) -> datetime | None:
        """Cuándo deja de estar armada la ventana que abre este toque.

        Es el **cierre** de la primera vela de `timeframe` que, desde la del
        toque en adelante, cierra fuera de la zona; o el de la última vela con el
        ID vivo si nunca la abandona. `None` cuando el histórico se acaba antes,
        que es la única forma de que la ventana siga abierta.
        """
        series = self._series[timeframe]
        limit = zoned.index_end if zoned.index_end is not None else len(series) - 1
        start = _bar_of(series, touch)
        for index in range(max(start, 0), limit + 1):
            if not zone.contains(float(series.close[index])):
                return _closes_at(series, index)
        return _closes_at(series, limit)

    def _hourly_span(self, touch: datetime, end: datetime | None) -> tuple[int, int] | None:
        """Las velas de H1 que caen en la ventana, en posiciones de la serie.

        Arranca en la vela que contiene el instante del toque —todavía sin
        cerrar, y por eso es la primera que puede confirmar— y termina en la
        última que cierra dentro de la ventana.
        """
        series = self._series[H1]
        first = max(_bar_of(series, touch), 0)
        if first >= len(series):
            return None
        if end is None:
            last = len(series) - 1
        else:
            last = int(series.timestamps.searchsorted(pd.Timestamp(end), side="left")) - 1
        if last < first:
            return None
        return first, min(last, len(series) - 1)

    # --- Marcas -------------------------------------------------------------

    def _mark(
        self,
        *,
        step: CascadeStep,
        chart: str,
        touch: ImpulseSignal,
        zone: Zone,
        window_end: datetime | None,
        parent: int | None,
    ) -> int:
        """Una marca hecha sobre un toque de zona ya medido por la fase 2.0."""
        self._seq += 1
        self._marks.append(
            CascadeMark(
                step=step,
                chart=chart,
                source=touch.source or touch.timeframe,
                timestamp=touch.timestamp,
                price=touch.signal.touch,
                direction=touch.direction,
                timeframe=touch.timeframe,
                id_num=touch.id_num,
                seq=self._seq,
                parent=parent,
                level=touch.signal.level,
                reach=touch.signal.reach,
                close=touch.signal.close,
                low=zone.low,
                high=zone.high,
                window_end=window_end,
            )
        )
        return self._seq

    def _hourly_mark(
        self,
        *,
        step: CascadeStep,
        index: int,
        direction: ImpulseDirection,
        zoned: ImpulseZones,
        parent: int,
        level: float,
        low: float | None = None,
        high: float | None = None,
        attempts: int | None = None,
        zone_start: datetime | None = None,
    ) -> int:
        """Una marca sobre una vela de H1. El ID del que cuelga sigue siendo el de H4."""
        series = self._series[H1]
        self._seq += 1
        self._marks.append(
            CascadeMark(
                step=step,
                chart=H1,
                source=H1,
                timestamp=series.at(index),
                price=float(series.close[index]),
                direction=direction,
                timeframe=zoned.timeframe,
                id_num=zoned.id_num,
                seq=self._seq,
                parent=parent,
                level=level,
                reach=series.wick_tip_towards(index, direction),
                close=float(series.close[index]),
                low=low,
                high=high,
                attempts=attempts,
                zone_start=zone_start,
            )
        )
        return self._seq


def _first_turtle_soup(
    series: CandleSeries, first: int, last: int, direction: ImpulseDirection
) -> TurtleSoup | None:
    """El primer turtle soup del tramo. Su primera vela puede ser anterior a `first`."""
    for index in range(max(first, 1), last + 1):
        found = find_turtle_soup(series, index, direction)
        if found is not None:
            return found
    return None


def _winner(turtle: TurtleSoup | None, block: HourlyOrderBlock | None) -> int | None:
    """La vela en la que confirma la cascada, o `None` si no confirma.

    Las dos vías se miden en la misma serie, así que gana la de índice menor.
    Con las dos en la misma vela manda el turtle soup: es **parámetro abierto**,
    coincidieron en 209 de 1.508 casos la última vez que se midió y no cambió
    ninguna operación.
    """
    candidates = [
        index
        for index in (
            turtle.index if turtle is not None else None,
            block.index_confirmation if block is not None else None,
        )
        if index is not None
    ]
    return min(candidates) if candidates else None


def _bar_of(series: CandleSeries, moment: datetime) -> int:
    """Posición de la vela que **contiene** ese instante.

    Las velas van fechadas en su apertura, así que la que contiene un instante es
    la última cuya apertura no lo supera. Sale `-1` para un instante anterior a la
    primera vela, y quien llama lo recorta.
    """
    return int(series.timestamps.searchsorted(pd.Timestamp(moment), side="right")) - 1


def _closes_at(series: CandleSeries, index: int) -> datetime | None:
    """Cuándo cierra la vela `index`: la apertura de la siguiente.

    `None` en la última del histórico: no hay siguiente vela que lo diga, y
    fabricar el cierre sumando la duración nominal mentiría en el fin de semana.
    """
    if index + 1 >= len(series):
        return None
    return series.at(index + 1)


__all__ = ["CascadeMark", "CascadeRun", "CascadeStep", "detect_cascade"]
