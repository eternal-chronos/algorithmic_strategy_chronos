"""Señales de zona: toque del OB, rechazo del UL y rotura del UL. **Sólo dibujo.**

Esto **mide**, no decide, exactamente igual que `contacts.py`: ninguna regla del
módulo 1 ni de la fase 2.1 lee nada de aquí, ninguna zona se mueve por su
existencia y no hay entradas, stops ni targets detrás. Son las tres cosas que el
propietario quiere poder ver marcadas sobre el gráfico del Diario y de H4 —las
dos únicas temporalidades con ID y, por tanto, con zonas— mientras audita el
punto 1 de la lista de la fase 3: *cuándo el precio toca una zona y en qué se
distingue tocar de atravesar*.

Las tres, sobre las zonas que la fase 2.0 ya calculó y con la misma lectura de
"más allá" que usa la rotura:

- **TOQUE_OB** — el rango de la barra entra en la zona OB. Tocar es un asunto de
  mechas: basta con que `[low, high]` corte la zona, cierre donde cierre.
- **RECHAZO_UL** — el rango de la barra entra en la zona UL y el cierre **no**
  queda más allá de su borde exterior: llegó y no pudo con ella.
- **ROTURA_UL** — el cierre queda más allá del borde exterior del UL. Es la
  misma definición de rotura de la fase 2.1, escrita aquí como geometría para
  que la señal exista también con `break_by_zone: false`, donde el ID muere
  antes por línea.

`RECHAZO_UL` y `ROTURA_UL` se excluyen: una barra que rompe no rechaza. Y no se
inventa una señal de "rotura del OB": atravesar el OB es la rotura en contra que
el detector ya marca, y duplicarla aquí sería contar dos veces lo mismo.

**Nada de esto se sabe antes de tiempo.** El tramo que se clasifica empieza en la
barra *siguiente* al nacimiento de la zona —antes de ese cierre la zona no
existe, la misma convención que `classify_contacts`— y termina en la barra que
mata al ID, incluida: esa es justo la que puede llevar la rotura.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import Zone, ZoneKind


class ZoneSignalKind(StrEnum):
    """Las tres señales. Los valores salen al payload del explorador."""

    #: El rango de la barra entra en la zona OB.
    TOQUE_OB = "TOQUE_OB"
    #: El rango entra en la zona UL y el cierre no pasa de su borde exterior.
    RECHAZO_UL = "RECHAZO_UL"
    #: El cierre queda más allá del borde exterior del UL.
    ROTURA_UL = "ROTURA_UL"


@dataclass(frozen=True, slots=True)
class ZoneSignal:
    """Una señal de una barra contra una zona concreta."""

    kind: ZoneSignalKind
    zone: ZoneKind
    #: Posición de la barra en la serie de la temporalidad.
    index: int
    #: Borde de la zona al que se refiere la señal: el **interior** en el toque y
    #: en el rechazo —es el que el precio encuentra primero— y el **exterior** en
    #: la rotura, que es el que hay que cruzar.
    level: float
    #: Hasta dónde llegó la mecha de la barra hacia la zona. Puede quedarse
    #: dentro o pasarse de largo; es el dato en bruto, sin recortar.
    reach: float
    #: Ese mismo precio recortado a los bordes de la zona: dónde tocó. Es el
    #: punto de contacto, y por eso es donde se planta el marcador.
    touch: float
    close: float
    #: El cierre se quedó dentro de la zona, bordes incluidos.
    inside: bool
    #: Ordinal de esta señal dentro de su tipo y su ID: 1 es la primera.
    ordinal: int


def classify_zone_signals(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    zone: Zone,
    first: int,
    last: int,
) -> tuple[ZoneSignal, ...]:
    """Señales de una zona en las barras `[first, last]`, en orden cronológico.

    `first` es la barra **siguiente** al nacimiento de la zona y `last` la que
    mata al ID —o la última de la serie si el ID sigue vivo—. El tramo lo decide
    quien llama, con los índices que la fase 2.0 ya registró: aquí no se busca
    ninguna vela ni se vuelve a leer cuándo nació nada.
    """
    if not len(high) == len(low) == len(close):
        raise StructureError("Las series de high, low y close no miden lo mismo")
    if first < 0 or last >= len(close):
        raise StructureError(f"Tramo [{first}, {last}] fuera de la serie ({len(close)} barras)")
    if last < first:
        return ()

    window = slice(first, last + 1)
    highs, lows, closes = high[window], low[window], close[window]

    # Tocar la zona es cortarla con el rango de la barra, bordes incluidos: es lo
    # que `Zone.contains` dice de un precio, dicho de un intervalo.
    reaches = (highs >= zone.low) & (lows <= zone.high)
    # Atravesarla es cerrar más allá del borde exterior, estricto como la rotura.
    beyond = (
        closes > zone.outer
        if zone.direction is ImpulseDirection.ALCISTA
        else closes < zone.outer
    )
    # El UL se recorre en la dirección del ID; el OB, en la contraria. Es la
    # mecha con la que la barra va a buscar la zona.
    towards = zone.direction if zone.kind is ZoneKind.LAST else zone.direction.opposite()
    reach_of = highs if towards is ImpulseDirection.ALCISTA else lows

    signals: list[ZoneSignal] = []
    counted: dict[ZoneSignalKind, int] = {}
    for position in range(len(closes)):
        if zone.kind is ZoneKind.ORDER_BLOCK:
            if not reaches[position]:
                continue
            kind, level = ZoneSignalKind.TOQUE_OB, zone.inner
        elif beyond[position]:
            kind, level = ZoneSignalKind.ROTURA_UL, zone.outer
        elif reaches[position]:
            kind, level = ZoneSignalKind.RECHAZO_UL, zone.inner
        else:
            continue

        counted[kind] = counted.get(kind, 0) + 1
        reach = float(reach_of[position])
        signals.append(
            ZoneSignal(
                kind=kind,
                zone=zone.kind,
                index=first + position,
                level=level,
                reach=reach,
                touch=min(max(reach, zone.low), zone.high),
                close=float(closes[position]),
                inside=zone.contains(float(closes[position])),
                ordinal=counted[kind],
            )
        )
    return tuple(signals)
