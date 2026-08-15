"""El informe y las capturas de la fase 3.0 (§9, §10 y §11).

Lo caro de las imágenes es renderizarlas, así que aquí se comprueba lo que se
decide *antes* de dibujar —qué lotes se piden y con qué criterio— y lo que el
informe promete: la declaración de portada, el orden de las secciones y las
ausencias declaradas de los arquetipos.

Los dos requisitos del enunciado que más se juegan aquí:

- **los stops van ANTES que los resultados** (§10), porque ver primero qué
  operaciones ganaron contamina el juicio sobre dónde va el stop;
- **si un arquetipo no existe en la muestra se declara** (§9): la ausencia es un
  dato, y rellenarla con el ejemplar más parecido la convertiría en una opinión.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries import archetypes
from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.execution import (
    ExecutionRun,
    M1Executor,
    discarded_table,
    trades_table,
)
from chronos.application.entries.synthetic_run import build_synthetic
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.domain.entries.synthetic_entries import H4_RETEST_UP
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.reporting.entry_captures import (
    LOSERS,
    PER_GUARD_RAIL,
    WINNERS,
    _discarded_lots,
    _trade_lots,
    write_entry_captures,
)
from chronos.infrastructure.reporting.entry_report import (
    EDGE_CASES,
    render_archetype_index,
    render_entry_report,
)


@pytest.fixture(scope="module")
def corrida() -> tuple[ImpulseRun, CascadeRun, ExecutionRun, object]:
    """La cascada del sintético, ejecutada sobre sus propias velas M1."""
    synthetic = build_synthetic(H4_RETEST_UP)
    entries = EntriesConfig(enabled=True, allow_missing_ask=True)
    execution = M1Executor(
        synthetic.m1,
        InstrumentSpec(symbol="SYNTH"),
        entries,
        has_ask=False,
    ).execute(synthetic.cascade)
    return synthetic.run, synthetic.cascade, execution, synthetic.zones


@pytest.fixture(scope="module")
def report(corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object]) -> str:
    run, cascade, execution, _zones = corrida
    return render_entry_report(
        run,
        cascade,
        execution,
        trades_table(execution.trades),
        provenance="sintético",
        regression_ok=True,
        regression_note="sintético: la línea base real se comprueba en su propio test",
    )


# --- §4: la declaración obligatoria de portada -------------------------------


def test_la_portada_declara_que_no_hay_fichero_de_ask(report: str) -> None:
    """Un neto leído sin saber esto es un neto mal leído."""
    portada = report.split("0. Regresión")[0]

    assert "NO HAY FICHERO DE ASK" in portada
    assert "bid" in portada


def test_la_portada_marca_todos_los_costes_como_verificar(report: str) -> None:
    portada = report.split("0. Regresión")[0]

    assert portada.count("VERIFICAR") >= 5
    for concepto in ("horquilla", "deslizamiento", "comisión", "swap"):
        assert concepto in portada


# --- §10: los stops, antes que los resultados --------------------------------


def test_los_stops_van_antes_que_los_resultados(report: str) -> None:
    """La nota del §10 convertida en índice, no en una advertencia al final."""
    assert report.index("1. LOS STOPS") < report.index("3. Resultados")


def test_la_seccion_de_stops_declara_la_version_pre_registrada(report: str) -> None:
    stops = report.split("1. LOS STOPS")[1].split("2. Embudo")[0]

    assert "PRE-REGISTRADA" in stops
    assert "versión 2" in stops
    assert "23.3%" in stops.replace(",", ".")


def test_la_distribucion_del_1r_va_en_las_tres_unidades(report: str) -> None:
    stops = report.split("1. LOS STOPS")[1].split("2. Embudo")[0]

    for unidad in ("usd_p50", "atr_p50", "pct_precio_p50", "bajo_una_horquilla"):
        assert unidad in stops


# --- §5: ningún número agregado sin su desglose ------------------------------


def test_estan_los_ocho_desgloses_del_5(report: str) -> None:
    for titulo in (
        "5.1 · Con contexto diario",
        "5.2 · Tipo de zona",
        "5.3 · Desenlace de la zona",
        "5.4 · Entrada en H1",
        "5.5 · Stop en H1",
        "5.6 · Dirección",
        "5.7 · Año",
        "5.8 · Definición de rechazo",
    ):
        assert titulo in report, titulo


def test_se_declara_la_combinacion_que_no_existe(report: str) -> None:
    """Entrada en H1 con stop de M15 es imposible sin lookahead: se dice."""
    assert "entrada en H1 con stop de M15" in report
    assert "NO aparece y no es un olvido" in report


# --- §2: las tres definiciones, ninguna adoptada -----------------------------


def test_el_informe_no_adopta_ninguna_definicion_de_rechazo(report: str) -> None:
    assert "NINGUNA ADOPTADA" in report
    assert "la UNIÓN" in report
    for kind in ("R1_mecha_en_zona", "R2_mecha_dominante", "R3_cierre_en_extremo"):
        assert kind in report


# --- §11.9: los casos límite -------------------------------------------------


def test_los_casos_limite_van_en_el_informe(report: str) -> None:
    """Son decisiones, y las decisiones las audita el propietario."""
    assert "6. Casos límite encontrados" in report
    assert len(EDGE_CASES) >= 8
    for case in EDGE_CASES:
        assert case.split(".")[0][:30] in report


# --- §9: los cinco arquetipos ------------------------------------------------


def test_los_cinco_arquetipos_estan_todos_en_el_informe(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object], report: str
) -> None:
    _run, cascade, execution, _zones = corrida
    found = archetypes.audit(cascade, execution)

    assert {item.number for item in found} == {1, 2, 3, 4, 5}
    for item in found:
        assert item.title in report
        assert item.criterion in report


def test_un_arquetipo_que_no_existe_se_declara(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object], report: str
) -> None:
    """La ausencia es un dato: no se sustituye por el ejemplar más parecido."""
    _run, cascade, execution, _zones = corrida
    absent = [item for item in archetypes.audit(cascade, execution) if not item.found]

    if not absent:
        pytest.skip("el sintético tiene los cinco arquetipos")
    assert "NO EXISTE EN LA MUESTRA" in report
    for item in absent:
        assert item.absence
        assert item.absence in report


def test_la_tabla_de_arquetipos_dice_cuando_y_con_que_captura(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    table = archetypes.table(archetypes.audit(cascade, execution))

    assert list(table.columns) == [
        "n", "arquetipo", "encontrado", "cuando", "captura", "ausencia"
    ]
    assert table["captura"].is_unique


# --- §10: qué se captura -----------------------------------------------------


def test_los_lotes_de_capturas_son_los_del_enunciado(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    lots = dict(_trade_lots(execution))

    assert set(lots) == {"ganadora", "perdedora"}
    assert len(lots["ganadora"]) <= WINNERS
    assert len(lots["perdedora"]) <= LOSERS
    for rail, items in _discarded_lots(cascade, execution):
        assert items, f"{rail} no debería aparecer con el lote vacío"
        assert len(items) <= PER_GUARD_RAIL


def test_las_ganadoras_y_las_perdedoras_son_las_primeras(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    """No una selección: elegir "las mejores" haría de la carpeta un argumento."""
    _run, _cascade, execution, _zones = corrida
    for _lot, trades in _trade_lots(execution):
        stamps = [trade.ts_entry for trade in trades]
        assert stamps == sorted(stamps)


def test_un_guardarrail_sin_senales_no_produce_lote_vacio(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    rails = {rail for rail, _ in _discarded_lots(cascade, execution)}
    presentes = {
        item.guard_rail.value for item in (*cascade.discarded, *execution.discarded)
    }

    assert rails == presentes


# --- El LEEME ----------------------------------------------------------------


def test_el_leeme_avisa_de_lo_que_hay_que_saber(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    index = render_archetype_index(cascade, execution, ["ganadora_01.png"])

    assert "revisa los stops ANTES" in index
    assert "NO HAY FICHERO DE ASK" in index
    assert "VERIFICAR" in index
    assert "decide el propietario" in index
    assert "1 capturas" in index


def test_el_leeme_lista_los_cinco_arquetipos_con_su_estado(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    index = render_archetype_index(cascade, execution, [])

    for item in archetypes.audit(cascade, execution):
        assert item.capture in index
        assert ("encontrado" if item.found else "NO EXISTE EN LA MUESTRA") in index


# --- Los CSV -----------------------------------------------------------------


def test_el_csv_de_descartadas_dice_donde_murio_cada_una(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    table = discarded_table((*cascade.discarded, *execution.discarded))

    assert not table.empty
    assert "guardarrail" in table.columns
    assert table["guardarrail"].notna().all()


# --- Las imágenes de verdad --------------------------------------------------


@pytest.mark.slow
def test_escribe_las_imagenes(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object], tmp_path
) -> None:
    run, cascade, execution, zones = corrida
    written = write_entry_captures(run, zones, cascade, execution, tmp_path)  # type: ignore[arg-type]

    assert written
    for path in written:
        assert path.exists()
        assert path.stat().st_size > 0
    assert len(set(written)) == len(written)


def test_la_tabla_de_operaciones_no_pierde_ninguna(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, _cascade, execution, _zones = corrida
    table = trades_table(execution.trades)

    assert len(table) == len(execution.trades)
    assert pd.api.types.is_datetime64_any_dtype(table["ts_entrada"])
