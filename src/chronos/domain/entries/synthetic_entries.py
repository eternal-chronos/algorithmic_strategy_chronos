"""El día sintético de la fase 3 (§8): la cascada de entrada, calculada a mano.

Los **valores esperados no están aquí**: están en `tests/domain/entries/` y en el
listado de casos de `application/entries/evidence.py`, escritos antes de correr el
motor. Esa separación es el sentido de la prueba, igual que en las fases 1, 2.0
y 2.1.

Los diez casos del §8 no caben en una sola serie —salvar una rotura cambia toda
la historia posterior, y un objetivo alcanzado y un stop saltado son dos futuros
distintos del mismo presente— así que van repartidos en el nivel en que cada uno
se puede comprobar **exacto**:

    1. objetivo alcanzado ................. `M1_TARGET_UP`, sobre el ejecutor
    2. el stop salta primero .............. `M1_STOP_UP`, sobre el ejecutor
    3. stop y objetivo en la misma barra .. `M1_BOTH_UP`, sobre el ejecutor
    4. UL roto y retesteado ............... `H4_RETEST_UP`, cascada entera
    5. UL roto sin retesteo ............... `H4_NO_RETEST_UP`, cascada entera
    6. OB roto -> observación muerta ...... `H4_ORDER_BLOCK_BROKEN_UP`, cascada entera
    7. contexto diario a favor ............ regla `daily_context`, valores exactos
    8. Diario contra H4 -> manda H4 ....... regla `daily_context`, valores exactos
    9. OB suelto de M15 sin ID de M15 ..... `M15_LOOSE_UP`, sobre el buscador
   10. todos los anteriores en bajista .... los espejos, sin escribir a mano

**Por qué repartidos y no todos en una serie.** Un objetivo de 3,3 R exige un
recorrido concreto del precio *después* de entrar; forzarlo dentro de la misma
serie que produce los casos 4, 5 y 6 obligaría a estirar las velas hasta que
dejaran de parecerse a un mercado, y entonces la prueba no probaría la cascada
sino la serie. Cada caso se comprueba donde se puede escribir su esperado sin
ambigüedad. Lo que sí es de punta a punta —de M1 a la señal— son los casos 4, 5
y 6, que son los del enunciado que dependen de las cuatro temporalidades.

**Las series bajistas no se escriben a mano** (§8.10): son las alcistas
reflejadas en `p -> 2C - p`. Todas las desigualdades del módulo son estrictas y
la reflexión las invierte a la vez, así que cualquier asimetría es un fallo del
código y no de la serie.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chronos.domain.structure.synthetic_break import (
    MIRROR_CENTRE,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_ORDER_BLOCK_UP,
)
from chronos.domain.structure.synthetic_zones import Candle, mirror

#: Arranque de las series de H4. Medianoche en punto para que la rejilla de
#: agregación del sintético —H4 sin desplazamiento y día natural de UTC— caiga
#: sobre las mismas velas que se escriben a mano aquí.
SYNTHETIC_ENTRY_START = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)

#: Las siete primeras velas de la fase 2.1: un ID alcista que sobrevive a dos
#: velas y muere en la tercera atravesando su UL [2011.50, 2014]. Se reutiliza tal
#: cual para que los casos 4 y 5 arranquen de una historia **ya auditada**.
_BROKEN_LAST_ZONE: tuple[Candle, ...] = SYNTHETIC_BREAK_UP[:7]

#: Caso 4. El UL se rompe en b6 y el precio **vuelve a testearlo** en b7, que
#: entra en [2011.50, 2014] con su mecha inferior sin volver a cerrar dentro. Se
#: opera a favor de la rotura, que en un ID alcista es al alza.
#:
#: b8 vuelve a bajar a la zona y sale: es lo que da tiempo a que una vela de H1
#: cierre **dentro** de ella, que es lo que el §1.3 exige para confirmar. Con la
#: zona correcta —la que se rompió, [2011.50, 2014], y no la redibujada con la
#: vela de la rotura, que llegaría a 2016— sin esa segunda visita no hay señal.
H4_RETEST_UP: tuple[Candle, ...] = (
    *_BROKEN_LAST_ZONE,
    (2015.00, 2016.00, 2012.00, 2015.50),  # b7 verde · RETESTEO: 2012 cae dentro del UL roto
    (2015.50, 2015.60, 2011.60, 2015.55),  # b8 verde · baja otra vez a la zona y vuelve a salir
    (2015.55, 2016.00, 2014.00, 2014.20),  # b9 roja  · CONSTITUYE el ID siguiente y cierra la ventana
)

#: Caso 5. El mismo UL roto, y el precio **no vuelve**: b7 se queda por encima del
#: borde exterior y b8 constituye el ID siguiente. La observación muere sin
#: operación, con motivo `rotura_sin_retesteo`.
H4_NO_RETEST_UP: tuple[Candle, ...] = (
    *_BROKEN_LAST_ZONE,
    (2015.00, 2020.00, 2014.50, 2019.00),  # b7 verde · 2014.50 > 2014: no llega a tocar el UL
    (2019.00, 2019.50, 2017.00, 2017.50),  # b8 roja  · CONSTITUYE el ID siguiente; ventana cerrada
    (2017.50, 2018.00, 2016.00, 2016.50),  # b9 roja  · relleno, para que la serie no acabe en seco
)

#: Caso 6. El OB del ID#1 —[1980, 2002], la vela c0 entera— se toca en c4, se
#: perfora en c5 y se **atraviesa** en c6. En el OB la rotura invalida siempre: no
#: hay variante de retesteo, y la observación muere con motivo `ob_roto`.
H4_ORDER_BLOCK_BROKEN_UP: tuple[Candle, ...] = SYNTHETIC_ORDER_BLOCK_UP

#: Caso 9. Cuatro velas de M15 **sin ningún ID de M15**: no hace falta. La vela
#: roja `m1` es superada, mecha incluida, por la verde `m3`, así que el OB suelto
#: es `m1` entera: [1998.00, 2003.00].
M15_LOOSE_UP: tuple[Candle, ...] = (
    (2005.00, 2006.00, 2004.00, 2004.50),  # m0 roja · candidata, pero llega otra contraria después
    (2004.50, 2003.00 + 0.0, 1998.00, 1999.00),  # m1 roja · LA vela del OB suelto: [1998, 2003]
    (1999.00, 2000.00, 1998.50, 1999.50),  # m2 verde · no supera 2003: no confirma nada
    (1999.50, 2004.00, 1999.00, 2003.50),  # m3 verde · 2004 > 2003: CONFIRMA el OB suelto
)

#: Las mismas historias del revés (§8.10). No se escriben a mano a propósito.
H4_RETEST_DOWN: tuple[Candle, ...] = mirror(H4_RETEST_UP, MIRROR_CENTRE)
H4_NO_RETEST_DOWN: tuple[Candle, ...] = mirror(H4_NO_RETEST_UP, MIRROR_CENTRE)
H4_ORDER_BLOCK_BROKEN_DOWN: tuple[Candle, ...] = mirror(
    H4_ORDER_BLOCK_BROKEN_UP, MIRROR_CENTRE
)
M15_LOOSE_DOWN: tuple[Candle, ...] = mirror(M15_LOOSE_UP, MIRROR_CENTRE)


# --- Casos 1, 2 y 3: el desenlace, sobre M1 ---------------------------------
#
# La señal de estos tres casos se construye a mano en los tests y en la
# evidencia: zona de entrada [2000, 1990], así que el stop va en 1990, el 1R son
# 10 USD exactos y el objetivo, 3,3 R por encima de la entrada. Con la entrada en
# el open de la primera M1 —2000.00— el objetivo cae en 2033.00 clavado.

#: Precio de entrada de los tres casos: el open de la primera vela M1.
M1_ENTRY = 2000.00
#: Stop y objetivo que salen de la zona [2000, 1990] con ese precio de entrada.
M1_STOP = 1990.00
M1_TARGET = 2033.00

#: Caso 1. Sube sin tocar el stop y alcanza el objetivo en la cuarta vela.
M1_TARGET_UP: tuple[Candle, ...] = (
    (2000.00, 2004.00, 1999.00, 2003.00),  # entra aquí, al open
    (2003.00, 2012.00, 2002.00, 2011.00),
    (2011.00, 2026.00, 2010.00, 2025.00),
    (2025.00, 2034.00, 2024.00, 2033.50),  # 2034 >= 2033: OBJETIVO
    (2033.50, 2040.00, 2033.00, 2039.00),  # relleno: no se llega a mirar
)

#: Caso 2. Baja y toca el stop en la tercera vela, sin haber rozado el objetivo.
M1_STOP_UP: tuple[Candle, ...] = (
    (2000.00, 2004.00, 1999.00, 2003.00),  # entra aquí, al open
    (2003.00, 2005.00, 1995.00, 1996.00),
    (1996.00, 1997.00, 1989.00, 1990.50),  # 1989 <= 1990: STOP
    (1990.50, 1991.00, 1980.00, 1981.00),  # relleno: no se llega a mirar
)

#: Caso 3. La segunda vela toca los dos a la vez. **Gana el stop** (§4), y se
#: marca aparte para poder contar cuántas operaciones dependen de esa convención.
M1_BOTH_UP: tuple[Candle, ...] = (
    (2000.00, 2004.00, 1999.00, 2003.00),  # entra aquí, al open
    (2003.00, 2035.00, 1988.00, 2020.00),  # 2035 >= 2033 y 1988 <= 1990 EN LA MISMA VELA
    (2020.00, 2021.00, 2019.00, 2020.50),  # relleno: no se llega a mirar
)

M1_TARGET_DOWN: tuple[Candle, ...] = mirror(M1_TARGET_UP, MIRROR_CENTRE)
M1_STOP_DOWN: tuple[Candle, ...] = mirror(M1_STOP_UP, MIRROR_CENTRE)
M1_BOTH_DOWN: tuple[Candle, ...] = mirror(M1_BOTH_UP, MIRROR_CENTRE)

#: Los espejos de la zona de entrada: la reflexión cambia stop y objetivo de lado.
M1_ENTRY_MIRRORED = 2 * MIRROR_CENTRE - M1_ENTRY
M1_STOP_MIRRORED = 2 * MIRROR_CENTRE - M1_STOP
M1_TARGET_MIRRORED = 2 * MIRROR_CENTRE - M1_TARGET


# --- Explosión de velas -----------------------------------------------------


def explode(candle: Candle, parts: int) -> tuple[Candle, ...]:
    """Parte una vela en `parts` velas menores que agregan **exactamente** a ella.

    El camino es el conservador de siempre: en una vela verde el precio baja al
    `low`, sube al `high` y vuelve al cierre; en una roja, al revés. Es una
    elección declarada, no un dato: el OHLC de una vela no dice en qué orden se
    visitaron sus extremos, y cualquier reconstrucción tiene que elegir uno.

    Lo que sí queda garantizado es la consistencia: el primer `open` es el de la
    vela grande, el último `close` también, y el máximo y el mínimo de las
    pequeñas son los suyos. Así la serie M1 sintética vuelve a agregarse a las
    velas escritas a mano y la prueba recorre el motor de verdad —agregación,
    detector, zonas y cascada— en vez de un atajo.
    """
    if parts < 1:
        raise ValueError("Hay que partir la vela en al menos un trozo")
    if parts == 1:
        return (candle,)
    open_, high, low, close = candle
    path = [open_, low, high, close] if close >= open_ else [open_, high, low, close]
    legs = [abs(path[position + 1] - path[position]) for position in range(len(path) - 1)]
    total = sum(legs)
    if total == 0:
        return tuple([(open_, high, low, close)] + [(close, close, close, close)] * (parts - 1))

    step = total / parts
    built: list[Candle] = []
    for position in range(parts):
        first = _walk(path, legs, position * step)
        last = _walk(path, legs, min((position + 1) * step, total))
        reached = _extremes(path, legs, position * step, min((position + 1) * step, total))
        built.append((first, max(reached), min(reached), last))
    return tuple(built)


def _walk(path: list[float], legs: list[float], distance: float) -> float:
    """Precio del camino a `distance` de recorrido acumulado."""
    travelled = 0.0
    for position, leg in enumerate(legs):
        if distance <= travelled + leg or position == len(legs) - 1:
            if leg == 0:
                return path[position + 1]
            fraction = min(1.0, (distance - travelled) / leg)
            return path[position] + fraction * (path[position + 1] - path[position])
        travelled += leg
    return path[-1]


def _extremes(
    path: list[float], legs: list[float], start: float, end: float
) -> list[float]:
    """Precios visitados entre dos distancias: los dos bordes y los vértices."""
    reached = [_walk(path, legs, start), _walk(path, legs, end)]
    travelled = 0.0
    for position, leg in enumerate(legs):
        travelled += leg
        if start < travelled < end:
            reached.append(path[position + 1])
    return reached


def explode_all(candles: tuple[Candle, ...], parts: int) -> tuple[Candle, ...]:
    """La serie entera partida, vela a vela y en orden."""
    return tuple(part for candle in candles for part in explode(candle, parts))


__all__ = [
    "H4_NO_RETEST_DOWN",
    "H4_NO_RETEST_UP",
    "H4_ORDER_BLOCK_BROKEN_DOWN",
    "H4_ORDER_BLOCK_BROKEN_UP",
    "H4_RETEST_DOWN",
    "H4_RETEST_UP",
    "M1_BOTH_DOWN",
    "M1_BOTH_UP",
    "M1_ENTRY",
    "M1_ENTRY_MIRRORED",
    "M1_STOP",
    "M1_STOP_DOWN",
    "M1_STOP_MIRRORED",
    "M1_STOP_UP",
    "M1_TARGET",
    "M1_TARGET_DOWN",
    "M1_TARGET_MIRRORED",
    "M1_TARGET_UP",
    "M15_LOOSE_DOWN",
    "M15_LOOSE_UP",
    "SYNTHETIC_ENTRY_START",
    "explode",
    "explode_all",
]
