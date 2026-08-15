"""Las entidades de la cascada: observación, señal y operación.

Una **observación** es una zona de H4 que el precio ha tocado. Una **señal** es
una observación que ha confirmado en H1 y ha encontrado zona de entrada. Una
**operación** es una señal ejecutada, con su desenlace.

Las tres guardan por dónde pasaron —contacto, observación, confirmación, entrada,
stop, objetivo y desenlace— porque el §10 exige poder navegar cada operación
sobre las cuatro temporalidades, y reconstruir eso más tarde desde un CSV plano
no es lo mismo que registrarlo cuando ocurre.

**Todo resultado va en múltiplos de R.** El 1R es la distancia de la entrada al
stop, y se guarda además en USD, en ATR y en % del precio porque el §3 lo exige:
es el control de sanidad que dice si la fórmula del stop aterriza en una banda
operable o produce stops de un dólar que la horquilla se come.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    EntryTimeframe,
    GuardRail,
    Outcome,
    RejectionKind,
    StopZone,
    TradeOutcome,
)
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

#: §3 — objetivo fijo, pre-registrado antes de ver ningún resultado. El punto de
#: equilibrio bruto es 1 / (1 + 3,3) = 23,3 % de aciertos.
TARGET_R = 3.3

#: §3 — versión del stop. Cualquier cambio posterior es la versión 2 y cuenta
#: como configuración medida, no como corrección.
STOP_VERSION = 1


@dataclass(frozen=True, slots=True)
class DailyTouch:
    """Contacto del precio con una zona del ID diario vigente (§1.1).

    **No es señal.** Sólo marca que la entrada, si aparece en H4, es más fuerte y
    potencialmente más larga. El desglose del §5.1 confirma o desmiente eso.
    """

    id_num: int
    direction: ImpulseDirection
    zone: ZoneKind
    index: int
    timestamp: datetime
    #: Última barra diaria en la que el contexto sigue activo: el ID diario que
    #: lo produjo muere ahí. `None` = sigue vigente al final del histórico.
    index_expiry: int | None
    ts_expiry: datetime | None


@dataclass(frozen=True, slots=True)
class Confirmation:
    """Lo que confirmó en H1, con la vela cerrada que lo produjo."""

    kind: ConfirmationKind
    index: int
    timestamp: datetime
    #: ID de H1 implicado, cuando lo hay (la confirmación por rechazo no lo pide).
    id_num: int | None = None
    #: Definiciones de rechazo que marcaron esa vela. Vacío si no fue un rechazo.
    #: Las tres se registran siempre: **ninguna está adoptada** (§2).
    rejections: tuple[RejectionKind, ...] = ()
    #: Fase 3.1 — **todas** las vías que estaban disponibles en esa vela, no sólo
    #: la que se tomó. En la 3.0 la cascada cortaba al confirmar y esa información
    #: se perdía: sin ella no se puede decir cuántas veces coincidieron las dos
    #: vías ni si el orden de prioridad cambia algo, y hubo que reconstruirlo a
    #: mano después. Incluye siempre a `kind`.
    available: tuple[ConfirmationKind, ...] = ()

    @property
    def both_available(self) -> bool:
        """Las dos vías de la 3.1 cayeron en la misma vela."""
        return (
            ConfirmationKind.TURTLE_SOUP in self.available
            and ConfirmationKind.OB_H1 in self.available
        )


@dataclass(frozen=True, slots=True)
class EntryZone:
    """La zona sobre la que se coloca la entrada y de la que sale el stop (§3)."""

    timeframe: EntryTimeframe
    #: Borde que el precio encuentra primero viniendo a favor de la dirección.
    inner: float
    #: Borde que hay que cruzar para dejar la zona atrás. Ahí va el stop.
    outer: float
    index_defining: int
    ts_defining: datetime
    index_confirmation: int
    ts_confirmation: datetime
    #: ID de H1 del que sale el OB. `None` en el OB suelto de M15, que no lo pide.
    id_num: int | None = None

    @property
    def low(self) -> float:
        return min(self.inner, self.outer)

    @property
    def high(self) -> float:
        return max(self.inner, self.outer)

    @property
    def height(self) -> float:
        return self.high - self.low

    @property
    def is_flat(self) -> bool:
        return self.height == 0.0


@dataclass(frozen=True, slots=True)
class Observation:
    """Una zona de H4 que el precio ha tocado (§1.2).

    Nace en el contacto y muere de una de tres formas: produce entrada, la zona se
    rompe sin retesteo, o el ID de H4 se acaba. `guard_rail` guarda **el primer**
    motivo que la mató; el embudo del §6 cuenta uno por señal y no dos.
    """

    timeframe: str
    id_num: int
    direction: ImpulseDirection
    zone: ZoneKind
    #: Bordes de la zona **tal como se conocían en el contacto**. El UL se mueve
    #: con las extensiones del extremo, así que apuntar al último sería mirar al
    #: futuro (§7).
    zone_inner: float
    zone_outer: float
    index_contact: int
    ts_contact: datetime
    #: Cierre de la **última** barra de H1 en la que esta observación todavía
    #: podía confirmar. Va aquí y no se re-deriva porque es la frontera que
    #: distingue "no confirmó" de "confirmó justo a tiempo", y el §9.4 pide
    #: retratar exactamente ese borde.
    ts_window_end: datetime | None
    daily: DailyContext
    #: Contacto diario que dio el contexto, si lo había.
    daily_touch: DailyTouch | None = None
    #: La rotura de la zona, cuando la hubo. Sólo el UL sigue vivo después.
    index_break: int | None = None
    ts_break: datetime | None = None
    #: El retesteo posterior a la rotura, cuando lo hubo.
    index_retest: int | None = None
    ts_retest: datetime | None = None

    @property
    def outcome(self) -> Outcome:
        """Con qué desenlace se opera esta observación."""
        return (
            Outcome.ROTURA_Y_RETESTEO
            if self.index_retest is not None
            else Outcome.RESPETO
        )

    @property
    def zone_low(self) -> float:
        return min(self.zone_inner, self.zone_outer)

    @property
    def zone_high(self) -> float:
        return max(self.zone_inner, self.zone_outer)


@dataclass(frozen=True, slots=True)
class DiscardedSignal:
    """Una observación que no llegó a operación, y en qué guardarraíl murió.

    El §10 las dibuja y el §6 las cuenta: sin ellas el embudo no se puede leer, y
    una estrategia que pierde el 99 % de sus contactos en un sitio concreto no se
    juzga igual que una que los reparte.
    """

    observation: Observation
    guard_rail: GuardRail
    index: int
    timestamp: datetime
    confirmation: Confirmation | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    """Una observación confirmada en H1 con zona de entrada ya localizada.

    Todavía no es una operación: falta ejecutarla en el M1 siguiente, y ahí puede
    caerse por stop inválido o por final del histórico.
    """

    observation: Observation
    confirmation: Confirmation
    entry_zone: EntryZone
    #: Zonas bajo las que se puede poner el stop **en el instante de decidir**
    #: (§3, `STOP_ZONE`). Una entrada de H1 sólo puede llevar stop de H1: la zona
    #: de M15 todavía no existe cuando se decide, y usarla sería lookahead. Una
    #: entrada de M15 puede llevar los dos, y los dos se miden por separado.
    stop_options: tuple[tuple[StopZone, EntryZone], ...]
    #: Cierre de la barra que dispara la decisión. La ejecución va en la M1
    #: siguiente y no antes (§4 y §7).
    ts_decision: datetime
    #: Todas las definiciones de rechazo que marcaron la vela de confirmación,
    #: marcadas por percentil para `R2`. Se arrastran hasta el resultado para
    #: poder desglosar por definición (§5.8) sin volver a calcular nada.
    rejection_marks: dict[str, bool] = field(default_factory=dict)

    @property
    def direction(self) -> ImpulseDirection:
        return self.observation.direction

    @property
    def is_long(self) -> bool:
        return self.direction is ImpulseDirection.ALCISTA

    def stop_price(self, zone: EntryZone) -> float:
        """§3 — al otro lado de la zona de entrada, más allá de su borde exterior.

        Sin holgura añadida: la versión 1 del stop es literalmente el borde
        exterior. Cualquier colchón sería un parámetro que el propietario no ha
        decidido, y esta versión está pre-registrada antes de ver resultados.
        """
        return zone.outer

    def target_price(self, entry: float, stop: float) -> float:
        """§3 — 1 : 3,3 R fijo, siempre. Sin parciales, sin trailing, sin BE."""
        risk = abs(entry - stop)
        return entry + TARGET_R * risk if self.is_long else entry - TARGET_R * risk


@dataclass(frozen=True, slots=True)
class Trade:
    """Una señal ejecutada, con su desenlace y su resultado en R.

    Bruto y neto van los dos: la diferencia entre ellos es lo que diagnostica si
    un efecto es real o aritmética de costes (§6).
    """

    signal: Signal
    entry_timeframe: EntryTimeframe
    stop_zone: StopZone
    #: Ejecución en el open de la barra M1 siguiente a la decisión (§4).
    index_entry_m1: int
    ts_entry: datetime
    entry_price: float
    stop_price: float
    target_price: float
    lots: float
    #: 1R en las tres unidades que exige el §3.
    risk_usd: float
    risk_atr: float
    risk_pct_price: float
    outcome: TradeOutcome
    ts_exit: datetime | None
    exit_price: float | None
    index_exit_m1: int | None
    #: Costes en divisa de cuenta, todos marcados VERIFICAR hasta calibrarlos.
    commission_usd: float
    slippage_usd: float
    swap_usd: float
    nights: int
    #: Resultado en múltiplos de R.
    gross_r: float
    net_r: float

    def __post_init__(self) -> None:
        if self.risk_usd <= 0:
            raise DomainError(
                f"1R no positivo en la operación de {self.ts_entry}: {self.risk_usd}"
            )

    @property
    def direction(self) -> ImpulseDirection:
        return self.signal.direction

    @property
    def is_long(self) -> bool:
        return self.signal.is_long

    @property
    def cost_r(self) -> float:
        """Coste total en R. Es `gross_r - net_r` y se nombra para poder pedirlo."""
        return self.gross_r - self.net_r

    @property
    def year(self) -> int:
        return self.ts_entry.year

    @property
    def daily(self) -> DailyContext:
        return self.signal.observation.daily

    @property
    def outcome_kind(self) -> Outcome:
        return self.signal.observation.outcome

    @property
    def zone(self) -> ZoneKind:
        return self.signal.observation.zone


__all__ = [
    "STOP_VERSION",
    "TARGET_R",
    "Confirmation",
    "DailyTouch",
    "DiscardedSignal",
    "EntryZone",
    "Observation",
    "Signal",
    "Trade",
]
