"""El día sintético del §4: la serie de cuerpos calculada a mano.

Vive en el código y no sólo en los tests porque es un artefacto de la fase 1: el
comando `chronos structure evidencia` lo vuelve a correr y enseña el resultado
esperado al lado del obtenido, en las tres temporalidades. Los tests importan
esta misma serie, así que no hay dos copias que puedan separarse.

Los valores esperados **no** están aquí: están escritos a mano en
`tests/domain/structure/test_synthetic_day.py` y en el listado de casos de
`application/structure/evidence.py`. Esa separación es el sentido de la prueba.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Nueve casos del §4 en veinte velas. Cada par es (open, close): el módulo 1
#: sólo mira el cuerpo, y añadir mechas escondería lo que cada caso fija.
SYNTHETIC_DAY: tuple[tuple[float, float], ...] = (
    # --- pierna bajista limpia + vela contraria -> ID#1 bajista ---------------
    (2000.00, 1990.00),  # b0  bajista
    (1990.00, 1980.00),  # b1  bajista
    (1980.00, 1970.00),  # b2  bajista
    (1971.00, 1975.00),  # b3  CONTRARIA -> constituye ID#1
    # --- retroceso profundo dentro del rango: no pasa nada -------------------
    (1975.00, 1998.00),  # b4  cierra a 2 USD del ancla, pero dentro
    (1997.00, 1972.00),  # b5  cierra a 2 USD del extremo, pero dentro
    # --- ROTURA_A_FAVOR + limbo de 3 barras ----------------------------------
    (1972.00, 1965.00),  # b6  cierre bajo el extremo -> rompe ID#1
    (1965.00, 1960.00),  # b7  limbo
    (1960.00, 1955.00),  # b8  limbo
    (1955.00, 1950.00),  # b9  limbo
    (1950.00, 1950.01),  # b10 CONTRARIA de 1 céntimo -> constituye ID#2
    # --- rotura y doji que no constituye -------------------------------------
    (1950.01, 1949.00),  # b11 rompe ID#2 a favor
    (1949.00, 1949.00),  # b12 DOJI: no constituye, el limbo sigue
    (1949.00, 1945.00),  # b13 limbo
    (1945.50, 1946.00),  # b14 CONTRARIA -> constituye ID#3
    # --- ROTURA_EN_CONTRA + constitución inmediata ---------------------------
    (1946.00, 1951.00),  # b15 cierre sobre el ancla -> cambio de sesgo
    (1951.00, 1950.00),  # b16 CONTRARIA ya en la barra siguiente -> ID#4
    # --- barra que rompe y además es contraria a la pierna nueva -------------
    (1960.00, 1955.00),  # b17 rompe a favor con cuerpo bajista
    (1955.00, 1958.00),  # b18 limbo
    (1958.00, 1957.00),  # b19 CONTRARIA -> constituye ID#5 (queda vigente)
)

#: El hueco de fin de semana cae entre b5 (viernes 20:00) y b6 (domingo 20:00).
SYNTHETIC_DAY_GAP_AFTER = 5

#: Viernes, para que el hueco caiga donde tiene que caer.
SYNTHETIC_DAY_START = datetime(2024, 3, 8, 0, 0, tzinfo=UTC)

#: Salto del viernes a la reapertura del domingo.
SYNTHETIC_DAY_GAP = timedelta(days=1, hours=20)
