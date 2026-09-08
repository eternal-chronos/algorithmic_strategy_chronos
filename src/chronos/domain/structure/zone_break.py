"""Los niveles con que las zonas sustituyen a las dos líneas del ID.

La fase 1 mata un ID cuando una vela cierra más allá de una de sus dos líneas.
Con `break_by_zone` la línea deja de ser el nivel de rotura **cuando existe una
zona que la sustituya**. Romper una zona es cerrar más allá de su borde
**exterior**, es decir atravesarla entera: perforarla con mecha o cerrar dentro
no rompe.

    lado a favor (extremo) -> UL si existe, y existe siempre
    lado en contra (ancla) -> PUL:  el UL del ID inmediatamente anterior cuando
                                    iba en el MISMO sentido que éste
                              APUL: la zona en contra que le prestó aquel ID —si
                                    iba al revés—, o el extremo del último ID
                                    interior contrario del retroceso cuando en
                                    medio se abortó una constitución
                              la línea sólo si no hay ningún ID detrás

En el lado en contra hay **dos** zonas posibles y nunca mandan las dos a la vez:
cuál manda se decide al constituirse el ID y no se recalcula después.

**El lado en contra puede no gobernarse por zona.** Con `against_by_zone`
apagado —la regla del propietario desde la fase 3.0— el ID muere en ese lado
cuando una vela cierra más allá del **ancla**, y la zona en contra se sigue
clasificando y dibujando porque de ella cuelgan el toque y la cascada. El lado a
favor lo manda el UL en las dos variantes.

Este módulo no decide nada de eso: recibe qué zona gobierna, sobre qué velas
cuelga y hacia dónde iba el ID que la fijó —que es lo que dice qué borde es el
exterior—, calcula los dos niveles vigentes en un instante y dice de dónde sale
cada uno. Quién los compara con el cierre es el detector.

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
- La zona en contra sale de velas que cerraron antes de que el ID naciera —las de
  un ID que ya había muerto, o las de uno que se quedó dentro de un retroceso—,
  así que gobierna desde la primera vela que se juzga y no hay nada que esperar.
  Su punta se busca en la ventana de la vida de aquel ID, que también está
  entera en el pasado, y la comprobación se hace igual contra la última vela de
  esa ventana. Por lo mismo no hereda la vela de margen del UL: aquella regla es
  la del extremo recién fijado.
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
from chronos.domain.structure.zones import (
    CandleSeries,
    ZoneKind,
    against_edges,
    furthest_wick_index,
)


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
class AgainstZone:
    """Qué gobierna el lado en contra de un ID, y sobre qué velas.

    Se fija en la constitución y viaja con el impulso. No se re-deriva más tarde
    a propósito: la vela es la del extremo de un ID que acababa de morir —o la de
    un ID interior de su retroceso, que no está en ninguna lista, o la que le
    prestó el ID anterior—, y en cuanto nace otro impulso detrás esa respuesta ya
    no se puede reconstruir mirando la lista de impulsos.
    """

    #: `PENULTIMATE`, `ANTE_PENULTIMATE` o `LINE`. Nunca `LAST`: ése es el otro
    #: lado del ID.
    source: BreakLevelSource
    #: Vela del borde del cuerpo. `None` exactamente cuando manda la línea.
    index: int | None
    #: Hacia dónde iba el ID que fijó esa mecha. Decide qué borde de la zona es
    #: el interior y cuál el exterior, y por eso viaja con ella. `None` con la
    #: línea.
    direction: ImpulseDirection | None = None
    #: Velas entre las que se busca la punta: la vida entera del ID que fijó la
    #: zona, del arranque de su pierna a la vela anterior a la que lo rompió.
    #: Quien lee la mecha es `ZoneBreakLevels`; el detector sólo apunta el tramo,
    #: que sale de índices que ya tenía. `None` con la línea.
    tip_window: tuple[int, int] | None = None
    #: `True` sólo en el APUL **heredado**: la zona no sale de ningún extremo del
    #: ID anterior sino del nivel que aquél llevaba en su propio lado en contra,
    #: y puede venir de varios ID atrás. Los otros dos APUL —el UL del ID
    #: anterior contrario que dejó su extremo detrás, y el del ID interior de un
    #: retroceso— salen de un extremo, así que van con `False`. No se puede
    #: deducir de los bordes y son tres historias distintas.
    inherited: bool = False

    def __post_init__(self) -> None:
        if self.source is BreakLevelSource.LAST:
            raise StructureError("El UL no gobierna el lado en contra de un ID")
        if self.inherited and self.source is not BreakLevelSource.ANTE_PENULTIMATE:
            raise StructureError(
                f"Sólo el APUL se hereda, y esta zona es {self.source.value}"
            )
        if (self.source is BreakLevelSource.LINE) != (self.index is None):
            raise StructureError(
                f"Lado en contra incoherente: {self.source.value} con vela {self.index}"
            )
        if (self.index is None) != (self.direction is None):
            raise StructureError(
                "El lado en contra necesita la dirección del ID que fijó su vela"
            )
        if (self.index is None) != (self.tip_window is None):
            raise StructureError(
                "El lado en contra necesita la ventana en la que buscar su mecha"
            )


#: Lado en contra sin zona detrás: manda la línea del ancla.
AGAINST_BY_LINE = AgainstZone(source=BreakLevelSource.LINE, index=None)


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


def line_level(price: float) -> SideLevel:
    """Un lado gobernado por su línea: no hay zona que la sustituya, o no manda."""
    return SideLevel(
        price=price, line=price, inner=price, source=BreakLevelSource.LINE
    )


def line_levels(
    *, extreme: float, anchor: float
) -> BreakLevels:
    """Los niveles de la fase 1: las dos líneas, sin zonas de por medio.

    Es lo que devuelve `BREAK_BY_ZONE = false`, y por eso el interruptor apagado
    reproduce la línea base sin que el detector tenga dos caminos distintos.
    """
    return BreakLevels(favor=line_level(extreme), against=line_level(anchor))


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
        against: AgainstZone,
        through: int,
        against_by_zone: bool = True,
    ) -> BreakLevels:
        """Los niveles vigentes de un ID con la información cerrada en `through`.

        `against_by_zone` apagado es la regla del propietario a partir de la fase
        3.0: **el lado a favor lo manda el UL y el lado en contra el ancla**. La
        zona en contra se sigue clasificando y dibujando —de ella cuelgan el
        toque y la cascada—, pero no decide la vida del ID.
        """
        return BreakLevels(
            favor=self.last_level(
                direction=direction,
                extreme=extreme,
                index_extreme=index_extreme,
                through=through,
            ),
            against=(
                self.against_level(
                    direction=direction,
                    anchor=anchor,
                    against=against,
                    through=through,
                )
                if against_by_zone
                else line_level(anchor)
            ),
        )

    def against_level(
        self,
        *,
        direction: ImpulseDirection,
        anchor: float,
        against: AgainstZone,
        through: int,
    ) -> SideLevel:
        """Lado en contra: el PUL, el APUL, o la línea del ancla si no hay zona.

        Cuál de los tres manda no se decide aquí —lo trae `against`, que fijó la
        constitución del ID—: aquí sólo se leen las velas que se dicen, con la
        geometría que le toca a su zona.

        Las dos son el UL de un ID que ya murió, así que se leen igual: el borde
        del cuerpo de la vela de su extremo y la punta de la mecha más lejana que
        alcanzó mientras vivía. Qué borde es el exterior lo dice hacia dónde
        miraba aquel extremo, y de eso se encarga `against_edges`.
        """
        if (
            against.source is BreakLevelSource.LINE
            or against.index is None
            or against.direction is None
            or against.tip_window is None
        ):
            return line_level(anchor)
        what = "la vela del PUL" if against.source is BreakLevelSource.PENULTIMATE else "la vela del APUL"
        self._require_visible(against.index, through, what)
        self._require_visible(against.tip_window[1], through, f"la mecha de {what}")
        index_tip = furthest_wick_index(
            self._series, against.tip_window, against.direction
        )
        inner, outer = against_edges(
            self._series,
            index_body=against.index,
            index_tip=index_tip,
            zone_direction=against.direction,
            direction=direction,
        )
        return SideLevel(
            price=outer, line=anchor, inner=inner, source=against.source
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
    "AGAINST_BY_LINE",
    "AgainstZone",
    "AvoidedBreak",
    "BreakLevels",
    "SideLevel",
    "ZoneBreakLevels",
    "line_level",
    "line_levels",
]
