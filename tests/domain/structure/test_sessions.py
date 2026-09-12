"""El alto y el bajo de Asia y de Londres, marcados a las 7:58 del reloj del gráfico.

Las barras se construyen en el reloj de Nueva York y se convierten a UTC, y la
regla se pasa con ese mismo reloj: lo que se comprueba es la regla del
propietario en su hora, no en la del servidor. El reloj por defecto de la regla
—el UTC-4 fijo de su plataforma— tiene su propio test.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
import pytest

from chronos.domain.errors import DomainError
from chronos.domain.structure.sessions import (
    SESSION_COLUMNS,
    SessionLevelsRule,
    session_levels,
)

NY = "America/New_York"
#: La regla en el reloj de la plaza, con horario de verano real.
RULE = SessionLevelsRule(timezone=NY)


def _m1(start: str, end: str, *, base: float = 2000.0) -> pd.DataFrame:
    """M1 plano a `base` entre dos horas de pared de Nueva York (`end` incluido)."""
    index = pd.date_range(start, end, freq="1min", tz=NY).tz_convert("UTC")
    return pd.DataFrame(
        {
            "open": base,
            "high": base,
            "low": base,
            "close": base,
            "volume": 1.0,
        },
        index=index,
    )


def _spike(frame: pd.DataFrame, at: str, *, high: float | None = None, low: float | None = None) -> None:
    stamp = pd.Timestamp(at, tz=NY).tz_convert("UTC")
    if high is not None:
        frame.loc[stamp, "high"] = high
    if low is not None:
        frame.loc[stamp, "low"] = low


def _utc(at: str) -> pd.Timestamp:
    return pd.Timestamp(at, tz=NY).tz_convert("UTC")


@pytest.fixture
def day() -> pd.DataFrame:
    """Un día entero de mercado: del domingo 17:00 al lunes 17:00 de Nueva York."""
    frame = _m1("2024-01-07 17:00", "2024-01-08 16:59")
    _spike(frame, "2024-01-07 22:30", high=2010.0)  # alto de Asia
    _spike(frame, "2024-01-08 00:59", low=1990.0)  # bajo de Asia, último minuto
    _spike(frame, "2024-01-08 05:15", high=2020.0)  # alto de Londres
    _spike(frame, "2024-01-08 03:00", low=1985.0)  # bajo de Londres, primer minuto
    return frame


def test_marca_los_cuatro_niveles_a_las_7_58_de_nueva_york(day: pd.DataFrame) -> None:
    levels = session_levels(day, RULE)

    assert list(levels.columns) == list(SESSION_COLUMNS)
    assert len(levels) == 1
    row = levels.iloc[0]
    assert row["day"] == pd.Timestamp("2024-01-08")
    # 7:58 de enero en Nueva York son las 12:58 UTC.
    assert row["marked_at"] == pd.Timestamp("2024-01-08 12:58", tz="UTC")
    assert row["until"] == _utc("2024-01-08 17:00")
    assert row["asia_high"] == 2010.0
    assert row["asia_low"] == 1990.0
    assert row["london_high"] == 2020.0
    assert row["london_low"] == 1985.0
    assert row["asia_high_at"] == _utc("2024-01-07 22:30")
    assert row["asia_low_at"] == _utc("2024-01-08 00:59")
    assert row["london_high_at"] == _utc("2024-01-08 05:15")
    assert row["london_low_at"] == _utc("2024-01-08 03:00")


def test_la_marca_sigue_al_horario_de_verano(day: pd.DataFrame) -> None:
    """En julio las 7:58 de Nueva York son las 11:58 UTC: la hora es de plaza."""
    summer = _m1("2024-07-07 17:00", "2024-07-08 16:59")
    levels = session_levels(summer, RULE)

    assert levels.iloc[0]["marked_at"] == pd.Timestamp("2024-07-08 11:58", tz="UTC")
    assert levels.iloc[0]["until"] == pd.Timestamp("2024-07-08 21:00", tz="UTC")


def test_lo_que_queda_fuera_de_las_ventanas_no_cuenta(day: pd.DataFrame) -> None:
    _spike(day, "2024-01-07 19:59", high=2100.0)  # un minuto antes de Asia
    _spike(day, "2024-01-08 01:00", low=1900.0)  # Asia ya ha cerrado
    _spike(day, "2024-01-08 02:30", high=2100.0)  # entre Asia y Londres
    _spike(day, "2024-01-08 09:00", low=1900.0)  # Nueva York

    row = session_levels(day, RULE).iloc[0]
    assert row["asia_high"] == 2010.0
    assert row["asia_low"] == 1990.0
    assert row["london_high"] == 2020.0
    assert row["london_low"] == 1985.0


def test_londres_se_mide_hasta_la_marca_y_no_hasta_las_8(day: pd.DataFrame) -> None:
    """A las 7:58 los minutos 7:58 y 7:59 no han cerrado: no entran, aunque la
    sesión termine a las 8:00. Lo contrario sería enseñar el futuro."""
    _spike(day, "2024-01-08 07:57", high=2030.0)  # cierra a las 7:58: entra
    _spike(day, "2024-01-08 07:58", high=2040.0)  # cierra a las 7:59: no entra
    _spike(day, "2024-01-08 07:59", low=1900.0)

    row = session_levels(day, RULE).iloc[0]
    assert row["london_high"] == 2030.0
    assert row["london_low"] == 1985.0


def test_sin_look_ahead_la_marca_es_la_misma_con_el_historico_truncado(
    day: pd.DataFrame,
) -> None:
    truncated = day[day.index < _utc("2024-01-08 07:58")]

    full = session_levels(day, RULE)
    partial = session_levels(truncated, RULE)

    pd.testing.assert_frame_equal(full, partial)


def test_si_el_historico_termina_antes_de_la_marca_no_hay_marca(day: pd.DataFrame) -> None:
    """Hasta las 7:58 no se ha marcado nada: un histórico que acaba a las 7:00
    no puede traer la marca de ese día."""
    early = day[day.index < _utc("2024-01-08 07:00")]
    assert session_levels(early, RULE).empty


def test_asia_pertenece_al_dia_en_que_termina() -> None:
    """El Asia del domingo por la noche se marca el lunes; el del lunes, el martes."""
    two_days = _m1("2024-01-07 17:00", "2024-01-09 16:59")
    _spike(two_days, "2024-01-07 21:00", high=2010.0)
    _spike(two_days, "2024-01-08 21:00", high=2050.0)

    levels = session_levels(two_days, RULE)
    assert list(levels["day"]) == [pd.Timestamp("2024-01-08"), pd.Timestamp("2024-01-09")]
    assert list(levels["asia_high"]) == [2010.0, 2050.0]


def test_una_sesion_sin_barras_deja_su_nivel_vacio() -> None:
    """Festivo con Londres cerrado: la marca existe con lo que se sabía —Asia—
    y Londres queda en blanco, no en cero ni copiado de Asia."""
    frame = _m1("2024-01-07 17:00", "2024-01-08 01:30")
    tail = _m1("2024-01-08 08:00", "2024-01-08 16:59")
    frame = pd.concat([frame, tail])
    _spike(frame, "2024-01-07 23:00", high=2010.0, low=1990.0)

    row = session_levels(frame, RULE).iloc[0]
    assert row["asia_high"] == 2010.0
    assert row["asia_low"] == 1990.0
    assert np.isnan(row["london_high"])
    assert np.isnan(row["london_low"])
    assert pd.isna(row["london_high_at"])


def test_un_dia_sin_ninguna_de_las_dos_sesiones_no_se_marca() -> None:
    only_ny = _m1("2024-01-08 09:00", "2024-01-08 16:59")
    assert session_levels(only_ny, RULE).empty


def test_el_fin_de_semana_no_produce_marcas() -> None:
    """Del viernes 17:00 al domingo 17:00 no hay barras: ni sábado ni domingo se marcan."""
    week = pd.concat(
        [
            _m1("2024-01-11 17:00", "2024-01-12 16:59"),  # jueves tarde → viernes
            _m1("2024-01-14 17:00", "2024-01-15 16:59"),  # domingo tarde → lunes
        ]
    )
    levels = session_levels(week, RULE)
    assert list(levels["day"]) == [pd.Timestamp("2024-01-12"), pd.Timestamp("2024-01-15")]


def test_con_un_dataframe_vacio_no_hay_nada_que_marcar() -> None:
    empty = pd.DataFrame(columns=["open", "high", "low", "close"])
    levels = session_levels(empty, RULE)
    assert levels.empty
    assert list(levels.columns) == list(SESSION_COLUMNS)


def test_exige_indice_con_zona_horaria(day: pd.DataFrame) -> None:
    naive = day.copy()
    naive.index = pd.DatetimeIndex(naive.index).tz_localize(None)
    with pytest.raises(DomainError):
        session_levels(naive, RULE)


def test_por_defecto_el_reloj_es_el_utc_4_fijo_de_la_pantalla() -> None:
    """El propietario lee las 7:58 en cTrader, que va en UTC-4 todo el año: en
    enero la marca cae a las 11:58 UTC y no a las 12:58 de Nueva York, que en su
    pantalla serían las 8:58, tarde para operar las 8:00."""
    winter = _m1("2024-01-07 17:00", "2024-01-08 16:59")
    levels = session_levels(winter)

    assert SessionLevelsRule().timezone == "Etc/GMT+4"
    assert levels.iloc[0]["marked_at"] == pd.Timestamp("2024-01-08 11:58", tz="UTC")
    assert levels.iloc[0]["until"] == pd.Timestamp("2024-01-08 21:00", tz="UTC")


def test_la_regla_se_describe_con_las_horas_que_de_verdad_mide() -> None:
    assert RULE.describe() == (
        "Asia 20:00 → 01:00 · Londres 03:00 → 07:58 · marca a las 07:58 · "
        "hasta las 17:00 · reloj America/New_York"
    )
    assert SessionLevelsRule().describe("UTC-4").endswith("reloj UTC-4")


def test_la_regla_no_admite_marcar_despues_de_retirar() -> None:
    with pytest.raises(DomainError):
        SessionLevelsRule(mark_at=time(18, 0))
