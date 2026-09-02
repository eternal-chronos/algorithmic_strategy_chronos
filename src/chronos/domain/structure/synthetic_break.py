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
    4. los tres anteriores con el PUL ............. `SYNTHETIC_PENULTIMATE_UP`, e8/e9/e10
    5. sin PUL -> muere por línea ................. `SYNTHETIC_PENULTIMATE_UP`, ID#1 en e5
    6. con el PUL -> ya no muere .................. `SYNTHETIC_PENULTIMATE_UP`, e8
    7. sobrevive y extiende el extremo ............ `SYNTHETIC_BREAK_UP`, b4 y b5
    8. zonas y una vela que cumple las dos ........ `SYNTHETIC_OVERLAP_UP`, d4
    9. UL de altura cero .......................... `SYNTHETIC_BREAK_UP`, ID#2
   10. todos los anteriores en bajista ............ los espejos, sin escribir a mano

Los espejos se derivan con el `mirror` de la fase 2.0 y no se escriben a mano a
propósito: la reflexión conserva todas las desigualdades estrictas del módulo,
así que cualquier asimetría que aparezca al correrlos es un fallo del código.

**Casos 5 y 6 en la misma serie.** Ya no hacen falta dos versiones de las mismas
velas: con la regla del PUL, quedarse sin zona en el lado en contra sólo le pasa
al **primer ID del histórico**, que no tiene ID anterior del que sacarla. El
`SYNTHETIC_PENULTIMATE_UP` lleva los dos dentro: el ID#1 muere por línea porque
no tiene PUL (caso 5) y el ID#2 sobrevive a un cierre más allá de su línea
porque el suyo lo cubre (caso 6).

**Caso 8, y lo que enseña.** Que dos niveles se solapen en precio **no** basta
para que una vela cumpla las dos condiciones de rotura: hace falta que los dos
bordes exteriores estén *invertidos*, y eso es lo contrario de solaparse. En un
ID alcista el borde exterior del UL está en la mecha de la vela del extremo y el
del lado en contra nunca pasa del ancla, así que

    borde exterior del UL >= extremo > ancla >= nivel en contra

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
    (2000.00, 2002.00, 1994.00, 1995.00),  # b0 roja · semilla bajista; el ID#1 no tiene PUL
    (1995.00, 2006.00, 1994.00, 2005.00),  # b1 verde · abre la pierna del ID#1
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

#: Casos 4, 5 y 6. Un ID#1 bajista que muere por línea porque es el primero del
#: histórico y no tiene PUL, y el ID#2 alcista que nace de esa rotura: su PUL es
#: el cuerpo de `e2`, la vela que fijó el extremo del ID#1, y cubre la línea de
#: su propio ancla, así que aguanta dos velas que la fase 1 habría llamado
#: rotura y cede a la tercera.
SYNTHETIC_PENULTIMATE_UP: tuple[Candle, ...] = (
    (1990.00, 1996.00, 1989.00, 1995.00),  # e0 verde · semilla alcista; su cuerpo alto (1995) es
    #                                            el ancla del ID#1
    (1995.00, 1996.00, 1985.00, 1986.00),  # e1 roja · abre la pierna bajista
    (1986.00, 1987.00, 1975.00, 1976.00),  # e2 roja · extremo del ID#1. Su cuerpo [1976, 1986]
    #                                            será el PUL del ID#2: ancho a propósito
    (1976.00, 1981.00, 1975.00, 1980.00),  # e3 verde · CONSTITUYE ID#1 bajista; ancla 1995
    (1980.00, 1981.00, 1977.00, 1978.00),  # e4 roja · retroceso; última contraria antes de la
    #                                            pierna alcista, así que su cuerpo bajo (1978)
    #                                            será el ancla del ID#2
    (1978.00, 1998.00, 1977.00, 1997.00),  # e5 verde · CASO 5: cierra 1997 > 1995 y mata al ID#1
    #                                            POR LÍNEA: es el primer ID y no tiene PUL
    (1997.00, 2006.00, 1996.00, 2005.00),  # e6 verde · extremo del ID#2 alcista
    (2005.00, 2006.00, 2002.00, 2003.00),  # e7 roja · CONSTITUYE ID#2; ancla 1978, extremo 2005
    #                                            PUL#2 = [1976, 1986], interior 1986
    (2003.00, 2004.00, 1976.50, 1977.00),  # e8 roja · CASOS 4b y 6: cierra 1977, bajo la línea
    #                                            1978 pero dentro del PUL, y la mecha 1976.50 no
    #                                            llega a 1976. SOBREVIVE
    (1977.00, 1978.00, 1975.00, 1976.50),  # e9 roja · CASO 4a: la mecha 1975 PERFORA el borde
    #                                            1976 y el cierre 1976.50 se queda dentro. SOBREVIVE
    (1976.50, 1977.00, 1974.00, 1975.00),  # e10 roja · CASO 4c: cierra 1975 < 1976.
    #                                            ROTURA_EN_CONTRA por zona
)


#: Caso 8. Un hueco a la baja se salta el ancla y deja un ID de **rango
#: negativo**: su extremo (2040) queda por debajo de su ancla (2090). Ahí, y sólo
#: ahí, los dos niveles se invierten —el borde exterior del UL en 2050 y la línea
#: del ancla en 2090— y una vela puede cerrar más allá de los dos a la vez.
#:
#: La vela que constituye vuelve con otro hueco por encima de esa línea (cierra
#: en 2091, sobre el ancla 2090). No es adorno: si cerrase por debajo, el ID
#: nacería ya roto y la regla no lo dejaría nacer, así que el conflicto no
#: existiría. Y por eso entre el extremo y la constitución hay una vela de margen
#: (d3): con la constituyente pegada al extremo, su propia mecha estiraría el UL
#: y volvería a cerrar la ventana del conflicto.
SYNTHETIC_OVERLAP_UP: tuple[Candle, ...] = (
    (2100.00, 2105.00, 2080.00, 2090.00),  # d0 roja · semilla bajista; ancla del ID#1 = 2090
    (2000.00, 2106.00, 1995.00, 2010.00),  # d1 verde · HUECO A LA BAJA; abre la pierna alcista
    (2010.00, 2050.00, 2008.00, 2040.00),  # d2 verde · extremo del ID#1 (cuerpo 2040, mecha 2050)
    (2020.00, 2030.00, 2015.00, 2035.00),  # d3 verde · no mejora el extremo (2035 < 2040) y su
    #                                            mecha 2030 no estira el UL: es la vela de margen
    (2095.00, 2096.00, 2082.00, 2091.00),  # d4 roja · HUECO AL ALZA; CONSTITUYE ID#1.
    #                                            rango 2040 - 2090 = -50. Cierra en 2091, sobre la
    #                                            línea 2090: el ID#1 es el primero del histórico y
    #                                            no tiene PUL, así que ese lado lo manda el ancla
    (2038.00, 2070.00, 2035.00, 2060.00),  # d5 verde · CASO 8: 2060 > 2050 (borde del UL) y
    #                                            2060 < 2090 (línea del ancla) A LA VEZ.
    #                                            a_favor_primero   -> ROTURA_A_FAVOR
    #                                            en_contra_primero -> ROTURA_EN_CONTRA
)

#: Los mismos casos del revés. No se escriben a mano a propósito (§5.10).
SYNTHETIC_BREAK_DOWN: tuple[Candle, ...] = mirror(SYNTHETIC_BREAK_UP, MIRROR_CENTRE)
SYNTHETIC_PENULTIMATE_DOWN: tuple[Candle, ...] = mirror(
    SYNTHETIC_PENULTIMATE_UP, MIRROR_CENTRE
)
SYNTHETIC_OVERLAP_DOWN: tuple[Candle, ...] = mirror(SYNTHETIC_OVERLAP_UP, MIRROR_CENTRE)

#: Viernes, igual que en las fases anteriores.
SYNTHETIC_BREAK_START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)


__all__ = [
    "MIRROR_CENTRE",
    "SYNTHETIC_BREAK_DOWN",
    "SYNTHETIC_BREAK_START",
    "SYNTHETIC_BREAK_UP",
    "SYNTHETIC_OVERLAP_DOWN",
    "SYNTHETIC_OVERLAP_UP",
    "SYNTHETIC_PENULTIMATE_DOWN",
    "SYNTHETIC_PENULTIMATE_UP",
]
