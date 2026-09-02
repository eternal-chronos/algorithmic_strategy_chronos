"""El día sintético de la fase 2.0 (§6): OHLC calculado a mano.

El día sintético de la fase 1 son pares `(open, close)` porque el módulo 1 sólo
mira el cuerpo. Las zonas son un asunto de mechas, así que aquí hacen falta las
cuatro cifras y una serie propia: añadirle mechas a la de la fase 1 cambiaría
las velas de sus tests.

Vive en el código y no sólo en los tests porque `chronos structure zonas
evidencia` lo vuelve a correr y enseña el esperado al lado del obtenido. Los
**valores esperados no están aquí**: están escritos a mano en
`tests/domain/structure/test_synthetic_zones.py` y en el listado de casos de
`application/structure/zone_evidence.py`. Esa separación es el sentido de la
prueba.

La serie bajista **no se escribe a mano**: es la alcista reflejada en el espejo
`p -> 2C - p` (con `high` y `low` intercambiados). Un espejo es exacto —todas
las desigualdades del módulo son estrictas y la reflexión las conserva— así que
cualquier asimetría que aparezca al correrla es un fallo del código y no de la
serie, que es justo lo que el §6.9 pide comprobar.

Los casos del §6 y dónde caen:

    1. UL de mecha normal ................. ID#1, ID#4, ID#5
    2. UL que se extiende a la siguiente .. ID#2 (b6 llega a 2023 > 2020)
    3. UL que NO se extiende .............. ID#1 (2011 < 2012) y ID#3 (empate exacto)
    4. UL de altura cero .................. ID#3 (b8 cierra en su propio máximo)
    5. PUL = el UL anterior hecho cuerpo .. ID#2 (b2), ID#3 (b5), ID#4 (b8), ID#5 (b12)
    6. ID sin PUL ......................... ID#1, el primero del histórico
    7. UL y PUL sobre la MISMA vela ....... b2: UL [2010, 2012] y PUL#2 [2005, 2010]

**Caso 6 — sólo le pasa al primero.** El PUL sale de la vela del extremo del ID
anterior, así que el único impulso que puede quedarse sin él es el que no tiene
ID anterior. En el histórico real es un caso por temporalidad, y el informe lo
cuenta para que se vea que es exactamente uno.

**Caso 7 — las dos zonas se reparten la misma vela sin tocarse.** El UL toma el
tramo de mecha, del borde del cuerpo a la punta; el PUL del ID siguiente toma el
cuerpo de esa misma vela. Comparten el borde —2010 en b2— y no se solapan en
ningún otro precio.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: Una vela por fila: `(open, high, low, close)`.
Candle = tuple[float, float, float, float]

#: Veinte velas con cinco impulsos alcistas encadenados. Cada rotura es a favor
#: salvo la última, que es en contra y mata al ID#5.
SYNTHETIC_ZONES_UP: tuple[Candle, ...] = (
    # --- ID#1: UL de mecha normal que no se extiende --------------------------
    (2000.00, 2002.00, 1994.00, 1995.00),  # b0  roja · semilla; el ID#1 no tiene PUL
    (1995.00, 2006.00, 1994.00, 2005.00),  # b1  verde · abre la pierna del ID#1
    (2005.00, 2012.00, 2004.00, 2010.00),  # b2  verde · fija el extremo (cuerpo 2010, mecha 2012);
    #                                             su cuerpo [2005, 2010] será el PUL del ID#2
    (2010.00, 2011.00, 2008.00, 2009.00),  # b3  roja · CONSTITUYE ID#1; 2011 < 2012, no extiende
    # --- ID#2: UL que se extiende a la vela siguiente -------------------------
    (2009.00, 2016.00, 2008.00, 2015.00),  # b4  verde · ROTURA_A_FAVOR de ID#1
    (2015.00, 2020.00, 2014.00, 2019.00),  # b5  verde · extremo del ID#2 (cuerpo 2019, mecha 2020)
    (2019.00, 2023.00, 2018.00, 2018.50),  # b6  roja · CONSTITUYE ID#2; 2023 > 2020, EXTIENDE
    # --- ID#3: UL de altura cero ----------------------------------------------
    (2018.50, 2024.00, 2018.00, 2023.00),  # b7  verde · ROTURA_A_FAVOR de ID#2
    (2023.00, 2026.00, 2022.00, 2026.00),  # b8  verde · cierra en su máximo: sin mecha superior
    (2026.00, 2026.00, 2023.00, 2024.00),  # b9  roja · CONSTITUYE ID#3; empate en 2026, no extiende
    # --- ID#4: PUL en el cuerpo de b8, que es plano por arriba ----------------
    (2024.00, 2045.00, 2023.00, 2023.50),  # b10 roja · retroceso con mecha alta que no define nada
    (2023.50, 2031.00, 2023.00, 2030.00),  # b11 verde · ROTURA_A_FAVOR de ID#3
    (2030.00, 2035.00, 2029.00, 2034.00),  # b12 verde · extremo del ID#4
    (2034.00, 2034.50, 2033.00, 2033.50),  # b13 roja · CONSTITUYE ID#4
    (2033.50, 2040.00, 2033.00, 2034.00),  # b14 verde · cierra en el extremo, y "más allá" es
    #                                             estricto: no rompe
    (2033.80, 2046.00, 2033.50, 2034.00),  # b15 verde · mecha larga que tampoco define nada
    # --- ID#5: muere por rotura en contra --------------------------------------
    (2034.00, 2200.00, 2033.00, 2033.60),  # b16 roja · retroceso con una mecha enorme en 2200
    (2033.60, 2040.00, 2033.00, 2039.00),  # b17 verde · ROTURA_A_FAVOR de ID#4
    (2039.00, 2039.50, 2035.00, 2036.00),  # b18 roja · CONSTITUYE ID#5
    (2036.00, 2037.00, 2030.00, 2031.00),  # b19 roja · ROTURA_EN_CONTRA: mata al ID#5
)

#: Precio alrededor del cual se refleja la serie para obtener la bajista.
MIRROR_CENTRE = 2000.0


def mirror(candles: tuple[Candle, ...], centre: float = MIRROR_CENTRE) -> tuple[Candle, ...]:
    """Refleja la serie en `centre`: `p -> 2c - p`, con `high` y `low` cambiados.

    Una vela verde sale roja, el techo del cuerpo sale base del cuerpo y la mecha
    superior sale inferior. Todas las comparaciones del módulo son estrictas y la
    reflexión invierte el sentido de todas a la vez, así que la serie reflejada
    tiene que producir exactamente los impulsos y las zonas espejo. Si no lo
    hace, el fallo está en el código.
    """
    return tuple(
        (
            2 * centre - open_,
            2 * centre - low,
            2 * centre - high,
            2 * centre - close,
        )
        for open_, high, low, close in candles
    )


#: La misma historia del revés. No se escribe a mano a propósito (§6.9).
SYNTHETIC_ZONES_DOWN: tuple[Candle, ...] = mirror(SYNTHETIC_ZONES_UP)

#: Viernes, igual que en la fase 1: el hueco de fin de semana cae donde debe si
#: alguien vuelve a usar esta serie con paso diario.
SYNTHETIC_ZONES_START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)


__all__ = [
    "MIRROR_CENTRE",
    "SYNTHETIC_ZONES_DOWN",
    "SYNTHETIC_ZONES_START",
    "SYNTHETIC_ZONES_UP",
    "Candle",
    "mirror",
]
