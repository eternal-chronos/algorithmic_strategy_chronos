"""La rotura por zona sobre una corrida completa (fase 2.1).

Lo que más se comprueba aquí no es lo que la regla nueva produce sino lo que **no**
toca cuando está apagada: con `break_by_zone: false` la fase 1 tiene que salir
exactamente como estaba, hash incluido. Lo demás son las invariantes que el §3.2
señala como el sitio donde es más fácil meter un bug silencioso.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from chronos.application.structure import break_comparison
from chronos.application.structure.config import (
    AggregationConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import (
    AUDIT_COLUMNS,
    TABLE_COLUMNS,
    DetectDominantImpulses,
    ImpulseRun,
)
from chronos.application.structure.evidence import BASELINE_HASH
from chronos.application.structure.zones import detect_zones
from chronos.domain.structure.enums import BreakKind, BreakLevelSource, OverlapPriority
from chronos.infrastructure.reporting.break_report import render_break_report
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

CONFIG = Path("config/impulse.yaml")


def _config(*, break_by_zone: bool, priority: OverlapPriority | None = None) -> ImpulseConfig:
    rules = ImpulseRulesConfig(warmup_bars=5, break_by_zone=break_by_zone)
    if priority is not None:
        rules = replace(rules, overlap_priority=priority)
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=rules,
        zones=ZonesConfig(enabled=True),
    )


@pytest.fixture(scope="module")
def series() -> dict[str, pd.DataFrame]:
    history = make_m1_history(weeks=16)
    config = _config(break_by_zone=False)
    return {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }


@pytest.fixture(scope="module")
def baseline(series: dict[str, pd.DataFrame]) -> ImpulseRun:
    return DetectDominantImpulses(_config(break_by_zone=False)).execute(series)


@pytest.fixture(scope="module")
def zoned(series: dict[str, pd.DataFrame]) -> ImpulseRun:
    return DetectDominantImpulses(_config(break_by_zone=True)).execute(series)


# --- El interruptor ----------------------------------------------------------


def test_apagada_la_corrida_es_la_de_la_fase_1(baseline: ImpulseRun) -> None:
    """Ni una zona consultada, ni una extensión, ni un contador movido."""
    for analysis in baseline.analyses.values():
        assert analysis.avoided == ()
        assert all(impulse.extreme_extensions == 0 for impulse in analysis.impulses)
        assert all(
            impulse.exit_level_source in (None, BreakLevelSource.LINE)
            for impulse in analysis.impulses
        )
        for key, value in analysis.diagnostics.items():
            if key.startswith(("roturas_evitadas", "extremos_extendidos")) or key.endswith(
                ("_por_zona", "_por_linea")
            ):
                assert value == 0, key


def test_el_hash_de_la_linea_base_no_se_mueve_con_el_parametro_apagado() -> None:
    """`break_by_zone: false` se omite del `fingerprint` justamente para esto."""
    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    from chronos.infrastructure.config.loader import load_impulse_config

    config = load_impulse_config(CONFIG)
    assert config.rules.break_by_zone is False
    assert config.fingerprint() == BASELINE_HASH
    # Y cambiarlo produce un hash distinto: ninguna salida de la regla nueva
    # puede confundirse con la de la vieja.
    turned_on = replace(config, rules=replace(config.rules, break_by_zone=True))
    assert turned_on.fingerprint() != BASELINE_HASH


def test_el_orden_de_solape_solo_entra_en_el_hash_con_la_regla_encendida() -> None:
    off = _config(break_by_zone=False)
    other_off = _config(break_by_zone=False, priority=OverlapPriority.EN_CONTRA_FIRST)
    assert off.fingerprint() == other_off.fingerprint()

    on = _config(break_by_zone=True)
    other_on = _config(break_by_zone=True, priority=OverlapPriority.EN_CONTRA_FIRST)
    assert on.fingerprint() != other_on.fingerprint()


# --- Lo que cambia -----------------------------------------------------------


def test_la_regla_nueva_no_puede_producir_mas_impulsos(
    baseline: ImpulseRun, zoned: ImpulseRun
) -> None:
    """Salvar roturas sólo puede quitar impulsos, nunca añadirlos.

    Cada ID nace de una rotura anterior; si hay menos roturas hay menos ID. No es
    una identidad exacta —el limbo y la constitución siguen su propio ritmo— pero
    el sentido de la desigualdad sí está garantizado.
    """
    for timeframe, analysis in zoned.analyses.items():
        assert len(analysis.impulses) <= len(baseline.analyses[timeframe].impulses)


def test_toda_rotura_a_favor_es_por_zona(zoned: ImpulseRun) -> None:
    """El UL existe siempre: en el lado a favor nunca falta la zona."""
    for analysis in zoned.analyses.values():
        assert all(
            event.level_source is BreakLevelSource.LAST
            for event in analysis.events
            if event.kind is BreakKind.A_FAVOR
        )


def test_romper_por_linea_solo_ocurre_sin_ob_confirmado(zoned: ImpulseRun) -> None:
    """§6.7 — la única forma de morir por línea con la regla nueva."""
    zones = detect_zones(zoned)
    for timeframe, analysis in zoned.analyses.items():
        without_ob = {
            item.id_num
            for item in zones.per_timeframe[timeframe].without_order_block
        }
        for event in analysis.events:
            if event.level_source is not BreakLevelSource.LINE:
                continue
            assert event.kind is BreakKind.EN_CONTRA
            assert event.broken_id_num in without_ob


def test_el_ancla_nunca_se_mueve_y_el_extremo_solo_mejora(zoned: ImpulseRun) -> None:
    """§3.2 — el OB lo fija la vela del ancla y esa vela no cambia."""
    for analysis in zoned.analyses.values():
        for impulse in analysis.impulses:
            if impulse.extreme_extensions == 0:
                assert impulse.extreme == impulse.extreme_at_constitution
                continue
            # Extenderse es avanzar en la dirección del impulso, nunca retroceder.
            assert impulse.range_usd > 0
            reach = impulse.extreme - impulse.extreme_at_constitution
            assert (reach > 0) is (impulse.direction.value == "alcista")
            # El ancla no tiene equivalente: sigue en la vela del arranque.
            assert impulse.index_anchor <= impulse.index_leg_start


def test_cada_extension_tiene_su_rotura_evitada(zoned: ImpulseRun) -> None:
    """El extremo sólo se mueve al salvarse una rotura a favor, y se anota."""
    for analysis in zoned.analyses.values():
        extensions = sum(impulse.extreme_extensions for impulse in analysis.impulses)
        extended = sum(1 for item in analysis.avoided if item.extended_extreme)
        assert extensions == extended
        assert extended == analysis.diagnostics["extremos_extendidos"]
        assert extended == analysis.diagnostics["roturas_evitadas_a_favor"]


def test_la_rotura_evitada_queda_dentro_de_su_zona(zoned: ImpulseRun) -> None:
    """Cerró más allá de la línea y sin llegar al borde exterior. Las dos cosas."""
    for analysis in zoned.analyses.values():
        for item in analysis.avoided:
            low, high = sorted((item.zone_inner, item.zone_outer))
            assert low <= item.close <= high
            assert item.line != item.zone_outer  # una zona plana no salva a nadie


def test_el_extremo_extendido_coincide_con_su_vela(zoned: ImpulseRun) -> None:
    """El UL se recalcula sobre la vela nueva: el nivel tiene que ser su cuerpo."""
    for analysis in zoned.analyses.values():
        bars = analysis.bars
        for impulse in analysis.impulses:
            if impulse.extreme_extensions == 0:
                continue
            bar = bars.iloc[impulse.index_extreme]
            body = (
                max(float(bar["open"]), float(bar["close"]))
                if impulse.direction.value == "alcista"
                else min(float(bar["open"]), float(bar["close"]))
            )
            assert impulse.extreme == pytest.approx(body)


# --- La tabla y el informe ---------------------------------------------------


def test_la_tabla_lleva_las_columnas_de_la_fase(zoned: ImpulseRun) -> None:
    table = zoned.table()
    assert list(table.columns) == [*TABLE_COLUMNS, *AUDIT_COLUMNS]
    for column in (
        "origen_nivel_rotura",
        "extensiones_extremo",
        "precio_extremo_al_constituirse",
    ):
        assert column in table.columns


def test_las_tablas_de_roturas_salen_completas(zoned: ImpulseRun) -> None:
    avoided = break_comparison.avoided_table(zoned)
    breaks = break_comparison.break_table(zoned)
    assert list(avoided.columns) == list(break_comparison.AVOIDED_COLUMNS)
    assert list(breaks.columns) == list(break_comparison.BREAK_COLUMNS)
    assert len(avoided) == sum(
        len(analysis.avoided) for analysis in zoned.analyses.values()
    )
    assert len(breaks) == sum(
        len(analysis.events) for analysis in zoned.analyses.values()
    )


def test_el_informe_se_renderiza_entero(
    baseline: ImpulseRun, zoned: ImpulseRun, series: dict[str, pd.DataFrame]
) -> None:
    alternative = DetectDominantImpulses(
        _config(break_by_zone=True, priority=OverlapPriority.EN_CONTRA_FIRST)
    ).execute(series)
    comparison = break_comparison.compare(
        baseline,
        zoned,
        baseline_zones=detect_zones(baseline),
        zoned_zones=detect_zones(zoned),
        alternative=alternative,
    )
    text = render_break_report(
        baseline,
        zoned,
        comparison,
        regression_ok=True,
        regression_note="fixture sintética",
    )
    for heading in (
        "6.1 · Censo de impulsos",
        "6.5 · LOS ENANOS · PRIORITARIA",
        "6.6 · LATIGAZO · PRIORITARIA",
        "6.8 · ZONAS SOLAPADAS · PRIORITARIA",
        "6.11 · Contraste con la estimación de la fase 2.0",
        "6.8 bis · Efecto real de OVERLAP_PRIORITY",
    ):
        assert heading in text
    assert comparison.baseline_hash != comparison.zoned_hash


def test_las_zonas_de_la_fase_20_siguen_saliendo_con_la_regla_nueva(
    zoned: ImpulseRun,
) -> None:
    """El UL es el de la constitución, no el del extremo con el que el ID murió."""
    zones = detect_zones(zoned)
    assert zones.enabled
    for timeframe, item in zones.per_timeframe.items():
        published = {impulse.id_num for impulse in zoned.analyses[timeframe].published}
        assert {zoned_item.id_num for zoned_item in item.items} == published
        # El UL no se remarca: su borde interior es la línea que el ID tenía al
        # nacer, y en los ID que estiraron el extremo ésa ya no es la final.
        for measured in item.items:
            impulse = next(
                one
                for one in zoned.analyses[timeframe].published
                if one.id_num == measured.id_num
            )
            assert measured.last.inner == pytest.approx(impulse.extreme_at_constitution)
            assert measured.last.index_defining == impulse.index_extreme_at_constitution
