"""Las reglas puras de una entrada. **Aritmética sobre precios, nada más.**

Aquí no hay series, ni reloj, ni temporalidades: son las cuentas que la
orquestación de `application/entries/` hace en cada vela, escritas una vez para
que el dibujo, el CSV y el test digan lo mismo. Ninguna regla del módulo 1 ni de
las zonas lee nada de aquí.

Lo que decide cada función lo dictó el propietario el 2026-09-13 y se anota al
lado de cada una. Lo que no dictó y hubo que suponer está marcado como SUPUESTO.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError


class EntryKind(StrEnum):
    """Las dos maneras de entrar. Los valores salen al CSV y al explorador."""

    #: El ID de H1 rompe SU PUL —sólo el PUL, no el APUL ni el UL— sin romper
    #: el ID: se entra en contra del ID, en el PUL, cuando el precio vuelve a él,
    #: si M15 lleva un ID en esa misma dirección.
    ROTURA_PUL = "ROTURA_PUL"
    #: El precio vuelve a la zona en contra del ID de H1 —el PUL o el APUL— y se
    #: entra a favor del ID, en esa zona, si M15 lleva un ID en esa dirección.
    TOQUE_ZONA = "TOQUE_ZONA"


class TradeOutcome(StrEnum):
    """Cómo acabó una operación que sí entró."""

    OBJETIVO = "OBJETIVO"
    STOP = "STOP"
    #: Llegaron las 16:00 de Nueva York con la posición viva: se cierra al
    #: precio de esa vela, ni en ganancia entera ni en pérdida entera.
    CIERRE_SESION = "CIERRE_SESION"
    #: Se acabó el histórico con la posición abierta.
    ABIERTA = "ABIERTA"


class OrderEnd(StrEnum):
    """Por qué se quitó un límite que no llegó a llenarse."""

    #: Murió el ID de H1 del que colgaba, o entró en limbo.
    MUERTE_ID_H1 = "MUERTE_ID_H1"
    #: Se salió de la franja de operativa (las 12:00 de Nueva York).
    FIN_FRANJA = "FIN_FRANJA"
    #: El Diario o H4 dejaron de permitir esa dirección.
    CAMBIO_CONTEXTO = "CAMBIO_CONTEXTO"
    #: M15 dejó de llevar un ID en la dirección de la entrada.
    CAMBIO_M15 = "CAMBIO_M15"
    FIN_HISTORICO = "FIN_HISTORICO"


class ContextState(StrEnum):
    """El estado de un ID de temporalidad superior (Diario o H4) para las entradas.

    Es lo que decide qué direcciones se buscan más abajo. Los valores salen al
    explorador y al CSV.
    """

    #: Todavía no ha tocado su zona en contra: abajo se acepta cualquier ID.
    LIBRE = "LIBRE"
    #: Tocó su PUL o su APUL: abajo sólo se busca A FAVOR de este ID.
    A_FAVOR = "A_FAVOR"
    #: Una vela llegó a su UL y lo rechazó: abajo sólo se busca EN CONTRA de
    #: este ID (sólo H4: el propietario no lo dictó para el Diario).
    EN_CONTRA = "EN_CONTRA"
    #: Ya se hizo la entrada en contra tras el rechazo del UL: abajo se acepta
    #: cualquier ID y ni un nuevo rechazo ni un nuevo toque cambian nada,
    #: hasta que muera el ID.
    AGOTADO = "AGOTADO"


def first_close_beyond(
    close: np.ndarray,
    *,
    level: float,
    direction: ImpulseDirection,
    first: int,
    last: int,
) -> int | None:
    """Primera barra de `[first, last]` que CIERRA más allá de `level` en `direction`.

    Es la rotura del PUL de H1: cerrar más allá de su borde exterior, en el
    sentido contrario al ID. Perforarlo con mecha y cerrar dentro no rompe;
    cerrar dentro tampoco. El tramo lo recorta quien llama a la vida del ID: lo
    que pase después de su muerte no es una rotura del PUL, es otra historia.
    """
    if last < first:
        return None
    if first < 0 or last >= len(close):
        raise StructureError(f"Tramo [{first}, {last}] fuera de la serie ({len(close)} barras)")
    window = close[first : last + 1]
    beyond = window > level if direction is ImpulseDirection.ALCISTA else window < level
    hits = np.flatnonzero(beyond)
    return first + int(hits[0]) if hits.size else None


def beyond_price(entry: float, direction: ImpulseDirection, price: float) -> bool:
    """`True` si el límite queda al OTRO LADO del precio: por encima en una venta,
    por debajo en una compra. Un límite se pone donde el precio todavía no está."""
    if direction is ImpulseDirection.BAJISTA:
        return entry > price
    return entry < price


def nearest_stop(
    entry: float, direction: ImpulseDirection, candidates: Iterable[float | None]
) -> float | None:
    """El stop MÁS CERCANO a la entrada de entre los niveles ofrecidos.

    Regla del propietario (imagen 4 del 2026-09-13): el stop va detrás del PUL o
    del APUL de M15, salvo que ése quede más lejos que el de H1, en cuyo caso se
    deja en el de H1. Es decir, el más cercano de los dos. Un nivel que no queda
    al otro lado de la entrada —por debajo en una venta, por encima en una
    compra— no sirve de stop y se descarta. `None` si ninguno sirve.
    """
    valid = [
        float(level)
        for level in candidates
        if level is not None and _protects(entry, direction, float(level))
    ]
    if not valid:
        return None
    return min(valid) if direction is ImpulseDirection.BAJISTA else max(valid)


def _protects(entry: float, direction: ImpulseDirection, level: float) -> bool:
    """Un stop protege si está al otro lado de la entrada: arriba en venta, abajo en compra."""
    if direction is ImpulseDirection.BAJISTA:
        return level > entry
    return level < entry


def target_for(
    entry: float, stop: float, direction: ImpulseDirection, risk_reward: float
) -> float:
    """El objetivo a `risk_reward` veces la distancia del stop. Siempre 1:4."""
    if risk_reward <= 0:
        raise StructureError(f"El R:R tiene que ser positivo, no {risk_reward}")
    if not _protects(entry, direction, stop):
        raise StructureError(
            f"El stop {stop} no protege una entrada {direction.value} en {entry}"
        )
    step = abs(entry - stop) * risk_reward
    return entry - step if direction is ImpulseDirection.BAJISTA else entry + step


def fills(direction: ImpulseDirection, entry: float, high: float, low: float) -> bool:
    """`True` si esta vela llena el límite: el precio ha llegado hasta él."""
    if direction is ImpulseDirection.BAJISTA:
        return high >= entry
    return low <= entry


def resolve(
    direction: ImpulseDirection,
    *,
    stop: float,
    target: float,
    high: float,
    low: float,
) -> tuple[TradeOutcome, float] | None:
    """Cómo se cierra la operación con esta vela, o `None` si sigue abierta.

    **El stop manda**: cuando una misma vela alcanza los dos no se sabe en qué
    orden ocurrió dentro de ella y se cuenta la mala. SUPUESTO: el propietario
    no lo ha dictado; es la lectura prudente.
    """
    if direction is ImpulseDirection.BAJISTA:
        if high >= stop:
            return TradeOutcome.STOP, stop
        if low <= target:
            return TradeOutcome.OBJETIVO, target
        return None
    if low <= stop:
        return TradeOutcome.STOP, stop
    if high >= target:
        return TradeOutcome.OBJETIVO, target
    return None


__all__ = [
    "ContextState",
    "EntryKind",
    "OrderEnd",
    "TradeOutcome",
    "beyond_price",
    "fills",
    "first_close_beyond",
    "nearest_stop",
    "resolve",
    "target_for",
]
