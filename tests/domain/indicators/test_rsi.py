"""RSI de Wilder: los casos límite y el que se puede comprobar a mano.

El RSI no decide nada —hoy sólo se dibuja—, así que lo que se prueba es que el
número sale bien, que el hueco del arranque es hueco de verdad y que el valor de
la vela `t` no cambia porque después vengan más velas.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.indicators import rsi

PERIOD = 21


def closes(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=float)


# --- Casos límite -----------------------------------------------------------


def test_sin_velas_no_hay_indicador() -> None:
    assert rsi(closes([]), PERIOD).size == 0


def test_una_sola_vela_no_da_ni_una_variacion() -> None:
    resultado = rsi(closes([100.0]), PERIOD)
    assert resultado.size == 1
    assert np.isnan(resultado).all()


def test_con_menos_velas_que_periodo_todo_es_hueco() -> None:
    """Ventana mayor que los datos: se devuelve el hueco, no un número inventado."""
    resultado = rsi(closes([100.0 + i for i in range(PERIOD)]), PERIOD)
    assert resultado.size == PERIOD
    assert np.isnan(resultado).all()


def test_la_primera_lectura_cae_en_la_vela_del_periodo() -> None:
    """Con `period` variaciones ya hay media: ni antes, ni una vela más tarde."""
    resultado = rsi(closes([100.0 + i for i in range(PERIOD + 1)]), PERIOD)
    assert np.isnan(resultado[:PERIOD]).all()
    assert not np.isnan(resultado[PERIOD])


def test_un_periodo_invalido_falla_ruidosamente() -> None:
    with pytest.raises(ValueError):
        rsi(closes([100.0, 101.0]), 0)


# --- El número --------------------------------------------------------------


def test_sólo_subidas_dan_cien() -> None:
    resultado = rsi(closes([100.0 + i for i in range(PERIOD + 5)]), PERIOD)
    assert resultado[PERIOD:] == pytest.approx(100.0)


def test_sólo_bajadas_dan_cero() -> None:
    resultado = rsi(closes([100.0 - i for i in range(PERIOD + 5)]), PERIOD)
    assert resultado[PERIOD:] == pytest.approx(0.0)


def test_el_precio_plano_no_es_ni_fuerza_ni_debilidad() -> None:
    """Sin subidas ni bajadas la fórmula divide por cero: ahí el índice es 50."""
    resultado = rsi(closes([100.0] * (PERIOD + 5)), PERIOD)
    assert resultado[PERIOD:] == pytest.approx(50.0)


def test_el_indice_y_el_de_la_serie_espejo_suman_cien() -> None:
    """Dar la vuelta al precio intercambia subidas y bajadas: 30 pasa a ser 70.

    Es la simetría de la fórmula y no depende de los números elegidos: si el
    suavizado tratara distinto a un lado que al otro, esta suma no daría 100.
    """
    values = [100.0]
    for step in range(60):
        values.append(values[-1] + (1.7 if step % 3 else -2.3))
    derecho = rsi(closes(values), PERIOD)
    espejo = rsi(closes([200.0 - value for value in values]), PERIOD)

    assert derecho[PERIOD:] + espejo[PERIOD:] == pytest.approx(100.0)


def test_el_suavizado_es_el_de_wilder_y_no_una_media_simple() -> None:
    """Comprobado con la recursión escrita a mano, que es la definición."""
    values = [100.0]
    for step in range(60):
        values.append(values[-1] + (2.0 if step % 3 else -1.5))
    resultado = rsi(closes(values), PERIOD)

    delta = np.diff(np.array(values))
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    average_gain = gain[:PERIOD].mean()
    average_loss = loss[:PERIOD].mean()
    esperado = [100.0 - 100.0 / (1.0 + average_gain / average_loss)]
    for position in range(PERIOD, delta.size):
        average_gain = (average_gain * (PERIOD - 1) + gain[position]) / PERIOD
        average_loss = (average_loss * (PERIOD - 1) + loss[position]) / PERIOD
        esperado.append(100.0 - 100.0 / (1.0 + average_gain / average_loss))

    assert resultado[PERIOD:] == pytest.approx(np.array(esperado))


# --- Correctitud temporal ---------------------------------------------------


def test_lo_que_viene_despues_no_cambia_lo_ya_calculado() -> None:
    """El valor de la vela `t` con el histórico truncado y con el entero es el mismo.

    Es la prueba de no-look-ahead del indicador: si el RSI de una vela cambiara
    al llegar la siguiente, dibujarlo en el replay estaría enseñando futuro.
    """
    values = [100.0]
    for step in range(80):
        values.append(values[-1] + (1.5 if step % 4 else -2.0))
    entero = rsi(closes(values), PERIOD)
    truncado = rsi(closes(values[:50]), PERIOD)

    assert truncado == pytest.approx(entero[:50], nan_ok=True)
