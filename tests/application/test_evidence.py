"""La evidencia de la sección G, comprobada como cualquier otra cosa.

Un panel de comprobaciones que siempre dice "OK" no vale nada: aquí se verifica
que las comprobaciones detectan de verdad —rompiendo a propósito lo que miran— y
que el día sintético da el mismo resultado en las dos temporalidades.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
)
from chronos.application.structure.evidence import (
    PHASE1_BASELINE,
    SYNTHETIC_STEPS,
    Check,
    collect,
)
from chronos.infrastructure.reporting.impulse_report import render_evidence
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture
def config() -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({DAILY: (DAILY,), H4: (H4, DAILY), H1: (H4,)}),
        rules=ImpulseRulesConfig(warmup_bars=5),
    )


@pytest.fixture
def series(config: ImpulseConfig) -> dict[str, pd.DataFrame]:
    history = make_m1_history(weeks=12)
    return {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.detected
        ).items()
    }


# --- El panel ---------------------------------------------------------------


def test_una_comprobacion_solo_pasa_si_los_dos_valores_coinciden() -> None:
    assert Check("x", "1", "1").ok
    assert not Check("x", "1", "2").ok


def test_el_dia_sintetico_da_lo_mismo_en_las_tres_temporalidades(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    evidencia = collect(config, series)
    grupos = {
        grupo.title: grupo
        for grupo in evidencia.groups
        if grupo.title.startswith("G.1")
    }
    assert len(grupos) == len(SYNTHETIC_STEPS)

    obtenidos = [
        tuple(check.obtained for check in grupo.checks) for grupo in grupos.values()
    ]
    # El detector no mira la temporalidad: los nueve casos tienen que dar
    # exactamente lo mismo con paso diario, de cuatro horas y de una hora.
    assert len(set(obtenidos)) == 1


def test_las_tres_vias_del_lookahead_saltan(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.2"))
    assert len(grupo.checks) == 3
    assert all(check.obtained == "LookaheadError" for check in grupo.checks)


def test_el_modulo_apagado_no_emite_nada(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.3"))
    assert grupo.ok


def test_la_regresion_compara_contra_la_linea_base_de_la_fase(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """Con el histórico sintético la línea base no se cumple, y tiene que verse."""
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.6"))
    assert not grupo.ok, "la fixture no es el histórico real: no puede dar la línea base"
    assert "401" in grupo.checks[0].expected
    assert PHASE1_BASELINE == {"D": 401, "H4": 1914}
    # La base provisional queda archivada en el texto, no comprobada.
    assert "477" in grupo.note


def test_la_independencia_compara_huellas_y_no_solo_recuentos(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.5"))
    assert grupo.ok
    assert all("huella" in check.expected for check in grupo.checks[1:])
    assert grupo.checks[0].expected == "D"


def test_el_determinismo_se_mide_con_dos_ejecuciones(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.4"))
    assert grupo.ok


# --- El panel detecta de verdad ---------------------------------------------


def test_el_dia_sintetico_no_depende_de_la_configuracion_de_la_corrida(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """Es el caso del §4 con sus propios parámetros; el YAML no lo puede tapar."""
    otro = replace(
        config, rules=replace(config.rules, warmup_bars=500, atr_period=3)
    )
    grupos = [g for g in collect(otro, series).groups if g.title.startswith("G.1")]
    assert grupos and all(grupo.ok for grupo in grupos)


def test_una_deteccion_alterada_haria_fallar_el_panel(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """El panel no está escrito para decir OK: compara cadenas y las contrasta."""
    grupo = next(g for g in collect(config, series).groups if g.title.startswith("G.1"))
    falseado = Check(grupo.checks[0].name, grupo.checks[0].expected, "otra cosa")

    assert grupo.checks[0].ok
    assert not falseado.ok


def test_el_informe_de_evidencia_enseña_las_dos_columnas(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    texto = render_evidence(collect(config, series))
    assert "esperado" in texto and "obtenido" in texto
    assert "G.1 Día sintético" in texto
    assert "G.6 Regresión" in texto
    assert "Veredicto de G: " in texto


def test_el_veredicto_nombra_el_bloque_de_la_cabecera(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """La fase 2.0 reutiliza el formato: el veredicto no puede decir "sección G"."""
    texto = render_evidence(collect(config, series), "Z. Evidencia de la fase 2.0")
    assert "Z. Evidencia de la fase 2.0" in texto
    assert "Veredicto de Z: " in texto
    assert "sección G" not in texto
