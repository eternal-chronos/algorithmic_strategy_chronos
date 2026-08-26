"""Vía 2 de la confirmación en H1: el **OB de H1**. Sin impulso detrás.

En el Diario y en H4 un OB es la vela del ancla de un ID: existe porque existe el
impulso. **En H1 no hay ID** —el módulo 1 no detecta estructura en esa
temporalidad y no se va a inventar aquí—, así que este OB es otra cosa y por eso
vive en su propio fichero, con su propia definición, y no reutiliza
`domain/structure/zones.py`.

Tal como lo dice el propietario, en dirección **alcista**:

    el precio llega a la zona de H4 con velas bajistas —rojas—; se forma una vela
    verde que supera, mecha incluida, el máximo de la vela roja anterior. Si no lo
    consigue la primera vela verde, se puede esperar a la segunda; si en la
    segunda tampoco, se desecha.

Puesto en términos de la serie:

- **La vela de referencia** es la vela contraria a la dirección buscada que está
  **inmediatamente antes** de la primera vela a favor del tramo. Es la que trajo
  el precio a la zona, y es la que hace de order block: la zona es su vela entera,
  de `low` a `high`, igual que el OB del módulo 2.
- **Superar** es lo mismo que en `order_block_zone`: el máximo de la vela a favor
  pasa del máximo de la de referencia, mecha contra mecha y con desigualdad
  estricta. Con empate no se supera nada.
- **Dos oportunidades y no más**, y las dos contra el **mismo** nivel: la primera
  vela a favor del tramo y la siguiente vela a favor que aparezca, con lo que
  haya en medio. Si la segunda tampoco lo consigue, esta vía queda cerrada; el
  turtle soup sigue buscando por su cuenta.

El **doji no cuenta** para nada: ni abre el intento ni sirve de referencia, la
misma neutralidad que §2.2 le da en todo el módulo. Una vela sin cuerpo no dice
que el precio haya girado.

**Causalidad.** Todo lo que se mira está cerrado cuando se evalúa: la referencia
está detrás de la primera vela a favor, y cada oportunidad se juzga con su propia
vela ya cerrada. El tramo `[first, last]` lo decide quien llama —arranca en la
vela del toque del OB de H4 y termina cuando el precio abandona esa zona—, así
que aquí no se busca ninguna vela ni se lee ningún reloj.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import CandleSeries


@dataclass(frozen=True, slots=True)
class HourlyOrderBlock:
    """Un intento de OB en H1: el que confirma y el que se desecha son el mismo.

    Se devuelve también cuando **no** confirma porque el descarte es material de
    auditoría: el propietario quiere ver dónde la máquina miró un OB y por qué no
    lo dio por bueno, y una vía que falla en silencio no se puede corregir.
    """

    direction: ImpulseDirection
    #: Vela de referencia: la contraria a `direction` que precede al primer
    #: intento. Es el order block; su vela entera es la zona.
    index: int
    #: Nivel que hay que superar: la punta de su mecha en la dirección buscada.
    level: float
    low: float
    high: float
    #: Primera vela a favor del tramo: la primera oportunidad.
    index_first_attempt: int
    #: La siguiente vela a favor, si llegó a haberla dentro del tramo.
    index_second_attempt: int | None
    #: La oportunidad que superó el nivel, o `None` si ninguna lo hizo.
    index_confirmation: int | None

    @property
    def confirmed(self) -> bool:
        return self.index_confirmation is not None

    @property
    def attempt(self) -> int | None:
        """Cuál de las dos oportunidades confirmó: 1 la primera, 2 la segunda."""
        if self.index_confirmation is None:
            return None
        return 1 if self.index_confirmation == self.index_first_attempt else 2

    @property
    def exhausted(self) -> bool:
        """Las dos oportunidades se gastaron sin superar el nivel: vía cerrada.

        Se distingue de quedarse sin tramo —una sola oportunidad y el precio se
        fue de la zona antes de la segunda—, que no es un descarte del patrón
        sino el final de la ventana.
        """
        return not self.confirmed and self.index_second_attempt is not None


def find_hourly_order_block(
    series: CandleSeries, *, first: int, last: int, direction: ImpulseDirection
) -> HourlyOrderBlock | None:
    """El primer intento de OB del tramo `[first, last]`, confirme o no.

    `None` cuando en todo el tramo no llega a haber ni un intento: hace falta una
    vela a favor con una vela contraria justo detrás, y sin las dos no hay nada
    que mirar. La vela de referencia puede quedar **antes** de `first` —es la que
    trajo el precio a la zona, así que suele ser anterior al toque—; la que no
    puede quedar antes es la vela que confirma.
    """
    if not 0 <= first < len(series) or not 0 <= last < len(series):
        raise StructureError(f"Tramo [{first}, {last}] fuera de la serie ({len(series)} velas)")

    body = _body_of(direction)
    counter = _body_of(direction.opposite())

    for index in range(first, last + 1):
        if series.direction_of(index) is not body:
            continue
        if index == 0 or series.direction_of(index - 1) is not counter:
            # Una vela a favor sin vela contraria justo detrás no abre nada: lo
            # que se está buscando es el giro, no la continuación.
            continue
        return _attempt(series, index=index, last=last, direction=direction, body=body)
    return None


def _attempt(
    series: CandleSeries,
    *,
    index: int,
    last: int,
    direction: ImpulseDirection,
    body: BodyDirection,
) -> HourlyOrderBlock:
    """Las dos oportunidades del intento que arranca en la vela `index`."""
    reference = index - 1
    level = series.wick_tip_towards(reference, direction)

    second: int | None = None
    for candidate in range(index + 1, last + 1):
        if series.direction_of(candidate) is body:
            second = candidate
            break

    confirmation: int | None = None
    for attempt in (index, second):
        if attempt is None:
            continue
        if _is_beyond(series.wick_tip_towards(attempt, direction), level, direction):
            confirmation = attempt
            break

    return HourlyOrderBlock(
        direction=direction,
        index=reference,
        level=level,
        low=float(series.low[reference]),
        high=float(series.high[reference]),
        index_first_attempt=index,
        index_second_attempt=second,
        index_confirmation=confirmation,
    )


def _body_of(direction: ImpulseDirection) -> BodyDirection:
    return (
        BodyDirection.BULLISH
        if direction is ImpulseDirection.ALCISTA
        else BodyDirection.BEARISH
    )


def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
    """Estricto, igual que la confirmación del OB del módulo 2: el empate no pasa."""
    return price > level if direction is ImpulseDirection.ALCISTA else price < level


__all__ = ["HourlyOrderBlock", "find_hourly_order_block"]
