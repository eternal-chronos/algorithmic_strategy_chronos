"""Los niveles con que la fase 2.1 sustituye a las dos líneas del ID.

La fase 1 mata un ID cuando una vela cierra más allá de una de sus dos líneas.
A partir de la fase 2.1 la línea deja de ser el nivel de rotura **cuando existe
una zona que la sustituya**: el UL en el lado a favor y el OB en el lado en
contra. Romper una zona es cerrar más allá de su borde **exterior**, es decir
atravesarla entera; perforarla con mecha o cerrar dentro no rompe.

    lado a favor (extremo) -> UL si existe, y existe siempre
    lado en contra (ancla) -> OB si está confirmado; si no, la línea

Este módulo no decide nada: calcula los dos niveles vigentes en un instante y
dice de dónde sale cada uno. Quién los compara con el cierre es el detector.

**Causalidad.** El nivel con el que se juzga la vela `t` sólo puede salir de
velas cerradas **antes** de `t`. Por eso todo se pide con un `through`, que es el
índice de la última vela ya cerrada, y pedir más allá de la frontera lanza
`LookaheadError` en vez de devolver un número. Las dos consecuencias que importan:

- El UL nace con la mecha de la vela del extremo de la **constitución** y puede
  estirarse a la **siguiente**, que es la vela de margen. Ese es todo su
  recorrido: no se remarca cuando el extremo se estira, así que el borde exterior
  contra el que se juzga al ID es el mismo desde que nace hasta que muere. La
  comprobación de que la vela de margen ha cerrado se queda igual, porque la
  regla es que un borde no se lee antes de que lo fije su vela.
- El OB no gobierna hasta que su vela de confirmación ha cerrado. La vela que
  confirma no se juzga ya contra el OB; la siguiente sí.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chronos.domain.structure.enums import (
    BodyDirection,
    BreakKind,
    BreakLevelSource,
    ImpulseDirection,
)
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.zones import CandleSeries, ZoneKind


@dataclass(frozen=True, slots=True)
class SideLevel:
    """El nivel que gobierna uno de los dos lados del ID, y de dónde sale.

    `line` es siempre la línea de la fase 1 —el extremo o el ancla— y se conserva
    aunque no mande: es lo que permite contar las roturas **evitadas**, que son
    las velas que habrían roto por línea y con la regla nueva no rompen.
    """

    #: Nivel que hay que cruzar para romper. Es `line` cuando no hay zona.
    price: float
    #: La línea del ID, mande o no.
    line: float
    #: Borde interior de la zona, o la propia línea cuando no hay zona.
    inner: float
    source: BreakLevelSource

    @property
    def is_zone(self) -> bool:
        return self.source is not BreakLevelSource.LINE

    @property
    def is_flat(self) -> bool:
        """Zona degenerada: los dos bordes en el mismo precio (§1.3 de la fase 2.1).

        Ocurre cuando la vela del extremo cerró en su propio máximo y no dejó
        mecha. La zona existe, mide cero y se comporta exactamente como la línea.
        No se trata aparte; se cuenta.
        """
        return self.is_zone and self.price == self.inner


@dataclass(frozen=True, slots=True)
class BreakLevels:
    """Los dos niveles vigentes de un ID en un cierre concreto."""

    favor: SideLevel
    against: SideLevel

    def of(self, kind: BreakKind) -> SideLevel:
        return self.favor if kind is BreakKind.A_FAVOR else self.against


@dataclass(frozen=True, slots=True)
class AvoidedBreak:
    """Vela que bajo la regla de la fase 1 habría roto el ID y ahora no.

    Es la capa que el propietario audita primero (§7), así que lleva dentro todo
    lo que hace falta para dibujarla y para explicarla en una línea de texto sin
    volver a ningún CSV.
    """

    kind: BreakKind
    timestamp: datetime
    index: int
    timeframe: str
    id_num: int
    direction: ImpulseDirection
    close: float
    #: Nivel que habría roto: el extremo a favor, el ancla en contra.
    line: float
    zone: ZoneKind
    zone_inner: float
    zone_outer: float
    #: `True` si además movió el extremo del ID (sólo en el lado a favor).
    extended_extreme: bool


def line_levels(
    *, extreme: float, anchor: float
) -> BreakLevels:
    """Los niveles de la fase 1: las dos líneas, sin zonas de por medio.

    Es lo que devuelve `BREAK_BY_ZONE = false`, y por eso el interruptor apagado
    reproduce la línea base sin que el detector tenga dos caminos distintos.
    """
    return BreakLevels(
        favor=SideLevel(
            price=extreme, line=extreme, inner=extreme, source=BreakLevelSource.LINE
        ),
        against=SideLevel(
            price=anchor, line=anchor, inner=anchor, source=BreakLevelSource.LINE
        ),
    )


class ZoneBreakLevels:
    """Las zonas de una temporalidad leídas barra a barra, sin mirar al futuro.

    Se alimenta con la misma serie que el detector y avanza con él. Es la única
    puerta por la que la máquina de estados lee mechas: el `BodyBar` del módulo 1
    sigue sin tener `high` ni `low`, y sigue siendo a propósito.
    """

    def __init__(self, series: CandleSeries, *, timeframe: str) -> None:
        self._series = series
        self._timeframe = timeframe
        self._frontier = -1
        #: Confirmación del OB ya encontrada, por vela del ancla y dirección.
        self._confirmed: dict[tuple[int, ImpulseDirection], int] = {}
        #: Hasta dónde se buscó sin encontrarla. La búsqueda no retrocede: cada
        #: vela se examina una vez en toda la vida del ID.
        self._cursor: dict[tuple[int, ImpulseDirection], int] = {}

    def __len__(self) -> int:
        return len(self._series)

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def frontier(self) -> int:
        """Última vela declarada cerrada. Nada posterior es legible."""
        return self._frontier

    def advance(self, index: int) -> None:
        """Declara que la vela `index` ya ha cerrado."""
        if index < self._frontier:
            raise StructureError(
                f"[{self._timeframe}] La frontera de las zonas no retrocede: "
                f"{index} tras {self._frontier}"
            )
        if index >= len(self._series):
            raise StructureError(
                f"[{self._timeframe}] Índice {index} fuera de la serie "
                f"({len(self._series)} velas)"
            )
        self._frontier = index

    # --- Los dos niveles ----------------------------------------------------

    def levels(
        self,
        *,
        direction: ImpulseDirection,
        extreme: float,
        index_extreme: int,
        anchor: float,
        index_anchor: int,
        through: int,
    ) -> BreakLevels:
        """Los niveles vigentes de un ID con la información cerrada en `through`."""
        return BreakLevels(
            favor=self.last_level(
                direction=direction,
                extreme=extreme,
                index_extreme=index_extreme,
                through=through,
            ),
            against=self.order_block_level(
                direction=direction,
                anchor=anchor,
                index_anchor=index_anchor,
                through=through,
            ),
        )

    def last_level(
        self,
        *,
        direction: ImpulseDirection,
        extreme: float,
        index_extreme: int,
        through: int,
    ) -> SideLevel:
        """Lado a favor: el UL, que existe siempre porque todo ID tiene extremo.

        `index_extreme` es la vela del extremo **de la constitución**, no la del
        extremo vigente: el UL se marca una vez y no se remarca. El borde interior
        es la línea que el ID tenía al nacer; el exterior, la punta de la mecha de
        esa vela, estirada a la siguiente **si ya ha cerrado** y llega más lejos.
        La comparación es estricta: con empate manda la primera vela que llegó al
        nivel, igual que en la fase 2.0.
        """
        self._require_visible(index_extreme, through, "la vela del extremo")
        inner = self._series.body_edge_towards(index_extreme, direction)
        outer = self._series.wick_tip_towards(index_extreme, direction)
        margin = index_extreme + 1
        if margin <= through:
            reach = self._series.wick_tip_towards(margin, direction)
            if _is_beyond(reach, outer, direction):
                outer = reach
        return SideLevel(
            price=outer, line=extreme, inner=inner, source=BreakLevelSource.LAST
        )

    def order_block_level(
        self,
        *,
        direction: ImpulseDirection,
        anchor: float,
        index_anchor: int,
        through: int,
    ) -> SideLevel:
        """Lado en contra: el OB si ya está confirmado, y si no la línea del ancla.

        Un ID sin OB confirmado se rompe por línea en este lado, que es un estado
        legítimo y no un fallo. Si el OB se confirma más tarde, el ID pasa a
        regirse por él **desde la vela siguiente a la confirmación**, nunca antes.
        """
        self._require_visible(index_anchor, through, "la vela del ancla")
        if self._confirmation(index_anchor, direction, through) is None:
            return SideLevel(
                price=anchor, line=anchor, inner=anchor, source=BreakLevelSource.LINE
            )
        # La vela del ancla entera: `high` y `low`. Cuál de los dos es interior lo
        # decide el sentido en que se recorre —la rotura en contra de un ID
        # alcista baja, así que encuentra primero el `high` y cruza el `low`—.
        inner = self._series.wick_tip_towards(index_anchor, direction)
        outer = self._series.wick_tip_towards(index_anchor, direction.opposite())
        return SideLevel(
            price=outer, line=anchor, inner=inner, source=BreakLevelSource.ORDER_BLOCK
        )

    def order_block_confirmed_at(
        self, *, direction: ImpulseDirection, index_anchor: int, through: int
    ) -> int | None:
        """Vela que confirmó el OB, o `None` si a esa altura no lo estaba."""
        self._require_visible(index_anchor, through, "la vela del ancla")
        return self._confirmation(index_anchor, direction, through)

    # --- Interno ------------------------------------------------------------

    def _confirmation(
        self, index_anchor: int, direction: ImpulseDirection, through: int
    ) -> int | None:
        """Primera vela del color del impulso que supera la mecha de la del ancla.

        La búsqueda avanza y no vuelve: cada vela se mira una sola vez por ID, así
        que el coste total es lineal en la serie y no cuadrático.
        """
        key = (index_anchor, direction)
        found = self._confirmed.get(key)
        if found is not None:
            return found if found <= through else None

        colour = (
            BodyDirection.BULLISH
            if direction is ImpulseDirection.ALCISTA
            else BodyDirection.BEARISH
        )
        level = self._series.wick_tip_towards(index_anchor, direction)
        cursor = self._cursor.get(key, index_anchor + 1)
        while cursor <= through:
            if self._series.direction_of(cursor) is colour and _is_beyond(
                self._series.wick_tip_towards(cursor, direction), level, direction
            ):
                self._confirmed[key] = cursor
                return cursor
            cursor += 1
        self._cursor[key] = cursor
        return None

    def _require_visible(self, index: int, through: int, what: str) -> None:
        if index < 0 or index >= len(self._series):
            raise StructureError(
                f"[{self._timeframe}] Índice {index} fuera de la serie "
                f"({len(self._series)} velas)"
            )
        if through > self._frontier:
            raise LookaheadError(
                f"[{self._timeframe}] Se pidieron zonas con la información de la vela "
                f"{through}, que todavía no ha cerrado (frontera {self._frontier})"
            )
        if index > through:
            raise LookaheadError(
                f"[{self._timeframe}] {what} es la {index} y todavía no había cerrado "
                f"en la {through}: su zona no se puede consultar"
            )


def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
    """"Más allá" es estricto, igual que en toda la fase 1."""
    return price > level if direction is ImpulseDirection.ALCISTA else price < level


__all__ = [
    "AvoidedBreak",
    "BreakLevels",
    "SideLevel",
    "ZoneBreakLevels",
    "line_levels",
]
