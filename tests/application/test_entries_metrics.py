"""Las métricas del §5 y del §6: en R, netas y brutas, y nunca sin desglose.

Lo que se fija aquí no es que los números sean buenos —eso no lo juzga el motor—
sino que cada tabla dice lo que promete: que el bruto y el neto van los dos, que
las poblaciones vacías se enseñan en vez de desaparecer, y que las tres
definiciones de rechazo se desglosan sin que ninguna quede adoptada.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chronos.application.entries import metrics
from chronos.domain.entries.enums import DailyContext, RejectionKind, TradeOutcome
from chronos.domain.entries.signal import TARGET_R

R2 = RejectionKind.R2_MECHA_DOMINANTE.value


def frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def trade(
    *,
    outcome: TradeOutcome = TradeOutcome.OBJETIVO,
    net: float | None = None,
    year: int = 2024,
    daily: str = DailyContext.SIN_CONTEXTO.value,
    confirmation: str = "rechazo",
    **extra: object,
) -> dict[str, object]:
    gross = TARGET_R if outcome is TradeOutcome.OBJETIVO else -1.0
    if outcome is TradeOutcome.ABIERTA:
        gross = 0.0
    row: dict[str, object] = {
        "desenlace": outcome.value,
        "bruto_r": gross,
        "neto_r": gross - 0.1 if net is None else net,
        "coste_r": 0.1,
        "anio": year,
        "contexto_diario": daily,
        "confirmacion": confirmation,
        "ts_entrada": pd.Timestamp(f"{year}-06-03 10:00", tz="UTC"),
        "r_usd": 5.0,
        "r_atr": 1.2,
        "r_pct_precio": 0.002,
    }
    row.update(extra)
    return row


# --- El bloque de métricas ---------------------------------------------------


def test_el_bruto_y_el_neto_van_los_dos() -> None:
    """La diferencia entre ellos es el diagnóstico que pide el §6."""
    result = metrics.metrics("x", frame([trade(), trade(outcome=TradeOutcome.STOP)]))

    assert result["expectativa_bruta_r"] == pytest.approx((TARGET_R - 1.0) / 2)
    assert result["expectativa_neta_r"] == pytest.approx((TARGET_R - 1.0) / 2 - 0.1)
    assert result["coste_medio_r"] == pytest.approx(0.1)


def test_las_operaciones_abiertas_no_entran_en_la_expectativa() -> None:
    """El histórico se acabó con ellas vivas: se cuentan aparte, no se suponen."""
    result = metrics.metrics("x", frame([trade(), trade(outcome=TradeOutcome.ABIERTA)]))

    assert result["n"] == 1
    assert result["abiertas"] == 1
    assert result["expectativa_bruta_r"] == pytest.approx(TARGET_R)


def test_el_stop_de_la_misma_barra_cuenta_como_perdida() -> None:
    """§4 — regla intra-barra conservadora: gana el stop."""
    result = metrics.metrics("x", frame([trade(outcome=TradeOutcome.STOP_MISMA_BARRA)]))

    assert result["n"] == 1
    assert result["aciertos"] == 0
    assert result["expectativa_bruta_r"] == pytest.approx(-1.0)


def test_las_rachas_se_cuentan_sobre_operaciones_consecutivas() -> None:
    rows = [
        trade(),
        trade(),
        trade(outcome=TradeOutcome.STOP),
        trade(outcome=TradeOutcome.STOP),
        trade(outcome=TradeOutcome.STOP),
        trade(),
    ]
    result = metrics.metrics("x", frame(rows))

    assert result["racha_ganadora"] == 2
    assert result["racha_perdedora"] == 3


def test_el_intervalo_no_se_inventa_con_una_sola_operacion() -> None:
    result = metrics.metrics("x", frame([trade()]))
    assert np.isnan(result["ic95_bajo_r"])
    assert np.isnan(result["ic95_alto_r"])


def test_el_intervalo_encierra_la_expectativa() -> None:
    rows = [trade() for _ in range(10)] + [
        trade(outcome=TradeOutcome.STOP) for _ in range(30)
    ]
    result = metrics.metrics("x", frame(rows))

    assert result["ic95_bajo_r"] < result["expectativa_neta_r"] < result["ic95_alto_r"]


# --- Los desgloses del §5 ----------------------------------------------------


def test_una_poblacion_vacia_no_desaparece_del_desglose() -> None:
    """Una categoría con cero operaciones es un dato; esconderla la convierte en
    un olvido. Lo que no se enseña es una fila que no existe en los datos."""
    rows = [trade(daily=DailyContext.A_FAVOR.value)]
    table = metrics.breakdown(
        frame(rows), "contexto_diario", tuple(value.value for value in DailyContext)
    )

    assert list(table["poblacion"]) == [DailyContext.A_FAVOR.value, metrics.TOTAL_ROW]


def test_el_total_va_al_final_y_no_sustituye_a_nadie() -> None:
    rows = [trade(year=2023), trade(year=2024, outcome=TradeOutcome.STOP)]
    table = metrics.by_year(frame(rows))

    assert list(table["poblacion"]) == ["2023", "2024", metrics.TOTAL_ROW]
    assert int(table.iloc[-1]["n"]) == 2


def test_los_ocho_desgloses_del_5_estan_todos() -> None:
    rows = [
        trade(
            zona_h4="UL",
            desenlace_zona="respeto",
            entrada_en="H1",
            stop_en="h1",
            direccion="alcista",
        )
    ]
    titles = [title for title, _ in metrics.all_breakdowns(frame(rows))]

    assert [title.split(" ")[0] for title in titles] == [
        "5.1", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8"
    ]


# --- §5.8 y §2: las tres definiciones ----------------------------------------


def test_las_definiciones_de_rechazo_se_solapan_y_no_suman_al_total() -> None:
    """Es a propósito: lo que el §2 pide es ver cuánto se parecen, no repartirlas."""
    rows = [
        trade(**{RejectionKind.R1_MECHA_EN_ZONA.value: True, f"{R2}_p75": True}),
        trade(**{RejectionKind.R3_CIERRE_EN_EXTREMO.value: True, f"{R2}_p75": True}),
    ]
    table = metrics.by_rejection(frame(rows), grid=(75,))
    counts = dict(zip(table["poblacion"], table["n"], strict=True))

    assert counts[RejectionKind.R1_MECHA_EN_ZONA.value] == 1
    assert counts[f"{R2} (P75)"] == 2
    assert counts[RejectionKind.R3_CIERRE_EN_EXTREMO.value] == 1
    assert counts[metrics.TOTAL_ROW] == 2


def test_las_confirmadas_sin_rechazo_se_cuentan_aparte() -> None:
    rows = [
        trade(confirmation="id_h1"),
        trade(confirmation="rechazo", **{RejectionKind.R1_MECHA_EN_ZONA.value: True}),
    ]
    table = metrics.by_rejection(frame(rows), grid=(75,))
    counts = dict(zip(table["poblacion"], table["n"], strict=True))

    assert counts["confirmadas SIN rechazo (ID u OB de H1)"] == 1


def test_la_matriz_de_solape_es_simetrica_y_la_diagonal_es_el_recuento() -> None:
    rows = [
        trade(
            **{
                RejectionKind.R1_MECHA_EN_ZONA.value: True,
                RejectionKind.R3_CIERRE_EN_EXTREMO.value: True,
                f"{R2}_p75": False,
            }
        ),
        trade(
            **{
                RejectionKind.R1_MECHA_EN_ZONA.value: True,
                RejectionKind.R3_CIERRE_EN_EXTREMO.value: False,
                f"{R2}_p75": False,
            }
        ),
    ]
    matrix = metrics.rejection_overlap(frame(rows), grid=(75,))
    r1 = RejectionKind.R1_MECHA_EN_ZONA.value
    r3 = RejectionKind.R3_CIERRE_EN_EXTREMO.value

    assert matrix.loc[r1, r1] == 2
    assert matrix.loc[r3, r3] == 1
    assert matrix.loc[r1, r3] == matrix.loc[r3, r1] == 1


# --- §3: la distribución del 1R ----------------------------------------------


def test_el_1r_sale_en_las_tres_unidades() -> None:
    table = metrics.risk_distribution(frame([trade(), trade(year=2024)]))

    for unit in ("usd", "atr", "pct_precio"):
        assert f"{unit}_p50" in table.columns
    assert "bajo_una_horquilla" in table.columns


def test_se_cuentan_los_stops_que_la_horquilla_se_come() -> None:
    """El control de sanidad del §3: un stop de un dólar no es operable."""
    table = metrics.risk_distribution(frame([trade(r_usd=0.05), trade(r_usd=12.0)]))

    assert int(table.iloc[-1]["bajo_una_horquilla"]) == 1
    assert float(table.iloc[-1]["usd_minimo"]) == pytest.approx(0.05)


# --- §6: frecuencia ----------------------------------------------------------


def test_las_semanas_vacias_se_cuentan_sobre_el_calendario_completo() -> None:
    """Sobre las semanas con operaciones saldría cero por construcción."""
    rows = [
        trade(**{"ts_entrada": pd.Timestamp("2024-01-03 10:00", tz="UTC")}),
        trade(**{"ts_entrada": pd.Timestamp("2024-03-06 10:00", tz="UTC")}),
    ]
    table = metrics.frequency(
        frame(rows),
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2024-03-31", tz="UTC"),
    )
    total = table.iloc[-1]

    assert int(total["operaciones"]) == 2
    assert int(total["semanas"]) > 10
    assert int(total["semanas_vacias"]) == int(total["semanas"]) - 2


def test_el_punto_de_equilibrio_es_el_del_enunciado() -> None:
    assert "23.3%" in metrics.break_even_note().replace(",", ".")
