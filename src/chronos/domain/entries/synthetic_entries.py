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

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.synthetic_break import (
    MIRROR_CENTRE,
    SYNTHETIC_BREAK_UP,
    SYNTHETIC_ORDER_BLOCK_UP,
)
from chronos.domain.structure.synthetic_zones import Candle, mirror
from chronos.domain.structure.zones import Zone, ZoneKind

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

# --- Fase 3.1, vía 1: el turtle soup de H1 -----------------------------------
#
# Cuatro series de dos o tres velas de H1. Todas arrancan con la misma primera
# vela —mecha inferior hasta 1995— para que lo único que cambie entre los casos
# sea la SEGUNDA, que es la que decide. El extremo a rechazar es siempre 1995.

#: Caso 1. Turtle soup limpio: la segunda baja a 1994,50 —alcanza el extremo de
#: la mecha de la primera— y cierra en 1999, por encima de él. **Confirma.**
H1_TURTLE_CLEAN_UP: tuple[Candle, ...] = (
    (2000.00, 2001.00, 1995.00, 2000.50),  # t0 verde · mecha inferior hasta 1995
    (2000.50, 2001.00, 1994.50, 1999.00),  # t1 rojo  · llega a 1995 y cierra encima
)

#: Caso 2. La segunda **supera** el extremo con el cierre: 1994 queda por debajo
#: de 1995. Eso no es un rechazo, es una rotura. **No confirma.**
H1_TURTLE_BREAKS_UP: tuple[Candle, ...] = (
    (2000.00, 2001.00, 1995.00, 2000.50),
    (2000.50, 2001.00, 1993.00, 1994.00),  # t1 · cierra POR DEBAJO de 1995
)

#: Caso 3. La segunda **no llega**: su mínimo se queda en 1996. **No confirma.**
H1_TURTLE_SHORT_UP: tuple[Candle, ...] = (
    (2000.00, 2001.00, 1995.00, 2000.50),
    (2000.50, 2002.00, 1996.00, 2001.00),  # t1 · 1996 > 1995: no lo alcanza
)

#: Caso 4. Las dos velas **no son consecutivas**: entre la que deja la mecha y la
#: que baja a buscarla hay una tercera, y esa tercera no tiene mecha inferior. El
#: patrón se evalúa sobre t1 y t2, no sobre t0 y t2. **No confirma.**
H1_TURTLE_GAPPED_UP: tuple[Candle, ...] = (
    (2000.00, 2001.00, 1995.00, 2000.50),  # t0 · deja la mecha hasta 1995
    (2000.50, 2003.00, 2000.50, 2002.50),  # t1 · en medio, y SIN mecha inferior
    (2002.50, 2003.00, 1994.00, 2001.00),  # t2 · baja a 1994, pero la mecha es de t0
)

#: El mismo caso 1 con la primera vela **sin mecha superior**: cierra en su
#: máximo. Existe para comprobar que el patrón mira la mecha del lado que toca y
#: no la que tenga más cerca: en alcista confirma igual que el caso 1, y en
#: bajista no hay ni extremo que rechazar.
#:
#: Hace falta como serie aparte porque una misma pareja de velas PUEDE ser turtle
#: soup en las dos direcciones a la vez —si la primera deja mecha por arriba y
#: por abajo y la segunda va a buscar las dos—, y eso no es un fallo: es lo que
#: la definición dice. Sin una serie de un solo lado, la comprobación de que cada
#: dirección mira su mecha no se podría escribir.
H1_TURTLE_ONE_SIDED_UP: tuple[Candle, ...] = (
    (2000.00, 2000.50, 1995.00, 2000.50),  # t0 · cierra en su máximo: sin mecha arriba
    (2000.50, 2001.00, 1994.50, 1999.00),  # t1 · llega a 1995 y cierra encima
)
H1_TURTLE_ONE_SIDED_DOWN: tuple[Candle, ...] = mirror(
    H1_TURTLE_ONE_SIDED_UP, MIRROR_CENTRE
)

#: Caso 10, la versión bajista. No se escribe a mano: es el espejo.
H1_TURTLE_CLEAN_DOWN: tuple[Candle, ...] = mirror(H1_TURTLE_CLEAN_UP, MIRROR_CENTRE)
H1_TURTLE_BREAKS_DOWN: tuple[Candle, ...] = mirror(H1_TURTLE_BREAKS_UP, MIRROR_CENTRE)
H1_TURTLE_SHORT_DOWN: tuple[Candle, ...] = mirror(H1_TURTLE_SHORT_UP, MIRROR_CENTRE)
H1_TURTLE_GAPPED_DOWN: tuple[Candle, ...] = mirror(H1_TURTLE_GAPPED_UP, MIRROR_CENTRE)

#: El extremo que la segunda vela tiene que alcanzar y no superar, en las series
#: alcistas y en sus espejos. Va aquí para que el esperado del test no lo
#: recalcule: si el espejo se moviera, el test tiene que fallar.
TURTLE_EXTREME_UP = 1995.00
TURTLE_EXTREME_DOWN = 2 * MIRROR_CENTRE - TURTLE_EXTREME_UP


# --- Fase 3.2: el rechazo en H4 ---------------------------------------------
#
# Once casos, y van donde su esperado se puede escribir **exacto**, igual que los
# diez de la 3.0:
#
#    1. contacto sin rechazo ni rotura ...... `H4_NO_REJECTION_UP`
#    2. rechazo forma A ..................... `H4_REJECT_A_UP`
#    3. rechazo forma B (turtle soup de H4) . `H4_REJECT_B_UP`
#    4. las dos formas a la vez ............. `H4_REJECT_BOTH_UP` y `H4_REJECT_ORDER_UP`
#    5. UL roto y retesteado ................ `H4_RETEST_UP`, ya escrito arriba
#    6. UL roto sin retesteo ................ `H4_NO_RETEST_UP`, ya escrito arriba
#    7. OB rechazado ........................ `H4_REJECT_OB_A_UP`
#    8. OB roto ............................. `H4_ORDER_BLOCK_BROKEN_UP`, ya escrito
#    9. rechazo sin confirmación en H1 ...... la cascada entera, sobre estas velas
#   10. confirmación de H1 en la dirección contraria a la del rechazo
#   11. todos los anteriores en bajista ..... los espejos, sin escribir a mano
#
# Los casos 1, 2, 3, 4 y 7 se comprueban sobre el **detector de rechazos** con la
# zona escrita a mano: ahí el esperado es exacto y no depende de que el detector
# de impulsos coloque el ID donde uno cree. Los demás siguen recorriendo la
# cascada entera, que es donde viven.

#: La zona UL alcista de referencia: [2010, 2012]. **No es una zona nueva**: es
#: la del ID#1 de la fase 2.1 (`SYNTHETIC_BREAK_UP`, el UL que deja b3), escrita
#: aquí como número para que el esperado del test no la recalcule.
H4_UL_INNER_UP = 2010.00
H4_UL_OUTER_UP = 2012.00

#: La zona OB alcista de referencia: [1994, 2002], la vela b0 entera. En un ID
#: alcista el OB se recorre hacia abajo, así que su borde INTERIOR es el de
#: arriba (2002) y el exterior el de abajo (1994).
H4_OB_INNER_UP = 2002.00
H4_OB_OUTER_UP = 1994.00

#: Caso 1. El precio toca [2010, 2012] y **la sostiene**: cierra dentro las dos
#: veces. Ni forma A —el cierre no sale de la zona— ni forma B —la primera vela
#: no deja mecha que rechazar, y la segunda no cierra por debajo de la de n1—.
#: Sin desenlace no hay operación, que es justo lo que la 3.1 sí operaba.
H4_NO_REJECTION_UP: tuple[Candle, ...] = (
    (2005.00, 2005.50, 2004.00, 2005.50),  # n0 verde · cierra en su máximo: SIN mecha superior
    (2005.50, 2010.50, 2005.00, 2010.30),  # n1 verde · TOCA la zona y cierra DENTRO: la sostiene
    (2010.30, 2011.00, 2010.10, 2010.90),  # n2 verde · sigue dentro; 2010.90 no baja de 2010.50
)

#: Caso 2. Forma A pura. `a0` cierra en su máximo, así que no deja mecha superior
#: y la forma B **no puede** existir sobre `a1`: lo que se comprueba es la forma
#: A sola, sin que la otra la tape.
H4_REJECT_A_UP: tuple[Candle, ...] = (
    (2005.00, 2005.50, 2004.00, 2005.50),  # a0 verde · sin mecha superior
    (2005.50, 2011.00, 2005.00, 2008.00),  # a1 roja · entra a 2011 y CIERRA EN 2008, fuera de la
    #                                            zona y por el lado por el que entró: FORMA A
)

#: Caso 3. Forma B pura. La vela que rechaza cierra **dentro** de la zona, así
#: que la forma A no se cumple, y aun así es un turtle soup bajista en la zona.
H4_REJECT_B_UP: tuple[Candle, ...] = (
    (2005.00, 2011.50, 2004.00, 2010.50),  # b0 verde · deja mecha superior hasta 2011.50, en la zona
    (2010.50, 2011.50, 2010.00, 2010.20),  # b1 roja · llega a 2011.50 y cierra en 2010.20, por
    #                                            debajo: TURTLE SOUP. Y 2010.20 está DENTRO de
    #                                            [2010, 2012], así que la forma A no marca
)

#: Caso 4a. Las dos formas en la **misma** vela: llega a la mecha de la anterior
#: y además cierra fuera de la zona. La decisión es idéntica por las dos, así que
#: cuál se apunte como disparo es cosmético; lo que no puede perderse es que las
#: dos estaban disponibles.
H4_REJECT_BOTH_UP: tuple[Candle, ...] = (
    (2005.00, 2011.50, 2004.00, 2010.50),  # c0 verde · mecha superior hasta 2011.50
    (2010.50, 2011.50, 2007.00, 2008.00),  # c1 roja · llega a 2011.50 (forma B) y cierra en 2008,
    #                                            fuera de la zona (forma A)
)

#: Caso 4b. Las dos formas en velas **distintas**: gana la primera
#: cronológicamente, que aquí es la A de `d1`. La B de `d3` llega dos velas
#: después y no decide nada.
H4_REJECT_ORDER_UP: tuple[Candle, ...] = (
    (2005.00, 2005.50, 2004.00, 2005.50),  # d0 verde · sin mecha superior
    (2005.50, 2011.00, 2005.00, 2008.00),  # d1 roja · FORMA A, y la B no puede existir sobre ella
    (2008.00, 2011.50, 2007.00, 2010.50),  # d2 verde · deja mecha hasta 2011.50
    (2010.50, 2011.50, 2010.00, 2010.20),  # d3 roja · FORMA B, dos velas tarde
)

#: Caso 7. El OB rechazado: la vela entra en [1994, 2002] y cierra **por encima**
#: de 2002, que en el OB es el lado de dentro. La operación va A FAVOR del ID.
#: `o0` no deja mecha inferior, así que la forma B no puede taparlo.
H4_REJECT_OB_A_UP: tuple[Candle, ...] = (
    (2004.00, 2005.00, 2004.00, 2004.50),  # o0 verde · low = open: SIN mecha inferior
    (2004.50, 2005.00, 2001.00, 2003.00),  # o1 roja · entra a 2001 y CIERRA EN 2003: FORMA A ->
    #                                            COMPRA, a favor del ID alcista
)

#: Los espejos del §8.11. No se escriben a mano: la reflexión invierte a la vez
#: todas las desigualdades del módulo, así que una asimetría es un fallo del
#: código y no de la serie.
H4_NO_REJECTION_DOWN: tuple[Candle, ...] = mirror(H4_NO_REJECTION_UP, MIRROR_CENTRE)
H4_REJECT_A_DOWN: tuple[Candle, ...] = mirror(H4_REJECT_A_UP, MIRROR_CENTRE)
H4_REJECT_B_DOWN: tuple[Candle, ...] = mirror(H4_REJECT_B_UP, MIRROR_CENTRE)
H4_REJECT_BOTH_DOWN: tuple[Candle, ...] = mirror(H4_REJECT_BOTH_UP, MIRROR_CENTRE)
H4_REJECT_ORDER_DOWN: tuple[Candle, ...] = mirror(H4_REJECT_ORDER_UP, MIRROR_CENTRE)
H4_REJECT_OB_A_DOWN: tuple[Candle, ...] = mirror(H4_REJECT_OB_A_UP, MIRROR_CENTRE)

#: Los bordes de las dos zonas, reflejados. La reflexión cambia de sitio el borde
#: interior y el exterior, así que se escriben derivados y no a ojo.
H4_UL_INNER_DOWN = 2 * MIRROR_CENTRE - H4_UL_INNER_UP
H4_UL_OUTER_DOWN = 2 * MIRROR_CENTRE - H4_UL_OUTER_UP
H4_OB_INNER_DOWN = 2 * MIRROR_CENTRE - H4_OB_INNER_UP
H4_OB_OUTER_DOWN = 2 * MIRROR_CENTRE - H4_OB_OUTER_UP


def reference_zone(
    kind: ZoneKind, direction: ImpulseDirection, *, id_num: int = 1, timeframe: str = "H4"
) -> Zone:
    """La zona de referencia de la fase 3.2, construida una sola vez.

    Existe para que el test, la evidencia y las capturas no la escriban cada uno
    con sus propios números: si los bordes se movieran, se moverían en un sitio.
    Es una zona **de laboratorio** —no sale de ningún detector— y por eso nace en
    el arranque del sintético: aquí lo que se prueba es el rechazo, no cuándo
    empieza a existir una zona, que ya lo fija la fase 2.0.
    """
    up = direction is ImpulseDirection.ALCISTA
    if kind is ZoneKind.LAST:
        inner = H4_UL_INNER_UP if up else H4_UL_INNER_DOWN
        outer = H4_UL_OUTER_UP if up else H4_UL_OUTER_DOWN
    else:
        inner = H4_OB_INNER_UP if up else H4_OB_INNER_DOWN
        outer = H4_OB_OUTER_UP if up else H4_OB_OUTER_DOWN
    return Zone(
        kind=kind,
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        index_defining=0,
        ts_defining=SYNTHETIC_ENTRY_START,
        defining_body=BodyDirection.BULLISH if up else BodyDirection.BEARISH,
        inner=inner,
        outer=outer,
        ts_outer_known=SYNTHETIC_ENTRY_START,
        ts_birth=SYNTHETIC_ENTRY_START,
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
    "H1_TURTLE_BREAKS_DOWN",
    "H1_TURTLE_BREAKS_UP",
    "H1_TURTLE_CLEAN_DOWN",
    "H1_TURTLE_CLEAN_UP",
    "H1_TURTLE_GAPPED_DOWN",
    "H1_TURTLE_GAPPED_UP",
    "H1_TURTLE_ONE_SIDED_DOWN",
    "H1_TURTLE_ONE_SIDED_UP",
    "H1_TURTLE_SHORT_DOWN",
    "H1_TURTLE_SHORT_UP",
    "H4_NO_REJECTION_DOWN",
    "H4_NO_REJECTION_UP",
    "H4_NO_RETEST_DOWN",
    "H4_NO_RETEST_UP",
    "H4_OB_INNER_DOWN",
    "H4_OB_INNER_UP",
    "H4_OB_OUTER_DOWN",
    "H4_OB_OUTER_UP",
    "H4_ORDER_BLOCK_BROKEN_DOWN",
    "H4_ORDER_BLOCK_BROKEN_UP",
    "H4_REJECT_A_DOWN",
    "H4_REJECT_A_UP",
    "H4_REJECT_BOTH_DOWN",
    "H4_REJECT_BOTH_UP",
    "H4_REJECT_B_DOWN",
    "H4_REJECT_B_UP",
    "H4_REJECT_OB_A_DOWN",
    "H4_REJECT_OB_A_UP",
    "H4_REJECT_ORDER_DOWN",
    "H4_REJECT_ORDER_UP",
    "H4_RETEST_DOWN",
    "H4_RETEST_UP",
    "H4_UL_INNER_DOWN",
    "H4_UL_INNER_UP",
    "H4_UL_OUTER_DOWN",
    "H4_UL_OUTER_UP",
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
    "TURTLE_EXTREME_DOWN",
    "TURTLE_EXTREME_UP",
    "explode",
    "explode_all",
    "reference_zone",
]
