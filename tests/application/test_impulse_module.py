"""El módulo 1 de punta a punta: interruptor, tabla de salida e informes.

Cubre dos exigencias del enunciado que no son del detector sino del módulo:
"todo apagable" (con un test que lo comprueba) y la tabla de §5.1 con todas sus
columnas, incluidas las normalizaciones por ATR y por porcentaje de precio.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M5,
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    StructureReportingConfig,
)
from chronos.application.structure.detect_impulses import (
    AUDIT_COLUMNS,
    TABLE_COLUMNS,
    DetectDominantImpulses,
    ImpulseRun,
    session_slots,
)
from chronos.application.structure.statistics import summarize
from chronos.domain.structure.enums import AnchorMode, ImpulseDirection
from chronos.domain.structure.patterns import PATTERN_COLUMNS
from chronos.infrastructure.reporting.impulse_explorer import build_payload, render_explorer
from chronos.infrastructure.reporting.impulse_report import render_report
from chronos.infrastructure.reporting.impulse_writer import ImpulseReportWriter
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture
def config() -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee-en-estos-tests.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
        # Las capturas abren un navegador headless por imagen: se prueban aparte,
        # no en cada test que escriba una carpeta.
        reporting=StructureReportingConfig(captures=False),
    )


@pytest.fixture
def series(config: ImpulseConfig) -> dict[str, pd.DataFrame]:
    history = make_m1_history(weeks=12)
    return {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }


@pytest.fixture
def run(config: ImpulseConfig, series: dict[str, pd.DataFrame]) -> ImpulseRun:
    return DetectDominantImpulses(config).execute(series, provenance="fixture sintética")


# --- Todo apagable ----------------------------------------------------------


def test_con_el_modulo_apagado_no_se_emite_nada(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    apagado = DetectDominantImpulses(_replace_enabled(config, False)).execute(series)

    assert apagado.emits_nothing
    assert apagado.analyses == {}
    assert apagado.chart_bars == {}
    assert apagado.table().empty
    assert summarize(apagado).per_timeframe == {}


def test_con_el_modulo_apagado_no_se_escribe_ningun_fichero(
    config: ImpulseConfig, series: dict[str, pd.DataFrame], tmp_path: object
) -> None:
    apagado = DetectDominantImpulses(_replace_enabled(config, False)).execute(series)
    carpeta = ImpulseReportWriter(str(tmp_path)).write(apagado)

    assert carpeta is None
    assert list(pd.io.common.Path(str(tmp_path)).iterdir()) == []  # type: ignore[attr-defined]


def test_el_informe_de_un_modulo_apagado_lo_dice(config: ImpulseConfig) -> None:
    apagado = DetectDominantImpulses(_replace_enabled(config, False)).execute({})
    texto = render_report(apagado)
    assert "DESACTIVADO" in texto
    assert "no se emite ningún impulso" in texto


# --- Tabla de impulsos (§5.1) -----------------------------------------------


def test_la_tabla_tiene_las_columnas_exigidas(run: ImpulseRun) -> None:
    tabla = run.table()
    assert list(tabla.columns) == [*TABLE_COLUMNS, *AUDIT_COLUMNS]
    assert not tabla.empty


def test_cada_fila_es_trazable_a_su_configuracion(run: ImpulseRun) -> None:
    tabla = run.table()
    assert set(tabla["config_hash"]) == {run.config_hash}


def test_solo_se_detecta_en_las_temporalidades_que_llevan_impulso(run: ImpulseRun) -> None:
    tabla = run.table()
    # H1, M15 y M5 se dibujan pero no llevan detector: no se les marca ID, y
    # sobre las tres se ve el impulso de H4.
    assert set(tabla["timeframe"]) == {H4, DAILY}
    assert set(run.chart_bars) == {M5, M15, H1, H4, DAILY}
    # Un ID diario se rompe con cierres diarios: hay muchos menos que en H4.
    assert (tabla["timeframe"] == H4).sum() > (tabla["timeframe"] == DAILY).sum()


def test_faltar_las_velas_de_una_temporalidad_detectada_es_un_error(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    incompleto = {tf: frame for tf, frame in series.items() if tf != H4}
    with pytest.raises(Exception, match="Faltan las velas de H4"):
        DetectDominantImpulses(config).execute(incompleto)


def test_el_rango_se_publica_en_usd_en_atr_y_en_porcentaje(run: ImpulseRun) -> None:
    """Sin normalizar, 2015 y 2026 no son comparables: el oro triplicó su precio."""
    tabla = run.table()
    usd = tabla["rango_usd"].to_numpy(dtype=float)
    atr = tabla["rango_atr"].to_numpy(dtype=float)
    pct = tabla["rango_pct_precio"].to_numpy(dtype=float)

    assert np.isfinite(usd).all()
    assert np.isfinite(atr).sum() > 0
    assert pct == pytest.approx(usd / tabla["precio_ancla"].abs().to_numpy(dtype=float))


def test_el_atr_publicado_es_el_de_la_barra_anterior(run: ImpulseRun) -> None:
    """`atr_previo` nunca puede incluir la barra en la que se constituye el ID."""
    analysis = run.analyses[H4]
    bars = analysis.bars
    verdadero_rango = (bars["high"] - bars["low"]).to_numpy(dtype=float)
    tabla = analysis.table
    for _, row in tabla.head(20).iterrows():
        position = bars.index.get_loc(pd.Timestamp(row["ts_constitucion"]))
        if not np.isfinite(row["atr_previo"]):
            continue
        # El ATR previo se calcula sobre barras anteriores: no puede coincidir
        # con el rango de la propia barra salvo por casualidad numérica.
        assert row["atr_previo"] != verdadero_rango[position]


def test_las_direcciones_se_alternan_segun_el_tipo_de_rotura(run: ImpulseRun) -> None:
    analysis = run.analyses[H4]
    for anterior, siguiente in pairwise(analysis.published):
        if anterior.exit_break_kind is None:
            continue
        esperada = (
            anterior.direction
            if anterior.exit_break_kind.value == "ROTURA_A_FAVOR"
            else anterior.direction.opposite()
        )
        assert siguiente.direction is esperada


def test_el_estado_por_barra_cubre_todas_las_barras(run: ImpulseRun) -> None:
    for analysis in run.analyses.values():
        assert len(analysis.states) == len(analysis.bars)
        assert [state.timestamp for state in analysis.states] == list(analysis.bars.index)


# --- Determinismo y trazabilidad --------------------------------------------


def test_misma_configuracion_mismo_hash_y_misma_tabla(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    primera = DetectDominantImpulses(config).execute(series)
    segunda = DetectDominantImpulses(config).execute(series)
    assert primera.config_hash == segunda.config_hash
    pd.testing.assert_frame_equal(primera.table(), segunda.table())


def test_cambiar_un_parametro_cambia_el_hash(config: ImpulseConfig) -> None:
    otro = ImpulseConfig(
        data=config.data,
        aggregation=AggregationConfig(h4_offset_hours=1),
        rules=config.rules,
    )
    assert otro.fingerprint() != config.fingerprint()


def test_cambiar_las_temporalidades_detectadas_cambia_el_hash(config: ImpulseConfig) -> None:
    otro = ImpulseConfig(
        data=config.data, rules=config.rules, charts=ChartsConfig({H4: (H4,)})
    )
    assert otro.fingerprint() != config.fingerprint()


def test_el_hash_ignora_lo_que_no_cambia_ningun_impulso(config: ImpulseConfig) -> None:
    """Cambiar la carpeta de salida no debería invalidar una corrida."""
    otro = ImpulseConfig(
        data=config.data,
        aggregation=config.aggregation,
        rules=config.rules,
        reporting=StructureReportingConfig(output_dir="otra-carpeta"),
    )
    assert otro.fingerprint() == config.fingerprint()


# --- Estadística e informes -------------------------------------------------


def test_la_estadistica_va_desglosada_por_año(run: ImpulseRun) -> None:
    stats = summarize(run)
    tablas = stats.per_timeframe[H4]
    for tabla in (
        tablas.counts_by_year,
        tablas.id_duration,
        tablas.limbo_duration,
        tablas.range_usd,
        tablas.breaks_by_year,
        tablas.bodies_by_year,
    ):
        assert "anio" in tabla.columns
        assert "TOTAL" in set(tabla["anio"])


def test_la_duracion_del_limbo_se_reporta_con_maximo(run: ImpulseRun) -> None:
    limbo = summarize(run).per_timeframe[H4].limbo_duration
    assert {"mediana", "p10", "p90", "maximo"} <= set(limbo.columns)
    assert float(limbo.iloc[-1]["maximo"]) >= float(limbo.iloc[-1]["mediana"])


def test_el_informe_declara_lado_offset_y_decisiones_abiertas(run: ImpulseRun) -> None:
    texto = render_report(run)
    assert "STRUCTURE_SIDE" in texto
    assert "H4_OFFSET_HOURS" in texto
    assert "ANCHOR_MODE" in texto
    assert "SEED_MODE" in texto
    assert "DOJI_BREAK_MODE" in texto
    # Y deja claro lo que la fase NO hace.
    assert "ni entradas, ni stops" in texto


def test_el_informe_compara_las_dos_anclas(run: ImpulseRun) -> None:
    texto = render_report(run)
    assert "A1 frente a A2" in texto
    veredicto = summarize(run).per_timeframe[H4].anchor_verdict
    assert veredicto.startswith(("DESPRECIABLE", "RELEVANTE"))


def test_el_informe_cuenta_dojis_y_cuerpos_minimos(run: ImpulseRun) -> None:
    tabla = summarize(run).per_timeframe[H4].bodies_by_year
    assert {"dojis", "por_cuerpo_minimo", "pct_minimo"} <= set(tabla.columns)
    assert int(tabla.iloc[-1]["impulsos"]) == len(run.analyses[H4].published)


def test_el_explorador_dibuja_impulsos_limbo_y_marcadores(run: ImpulseRun) -> None:
    payload = build_payload(run)
    h4 = payload["impulses"][H4]
    assert h4["limbo"], "sin tramos de limbo sombreados"
    assert h4["constitutions"], "sin marcadores de constitución"
    assert h4["breaks"], "sin marcadores de rotura"
    assert any(
        item["d"] == ImpulseDirection.ALCISTA.value for item in h4["list"]
    ), "sin ningún impulso alcista que dibujar"
    assert payload["charts"] == [DAILY, H4, H1, M15, M5]


def test_el_explorador_es_autocontenido_y_con_cuatro_decimales(run: ImpulseRun) -> None:
    html = render_explorer(run, max_bars=100)
    assert "Plotly" in html
    # Se abre con doble clic y sin conexión: nada se carga desde fuera.
    assert "<script src=" not in html
    assert "<link " not in html
    assert "Diario" in html and "Velas" in html
    assert 'id="explorer-data"' in html
    payload = build_payload(run, max_bars=100)
    assert payload["meta"]["decimals"] == 4
    assert payload["bars"][H4]["truncated"] is True


def test_el_escritor_deja_una_carpeta_autocontenida(
    run: ImpulseRun, tmp_path: object
) -> None:
    carpeta = ImpulseReportWriter(str(tmp_path)).write(run, run_id="20260810_000000")
    assert carpeta is not None
    nombres = {fichero.name for fichero in carpeta.iterdir()}
    assert nombres == {
        "impulsos.csv",
        "eventos_rotura.csv",
        "estado_por_barra.csv",
        "contactos.csv",
        "patrones.csv",
        "reporte.txt",
        "explorador.html",
        "run.json",
    }
    tabla = pd.read_csv(carpeta / "impulsos.csv")
    assert list(tabla.columns) == [*TABLE_COLUMNS, *AUDIT_COLUMNS]
    patrones = pd.read_csv(carpeta / "patrones.csv")
    assert list(patrones.columns) == list(PATTERN_COLUMNS)
    assert len(patrones) == len(run.patterns_table()) > 0


# --- K.2 · los cortes del día en los que se marcan el OB y el FVG -------------


def test_los_patrones_solo_se_marcan_en_h4_dentro_de_su_id(run: ImpulseRun) -> None:
    assert list(run.patterns) == [H4]
    tabla = run.patterns[H4]
    assert tabla["timeframe"].eq(H4).all() and tabla["id_timeframe"].eq(H4).all()
    publicados = {impulse.id_num for impulse in run.analyses[H4].published}
    assert set(tabla["id_num"]) <= publicados


def test_la_posicion_en_el_dia_de_sesion_no_se_mueve_con_el_horario_de_verano() -> None:
    """Con el día anclado a las 17:00 de Nueva York, la 2.ª vela es la de las
    21:00 de Nueva York todo el año: 02:00 UTC en enero y 01:00 UTC en julio."""
    index = pd.DatetimeIndex(
        [
            "2024-01-07 22:00",  # domingo 17:00 NY: 1.ª vela
            "2024-01-08 02:00",  # 21:00 NY: 2.ª
            "2024-01-08 06:00",  # 01:00 NY: 3.ª
            "2024-01-08 10:00",  # 05:00 NY: 4.ª
            "2024-01-08 14:00",  # 09:00 NY: 5.ª
            "2024-01-08 18:00",  # 13:00 NY: 6.ª
            "2024-07-07 21:00",  # domingo 17:00 NY en verano: 1.ª
            "2024-07-08 01:00",  # 21:00 NY: 2.ª
            "2024-07-08 09:00",  # 05:00 NY: 4.ª
        ],
        tz="UTC",
    )
    assert session_slots(index, AggregationConfig()).tolist() == [0, 1, 2, 3, 4, 5, 0, 1, 3]
    # Con el corte fijo en UTC, el día empieza a esa hora todo el año.
    assert session_slots(index, AggregationConfig(d_session_start="22:00")).tolist() == [
        0, 1, 2, 3, 4, 5, 5, 0, 2,
    ]


# --- Apoyo ------------------------------------------------------------------


def _replace_enabled(config: ImpulseConfig, enabled: bool) -> ImpulseConfig:
    return ImpulseConfig(
        enabled=enabled,
        symbol=config.symbol,
        structure_side=config.structure_side,
        data=config.data,
        aggregation=config.aggregation,
        charts=config.charts,
        rules=config.rules,
        timezone_audit=config.timezone_audit,
        reporting=config.reporting,
    )


def test_el_modo_de_ancla_se_refleja_en_la_columna_publicada(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> None:
    # Los dos modos se declaran a mano: cuál es el del proyecto es una decisión
    # del propietario (R-02) y este test no debe moverse cuando la cambie.
    def tabla(mode: AnchorMode) -> pd.DataFrame:
        tuned = ImpulseConfig(
            data=config.data,
            rules=ImpulseRulesConfig(anchor_mode=mode, warmup_bars=5),
        )
        return DetectDominantImpulses(tuned).execute(series).table()

    a1 = tabla(AnchorMode.A1_LAST_COUNTER_BODY)
    a2 = tabla(AnchorMode.A2_FIRST_LEG_BAR)

    assert (a1["precio_ancla"] == a1["precio_ancla_a1"]).all()
    assert (a2["precio_ancla"] == a2["precio_ancla_a2"]).all()
    assert not a1["precio_ancla"].equals(a2["precio_ancla"])
