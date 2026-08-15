"""El OB **suelto** de M15 (§1.4). Misma definición que el OB del módulo 2, sin ID.

En M15 no se calculan impulsos —es temporalidad de ejecución y no aporta
estructura propia—, así que aquí no hay ancla que señale la vela. La definición
del propietario se sostiene sola:

    una vela de color contrario a la dirección buscada que después es
    **superada, mecha incluida**, por una vela del color de la dirección.

La zona es esa vela **entera**, de `low` a `high`, mechas y cuerpo, exactamente
como el OB del módulo 2. `interior` es el borde que el precio encuentra primero
viniendo a favor de la dirección buscada, y `exterior` el que hay que cruzar para
dejar la zona atrás: en una entrada larga el interior es el `high` de la vela y
el exterior su `low`, que es donde irá el stop.

**Qué vela es "la" vela contraria.** El módulo 2 ancla en la *última* vela
contraria previa al arranque de la pierna (R-02, `A1_last_counter_body`). Aquí se
lee igual: la candidata de una vela `j` del color buscado es la última vela de
color contrario anterior a `j`. Entre las dos sólo puede haber velas del mismo
color que `j` o doji, así que no hay ambigüedad sobre cuál es.

**El doji no juega.** §2.2 lo declara neutro en todo el módulo: ni sirve de vela
contraria ni supera nada.

**Causalidad.** El OB suelto **no existe hasta que cierra la vela que lo supera**
(§7). Pedirlo antes lanza `LookaheadError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.zones import CandleSeries


@dataclass(frozen=True, slots=True)
class LooseOrderBlock:
    """Un OB de M15 sin ID detrás. Los mismos bordes que el OB del módulo 2."""

    timeframe: str
    direction: ImpulseDirection
    #: Vela contraria que define la zona.
    index_defining: int
    ts_defining: datetime
    #: Vela del color de la dirección que la superó, mecha incluida.
    index_confirmation: int
    ts_confirmation: datetime
    #: Borde que el precio encuentra primero viniendo a favor de la dirección.
    inner: float
    #: Borde que hay que cruzar para dejar la zona atrás. Ahí va el stop.
    outer: float

    @property
    def low(self) -> float:
        return min(self.inner, self.outer)

    @property
    def high(self) -> float:
        return max(self.inner, self.outer)

    @property
    def height(self) -> float:
        """Altura en USD. Puede ser exactamente cero y no se corrige en silencio:
        una vela sin rango produce una zona plana, sin stop y sin 1R, y esa señal
        se descarta con su propio motivo en el embudo."""
        return self.high - self.low

    @property
    def is_flat(self) -> bool:
        return self.height == 0.0

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def touched_by(self, high: float, low: float) -> bool:
        return low <= self.high and high >= self.low


def find_loose_order_block(
    series: CandleSeries,
    *,
    direction: ImpulseDirection,
    first: int,
    through: int,
    timeframe: str = "M15",
) -> LooseOrderBlock | None:
    """El **primer** OB suelto confirmado en `[first, through]`, o `None`.

    Se devuelve el primero por orden de confirmación porque es el primero que el
    propietario podría haber operado: elegir "el mejor" de la ventana exigiría
    conocer la ventana entera, que es justo lo que no se sabe en el momento de
    decidir.

    `through` es la última vela **ya cerrada**. Nada posterior se mira: el OB no
    existe hasta que cierra la vela que lo supera, y esa vela no puede ser una
    que todavía no ha cerrado.
    """
    if through >= len(series):
        raise LookaheadError(
            f"[{timeframe}] Se buscó un OB suelto hasta la vela {through}, que no "
            f"existe todavía (la serie tiene {len(series)} velas)"
        )
    if first < 0:
        raise StructureError(f"Índice inicial fuera de la serie: {first}")

    colour = _colour_of(direction)
    counter = _colour_of(direction.opposite())
    # La vela contraria puede estar **antes** de la ventana: lo que la ventana
    # acota es cuándo se confirma, que es cuando el OB empieza a existir.
    candidate: int | None = _last_before(series, first, counter)
    for index in range(first, through + 1):
        body = series.direction_of(index)
        if body is counter:
            candidate = index
            continue
        if body is not colour or candidate is None:
            continue
        if not _is_beyond(
            series.wick_tip_towards(index, direction),
            series.wick_tip_towards(candidate, direction),
            direction,
        ):
            continue
        return LooseOrderBlock(
            timeframe=timeframe,
            direction=direction,
            index_defining=candidate,
            ts_defining=series.at(candidate),
            index_confirmation=index,
            ts_confirmation=series.at(index),
            inner=series.wick_tip_towards(candidate, direction),
            outer=series.wick_tip_towards(candidate, direction.opposite()),
        )
    return None


def _last_before(series: CandleSeries, first: int, colour: BodyDirection) -> int | None:
    """Última vela de color `colour` estrictamente anterior a `first`.

    Sirve de candidata inicial de la ventana: la vela contraria que da forma al
    OB puede ser anterior a la confirmación de H1 —lo normal es que lo sea— y lo
    que la ventana acota es **cuándo se supera**, que es cuando el OB existe.
    """
    for index in range(first - 1, -1, -1):
        if series.direction_of(index) is colour:
            return index
    return None


def _colour_of(direction: ImpulseDirection) -> BodyDirection:
    return (
        BodyDirection.BULLISH
        if direction is ImpulseDirection.ALCISTA
        else BodyDirection.BEARISH
    )


def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
    """"Superada" es estricto, igual que en toda la fase 1."""
    return price > level if direction is ImpulseDirection.ALCISTA else price < level


__all__ = ["LooseOrderBlock", "find_loose_order_block"]
