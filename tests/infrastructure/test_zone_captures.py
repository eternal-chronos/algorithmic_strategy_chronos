"""Capturas de la fase 2.0 (§8).

Lo caro de estas imágenes es renderizarlas, así que aquí se comprueba sobre todo
lo que se decide *antes* de dibujar: qué lotes se piden, con qué zonas y con qué
texto sobreimpreso. Sólo una prueba escribe PNG de verdad, y va marcada como
lenta.

Lo que más importa fijar es la convención del dibujo: el relleno sólido cubre
exactamente la vida de la zona y el contorno atenuado, el tramo en que la vela ya
la definía pero la zona todavía no existía.
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
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.infrastructure.reporting.zone_captures import (
    EXTREME_ORDER_BLOCKS,
    WELL_FORMED,
    _birth_index,
    _figure,
    _lots,
    write_zone_captures,
)
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture(scope="module")
def run() -> ImpulseRun:
    history = make_m1_history(weeks=20)
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({DAILY: (DAILY,), H4: (H4, DAILY)}),
        rules=ImpulseRulesConfig(warmup_bars=5),
        zones=ZonesConfig(enabled=True),
    )
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.detected
        ).items()
    }
    return DetectDominantImpulses(config).execute(series)


@pytest.fixture(scope="module")
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


# --- Qué se pide dibujar ----------------------------------------------------


def test_los_cinco_lotes_del_enunciado(zones: ZonesRun) -> None:
    assert [lot for lot, _ in _lots(zones)] == [
        "bien_formados_alcista",
        "bien_formados_bajista",
        "ul_extendido",
        "sin_zona_en_contra",
        "ul_altura_cero",
        "pul_mas_alto",
        "pul_mas_bajo",
    ]


def test_los_bien_formados_son_de_h4_y_tienen_las_dos_zonas(zones: ZonesRun) -> None:
    lots = dict(_lots(zones))
    for lot in ("bien_formados_alcista", "bien_formados_bajista"):
        picks = lots[lot]
        assert picks, lot
        assert len(picks) <= WELL_FORMED
        assert all(timeframe == H4 for timeframe, _ in picks)
        assert all(zoned.has_penultimate for _, zoned in picks)
        assert all(not zoned.last.is_flat for _, zoned in picks)
    assert all(
        zoned.direction.value == "alcista" for _, zoned in lots["bien_formados_alcista"]
    )
    assert all(
        zoned.direction.value == "bajista" for _, zoned in lots["bien_formados_bajista"]
    )


def test_cada_lote_selecciona_lo_que_promete(zones: ZonesRun) -> None:
    lots = dict(_lots(zones))
    assert all(zoned.last.extended for _, zoned in lots["ul_extendido"])
    assert all(zoned.against is None for _, zoned in lots["sin_zona_en_contra"])
    assert all(zoned.last.is_flat for _, zoned in lots["ul_altura_cero"])


def test_los_ul_extendidos_se_ordenan_en_atr(zones: ZonesRun) -> None:
    """En dólares el diario saldría siempre delante y el lote no enseñaría nada."""
    alturas = [
        zoned.last.height / zoned.atr for _, zoned in dict(_lots(zones))["ul_extendido"]
    ]
    assert alturas == sorted(alturas, reverse=True)


def test_los_pul_extremos_estan_ordenados_por_altura_en_atr(zones: ZonesRun) -> None:
    """En ATR y no en dólares: el oro no vale lo mismo en 2018 que en 2025."""
    lots = dict(_lots(zones))
    alto = [z.penultimate.height / z.atr for _, z in lots["pul_mas_alto"]]  # type: ignore[union-attr]
    bajo = [z.penultimate.height / z.atr for _, z in lots["pul_mas_bajo"]]  # type: ignore[union-attr]

    assert len(alto) == len(bajo) == EXTREME_ORDER_BLOCKS
    assert alto == sorted(alto, reverse=True)
    assert bajo == sorted(bajo)
    assert min(alto) >= max(bajo)


def test_con_las_zonas_apagadas_no_se_escribe_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    apagadas = detect_zones(run, replace(run.config, zones=ZonesConfig(enabled=False)))
    assert write_zone_captures(run, apagadas, tmp_path / "vacio") == []
    assert not (tmp_path / "vacio").exists()


# --- La convención del dibujo -----------------------------------------------


def test_el_relleno_empieza_donde_nace_la_zona(zones: ZonesRun) -> None:
    """Las dos nacen al constituirse el ID, y sus velas quedan por detrás."""
    for item in zones.per_timeframe.values():
        for zoned in item.items:
            assert _birth_index(zoned) == zoned.index_constitution
            block = zoned.penultimate
            if block is None:
                continue
            assert block.index_defining < zoned.index_constitution


def test_la_zona_se_dibuja_en_dos_tramos(run: ImpulseRun, zones: ZonesRun) -> None:
    """Relleno mientras existe, contorno atenuado mientras sólo estaba la vela."""
    item = zones.per_timeframe[H4]
    zoned = next(
        z
        for z in item.items
        if z.has_penultimate and z.last.index_defining < z.index_constitution
    )
    figure, _ = _figure(run.analyses[H4], zoned, "UTC")
    rectangulos = [shape for shape in figure.layout.shapes if shape.type == "rect"]

    rellenos = [shape for shape in rectangulos if shape.fillcolor != "rgba(0,0,0,0)"]
    contornos = [shape for shape in rectangulos if shape.fillcolor == "rgba(0,0,0,0)"]
    assert rellenos, "falta el tramo en que la zona existe"
    assert contornos, "falta el tramo anterior al nacimiento"
    assert all(shape.line.dash == "dot" for shape in contornos)


def test_la_ventana_incluye_las_velas_que_definen_las_zonas(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """Si no, la captura enseñaría un rectángulo que empieza fuera del encuadre."""
    analysis = run.analyses[H4]
    for zoned in zones.per_timeframe[H4].items[:20]:
        figure, _ = _figure(analysis, zoned, "UTC")
        etiquetas = set(figure.data[0].x)
        for zone in zoned.zones():
            assert (
                analysis.bars.index[zone.index_defining].strftime("%Y-%m-%d %H:%M")
                in etiquetas
            )


def test_el_subtitulo_lleva_las_dos_alturas_y_las_dos_horas(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    zoned = zones.per_timeframe[H4].with_penultimate[0]
    figure, _ = _figure(run.analyses[H4], zoned, "Europe/Athens")
    texto = figure.layout.title.text

    assert "UL " in texto and "ATR" in texto
    assert "PUL " in texto
    assert "UTC" in texto and "Europe/Athens" in texto


def test_un_id_sin_zona_en_contra_lo_dice_en_la_nota(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    sin_zona = zones.per_timeframe[H4].without_against_zone
    if not sin_zona:
        pytest.skip("esta fixture no produjo ningún ID sin zona en contra")
    _, nota = _figure(run.analyses[H4], sin_zona[0], "UTC")
    assert "ni PUL ni APUL" in nota


def test_un_id_con_apul_lo_dice_en_la_nota(run: ImpulseRun, zones: ZonesRun) -> None:
    """El APUL no es un PUL: la captura tiene que decir cuál está viendo."""
    con_apul = zones.per_timeframe[H4].with_ante_penultimate
    assert con_apul, "sin APUL en la fixture no hay nada que auditar"
    _, nota = _figure(run.analyses[H4], con_apul[0], "UTC")
    assert "APUL" in nota


def test_un_id_sin_zona_en_contra_dibuja_solo_su_ul(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """Sin ID contrario detrás no hay zona que dibujar, y no se inventa una."""
    analysis = run.analyses[H4]
    sin_zona = zones.per_timeframe[H4].without_against_zone
    if not sin_zona:
        pytest.skip("esta fixture no produjo ningún ID sin zona en contra")

    for zoned in sin_zona[:10]:
        figure, nota = _figure(analysis, zoned, "UTC")
        assert "ni PUL ni APUL" in nota
        rectangulos = [shape for shape in figure.layout.shapes if shape.type == "rect"]
        # Sólo los del UL: el tramo atenuado y el relleno.
        assert len(rectangulos) <= 2


# --- Escritura real ---------------------------------------------------------


@pytest.mark.slow
def test_se_escriben_los_png_y_el_leeme(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    escritas = write_zone_captures(run, zones, tmp_path, session_timezone="Europe/Athens")
    assert escritas
    assert all(capture.path.exists() for capture in escritas)

    leeme = (tmp_path / "LEEME.txt").read_text(encoding="utf-8")
    for capture in escritas:
        assert capture.path.name in leeme
        assert f"ID {capture.id_num}" in leeme
    assert "USD" in leeme and "ATR" in leeme
