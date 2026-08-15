"""El informe y las capturas de la fase 3.1.

Lo caro de las imágenes es renderizarlas, así que aquí se comprueba lo que se
decide *antes* de dibujar —qué lotes se piden y con qué criterio— y lo que el
informe promete: la declaración de portada, el orden de las secciones, la columna
de la 3.0 al lado y las ausencias declaradas de los arquetipos.

Los tres requisitos que más se juegan aquí:

- **los stops van ANTES que los resultados**, porque ver primero qué operaciones
  ganaron contamina el juicio sobre dónde va el stop;
- **si un arquetipo no existe en la muestra se declara**: la ausencia es un dato,
  y rellenarla con el ejemplar más parecido la convertiría en una opinión;
- **la tabla de largos y cortos por año es imprescindible** y va sin ninguna
  conclusión: sin ella no se puede distinguir un fallo del setup de la tendencia
  del oro durante todo el histórico.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries import archetypes
from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.comparison import (
    PhaseRun,
    lost_confirmations,
    priority_effect,
)
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.execution import (
    ExecutionRun,
    M1Executor,
    discarded_table,
    trades_table,
)
from chronos.application.entries.synthetic_run import build_synthetic
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.domain.entries.enums import ConfirmMode
from chronos.domain.entries.synthetic_entries import H4_RETEST_UP
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.reporting.entry_captures import (
    LOST,
    PER_VIA,
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
def anterior() -> PhaseRun:
    """La misma corrida con las tres vías de la fase 3.0, para comparar."""
    from dataclasses import replace

    from chronos.application.entries.cascade import build_cascade

    synthetic = build_synthetic(H4_RETEST_UP)
    entries = EntriesConfig(
        enabled=True, allow_missing_ask=True, confirm_mode=ConfirmMode.V30_TRES_VIAS
    )
    cascade = build_cascade(
        synthetic.run, synthetic.zones, synthetic.bars["M15"], entries
    )
    execution = M1Executor(
        synthetic.m1,
        InstrumentSpec(symbol="SYNTH"),
        replace(entries, confirm_mode=ConfirmMode.V30_TRES_VIAS),
        has_ask=False,
    ).execute(cascade)
    return PhaseRun(cascade, execution, trades_table(execution.trades))


@pytest.fixture(scope="module")
def report(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object], anterior: PhaseRun
) -> str:
    run, cascade, execution, zones = corrida
    synthetic = build_synthetic(H4_RETEST_UP)
    return render_entry_report(
        run,
        cascade,
        execution,
        trades_table(execution.trades),
        provenance="sintético",
        regression_ok=True,
        regression_note="sintético: la línea base real se comprueba en su propio test",
        previous=anterior,
        priority=priority_effect(run, zones, synthetic.bars["M15"], cascade),  # type: ignore[arg-type]
        lost=lost_confirmations(anterior.cascade, cascade),
        v30_regression="sintético: los recuentos reales se comprueban en su propio test",
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
    assert report.index("1. LOS STOPS") < report.index("§5.3 · Resultados")


def test_la_seccion_de_stops_declara_la_version_pre_registrada(report: str) -> None:
    stops = report.split("1. LOS STOPS")[1].split("2. §5.1")[0]

    assert "PRE-REGISTRADA" in stops
    assert "versión 2" in stops
    assert "23.3%" in stops.replace(",", ".")


def test_la_distribucion_del_1r_va_en_las_tres_unidades(report: str) -> None:
    stops = report.split("1. LOS STOPS")[1].split("2. §5.1")[0]

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


def test_el_informe_declara_que_r1_r2_y_r3_ya_no_confirman(report: str) -> None:
    """Dejan de ser vías y se siguen midiendo: las dos cosas, dichas."""
    assert "YA NO CONFIRMAN NADA" in report
    assert "columnas informativas" in report
    assert "SIN VALOR DE DECISIÓN" in report
    for kind in ("R1_mecha_en_zona", "R2_mecha_dominante", "R3_cierre_en_extremo"):
        assert kind in report


# --- §11.9: los casos límite -------------------------------------------------


def test_los_casos_limite_van_en_el_informe(report: str) -> None:
    """Son decisiones, y las decisiones las audita el propietario."""
    assert "10. Casos límite encontrados" in report
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


def test_los_lotes_de_capturas_son_una_por_via(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    """Lo que esta fase cambia es QUÉ confirma: la muestra enseña cada vía."""
    _run, _cascade, execution, _zones = corrida
    lots = dict(_trade_lots(execution))

    assert set(lots) == {"turtle", "ob"}
    for trades in lots.values():
        assert len(trades) <= PER_VIA
    for kind, trades in (("turtle_soup", lots["turtle"]), ("ob_h1", lots["ob"])):
        assert all(trade.signal.confirmation.kind.value == kind for trade in trades)


def test_las_capturas_de_cada_lote_son_las_primeras(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    """No una selección: elegir "las mejores" haría de la carpeta un argumento."""
    _run, _cascade, execution, _zones = corrida
    for _lot, trades in _trade_lots(execution):
        stamps = [trade.ts_entry for trade in trades]
        assert stamps == sorted(stamps)


def test_el_lote_de_perdidas_esta_acotado() -> None:
    """Veinte, como los otros: la carpeta es una muestra, no el histórico."""
    assert LOST == 20
    assert PER_VIA == 20


# --- Fase 3.1: lo que esta fase añade al informe -----------------------------


def test_la_portada_declara_el_modo_y_el_orden_de_las_vias(report: str) -> None:
    portada = report.split("0. Regresión")[0]

    assert "FASE 3.1" in portada
    assert "CONFIRM_MODE = v31_dos_vias" in portada
    assert "CONFIRM_PRIORITY" in portada


def test_el_informe_declara_las_dos_regresiones(report: str) -> None:
    """La de la 2.1 y la de la 3.0: las dos líneas base que esta fase no mueve."""
    seccion = report.split("0. Regresión")[1].split("Alcance de esta fase")[0]

    assert "LÍNEA BASE DE LA FASE 2.1" in seccion
    assert "LÍNEA BASE DE LA FASE 3.0" in seccion


def test_el_alcance_dice_las_dos_vias_y_lo_que_se_elimina(report: str) -> None:
    alcance = report.split("Alcance de esta fase")[1].split("Parámetros")[0]

    assert "TURTLE SOUP" in alcance
    assert "OB DE H1 ALCANZADO" in alcance
    assert "LO QUE SE ELIMINA" in alcance
    assert "Un ID solo no confirma" in alcance


def test_el_embudo_va_antes_y_despues(report: str) -> None:
    embudo = report.split("2. §5.1")[1].split("3. §5.2")[0]

    assert "3.0 contra 3.1" in embudo
    assert "n_30" in embudo
    assert "confirmaban en la 3.0 y ahora mueren" in embudo


def test_las_dos_vias_se_miden_por_separado(report: str) -> None:
    vias = report.split("3. §5.2")[1].split("4. §5.3")[0]

    assert "con_la_otra_via_disponible" in vias
    assert "CONFIRM_PRIORITY" in vias
    assert "Turtle soup detectados" in vias


def test_cada_desglose_lleva_la_columna_de_la_30_al_lado(report: str) -> None:
    resultados = report.split("4. §5.3")[1].split("5. §5.5")[0]

    assert resultados.count("3.1 contra 3.0:") >= 6
    assert "expectativa_neta_r_30" in resultados


def test_el_coste_por_operacion_tiene_su_propia_seccion(report: str) -> None:
    costes = report.split("5. §5.5")[1].split("6. §5.6")[0]

    assert "coste_p90_r" in costes
    assert "0,191 R" in costes
    assert "VERIFICAR" in costes


def test_largos_y_cortos_por_ano_van_sin_ninguna_conclusion(report: str) -> None:
    """La tabla es imprescindible y NO se interpreta: se presenta y ya está."""
    seccion = report.split("6. §5.6")[1].split("7. §5.7")[0]

    assert "IMPRESCINDIBLE" in seccion
    assert "NO SE SACA NINGUNA CONCLUSIÓN" in seccion
    assert "NO SE PROPONE FILTRAR POR DIRECCIÓN" in seccion


def test_la_frecuencia_va_antes_y_despues(report: str) -> None:
    seccion = report.split("7. §5.7")[1].split("8. R1")[0]

    assert "pct_vacias" in seccion
    assert "fase 3.0" in seccion


def test_el_informe_no_recomienda_ni_interpreta(report: str) -> None:
    """La fase pide números e imágenes; decide el propietario."""
    for prohibido in ("recomendamos", "se recomienda usar", "conviene filtrar"):
        assert prohibido not in report.lower()


# --- El LEEME ----------------------------------------------------------------


def test_el_leeme_avisa_de_lo_que_hay_que_saber(
    corrida: tuple[ImpulseRun, CascadeRun, ExecutionRun, object],
) -> None:
    _run, cascade, execution, _zones = corrida
    index = render_archetype_index(cascade, execution, ["turtle_01.png"])

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
