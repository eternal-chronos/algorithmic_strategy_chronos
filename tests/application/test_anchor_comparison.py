"""Cierre de R-02: A1 frente a A2 ejecutando el módulo dos veces (sección B).

Lo que hay que fijar aquí es qué significa "cambia el momento de la rotura", que
es la cifra con la que el propietario decide. Se comprueba con dos series
construidas a mano: una donde las dos anclas caen en el mismo precio y otra
donde el hueco las separa lo bastante como para mover una ROTURA_EN_CONTRA.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from chronos.application.structure.anchor_comparison import compare_anchor_modes
from chronos.application.structure.config import (
    H4,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
)
from chronos.domain.structure.enums import AnchorMode, SeedMode
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

STEP = pd.Timedelta(hours=4)
START = pd.Timestamp("2024-03-08 00:00", tz="UTC")


@pytest.fixture
def config() -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({H4: (H4,)}),
        # S1 para que la primera vela abra ya la pierna: las series de este
        # fichero son de siete velas y con S2 el arranque se comería la mitad.
        rules=ImpulseRulesConfig(warmup_bars=0, seed_mode=SeedMode.S1_FIRST_NON_DOJI),
    )


def frame(bodies: list[tuple[float, float]]) -> pd.DataFrame:
    """Velas H4 sin mechas: el módulo 1 sólo mira el cuerpo."""
    index = pd.DatetimeIndex([START + STEP * position for position in range(len(bodies))])
    return pd.DataFrame(
        {
            "open": [open_ for open_, _ in bodies],
            "high": [max(open_, close) for open_, close in bodies],
            "low": [min(open_, close) for open_, close in bodies],
            "close": [close for _, close in bodies],
            "volume": 1.0,
        },
        index=index,
    )


# --- Series continuas: las dos anclas son el mismo número -------------------


def test_sin_huecos_entre_velas_la_eleccion_es_cosmetica(config: ImpulseConfig) -> None:
    """Si el open de cada vela es el close de la anterior, A1 y A2 coinciden."""
    continua = frame(
        [
            (2000.0, 1990.0),
            (1990.0, 1980.0),
            (1980.0, 1985.0),  # contraria: constituye
            (1985.0, 1975.0),  # rompe a favor
            (1975.0, 1980.0),  # contraria: constituye
            (1980.0, 1990.0),
            (1990.0, 1985.0),
        ]
    )
    comparacion = compare_anchor_modes(config, {H4: continua}).per_timeframe[H4]

    assert comparacion.changed_break == 0
    assert comparacion.unaligned == 0
    assert comparacion.is_cosmetic
    assert comparacion.identical_anchor == comparacion.aligned
    assert "COSMÉTICA" in comparacion.verdict


# --- Con hueco: el ancla se mueve y con ella la rotura ----------------------


def test_un_hueco_puede_mover_el_momento_de_la_rotura(config: ImpulseConfig) -> None:
    """El hueco separa las dos anclas 15 USD y una rotura cae justo en medio.

    La pierna bajista arranca en la vela 3, que abre con hueco en 2000: A2 es su
    body high (2000) y A1 el de la contraria anterior (1985). La vela 6 cierra
    en 1995: más allá del ancla A1 y dentro del rango con la A2. Con A1 el ID
    muere ahí; con A2 sigue vivo y muere una vela después, por el otro extremo.
    """
    con_hueco = frame(
        [
            (2000.0, 1990.0),
            (1990.0, 1980.0),
            (1980.0, 1985.0),  # contraria: de su body high sale A1 = 1985
            (2000.0, 1975.0),  # HUECO al abrir: la pierna arranca aquí, A2 = 2000
            (1975.0, 1970.0),
            (1970.0, 1990.0),  # contraria: constituye con extremo 1970
            (1990.0, 1995.0),  # más allá de 1985 (A1), dentro de 2000 (A2)
            (1995.0, 1960.0),
        ]
    )
    comparacion = compare_anchor_modes(config, {H4: con_hueco}).per_timeframe[H4]

    assert not comparacion.is_cosmetic
    assert comparacion.changed_break == 1
    assert comparacion.impulses_a1 != comparacion.impulses_a2
    assert comparacion.first_divergence is not None
    assert "MATERIAL" in comparacion.verdict
    assert comparacion.gap_by_year["max_usd"].max() == pytest.approx(15.0)


def test_las_dos_corridas_son_del_modulo_entero(config: ImpulseConfig) -> None:
    """El recuento de impulsos puede cambiar, no sólo su ancla."""
    history = make_m1_history(weeks=10)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), (H4,)
        ).items()
    }
    comparacion = compare_anchor_modes(config, series).per_timeframe[H4]

    assert comparacion.impulses_a1 > 0
    assert comparacion.impulses_a2 > 0
    assert comparacion.aligned <= min(comparacion.impulses_a1, comparacion.impulses_a2)


# --- La tabla por año -------------------------------------------------------


def test_la_diferencia_se_reporta_en_las_tres_unidades(config: ImpulseConfig) -> None:
    history = make_m1_history(weeks=10)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), (H4,)
        ).items()
    }
    tabla = compare_anchor_modes(config, series).per_timeframe[H4].gap_by_year

    assert {"mediana_usd", "p90_usd", "max_usd", "max_pct_precio", "max_atr"} <= set(
        tabla.columns
    )
    total = tabla[tabla["anio"] == "TOTAL"].iloc[0]
    assert total["max_usd"] >= total["p90_usd"] >= total["mediana_usd"]
    assert int(total["difieren"]) <= int(total["n"])


def test_el_modo_configurado_no_altera_la_comparacion(config: ImpulseConfig) -> None:
    """Se corren siempre los dos modos: da igual cuál esté puesto en el YAML."""
    history = make_m1_history(weeks=10)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), (H4,)
        ).items()
    }
    con_a1 = replace(
        config, rules=replace(config.rules, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    )
    izquierda = compare_anchor_modes(config, series).per_timeframe[H4]
    derecha = compare_anchor_modes(con_a1, series).per_timeframe[H4]

    assert izquierda.impulses_a1 == derecha.impulses_a1
    assert izquierda.impulses_a2 == derecha.impulses_a2
    assert izquierda.changed_break == derecha.changed_break
