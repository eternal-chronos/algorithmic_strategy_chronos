"""La cascada H4 → H1, con el Diario de **veto**. Todavía sólo señales.

Esto no abre nada. No hay orden, ni stop, ni target, ni tamaño, ni resultado en
R: lo único que produce son marcas sobre el gráfico para que el propietario mire
si la máquina está viendo lo que ve él. Las entradas vienen después, y sólo
cuando estas señales estén auditadas.

**La cascada son dos escalones, no tres.** El Diario no es un paso: no hay que
tocar el PUL diario para poder mirar H4, y una búsqueda no «baja» del Diario a H4.
Lo que hace el Diario es prohibir, y sólo eso.

1. **El precio toca el PUL de un ID de H4.** Basta con tocarlo: es un asunto de
   mechas y no se espera al cierre de la vela de H4, así que la señal se fecha en
   la vela fina en la que el precio entró. Es el `TOQUE_PUL` que la fase 2.0 ya
   calcula y el propietario ya ha auditado; aquí no se vuelve a definir.
   → **BUSCAR_H1**: se abre la ventana de búsqueda en H1.

2. **Al tocar el PUL de H4 se baja a H1 y se espera a que el ID de H1 se ponga en
   la dirección del de H4.** No se busca ningún patrón de velas: en H1 hay ID
   propio —el mismo detector del Diario y de H4, con las mismas reglas— y lo que
   se espera es justo eso, que el ID vigente en H1 deje de ir en contra. Con el
   PUL de un ID **alcista** de H4 tocado, si en H1 manda un ID bajista hay que
   esperar a que se rompa y se constituya el alcista; y en cuanto ese ID de H1
   existe, su PUL es el que se marca.
   → **CONFIRMA_PUL_H1**, con la zona del PUL de ese ID de H1.

   Da igual que el ID de H1 ya estuviera alineado al llegar el toque: lo que se
   pide es que lo esté, no que gire. Lo que sí hace falta es el PUL, porque es lo
   que se marca: un ID de H1 alineado sin PUL —sólo el primero del histórico— no
   señala nada y la espera sigue con el siguiente. **Una confirmación por ventana
   y ni una más.**

3. **El precio toca ese PUL de H1 y ahí salta la señal.** Es el mismo `TOQUE_PUL`
   de la fase 2.0 que ya se cobra en H4 y en el Diario, leído sobre el PUL de H1 y
   sin redefinir nada: basta con que el rango entre en la zona viniendo de fuera.
   **No se espera al cierre de la vela de H1**, igual que no se espera al de la
   de H4 ni al de la diaria: la señal se fecha en la vela fina —M15 en el reparto
   por defecto— en la que el precio entró en la zona, con los números de esa vela.
   → **TOQUE_PUL_H1**, que es la señal que cierra la cascada.

   **Se espera mientras dure la ventana de H4**, y ni un minuto más. Cerrada la
   búsqueda, el PUL de H1 que estaba marcado deja de valer: no hay señal tardía
   que salte cuando el movimiento que la justificaba ya pasó. Muerto el ID de H1
   muere además su PUL, así que manda la que llegue antes. **Un toque por
   confirmación y ni uno más.**

**El veto del Diario.** Mientras el precio esté dentro del PUL de un ID diario
**no se mira ninguna confirmación que vaya en contra de él**: en el PUL de un ID
diario alcista, todo toque bajista de H4 se desecha hasta que el precio salga de
esa zona. No es que valga menos, es que no se mira. El tramo del veto se marca en
el gráfico diario (**ZONA_DIARIA**) para que se vea de dónde viene cada descarte,
y arranca en el `TOQUE_PUL` diario porque el precio no está dentro de la zona
hasta que entra en ella. Fuera de esos tramos, **todo toque de H4 vale, alcista o
bajista**: el Diario no autoriza nada, sólo prohíbe.

**Hasta cuándo se busca: hasta romper el PUL o hasta llegar al UL.** Lo decide el
propietario. La ventana se abre en el instante del toque y se cierra con lo
primero de estas tres cosas:

1. **se rompe el PUL** — una vela de H4 cierra más allá de su borde **exterior**,
   es decir lo atraviesa entero. Por ahí abajo se acabó: la zona que mandó buscar
   ya no está;
2. **el precio llega al UL** del mismo ID de H4, y basta con **tocarlo**: en
   cuanto el rango entra en la zona, sin esperar al cierre de la vela de H4, igual
   que el toque del PUL. El UL es el extremo del impulso, o sea el sitio al que se
   iba: llegar ahí es haberse quedado sin viaje, y es lo que impide seguir
   buscando indefinidamente cuando H1 no llega a girar o gira tarde;
3. **muere el ID de H4**, si llega antes que las dos anteriores.

**Salir del PUL hacia arriba ya no cierra nada.** El precio puede abandonar la zona
a favor y la búsqueda sigue viva hasta el UL, que es lo que hace falta para que el
segundo escalón tenga sitio donde ocurrir. Cada visita nueva a la zona vuelve a
armar la búsqueda —un `TOQUE_PUL` exige venir de fuera, así que hay uno por visita—
y **la búsqueda anterior queda anulada**: si al volver no ha nacido ningún ID de
H1 nuevo se vuelve a coger el mismo, con su mismo PUL.

El UL que se mira es el que se dibuja, el de la vela del extremo con la que el ID
se constituyó, y no se remarca aunque el extremo se estire después. Por eso se
conoce entero desde que nace el ID y mirarlo no adelanta nada.

**El tramo diario del veto se mide distinto, y es a propósito**: ahí la pregunta
no es hasta cuándo se busca sino mientras el precio esté **dentro** del PUL diario,
así que ese tramo se levanta con el cierre de una vela **diaria** fuera de la
zona, por el lado que sea.

**El UL de H1 no interviene.** El ID de H1 trae sus dos zonas, como cualquier
otro, y las dos se dibujan; pero la cascada sólo lee el PUL. Lo que decide en H1
es la dirección del ID y la existencia de su PUL, nada más.

**La franja de operativa: de 03:00 a 12:00 de Nueva York.** Es Londres y la
primera mitad de Nueva York, y **fuera de ella la cascada no hace nada**: un
toque del PUL de H4 de madrugada no abre búsqueda —ni se dibuja, ni siquiera como
descarte—, un ID de H1 que se alinea de noche no marca su PUL hasta que la franja
vuelve a abrir, y un toque del PUL de H1 fuera de hora no es señal. La hora es la
de la plaza, con horario de verano real, no un desfase fijo en UTC.

Lo único que sigue corriendo a todas horas es el **veto del Diario**: prohibir no
es operar, así que su tramo se levanta con la vela diaria que lo cierra, caiga a
la hora que caiga, y sigue tapando los toques de H4 que vayan en contra.

Las ventanas de búsqueda tampoco entienden de horas: se abren con un toque que sí
está en franja y se cierran por precio —rotura del PUL, toque del UL o muerte del
ID—, aunque eso ocurra de noche. Lo que la franja decide es qué se mira, no
cuánto dura lo que ya se estaba mirando.

**Causalidad.** Que la ventana se cierre en una vela futura no adelanta nada: para
saber si sigue abierta en el instante `t` sólo hacen falta los cierres anteriores
a `t`, que es exactamente lo que se comprueba. Y el PUL de H1 se marca en la vela
de la constitución de su ID, nunca antes; su vela definitoria, la del extremo del
ID anterior, queda detrás, que es su definición. Y el toque de ese PUL llega
todavía más tarde: la fase 2.0 sólo lo busca a partir de la vela **siguiente** al
nacimiento de la zona, así que cuando el precio entra en ella hace ya una vela de
H1 que se sabía que existía.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from chronos.application.entries.trading_window import TRADING_WINDOW, TradingWindow
from chronos.application.structure.config import DAILY, H1, H4, TIMEFRAME_MINUTES
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zone_signals import (
    ImpulseSignal,
    ZoneSignalsRun,
    detect_zone_signals,
)
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind


class CascadeStep(StrEnum):
    """Los pasos que la cascada marca. Los valores salen al explorador."""

    #: Tramo en el que el precio está dentro del PUL de un ID diario: mientras
    #: dure, nada que vaya en contra de él se mira. No abre ninguna búsqueda.
    ZONA_DIARIA = "ZONA_DIARIA"
    #: Toque de un PUL de H4 en contra de una zona diaria vigente. No se mira.
    H4_DESCARTADO = "H4_DESCARTADO"
    #: El precio toca el PUL de un ID de H4: se baja a H1.
    BUSCAR_H1 = "BUSCAR_H1"
    #: El ID de H1 va ya en la dirección del de H4 y tiene PUL: se marca ese PUL.
    CONFIRMA_PUL_H1 = "CONFIRMA_PUL_H1"
    #: El precio toca ese PUL de H1: la señal, en el instante del toque.
    TOQUE_PUL_H1 = "TOQUE_PUL_H1"


class WindowEnd(StrEnum):
    """Por qué se cerró una ventana. Se dibuja: es la regla que se audita."""

    #: Una vela de H4 cerró más allá del borde exterior del PUL: lo atravesó.
    ROTURA_PUL = "ROTURA_PUL"
    #: El precio llegó al UL del ID de H4, que es el sitio al que se iba.
    TOQUE_UL = "TOQUE_UL"
    #: Se acabó el ID antes que las otras dos.
    MUERTE_ID = "MUERTE_ID"
    #: Sólo del tramo diario: una vela diaria cerró fuera de la zona.
    SALIDA_ZONA = "SALIDA_ZONA"


@dataclass(frozen=True, slots=True)
class CascadeMark:
    """Un paso de la cascada, situado en el tiempo y colgado del anterior.

    `parent` es lo que convierte una lista de marcas en un árbol: cada
    confirmación sabe de qué toque de H4 viene, y cada descarte de qué zona
    diaria le prohibió mirar. Sin eso, dos búsquedas solapadas serían
    indistinguibles sobre el gráfico.
    """

    step: CascadeStep
    #: Gráfico en el que se dibuja. No siempre es la temporalidad del ID: el
    #: toque de H4 se mide en la vela fina y se dibuja sobre H4.
    chart: str
    #: Temporalidad de la vela en la que se midió. El toque va en la vela fina;
    #: lo demás, en la vela de su propio gráfico.
    source: str
    timestamp: datetime
    #: Dónde se planta el marcador.
    price: float
    direction: ImpulseDirection
    #: Temporalidad y número del ID del que cuelga el paso. En la confirmación es
    #: el ID de **H1**, que es el que se ha esperado; de qué toque de H4 viene lo
    #: dice `parent`.
    timeframe: str
    id_num: int
    seq: int
    parent: int | None
    #: Nivel al que se refiere el paso: el borde interior de la zona, que es el
    #: que el precio encuentra primero.
    level: float
    reach: float
    close: float
    #: Bordes de la zona implicada, cuando el paso tiene una.
    low: float | None = None
    high: float | None = None
    #: Hasta cuándo quedó armada la ventana que abre este paso. `None` = seguía
    #: armada al final del histórico.
    window_end: datetime | None = None
    #: Qué la cerró. `None` cuando el histórico se acabó antes.
    window_reason: WindowEnd | None = None
    #: Vela en la que empieza la zona del paso, cuando no es la de la marca: el
    #: PUL de H1 se dibuja sobre la vela del extremo del ID anterior al suyo, que
    #: queda detrás.
    zone_start: datetime | None = None
    #: Qué zona es la del paso —hoy siempre `PUL` o `UL`—. Viaja con la marca en
    #: vez de escribirse en el nombre para que el dibujo no tenga que suponerla.
    #: `None` en los pasos que no cuelgan de ninguna zona.
    zone_kind: ZoneKind | None = None


@dataclass(frozen=True, slots=True)
class CascadeRun:
    """Todas las marcas de una corrida, en orden cronológico."""

    enabled: bool
    marks: tuple[CascadeMark, ...]
    #: La franja en la que se ha operado. Viaja con la corrida porque sin ella
    #: los recuentos no se pueden leer: un gráfico sin marcas de madrugada no es
    #: que no hubiera nada, es que ahí no se mira.
    window: TradingWindow = TRADING_WINDOW

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
    """Un tramo dentro del PUL de un ID diario: prohíbe lo contrario a `direction`."""

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


def detect_cascade(
    run: ImpulseRun, zones: ZonesRun | None, window: TradingWindow = TRADING_WINDOW
) -> CascadeRun:
    """La cascada de una corrida. Sin zonas no hay toques y no hay nada que buscar.

    Hacen falta los **dos ID**, el de H4 y el de H1: son los dos escalones, y el
    de H1 es estructura de verdad, no un patrón de velas. Sin detector en H1 —el
    reparto por defecto de las fases 1 y 2— no hay cascada que calcular: la fase
    3.0 lo enciende con `with_hourly_structure`. El Diario sólo aporta vetos, así
    que si el reparto no lo trae la cascada corre igual y no prohíbe nada.

    `window` es la franja de operativa: fuera de ella no se busca ni se señala.
    """
    if zones is None or not zones.enabled:
        return CascadeRun(enabled=False, marks=(), window=window)
    if H4 not in run.analyses or H1 not in run.analyses:
        return CascadeRun(enabled=False, marks=(), window=window)
    if H4 not in zones.per_timeframe or H1 not in zones.per_timeframe:
        return CascadeRun(enabled=False, marks=(), window=window)

    signals = detect_zone_signals(run, zones)
    builder = _Cascade(run, zones, signals, window)
    return CascadeRun(enabled=True, marks=builder.build(), window=window)


class _Cascade:
    """El recorrido de la cascada. Toda la lectura de series vive aquí."""

    def __init__(
        self,
        run: ImpulseRun,
        zones: ZonesRun,
        signals: ZoneSignalsRun,
        window: TradingWindow,
    ) -> None:
        self._window = window
        self._series = {
            H4: CandleSeries.of(run.analyses[H4].bars),
            H1: CandleSeries.of(run.analyses[H1].bars),
        }
        if DAILY in run.analyses:
            self._series[DAILY] = CandleSeries.of(run.analyses[DAILY].bars)
        #: La serie de vela más corta de la corrida —M15 en el reparto por
        #: defecto—: es donde se mide el toque del UL, que no espera al cierre de
        #: la vela de H4 igual que no lo espera el del PUL. Si el reparto no trae
        #: ninguna más fina, se mide en la del propio ID y funciona igual.
        self._fine = _finest(run, self._series[H4])
        self._zoned = {
            timeframe: {item.id_num: item for item in measurement.items}
            for timeframe, measurement in zones.per_timeframe.items()
        }
        #: Los ID de H1 en orden cronológico: es la fila en la que se espera.
        self._hourly_ids = zones.per_timeframe[H1].items
        self._touches = {
            timeframe: self._touches_of(signals, timeframe)
            for timeframe in (DAILY, H4)
        }
        #: Los toques del PUL de H1 por ID: la señal que cierra la cascada. Es el
        #: mismo `TOQUE_PUL` de la fase 2.0, ya fechado en la vela fina.
        self._hourly_touches: dict[int, list[ImpulseSignal]] = {}
        for touch in self._touches_of(signals, H1):
            self._hourly_touches.setdefault(touch.id_num, []).append(touch)
        #: Qué velas de H1 caen dentro de la franja de operativa. Se resuelve de
        #: una vez sobre la serie entera: la confirmación se busca vela a vela y
        #: preguntar la hora en cada una sería un bucle Python en el camino.
        self._hourly_in_window = window.mask(self._series[H1].timestamps)
        self._marks: list[CascadeMark] = []
        self._seq = 0

    def _touches_of(self, signals: ZoneSignalsRun, timeframe: str) -> tuple[ImpulseSignal, ...]:
        """Los `TOQUE_PUL` de una temporalidad, en orden. Vacío si no se dibuja."""
        if timeframe not in signals.per_timeframe or timeframe not in self._series:
            return ()
        return tuple(
            item
            for item in signals.per_timeframe[timeframe].items
            if item.kind is ZoneSignalKind.TOQUE_PUL
        )

    # --- Recorrido ----------------------------------------------------------

    def build(self) -> tuple[CascadeMark, ...]:
        vetoes = self._daily_vetoes()
        for touch in self._touches[H4]:
            zoned = self._zoned.get(H4, {}).get(touch.id_num)
            against = None if zoned is None else zoned.against
            if zoned is None or against is None:
                continue
            if not self._window.contains(touch.timestamp):
                # Fuera de la franja no se mira nada, así que no hay ni descarte
                # que dibujar: lo que no se mira no deja marca.
                continue
            veto = self._veto_over(vetoes, touch)
            if veto is not None:
                # La regla del propietario: dentro de una zona diaria no se mira
                # nada que vaya en contra de ella, se vea lo que se vea en H4.
                self._mark(
                    step=CascadeStep.H4_DESCARTADO,
                    chart=H4,
                    touch=touch,
                    zone=against,
                    window_end=None,
                    parent=veto.seq,
                )
                continue
            self._hourly_window(touch, zoned, against)
        self._marks.sort(key=lambda mark: (mark.timestamp, mark.seq))
        return tuple(self._marks)

    def _daily_vetoes(self) -> tuple[_DailyVeto, ...]:
        """Los tramos en los que el precio está dentro del PUL de un ID diario.

        Se marcan todos, hayan llegado a vetar algo o no: el propietario audita
        dónde estaba prohibido mirar, no sólo dónde se prohibió de hecho.
        """
        vetoes: list[_DailyVeto] = []
        for touch in self._touches[DAILY]:
            zoned = self._zoned.get(DAILY, {}).get(touch.id_num)
            against = None if zoned is None else zoned.against
            if zoned is None or against is None:
                continue
            end, why = self._veto_end(zoned, against, touch.timestamp)
            seq = self._mark(
                step=CascadeStep.ZONA_DIARIA,
                chart=DAILY,
                touch=touch,
                zone=against,
                window_end=end,
                window_reason=why,
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
        """Se abre la ventana del toque de H4 y se espera al ID de H1 que confirma."""
        end, why = self._search_end(zoned, zone, touch.timestamp)
        origin = self._mark(
            step=CascadeStep.BUSCAR_H1,
            chart=H4,
            touch=touch,
            zone=zone,
            window_end=end,
            window_reason=why,
            parent=None,
        )
        span = self._hourly_span(touch.timestamp, end)
        if span is None:
            return
        found = self._aligned_hourly_id(span[0], span[1], touch.direction)
        if found is None:
            return
        index, hourly, block = found
        confirmed = self._hourly_mark(
            step=CascadeStep.CONFIRMA_PUL_H1,
            index=index,
            direction=touch.direction,
            zoned=hourly,
            parent=origin,
            level=block.inner,
            low=block.low,
            high=block.high,
            zone_start=block.ts_defining,
            zone_kind=block.kind,
        )
        self._hourly_touch(
            parent=confirmed,
            id_num=hourly.id_num,
            zone=block,
            # Ni antes de que se marcara el PUL ni antes de que hubiera nada que
            # buscar: el toque que cuenta es el primero que llega después de las
            # dos cosas.
            since=max(self._series[H1].at(index), touch.timestamp),
            until=end,
        )

    def _hourly_touch(
        self, *, parent: int, id_num: int, zone: Zone, since: datetime, until: datetime | None
    ) -> None:
        """El primer toque del PUL de ese ID de H1 dentro de `[since, until)`.

        Por abajo lo ata el PUL —no se toca lo que todavía no se había marcado— y
        por arriba, la ventana de H4: cerrada la búsqueda no hay señal, aunque el
        ID de H1 siga vivo y su zona siga en el gráfico. Los toques que la fase
        2.0 calcula ya mueren con el ID de H1, así que de las dos manda la que
        llegue antes. Y no se espera al cierre de la vela de H1: el toque ya viene
        fechado en la vela fina en la que el precio entró en la zona.

        Y por la hora lo ata la franja de operativa: un toque de madrugada no es
        señal, y se sigue esperando al primero que llegue con la franja abierta.

        `until` es `None` sólo cuando el histórico se acabó con la ventana abierta.
        """
        for touch in self._hourly_touches.get(id_num, ()):
            if touch.timestamp < since:
                continue
            if until is not None and touch.timestamp >= until:
                return
            if not self._window.contains(touch.timestamp):
                # Fuera de hora no se opera: ese toque no es señal y tampoco
                # gasta la confirmación. Si el precio sale y vuelve a entrar ya
                # con la franja abierta, ése sí cuenta.
                continue
            self._mark(
                step=CascadeStep.TOQUE_PUL_H1,
                chart=H1,
                touch=touch,
                zone=zone,
                window_end=None,
                parent=parent,
            )
            return

    def _aligned_hourly_id(
        self, first: int, last: int, direction: ImpulseDirection
    ) -> tuple[int, ImpulseZones, Zone] | None:
        """La primera vela de `[first, last]` con el ID de H1 alineado y con PUL.

        Se recorren los ID de H1 en orden y se devuelve el primero que va en
        `direction` y está vivo dentro del tramo, junto con la vela en la que se
        supo —la de su constitución— o la primera del tramo si quedó detrás, que
        es el caso de un ID de H1 que ya venía alineado cuando el precio tocó la
        zona de H4.

        La vela que se devuelve está además **dentro de la franja de operativa**:
        lo que la máquina ve fuera de hora no se marca ni se opera.

        `None` cuando en toda la ventana no manda ningún ID de H1 en esa
        dirección con PUL y en franja: no hay nada que marcar.
        """
        series = self._series[H1]
        for zoned in self._hourly_ids:
            block = zoned.against
            if zoned.direction is not direction or block is None:
                continue
            # El PUL nace con el ID: su vela ya estaba cerrada antes.
            known = zoned.index_constitution
            death = zoned.index_end if zoned.index_end is not None else len(series) - 1
            index = self._first_in_window(max(known, first), min(death, last))
            if index is not None:
                return index, zoned, block
        return None

    def _first_in_window(self, first: int, last: int) -> int | None:
        """La primera vela de H1 de `[first, last]` dentro de la franja de operativa.

        Un ID de H1 que se alinea de madrugada no marca su PUL hasta que la franja
        vuelve a abrir, y si no vuelve a abrir mientras el ID está vivo y la
        búsqueda armada, no lo marca: `None`, y se sigue con el ID siguiente.
        """
        if last < first:
            return None
        hits = np.flatnonzero(self._hourly_in_window[first : last + 1])
        return first + int(hits[0]) if hits.size else None

    # --- Ventanas -----------------------------------------------------------

    def _veto_end(
        self, zoned: ImpulseZones, zone: Zone, touch: datetime
    ) -> tuple[datetime | None, WindowEnd | None]:
        """Cuándo deja de estar el precio dentro del PUL diario, y por tanto el veto.

        Es el **cierre** de la primera vela diaria que, desde la del toque en
        adelante, cierra fuera de la zona —por el lado que sea, que aquí la
        pregunta es estar dentro o no estarlo—; o el de la última vela con el ID
        vivo si nunca la abandona. `None` cuando el histórico se acaba antes, que
        es la única forma de que el tramo siga abierto.
        """
        series = self._series[DAILY]
        limit = zoned.index_end if zoned.index_end is not None else len(series) - 1
        start = _bar_of(series, touch)
        for index in range(max(start, 0), limit + 1):
            if not zone.contains(float(series.close[index])):
                return _closes_at(series, index), WindowEnd.SALIDA_ZONA
        return _closes_at(series, limit), WindowEnd.MUERTE_ID

    def _search_end(
        self, zoned: ImpulseZones, zone: Zone, touch: datetime
    ) -> tuple[datetime | None, WindowEnd | None]:
        """Cuándo deja de estar armada la búsqueda que abre este toque del PUL de H4.

        Lo primero de tres: que una vela de H4 cierre más allá del borde
        **exterior** del PUL —romperlo—, que el precio **toque** el UL del mismo
        ID, o que muera el ID. Salir del PUL hacia arriba no cuenta: el precio
        puede irse a favor y la búsqueda sigue viva hasta el UL.

        `None` cuando ninguna de las tres llega antes del fin del histórico.
        """
        series = self._series[H4]
        limit = zoned.index_end if zoned.index_end is not None else len(series) - 1
        start = max(_bar_of(series, touch), 0)
        closing: datetime | None = _closes_at(series, limit)
        why = WindowEnd.MUERTE_ID
        for index in range(start, limit + 1):
            if _beyond(float(series.close[index]), zone.outer, zoned.direction):
                closing, why = _closes_at(series, index), WindowEnd.ROTURA_PUL
                break
        return _earliest(
            (closing, why), (self._reaches_last(zoned, touch, limit), WindowEnd.TOQUE_UL)
        )

    def _reaches_last(
        self, zoned: ImpulseZones, touch: datetime, limit: int
    ) -> datetime | None:
        """Cuándo el precio llega al UL del ID de H4. `None` si no llega nunca.

        Llegar es entrar en la zona, así que se mide contra su borde **interior**
        —la línea del extremo, que es lo primero que el precio encuentra— y con
        mechas, en la vela fina: esperar cuatro horas al cierre de H4 sería seguir
        buscando en un sitio que ya se ha ido. Se mira desde la vela del toque
        inclusive: si una sola vela fina recorre el impulso entero, la búsqueda
        nace cerrada, que es lo prudente.
        """
        series = self._fine
        start = max(_bar_of(series, touch), 0)
        if start >= len(series):
            return None
        death = _closes_at(self._series[H4], limit)
        stop = (
            len(series)
            if death is None
            else int(series.timestamps.searchsorted(pd.Timestamp(death), "left"))
        )
        if stop <= start:
            return None
        inner = zoned.last.inner
        if zoned.direction is ImpulseDirection.ALCISTA:
            hits = np.flatnonzero(series.high[start:stop] >= inner)
        else:
            hits = np.flatnonzero(series.low[start:stop] <= inner)
        return series.at(start + int(hits[0])) if hits.size else None

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
            # La última vela que **cierra** dentro de la ventana. La ventana ya no
            # termina siempre en el cierre de una vela de H4 —el toque del UL la
            # corta a media vela—, así que se cuenta por cierres y no por
            # aperturas: una vela que aún no ha cerrado no ha confirmado nada.
            last = int(series.timestamps.searchsorted(pd.Timestamp(end), side="right")) - 2
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
        window_reason: WindowEnd | None = None,
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
                zone_kind=zone.kind,
                window_end=window_end,
                window_reason=window_reason,
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
        low: float,
        high: float,
        zone_start: datetime,
        zone_kind: ZoneKind,
    ) -> int:
        """Una marca sobre una vela de H1. El ID del que cuelga es el de **H1**."""
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
                zone_start=zone_start,
                zone_kind=zone_kind,
            )
        )
        return self._seq


def _bar_of(series: CandleSeries, moment: datetime) -> int:
    """Posición de la vela que **contiene** ese instante.

    Las velas van fechadas en su apertura, así que la que contiene un instante es
    la última cuya apertura no lo supera. Sale `-1` para un instante anterior a la
    primera vela, y quien llama lo recorta.
    """
    return int(series.timestamps.searchsorted(pd.Timestamp(moment), side="right")) - 1


def _finest(run: ImpulseRun, fallback: CandleSeries) -> CandleSeries:
    """La serie de vela más corta de la corrida, o la del propio ID si no hay otra."""
    charts = [name for name in run.chart_bars if name in TIMEFRAME_MINUTES]
    if not charts:
        return fallback
    finest = min(charts, key=lambda name: TIMEFRAME_MINUTES[name])
    series = CandleSeries.of(run.chart_bars[finest])
    return series if len(series) > len(fallback) else fallback


def _beyond(price: float, outer: float, direction: ImpulseDirection) -> bool:
    """El precio ha dejado la zona atrás por su borde exterior."""
    if direction is ImpulseDirection.ALCISTA:
        return price < outer
    return price > outer


def _earliest(
    first: tuple[datetime | None, WindowEnd | None],
    second: tuple[datetime | None, WindowEnd | None],
) -> tuple[datetime | None, WindowEnd | None]:
    """El cierre que llegue antes, con su motivo.

    Un instante `None` significa «no llega», no «llega ya»: con los dos a `None`
    la ventana seguía armada al acabarse el histórico y no hay motivo que dar.
    """
    if first[0] is None:
        return second
    if second[0] is None:
        return first
    return first if first[0] <= second[0] else second


def _closes_at(series: CandleSeries, index: int) -> datetime | None:
    """Cuándo cierra la vela `index`: la apertura de la siguiente.

    `None` en la última del histórico: no hay siguiente vela que lo diga, y
    fabricar el cierre sumando la duración nominal mentiría en el fin de semana.
    """
    if index + 1 >= len(series):
        return None
    return series.at(index + 1)


__all__ = ["CascadeMark", "CascadeRun", "CascadeStep", "WindowEnd", "detect_cascade"]
