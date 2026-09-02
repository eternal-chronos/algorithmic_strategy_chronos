"""Zonas UL y PUL sobre una corrida completa (fase 2.0).

Lo que más se comprueba aquí no es lo que las zonas producen sino lo que **no**
tocan: con el interruptor apagado la fase 1 tiene que salir byte a byte como
antes, y con él encendido el número de impulsos no puede moverse ni en uno. Las
zonas no deciden nada hasta la fase 2.1.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from chronos.application.structure import zones as zone_tables
from chronos.application.structure.config import (
    AggregationConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.evidence import BASELINE_HASH, PHASE1_BASELINE
from chronos.application.structure.zones import (
    ZONE_AUDIT_COLUMNS,
    ZONE_COLUMNS,
    ZonesRun,
    detect_zones,
)
from chronos.domain.structure.enums import BreakKind
from chronos.domain.structure.zones import ZoneKind
from chronos.infrastructure.config.loader import load_impulse_config
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

H4 = "H4"
CONFIG = Path("config/impulse.yaml")


def _series(config: ImpulseConfig) -> dict[str, pd.DataFrame]:
    history = make_m1_history(weeks=16)
    return {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }


def _config(*, zones: bool) -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
        zones=ZonesConfig(enabled=zones),
    )


@pytest.fixture(scope="module")
def run() -> ImpulseRun:
    config = _config(zones=True)
    return DetectDominantImpulses(config).execute(_series(config))


@pytest.fixture(scope="module")
def measured(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


# --- El interruptor ----------------------------------------------------------


def test_apagadas_no_se_emite_ni_una_zona() -> None:
    config = _config(zones=False)
    run = DetectDominantImpulses(config).execute(_series(config))
    zonas = detect_zones(run)

    assert zonas.emits_nothing
    assert zonas.per_timeframe == {}
    assert zonas.table().empty
    assert zonas.survivals().empty


def test_apagadas_la_fase_1_sale_exactamente_igual() -> None:
    """Byte a byte: tabla de impulsos, eventos y estado barra a barra."""
    encendida = _config(zones=True)
    apagada = _config(zones=False)
    con = DetectDominantImpulses(encendida).execute(_series(encendida))
    sin = DetectDominantImpulses(apagada).execute(_series(apagada))

    assert con.config_hash == sin.config_hash
    pd.testing.assert_frame_equal(con.table(), sin.table())
    for timeframe, analysis in con.analyses.items():
        otro = sin.analyses[timeframe]
        assert analysis.impulses == otro.impulses
        assert analysis.events == otro.events
        assert analysis.states == otro.states
        assert analysis.diagnostics == otro.diagnostics


def test_encenderlas_no_mueve_ni_un_impulso(run: ImpulseRun, measured: ZonesRun) -> None:
    """§4: el número de impulsos tiene que ser idéntico en los dos casos."""
    apagada = _config(zones=False)
    sin = DetectDominantImpulses(apagada).execute(_series(apagada))

    assert measured.enabled
    assert {tf: len(a.impulses) for tf, a in run.analyses.items()} == {
        tf: len(a.impulses) for tf, a in sin.analyses.items()
    }


def test_el_hash_no_cambia_al_encender_las_zonas() -> None:
    """Las zonas no mueven una vela ni un impulso, así que no entran en el hash.

    Es lo que mantiene comparables las corridas archivadas de la fase 1 con las
    de ahora, y lo que hace que la línea base se conserve con las zonas puestas.
    """
    assert _config(zones=True).fingerprint() == _config(zones=False).fingerprint()


def test_con_el_modulo_1_apagado_tampoco_hay_zonas() -> None:
    config = replace(_config(zones=True), enabled=False)
    run = DetectDominantImpulses(config).execute({})

    assert detect_zones(run).emits_nothing


# --- La tabla ----------------------------------------------------------------


def test_la_tabla_lleva_las_columnas_del_enunciado(measured: ZonesRun) -> None:
    table = measured.table()
    assert list(table.columns) == [*ZONE_COLUMNS, *ZONE_AUDIT_COLUMNS]
    assert not table.empty


def test_cada_id_publicado_tiene_su_ul(run: ImpulseRun, measured: ZonesRun) -> None:
    """El UL lo fija la misma vela que fija el extremo: no puede faltar ninguno."""
    for timeframe, analysis in run.analyses.items():
        item = measured.per_timeframe[timeframe]
        uls = item.table[item.table["tipo"] == ZoneKind.LAST.value]
        assert len(uls) == len(analysis.published)
        assert set(uls["id_num"]) == {impulse.id_num for impulse in analysis.published}


def test_los_id_de_calentamiento_no_llevan_zonas(
    run: ImpulseRun, measured: ZonesRun
) -> None:
    """No salen en ninguna tabla de la fase 1: sus zonas no tendrían con qué compararse."""
    for timeframe, analysis in run.analyses.items():
        sin_publicar = {
            impulse.id_num for impulse in analysis.impulses if not impulse.publishable
        }
        publicados = set(measured.per_timeframe[timeframe].table["id_num"])
        assert not (sin_publicar & publicados)


def test_cada_id_lleva_una_sola_zona_en_contra(measured: ZonesRun) -> None:
    """PUL o APUL, nunca las dos, y ninguna sólo al principio del histórico."""
    item = measured.per_timeframe[H4]
    puls = item.table[item.table["tipo"] == ZoneKind.PENULTIMATE.value]
    apuls = item.table[item.table["tipo"] == ZoneKind.ANTE_PENULTIMATE.value]

    assert len(puls) == len(item.with_penultimate)
    assert set(puls["id_num"]) == {zoned.id_num for zoned in item.with_penultimate}
    assert len(apuls) == len(item.with_ante_penultimate)
    assert set(apuls["id_num"]) == {
        zoned.id_num for zoned in item.with_ante_penultimate
    }
    assert not (set(puls["id_num"]) & set(apuls["id_num"]))
    # El APUL no es una rareza: es la continuación de tendencia.
    assert item.with_ante_penultimate
    # Sin ningún ID contrario detrás sólo se quedan los primeros del histórico.
    assert len(item.without_against_zone) <= 1


def test_las_tres_unidades_de_altura_estan_siempre(measured: ZonesRun) -> None:
    """El oro pasó de ~1.200 a ~4.300 USD: los dólares solos no comparan nada."""
    table = measured.table()
    assert (table["altura_usd"] >= 0).all()
    assert table["altura_pct_precio"].notna().all()
    # El ATR falta sólo donde la fase 1 tampoco lo tenía (calentamiento del ATR).
    assert table["altura_atr"].notna().any()


def test_la_altura_es_la_distancia_entre_los_dos_bordes(measured: ZonesRun) -> None:
    table = measured.table()
    esperado = (table["borde_exterior"] - table["borde_interior"]).abs()
    pd.testing.assert_series_equal(
        table["altura_usd"], esperado, check_names=False, rtol=1e-12
    )


def test_solo_el_ul_declara_si_se_extendio(measured: ZonesRun) -> None:
    """En las otras la columna va a `None`: decir "no se extendió" sugeriría que podía."""
    table = measured.table()
    uls = table[table["tipo"] == ZoneKind.LAST.value]
    resto = table[table["tipo"] != ZoneKind.LAST.value]

    assert uls["extendida_a_vela_siguiente"].isin([True, False]).all()
    assert resto["extendida_a_vela_siguiente"].isna().all()


def test_ninguna_zona_nace_antes_de_su_id(run: ImpulseRun, measured: ZonesRun) -> None:
    """Durante el limbo no existe ninguna zona."""
    for timeframe, analysis in run.analyses.items():
        constituciones = {
            impulse.id_num: impulse.ts_constitution for impulse in analysis.published
        }
        table = measured.per_timeframe[timeframe].table
        for id_num, nacimiento in zip(
            table["id_num"], table["ts_nacimiento_zona"], strict=True
        ):
            assert nacimiento >= constituciones[int(id_num)]


def test_la_zona_en_contra_nace_con_su_id_y_su_vela_queda_detras(
    measured: ZonesRun,
) -> None:
    """No espera a nada: la vela que la define cerró antes de que el ID naciera."""
    table = measured.table()
    contra = table[table["tipo"] != ZoneKind.LAST.value]
    for definitoria, constitucion, nacimiento in zip(
        contra["ts_vela_definitoria"],
        contra["ts_constitucion_id"],
        contra["ts_nacimiento_zona"],
        strict=True,
    ):
        assert nacimiento == constitucion
        assert definitoria < constitucion


def test_la_vela_definitoria_es_la_que_registro_la_fase_1(
    run: ImpulseRun, measured: ZonesRun
) -> None:
    """Las zonas no re-derivan nada: usan las velas que el detector ya registró."""
    analysis = run.analyses[H4]
    por_id = {impulse.id_num: impulse for impulse in analysis.published}
    table = measured.per_timeframe[H4].table
    esperados = {
        ZoneKind.LAST.value: lambda item: item.ts_extreme,
        ZoneKind.PENULTIMATE.value: lambda item: item.ts_penultimate,
        ZoneKind.ANTE_PENULTIMATE.value: lambda item: item.ts_ante_penultimate,
    }

    for id_num, tipo, definitoria in zip(
        table["id_num"], table["tipo"], table["ts_vela_definitoria"], strict=True
    ):
        assert definitoria == esperados[tipo](por_id[int(id_num)])


# --- Anti-lookahead sobre la corrida entera ---------------------------------


def test_el_libro_de_cada_temporalidad_conoce_los_id_sin_zona_en_contra(
    measured: ZonesRun,
) -> None:
    for item in measured.per_timeframe.values():
        assert item.book.without_penultimate == {
            zoned.id_num for zoned in item.without_against_zone
        }


def test_ninguna_zona_se_apoya_en_una_vela_posterior_a_su_nacimiento(
    measured: ZonesRun,
) -> None:
    """La vela de margen del UL incluida: si cerrara después, sería lookahead."""
    for item in measured.per_timeframe.values():
        for zona in item.book.zones:
            assert zona.ts_outer_known <= zona.ts_birth
            assert zona.ts_defining <= zona.ts_birth


# --- §7: las tablas del informe ---------------------------------------------


def test_la_cobertura_dice_que_todos_tienen_ul(measured: ZonesRun) -> None:
    tabla = zone_tables.coverage_by_year(measured.per_timeframe[H4])
    total = tabla[tabla["anio"] == zone_tables.TOTAL_ROW].iloc[0]

    assert total["pct_ul"] == 1.0
    # Los tres estados del lado en contra suman: nunca hay PUL y APUL a la vez.
    assert (
        total["con_pul"] + total["con_apul"] + total["sin_zona"] == total["impulsos"]
    )
    assert total["con_apul"] > 0


def test_la_comparativa_de_alturas_empareja_solo_los_id_con_las_dos_zonas(
    measured: ZonesRun,
) -> None:
    item = measured.per_timeframe[H4]
    tabla = zone_tables.height_comparison(item)
    total = tabla[tabla["anio"] == zone_tables.TOTAL_ROW].iloc[0]

    assert total["pares"] == len(item.with_penultimate)
    assert total["pul_mayor"] + total["empates"] + total["ul_mayor"] == total["pares"]


def test_el_anticipo_de_la_fase_21_reparte_cada_rotura_a_su_zona(
    run: ImpulseRun, measured: ZonesRun
) -> None:
    """A favor se cruza el extremo, y ahí está el UL; en contra, la zona en contra."""
    detalle = measured.per_timeframe[H4].survivals
    a_favor = detalle[detalle["tipo_rotura"] == BreakKind.A_FAVOR.value]
    en_contra = detalle[detalle["tipo_rotura"] == BreakKind.EN_CONTRA.value]

    assert (a_favor["zona"] == ZoneKind.LAST.value).all()
    assert en_contra["zona"].isin(
        [ZoneKind.PENULTIMATE.value, ZoneKind.ANTE_PENULTIMATE.value]
    ).all()
    assert (en_contra["zona"] == ZoneKind.ANTE_PENULTIMATE.value).any()


def test_una_rotura_sin_pul_no_cuenta_como_superviviente(measured: ZonesRun) -> None:
    """Sin PUL el ID se rompe por línea: no es lo mismo que cerrar fuera."""
    detalle = measured.per_timeframe[H4].survivals
    sin_zona = detalle[detalle["sin_zona"]]
    assert not sin_zona["cerro_dentro"].any()


def test_lo_que_cerro_dentro_esta_de_verdad_dentro(measured: ZonesRun) -> None:
    detalle = measured.per_timeframe[H4].survivals
    dentro = detalle[detalle["cerro_dentro"]]
    if dentro.empty:
        pytest.skip("esta fixture no produjo ninguna rotura dentro de zona")

    bajo = dentro[["borde_interior", "borde_exterior"]].min(axis=1)
    alto = dentro[["borde_interior", "borde_exterior"]].max(axis=1)
    assert (dentro["cierre"] >= bajo).all()
    assert (dentro["cierre"] <= alto).all()


def test_el_anticipo_cuadra_con_el_numero_de_roturas(
    run: ImpulseRun, measured: ZonesRun
) -> None:
    resumen = zone_tables.survival_estimate(measured.per_timeframe[H4])
    total = resumen[resumen["tipo_rotura"] == zone_tables.TOTAL_ROW].iloc[0]
    publicados = {impulse.id_num for impulse in run.analyses[H4].published}
    roturas = [
        event for event in run.analyses[H4].events if event.broken_id_num in publicados
    ]

    assert total["roturas"] == len(roturas)
    assert total["con_zona"] + total["sin_zona"] == total["roturas"]


# --- Regresión contra la línea base con las zonas puestas -------------------


@pytest.mark.parametrize("con_zonas", [False, True])
def test_la_linea_base_se_conserva_con_las_zonas(con_zonas: bool) -> None:
    """El recuento exige el histórico M1 real; se salta si no está descargado."""
    from chronos.infrastructure.structure.aggregation import aggregate
    from chronos.infrastructure.structure.loader import load_history

    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    config = load_impulse_config(CONFIG)
    if not Path(config.data.bid_path).exists():
        pytest.skip(f"no está el histórico {config.data.bid_path}")

    config = replace(config, zones=ZonesConfig(enabled=con_zonas))
    history = load_history(config.data, config.structure_side)
    series = {
        timeframe: aggregate(history.frame, timeframe, config.aggregation).frame
        for timeframe in config.charts.detected
    }
    run = DetectDominantImpulses(config).execute(series)

    assert run.config_hash == BASELINE_HASH
    assert {tf: len(a.impulses) for tf, a in run.analyses.items()} == PHASE1_BASELINE
    assert detect_zones(run).enabled is con_zonas
