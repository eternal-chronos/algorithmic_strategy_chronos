"""Contactos del precio con los dos límites de un ID (sección D).

Esto **mide**, no decide: ninguna regla del módulo 1 lee nada de aquí y la
detección de impulsos no cambia ni un número. Sirve para responder si lo que el
propietario llama lateralización —el precio toca arriba, toca abajo y repite—
tiene una firma reconocible en los ID que el motor ya detecta.

Los límites no son parámetros nuevos: son el `ancla` y el `extremo` del propio
ID, leídos como parte alta y parte baja del rango.

Tres categorías, todas determinables con los datos que ya existen:

- **TOQUE_MECHA** — la barra alcanza el nivel con la mecha pero **cierra dentro**.
- **ROTURA_FALLIDA** — la barra **cierra fuera** y la siguiente vuelve a cerrar
  dentro.
- **ROTURA_REAL** — cierra fuera y no vuelve: es la rotura que ya detecta el
  módulo, y la que mata al ID.

Consecuencia estructural que conviene tener presente al leer los recuentos: un
ID muere en el primer cierre más allá de uno de sus límites, así que durante su
vigencia **casi no puede haber ROTURA_FALLIDA**. Sólo aparece cuando un doji
cierra fuera con `D1_doji_no_rompe` —que por definición no rompe nada— y la
barra siguiente vuelve dentro. No es un fallo de la medición: es lo que la regla
del propietario implica, y por eso se cuenta y se reporta en vez de esconderse.

Aquí las mechas sí se miran. El módulo 1 traza la estructura por cuerpos, pero
un toque es por definición un asunto de mechas: preguntar si el precio "llegó a
tocar" el nivel sin cerrar más allá es preguntar por el `high` y el `low`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chronos.domain.structure.enums import ContactKind, ContactSide
from chronos.domain.structure.errors import StructureError


@dataclass(frozen=True, slots=True)
class Contact:
    """Un contacto de una barra con uno de los dos límites del ID."""

    kind: ContactKind
    side: ContactSide
    #: Posición de la barra en la serie de la temporalidad, no dentro del tramo.
    index: int
    level: float


@dataclass(frozen=True, slots=True)
class ContactSeries:
    """Contactos de un ID durante su vigencia, más lo que no encaja en las tres
    categorías del propietario.

    `unclassified` cuenta las barras que cierran más allá de un límite sin ser la
    rotura que mató al ID y sin que la siguiente vuelva dentro. Sólo pueden ser
    dojis con `D1_doji_no_rompe`. No se les inventa una categoría: se cuentan
    aparte y el informe las declara.
    """

    contacts: tuple[Contact, ...]
    unclassified: int = 0

    def of(self, side: ContactSide) -> tuple[Contact, ...]:
        return tuple(contact for contact in self.contacts if contact.side is side)


def classify_contacts(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    upper: float,
    lower: float,
    first: int,
    last: int,
    broke_at: int | None = None,
) -> ContactSeries:
    """Clasifica los contactos con `upper` y `lower` en las barras `[first, last]`.

    `first` es la barra **siguiente** a la constitución: antes de ese cierre el
    rango todavía no existe y hablar de contacto con él sería mirar atrás con
    información que la máquina aún no tenía.

    `broke_at` es la barra donde el ID murió, si murió: la única que puede llevar
    una `ROTURA_REAL`. Se pasa en vez de deducirla del precio porque quien decide
    qué rompe es el detector —con su modo de doji y su desigualdad estricta— y
    esta función no debe tener una segunda opinión sobre eso.
    """
    if upper < lower:
        raise StructureError(f"Límites cruzados: superior {upper} < inferior {lower}")
    if not len(high) == len(low) == len(close):
        raise StructureError("Las series de high, low y close no miden lo mismo")
    if first < 0 or last >= len(close):
        raise StructureError(f"Tramo [{first}, {last}] fuera de la serie ({len(close)} barras)")
    if last < first:
        return ContactSeries(contacts=())

    window = slice(first, last + 1)
    highs, lows, closes = high[window], low[window], close[window]

    # "Más allá" es estricto, igual que en la rotura: cerrar justo en el nivel es
    # cerrar dentro.
    above = closes > upper
    below = closes < lower
    # `True` si la barra siguiente vuelve a cerrar dentro. La última del tramo no
    # tiene siguiente que mirar sin salirse de la vigencia del ID.
    back_inside_above = np.append(~above[1:], False)
    back_inside_below = np.append(~below[1:], False)

    contacts: list[Contact] = []
    unclassified = 0
    for side, level, touched, outside, back_inside in (
        (ContactSide.SUPERIOR, upper, highs >= upper, above, back_inside_above),
        (ContactSide.INFERIOR, lower, lows <= lower, below, back_inside_below),
    ):
        for position in np.flatnonzero(touched):
            index = first + int(position)
            if not outside[position]:
                kind = ContactKind.TOQUE_MECHA
            elif index == broke_at:
                kind = ContactKind.ROTURA_REAL
            elif back_inside[position]:
                kind = ContactKind.ROTURA_FALLIDA
            else:
                unclassified += 1
                continue
            contacts.append(Contact(kind=kind, side=side, index=index, level=level))

    return ContactSeries(
        contacts=tuple(sorted(contacts, key=lambda item: (item.index, item.side.value))),
        unclassified=unclassified,
    )


def touches(series: ContactSeries, side: ContactSide) -> int:
    """Contactos que cuentan para la firma de lateralización, en un límite.

    Mecha y rotura fallida cuentan igual —lo dice el propietario— y la rotura
    real no cuenta: es la salida del rango, no un rebote dentro de él.
    """
    return sum(
        1
        for contact in series.contacts
        if contact.side is side and contact.kind is not ContactKind.ROTURA_REAL
    )
