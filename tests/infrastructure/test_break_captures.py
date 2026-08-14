"""Capturas de la fase 2.1 (§7).

Lo caro de estas imágenes es renderizarlas, así que aquí se comprueba sobre todo
lo que se decide *antes* de dibujar: qué lotes se piden, con qué ID y con qué
texto sobreimpreso. Sólo una prueba escribe PNG de verdad, y va marcada como
lenta.

Lo que más importa fijar son dos cosas del §7: que la capa de roturas evitadas
existe y no tapa el gráfico cuando hay veinte, y que el `LEEME` dice cuándo un
lote sale vacío en vez de callarlo.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chronos.application.structure.config import (
    DAILY,
    H4,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.domain.structure.enums import BreakLevelSource, OverlapPriority
from chronos.infrastructure.reporting.break_captures import (
    BY_LINE,
    LONGEST,
    MAX_AVOIDED_ANNOTATIONS,
    SURVIVORS,
    _annotated,
    _bars_under_old_rule,
    _lots,
    write_break_captures,
)
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


def _config(*, break_by_zone: bool, priority: OverlapPriority | None = None) -> ImpulseConfig:
    rules = ImpulseRulesConfig(warmup_bars=5, break_by_zone=break_by_zone)
    if priority is not None:
        rules = replace(rules, overlap_priority=priority)
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({DAILY: (DAILY,), H4: (H4, DAILY)}),
        rules=rules,
        zones=ZonesConfig(enabled=True),
    )


@pytest.fixture(scope="module")
def runs() -> tuple[ImpulseRun, ImpulseRun, ImpulseRun]:
    history = make_m1_history(weeks=24)
    config = _config(break_by_zone=False)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.detected
        ).items()
    }
    return (
        DetectDominantImpulses(config).execute(series),
        DetectDominantImpulses(_config(break_by_zone=True)).execute(series),
        DetectDominantImpulses(
            _config(break_by_zone=True, priority=OverlapPriority.EN_CONTRA_FIRST)
        ).execute(series),
    )


# --- Qué se pide dibujar ----------------------------------------------------


def test_los_cuatro_lotes_por_id_del_enunciado(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun],
) -> None:
    _, zoned, _ = runs
    lots = dict(_lots(zoned))
    assert list(lots) == [
        "sobrevive",
        "mas_larga",
        "rotura_por_linea",
        "conflicto_solape",
    ]
    assert len(lots["sobrevive"]) <= SURVIVORS
    assert len(lots["mas_larga"]) <= LONGEST
    assert len(lots["rotura_por_linea"]) <= BY_LINE


def test_los_supervivientes_tienen_roturas_evitadas(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun],
) -> None:
    _, zoned, _ = runs
    lots = dict(_lots(zoned))
    for timeframe, impulse in lots["sobrevive"]:
        avoided = [
            item
            for item in zoned.analyses[timeframe].avoided
            if item.id_num == impulse.id_num
        ]
        assert avoided


def test_las_roturas_por_linea_son_lo_que_dicen(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun],
) -> None:
    """El lote existe para enseñar el único caso que muere sin zona."""
    _, zoned, _ = runs
    for _timeframe, impulse in dict(_lots(zoned))["rotura_por_linea"]:
        assert impulse.exit_level_source is BreakLevelSource.LINE


def test_sin_conflictos_el_lote_de_solape_sale_vacio(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun],
) -> None:
    _, zoned, _ = runs
    conflicts = sum(
        analysis.diagnostics["conflictos_de_solape"]
        for analysis in zoned.analyses.values()
    )
    if conflicts:
        pytest.skip("la fixture sí produce conflictos: el lote no está vacío")
    assert dict(_lots(zoned))["conflicto_solape"] == []


# --- La capa de roturas evitadas --------------------------------------------


def test_solo_se_rotulan_la_primera_y_la_ultima() -> None:
    """Veinte cuadros de texto no se leen: tapan las velas que hay que mirar."""
    assert _annotated([object()] * 1) == (0,)  # type: ignore[list-item]
    assert _annotated([object()] * MAX_AVOIDED_ANNOTATIONS) == tuple(
        range(MAX_AVOIDED_ANNOTATIONS)
    )
    assert _annotated([object()] * 12) == (0, 11)  # type: ignore[list-item]


def test_la_duracion_antigua_es_hasta_la_primera_rotura_evitada(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun],
) -> None:
    """Sin roturas evitadas las dos columnas del LEEME tienen que coincidir."""
    _, zoned, _ = runs
    analysis = zoned.analyses[H4]
    last_index = len(analysis.bars) - 1
    for impulse in analysis.published:
        avoided = [item for item in analysis.avoided if item.id_num == impulse.id_num]
        old = _bars_under_old_rule(impulse, avoided, last_index)
        if not avoided:
            assert old == impulse.bars_alive(last_index)
            continue
        # Con roturas evitadas el ID vive al menos hasta la primera de ellas.
        assert old <= impulse.bars_alive(last_index)
        assert old == min(item.index for item in avoided) - impulse.index_constitution


# --- Escribir de verdad ------------------------------------------------------


@pytest.mark.slow
def test_escribe_las_imagenes_y_el_leeme(
    runs: tuple[ImpulseRun, ImpulseRun, ImpulseRun], tmp_path: Path
) -> None:
    baseline, zoned, alternative = runs
    written = write_break_captures(baseline, zoned, alternative, tmp_path)

    assert written
    assert all(capture.path.exists() for capture in written)
    # El §7.5 pide el mismo tramo bajo las dos reglas, y son dos imágenes.
    side = [capture for capture in written if capture.lot == "lado_a_lado"]
    assert len(side) % 2 == 0

    readme = (tmp_path / "LEEME.txt").read_text(encoding="utf-8")
    assert "ROTURA DEL ID POR ZONA" in readme
    for capture in written:
        assert capture.path.name in readme
    conflicts = sum(
        analysis.diagnostics["conflictos_de_solape"]
        for analysis in zoned.analyses.values()
    )
    if conflicts == 0:
        # Un lote vacío se explica, no se calla.
        assert "SOBRE EL LOTE conflicto_solape" in readme
