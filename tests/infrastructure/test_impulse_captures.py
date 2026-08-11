"""Capturas exportadas (F.4).

Lo caro de estas imágenes es renderizarlas, así que aquí se comprueba sobre todo
lo que se decide *antes* de dibujar: qué ventanas se piden, con cuánto contexto y
con qué texto sobreimpreso. Sólo una prueba escribe un PNG de verdad, y va
marcada como lenta.
"""

from __future__ import annotations

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
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.lateralization import measure
from chronos.application.structure.statistics import summarize
from chronos.infrastructure.reporting.impulse_captures import (
    BEST_SIGNATURE,
    CONTEXT_BARS,
    SMALLEST,
    WORST_WHIPSAWS,
    _figure,
    _requests,
    write_captures,
)
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture
def run() -> ImpulseRun:
    history = make_m1_history(weeks=20)
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        charts=ChartsConfig({DAILY: (DAILY,), H4: (H4, DAILY)}),
        rules=ImpulseRulesConfig(warmup_bars=5),
    )
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.detected
        ).items()
    }
    return DetectDominantImpulses(config).execute(series)


def requests(run: ImpulseRun):
    return _requests(run, summarize(run), measure(run))


# --- Qué se pide ------------------------------------------------------------


def test_se_piden_las_capturas_que_exige_la_fase(run: ImpulseRun) -> None:
    nombres = [request.name for request in requests(run)]
    familias = {nombre.split("_")[0] for nombre in nombres}

    # `diciembre` sólo aparece si el histórico llega a diciembre de 2025; esta
    # fixture es de 2024 y tiene su propio test más abajo.
    assert {"pequenos", "latigazo", "alineadas"} <= familias
    assert sum(1 for nombre in nombres if nombre.startswith("pequenos_D")) <= SMALLEST
    assert sum(1 for nombre in nombres if nombre.startswith("firma_D")) <= BEST_SIGNATURE
    assert sum(1 for nombre in nombres if nombre.startswith("latigazo")) <= WORST_WHIPSAWS
    assert len(set(nombres)) == len(nombres), "dos capturas no pueden pisarse el fichero"


def test_las_capturas_de_los_mas_pequeños_van_de_menor_a_mayor(run: ImpulseRun) -> None:
    tabla = run.analyses[DAILY].table.dropna(subset=["rango_atr"])
    esperados = list(tabla.nsmallest(SMALLEST, "rango_atr")["id_num"].astype(int))
    obtenidos = [
        int(request.name.split("id")[-1])
        for request in requests(run)
        if request.name.startswith("pequenos_D")
    ]
    assert obtenidos == esperados


def test_cada_captura_dice_lo_que_hace_falta_para_juzgarla(run: ImpulseRun) -> None:
    pequeña = next(r for r in requests(run) if r.name.startswith("pequenos_"))
    assert "ATR" in pequeña.subtitle
    assert "USD" in pequeña.subtitle
    assert "salida" in pequeña.subtitle
    assert "vigente" in pequeña.subtitle
    assert pequeña.highlight, "el ID que se está enseñando tiene que ir destacado"


def test_un_id_de_una_barra_se_enseña_con_contexto(run: ImpulseRun) -> None:
    pequeña = next(r for r in requests(run) if r.name.startswith("pequenos_"))
    assert pequeña.context == CONTEXT_BARS


def test_el_tramo_alineado_sale_en_todas_las_temporalidades(run: ImpulseRun) -> None:
    alineadas = [r for r in requests(run) if r.name.startswith("alineadas_")]
    assert {request.timeframe for request in alineadas} == set(run.analyses)
    assert all("alineadas" in request.title for request in alineadas)


def test_el_latigazo_va_de_la_ida_a_la_vuelta(run: ImpulseRun) -> None:
    latigazos = [r for r in requests(run) if r.name.startswith("latigazo_")]
    if not latigazos:
        pytest.skip("la fixture no produjo ningún latigazo")
    for request in latigazos:
        assert request.last > request.first
        assert len(request.highlight) == 2


def test_diciembre_de_2025_sale_si_el_historico_llega(run: ImpulseRun) -> None:
    """La ficha de D.6 se exporta sólo cuando esas velas existen."""
    history = make_m1_history(weeks=10, start="2025-11-02 22:00")
    config = run.config
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.detected
        ).items()
    }
    reciente = DetectDominantImpulses(config).execute(series)
    peticiones = _requests(reciente, summarize(reciente), measure(reciente))
    diciembre = [r for r in peticiones if r.name.startswith("diciembre")]

    assert len(diciembre) == 1
    assert diciembre[0].timeframe == DAILY
    assert diciembre[0].context < CONTEXT_BARS, "un mes ya trae su propio contexto"
    assert not any(r.name.startswith("diciembre") for r in requests(run))


# --- Dibujo -----------------------------------------------------------------


def test_la_figura_recorta_la_ventana_y_le_añade_el_contexto(run: ImpulseRun) -> None:
    peticion = next(r for r in requests(run) if r.name.startswith("pequenos_D"))
    analysis = run.analyses[DAILY]
    figura = _figure(analysis, measure(run).per_timeframe[DAILY], peticion)

    velas = figura.data[0]
    esperadas = min(len(analysis.bars) - 1, peticion.last + peticion.context) - max(
        0, peticion.first - peticion.context
    ) + 1
    assert len(velas.x) == esperadas
    assert velas.type == "candlestick"
    # Eje de categorías: sin huecos de fin de semana en el dibujo.
    assert figura.layout.xaxis.type == "category"


def test_la_figura_dibuja_los_dos_limites_y_el_nivel_del_50(run: ImpulseRun) -> None:
    peticion = next(r for r in requests(run) if r.name.startswith("pequenos_D"))
    figura = _figure(run.analyses[DAILY], measure(run).per_timeframe[DAILY], peticion)
    lineas = [trace for trace in figura.data if trace.type == "scatter"]
    punteadas = [trace for trace in lineas if trace.line.dash == "dot"]

    assert lineas, "el ID tiene que salir dibujado"
    assert punteadas, "el nivel del 50 % va punteado"
    assert any("ID " in (anotacion.text or "") for anotacion in figura.layout.annotations)


@pytest.mark.slow
def test_escribir_las_capturas_deja_ficheros_png(
    run: ImpulseRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kaleido tarda ~2 s por imagen: aquí se piden pocas, no las setenta."""
    for nombre in ("SMALLEST", "BEST_SIGNATURE", "WORST_WHIPSAWS"):
        monkeypatch.setattr(f"chronos.infrastructure.reporting.impulse_captures.{nombre}", 1)

    escritas = write_captures(run, summarize(run), measure(run), tmp_path / "capturas")

    assert escritas
    for path in escritas:
        assert path.suffix == ".png"
        assert path.stat().st_size > 5_000
