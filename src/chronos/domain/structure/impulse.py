"""Entidades del ciclo de vida del impulso dominante (§2.2, §2.3, §2.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from chronos.domain.structure.enums import (
    BodyDirection,
    BreakKind,
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

    def close(self, *, timestamp: datetime, index: int, kind: BreakKind) -> None:
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
    #: Nivel superado: el `extremo` en una rotura a favor, el `ancla` en contra.
    level: float


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
