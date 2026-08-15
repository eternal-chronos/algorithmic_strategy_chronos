"""Caso de uso: la cascada de entrada de la fase 3.0 (§1).

    H4 es el motor. El día a día se busca ahí. El Diario es contexto: confirma y
    permite alargar, pero **nunca dispara**. H1 confirma. M15 afina.

Aquí no se detecta ningún impulso ni se calcula ninguna zona: eso ya está hecho y
auditado. Lo que se hace es recorrer lo que la fase 2.1 dejó y decidir cuándo hay
señal, con una regla de oro que atraviesa todo el fichero: **la decisión de una
barra sólo puede usar información cerrada en esa barra** (§7).

Tres cosas que el código hace de una forma concreta y conviene leer antes:

1. **El UL se mueve.** Con la rotura por zona un ID vigente extiende su extremo, y
   cada extensión cambia su UL. La observación usa el UL vigente en cada barra
   —`LastZoneTimeline`—, nunca el último. Preguntar por el último sería mirar al
   futuro justo en las velas que más importan, porque las que extienden el extremo
   son las mismas que tocan la zona.
2. **La rotura de la zona es la muerte del ID.** Con `BREAK_BY_ZONE = true`,
   cerrar más allá del borde exterior del UL *es* la rotura a favor, y más allá
   del OB *es* la rotura en contra. Así que la rotura de la observación no se
   vuelve a calcular: se lee de cómo murió el impulso. Una regla escrita dos veces
   es una regla que se contradice a sí misma en la siguiente refactorización.
3. **La vela que rompe cierra primero.** El retesteo del UL sólo lo valida otra
   vela **de la temporalidad del ID**, posterior a la de la rotura: un toque en
   una temporalidad menor dentro de la misma vela que rompió no vale. La vela que
   atraviesa la zona entera pasa por ella obligatoriamente, así que sin esta
   regla toda rotura vendría con retesteo gratis y la invalidación del §1.2 no
   descartaría nunca nada. La escribe `first_retest_after_break`, una sola vez y
   para las tres temporalidades.
4. **La búsqueda en H1 va cronológica.** Las observaciones se recogen primero y
   después se recorre H1 **una sola vez**, hacia adelante, avanzando la frontera
   de las series causales barra a barra. Recorrer H1 por observación habría hecho
   saltar la frontera hacia atrás y la garantía anti-lookahead habría quedado en
   un adorno.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.entries.clock import BarClock, session_ids
from chronos.application.entries.config import UNTIL_OBSERVATION_ENDS, EntriesConfig
from chronos.application.structure.config import DAILY, H1, H4, M15
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, TimeframeZones, ZonesRun
from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    EntryTimeframe,
    GuardRail,
    RejectionKind,
    StopZone,
)
from chronos.domain.entries.loose_order_block import find_loose_order_block
from chronos.domain.entries.rejection import (
    RejectionMarks,
    RollingWickPercentile,
    mark_rejections,
)
from chronos.domain.entries.signal import (
    Confirmation,
    DailyTouch,
    DiscardedSignal,
    EntryZone,
    Observation,
    Signal,
)
from chronos.domain.entries.zone_timeline import (
    LastZoneTimeline,
    first_retest_after_break,
    touches,
)
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import BreakKind, ImpulseDirection
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind

#: Orden en que se imprime el embudo del §6. Es la cascada leída de arriba abajo.
FUNNEL_STEPS: tuple[str, ...] = (
    "zonas_de_h4",
    "zonas_tocadas",
    "observaciones",
    "confirman_en_h1",
    "entrada_localizada_h1",
    "entrada_localizada_m15",
)


@dataclass(frozen=True, slots=True)
class CascadeRun:
    """Lo que la cascada produce antes de ejecutar nada.

    Las señales todavía no son operaciones: falta la barra M1 siguiente, y ahí se
    caen las que no tienen stop válido o se quedan sin histórico.
    """

    enabled: bool
    config: EntriesConfig
    config_hash: str
    #: Hash de la corrida de estructura sobre la que se montó. Una señal sin la
    #: estructura que la produjo no se puede reproducir.
    structure_hash: str
    daily_touches: tuple[DailyTouch, ...] = ()
    observations: tuple[Observation, ...] = ()
    signals: tuple[Signal, ...] = ()
    discarded: tuple[DiscardedSignal, ...] = ()
    funnel: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def emits_nothing(self) -> bool:
        """`True` con la fase apagada: ni una observación, ni una señal, ni nada."""
        return not self.enabled and not self.observations and not self.signals

    def guard_rail_counts(self) -> dict[str, int]:
        counts = {rail.value: 0 for rail in GuardRail}
        for item in self.discarded:
            counts[item.guard_rail.value] += 1
        return counts


def build_cascade(
    run: ImpulseRun,
    zones: ZonesRun,
    m15_bars: pd.DataFrame | None,
    config: EntriesConfig,
) -> CascadeRun:
    """Recorre la estructura ya detectada y produce las señales de la fase 3.

    Con `enabled: False` no se emite absolutamente nada, ni siquiera un embudo
    vacío: la corrida sale byte a byte como la de la fase 2.1.
    """
    if not config.enabled or not run.enabled or not zones.enabled:
        return CascadeRun(
            enabled=False,
            config=config,
            config_hash=config.fingerprint(),
            structure_hash=run.config_hash,
        )
    if not run.config.rules.break_by_zone:
        raise DomainError(
            "La fase 3 se monta sobre la línea base de la fase 2.1: hace falta "
            "`break_by_zone: true`. Con la rotura por línea las zonas no deciden "
            "nada y la cascada estaría leyendo otra historia."
        )
    return _Cascade(run, zones, m15_bars, config).execute()


def daily_context(
    touched: Sequence[DailyTouch], at: pd.Timestamp, direction: ImpulseDirection
) -> tuple[DailyContext, DailyTouch | None]:
    """§1.1 y §1.2 — contexto diario vigente en `at`, y qué pasa si contradice a H4.

    **Manda H4.** El conflicto se registra en la señal y no la descarta: es una
    afirmación del propietario que el desglose del §5.1 puede desmentir, y para
    desmentirla hacen falta las operaciones en conflicto, no su ausencia.

    El contexto vive mientras vive el ID diario que lo produjo. No es una ventana
    inventada: es la única frontera que el enunciado nombra —"su ID diario
    vigente"— y fuera de ella la zona ya no es de nadie.
    """
    active = [
        item
        for item in touched
        if pd.Timestamp(item.timestamp) <= at
        and (item.ts_expiry is None or at <= pd.Timestamp(item.ts_expiry))
    ]
    if not active:
        return DailyContext.SIN_CONTEXTO, None
    # Sólo hay un ID diario vigente a la vez, así que sus contactos comparten
    # dirección; con los dos —UL y OB— manda el más reciente.
    latest = active[-1]
    if latest.direction is direction:
        return DailyContext.A_FAVOR, latest
    return DailyContext.CONFLICTO, latest


# --- Interno -----------------------------------------------------------------


@dataclass(slots=True)
class _Watch:
    """Una observación en curso, con lo que hace falta para seguirla en H1.

    Vive sólo dentro de la cascada: lo que sale al mundo es `Observation`, que es
    inmutable y no arrastra series ni diccionarios.
    """

    impulse: DominantImpulse
    kind: ZoneKind
    #: Zona vigente en cada barra de H4 mientras la observación estuvo viva. El
    #: OB no se mueve y trae una sola entrada; el UL, una por barra.
    zone_by_bar: dict[int, Zone]
    zone_at_contact: Zone
    index_contact: int
    ts_contact: pd.Timestamp
    daily: DailyContext
    daily_touch: DailyTouch | None
    #: Ventana de búsqueda en H1, en índices de H1 y ya cerrada por los dos lados.
    h1_first: int
    h1_last: int
    #: Cierre de la última barra de esa ventana, que es la frontera del §9.4.
    ts_window_end: datetime
    #: Última barra de H4 de la ventana: hasta ahí llega `zone_by_bar`.
    h4_last: int
    index_break: int | None = None
    ts_break: pd.Timestamp | None = None
    index_retest: int | None = None
    ts_retest: pd.Timestamp | None = None
    confirmation: Confirmation | None = None
    #: Las tres definiciones de rechazo que marcaron la vela de confirmación.
    #: Viajan con la observación hasta la señal para poder desglosar por
    #: definición (§5.8) sin volver a recorrer H1. Vacío cuando confirmó por ID o
    #: por OB de H1, que no son rechazos y no tienen definición que desglosar.
    rejection_marks: dict[str, bool] = field(default_factory=dict)
    #: Zona congelada de la rama de rotura y retesteo: la que se **rompió**. El
    #: impulso está muerto, así que no puede volver a moverse, y redibujarla con
    #: la vela de la rotura la agrandaría hasta contener el cierre que la
    #: atravesó. Vacío en la rama de respeto, donde el UL sí sigue vivo.
    frozen_zone: Zone | None = None

    def zone_at(self, h4_index: int) -> Zone:
        """La zona tal como se conocía al cierre de esa barra de H4."""
        if self.frozen_zone is not None:
            return self.frozen_zone
        if h4_index in self.zone_by_bar:
            return self.zone_by_bar[h4_index]
        # Antes del contacto o después del fin del ID se devuelve el borde de la
        # ventana: fuera de ella la zona no gobierna nada y no se extrapola.
        return self.zone_by_bar[min(max(h4_index, self.index_contact), self.h4_last)]

    def to_observation(self) -> Observation:
        return Observation(
            timeframe=self.impulse.timeframe,
            id_num=self.impulse.id_num,
            direction=self.impulse.direction,
            zone=self.kind,
            zone_inner=self.zone_at_contact.inner,
            zone_outer=self.zone_at_contact.outer,
            index_contact=self.index_contact,
            ts_contact=self.ts_contact.to_pydatetime(),
            ts_window_end=self.ts_window_end,
            daily=self.daily,
            daily_touch=self.daily_touch,
            index_break=self.index_break,
            ts_break=None if self.ts_break is None else self.ts_break.to_pydatetime(),
            index_retest=self.index_retest,
            ts_retest=None if self.ts_retest is None else self.ts_retest.to_pydatetime(),
        )


class _Cascade:
    """La cascada entera. Una instancia por corrida; no se reutiliza."""

    def __init__(
        self,
        run: ImpulseRun,
        zones: ZonesRun,
        m15_bars: pd.DataFrame | None,
        config: EntriesConfig,
    ) -> None:
        self._run = run
        self._config = config
        self._notes: list[str] = []

        for timeframe in (DAILY, H4, H1):
            if timeframe not in run.analyses:
                raise DomainError(
                    f"La cascada necesita el impulso de {timeframe} y la corrida no lo trae"
                )
            if timeframe not in zones.per_timeframe:
                raise DomainError(f"Faltan las zonas de {timeframe}")

        self._analysis: dict[str, TimeframeAnalysis] = dict(run.analyses)
        self._zones: dict[str, TimeframeZones] = dict(zones.per_timeframe)
        self._series = {
            timeframe: CandleSeries.of(analysis.bars)
            for timeframe, analysis in self._analysis.items()
        }
        self._clock = {
            timeframe: BarClock.of(analysis.bars, timeframe)
            for timeframe, analysis in self._analysis.items()
        }
        self._m15 = m15_bars
        self._m15_series = (
            CandleSeries.of(m15_bars) if m15_bars is not None and not m15_bars.empty else None
        )
        self._m15_clock = (
            BarClock.of(m15_bars, M15)
            if m15_bars is not None and not m15_bars.empty
            else None
        )
        if self._m15_series is None:
            self._notes.append(
                "No hay velas de M15: la variante de entrada afinada en M15 no se "
                "puede medir y todas sus señales se declaran ausentes, no cero."
            )
        # `R2` sólo puede mirar sesiones anteriores, y la sesión de este proyecto
        # es la del corte anclado a Nueva York, no el día natural de UTC.
        sessions = session_ids(
            self._clock[H1].opens, self._clock[DAILY].opens
        )
        self._percentile = {
            direction: RollingWickPercentile(
                self._series[H1],
                sessions,
                direction=direction,
                percentiles=config.rejection_grid,
                label=f"R2 H1 {direction.value}",
            )
            for direction in ImpulseDirection
        }
        self._funnel = dict.fromkeys(FUNNEL_STEPS, 0)
        self._discarded: list[DiscardedSignal] = []

    # --- Orquestación -------------------------------------------------------

    def execute(self) -> CascadeRun:
        daily = self._daily_touches()
        watches = self._observations(daily)
        self._confirm_in_h1(watches)
        signals = self._entries(watches)
        return CascadeRun(
            enabled=True,
            config=self._config,
            config_hash=self._config.fingerprint(),
            structure_hash=self._run.config_hash,
            daily_touches=tuple(daily),
            observations=tuple(watch.to_observation() for watch in watches),
            signals=tuple(signals),
            discarded=tuple(self._discarded),
            funnel=dict(self._funnel),
            notes=tuple(self._notes),
        )

    # --- §1.1 Contexto diario -----------------------------------------------

    def _daily_touches(self) -> list[DailyTouch]:
        """Contactos del precio con las zonas del ID diario vigente.

        **No son señales.** Marcan que la entrada de H4, si aparece, es más fuerte
        y potencialmente más larga; el desglose del §5.1 dirá si eso es cierto.

        El contexto vive mientras vive el ID diario que lo produjo. No es una
        ventana inventada: es la única frontera que el enunciado nombra —"su ID
        diario vigente"— y fuera de ella la zona ya no es de nadie.
        """
        analysis = self._analysis[DAILY]
        series = self._series[DAILY]
        clock = self._clock[DAILY]
        by_id = {item.id_num: item for item in self._zones[DAILY].items}
        last_index = len(series) - 1

        touched: list[DailyTouch] = []
        for impulse in analysis.published:
            item = by_id.get(impulse.id_num)
            if item is None:
                continue
            end = impulse.index_end if impulse.index_end is not None else last_index
            for kind, zone_by_bar in self._zone_windows(DAILY, impulse, item, end).items():
                contact = self._first_contact(series, zone_by_bar, impulse, end)
                if contact is None:
                    continue
                index, _ = contact
                touched.append(
                    DailyTouch(
                        id_num=impulse.id_num,
                        direction=impulse.direction,
                        zone=kind,
                        index=index,
                        timestamp=clock.close_of(index).to_pydatetime(),
                        index_expiry=impulse.index_end,
                        ts_expiry=(
                            clock.close_of(end).to_pydatetime()
                            if impulse.index_end is not None
                            else None
                        ),
                    )
                )
        touched.sort(key=lambda item: item.timestamp)
        return touched

    def _daily_context(
        self, touched: Sequence[DailyTouch], at: pd.Timestamp, direction: ImpulseDirection
    ) -> tuple[DailyContext, DailyTouch | None]:
        return daily_context(touched, at, direction)

    # --- §1.2 Observaciones en H4 -------------------------------------------

    def _observations(self, daily: Sequence[DailyTouch]) -> list[_Watch]:
        analysis = self._analysis[H4]
        series = self._series[H4]
        clock = self._clock[H4]
        h1_clock = self._clock[H1]
        by_id = {item.id_num: item for item in self._zones[H4].items}
        last_index = len(series) - 1
        published = list(analysis.published)
        constitutions = sorted(impulse.index_constitution for impulse in analysis.impulses)

        watches: list[_Watch] = []
        for impulse in published:
            item = by_id.get(impulse.id_num)
            if item is None:
                continue
            end = impulse.index_end if impulse.index_end is not None else last_index
            windows = self._zone_windows(H4, impulse, item, end)
            self._funnel["zonas_de_h4"] += len(windows)

            for kind, zone_by_bar in windows.items():
                contact = self._first_contact(series, zone_by_bar, impulse, end)
                if contact is None:
                    continue
                self._funnel["zonas_tocadas"] += 1
                index, zone = contact
                respect = self._respect_watch(
                    impulse, kind, zone_by_bar, index, zone, end, daily, clock, h1_clock
                )
                if respect is not None:
                    watches.append(respect)
                retest = self._retest_watch(
                    impulse,
                    kind,
                    zone_by_bar,
                    index,
                    end,
                    daily,
                    h1_clock,
                    constitutions,
                    last_index,
                )
                if retest is not None:
                    watches.append(retest)

        self._funnel["observaciones"] = len(watches)
        watches.sort(key=lambda watch: (watch.h1_first, watch.impulse.id_num))
        return watches

    def _respect_watch(
        self,
        impulse: DominantImpulse,
        kind: ZoneKind,
        zone_by_bar: dict[int, Zone],
        index: int,
        zone: Zone,
        end: int,
        daily: Sequence[DailyTouch],
        clock: BarClock,
        h1_clock: BarClock,
    ) -> _Watch | None:
        """La observación mientras la zona sigue entera (§1.2, RESPETO)."""
        opened = clock.close_of(index)
        first = h1_clock.closed_at(opened)
        # La primera barra de H1 utilizable es la que cierra a la vez que la de
        # H4 del contacto: antes de ese instante el contacto no se sabía.
        first = max(first, 0)
        last = h1_clock.closed_at(clock.close_of(end))
        if first > last or first >= len(h1_clock):
            self._discard(
                impulse, kind, zone, index, clock, GuardRail.ID_H4_MUERTO, daily
            )
            return None
        context, touch = self._daily_context(daily, opened, impulse.direction)
        return _Watch(
            impulse=impulse,
            kind=kind,
            zone_by_bar=zone_by_bar,
            zone_at_contact=zone,
            index_contact=index,
            ts_contact=opened,
            daily=context,
            daily_touch=touch,
            h1_first=first,
            h1_last=min(last, len(h1_clock) - 1),
            ts_window_end=h1_clock.close_of(min(last, len(h1_clock) - 1)).to_pydatetime(),
            h4_last=end,
        )

    def _retest_watch(
        self,
        impulse: DominantImpulse,
        kind: ZoneKind,
        zone_by_bar: dict[int, Zone],
        index_contact: int,
        end: int,
        daily: Sequence[DailyTouch],
        h1_clock: BarClock,
        constitutions: Sequence[int],
        last_index: int,
    ) -> _Watch | None:
        """La observación tras romperse la zona (§1.2, ROTURA Y RETESTEO).

        **Sólo aplica al UL.** En el OB la rotura invalida siempre, y eso no es
        una simplificación: lo dice el §1.2 con todas las letras.

        La rotura no se vuelve a calcular. Con `BREAK_BY_ZONE = true` cerrar más
        allá del borde exterior del UL *es* la rotura a favor del ID, así que la
        rotura de la observación es la muerte del impulso y se lee de ahí.

        **La zona que se retestea es la que se rompió**, es decir la vigente al
        cierre **anterior** al de la rotura: ése es el borde contra el que se
        juzgó la vela que rompió (la fase 2.1 lo pide con `through = t - 1`).
        Redibujarla con la propia vela de la rotura la estiraría hasta contener el
        cierre que la atravesó, y el retesteo pasaría a medirse contra un nivel
        que nunca gobernó.

        **La vela que rompe cierra primero.** El retesteo se busca con
        `first_retest_after_break`, que sólo mira velas de la temporalidad del ID
        **posteriores** a la de la rotura. La serie y el reloj se toman aquí de
        `impulse.timeframe` y no se reciben de fuera: así la regla se lee igual en
        Diario, H4 y H1, y no depende de qué serie le pase quien llame.
        """
        if impulse.index_end is None:
            return None
        series = self._series[impulse.timeframe]
        clock = self._clock[impulse.timeframe]
        broken_by_zone = (
            impulse.exit_break_kind is BreakKind.A_FAVOR
            if kind is ZoneKind.LAST
            else impulse.exit_break_kind is BreakKind.EN_CONTRA
        )
        if not broken_by_zone:
            # El ID murió por el otro lado, o sigue vivo: esta rama nunca llegó a
            # abrirse. No se apunta un descarte porque no ha muerto ninguna señal
            # aquí; la observación de respeto es la que lleva su propio motivo.
            return None
        zone = zone_by_bar[max(impulse.index_constitution, end - 1)]
        if kind is ZoneKind.ORDER_BLOCK:
            # §1.2, literal: en el OB la rotura invalida SIEMPRE. No hay variante
            # de retesteo y no se inventa una.
            self._discard(
                impulse,
                kind,
                zone,
                end,
                clock,
                GuardRail.OB_ROTO,
                daily,
                index_contact=index_contact,
            )
            return None

        limit = self._retest_limit(end, constitutions, last_index)
        retest = first_retest_after_break(
            series, zone, index_break=end, through=limit
        )
        if retest is None:
            self._discard(
                impulse,
                kind,
                zone,
                limit,
                clock,
                GuardRail.ROTURA_SIN_RETESTEO,
                daily,
                index_contact=index_contact,
            )
            return None

        opened = clock.close_of(retest)
        first = h1_clock.closed_at(opened)
        last = h1_clock.closed_at(clock.close_of(limit))
        if first < 0 or first > last or first >= len(h1_clock):
            self._discard(
                impulse,
                kind,
                zone,
                retest,
                clock,
                GuardRail.SIN_CONFIRMACION_H1,
                daily,
                index_contact=index_contact,
            )
            return None
        context, touch = self._daily_context(daily, opened, impulse.direction)
        return _Watch(
            impulse=impulse,
            kind=kind,
            zone_by_bar=zone_by_bar,
            zone_at_contact=zone,
            index_contact=index_contact,
            ts_contact=clock.close_of(index_contact),
            daily=context,
            daily_touch=touch,
            h1_first=first,
            h1_last=min(last, len(h1_clock) - 1),
            ts_window_end=h1_clock.close_of(min(last, len(h1_clock) - 1)).to_pydatetime(),
            h4_last=end,
            frozen_zone=zone,
            index_break=end,
            ts_break=clock.close_of(end),
            index_retest=retest,
            ts_retest=opened,
        )

    def _retest_limit(
        self, end: int, constitutions: Sequence[int], last_index: int
    ) -> int:
        """Hasta dónde se espera el retesteo. **Parámetro abierto** (§0).

        El valor por defecto no es un número nuevo: es la frontera que la máquina
        de estados ya tenía escrita, la constitución del ID de H4 siguiente. A
        partir de ahí la estructura es otra y el retesteo de la zona anterior ya
        no se está operando contra el mismo sesgo.
        """
        if self._config.retest_window_h4 != UNTIL_OBSERVATION_ENDS:
            return min(end + self._config.retest_window_h4, last_index)
        position = int(np.searchsorted(constitutions, end, side="right"))
        if position >= len(constitutions):
            return last_index
        return min(constitutions[position], last_index)

    def _zone_windows(
        self,
        timeframe: str,
        impulse: DominantImpulse,
        item: ImpulseZones,
        end: int,
    ) -> dict[ZoneKind, dict[int, Zone]]:
        """La zona vigente en cada barra de la vida del ID, por tipo.

        El UL se reconstruye barra a barra desde la traza de extensiones; el OB no
        se mueve y sólo empieza a existir cuando se confirma. Un ID sin OB
        confirmado no aporta ventana de OB: no hay zona que observar, y eso es un
        estado legítimo que la fase 2.0 ya contaba.
        """
        series = self._series[timeframe]
        clock = self._clock[timeframe]
        timeline = LastZoneTimeline(series, impulse)
        last_zones: dict[int, Zone] = {}
        for position in range(impulse.index_constitution, end + 1):
            timeline.advance(position)
            last_zones[position] = timeline.at(position)

        windows: dict[ZoneKind, dict[int, Zone]] = {ZoneKind.LAST: last_zones}
        block = item.order_block
        if block is not None:
            birth = clock.index_of(block.ts_birth)
            if birth <= end:
                windows[ZoneKind.ORDER_BLOCK] = dict.fromkeys(
                    range(birth, end + 1), block
                )
        return windows

    def _first_contact(
        self,
        series: CandleSeries,
        zone_by_bar: Mapping[int, Zone],
        impulse: DominantImpulse,
        end: int,
    ) -> tuple[int, Zone] | None:
        """Primer contacto: el `high`/`low` de la vela alcanza la zona (§1.1).

        Basta el contacto, no hace falta cerrar dentro. Se toma el **primero**
        porque es el primero que el propietario podría haber visto; elegir otro
        exigiría conocer la ventana entera.
        """
        for position in sorted(zone_by_bar):
            if position > end:
                break
            zone = zone_by_bar[position]
            if touches(zone, float(series.high[position]), float(series.low[position])):
                return position, zone
        return None

    def _discard(
        self,
        impulse: DominantImpulse,
        kind: ZoneKind,
        zone: Zone,
        index: int,
        clock: BarClock,
        rail: GuardRail,
        daily: Sequence[DailyTouch],
        confirmation: Confirmation | None = None,
        index_contact: int | None = None,
    ) -> None:
        """Apunta una señal muerta. `index` es DÓNDE muere, no dónde nació.

        Los dos van por separado porque en la rama de rotura y retesteo no
        coinciden: la zona se tocó mucho antes de romperse, y una ficha que
        enseñara la misma hora en las dos líneas haría dudar de la otra.
        """
        stamp = clock.close_of(index)
        born = clock.close_of(index if index_contact is None else index_contact)
        context, touch = self._daily_context(daily, stamp, impulse.direction)
        self._discarded.append(
            DiscardedSignal(
                observation=Observation(
                    timeframe=impulse.timeframe,
                    id_num=impulse.id_num,
                    direction=impulse.direction,
                    zone=kind,
                    zone_inner=zone.inner,
                    zone_outer=zone.outer,
                    index_contact=index if index_contact is None else index_contact,
                    ts_contact=born.to_pydatetime(),
                    ts_window_end=None,
                    daily=context,
                    daily_touch=touch,
                ),
                guard_rail=rail,
                index=index,
                timestamp=stamp.to_pydatetime(),
                confirmation=confirmation,
            )
        )

    # --- §1.3 Confirmación en H1 --------------------------------------------

    def _confirm_in_h1(self, watches: Sequence[_Watch]) -> None:
        """Una **única** pasada cronológica por H1, con la frontera avanzando.

        Recorrer H1 por observación habría obligado a mover la frontera hacia
        atrás en cada salto, y la garantía anti-lookahead habría quedado en un
        adorno. Aquí la frontera sólo avanza, y cada umbral de `R2` se lee en la
        misma barra en la que se declara cerrada.
        """
        series = self._series[H1]
        clock = self._clock[H1]
        h4_clock = self._clock[H4]
        constitutions = self._constitutions_by_bar(H1)
        blocks = self._order_blocks_by_bar(H1)

        pending = sorted(watches, key=lambda watch: watch.h1_first)
        cursor = 0
        active: list[_Watch] = []
        for bar in range(len(series)):
            for direction in ImpulseDirection:
                self._percentile[direction].advance(bar)
            while cursor < len(pending) and pending[cursor].h1_first <= bar:
                active.append(pending[cursor])
                cursor += 1
            if not active:
                continue

            high = float(series.high[bar])
            low = float(series.low[bar])
            close_at = clock.close_of(bar)
            h4_bar = h4_clock.closed_at(close_at)
            still: list[_Watch] = []
            for watch in active:
                if bar > watch.h1_last:
                    if watch.confirmation is None:
                        self._discard_watch(watch, GuardRail.SIN_CONFIRMACION_H1)
                    continue
                if watch.confirmation is None:
                    self._try_confirm(
                        watch, bar, high, low, h4_bar, series, clock, constitutions, blocks
                    )
                still.append(watch)
            active = still

        for watch in active:
            if watch.confirmation is None:
                self._discard_watch(watch, GuardRail.SIN_CONFIRMACION_H1)

    def _try_confirm(
        self,
        watch: _Watch,
        bar: int,
        high: float,
        low: float,
        h4_bar: int,
        series: CandleSeries,
        clock: BarClock,
        constitutions: Mapping[int, list[DominantImpulse]],
        blocks: Mapping[int, list[tuple[int, ImpulseDirection]]],
    ) -> None:
        """§1.3 — con la zona en observación y el precio **dentro de ella**.

        Las tres confirmaciones valen por igual y no se ordenan por calidad: se
        toma la primera que aparece, que es la única que el propietario podría
        haber operado. Si varias caen en la misma vela se registra la primera del
        orden en que el §1.3 las enumera, y las tres quedan en el CSV.
        """
        zone = watch.zone_at(h4_bar)
        if not touches(zone, high, low):
            return
        direction = watch.impulse.direction

        for impulse in constitutions.get(bar, ()):
            if impulse.direction is direction:
                watch.confirmation = Confirmation(
                    kind=ConfirmationKind.ID_H1,
                    index=bar,
                    timestamp=clock.close_of(bar).to_pydatetime(),
                    id_num=impulse.id_num,
                )
                self._funnel["confirman_en_h1"] += 1
                return

        for id_num, block_direction in blocks.get(bar, ()):
            if block_direction is direction:
                watch.confirmation = Confirmation(
                    kind=ConfirmationKind.OB_H1,
                    index=bar,
                    timestamp=clock.close_of(bar).to_pydatetime(),
                    id_num=id_num,
                )
                self._funnel["confirman_en_h1"] += 1
                return

        thresholds = {
            percentile: self._percentile[direction].at(bar, percentile)
            for percentile in self._config.rejection_grid
        }
        marks = mark_rejections(series, bar, direction, zone, thresholds)
        # **La unión**, no una definición adoptada: es el filtro más laxo posible
        # y las tres son subconjuntos suyos, así que el desglose del §5.8 puede
        # recortar la población hacia cada una sin volver a recorrer H1. Elegir
        # aquí una de las tres sería decidir lo que el §2 delega en el propietario.
        if not marks.any_kind:
            return
        watch.confirmation = Confirmation(
            kind=ConfirmationKind.RECHAZO,
            index=bar,
            timestamp=clock.close_of(bar).to_pydatetime(),
            rejections=tuple(
                kind
                for kind in RejectionKind
                if _marked(marks, kind, self._config.rejection_grid)
            ),
        )
        self._funnel["confirman_en_h1"] += 1
        watch.rejection_marks = _marks_payload(marks)

    def _constitutions_by_bar(
        self, timeframe: str
    ) -> dict[int, list[DominantImpulse]]:
        grouped: dict[int, list[DominantImpulse]] = {}
        for impulse in self._analysis[timeframe].published:
            grouped.setdefault(impulse.index_constitution, []).append(impulse)
        return grouped

    def _order_blocks_by_bar(
        self, timeframe: str
    ) -> dict[int, list[tuple[int, ImpulseDirection]]]:
        """Cuándo **existe** cada OB de la temporalidad, por barra de nacimiento.

        Nacer es lo que la fase 2.0 ya definió: el ID constituido y el OB
        confirmado, las dos cosas. Antes de eso el OB no se puede leer, y por eso
        no puede confirmar nada.
        """
        clock = self._clock[timeframe]
        grouped: dict[int, list[tuple[int, ImpulseDirection]]] = {}
        for item in self._zones[timeframe].items:
            block = item.order_block
            if block is None:
                continue
            grouped.setdefault(clock.index_of(block.ts_birth), []).append(
                (item.id_num, item.direction)
            )
        return grouped

    def _discard_watch(self, watch: _Watch, rail: GuardRail) -> None:
        clock = self._clock[H1]
        index = min(watch.h1_last, len(clock) - 1)
        self._discarded.append(
            DiscardedSignal(
                observation=watch.to_observation(),
                guard_rail=rail,
                index=index,
                timestamp=clock.close_of(index).to_pydatetime(),
                confirmation=watch.confirmation,
            )
        )

    # --- §1.4 Entradas -------------------------------------------------------

    def _entries(self, watches: Sequence[_Watch]) -> list[Signal]:
        """Las **dos** variantes, implementadas y medidas por separado (§1.4)."""
        signals: list[Signal] = []
        for watch in watches:
            if watch.confirmation is None:
                continue
            marks = watch.rejection_marks
            h1_zone = self._h1_entry_zone(watch)
            if h1_zone is None:
                self._discard_watch(watch, GuardRail.SIN_OB_H1)
            else:
                self._funnel["entrada_localizada_h1"] += 1
                signals.append(
                    Signal(
                        observation=watch.to_observation(),
                        confirmation=watch.confirmation,
                        entry_zone=h1_zone,
                        # Una entrada de H1 sólo puede llevar stop de H1: la zona
                        # de M15 se forma **después** de decidir y ponerla aquí
                        # sería exactamente el lookahead que el §7 prohíbe. Es un
                        # caso límite del §5.5 y se declara, no se rellena.
                        stop_options=((StopZone.H1, h1_zone),),
                        ts_decision=watch.confirmation.timestamp,
                        rejection_marks=dict(marks),
                    )
                )
            m15_zone = self._m15_entry_zone(watch)
            if m15_zone is None:
                if self._m15_series is not None:
                    self._discard_watch(watch, GuardRail.SIN_OB_M15)
            else:
                self._funnel["entrada_localizada_m15"] += 1
                options = [(StopZone.M15, m15_zone)]
                if h1_zone is not None:
                    # La zona de H1 ya existía al decidir la entrada de M15, así
                    # que su stop sí es legible aquí. Las dos se miden por separado.
                    options.append((StopZone.H1, h1_zone))
                signals.append(
                    Signal(
                        observation=watch.to_observation(),
                        confirmation=watch.confirmation,
                        entry_zone=m15_zone,
                        stop_options=tuple(options),
                        ts_decision=m15_zone.ts_confirmation,
                        rejection_marks=dict(marks),
                    )
                )
        return signals

    def _h1_entry_zone(self, watch: _Watch) -> EntryZone | None:
        """§1.4 — la entrada se coloca en la zona de H1: **su OB**.

        "Su" es el OB del ID de H1 vigente en la confirmación, y tiene que
        cumplir dos cosas que no son negociables: ir en la dirección buscada y
        **haber nacido ya**. Un OB que se confirma después no estaba ahí para
        colocar nada.
        """
        confirmation = watch.confirmation
        if confirmation is None:
            return None
        clock = self._clock[H1]
        at = pd.Timestamp(confirmation.timestamp)
        item = self._h1_zone_owner(watch.impulse.direction, at)
        if item is None or item.order_block is None:
            return None
        block = item.order_block
        if pd.Timestamp(block.ts_birth) > at:
            return None
        return EntryZone(
            timeframe=EntryTimeframe.H1,
            inner=block.inner,
            outer=block.outer,
            index_defining=block.index_defining,
            ts_defining=block.ts_defining,
            index_confirmation=clock.index_of(block.ts_birth),
            ts_confirmation=confirmation.timestamp,
            id_num=item.id_num,
        )

    def _h1_zone_owner(
        self, direction: ImpulseDirection, at: pd.Timestamp
    ) -> ImpulseZones | None:
        """El ID de H1 vigente en `at`, si va en la dirección buscada."""
        for item in reversed(self._zones[H1].items):
            if pd.Timestamp(item.ts_constitution) > at:
                continue
            if item.ts_end is not None and pd.Timestamp(item.ts_end) < at:
                return None
            return item if item.direction is direction else None
        return None

    def _m15_entry_zone(self, watch: _Watch) -> EntryZone | None:
        """§1.4 — el OB **suelto** de M15: no hace falta ID de M15.

        Se busca a partir de la confirmación de H1 y no antes: la entrada afinada
        es lo que viene *después* de confirmar. La vela contraria que da forma al
        OB sí puede ser anterior; lo que la ventana acota es cuándo se supera, que
        es cuando el OB empieza a existir (§7).
        """
        confirmation = watch.confirmation
        if confirmation is None or self._m15_series is None or self._m15_clock is None:
            return None
        first = self._m15_clock.first_open_strictly_after(confirmation.timestamp)
        if first >= len(self._m15_clock):
            return None
        limit = self._m15_limit(watch, first)
        if limit < first:
            return None
        block = find_loose_order_block(
            self._m15_series,
            direction=watch.impulse.direction,
            first=first,
            through=limit,
        )
        if block is None:
            return None
        return EntryZone(
            timeframe=EntryTimeframe.M15,
            inner=block.inner,
            outer=block.outer,
            index_defining=block.index_defining,
            ts_defining=block.ts_defining,
            index_confirmation=block.index_confirmation,
            ts_confirmation=self._m15_clock.close_of(
                block.index_confirmation
            ).to_pydatetime(),
        )

    def _m15_limit(self, watch: _Watch, first: int) -> int:
        assert self._m15_clock is not None
        if self._config.m15_search_bars != UNTIL_OBSERVATION_ENDS:
            return min(first + self._config.m15_search_bars - 1, len(self._m15_clock) - 1)
        end = self._clock[H1].close_of(watch.h1_last)
        return min(
            self._m15_clock.closed_at(end), len(self._m15_clock) - 1
        )


def _marked(marks: RejectionMarks, kind: RejectionKind, grid: Sequence[int]) -> bool:
    if kind is RejectionKind.R1_MECHA_EN_ZONA:
        return marks.r1
    if kind is RejectionKind.R3_CIERRE_EN_EXTREMO:
        return marks.r3
    return any(marks.r2.get(value, False) for value in grid)


def _marks_payload(marks: RejectionMarks) -> dict[str, bool]:
    """Las tres definiciones, con `R2` abierta por percentil (§5.8).

    Se guardan las tres siempre, marque la que marque: el desglose del §5.8 sólo
    se puede hacer si cada operación sabe qué definiciones la habrían producido, y
    reconstruirlo después obligaría a volver a recorrer H1.
    """
    payload: dict[str, bool] = {
        RejectionKind.R1_MECHA_EN_ZONA.value: marks.r1,
        RejectionKind.R3_CIERRE_EN_EXTREMO.value: marks.r3,
    }
    for percentile, value in marks.r2.items():
        payload[f"{RejectionKind.R2_MECHA_DOMINANTE.value}_p{percentile}"] = value
    return payload


__all__ = ["FUNNEL_STEPS", "CascadeRun", "build_cascade", "daily_context"]
