"""Los niveles con que la fase 2.1 sustituye a las dos líneas del ID.

La fase 1 mata un ID cuando una vela cierra más allá de una de sus dos líneas.
A partir de la fase 2.1 la línea deja de ser el nivel de rotura **cuando existe
una zona que la sustituya**: el UL en el lado a favor y el PUL en el lado en
contra. Romper una zona es cerrar más allá de su borde **exterior**, es decir
atravesarla entera; perforarla con mecha o cerrar dentro no rompe.

    lado a favor (extremo) -> UL si existe, y existe siempre
    lado en contra (ancla) -> PUL si hay ID anterior; si no, la línea

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
- El PUL sale de una vela que cerró antes de que el ID naciera —la del extremo
  del ID anterior, la misma que llevaba su UL—, así que gobierna desde la
  primera vela que se juzga y no hay nada que esperar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chronos.domain.structure.enums import (
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
        index_penultimate: int | None,
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
            against=self.penultimate_level(
                direction=direction,
                anchor=anchor,
                index_penultimate=index_penultimate,
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

    def penultimate_level(
        self,
        *,
        direction: ImpulseDirection,
        anchor: float,
        index_penultimate: int | None,
        through: int,
    ) -> SideLevel:
        """Lado en contra: el PUL si el ID tiene uno, y si no la línea del ancla.

        Sólo se queda sin PUL el primer ID del histórico, que no tiene ID
        anterior del que sacar la vela: ése se rompe por línea en este lado, que
        es un estado legítimo y no un fallo. El PUL no se confirma ni se estira:
        su vela ya estaba cerrada cuando el ID nació, así que el borde contra el
        que se juzga es el mismo desde la primera vela hasta la última.
        """
        if index_penultimate is None:
            return SideLevel(
                price=anchor, line=anchor, inner=anchor, source=BreakLevelSource.LINE
            )
        self._require_visible(index_penultimate, through, "la vela del PUL")
        # El cuerpo de la vela del extremo anterior, sin mechas. Cuál de los dos
        # bordes es interior lo decide el sentido en que se recorre —la rotura en
        # contra de un ID alcista baja, así que encuentra primero el borde alto
        # del cuerpo y cruza el bajo—.
        inner = self._series.body_edge_towards(index_penultimate, direction)
        outer = self._series.body_edge_towards(index_penultimate, direction.opposite())
        return SideLevel(
            price=outer, line=anchor, inner=inner, source=BreakLevelSource.PENULTIMATE
        )

    # --- Interno ------------------------------------------------------------

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
