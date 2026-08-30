"""El día sintético de la fase 2.1 (§5): la rotura por zona, calculada a mano.

Los valores esperados **no están aquí**: están escritos a mano en
`tests/domain/structure/test_synthetic_break.py` y en el listado de casos de
`application/structure/break_evidence.py`, antes de correr el motor. Esa
separación es el sentido de la prueba.

Los diez casos del §5 no caben en una sola serie sin que unos tapen a otros —
salvar una rotura cambia toda la historia posterior—, así que van en tres series
cortas, cada una con lo suyo, más sus espejos:

    1. mecha que perfora el UL y cierra dentro .... `SYNTHETIC_BREAK_UP`, b5
    2. cierre dentro del UL ....................... `SYNTHETIC_BREAK_UP`, b4
    3. cierre más allá del borde exterior del UL .. `SYNTHETIC_BREAK_UP`, b6
    4. los tres anteriores con el OB .............. `SYNTHETIC_ORDER_BLOCK_UP`, c4/c5/c6
    5. sin OB confirmado -> muere por línea ....... `SYNTHETIC_WITHOUT_ORDER_BLOCK_UP`, c8
    6. con el OB confirmado -> ya no muere ........ `SYNTHETIC_ORDER_BLOCK_UP`, c8
    7. sobrevive y extiende el extremo ............ `SYNTHETIC_BREAK_UP`, b4 y b5
    8. zonas y una vela que cumple las dos ........ `SYNTHETIC_OVERLAP_UP`, d4
    9. UL de altura cero .......................... `SYNTHETIC_BREAK_UP`, ID#2
   10. todos los anteriores en bajista ............ los espejos, sin escribir a mano

Los espejos se derivan con el `mirror` de la fase 2.0 y no se escriben a mano a
propósito: la reflexión conserva todas las desigualdades estrictas del módulo,
así que cualquier asimetría que aparezca al correrlos es un fallo del código.

**Casos 5 y 6 sobre las mismas velas.** Las dos series del OB se diferencian en
**un solo número**: la mecha inferior de `c2`, la vela que acaba siendo el ancla
del ID#2. Con `2004.00` alguien la supera y el OB se confirma; con `1900.00` no
la supera nadie y el ID se queda sin OB. Todo lo demás —cuerpos, impulsos,
numeración— es idéntico, porque el módulo 1 sólo mira cuerpos y esa mecha no
interviene en ninguna otra zona. Así el §5 se puede leer literalmente: *el mismo
ID*, antes y después de tener OB, contra *la misma vela*.

**Caso 8, y lo que enseña.** Que dos zonas se solapen en precio **no** basta para
que una vela cumpla las dos condiciones de rotura: hace falta que los dos bordes
exteriores estén *invertidos*, y eso es lo contrario de solaparse. En un ID
alcista el borde exterior del UL está en la mecha de la vela del extremo y el del
OB en el suelo de la vela del ancla, así que

    borde exterior del UL >= extremo > ancla >= borde exterior del OB

mientras el rango del ID sea positivo, y ninguna vela puede cerrar por encima del
primero y por debajo del segundo a la vez. El orden sólo decide algo cuando el
rango es **no positivo**, que es lo que produce un hueco que se salta el ancla.
`SYNTHETIC_OVERLAP_UP` construye exactamente ese caso; el informe cuenta cuántos
hay en el histórico de verdad.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chronos.domain.structure.synthetic_zones import Candle, mirror

#: Precio alrededor del cual se reflejan las tres series.
MIRROR_CENTRE = 2000.0

#: Casos 1, 2, 3, 7 y 9. Dos impulsos alcistas encadenados: el primero sobrevive
#: a dos velas que la fase 1 habría llamado rotura y muere en la tercera; el
#: segundo tiene un UL de altura cero y muere exactamente como en la fase 1.
SYNTHETIC_BREAK_UP: tuple[Candle, ...] = (
    # --- ID#1: dos roturas evitadas y el extremo que las sigue ---------------
    (2000.00, 2002.00, 1994.00, 1995.00),  # b0 roja · semilla bajista; OB del ID#1 [1994, 2002]
    (1995.00, 2006.00, 1994.00, 2005.00),  # b1 verde · abre la pierna; 2006 > 2002 confirma el OB#1
    (2005.00, 2012.00, 2004.00, 2010.00),  # b2 verde · extremo del ID#1 (cuerpo 2010, mecha 2012)
    (2010.00, 2011.00, 2008.00, 2009.00),  # b3 roja · CONSTITUYE ID#1; 2011 < 2012, no extiende
    #                                            UL#1 = [2010, 2012] · ancla 1995 · extremo 2010
    (2009.00, 2011.90, 2008.00, 2011.00),  # b4 verde · CASO 2: cierra en 2011, dentro del UL, y la
    #                                            mecha 2011.90 ni llega al borde. SOBREVIVE y el
    #                                            extremo se estira a 2011 -> UL = [2011, 2011.90]
    (2011.00, 2014.00, 2010.00, 2011.50),  # b5 verde · CASO 1: la mecha 2014 PERFORA ese borde y el
    #                                            cierre 2011.50 se queda dentro. SOBREVIVE y el
    #                                            extremo se estira a 2011.50 -> UL = [2011.50, 2014]
    (2011.50, 2016.00, 2011.00, 2015.00),  # b6 verde · CASO 3: cierra 2015 > 2014. ROTURA_A_FAVOR
    #                                            por zona. La mecha 2016 no cuenta: la vela no puede
    #                                            estirar el borde contra el que se la juzga
    # --- ID#2: UL de altura cero, que se comporta como la línea --------------
    (2015.00, 2018.00, 2014.00, 2018.00),  # b7 verde · cierra en su propio máximo: sin mecha arriba
    (2018.00, 2018.00, 2016.00, 2017.00),  # b8 roja · CONSTITUYE ID#2; empate en 2018, no extiende
    #                                            UL#2 = [2018, 2018], ALTURA CERO
    (2017.00, 2019.00, 2016.00, 2018.50),  # b9 verde · CASO 9: cierra 2018.50 sobre un borde que es
    #                                            la propia línea. ROTURA_A_FAVOR, igual que la fase 1
)

#: Casos 4 y 6. Un ID alcista con un OB muy ancho que aguanta dos velas y cede a
#: la tercera; después un ID bajista que sobrevive a un cierre más allá de su
#: línea del ancla porque su OB **sí** está confirmado.
SYNTHETIC_ORDER_BLOCK_UP: tuple[Candle, ...] = (
    (2000.00, 2002.00, 1980.00, 1995.00),  # c0 roja · semilla; OB del ID#1, ancho: [1980, 2002]
    (1995.00, 2006.00, 1994.00, 2005.00),  # c1 verde · abre la pierna; 2006 > 2002 confirma el OB#1
    (2005.00, 2012.00, 2004.00, 2010.00),  # c2 verde · extremo del ID#1. Su MECHA INFERIOR es lo
    #                                            único que separa esta serie de la de abajo: con
    #                                            2004.00 el OB del ID#2 llega a confirmarse
    (2010.00, 2011.00, 2008.00, 2009.00),  # c3 roja · CONSTITUYE ID#1; ancla 1995, extremo 2010
    (2009.00, 2010.00, 1989.00, 1990.00),  # c4 roja · CASO 4b: cierra 1990, bajo la línea 1995 pero
    #                                            dentro del OB, y la mecha 1989 no llega a 1980.
    #                                            SOBREVIVE. El ancla no se mueve: nunca lo hace
    (1990.00, 1992.00, 1975.00, 1985.00),  # c5 roja · CASO 4a: la mecha 1975 PERFORA el borde 1980
    #                                            y el cierre 1985 se queda dentro. SOBREVIVE
    (1985.00, 1986.00, 1975.00, 1978.00),  # c6 roja · CASO 4c: cierra 1978 < 1980. ROTURA_EN_CONTRA
    #                                            por zona
    (1978.00, 1982.00, 1977.00, 1981.00),  # c7 verde · CONSTITUYE ID#2 bajista; ancla 2010 (la vela
    #                                            c2), extremo 1978
    (1981.00, 2015.00, 1980.00, 2011.00),  # c8 verde · CASO 6: cierra 2011, más allá de la línea del
    #                                            ancla (2010) pero dentro del OB [2004, 2012].
    #                                            SOBREVIVE: con OB confirmado ya no muere por línea
)

#: Posición y valor de la mecha que distingue las dos series del OB.
_OB_WICK_POSITION = 2
_OB_WICK_UNREACHABLE = 1900.00


def _without_order_block(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
    """La misma serie con la mecha del ancla del ID#2 fuera de alcance.

    Un solo número cambia. El módulo 1 sólo mira cuerpos, así que los impulsos, su
    numeración y sus dos líneas salen idénticos; lo único que desaparece es el OB
    del ID#2, porque ninguna vela llega a superar esa mecha.
    """
    open_, high, _low, close = candles[_OB_WICK_POSITION]
    return (
        *candles[:_OB_WICK_POSITION],
        (open_, high, _OB_WICK_UNREACHABLE, close),
        *candles[_OB_WICK_POSITION + 1 :],
    )


#: Caso 5. Las mismas velas y el mismo ID#2, esta vez sin OB que lo proteja: el
#: cierre de `c8` lo mata por línea.
SYNTHETIC_WITHOUT_ORDER_BLOCK_UP: tuple[Candle, ...] = _without_order_block(
    SYNTHETIC_ORDER_BLOCK_UP
)

#: Caso 8. Un hueco a la baja se salta el ancla y deja un ID de **rango
#: negativo**: su extremo (2040) queda por debajo de su ancla (2090). Ahí, y sólo
#: ahí, los dos bordes exteriores se invierten —el del UL en 2050, el del OB en
#: 2080— y una vela puede cerrar más allá de los dos a la vez.
#:
#: La vela que constituye vuelve con otro hueco a dentro del OB (cierra en 2085,
#: sobre el borde 2080). No es adorno: si cerrase por debajo, el ID nacería ya
#: roto y la regla no lo dejaría nacer, así que el conflicto no existiría. Y por
#: eso entre el extremo y la constitución hay una vela de margen (d3): con la
#: constituyente pegada al extremo, su propia mecha estiraría el UL por encima
#: del borde del OB y volvería a cerrar la ventana del conflicto.
SYNTHETIC_OVERLAP_UP: tuple[Candle, ...] = (
    (2100.00, 2105.00, 2080.00, 2090.00),  # d0 roja · semilla bajista; ancla del ID#1 = 2090 y su
    #                                            vela entera es el OB: [2080, 2105]
    (2000.00, 2106.00, 1995.00, 2010.00),  # d1 verde · HUECO A LA BAJA; abre la pierna alcista.
    #                                            Su mecha 2106 > 2105 CONFIRMA el OB#1 sin que su
    #                                            cuerpo llegue a ninguna parte
    (2010.00, 2050.00, 2008.00, 2040.00),  # d2 verde · extremo del ID#1 (cuerpo 2040, mecha 2050)
    (2020.00, 2030.00, 2015.00, 2035.00),  # d3 verde · no mejora el extremo (2035 < 2040) y su
    #                                            mecha 2030 no estira el UL: es la vela de margen
    (2090.00, 2095.00, 2082.00, 2085.00),  # d4 roja · HUECO AL ALZA; CONSTITUYE ID#1.
    #                                            rango 2040 - 2090 = -50. Cierra en 2085, dentro
    #                                            del OB: el ID no nace roto y llega a existir
    (2038.00, 2070.00, 2035.00, 2060.00),  # d5 verde · CASO 8: 2060 > 2050 (borde del UL) y
    #                                            2060 < 2080 (borde del OB) A LA VEZ.
    #                                            a_favor_primero   -> ROTURA_A_FAVOR
    #                                            en_contra_primero -> ROTURA_EN_CONTRA
)

#: Los mismos casos del revés. No se escriben a mano a propósito (§5.10).
SYNTHETIC_BREAK_DOWN: tuple[Candle, ...] = mirror(SYNTHETIC_BREAK_UP, MIRROR_CENTRE)
SYNTHETIC_ORDER_BLOCK_DOWN: tuple[Candle, ...] = mirror(
    SYNTHETIC_ORDER_BLOCK_UP, MIRROR_CENTRE
)
SYNTHETIC_WITHOUT_ORDER_BLOCK_DOWN: tuple[Candle, ...] = mirror(
    SYNTHETIC_WITHOUT_ORDER_BLOCK_UP, MIRROR_CENTRE
)
SYNTHETIC_OVERLAP_DOWN: tuple[Candle, ...] = mirror(SYNTHETIC_OVERLAP_UP, MIRROR_CENTRE)

#: Viernes, igual que en las fases anteriores.
SYNTHETIC_BREAK_START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)


__all__ = [
    "MIRROR_CENTRE",
    "SYNTHETIC_BREAK_DOWN",
    "SYNTHETIC_BREAK_START",
    "SYNTHETIC_BREAK_UP",
    "SYNTHETIC_ORDER_BLOCK_DOWN",
    "SYNTHETIC_ORDER_BLOCK_UP",
    "SYNTHETIC_OVERLAP_DOWN",
    "SYNTHETIC_OVERLAP_UP",
    "SYNTHETIC_WITHOUT_ORDER_BLOCK_DOWN",
    "SYNTHETIC_WITHOUT_ORDER_BLOCK_UP",
]
