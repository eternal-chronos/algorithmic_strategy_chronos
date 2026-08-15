"""Qué zona estaba vigente en cada barra, sin mirar al futuro (§7).

La fase 2.0 calcula **una** zona UL por impulso: la que le quedó al morir. Con la
rotura por zona de la fase 2.1 un ID vigente puede **extender su extremo**, y
cada extensión mueve el UL a otra vela. Preguntarle a `ImpulseZones.last` cuál
era el UL en una barra anterior a la última extensión sería leer una zona que en
esa barra no existía todavía: exactamente el lookahead que el §7 prohíbe, y en el
peor sitio posible, porque las velas que extienden el extremo son las mismas que
tocan la zona.

Este módulo reconstruye la línea temporal del UL desde la traza de extensiones
que el detector ya registra. No re-ejecuta nada y no cambia ninguna zona: al
final del ID devuelve la misma que la fase 2.0, y hay un test que lo fija.

El OB no necesita línea temporal: lo fija la vela del ancla, que no cambia nunca.
Lo que sí varía es **cuándo empieza a existir** —no antes de su confirmación— y
eso ya viaja en `Zone.ts_birth`.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind, last_zone


@dataclass(frozen=True, slots=True)
class ZoneSegment:
    """Un UL y el tramo de barras en el que fue **el** UL del impulso."""

    #: Primera barra cerrada en la que este UL ya gobernaba.
    first: int
    #: Última barra en la que seguía gobernando. `None` = hasta el fin del ID.
    last: int | None
    zone: Zone

    def covers(self, index: int) -> bool:
        return index >= self.first and (self.last is None or index <= self.last)


class LastZoneTimeline:
    """Los UL sucesivos de un impulso, consultables barra a barra.

    Cada extensión del extremo abre un tramo nuevo. Dentro de un tramo el UL
    todavía puede crecer una vez más, por la **vela de margen**: si la vela
    inmediatamente posterior a la del extremo llega más lejos, el borde exterior
    se estira hasta ella. Esa vela también tiene su hora: hasta que no cierra, el
    borde exterior es el de la vela del extremo y no otro.
    """

    def __init__(self, series: CandleSeries, impulse: DominantImpulse) -> None:
        self._series = series
        self._impulse = impulse
        self._frontier = -1
        self._end = impulse.index_end
        first_extreme = impulse.index_extreme_at_constitution
        if first_extreme < 0:
            raise StructureError(
                f"El impulso {impulse.id_num} no registró la vela de su extremo inicial"
            )
        #: (barra desde la que gobierna, vela que fija el extremo).
        self._points: tuple[tuple[int, int], ...] = (
            (impulse.index_constitution, first_extreme),
            *(
                (extension.index, extension.index)
                for extension in impulse.extension_trail
            ),
        )

    @property
    def frontier(self) -> int:
        return self._frontier

    @property
    def segments(self) -> tuple[ZoneSegment, ...]:
        """Todos los tramos del UL, para dibujarlos o auditarlos de una vez.

        Cada tramo lleva el UL **ya cerrado**, es decir con su vela de margen
        aplicada si la hubo: es la foto del UL tal como quedó, no la que se veía
        en su primera barra. Para la lectura causal barra a barra está `at()`.
        """
        built: list[ZoneSegment] = []
        for position, (first, extreme) in enumerate(self._points):
            following = (
                self._points[position + 1][0] - 1
                if position + 1 < len(self._points)
                else self._end
            )
            built.append(
                ZoneSegment(
                    first=first,
                    last=following,
                    zone=self._zone_from(extreme, through=len(self._series) - 1),
                )
            )
        return tuple(built)

    def advance(self, index: int) -> None:
        """Declara que la barra `index` ya ha cerrado."""
        if index < self._frontier:
            raise StructureError("La frontera de la línea temporal del UL no retrocede")
        if index >= len(self._series):
            raise StructureError(
                f"Índice {index} fuera de la serie ({len(self._series)} velas)"
            )
        self._frontier = index

    def at(self, index: int) -> Zone:
        """El UL vigente al cierre de `index`, con lo que se sabía en ese cierre."""
        if index > self._frontier:
            raise LookaheadError(
                f"[{self._impulse.timeframe}] Se pidió el UL del ID "
                f"{self._impulse.id_num} en la barra {index}, que todavía no ha "
                f"cerrado (frontera {self._frontier})"
            )
        if index < self._impulse.index_constitution:
            raise LookaheadError(
                f"[{self._impulse.timeframe}] El ID {self._impulse.id_num} no existe "
                f"en la barra {index}: se constituye en la {self._impulse.index_constitution}"
            )
        extreme = self._points[0][1]
        for first, candidate in self._points:
            if first > index:
                break
            extreme = candidate
        return self._zone_from(extreme, through=index)

    def _zone_from(self, index_extreme: int, *, through: int) -> Zone:
        zone = last_zone(
            self._series,
            id_num=self._impulse.id_num,
            timeframe=self._impulse.timeframe,
            direction=self._impulse.direction,
            index_extreme=index_extreme,
            ts_constitution=self._impulse.ts_constitution,
        )
        # `last_zone` mira siempre la vela de margen porque la fase 2.0 dibuja el
        # UL ya terminado. Aquí la vela de margen sólo cuenta si ya ha cerrado:
        # antes de eso el borde exterior es el de la vela del extremo.
        if zone.extended and index_extreme + 1 > through:
            return last_zone(
                _without(self._series, index_extreme + 1),
                id_num=self._impulse.id_num,
                timeframe=self._impulse.timeframe,
                direction=self._impulse.direction,
                index_extreme=index_extreme,
                ts_constitution=self._impulse.ts_constitution,
            )
        return zone


def _without(series: CandleSeries, index: int) -> CandleSeries:
    """La misma serie recortada justo antes de `index`.

    Es la forma más honesta de decirle a `last_zone` que la vela de margen aún no
    existe: en vez de copiar su lógica con una bandera, se le quita la vela. La
    alternativa —duplicar el cálculo del borde exterior aquí— dejaría dos sitios
    donde arreglar la regla del UL y sólo uno se acordaría.
    """
    return CandleSeries(
        timestamps=series.timestamps[:index],
        open=series.open[:index],
        high=series.high[:index],
        low=series.low[:index],
        close=series.close[:index],
    )


def zone_is_born(zone: Zone, index: int, series: CandleSeries) -> bool:
    """`True` si la zona ya existía al cierre de la barra `index`.

    El OB nace al confirmarse; el UL, al constituirse el ID. Los dos llevan su
    `ts_birth` desde la fase 2.0 y aquí sólo se traduce a índice.
    """
    if index < 0 or index >= len(series):
        return False
    return series.at(index) >= zone.ts_birth


def touches(zone: Zone, high: float, low: float) -> bool:
    """§1.1 y §1.2 — el `high`/`low` de la vela alcanza cualquier punto de la zona.

    Basta el contacto: no hace falta cerrar dentro. Los bordes cuentan, igual que
    en `Zone.contains`.
    """
    return low <= zone.high and high >= zone.low


def first_retest_after_break(
    series: CandleSeries,
    zone: Zone,
    *,
    index_break: int,
    through: int,
) -> int | None:
    """§1.2 — el retesteo de una zona UL rota, **en la temporalidad del ID**.

    La regla que esta función existe para escribir una sola vez: la vela que
    rompe tiene que **cerrar primero**, y el retesteo sólo lo valida **otra vela
    de esa misma temporalidad, posterior**. Un toque de la zona en una
    temporalidad menor dentro de la misma vela que rompió NO vale.

    No es una restricción de comodidad. Mientras la vela de la rotura no cierra,
    la rotura todavía no es un hecho: es una vela en formación que puede acabar
    dentro de la zona, y entonces no habría roto nada. Aceptar como retesteo un
    toque anterior a ese cierre sería dar por buena una reacción a un suceso que
    aún no había ocurrido —lookahead—, y además convertiría en retesteo el propio
    recorrido de salida: la vela que atraviesa la zona entera pasa por ella
    obligatoriamente, así que **toda** rotura vendría con retesteo gratis y la
    invalidación del §1.2 no descartaría nunca nada.

    `series` tiene que ser la de la temporalidad del ID —Diario, H4 o H1—: es lo
    que hace que la regla se lea igual en las tres. `through` es la última vela en
    la que todavía se espera el retesteo, inclusive.

    Devuelve la **primera** vela posterior que toca la zona, o `None` si ninguna
    lo hace dentro de la ventana.
    """
    for position in range(index_break + 1, min(through, len(series) - 1) + 1):
        if touches(zone, float(series.high[position]), float(series.low[position])):
            return position
    return None


def break_direction(zone: Zone) -> ImpulseDirection:
    """Hacia dónde hay que cerrar para atravesar la zona entera.

    El UL se cruza **a favor** del ID —está pegado al extremo— y el OB, en
    contra: está sobre la vela del ancla. Es la misma lectura de la fase 2.1, y
    se deriva de la dirección del impulso y no de comparar los dos bordes, que
    en un ID de rango no positivo salen invertidos.
    """
    if zone.kind is ZoneKind.LAST:
        return zone.direction
    return zone.direction.opposite()


def breaks(zone: Zone, close: float) -> bool:
    """§1.2 — romper es cerrar más allá del borde **exterior**, la regla de la 2.1.

    Se escribe aquí una sola vez y en los mismos términos que la fase 2.1:
    atravesar la zona entera. Perforarla con mecha y cerrar dentro no rompe;
    cerrar dentro tampoco. Una zona de altura cero tiene los dos bordes en el
    mismo precio y se comporta exactamente como la línea.
    """
    if break_direction(zone) is ImpulseDirection.ALCISTA:
        return close > zone.outer
    return close < zone.outer


__all__ = [
    "LastZoneTimeline",
    "ZoneSegment",
    "break_direction",
    "breaks",
    "first_retest_after_break",
    "touches",
    "zone_is_born",
]
