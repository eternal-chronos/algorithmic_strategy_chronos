"""Vía 1 de la fase 3.1: el **turtle soup** de H1. Dos velas y nada más.

    La primera deja una mecha en la dirección del movimiento previo. La segunda
    llega a esa mecha y cierra sin superarla: su recorrido alcanza el extremo de
    la mecha anterior pero su cierre queda del lado de acá. No lo supera con
    cuerpo: lo rechaza.

**Sin parámetros, sin umbrales, sin percentiles.** La relación entre las dos
velas es la definición completa, y por eso este módulo no recibe ninguna
configuración ni mira ninguna distribución: es lo contrario de `R2`, que
necesitaba sesiones anteriores para existir.

**Qué mecha, y por qué.** El movimiento previo es el que trajo el precio a la
zona, así que va en contra de lo que se busca: para una entrada **alcista** la
vela que la trae es la que hace suelo, la mecha que interesa es la **inferior** y
lo que confirma es que la segunda vela baje a ese mínimo y **cierre por encima**
de él. En una entrada bajista, todo del revés. Escrito al derecho: la mecha se
mide del lado contrario a la dirección buscada, y la confirmación es que el
precio va a buscarla y no se queda ahí.

**Consecutivas quiere decir consecutivas.** La segunda vela es `index` y la
primera es `index - 1`, sin excepciones: con una vela en medio el patrón no
existe, y ésa es una de las cuatro formas de no confirmar que el §4 enumera.

**Causalidad.** Las dos velas están cerradas cuando se evalúa `index`: aquí no
hay nada que pedir por adelantado y por eso no hay frontera que declarar. Pedir
una vela que no existe es un error de índice, no un lookahead, y se dice así.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import CandleSeries


@dataclass(frozen=True, slots=True)
class TurtleSoup:
    """Un turtle soup encontrado, con lo que hace falta para dibujarlo.

    `extreme` es el extremo de la mecha de la primera vela —el nivel que la
    segunda va a buscar y no consigue superar con el cierre— y es el precio sobre
    el que se marca el patrón en el explorador y en las capturas.
    """

    #: Índice de la **segunda** vela: la que rechaza. El patrón se fecha ahí
    #: porque es cuando se sabe, no cuando empezó.
    index: int
    direction: ImpulseDirection
    #: Extremo de la mecha de la primera vela.
    extreme: float
    #: Cierre de la segunda: el que se queda de este lado del extremo.
    close: float

    @property
    def index_first(self) -> int:
        return self.index - 1


def wick_extreme(
    series: CandleSeries, index: int, direction: ImpulseDirection
) -> float | None:
    """Extremo de la mecha del movimiento previo, o `None` si esa vela no la tiene.

    Para una dirección buscada **alcista** es el `low` de la vela, y sólo cuenta
    si hay mecha de verdad: el mínimo tiene que quedar por debajo del cuerpo. Una
    vela que cierra en su mínimo no ha dejado ninguna mecha ahí y no puede ser la
    primera mitad del patrón.
    """
    if not 0 <= index < len(series):
        raise StructureError(f"Índice {index} fuera de la serie ({len(series)} velas)")
    open_ = float(series.open[index])
    close = float(series.close[index])
    if direction is ImpulseDirection.ALCISTA:
        low = float(series.low[index])
        return low if low < min(open_, close) else None
    high = float(series.high[index])
    return high if high > max(open_, close) else None


def find_turtle_soup(
    series: CandleSeries, index: int, direction: ImpulseDirection
) -> TurtleSoup | None:
    """El patrón sobre las velas `index - 1` e `index`, o `None`.

    Las tres formas de no confirmar, en el orden en que se comprueban:

    1. la primera vela no dejó mecha en el lado que toca —no hay extremo que
       rechazar—;
    2. la segunda **no llega** a ese extremo: su recorrido se queda corto;
    3. la segunda **lo supera con el cierre**: eso no es un rechazo, es una
       rotura, y el patrón dice exactamente lo contrario.

    La cuarta —que las dos velas no sean consecutivas— no se comprueba: está en
    la firma. La primera vela es siempre `index - 1`.
    """
    if not 0 <= index < len(series):
        raise StructureError(f"Índice {index} fuera de la serie ({len(series)} velas)")
    if index == 0:
        return None
    extreme = wick_extreme(series, index - 1, direction)
    if extreme is None:
        return None

    close = float(series.close[index])
    if direction is ImpulseDirection.ALCISTA:
        reaches = float(series.low[index]) <= extreme
        respects = close > extreme
    else:
        reaches = float(series.high[index]) >= extreme
        respects = close < extreme
    if not (reaches and respects):
        return None
    return TurtleSoup(index=index, direction=direction, extreme=extreme, close=close)


__all__ = ["TurtleSoup", "find_turtle_soup", "wick_extreme"]
