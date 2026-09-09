"""Lectura CRT del Diario (setup 2): o hay RANGO, o hay OBJETIVO, o no hay nada.

Esto **mide**, no decide. Ninguna regla del módulo 1 ni de la fase 2 lee nada de
aquí, no mueve un solo ID y no abre nada: es lo que el propietario quiere ver
marcado sobre el Diario mientras escribe el setup 2, y de momento nada más.

Se lee vela cerrada contra vela cerrada, siempre contra la **inmediatamente
anterior**, y sólo con dos preguntas:

1. **¿Estamos en rango?** Una vela *manipula* cuando se sale de la vela anterior
   por un lado —le barre el alto o el bajo— y vuelve a **cerrar dentro** de ella.
   Entonces el rango es la vela **manipulada**: su bajo y su alto. En el ejemplo
   alcista —le barrió el bajo y cerró dentro— se empieza a buscar compras hacia
   el alto del rango; en bajista pasa lo mismo en espejo.
2. **Si no estamos en rango**, ¿la vela *rompió*? Romper es cerrar **más allá**
   del alto o del bajo de la anterior, no sólo asomarse con la mecha. Entonces se
   marca el **alto de la vela que rompió** —el bajo en bajista— como el nivel que
   se espera ver manipulado y rechazado.
   Si no hizo ninguna de las dos cosas, no se marca nada.

**Un rango deja de existir**, y por eso lleva `index_end`:

- `COMPLETADO` — el precio llega al extremo **contrario al manipulado** (en el
  alcista, el alto del rango). Basta con alcanzarlo: es cosa de mechas.
- `ROTO` — sin haberlo alcanzado, una vela **cierra fuera** del rango por el lado
  manipulado. Ahí el rango desaparece.

Mientras un rango está vigente no se lee nada más: la primera pregunta es «¿estamos
en rango?» y la respuesta es que sí. La vela que lo cierra —lo complete o lo
rompa— se vuelve a leer en el acto contra su anterior, que es de donde sale la
lectura siguiente.

**Nada de esto se sabe antes de tiempo.** Una lectura de la vela `t` no existe
hasta que `t` cierra, y el rango que nace en `t` sólo lo pueden completar o
romper las velas **posteriores**: la propia vela que manipula cerró dentro y no
tiene nada que decir sobre lo que venga después.

Una vela que barre los **dos** lados y cierra dentro no produce rango: no dice
cuál de los dos extremos es el manipulado, y el sentido de la lectura entera sale
justo de ahí.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError


class CrtReadingKind(StrEnum):
    """Las dos lecturas. Los valores salen al payload del explorador."""

    #: Una vela barrió un extremo de la anterior y cerró dentro: el rango es la
    #: vela manipulada.
    RANGO = "RANGO"
    #: Una vela cerró más allá de un extremo de la anterior: se marca su propio
    #: extremo, el que se espera ver manipulado y rechazado.
    OBJETIVO = "OBJETIVO"


class CrtRangeEnd(StrEnum):
    """Cómo dejó de existir un rango."""

    #: El precio alcanzó el extremo contrario al manipulado.
    COMPLETADO = "COMPLETADO"
    #: Una vela cerró fuera del rango por el lado manipulado.
    ROTO = "ROTO"


@dataclass(frozen=True, slots=True)
class CrtReading:
    """Una lectura del Diario, dicha en posiciones de la serie.

    `direction` es hacia dónde EMPUJÓ la vela que la produce: `ALCISTA` cuando
    barrió el bajo de la anterior y volvió a cerrar dentro —de ahí que se empiece
    a buscar compras— o cuando cerró por encima de su alto. `BAJISTA`, en espejo.
    """

    kind: CrtReadingKind
    direction: ImpulseDirection
    #: Vela que produce la lectura: la que manipuló, o la que rompió. Antes de su
    #: cierre la lectura no existe.
    index: int
    #: Vela de la que salen los precios: la **manipulada** en el rango —la
    #: anterior— y la que rompió en el objetivo, que es ella misma.
    index_source: int
    low: float
    high: float
    #: Vela en la que la lectura dejó de estar vigente, o `None` mientras lo
    #: siga. Un rango se cierra al completarse o al romperse; un objetivo, en
    #: cuanto nace la lectura siguiente.
    index_end: int | None = None
    #: Sólo en el rango: cómo acabó.
    end: CrtRangeEnd | None = None

    @property
    def mid(self) -> float:
        """El 50 % del rango: dónde se parte en mitad alta y mitad baja."""
        return (self.low + self.high) / 2

    @property
    def manipulated(self) -> float:
        """El extremo barrido —o roto—: el bajo en alcista, el alto en bajista."""
        return self.low if self.direction is ImpulseDirection.ALCISTA else self.high

    @property
    def target(self) -> float:
        """El extremo contrario: al que va el rango, y el nivel del objetivo.

        En el objetivo los dos bordes son el mismo precio —es una línea, no un
        rango—, así que `target` y `manipulated` coinciden.
        """
        return self.high if self.direction is ImpulseDirection.ALCISTA else self.low

    @property
    def live(self) -> bool:
        return self.index_end is None


def read_crt(
    *, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> tuple[CrtReading, ...]:
    """Las lecturas de la serie, en orden cronológico.

    Cada vela se lee contra la **inmediatamente anterior**, así que la primera no
    produce nada: no tiene contra qué leerse.
    """
    if not len(high) == len(low) == len(close):
        raise StructureError("Las series de high, low y close no miden lo mismo")
    if len(close) < 2:
        return ()

    # Lo que cada vela hizo con la anterior, de una vez y para toda la serie. El
    # bucle de después es el estado —si hay rango vivo o no—, que es secuencial
    # por definición: lo que se puede contar sin mirar atrás se cuenta aquí.
    swept_low = low[1:] < low[:-1]
    swept_high = high[1:] > high[:-1]
    # Cerrar dentro incluye los bordes: "más allá" es estricto en todo el módulo.
    inside = (close[1:] >= low[:-1]) & (close[1:] <= high[:-1])
    broke_high = close[1:] > high[:-1]
    broke_low = close[1:] < low[:-1]

    readings: list[CrtReading] = []
    live: int | None = None  # posición en `readings` del rango vigente
    for position in range(len(close) - 1):
        index = position + 1
        if live is not None:
            end = _end_of(readings[live], high[index], low[index], close[index])
            if end is None:
                # Estamos en rango: la primera pregunta ya está contestada y esta
                # vela no se lee contra la anterior.
                continue
            readings[live] = replace(readings[live], index_end=index, end=end)
            live = None

        born = _reading_at(
            index=index,
            swept_low=bool(swept_low[position]),
            swept_high=bool(swept_high[position]),
            inside=bool(inside[position]),
            broke_high=bool(broke_high[position]),
            broke_low=bool(broke_low[position]),
            high=high,
            low=low,
        )
        if born is None:
            continue
        # El objetivo anterior estaba vigente a falta de algo que lo sustituyera:
        # lo sustituye esto.
        if readings and readings[-1].index_end is None:
            readings[-1] = replace(readings[-1], index_end=index)
        readings.append(born)
        if born.kind is CrtReadingKind.RANGO:
            live = len(readings) - 1
    return tuple(readings)


def _end_of(
    reading: CrtReading, high: float, low: float, close: float
) -> CrtRangeEnd | None:
    """Qué le hace esta vela al rango vigente: completarlo, romperlo o nada.

    Se mira primero el extremo contrario porque es el que se está esperando: una
    vela que lo alcanza y encima cierra fuera por ahí completó el rango, no lo
    rompió.
    """
    if reading.direction is ImpulseDirection.ALCISTA:
        if high >= reading.high:
            return CrtRangeEnd.COMPLETADO
        if close < reading.low:
            return CrtRangeEnd.ROTO
        return None
    if low <= reading.low:
        return CrtRangeEnd.COMPLETADO
    if close > reading.high:
        return CrtRangeEnd.ROTO
    return None


def _reading_at(
    *,
    index: int,
    swept_low: bool,
    swept_high: bool,
    inside: bool,
    broke_high: bool,
    broke_low: bool,
    high: np.ndarray,
    low: np.ndarray,
) -> CrtReading | None:
    """La lectura que produce la vela `index`, o `None` si no produce ninguna."""
    if inside:
        if swept_low == swept_high:
            # O no se salió por ningún lado —no manipuló nada—, o se salió por
            # los dos y entonces no hay forma de decir cuál es el manipulado.
            return None
        return CrtReading(
            kind=CrtReadingKind.RANGO,
            direction=(
                ImpulseDirection.ALCISTA if swept_low else ImpulseDirection.BAJISTA
            ),
            index=index,
            index_source=index - 1,
            low=float(low[index - 1]),
            high=float(high[index - 1]),
        )
    if not broke_high and not broke_low:
        return None
    # El objetivo es una LÍNEA: el extremo de la vela que rompió, con sus dos
    # bordes en el mismo precio.
    level = float(high[index]) if broke_high else float(low[index])
    return CrtReading(
        kind=CrtReadingKind.OBJETIVO,
        direction=(
            ImpulseDirection.ALCISTA if broke_high else ImpulseDirection.BAJISTA
        ),
        index=index,
        index_source=index,
        low=level,
        high=level,
    )
