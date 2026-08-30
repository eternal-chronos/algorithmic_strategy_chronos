"""Lo que hizo el precio **después** de una señal. Aritmética pura sobre arrays.

Aquí no hay entrada, ni orden, ni stop, ni tamaño, ni resultado de una operación:
hay una medida. La fase 3.0 produce marcas sobre el gráfico y la pregunta que
este módulo contesta es la única que se puede contestar sin abrir nada: cuánto
recorrió el precio a favor y en contra a partir de la marca, y si llegó a
recorrerlo antes de que la señal quedara desmentida.

**La vara es `R`**: la distancia del precio de la señal al borde **exterior** de
la zona que la produjo, que es el sitio donde esa señal deja de tener sentido —el
precio ha atravesado la zona entera—. Medir en R es lo que permite poner en la
misma tabla un OB de tres dólares y uno de treinta sin inventarse un tamaño de
posición ni una gestión que todavía no existe.

**Desmentir es cerrar, no perforar.** Igual que en la fase 2.1: la señal queda
desmentida cuando una vela **cierra** más allá del borde exterior. Una mecha que
se pasa y vuelve no desmiente nada, y por eso el recorrido en contra puede pasar
de 1R sin que la señal esté muerta; las dos cosas se reportan por separado.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError


@dataclass(frozen=True, slots=True)
class Excursion:
    """Recorrido de una señal, en múltiplos de R."""

    #: Máximo recorrido **a favor**. Puede salir negativo: si el precio se fue en
    #: contra desde la primera vela, nunca llegó a estar a favor y eso es el dato.
    favor: float
    #: Máximo recorrido **en contra**, positivo. Se mide con mechas, así que puede
    #: superar 1R sin que ninguna vela haya cerrado fuera.
    against: float
    #: Velas observadas. Cero cuando la observación se quedó sin tramo.
    bars: int
    #: Por cada objetivo pedido, la primera vela que lo alcanzó, o `None`.
    reached: tuple[int | None, ...]


def first_adverse_close(
    close: np.ndarray, *, outer: float, direction: ImpulseDirection
) -> int | None:
    """Primera vela cuyo **cierre** queda más allá del borde exterior de la zona.

    `direction` es la del impulso que puso la zona: en un ID alcista el precio
    tiene que caer por debajo del borde exterior para desmentir la señal, y en uno
    bajista subir por encima. `None` si ninguna lo hace en el tramo dado.
    """
    beyond = close < outer if direction is ImpulseDirection.ALCISTA else close > outer
    hits = np.flatnonzero(beyond)
    return int(hits[0]) if hits.size else None


def measure_excursion(
    *,
    high: np.ndarray,
    low: np.ndarray,
    price: float,
    risk: float,
    direction: ImpulseDirection,
    targets: tuple[float, ...],
) -> Excursion:
    """Recorrido a favor y en contra en `[0, n)`, ya recortado por quien llama.

    El tramo llega recortado a propósito: dónde empieza la observación y hasta
    dónde se mira —la vela que desmiente, el tope, o el fin del histórico— son
    decisiones de la capa que conoce las series, no de la aritmética.

    Un objetivo se da por alcanzado con la **mecha**: llegar es llegar, aunque la
    vela cierre lejos. Y se lee sobre el máximo acumulado, así que el índice que
    sale es el de la primera vela en la que el precio ya había llegado.
    """
    if risk <= 0:
        raise StructureError("Una señal sin riesgo no se puede medir en R")
    if high.size != low.size:
        raise StructureError("Las series de la observación no miden lo mismo")
    if high.size == 0:
        return Excursion(favor=0.0, against=0.0, bars=0, reached=(None,) * len(targets))

    if direction is ImpulseDirection.ALCISTA:
        favor_run = (high - price) / risk
        against_run = (price - low) / risk
    else:
        favor_run = (price - low) / risk
        against_run = (high - price) / risk

    running = np.maximum.accumulate(favor_run)
    reached: list[int | None] = []
    for target in targets:
        hits = np.flatnonzero(running >= target)
        reached.append(int(hits[0]) if hits.size else None)

    return Excursion(
        favor=float(favor_run.max()),
        against=float(against_run.max()),
        bars=int(high.size),
        reached=tuple(reached),
    )


__all__ = ["Excursion", "first_adverse_close", "measure_excursion"]
