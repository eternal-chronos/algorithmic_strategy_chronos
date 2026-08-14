"""Entidades del ciclo de vida del impulso dominante (§2.2, §2.3, §2.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from chronos.domain.structure.enums import (
    BodyDirection,
    BreakKind,
    BreakLevelSource,
    ImpulseDirection,
    MachineState,
)
from chronos.domain.structure.errors import StructureError


@dataclass(slots=True)
class DominantImpulse:
    """Un rango con dirección, vivo desde su constitución hasta su rotura.

    Entidad con identidad (`id_num`) y una única transición: `close()`. Todo lo
    que ocurre dentro de `[ancla, extremo]` mientras está vigente es retroceso.
    """

    id_num: int
    timeframe: str
    direction: ImpulseDirection

    #: Rotura que mató al ID anterior y abrió el limbo del que nace éste.
    #: `None` sólo en el primer impulso del histórico (no hubo ID anterior).
    ts_previous_break: datetime | None
    #: Cierre de la primera vela contraria a la pierna: el instante de nacimiento.
    ts_constitution: datetime
    index_constitution: int
    #: Arranque de la pierna que produjo la rotura, de donde sale el ancla.
    ts_leg_start: datetime
    index_leg_start: int

    anchor: float
    extreme: float
    #: Ambas candidaturas de ancla se conservan siempre: el informe las compara
    #: para que el propietario cierre el parámetro `ANCHOR_MODE` (§2.5).
    anchor_a1: float | None
    anchor_a2: float

    #: Velas que **definen** cada uno de los dos niveles. El módulo ya las conoce
    #: al constituir; guardarlas evita que quien dibuje o audite tenga que
    #: reconstruirlas buscando qué cuerpo coincide con el precio, que con empates
    #: no da una respuesta única. El explorador las usa para pegar cada línea a su
    #: vela (R-36, B.1) y la auditoría de color, para comprobar la regla del
    #: propietario sin re-derivar nada.
    index_anchor: int
    ts_anchor: datetime
    index_extreme: int
    ts_extreme: datetime
    #: Color del cuerpo que fijó el extremo. La regla del propietario dice que en
    #: un impulso alcista es verde y en uno bajista, rojo.
    extreme_bar_direction: BodyDirection

    #: Barras cerradas en LIMBO entre la rotura anterior y esta constitución.
    limbo_bars: int
    #: Tamaño del cuerpo de la vela contraria que lo constituyó. Alimenta el
    #: recuento de "impulsos creados por una vela minúscula" del informe.
    constituting_body_size: float
    #: `False` durante el calentamiento o si el ancla del modo elegido no existe.
    publishable: bool = True

    ts_end: datetime | None = field(default=None, init=False)
    index_end: int | None = field(default=None, init=False)
    exit_break_kind: BreakKind | None = field(default=None, init=False)
    #: Fase 2.1. De dónde salió el nivel que lo mató: la línea, el UL o el OB.
    #: Con `BREAK_BY_ZONE = false` es siempre la línea.
    exit_level_source: BreakLevelSource | None = field(default=None, init=False)

    #: Fase 2.1. Veces que el extremo se estiró estando el ID ya vigente, porque
    #: una vela cerró más allá de la línea sin atravesar el UL entero. En la fase
    #: 1 esto no podía ocurrir: esa vela mataba el ID.
    extreme_extensions: int = field(default=0, init=False)
    #: El extremo con el que nació, antes de cualquier extensión. Se guarda para
    #: poder medir cuánto se movió sin reconstruirlo desde los eventos.
    extreme_at_constitution: float = field(default=float("nan"), init=False)

    def __post_init__(self) -> None:
        self.extreme_at_constitution = self.extreme

    def close(
        self,
        *,
        timestamp: datetime,
        index: int,
        kind: BreakKind,
        level_source: BreakLevelSource = BreakLevelSource.LINE,
    ) -> None:
        """Cierra el impulso en la barra que lo rompe."""
        if self.ts_end is not None:
            raise StructureError(f"El impulso {self.id_num} ya estaba cerrado")
        if index < self.index_constitution:
            raise StructureError(
                f"El impulso {self.id_num} no puede cerrarse antes de constituirse"
            )
        self.ts_end = timestamp
        self.index_end = index
        self.exit_break_kind = kind
        self.exit_level_source = level_source

    def extend_extreme(
        self,
        *,
        price: float,
        index: int,
        timestamp: datetime,
        bar_direction: BodyDirection,
    ) -> None:
        """Estira el extremo del ID vigente (fase 2.1, §3.2).

        Sólo lo llama el detector cuando una vela cierra más allá de la línea del
        extremo sin atravesar el UL entero: el ID sobrevive a lo que antes era
        una rotura y su extremo pasa a ser lo que ha alcanzado esta vela. El UL se
        recalcula solo, porque se deriva de `index_extreme`, y la zona nueva
        sustituye a la anterior.

        El ancla **no** tiene equivalente: la fija la vela del arranque de la
        pierna, que no cambia, y por eso el OB no se mueve nunca.
        """
        if self.ts_end is not None:
            raise StructureError(
                f"El impulso {self.id_num} ya estaba cerrado: su extremo no se mueve"
            )
        if index < self.index_constitution:
            raise StructureError(
                f"El impulso {self.id_num} no puede extender su extremo antes de constituirse"
            )
        improves = (
            price > self.extreme
            if self.direction is ImpulseDirection.ALCISTA
            else price < self.extreme
        )
        if not improves:
            raise StructureError(
                f"El extremo del impulso {self.id_num} no retrocede: "
                f"{price} no mejora {self.extreme}"
            )
        self.extreme = price
        self.index_extreme = index
        self.ts_extreme = timestamp
        self.extreme_bar_direction = bar_direction
        self.extreme_extensions += 1

    @property
    def is_open(self) -> bool:
        return self.ts_end is None

    @property
    def state(self) -> str:
        return "VIGENTE" if self.is_open else "CERRADO"

    def bars_alive(self, last_index: int) -> int:
        """Barras que el ID estuvo vigente. Si sigue abierto, hasta `last_index`."""
        end = self.index_end if self.index_end is not None else last_index
        return max(0, end - self.index_constitution)

    @property
    def extreme_on_counter_bar(self) -> bool:
        """`True` si el extremo lo fijó una vela del color contrario al impulso.

        La regla del propietario dice que esto no puede pasar. Es la medida de
        R-36: en `L1_actual` cuenta los casos que entraron por la vela de la
        rotura; en `L2` y `L3` debería quedar en cero.
        """
        if self.extreme_bar_direction is BodyDirection.DOJI:
            return False
        return self.extreme_bar_direction.as_impulse() is self.direction.opposite()

    @property
    def range_usd(self) -> float:
        """Amplitud del impulso en la dirección del impulso.

        Puede salir negativa con datos con huecos violentos (ancla y extremo
        cruzados); no se corrige en silencio, se cuenta en los diagnósticos.
        """
        if self.direction is ImpulseDirection.ALCISTA:
            return self.extreme - self.anchor
        return self.anchor - self.extreme

    @property
    def range_pct_price(self) -> float:
        """Amplitud como fracción del precio del ancla.

        Obligatorio para comparar 2015 (oro ~1.200) con 2026 (oro ~4.300): el
        mismo movimiento en dólares no significa lo mismo.
        """
        if self.anchor == 0:
            raise StructureError(f"Ancla nula en el impulso {self.id_num}")
        return self.range_usd / abs(self.anchor)

    def contains(self, price: float) -> bool:
        """`True` si el precio cae dentro del rango (es decir, es retroceso)."""
        low, high = sorted((self.anchor, self.extreme))
        return low <= price <= high


@dataclass(frozen=True, slots=True)
class BreakEvent:
    """Rotura de un ID por uno de sus dos límites. Lo consumirá el módulo 2."""

    kind: BreakKind
    timestamp: datetime
    index: int
    timeframe: str
    broken_id_num: int
    broken_direction: ImpulseDirection
    #: Dirección de la pierna que queda en curso durante el limbo, y por tanto
    #: del ID que acabará constituyéndose.
    new_leg_direction: ImpulseDirection
    close: float
    #: Nivel que el cierre superó. En la fase 1 es siempre una de las dos líneas
    #: del ID; con `BREAK_BY_ZONE` es el borde **exterior** de la zona que la
    #: sustituye, salvo cuando no hay zona en ese lado.
    level: float
    #: La línea del ID (`extremo` a favor, `ancla` en contra), mande o no. Con la
    #: regla nueva es lo que permite ver cuánto más lejos hubo que ir para romper.
    line: float | None = None
    level_source: BreakLevelSource = BreakLevelSource.LINE

    @property
    def by_zone(self) -> bool:
        return self.level_source is not BreakLevelSource.LINE


@dataclass(frozen=True, slots=True)
class BarState:
    """Fotografía del sistema al cierre de una barra.

    Se construye con la información disponible en ese cierre y nunca después:
    es lo que se publica y lo que dibuja el explorador.
    """

    index: int
    timestamp: datetime
    timeframe: str
    state: MachineState
    impulse_id: int | None
    #: Dirección de la pierna en curso mientras se está en limbo.
    leg_direction: ImpulseDirection | None

    @property
    def in_limbo(self) -> bool:
        return self.state is MachineState.LIMBO
