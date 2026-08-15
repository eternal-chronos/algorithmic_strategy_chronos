"""⚠️ Las tres definiciones de rechazo (§2). **Ninguna adoptada.**

Los esperados están escritos a mano antes de correr el motor. Lo que se fija
aquí no es cuál es la buena —eso lo decide el propietario mirando capturas— sino
que las tres hacen exactamente lo que dicen y que ninguna mira al futuro.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chronos.domain.entries.rejection import (
    REJECTION_PERCENTILES,
    RollingWickPercentile,
    body_size,
    mark_rejections,
    rejects_r1,
    rejects_r2,
    rejects_r3,
    wick_against,
    wick_body_ratios,
)
from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA
START = pd.Timestamp("2024-03-04", tz="UTC")


def series(*candles: tuple[float, float, float, float]) -> CandleSeries:
    index = pd.date_range(START, periods=len(candles), freq="1h", tz="UTC")
    return CandleSeries(
        timestamps=pd.DatetimeIndex(index),
        open=np.array([candle[0] for candle in candles], dtype=float),
        high=np.array([candle[1] for candle in candles], dtype=float),
        low=np.array([candle[2] for candle in candles], dtype=float),
        close=np.array([candle[3] for candle in candles], dtype=float),
    )


def zone(low: float, high: float, direction: ImpulseDirection = UP) -> Zone:
    """Una zona de precio suelta, con los bordes en el orden que le toca."""
    inner, outer = (high, low) if direction is UP else (low, high)
    return Zone(
        kind=ZoneKind.ORDER_BLOCK,
        id_num=1,
        timeframe="H4",
        direction=direction,
        index_defining=0,
        ts_defining=START.to_pydatetime(),
        defining_body=BodyDirection.BEARISH,
        inner=inner,
        outer=outer,
        ts_outer_known=START.to_pydatetime(),
        ts_birth=START.to_pydatetime(),
    )


# --- Las mechas y los cuerpos ------------------------------------------------


def test_la_mecha_contraria_de_un_rechazo_alcista_es_la_de_abajo() -> None:
    candles = series((100.0, 106.0, 90.0, 104.0))
    assert wick_against(candles, 0, UP) == pytest.approx(10.0)
    assert wick_against(candles, 0, DOWN) == pytest.approx(2.0)
    assert body_size(candles, 0) == pytest.approx(4.0)


def test_el_doji_no_entra_en_la_distribucion_de_r2() -> None:
    """Sin cuerpo no hay proporción que medir, y no se inventa un cuerpo mínimo."""
    candles = series((100.0, 110.0, 90.0, 100.0), (100.0, 104.0, 98.0, 102.0))
    ratios = wick_body_ratios(candles, UP)
    assert np.isnan(ratios[0])
    assert ratios[1] == pytest.approx(1.0)


# --- R1 ----------------------------------------------------------------------


def test_r1_marca_cuando_la_mecha_entra_y_el_cuerpo_cierra_fuera() -> None:
    candles = series((102.0, 106.0, 98.0, 105.0))  # la mecha baja a 98, cierra en 105
    assert rejects_r1(candles, 0, UP, zone(96.0, 100.0)) is True


def test_r1_no_marca_si_el_cierre_se_queda_dentro_de_la_zona() -> None:
    candles = series((102.0, 103.0, 98.0, 99.0))
    assert rejects_r1(candles, 0, UP, zone(96.0, 100.0)) is False


def test_r1_no_marca_si_la_mecha_ni_llega_a_la_zona() -> None:
    candles = series((102.0, 106.0, 101.0, 105.0))
    assert rejects_r1(candles, 0, UP, zone(96.0, 100.0)) is False


def test_r1_es_simetrico_en_el_espejo() -> None:
    up = series((102.0, 106.0, 98.0, 105.0))
    down = series((98.0, 102.0, 94.0, 95.0))
    assert rejects_r1(up, 0, UP, zone(96.0, 100.0)) is True
    assert rejects_r1(down, 0, DOWN, zone(100.0, 104.0, DOWN)) is True


def test_r1_no_marca_sobre_un_doji() -> None:
    """§2.2 declara el doji neutro en todo el módulo y aquí no se rompe.

    R1 podría evaluarse sobre un doji —sólo mira mecha y cierre— y se excluye
    igualmente, para que las tres definiciones se comparen sobre la misma
    población y la tabla de solape del §2 signifique algo.
    """
    candles = series((105.0, 106.0, 98.0, 105.0))
    assert rejects_r1(candles, 0, UP, zone(96.0, 100.0)) is False


# --- R2 ----------------------------------------------------------------------


def test_r2_marca_cuando_la_mecha_supera_el_umbral() -> None:
    candles = series((100.0, 101.0, 90.0, 102.0))  # mecha 10, cuerpo 2 -> 5,0
    assert rejects_r2(candles, 0, UP, threshold=4.0) is True
    assert rejects_r2(candles, 0, UP, threshold=5.0) is False  # estricto


def test_r2_no_marca_sin_distribucion_con_la_que_comparar() -> None:
    candles = series((100.0, 101.0, 90.0, 102.0))
    assert rejects_r2(candles, 0, UP, threshold=float("nan")) is False


def test_el_umbral_de_r2_solo_mira_sesiones_anteriores() -> None:
    """El umbral vigente en la sesión de hoy se calcula con las de ayer atrás.

    La primera sesión no tiene ninguna anterior, así que su umbral es `NaN` y R2
    no marca: no se rellena el hueco con la sesión en curso, que sería el
    lookahead que esta clase existe para impedir.
    """
    candles = series(*[(100.0, 101.0, 90.0, 102.0)] * 6)
    sessions = np.array([0, 0, 0, 1, 1, 1])
    rolling = RollingWickPercentile(candles, sessions, direction=UP)
    rolling.advance(5)

    assert np.isnan(rolling.at(0, 75))
    assert np.isnan(rolling.at(2, 75))
    assert rolling.at(3, 75) == pytest.approx(5.0)


def test_el_umbral_no_se_mueve_dentro_de_una_sesion() -> None:
    """Si se recalculara vela a vela, la vela juzgada entraría en su propio umbral."""
    candles = series(
        (100.0, 101.0, 90.0, 102.0),
        (100.0, 101.0, 95.0, 102.0),
        (100.0, 101.0, 50.0, 102.0),
        (100.0, 101.0, 99.0, 102.0),
    )
    sessions = np.array([0, 0, 1, 1])
    rolling = RollingWickPercentile(candles, sessions, direction=UP)
    rolling.advance(3)
    assert rolling.at(2, 75) == rolling.at(3, 75)


def test_pedir_el_umbral_mas_alla_de_la_frontera_es_lookahead() -> None:
    candles = series(*[(100.0, 101.0, 90.0, 102.0)] * 4)
    rolling = RollingWickPercentile(candles, np.array([0, 0, 1, 1]), direction=UP)
    rolling.advance(1)
    with pytest.raises(LookaheadError):
        rolling.at(2, 75)


def test_un_percentil_fuera_de_la_rejilla_no_se_inventa() -> None:
    candles = series(*[(100.0, 101.0, 90.0, 102.0)] * 2)
    rolling = RollingWickPercentile(candles, np.array([0, 1]), direction=UP)
    rolling.advance(1)
    with pytest.raises(StructureError):
        rolling.at(1, 42)


def test_la_rejilla_del_enunciado_es_la_del_dominio() -> None:
    assert REJECTION_PERCENTILES == (60, 75, 90)


# --- R3 ----------------------------------------------------------------------


def test_r3_marca_cuando_el_cierre_queda_en_el_tercio_favorable() -> None:
    candles = series((100.0, 130.0, 100.0, 125.0))  # rango 30, tercio alto desde 120
    assert rejects_r3(candles, 0, UP) is True
    assert rejects_r3(candles, 0, DOWN) is False


def test_r3_es_simetrico_en_el_espejo() -> None:
    candles = series((130.0, 130.0, 100.0, 105.0))
    assert rejects_r3(candles, 0, DOWN) is True
    assert rejects_r3(candles, 0, UP) is False


def test_r3_no_marca_una_vela_sin_rango() -> None:
    candles = series((100.0, 100.0, 100.0, 100.0))
    assert rejects_r3(candles, 0, UP) is False


# --- Las tres a la vez -------------------------------------------------------


def test_las_tres_se_marcan_siempre_y_ninguna_gana() -> None:
    """El motor no elige: devuelve las tres y quien las use dice cuál mira."""
    candles = series((102.0, 106.0, 90.0, 105.0))
    marks = mark_rejections(
        candles, 0, UP, zone(88.0, 100.0), {60: 1.0, 75: 3.0, 90: 100.0}
    )
    assert marks.r1 is True
    assert marks.r2 == {60: True, 75: True, 90: False}
    assert marks.r3 is True
    assert marks.any_kind is True
    assert len(marks.kinds(60)) == 3
    assert len(marks.kinds(90)) == 2
