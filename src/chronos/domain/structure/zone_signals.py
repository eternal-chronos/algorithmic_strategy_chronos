"""Señales de zona: toque del PUL, rechazo del UL y rotura del UL. **Sólo dibujo.**

Esto **mide**, no decide, exactamente igual que `contacts.py`: ninguna regla del
módulo 1 ni de la fase 2.1 lee nada de aquí, ninguna zona se mueve por su
existencia y no hay entradas, stops ni targets detrás. Son las tres cosas que el
propietario quiere poder ver marcadas sobre el gráfico del Diario y de H4 —las
dos únicas temporalidades con ID y, por tanto, con zonas— mientras audita el
punto 1 de la lista de la fase 3: *cuándo el precio toca una zona y en qué se
distingue tocar de atravesar*.

Las tres, sobre las zonas que la fase 2.0 ya calculó y con la misma lectura de
"más allá" que usa la rotura:

- **TOQUE_PUL** — el rango de la barra entra en la zona PUL viniendo **de fuera**.
  Tocar es un asunto de mechas: basta con que `[low, high]` corte la zona,
  cierre donde cierre. No hace falta esperar a ningún cierre para saberlo, así
  que el toque se **re-fecha** con `moment_of_touch` en la vela fina en la que
  el precio entró: una señal por visita, en el minuto en que ocurrió.
- **RECHAZO_UL** — el rango de la barra entra en la zona UL, el cierre **no**
  queda más allá de su borde exterior y el precio **venía de fuera**: llegó
  desde fuera y no pudo con ella.
- **ROTURA_UL** — el cierre queda más allá del borde exterior del UL. Es la
  misma definición de rotura de la fase 2.1, escrita aquí como geometría para
  que la señal exista también con `break_by_zone: false`, donde el ID muere
  antes por línea.

**Venir de fuera es parte de la señal, en las dos zonas.** La vela que fija el UL
cierra dentro de la zona —el borde interior es su propio cuerpo—, así que el
precio nace *dentro* del UL y todavía no lo ha abandonado: si la vela siguiente
lo toca, no lo está rechazando, sigue ahí metida. Y una vela que **abre dentro**
del PUL tampoco lo está tocando: ya estaba. Para que un toque o un rechazo
cuenten, el cierre anterior tiene que estar **fuera de la zona y por el lado por
el que el precio la busca** —el UL se busca hacia donde va el ID, el PUL hacia
el lado contrario, así que en un ID alcista se llega al UL desde abajo y al PUL
desde arriba—. Cerrar dentro de la zona desarma la señal hasta que el precio vuelva a
salir, y volver desde el otro lado del borde exterior no la arma: eso es un nivel
ya roto, no un rechazo.

`RECHAZO_UL` y `ROTURA_UL` se excluyen: una barra que rompe no rechaza. Y no se
inventa una señal de "rotura del PUL": atravesar el PUL es la rotura en contra que
el detector ya marca, y duplicarla aquí sería contar dos veces lo mismo.

**Nada de esto se sabe antes de tiempo.** El tramo que se clasifica empieza en la
barra *siguiente* al nacimiento de la zona —antes de ese cierre la zona no
existe, la misma convención que `classify_contacts`— y termina en la barra que
mata al ID, incluida: esa es justo la que puede llevar la rotura. La única barra
anterior que se mira es la que arranca el estado de "fuera": el cierre de
`first - 1`, que es el nacimiento de la zona y ya está en el pasado.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import Zone, ZoneKind


class ZoneSignalKind(StrEnum):
    """Las tres señales. Los valores salen al payload del explorador."""

    #: El rango de la barra entra en la zona PUL.
    TOQUE_PUL = "TOQUE_PUL"
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

    El estado de "fuera" arranca en el cierre de `first - 1` —la barra del
    nacimiento de la zona— para saber si el precio ya estaba fuera. Con
    `first == 0` no hay barra anterior y se arranca *dentro*: sin pasado no se
    puede venir de fuera.
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
    # El UL se recorre en la dirección del ID; el PUL, en la contraria. Es la
    # mecha con la que la barra va a buscar la zona, y también de dónde viene el
    # precio: el UL se busca desde abajo en un ID alcista y el PUL desde arriba.
    towards = zone.direction if zone.kind is ZoneKind.LAST else zone.direction.opposite()
    reach_of = highs if towards is ImpulseDirection.ALCISTA else lows
    # Cerrar fuera de la zona **por el lado desde el que el precio la busca** es lo
    # que arma la señal, y quien fija ese estado al abrir una barra es la barra
    # anterior: por eso el tramo se lee corrido una posición, empezando en la
    # vela del nacimiento de la zona.
    trail = close[max(first - 1, 0) : last + 1]
    outside = trail < zone.low if towards is ImpulseDirection.ALCISTA else trail > zone.high
    # `arrived[i]`: el precio ya estaba fuera cuando abrió la barra `first + i`.
    # Sin barra anterior se arranca dentro: sin pasado no se viene de fuera.
    arrived = outside[:-1] if first > 0 else np.concatenate(([False], outside[:-1]))

    signals: list[ZoneSignal] = []
    counted: dict[ZoneSignalKind, int] = {}
    for position in range(len(closes)):
        if zone.kind is ZoneKind.PENULTIMATE:
            if not (reaches[position] and arrived[position]):
                continue
            kind, level = ZoneSignalKind.TOQUE_PUL, zone.inner
        elif beyond[position]:
            kind, level = ZoneSignalKind.ROTURA_UL, zone.outer
        elif reaches[position] and arrived[position]:
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


def moment_of_touch(
    *,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    zone: Zone,
    first: int,
    last: int,
    signal: ZoneSignal,
) -> ZoneSignal | None:
    """`signal` re-fechado en la **primera** barra de `[first, last]` que entra en la zona.

    Es el toque contado con más resolución: la vela grande dice *que* el precio
    entró en la zona y *si venía de fuera*; estas barras —las de una serie más
    corta que la suya, dentro de esa misma vela— dicen *cuándo*. Devuelve la
    señal con el índice y **los números de esa barra** —hasta dónde llegó, dónde
    tocó, cómo cerró—, que son los que se conocen en ese instante: los de la vela
    grande no se sabrán hasta que cierre, horas después.

    `None` si ninguna barra del tramo entra en la zona, que sólo puede pasar si
    las dos series no cubren los mismos minutos. Quien llama se queda entonces
    con la señal de la vela grande, que no deja de ser cierta.
    """
    if last < first or first < 0 or last >= len(close):
        return None

    window = slice(first, last + 1)
    highs, lows, closes = high[window], low[window], close[window]
    reaches = (highs >= zone.low) & (lows <= zone.high)
    if not reaches.any():
        return None

    towards = zone.direction if zone.kind is ZoneKind.LAST else zone.direction.opposite()
    position = int(np.argmax(reaches))
    reach = float((highs if towards is ImpulseDirection.ALCISTA else lows)[position])
    closing = float(closes[position])
    return replace(
        signal,
        index=first + position,
        reach=reach,
        touch=min(max(reach, zone.low), zone.high),
        close=closing,
        inside=zone.contains(closing),
    )
