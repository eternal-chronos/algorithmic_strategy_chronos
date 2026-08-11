"""Censo de velas cortas y cortes de sesión (B.1 a B.4).

El histórico sintético de `make_m1_history` abre el domingo a las 22:00 UTC, así
que reproduce exactamente el defecto que se está midiendo: con el corte diario en
00:00 esas dos horas se quedan solas en una vela diaria propia.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from chronos.application.structure.config import (
    DAILY,
    H4,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.application.structure.session_audit import (
    CUT_COLUMNS,
    audit_timeframe,
    colour_audit,
    contrast_rows,
    cut_metrics,
    equivalent_cuts,
    session_dates,
    short_bars,
)
from chronos.domain.structure.enums import AnchorMode, SeedMode
from chronos.infrastructure.structure.aggregation import aggregate, minutes_covered
from tests.conftest import make_m1_history

STEP = pd.Timedelta(hours=4)
START = pd.Timestamp("2024-03-08 00:00", tz="UTC")


@pytest.fixture(scope="module")
def m1() -> pd.DataFrame:
    return make_m1_history(weeks=6)


def base_config(session_start: str = "00:00") -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({DAILY: (DAILY,)}),
        aggregation=AggregationConfig(d_session_start=session_start),
        rules=ImpulseRulesConfig(warmup_bars=0),
    )


def analysis_of(m1: pd.DataFrame, config: ImpulseConfig, timeframe: str = DAILY):
    bars = aggregate(m1, timeframe, config.aggregation).frame
    run = DetectDominantImpulses(config).execute({timeframe: bars})
    return run.analyses[timeframe], bars


# --- B.1 --------------------------------------------------------------------


def test_el_corte_de_medianoche_deja_el_domingo_en_una_vela_propia(m1: pd.DataFrame) -> None:
    """Las dos horas del domingo no caben en la vela del lunes y hacen la suya."""
    config = base_config("00:00")
    bars = aggregate(m1, DAILY, config.aggregation).frame
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    short = short_bars(minutes, DAILY)
    weekdays = pd.DatetimeIndex(bars.index).dayofweek.to_numpy()

    assert short.any()
    # Toda vela corta cae en domingo, y todo domingo es una vela corta.
    assert set(weekdays[short].tolist()) == {6}
    assert short.sum() == (weekdays == 6).sum()


def test_moviendo_el_corte_a_la_apertura_desaparecen_las_velas_cortas(m1: pd.DataFrame) -> None:
    config = base_config("22:00")
    bars = aggregate(m1, DAILY, config.aggregation).frame
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    assert not short_bars(minutes, DAILY).any()


def test_el_recuento_de_minutos_va_alineado_con_las_velas(m1: pd.DataFrame) -> None:
    """Una posición del recuento es la misma posición de las velas agregadas."""
    config = base_config("00:00")
    bars = aggregate(m1, H4, config.aggregation).frame
    minutes = minutes_covered(m1, H4, config.aggregation, pd.DatetimeIndex(bars.index))

    assert len(minutes) == len(bars)
    assert minutes.max() == 240
    assert minutes.sum() == len(m1.loc[: bars.index[-1] + pd.Timedelta(hours=4)]) - 1


def test_el_censo_reparte_las_cortas_por_dia_de_la_semana(m1: pd.DataFrame) -> None:
    config = base_config("00:00")
    analysis, bars = analysis_of(m1, config)
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    audit = audit_timeframe(analysis, minutes)

    domingo = audit.by_weekday.set_index("dia").loc["domingo"]
    assert domingo["cortas"] == audit.short_bars
    assert domingo["pct_de_las_cortas"] == pytest.approx(100.0)
    assert audit.census.iloc[-1]["anio"] == "TOTAL"
    assert audit.census.iloc[-1]["velas"] == len(bars)
    # El síntoma que dispara todo el censo: el corte de medianoche añade una vela
    # por semana —la del domingo— sobre el corte que respeta la apertura.
    _, sin_domingo = analysis_of(m1, base_config("22:00"))
    assert len(bars) - len(sin_domingo) == audit.short_bars


def test_el_censo_exige_un_recuento_por_vela(m1: pd.DataFrame) -> None:
    analysis, _ = analysis_of(m1, base_config("00:00"))

    with pytest.raises(ValueError, match="recuentos de minutos"):
        audit_timeframe(analysis, np.zeros(3, dtype=int))


# --- B.2 --------------------------------------------------------------------


def test_los_papeles_salen_de_las_posiciones_que_guardo_el_detector(m1: pd.DataFrame) -> None:
    """Ancla, extremo, constitución y rotura se leen del ID, no del precio."""
    config = base_config("00:00")
    analysis, bars = analysis_of(m1, config)
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))
    short = short_bars(minutes, DAILY)

    audit = audit_timeframe(analysis, minutes)
    roles = audit.roles.set_index("id_num")

    for impulse in analysis.published:
        row = roles.loc[impulse.id_num]
        assert row["ancla_corta"] == bool(short[impulse.index_anchor])
        assert row["extremo_corta"] == bool(short[impulse.index_extreme])
        assert row["constitucion_corta"] == bool(short[impulse.index_constitution])
        assert row["algun_papel"] == bool(
            row[["ancla_corta", "extremo_corta", "constitucion_corta", "rotura_corta"]].any()
        )


def test_un_id_todavia_vigente_no_lo_rompe_ninguna_vela(m1: pd.DataFrame) -> None:
    config = base_config("00:00")
    analysis, bars = analysis_of(m1, config)
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    audit = audit_timeframe(analysis, minutes)
    abiertos = [i.id_num for i in analysis.published if i.index_end is None]

    assert not audit.roles.set_index("id_num").loc[abiertos, "rotura_corta"].any()


def test_sin_velas_cortas_no_hay_impacto(m1: pd.DataFrame) -> None:
    config = base_config("22:00")
    analysis, bars = analysis_of(m1, config)
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    audit = audit_timeframe(analysis, minutes)

    assert audit.short_bars == 0
    assert audit.impact["id_con_algun_papel"] == 0
    assert audit.impact["latigazos_con_vela_corta"] == 0


# --- B.3 --------------------------------------------------------------------


def test_la_fila_de_un_corte_trae_las_columnas_pedidas(m1: pd.DataFrame) -> None:
    config = base_config("00:00")
    analysis, bars = analysis_of(m1, config)
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))

    row = cut_metrics("00:00", analysis, minutes)

    assert tuple(row) == CUT_COLUMNS
    assert row["n_velas"] == len(bars)
    assert 0.0 <= row["pct_barras_limbo"] <= 100.0


def test_cambiar_el_corte_cambia_las_velas_y_los_impulsos(m1: pd.DataFrame) -> None:
    """Si el corte no moviera nada, no habría nada que decidir."""
    filas = []
    for corte in ("00:00", "22:00"):
        config = base_config(corte)
        analysis, bars = analysis_of(m1, config)
        minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))
        filas.append(cut_metrics(corte, analysis, minutes))

    assert filas[0]["n_velas"] != filas[1]["n_velas"]
    assert filas[0]["n_velas_cortas"] > filas[1]["n_velas_cortas"]


# --- B.4 --------------------------------------------------------------------


def test_los_cortes_de_tarde_pertenecen_a_la_sesion_del_dia_siguiente() -> None:
    index = pd.DatetimeIndex(
        ["2024-06-09 22:00", "2024-06-10 00:00", "2024-06-10 11:00"], tz="UTC"
    )

    assert [str(date) for date in session_dates(index)] == [
        "2024-06-10",
        "2024-06-10",
        "2024-06-10",
    ]


def test_el_contraste_devuelve_el_ohlc_de_la_sesion_pedida(m1: pd.DataFrame) -> None:
    config = base_config("22:00")
    bars = aggregate(m1, DAILY, config.aggregation).frame
    minutes = minutes_covered(m1, DAILY, config.aggregation, pd.DatetimeIndex(bars.index))
    sesion = str(session_dates(pd.DatetimeIndex(bars.index))[5])

    rows = contrast_rows("22:00", bars, minutes, [sesion, "1999-01-01"])

    assert rows[0]["fecha_sesion"] == sesion
    assert rows[0]["close"] == pytest.approx(float(bars.iloc[5]["close"]), abs=1e-4)
    assert rows[0]["minutos_m1"] == int(minutes[5])
    # Una fecha fuera del histórico se dice, no se rellena con la vela más cercana.
    assert rows[1]["etiqueta_utc"] == "sin vela"


# --- Parte A ----------------------------------------------------------------


def test_con_a1_el_ancla_sale_siempre_de_una_vela_contraria(m1: pd.DataFrame) -> None:
    """R-02: por construcción, A1 no puede anclar en una vela del color del impulso."""
    config = replace(
        base_config("00:00"),
        rules=ImpulseRulesConfig(
            warmup_bars=0,
            anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY,
            seed_mode=SeedMode.S2_FIRST_COUNTER_BAR,
        ),
    )
    analysis, _ = analysis_of(m1, config)

    audit = colour_audit(analysis)

    assert audit["id_publicados"] > 0
    assert audit["ancla_contraria"] == audit["id_publicados"]
    assert audit["ancla_a_favor"] == 0
    assert audit["ancla_doji"] == 0


# --- Cortes indistinguibles --------------------------------------------------


def con_parada_diaria(days: int = 10) -> pd.DataFrame:
    """M1 continuo salvo una parada de una hora al día, de 21:00 a 22:00 UTC.

    Es la parada que el oro tiene de verdad al cierre de Nueva York. Con ella,
    cortar el día a las 21:00 o a las 22:00 reparte exactamente los mismos
    minutos: la frontera se mueve por un hueco donde no hay ni una barra.
    """
    index = pd.date_range("2024-04-01", periods=days * 1440, freq="1min", tz="UTC")
    index = index[index.hour != 21]
    price = 2000.0 + np.arange(len(index)) * 0.01
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price + 0.1,
            "volume": 1.0,
        },
        index=index,
    )


def test_dos_cortes_dentro_de_la_misma_parada_dan_las_mismas_velas() -> None:
    frame = con_parada_diaria()
    bars = {
        corte: aggregate(frame, DAILY, AggregationConfig(d_session_start=corte)).frame
        for corte in ("21:00", "22:00", "00:00")
    }

    grupos = equivalent_cuts(bars)

    assert grupos == [("21:00", "22:00")]
    # Las velas son las mismas pero la etiqueta no: es justo lo que se avisa.
    assert not bars["21:00"].index.equals(bars["22:00"].index)


def test_cortes_que_reparten_minutos_distintos_no_se_agrupan() -> None:
    frame = con_parada_diaria()
    bars = {
        corte: aggregate(frame, DAILY, AggregationConfig(d_session_start=corte)).frame
        for corte in ("00:00", "12:00")
    }

    assert equivalent_cuts(bars) == []
