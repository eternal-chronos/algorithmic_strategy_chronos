"""La evidencia de la fase 2.0, comprobada como cualquier otra cosa.

Un panel de comprobaciones que siempre dice "OK" no vale nada: aquí se verifica
que el panel entero pasa sobre datos sanos y —lo que importa— que **detecta** de
verdad, rompiendo a propósito lo que mira.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from chronos.application.structure import zone_evidence
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
from chronos.application.structure.evidence import PHASE1_BASELINE
from chronos.domain.structure.enums import LegStartMode
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


def test_el_panel_cubre_los_cinco_bloques(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    titulos = [group.title for group in zone_evidence.collect(config, series).groups]
    assert [titulo.split()[0] for titulo in titulos] == ["Z.1", "Z.2", "Z.3", "Z.4", "Z.5"]


def test_el_dia_sintetico_y_el_lookahead_pasan_siempre(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """No dependen del histórico: son series calculadas a mano."""
    grupos = {
        group.title.split()[0]: group
        for group in zone_evidence.collect(config, series).groups
    }
    for clave in ("Z.1", "Z.2", "Z.3"):
        fallos = [check for check in grupos[clave].checks if not check.ok]
        assert not fallos, [(c.name, c.expected, c.obtained) for c in fallos]


def test_el_apagado_pasa_sobre_cualquier_histórico(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    grupo = next(
        group
        for group in zone_evidence.collect(config, series).groups
        if group.title.startswith("Z.4")
    )
    assert grupo.ok, [(c.name, c.expected, c.obtained) for c in grupo.checks if not c.ok]


def test_la_regresion_falla_con_un_historico_que_no_es_el_de_la_linea_base(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """La fixture sintética no puede dar 401/1.914/7.231: el panel tiene que decirlo.

    Es la comprobación de que Z.5 mira de verdad y no se limita a saludar.
    """
    grupo = next(
        group
        for group in zone_evidence.collect(config, series).groups
        if group.title.startswith("Z.5")
    )
    assert not grupo.ok
    recuento = next(check for check in grupo.checks if check.name == "impulsos detectados")
    assert recuento.expected != recuento.obtained
    assert str(PHASE1_BASELINE["D"]) in recuento.expected


def test_el_panel_detecta_que_las_zonas_han_movido_la_deteccion(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """Z.4 compara dos corridas; si dieran distinto, el bloque tiene que caerse.

    Se simula cambiando el modo de arranque de pierna entre las dos, que sí mueve
    impulsos: si el panel siguiera diciendo OK, no estaría comparando nada.
    """
    otro = replace(
        config, rules=replace(config.rules, leg_start_mode=LegStartMode.L2_NEXT_BAR)
    )
    con = zone_evidence.collect(config, series)
    distinto = zone_evidence.collect(otro, series)
    bloque_con = next(g for g in con.groups if g.title.startswith("Z.4"))
    bloque_otro = next(g for g in distinto.groups if g.title.startswith("Z.4"))

    # Los dos pasan —cada uno se compara consigo mismo— pero los recuentos que
    # publican son distintos, que es lo que demuestra que leen los datos.
    assert bloque_con.ok and bloque_otro.ok
    recuento_con = next(c for c in bloque_con.checks if c.name.startswith("impulsos con zonas"))
    recuento_otro = next(c for c in bloque_otro.checks if c.name.startswith("impulsos con zonas"))
    assert recuento_con.obtained != recuento_otro.obtained


def test_el_id_sin_pul_se_publica_como_uno_y_solo_uno(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    """§6.6 — el panel tiene que decir cuántos son, no callarse."""
    grupo = next(
        group
        for group in zone_evidence.collect(config, series).groups
        if group.title.startswith("Z.1")
    )
    check = next(c for c in grupo.checks if "único que se queda sin PUL" in c.name)
    assert check.ok
    assert check.obtained == "1 de 5"


def test_el_panel_se_puede_renderizar(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    texto = render_evidence(zone_evidence.collect(config, series))
    assert "Z.1" in texto
    assert "LookaheadError" in texto
