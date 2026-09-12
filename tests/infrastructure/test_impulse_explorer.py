"""El explorador visual del impulso dominante (§5.3).

El explorador es la herramienta de auditoría de esta fase: el propietario lo pone
al lado de sus capturas de TradingView y compara impulso a impulso. Si se dibuja
mal —o directamente no se dibuja— la fase 1 no se puede validar, así que su
JavaScript se ejecuta aquí con node contra un DOM simulado.

Lo que más se comprueba es el reparto: cada gráfico tiene que llevar su impulso
y el de la temporalidad superior que le toque, con el principal en trazo continuo
y el de contexto punteado.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

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
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.lateralization import measure
from chronos.domain.indicators import rsi
from chronos.domain.structure.enums import LegStartMode
from chronos.domain.structure.patterns import PATTERN_COLUMNS
from chronos.domain.structure.sessions import session_levels
from chronos.infrastructure.reporting.impulse_explorer import (
    ASSETS,
    BEARISH,
    BULLISH,
    DECIMALS,
    FIB_LEVELS,
    HAND_FIB,
    HAND_LINES,
    HAND_RECTS,
    PATTERN_COLORS,
    RSI_BANDS,
    RSI_PERIOD,
    SESSION_COLORS,
    TIMEFRAME_COLORS,
    ModeVariant,
    bar_counts,
    build_payload,
    payload_size,
    render_explorer,
)
from chronos.infrastructure.reporting.timezones import session_label
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture
def run() -> ImpulseRun:
    history = make_m1_history(weeks=16)
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
    )
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }
    return DetectDominantImpulses(config).execute(
        series, provenance="fixture sintética", base_bars=history
    )


#: Un reparto con CONTEXTO: el Diario dibujado detrás del ID de H4. No es el del
#: propietario —el Diario se dibuja sólo en su gráfico— pero es el único modo de
#: retratar el trazo punteado y la casilla por temporalidad con los dos únicos
#: detectores que hay, el Diario y H4.
CONTEXT_CHARTS = ChartsConfig(
    {DAILY: (DAILY,), H4: (H4, DAILY), H1: (H4,), M15: (H4,), M5: (H4,)}
)


@pytest.fixture
def context_run(run: ImpulseRun) -> ImpulseRun:
    """La misma corrida con el Diario de contexto sobre H4."""
    series = {timeframe: analysis.bars for timeframe, analysis in run.analyses.items()}
    series.update(run.chart_bars)
    tuned = replace(run.config, charts=CONTEXT_CHARTS)
    return DetectDominantImpulses(tuned).execute(series, provenance="fixture con contexto")


# --- Reparto de gráficos ----------------------------------------------------


def test_el_reparto_por_defecto_es_el_del_propietario(run: ImpulseRun) -> None:
    payload = build_payload(run)
    assert payload["charts"] == [DAILY, H4, H1, M15, M5]
    assert payload["layout"] == {
        DAILY: [DAILY],
        # El Diario se dibuja sólo en su gráfico: en H4 no se ve nada suyo.
        H4: [H4],
        H1: [H4],
        M15: [H4],
        # M5 es donde se afinan la entrada y el stop: lleva el ID de H4 detrás.
        M5: [H4],
    }


def test_h1_m15_y_m5_llevan_velas_pero_no_impulso_propio(run: ImpulseRun) -> None:
    """El ID vive sólo en el Diario y en H4: sobre las otras tres se dibuja el de H4."""
    payload = build_payload(run)
    assert {H1, M15, M5} <= set(payload["bars"])
    assert not {H1, M15, M5} & set(payload["impulses"])
    assert set(payload["impulses"]) == {DAILY, H4}


def test_cada_grafico_tiene_sus_velas(run: ImpulseRun) -> None:
    counts = bar_counts(build_payload(run))
    assert set(counts) == {DAILY, H4, H1, M15, M5}
    assert counts[M5] > counts[M15] > counts[H1] > counts[H4] > counts[DAILY]


def test_un_grafico_sin_velas_no_llega_a_ofrecerse(run: ImpulseRun) -> None:
    """Con un histórico H1 no hay M15 ni M5: su pestaña no puede quedarse esperando."""
    sin_finas = ImpulseRun(
        enabled=True,
        config=run.config,
        config_hash=run.config_hash,
        analyses=run.analyses,
        chart_bars={
            chart: frame for chart, frame in run.chart_bars.items() if chart not in (M15, M5)
        },
    )
    payload = build_payload(sin_finas)

    assert payload["charts"] == [DAILY, H4, H1]
    assert not {M15, M5} & set(payload["layout"])
    assert not {M15, M5} & set(payload["bars"])


def test_no_se_puede_superponer_una_temporalidad_inferior() -> None:
    with pytest.raises(Exception, match="temporalidad superior"):
        ChartsConfig({H4: (H4, M15)})


def test_una_temporalidad_no_soportada_se_rechaza() -> None:
    with pytest.raises(Exception, match="no soportada"):
        ChartsConfig({"M3": ("M3",)})


def test_las_detectadas_salen_de_lo_que_se_dibuja() -> None:
    charts = ChartsConfig({M15: (H1,), H1: (H1, H4)})
    assert charts.detected == (H4, H1)  # M15 no lleva detector
    assert charts.charts == (H1, M15)
    assert charts.primary(M15) == H1


# --- Payload ----------------------------------------------------------------


def test_cada_impulso_publicado_viaja_al_explorador(run: ImpulseRun) -> None:
    payload = build_payload(run)
    for timeframe, analysis in run.analyses.items():
        assert len(payload["impulses"][timeframe]["list"]) == len(analysis.published)


def test_cada_impulso_lleva_sus_dos_niveles_y_su_tramo(run: ImpulseRun) -> None:
    """Ancla y extremo, desde la constitución hasta la rotura que lo mata."""
    payload = build_payload(run)
    impulso = run.analyses[H4].published[0]
    registro = payload["impulses"][H4]["list"][0]
    epoch = pd.Timestamp("1970-01-01", tz="UTC")

    assert registro["d"] == impulso.direction.value
    assert epoch + pd.Timedelta(minutes=registro["x0"]) == pd.Timestamp(impulso.ts_constitution)
    assert epoch + pd.Timedelta(minutes=registro["x1"]) == pd.Timestamp(impulso.ts_end)
    assert registro["a"] == pytest.approx(round(impulso.anchor, 4))
    assert registro["e"] == pytest.approx(round(impulso.extreme, 4))


def test_el_sombreado_cubre_exactamente_los_tramos_de_limbo(run: ImpulseRun) -> None:
    payload = build_payload(run)
    estados = run.analyses[H4].states

    tramos = 0
    anterior_en_limbo = False
    for estado in estados:
        if estado.in_limbo and not anterior_en_limbo:
            tramos += 1
        anterior_en_limbo = estado.in_limbo
    assert len(payload["impulses"][H4]["limbo"]) == tramos


def test_los_marcadores_distinguen_los_dos_tipos_de_rotura(run: ImpulseRun) -> None:
    payload = build_payload(run)
    breaks = payload["impulses"][H4]["breaks"]
    eventos = run.analyses[H4].events
    a_favor = sum(1 for evento in eventos if evento.kind.value == "ROTURA_A_FAVOR")

    assert len(breaks) == len(eventos)
    assert sum(1 for item in breaks if item["k"] == "favor") == a_favor
    assert sum(1 for item in breaks if item["k"] == "contra") == len(eventos) - a_favor


def test_el_marcador_de_constitucion_lleva_lo_que_hay_que_auditar(run: ImpulseRun) -> None:
    item = build_payload(run)["impulses"][H4]["constitutions"][0]
    assert set(item) >= {"id", "x", "y", "d", "a", "e", "a1", "a2", "body", "limbo"}


def test_el_payload_no_lleva_texto_montado(run: ImpulseRun) -> None:
    """Las etiquetas se componen en el navegador: con M15 de ocho años, mandar
    el texto ya hecho serían decenas de megabytes."""
    payload = build_payload(run)
    assert "hover" not in payload["bars"][H4]
    assert set(payload["bars"][H4]) == {
        "truncated", "total", "t", "o", "h", "l", "c", "rsi"
    }
    # Guardarraíl de tamaño, no un presupuesto ajustado: con el texto montado en
    # Python esto pasaba de 200 bytes por vela, y ocho años de M15 son ~200.000.
    total_velas = sum(bar_counts(payload).values())
    assert payload_size(payload) < 100 * total_velas


def test_el_recorte_conserva_las_velas_mas_recientes(run: ImpulseRun) -> None:
    completo = build_payload(run, max_bars=0)
    recortado = build_payload(run, max_bars=50)
    assert recortado["bars"][M15]["truncated"] is True
    assert recortado["bars"][M15]["total"] == completo["bars"][M15]["total"]
    assert recortado["bars"][M15]["t"] == completo["bars"][M15]["t"][-50:]


# --- HTML -------------------------------------------------------------------


def test_no_quedan_marcadores_sin_sustituir(run: ImpulseRun) -> None:
    template = (ASSETS / "impulse_explorer.html").read_text(encoding="utf-8")
    marcadores = set(re.findall(r"__[A-Z][A-Z_]*__", template))
    assert marcadores, "la plantilla debería tener marcadores"

    html = render_explorer(run)
    assert not {marcador for marcador in marcadores if marcador in html}


def test_todos_los_controles_que_busca_el_javascript_estan_en_la_plantilla() -> None:
    """El DOM simulado de los tests declara los elementos a mano, así que un
    identificador mal escrito en la plantilla se le escaparía: aquí se compara
    contra el HTML de verdad."""
    script = (ASSETS / "impulse_explorer.js").read_text(encoding="utf-8")
    template = (ASSETS / "impulse_explorer.html").read_text(encoding="utf-8")
    buscados = set(re.findall(r'getElementById\("([^"]+)"\)', script))
    declarados = set(re.findall(r'id="([^"]+)"', template))

    assert buscados, "el explorador tiene que buscar sus controles"
    assert not buscados - declarados


def test_la_cabecera_declara_el_reparto_y_el_hash(run: ImpulseRun) -> None:
    html = render_explorer(run)
    assert "lado bid" in html
    # El Diario ya no va dentro de H4: cada uno declara lo que dibuja de verdad.
    assert "H4: H4" in html
    assert "H4: H4 + Diario" not in html
    assert "M15: H4" in html
    assert "M5: H4" in html
    assert run.config_hash in html


def test_el_html_es_autocontenido(run: ImpulseRun) -> None:
    html = render_explorer(run, max_bars=100)
    assert "Plotly" in html
    assert "<script src=" not in html
    assert "<link " not in html


def test_el_json_embebido_no_puede_cerrar_la_etiqueta_script(run: ImpulseRun) -> None:
    html = render_explorer(run)
    bloque = html.split('id="explorer-data"')[1].split("</script>")[0]
    assert "</" not in bloque


# --- Ejecución real del JavaScript ------------------------------------------


@pytest.fixture
def variants(run: ImpulseRun) -> tuple[ModeVariant, ...]:
    """Las tres corridas de R-36 sobre las mismas velas de la fixture."""
    series = {timeframe: analysis.bars for timeframe, analysis in run.analyses.items()}
    built = []
    for mode in LegStartMode:
        tuned = replace(run.config, rules=replace(run.config.rules, leg_start_mode=mode))
        variant_run = DetectDominantImpulses(tuned).execute(series)
        built.append(ModeVariant(run=variant_run, lateralization=measure(variant_run)))
    return tuple(built)


def _draw(
    run: ImpulseRun,
    tmp_path: Path,
    variants: Sequence[ModeVariant] = (),
) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede ejecutar el JavaScript")

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            build_payload(
                run,
                lateralization=measure(run),
                variants=variants,
            ),
            default=str,
        ),
        encoding="utf-8",
    )
    stub = Path(__file__).parent / "explorer_dom_stub.js"
    output = subprocess.run(
        [node, str(stub), str(ASSETS / "impulse_explorer.js"), str(payload_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(output.stdout)


def _step(resultado: dict, label: str) -> dict:
    return next(step for step in resultado["steps"] if step["label"] == label)


def test_el_explorador_se_dibuja_sin_errores(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)

    assert not resultado["unknownElements"], (
        f"el explorador busca elementos que la plantilla no define: {resultado['unknownElements']}"
    )
    assert resultado["chartTabs"] == ["Diario", "H4", "H1", "M15", "M5"]
    assert resultado["presetLabels"][0] == "Todo"

    todo = _step(resultado, "todo")["plot"]
    assert todo["target"] == "chart"
    assert todo["yTickFormat"] == ".4f"
    assert todo["shapes"] > 0, "el limbo debe salir sombreado"
    assert "impulsos dibujados" in _step(resultado, "todo")["notes"]


def test_los_botones_dicen_que_impulso_dibuja_cada_grafico(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    titulos = dict(zip(resultado["chartTabs"], resultado["chartTitles"], strict=True))
    assert titulos["H4"] == "Dibuja el impulso de H4. Atajo de teclado: 4"
    assert titulos["M15"] == "Dibuja el impulso de H4. Atajo de teclado: m"
    assert titulos["M5"] == "Dibuja el impulso de H4. Atajo de teclado: 5"


def _ids_dibujados(step: dict) -> set[str]:
    nombres = [trace["name"] for trace in step["plot"]["traces"]]
    return {
        " ".join(nombre.split()[:2]) for nombre in nombres if nombre.startswith("ID ")
    }


def test_cada_grafico_dibuja_el_impulso_que_le_toca(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """En el reparto por defecto NINGÚN gráfico lleva contexto.

    El Diario se dibuja sólo en su gráfico: en H4 no se ve nada suyo. H1, M15 y
    M5 no tienen ID propio, así que lo que llevan es el de H4 y es su principal.
    """
    resultado = _draw(run, tmp_path)
    esperado = {
        "grafico-D": {"ID Diario"},
        "grafico-H4": {"ID H4"},
        "grafico-H1": {"ID H4"},
        "grafico-M15": {"ID H4"},
        "grafico-M5": {"ID H4"},
    }
    for label, temporalidades in esperado.items():
        paso = _step(resultado, label)
        assert _ids_dibujados(paso) == temporalidades, (label, _trace_names(paso))


def test_el_contexto_va_punteado_y_el_principal_continuo(
    context_run: ImpulseRun, tmp_path: Path
) -> None:
    """Con el Diario de contexto sobre H4: el suyo continuo, el del Diario punteado."""
    trazas = _step(_draw(context_run, tmp_path), "grafico-H4")["plot"]["traces"]
    propias = [t for t in trazas if t["name"].startswith("ID H4")]
    contexto = [t for t in trazas if t["name"].startswith("ID Diario")]

    assert propias and contexto
    assert all(t["dash"] == "solid" for t in propias)
    assert all(t["dash"] == "dot" for t in contexto)
    assert all("(contexto)" in t["name"] for t in contexto)


def test_en_h4_no_se_dibuja_nada_del_diario(run: ImpulseRun, tmp_path: Path) -> None:
    """El Diario es sólo para sí mismo: ni su ID ni su marco en H4."""
    resultado = _draw(run, tmp_path)
    nombres = _trace_names(_step(resultado, f"marco-de-{H4}"))

    assert nombres
    assert not [nombre for nombre in nombres if "Diario" in nombre], nombres


def test_los_marcadores_son_solo_del_impulso_principal(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """En M15 y en M5 los marcadores son los de H4, que es el único impulso que se ve."""
    resultado = _draw(run, tmp_path)
    for grafico in (M15, M5):
        nombres = [t["name"] for t in _step(resultado, f"grafico-{grafico}")["plot"]["traces"]]
        assert "Constitución H4" in nombres, grafico
        assert not any(nombre.startswith(f"Constitución {grafico}") for nombre in nombres)


def test_las_capas_se_rehacen_al_cambiar_de_grafico(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "grafico-H4")["layerLabels"] == ["ID H4 (principal)"]
    assert _step(resultado, "grafico-M15")["layerLabels"] == ["ID H4 (principal)"]
    assert _step(resultado, "grafico-M5")["layerLabels"] == ["ID H4 (principal)"]


def test_el_grafico_con_contexto_lleva_una_casilla_por_temporalidad(
    context_run: ImpulseRun, tmp_path: Path
) -> None:
    """Con dos ID en el gráfico, cada uno con su casilla y dicho cuál manda."""
    resultado = _draw(context_run, tmp_path)
    assert _step(resultado, "grafico-H4")["layerLabels"] == [
        "ID H4 (principal)",
        "ID Diario (contexto)",
    ]


def test_apagar_el_impulso_principal_deja_el_contexto(
    context_run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(context_run, tmp_path)
    con = _step(resultado, "con-contexto")["plot"]
    sin = _step(resultado, "sin-principal")["plot"]
    principal = con["traces"][1]["name"].split()[1]

    assert not any(t["name"].startswith("ID " + principal) for t in sin["traces"])
    assert any("(contexto)" in t["name"] for t in sin["traces"])
    assert sin["shapes"] == 0, "sin impulso principal no hay limbo que sombrear"


def test_el_conmutador_de_velas_a_lineas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "todo")["plot"]["traces"][0]["type"] == "candlestick"
    lineas = _step(resultado, "lineas")["plot"]
    assert lineas["traces"][0]["type"] == "scatter"
    assert lineas["traces"][0]["name"].startswith("Cierres")


def test_apagar_el_limbo_quita_el_sombreado(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "todo")["plot"]["shapes"] > 0
    assert _step(resultado, "sin-limbo")["plot"]["shapes"] == 0


def test_el_detalle_muestra_utc_y_la_zona_de_la_sesion(
    run: ImpulseRun, tmp_path: Path
) -> None:
    detalle = _step(_draw(run, tmp_path), "todo")["plot"]["hover"]
    assert "UTC" in detalle
    assert session_label(run.config.reporting.session_timezone) in detalle
    assert re.search(r"O \d+\.\d{4} · H \d+\.\d{4}", detalle)


@pytest.mark.parametrize(
    ("timezone", "escrito"),
    [("Etc/GMT+4", "UTC-4"), ("Etc/GMT-3", "UTC+3"), ("America/New_York", "America/New_York")],
)
def test_la_zona_de_desfase_fijo_se_escribe_con_su_signo(
    run: ImpulseRun, timezone: str, escrito: str
) -> None:
    """`Etc/GMT+4` ES el UTC-4: el nombre IANA lleva el signo al revés y al lado
    de una hora se leería justo como lo contrario. Las plazas van tal cual."""
    reporting = replace(run.config.reporting, session_timezone=timezone)
    otra = replace(run, config=replace(run.config, reporting=reporting))

    meta = build_payload(otra)["meta"]

    assert meta["sessionTimezone"] == timezone
    assert meta["sessionTimezoneLabel"] == escrito


# --- Navegación por fechas ---------------------------------------------------


def test_el_preset_recorta_la_ventana(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    todo = _step(resultado, "todo")
    corto = _step(resultado, "preset-corto")

    assert corto["plot"]["bars"] < todo["plot"]["bars"]
    assert corto["from"] > todo["from"]
    assert corto["to"] == todo["to"]


def test_los_pasos_recorren_el_historico_sin_solaparse(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    actual = _step(resultado, "preset-corto")
    anterior = _step(resultado, "ventana-anterior")
    siguiente = _step(resultado, "ventana-siguiente")

    assert anterior["from"] < actual["from"]
    assert anterior["to"] < actual["to"]
    assert (siguiente["from"], siguiente["to"]) == (actual["from"], actual["to"])
    assert anterior["plot"]["lastBar"] <= actual["plot"]["firstBar"]


def test_la_ventana_no_se_sale_del_historico(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    for step in resultado["steps"]:
        tiempos = payload["bars"][step["chart"]]["t"]
        primera = (epoch + pd.Timedelta(minutes=tiempos[0])).strftime("%Y-%m-%d")
        ultima = (epoch + pd.Timedelta(minutes=tiempos[-1])).strftime("%Y-%m-%d")
        assert step["from"] >= primera
        assert step["to"] <= ultima


# --- Capas nuevas y auditoría ciega (F.1, F.2, F.3) --------------------------


def _trace_names(step: dict) -> list[str]:
    return [trace["name"] for trace in step["plot"]["traces"]]


def test_los_contactos_viajan_al_explorador(run: ImpulseRun) -> None:
    payload = build_payload(run, lateralization=measure(run))
    contactos = payload["impulses"][H4]["contacts"]
    assert contactos, "la capa de contactos necesita datos que dibujar"
    assert {contacto["k"] for contacto in contactos} <= {"mecha", "fallida"}
    assert {contacto["s"] for contacto in contactos} <= {"superior", "inferior"}
    # La rotura real ya tiene su marcador: no se repite como contacto.
    assert all(contacto["k"] != "real" for contacto in contactos)


def test_sin_medicion_de_contactos_la_capa_va_vacia(run: ImpulseRun) -> None:
    assert build_payload(run)["impulses"][H4]["contacts"] == []


def test_la_capa_de_contactos_esta_apagada_hasta_que_se_enciende(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    apagada = _trace_names(_step(resultado, "preset-corto"))
    encendida = _trace_names(_step(resultado, "con-contactos"))

    assert not any(nombre.startswith("TOQUE_MECHA") for nombre in apagada)
    assert any(nombre.startswith("TOQUE_MECHA") for nombre in encendida)


def test_el_nivel_50_es_una_linea_punteada(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    con_nivel = _step(resultado, "con-nivel-50")["plot"]["traces"]
    nivel = [trace for trace in con_nivel if trace["name"] == "Nivel 50 %"]

    assert nivel, "el nivel del 50 % debe dibujarse al encender su casilla"
    assert nivel[0]["dash"] == "dot"
    assert "Nivel 50 %" not in _trace_names(_step(resultado, "con-contactos"))


def test_la_auditoria_ciega_deja_solo_las_velas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    ciega = _step(resultado, "ciega")

    assert len(ciega["plot"]["traces"]) == 1, "no puede quedar ninguna capa del motor"
    assert ciega["plot"]["traces"][0]["type"] == "candlestick"
    assert ciega["plot"]["shapes"] == 0, "el sombreado del limbo delataría el resultado"
    assert "AUDITORÍA CIEGA" in ciega["notes"]
    assert "semilla 4242" in ciega["notes"]


def test_revelar_devuelve_las_capas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    ciega = _step(resultado, "ciega")
    revelada = _step(resultado, "revelada")

    assert len(revelada["plot"]["traces"]) > len(ciega["plot"]["traces"])
    assert (revelada["from"], revelada["to"]) == (ciega["from"], ciega["to"])
    assert "semilla 4242" in revelada["notes"]


def test_la_misma_semilla_reabre_la_misma_ventana(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    primera = _step(resultado, "ciega")
    repetida = _step(resultado, "ciega-misma-semilla")

    assert (repetida["from"], repetida["to"]) == (primera["from"], primera["to"])


def test_salir_de_la_ciega_restaura_el_rango(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "con-nivel-50")
    fuera = _step(resultado, "fuera-de-la-ciega")

    assert (fuera["from"], fuera["to"]) == (antes["from"], antes["to"])
    assert "AUDITORÍA CIEGA" not in fuera["notes"]


# --- R-36 · Parte B: pegar las líneas, filtrar ID y navegar ------------------


def test_cada_nivel_viaja_con_la_vela_que_lo_define(run: ImpulseRun) -> None:
    """B.1 necesita saber dónde arranca el tramo punteado de cada línea."""
    payload = build_payload(run)
    impulso = run.analyses[H4].published[0]
    registro = payload["impulses"][H4]["list"][0]
    epoch = pd.Timestamp("1970-01-01", tz="UTC")

    assert epoch + pd.Timedelta(minutes=registro["xa"]) == pd.Timestamp(impulso.ts_anchor)
    assert epoch + pd.Timedelta(minutes=registro["xe"]) == pd.Timestamp(impulso.ts_extreme)


def test_la_vela_que_define_cada_nivel_es_anterior_a_la_constitucion(run: ImpulseRun) -> None:
    """Si no lo fuera, el tramo punteado no existiría y B.1 no tendría sentido."""
    for timeframe in run.analyses:
        for registro in build_payload(run)["impulses"][timeframe]["list"]:
            assert registro["xa"] < registro["x0"]
            assert registro["xe"] < registro["x0"]


def test_cada_id_se_dibuja_en_dos_tramos(run: ImpulseRun, tmp_path: Path) -> None:
    """El punteado va de la vela que define el nivel a la constitución; el sólido,
    de la constitución al fin del ID."""
    trazas = _step(_draw(run, tmp_path), "grafico-H4")["plot"]["traces"]
    previos = [t for t in trazas if t["name"].startswith("Nivel previo a la constitución")]
    vigentes = [t for t in trazas if t["name"].startswith("ID H4")]

    assert previos, "falta el tramo anterior a la constitución"
    assert all(traza["dash"] == "dot" for traza in previos)
    assert all(traza["dash"] == "solid" for traza in vigentes)


def test_la_linea_no_se_extiende_mas_alla_del_fin_del_id(run: ImpulseRun) -> None:
    payload = build_payload(run)
    ultima = pd.Timestamp(pd.DatetimeIndex(run.analyses[H4].bars.index)[-1])
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    for impulso, registro in zip(
        run.analyses[H4].published, payload["impulses"][H4]["list"], strict=True
    ):
        fin = pd.Timestamp(impulso.ts_end) if impulso.ts_end is not None else ultima
        assert epoch + pd.Timedelta(minutes=registro["x1"]) == fin


def test_el_filtro_de_id_visibles_viene_en_actual_mas_anterior(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    assert resultado["visibleLabels"] == ["Actual", "Actual + anterior", "Todos"]
    assert _step(resultado, "ids-por-defecto")["visibleMode"] == "pair"


def test_el_filtro_de_id_visibles_recorta_lo_que_se_dibuja(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """`actual` dibuja menos que `actual + anterior`, y éste menos que `todos`."""
    resultado = _draw(run, tmp_path)

    def puntos(label: str) -> int:
        return sum(
            trace["points"]
            for trace in _step(resultado, label)["plot"]["traces"]
            if trace["name"].startswith("ID ") or trace["name"].startswith("Nivel previo")
        )

    assert puntos("ids-current") < puntos("ids-pair") < puntos("ids-all")


def test_el_filtro_de_id_visibles_lo_dice_en_las_notas(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El propietario tiene que ver que está mirando un recorte de dibujo."""
    resultado = _draw(run, tmp_path)
    assert "filtro de dibujo" in _step(resultado, "ids-current")["notes"]
    assert "siguen en los datos y en los informes" in _step(resultado, "ids-pair")["notes"]
    assert "filtro de dibujo" not in _step(resultado, "ids-all")["notes"]


def test_las_flechas_del_teclado_mueven_la_ventana(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    partida = _step(resultado, "ventana-siguiente")
    izquierda = _step(resultado, "teclado-izquierda")
    derecha = _step(resultado, "teclado-derecha")

    assert izquierda["from"] < partida["from"]
    assert (derecha["from"], derecha["to"]) == (partida["from"], partida["to"])


def test_las_flechas_no_roban_el_teclado_a_los_campos(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con el foco en un campo, las flechas mueven el cursor, no el gráfico."""
    resultado = _draw(run, tmp_path)
    derecha = _step(resultado, "teclado-derecha")
    en_campo = _step(resultado, "teclado-en-un-campo")

    assert (en_campo["from"], en_campo["to"]) == (derecha["from"], derecha["to"])


def test_las_teclas_d_4_1_m_5_cambian_la_temporalidad(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Una tecla por gráfico: d = Diario, 4 = H4, 1 = H1, m = M15, 5 = M5."""
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "teclado-tf-h4")["chart"] == H4
    assert _step(resultado, "teclado-tf-m15")["chart"] == M15
    assert _step(resultado, "teclado-tf-m5")["chart"] == M5
    assert _step(resultado, "teclado-tf-h1")["chart"] == H1
    assert _step(resultado, "teclado-tf-diario")["chart"] == DAILY


def test_las_teclas_de_temporalidad_no_roban_el_teclado_a_los_campos(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con el foco en un campo, «d» escribe una letra: el gráfico no se mueve."""
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "teclado-tf-en-un-campo")["chart"] == H1


# --- R-36 · los tres modos dentro del mismo explorador ----------------------


def test_sin_variantes_el_payload_no_lleva_modos(run: ImpulseRun) -> None:
    """Un explorador de un solo modo sigue siendo exactamente el de antes."""
    payload = build_payload(run)
    assert payload["modes"] == []
    assert payload["byMode"] == {}


def test_las_tres_corridas_viajan_en_el_mismo_fichero(
    run: ImpulseRun, variants: tuple[ModeVariant, ...]
) -> None:
    payload = build_payload(run, variants=variants)
    activo = run.config.rules.leg_start_mode.value

    assert [mode["id"] for mode in payload["modes"]] == [m.value for m in LegStartMode]
    # El modo activo no se repite: sus impulsos ya están en `impulses`.
    assert set(payload["byMode"]) == {m.value for m in LegStartMode} - {activo}
    for variant in variants:
        block = payload["impulses"] if variant.mode == activo else payload["byMode"][variant.mode]
        for timeframe, analysis in variant.run.analyses.items():
            assert len(block[timeframe]["list"]) == len(analysis.published)


def test_las_velas_no_se_repiten_por_modo(
    run: ImpulseRun, variants: tuple[ModeVariant, ...]
) -> None:
    """Son idénticas en los tres modos: repetirlas triplicaría el fichero."""
    con = build_payload(run, variants=variants)
    sin = build_payload(run)
    assert con["bars"] == sin["bars"]


def test_cada_modo_declara_su_hash_y_sus_recuentos(
    run: ImpulseRun, variants: tuple[ModeVariant, ...]
) -> None:
    """El explorador enseña esas cifras; no las cuenta él."""
    payload = build_payload(run, variants=variants)
    for mode, variant in zip(payload["modes"], variants, strict=True):
        assert mode["hash"] == variant.run.config_hash
        assert mode["impulses"] == sum(
            len(analysis.impulses) for analysis in variant.run.analyses.values()
        )
        assert mode["wrong"] == sum(
            1
            for analysis in variant.run.analyses.values()
            for impulse in analysis.impulses
            if impulse.extreme_on_counter_bar
        )
    # Los hash distinguen los tres modos: ninguna salida puede confundirse.
    assert len({mode["hash"] for mode in payload["modes"]}) == len(LegStartMode)


def test_el_modo_base_es_el_de_la_corrida(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, variants)
    assert resultado["modeLabels"] == ["L1", "L2", "L3"]
    assert _step(resultado, "ids-all")["legStartMode"] == (
        run.config.rules.leg_start_mode.value
    )


def test_cambiar_de_modo_cambia_los_impulsos_dibujados(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, variants)
    dibujado = {}
    for mode in LegStartMode:
        step = _step(resultado, "modo-" + mode.value)
        assert step["legStartMode"] == mode.value
        dibujado[mode.value] = sum(
            trace["points"]
            for trace in step["plot"]["traces"]
            if trace["name"].startswith("ID ")
        )
    assert len(set(dibujado.values())) > 1, f"los tres modos dibujan lo mismo: {dibujado}"


def test_cada_modo_lleva_su_limbo(
    run: ImpulseRun, variants: tuple[ModeVariant, ...]
) -> None:
    """Mezclar el limbo de un modo con los impulsos de otro sería incoherente."""
    payload = build_payload(run, variants=variants)
    activo = run.config.rules.leg_start_mode.value
    tramos = {
        variant.mode: (
            payload["impulses"] if variant.mode == activo else payload["byMode"][variant.mode]
        )[H4]["limbo"]
        for variant in variants
    }
    assert len({len(item) for item in tramos.values()}) > 1


def test_el_modo_activo_se_declara_en_las_notas(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path, variants), "modo-L2_siguiente_barra")["notes"]
    assert "LEG_START_MODE = L2_siguiente_barra" in notas
    assert "extremo sobre vela de color contrario" in notas


def test_el_extremo_de_color_contrario_viaja_marcado(run: ImpulseRun) -> None:
    payload = build_payload(run)
    for timeframe, analysis in run.analyses.items():
        registros = {item["id"]: item for item in payload["impulses"][timeframe]["list"]}
        for impulso in analysis.published:
            assert registros[impulso.id_num]["w"] is impulso.extreme_on_counter_bar
            assert registros[impulso.id_num]["ec"] == impulso.extreme_bar_direction.value


def test_la_marca_de_r36_se_puede_apagar(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, variants)
    apagada = _trace_names(_step(resultado, "sin-marca-r36"))
    assert not any("R-36" in nombre for nombre in apagada)


def test_la_marca_de_r36_aparece_donde_hay_extremos_contrarios(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    """La fixture reproduce el patrón del histórico: L2 los multiplica, L3 los borra."""
    por_modo = {
        variant.mode: sum(
            1
            for analysis in variant.run.analyses.values()
            for impulse in analysis.published
            if impulse.extreme_on_counter_bar
        )
        for variant in variants
    }
    assert por_modo["L2_siguiente_barra"] > 0
    assert por_modo["L3_extremo_solo_color_valido"] == 0

    resultado = _draw(run, tmp_path, variants)
    con_marca = _trace_names(_step(resultado, "modo-L2_siguiente_barra"))
    sin_marca = _trace_names(_step(resultado, "modo-L3_extremo_solo_color_valido"))

    assert any("R-36" in nombre for nombre in con_marca), con_marca
    assert not any("R-36" in nombre for nombre in sin_marca), sin_marca


# --- G.1 · Replay desde una fecha -------------------------------------------
#
# El replay reproduce la historia paso a paso y en cada paso sólo puede dibujar
# lo que el motor ya sabía. Como las velas se etiquetan al inicio del intervalo,
# eso se mide por el CIERRE: la vela de `t` cierra en `t + span`. Todo lo que se
# comprueba aquí es esa frontera; si se colara un impulso constituido más tarde,
# el explorador estaría enseñando el futuro y la auditoría no valdría nada.

EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _minute(stamp: str) -> int:
    return int((pd.Timestamp(stamp, tz="UTC") - EPOCH) // pd.Timedelta(minutes=1))


def _replay_steps(resultado: dict) -> list[dict]:
    return [
        step
        for step in resultado["steps"]
        if step["label"].startswith("replay-") and step["label"] != "replay-fuera"
    ]


def _clock(payload: dict, step: dict) -> int:
    """Minuto en que cerró la última vela dibujada: el presente de ese paso."""
    return _minute(step["plot"]["lastBar"]) + payload["spans"][step["chart"]]


def _fine_clock(step: dict) -> int:
    """El reloj que declaran las notas: hasta qué minuto se ha visto el mercado.

    No es el cierre de la última vela dibujada cuando hay una a medio armar, y es
    lo único que tiene que coincidir entre temporalidades.
    """
    marca = re.search(r"reloj (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC", step["notes"])
    assert marca is not None, f"el replay no declara su reloj: {step['notes']}"
    return _minute(marca.group(1))


def _points(step: dict, name: str) -> int:
    return sum(trace["points"] for trace in step["plot"]["traces"] if trace["name"] == name)


def _a_su_hora(marcas: list[dict], desde: int, reloj: int, span: int) -> list[dict]:
    """Las marcas de la ventana cuya vela ya había cerrado a esa hora."""
    return [item for item in marcas if desde <= item["x"] and item["x"] + span <= reloj]


def test_cada_temporalidad_declara_cuanto_dura_su_vela(run: ImpulseRun) -> None:
    """Sin la duración no se puede saber cuándo cerró una vela, y sin eso el
    replay no sabe qué puede dibujar."""
    spans = build_payload(run)["spans"]
    assert spans == {DAILY: 1440, H4: 240, H1: 60, M15: 15, M5: 5}


def test_el_replay_arranca_en_la_fecha_elegida(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "antes-del-replay")
    inicio = _step(resultado, "replay-inicio")

    assert "REPLAY" in inicio["notes"]
    assert inicio["plot"]["lastBar"] < antes["plot"]["lastBar"]
    # La ventana arranca justo antes del día pedido: el primer paso descubre su
    # primera vela en vez de enseñarla ya hecha.
    assert inicio["plot"]["lastBar"][:10] <= inicio["replayDate"]


def test_el_replay_no_dibuja_nada_que_el_motor_no_supiera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    pasos = _replay_steps(resultado)
    assert pasos, "el recorrido tiene que pasar por el replay"

    for paso in pasos:
        dibujado = paso["plot"]["maxEngineX"]
        assert dibujado is not None, f"{paso['label']}: no se dibujó ninguna capa"
        assert _minute(dibujado) <= _clock(payload, paso), paso["label"]


def test_el_replay_ensena_cada_marca_a_su_hora(run: ImpulseRun, tmp_path: Path) -> None:
    """Ni una constitución ni una rotura antes de que cierre su vela, y todas las
    que ya cerraron dentro de la ventana."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    comprobados = 0

    for paso in _replay_steps(resultado):
        if paso["chart"] != H4:
            continue
        reloj = _clock(payload, paso)
        desde = _minute(paso["plot"]["firstBar"])
        span = payload["spans"][H4]
        marcas = payload["impulses"][H4]

        assert _points(paso, "Constitución H4") == len(
            _a_su_hora(marcas["constitutions"], desde, reloj, span)
        ), paso["label"]
        assert _points(paso, "ROTURA_A_FAVOR H4") + _points(paso, "ROTURA_EN_CONTRA H4") == len(
            _a_su_hora(marcas["breaks"], desde, reloj, span)
        ), paso["label"]
        comprobados += 1

    assert comprobados, "ningún paso del replay dibujó los marcadores de H4"


def test_la_linea_del_id_vigente_se_corta_en_el_presente(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El ID en curso llega hasta el reloj y ni un minuto más: dibujarlo entero
    delataría cuándo se rompe."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    span = payload["spans"][H4]
    comprobados = 0

    for paso in _replay_steps(resultado):
        if paso["chart"] != H4:
            continue
        reloj = _clock(payload, paso)
        vigente = [
            impulse
            for impulse in payload["impulses"][H4]["list"]
            if impulse["x0"] + span <= reloj < impulse["x1"]
        ]
        if not vigente:
            continue
        assert _minute(paso["plot"]["maxEngineX"]) == reloj, paso["label"]
        comprobados += 1

    assert comprobados, "en ningún paso había un ID vigente que recortar"


def test_el_id_no_esta_dibujado_antes_de_que_lo_constituya_su_vela(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El caso que decide si el replay sirve para auditar: la constitución que se
    está a punto de cruzar no puede aparecer hasta que su vela cierra."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    span = payload["spans"][H4]
    marcas = payload["impulses"][H4]["constitutions"]
    marca = marcas[len(marcas) // 2]

    pasos = [
        step for step in resultado["steps"] if step["label"].startswith("replay-nacimiento-")
    ]

    def dibujables(paso: dict) -> set[int]:
        visibles = _a_su_hora(
            marcas, _minute(paso["plot"]["firstBar"]), _clock(payload, paso), span
        )
        return {item["id"] for item in visibles}

    antes = [paso for paso in pasos if _clock(payload, paso) < marca["x"] + span]
    despues = [paso for paso in pasos if _clock(payload, paso) >= marca["x"] + span]
    assert antes and despues, "el recorrido no llega a cruzar la constitución"

    ultimo, primero = antes[-1], despues[0]
    assert marca["id"] not in dibujables(ultimo)
    assert marca["id"] in dibujables(primero)
    # Y lo dibujado coincide con lo que se podía dibujar a cada lado del cruce.
    assert _points(ultimo, "Constitución H4") == len(dibujables(ultimo))
    assert _points(primero, "Constitución H4") == len(dibujables(primero))


def test_la_vela_se_arma_con_la_temporalidad_inferior(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    inicio = _step(resultado, "replay-inicio")
    pasos = [_step(resultado, f"replay-paso-{numero}") for numero in range(1, 5)]
    intermedios = payload["spans"][H4] // payload["spans"][H1] - 1

    assert "Vela en formación" not in _trace_names(inicio)
    for numero, paso in enumerate(pasos[:intermedios], start=1):
        assert "Vela en formación" in _trace_names(paso)
        assert f"{numero} de {intermedios + 1} velas de H1" in paso["notes"]
        # Mientras se arma, la vela no ha cerrado: el motor no se entera.
        assert paso["plot"]["lastBar"] == inicio["plot"]["lastBar"]

    cierre = pasos[intermedios]
    assert cierre["plot"]["lastBar"] > inicio["plot"]["lastBar"]
    assert "Vela en formación" not in _trace_names(cierre)


def test_sin_vela_en_formacion_cada_paso_es_una_vela_entera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    apagada = _step(resultado, "replay-sin-formacion")
    siguiente = _step(resultado, "replay-vela-entera")

    assert "Vela en formación" not in _trace_names(apagada)
    assert siguiente["plot"]["lastBar"] > apagada["plot"]["lastBar"]


def test_el_paso_atras_deshace_el_ultimo(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "replay-paso-5")
    atras = _step(resultado, "replay-atras")
    teclado = _step(resultado, "replay-teclado")

    assert atras["notes"] == antes["notes"]
    # Y la flecha derecha vuelve a avanzar, como el botón.
    assert teclado["notes"] == _step(resultado, "replay-paso-6")["notes"]


def test_la_reproduccion_se_enciende_y_se_apaga(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "replay-reproduciendo")["replayPlay"] == "⏸"
    assert _step(resultado, "replay-pausado")["replayPlay"] == "▶"


def test_cambiar_de_temporalidad_no_mueve_el_reloj(run: ImpulseRun, tmp_path: Path) -> None:
    """El mismo instante visto en otra temporalidad: la última vela cerrada de la
    nueva, ni una más."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    origen = _step(resultado, "replay-sin-formacion")
    destino = _step(resultado, "replay-otra-temporalidad")

    assert destino["chart"] == DAILY
    reloj = _clock(payload, origen)
    cierre = _clock(payload, destino)
    assert cierre <= reloj
    # El histórico tiene hueco de fin de semana, así que la vela diaria de
    # después puede empezar mucho más tarde que el cierre de ésta. Lo que se
    # comprueba es que no haya ninguna posterior que ya hubiera cerrado.
    span = payload["spans"][DAILY]
    posteriores = [t for t in payload["bars"][DAILY]["t"] if cierre < t + span <= reloj]
    assert not posteriores, "hay una vela diaria posterior que ya había cerrado"


def test_durante_el_replay_los_controles_de_periodo_se_apagan(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con el cursor mandando, un selector de fechas vivo mentiría."""
    resultado = _draw(run, tmp_path)
    assert all(paso["replayLocked"] for paso in _replay_steps(resultado))
    assert not _step(resultado, "antes-del-replay")["replayLocked"]
    assert not _step(resultado, "replay-fuera")["replayLocked"]


def test_el_replay_deja_aire_a_la_derecha(run: ImpulseRun, tmp_path: Path) -> None:
    """Sin margen la última vela quedaría pegada al borde y el eje daría un salto
    en cada paso."""
    inicio = _step(_draw(run, tmp_path), "replay-inicio")
    rango = inicio["plot"]["xRange"]

    assert rango is not None
    assert rango[0] <= inicio["plot"]["firstBar"]
    assert rango[1] > inicio["plot"]["lastBar"]


def test_salir_del_replay_devuelve_el_periodo_de_partida(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "antes-del-replay")
    fuera = _step(resultado, "replay-fuera")

    assert (fuera["from"], fuera["to"]) == (antes["from"], antes["to"])
    assert fuera["plot"]["bars"] == antes["plot"]["bars"]
    assert fuera["plot"]["xRange"] is None
    assert "REPLAY" not in fuera["notes"]


def test_lo_avanzado_en_una_temporalidad_se_ve_en_las_demas(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El reloj es uno solo. Lo que llevas corrido dentro de la vela de H4 tiene
    que aparecer en el diario como su vela a medio armar; si no, saltar de
    temporalidad devolvía el gráfico al último cierre y parecía un reinicio."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    h4_antes = _step(resultado, "reloj-h4-avanzado")
    diario = _step(resultado, "reloj-en-diario")

    assert diario["chart"] == DAILY
    assert "Vela en formación" in _trace_names(diario), "el diario volvió al cierre de ayer"
    formadas = re.search(r"vela en formación con (\d+) de \d+ velas de H4", diario["notes"])
    assert formadas is not None and int(formadas.group(1)) >= 1

    # El reloj es el mismo: lo que el diario no puede enseñar no se olvida.
    assert _fine_clock(diario) == _fine_clock(h4_antes)
    # Y lo dibujado sigue sin adelantarse a lo que el motor sabía.
    assert _clock(payload, diario) <= _fine_clock(diario)


def test_el_reloj_no_se_degrada_al_pasar_por_el_diario(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El diario no tiene resolución para la hora y cuarto que llevas corrida en
    H1, pero el reloj no la olvida: volver a H1 devuelve el mismo minuto. Antes
    el reloj vivía en el par (vela, sub-vela) del gráfico que mirabas, así que
    pasar por el diario recortaba el reloj para todas las demás."""
    resultado = _draw(run, tmp_path)
    en_h1 = _step(resultado, "reloj-fino-h1")
    diario = _step(resultado, "reloj-fino-en-diario")
    vuelta = _step(resultado, "reloj-fino-de-vuelta")

    assert _fine_clock(en_h1) == _fine_clock(diario) == _fine_clock(vuelta)
    assert vuelta["notes"] == en_h1["notes"], "volver a H1 no devolvió el mismo paso"
    # Y el diario enseña el día en curso a medio armar, no el cierre de ayer a
    # secas: el avance de H1 se ve también ahí.
    assert "Vela en formación" in _trace_names(diario)


def test_volver_a_la_temporalidad_de_partida_no_retrocede(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "reloj-h4-avanzado")
    vuelta = _step(resultado, "reloj-de-vuelta-en-h4")

    assert vuelta["chart"] == H4
    assert vuelta["plot"]["lastBar"] == antes["plot"]["lastBar"]


# El encuadre manual (G.2). Auditar de cerca exige acercar el zoom y quedarse
# ahí: si cada paso del replay devolviera el gráfico a su escala, no se podría
# mirar una vela concreta mientras se avanza.


def _width(step: dict) -> pd.Timedelta:
    x = step["plot"]["xRange"]
    return pd.Timestamp(x[1]) - pd.Timestamp(x[0])


def test_el_zoom_manual_sobrevive_a_los_pasos_del_replay(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    pasos = [
        _step(resultado, f"replay-zoom-{etiqueta}")
        for etiqueta in ("ancho-1", "ancho-2", "estrecho-1", "estrecho-2")
    ]

    for paso in pasos:
        assert paso["plot"]["yRange"] == [1.05, 1.35], "el eje de precios se rehízo"
        assert "encuadre manual" in paso["notes"]
        assert not paso["zoomFree"], "«Ajustar» tiene que quedar disponible"
    # La anchura es la que fijó el propietario y no cambia de un paso al otro.
    assert _width(pasos[0]) == _width(pasos[1])
    assert _width(pasos[2]) == _width(pasos[3])
    assert _width(pasos[2]) < _width(pasos[0])


def test_el_encuadre_manual_sigue_al_presente(run: ImpulseRun, tmp_path: Path) -> None:
    """Conservar el zoom no puede dejar la vela nueva fuera de la pantalla: la
    ventana se desplaza justo un paso, sin cambiar de escala."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    uno = _step(resultado, "replay-zoom-estrecho-1")
    dos = _step(resultado, "replay-zoom-estrecho-2")

    vela = pd.Timedelta(minutes=payload["spans"][H4])
    corrimiento = pd.Timestamp(dos["plot"]["xRange"][0]) - pd.Timestamp(uno["plot"]["xRange"][0])
    assert corrimiento == vela
    for paso in (uno, dos):
        ultima = pd.Timestamp(paso["plot"]["lastBar"])
        assert pd.Timestamp(paso["plot"]["xRange"][0]) <= ultima
        assert ultima <= pd.Timestamp(paso["plot"]["xRange"][1])


def test_el_paso_atras_no_mueve_el_encuadre_manual(run: ImpulseRun, tmp_path: Path) -> None:
    """Mientras el presente siga dentro, retroceder no toca el eje."""
    resultado = _draw(run, tmp_path)
    atras = _step(resultado, "replay-zoom-atras")
    ultimo = _step(resultado, "replay-zoom-estrecho-2")

    assert atras["plot"]["xRange"] == ultimo["plot"]["xRange"]
    assert atras["plot"]["lastBar"] < ultimo["plot"]["lastBar"]


def test_el_encuadre_manual_pide_las_velas_que_tapa(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Alejar el zoom más allá de las velas a la vista no puede dejar media
    pantalla vacía: el recorte se amplía hasta cubrir lo que se ve."""
    resultado = _draw(run, tmp_path)
    sin_zoom = _step(resultado, "replay-zoom-sin-zoom")
    ancho = _step(resultado, "replay-zoom-ancho-1")

    assert ancho["plot"]["bars"] > sin_zoom["plot"]["bars"]
    assert pd.Timestamp(ancho["plot"]["firstBar"]) <= pd.Timestamp(ancho["plot"]["xRange"][0])


def test_ajustar_devuelve_el_encuadre_automatico(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    suelto = _step(resultado, "replay-zoom-suelto")

    assert suelto["zoomFree"]
    assert suelto["plot"]["yRange"] is None
    assert "encuadre manual" not in suelto["notes"]
    # Y vuelve el encuadre del replay, con su aire a la derecha.
    assert pd.Timestamp(suelto["plot"]["xRange"][1]) > pd.Timestamp(suelto["plot"]["lastBar"])


def test_fuera_del_replay_el_encuadre_manual_tambien_manda(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Encender una capa no devuelve el gráfico a su sitio; pedir otro tramo de
    historia sí, porque ahí el encuadre anterior ya no significa nada."""
    resultado = _draw(run, tmp_path)
    capa = _step(resultado, "zoom-fuera-del-replay")
    preset = _step(resultado, "zoom-suelto-por-el-preset")

    assert capa["plot"]["xRange"] is not None
    assert capa["plot"]["yRange"] == [1.05, 1.35]
    assert not capa["zoomFree"]
    assert preset["plot"]["xRange"] is None
    assert preset["plot"]["yRange"] is None
    assert preset["zoomFree"]


# --- El marco del ID -----------------------------------------------------------
#
# Cada ID lleva su recuadro, de la vela que lo constituye a la que lo mata y de
# su ancla a su extremo.


def _marcos(nombres: list[str]) -> list[str]:
    return [nombre for nombre in nombres if nombre.startswith("Marco ID")]


def _rectangulos(segments: list[list]) -> list[tuple[str, str, float, float]]:
    """Los recuadros de una traza de marco, como (x0, x1, mínimo, máximo).

    Cada uno viaja como cinco puntos y un nulo, en el orden que cierra el
    polígono: (x0, ancla) (x1, ancla) (x1, extremo) (x0, extremo) (x0, ancla).
    """
    cajas = []
    for start in range(0, len(segments), 6):
        esquinas = segments[start : start + 5]
        if len(esquinas) < 5 or any(punto[0] is None for punto in esquinas):
            continue
        alturas = [punto[1] for punto in esquinas]
        cajas.append(
            (esquinas[0][0], esquinas[1][0], min(alturas), max(alturas))
        )
    return cajas


def _globos_de_marco(step: dict, timeframe: str) -> list[str]:
    return [
        caption
        for trace in step["plot"]["traces"]
        if trace["name"] == f"Marco ID {timeframe}"
        for caption in (trace["captions"] or [])
        if caption
    ]


def test_el_marco_del_id_se_dibuja_y_se_apaga(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    con = _trace_names(_step(resultado, "marco-por-defecto"))
    sin = _trace_names(_step(resultado, "marco-apagado"))

    assert _marcos(con), con
    assert not _marcos(sin), sin


def test_el_marco_va_de_la_constitucion_a_la_muerte_y_del_ancla_al_extremo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El recuadro es el ID entero: sus cuatro esquinas salen del payload."""
    paso = _step(_draw(run, tmp_path), "marco-por-defecto")
    payload = build_payload(run, lateralization=measure(run))
    por_id = {impulse["id"]: impulse for impulse in payload["impulses"][H4]["list"]}
    trazas = [
        trace
        for trace in paso["plot"]["traces"]
        if trace["name"] == f"Marco ID {H4}"
    ]

    assert trazas, _trace_names(paso)
    rectangulos = _rectangulos(trazas[0]["segments"])
    assert rectangulos
    for x0, x1, lo, hi in rectangulos:
        dibujado = [
            impulse
            for impulse in por_id.values()
            if _minute(x0) == impulse["x0"] and impulse["a"] in (lo, hi)
        ]
        assert dibujado, (x0, x1, lo, hi)
        impulse = dibujado[0]
        assert (lo if impulse["a"] == hi else hi) == impulse["e"]
        assert _minute(x1) <= impulse["x1"]


def test_el_marco_de_un_id_vivo_llega_al_presente_y_lo_dice(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """`x1` de un ID vigente es la última vela, no la de su muerte: fechar ahí
    una muerte que no ha ocurrido sería mentir."""
    payload = build_payload(run, lateralization=measure(run))
    vivos = [
        impulse for impulse in payload["impulses"][H4]["list"] if impulse["v"]
    ]
    assert vivos, "la fixture tiene que dejar un ID vivo al final del histórico"

    globos = _globos_de_marco(_step(_draw(run, tmp_path), "marco-por-defecto"), H4)
    vivos_dibujados = [globo for globo in globos if "SIGUE VIVO" in globo]

    assert vivos_dibujados, globos[:3]
    assert all("nº " in globo and "empieza en la vela de" in globo for globo in globos)


def test_el_marco_obedece_el_filtro_de_id_visibles(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """A diferencia de las cajas de zona, el marco ES el ID: si el filtro deja
    un ID fuera, su recuadro se va con él."""
    resultado = _draw(run, tmp_path)

    def recuadros(label: str) -> int:
        return sum(
            trace["points"]
            for trace in _step(resultado, label)["plot"]["traces"]
            if trace["name"].startswith("Marco ID")
        )

    assert 0 < recuadros("marco-ids-current") < recuadros("marco-ids-all")
    assert recuadros("marco-ids-current") <= recuadros("marco-ids-pair")


def test_la_auditoria_ciega_tampoco_ensena_el_marco(
    run: ImpulseRun, tmp_path: Path
) -> None:
    ciega = _step(_draw(run, tmp_path), "ciega")
    assert len(ciega["plot"]["traces"]) == 1, _trace_names(ciega)


def test_las_notas_dicen_de_quien_es_cada_marco(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "marco-por-defecto")["notes"]

    assert "marcos de ID dibujados" in notas
    assert "cada temporalidad con su color" in notas


def test_el_replay_no_dibuja_ninguna_capa_antes_de_tiempo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La misma frontera para todas, marco incluido: nada más allá del reloj."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run, lateralization=measure(run))
    pasos = _replay_steps(resultado)
    assert pasos, "el recorrido tiene que pasar por el replay"

    for paso in pasos:
        dibujado = paso["plot"]["maxEngineX"]
        assert dibujado is not None, paso["label"]
        assert _minute(dibujado) <= _clock(payload, paso), paso["label"]


def test_el_modo_activo_no_se_duplica_en_el_payload(
    run: ImpulseRun, variants: tuple[ModeVariant, ...]
) -> None:
    """Repetirlo costaba 4,5 MB de fichero en el histórico real."""
    payload = build_payload(run, variants=variants)
    activo = run.config.rules.leg_start_mode.value

    assert activo not in payload["byMode"]
    assert len(payload["byMode"]) == len(LegStartMode) - 1
    # Y aun así el fichero con los tres modos pesa menos que tres ficheros: las
    # velas, que son la mayor parte, viajan una sola vez.
    assert payload_size(payload) < 3 * payload_size(build_payload(run))


# --- G.3 · escalar arrastrando sobre los ejes --------------------------------
#
# Como en cualquier gráfico de trading: apretar sobre los precios y arrastrar
# comprime o estira la vertical; apretar sobre las fechas abre o cierra el
# gráfico de lado. Plotly, sobre el eje, hace pan y no escala, así que el gesto
# es propio y aquí se comprueba que escala de verdad y que el encuadre aguanta.


def test_arrastrar_sobre_los_ejes_escala_el_grafico(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "ejes-antes-de-escalar")["plot"]
    precios = _step(resultado, "eje-precios-arrastrado")["lastRelayout"]
    fechas = _step(resultado, "eje-fechas-arrastrado")["lastRelayout"]

    base_y = [float(v) for v in antes["yRange"]]
    nuevo_y = [float(v) for v in precios["yaxis.range"]]
    assert precios["yaxis.autorange"] is False
    # Hacia abajo se ve MÁS rango —las velas se hacen pequeñas— y el centro
    # se queda donde estaba: el gesto escala, no desplaza.
    assert nuevo_y[1] - nuevo_y[0] > base_y[1] - base_y[0]
    assert sum(nuevo_y) / 2 == pytest.approx(sum(base_y) / 2, abs=1e-6)

    base_x = [_minute(v) for v in antes["xRange"]]
    nuevo_x = [_minute(v) for v in fechas["xaxis.range"]]
    # Hacia la izquierda entran más velas por el mismo sitio, y la última no se
    # mueve: el borde derecho es el presente.
    assert nuevo_x[1] - nuevo_x[0] > base_x[1] - base_x[0]
    assert nuevo_x[1] == base_x[1]


def test_la_escala_tomada_en_los_ejes_sobrevive_al_redibujo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Si no se guardara, encender una capa devolvería el gráfico a su sitio."""
    resultado = _draw(run, tmp_path)
    fechas = _step(resultado, "eje-fechas-arrastrado")["lastRelayout"]
    precios = _step(resultado, "eje-precios-arrastrado")["lastRelayout"]
    despues = _step(resultado, "ejes-tras-redibujar")["plot"]

    assert [_minute(v) for v in despues["xRange"]] == [
        _minute(v) for v in fechas["xaxis.range"]
    ]
    assert [float(v) for v in despues["yRange"]] == pytest.approx(
        [float(v) for v in precios["yaxis.range"]]
    )


# --- H.1 · el ruido ----------------------------------------------------------
#
# Con todas las capas encendidas el gráfico llega a llevar marcas de sobra sobre
# las mismas veinte velas: se ve QUE pasan cosas, no CUÁLES. «Ruido» es un preset
# de las casillas que ya existen —ni una capa nueva ni un cálculo nuevo— y el
# explorador abre en «Limpio».

#: Lo que el nivel «Limpio» deja fuera y lo que deja puesto. Es el contrato del
#: preset: si cambia, tiene que cambiarse aquí a la vez.
LIMPIO_APAGADO = (
    "layer-limbo",
    "layer-contacts",
    "layer-mid",
    "layer-wrong",
    "layer-sessions",
)
#: El marco del ID no lo apaga ningún nivel: es dónde empieza y dónde acaba el
#: ID, que es justo lo que el nivel «Limpio» deja a la vista.
LIMPIO_ENCENDIDO = ("layer-marks", "layer-frame")


def _marcas_del_motor(step: dict) -> int:
    """Cuántos puntos dibuja el motor en ese paso, sin contar las velas."""
    return sum(
        trace["points"]
        for trace in step["plot"]["traces"]
        if not trace["name"].startswith(("Velas ", "Cierres ", "Vela en formación"))
    )


def test_el_explorador_abre_en_el_nivel_limpio(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Lo que se abre por defecto es el gráfico legible, no el que lo lleva todo."""
    resultado = _draw(run, tmp_path)
    salida = _step(resultado, "ruido-de-salida")

    assert salida["noiseLevel"] == "clean"
    assert salida["visibleMode"] == "current"
    for casilla in LIMPIO_APAGADO:
        assert salida["boxes"][casilla] is False, casilla
    for casilla in LIMPIO_ENCENDIDO:
        assert salida["boxes"][casilla] is True, casilla


def test_el_nivel_limpio_dibuja_menos_que_el_normal(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Y «Todo» más que los dos: los tres niveles se distinguen en el dibujo."""
    resultado = _draw(run, tmp_path)

    limpio = _marcas_del_motor(_step(resultado, "ruido-limpio"))
    normal = _marcas_del_motor(_step(resultado, "ruido-normal"))
    todo = _marcas_del_motor(_step(resultado, "ruido-todo"))

    assert 0 < limpio < normal <= todo, (limpio, normal, todo)


def test_tocar_una_casilla_deja_el_nivel_sin_dueno(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El control dice lo que hay puesto, no lo que se pulsó la última vez."""
    resultado = _draw(run, tmp_path)
    a_mano = _step(resultado, "ruido-a-mano")

    assert _step(resultado, "ruido-normal")["noiseLevel"] == "normal"
    assert a_mano["noiseLevel"] is None
    assert a_mano["boxes"]["layer-mid"] is True
    assert "RUIDO: a mano" in a_mano["notes"]


def test_el_estado_dice_que_capas_ha_apagado_el_nivel(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Un gráfico con menos marcas tiene que decir cuáles se ha callado: si no,
    la ausencia de una capa se lee como que ahí no pasó nada."""
    resultado = _draw(run, tmp_path)
    notas = _step(resultado, "ruido-limpio")["notes"]

    assert "RUIDO: Limpio" in notas
    assert "capas apagadas:" in notas
    for nombre in ("limbo", "contactos", "nivel 50 %", "sesiones de Asia y Londres"):
        assert nombre in notas, nombre
    assert "siguen en los datos y en los informes" in notas
    assert "todas las capas encendidas" in _step(resultado, "ruido-todo")["notes"]


# --- I.1 · el simulador de entradas ------------------------------------------
#
# Dos botones que arman, un clic que planta la caja y arrastres que la mueven.
# Es lo único del gráfico que dibuja el propietario y no el motor, así que lo
# que se comprueba aquí es que cae donde se pulsa, que se mueve como se dice y
# que el estado deja claro que no es una operación.


def _caja(step: dict, numero: int = 1) -> dict[str, dict]:
    """Las tres formas de UNA caja —la 1 si no se dice otra—, con el nombre sin
    el número (`sim-1-entrada` → `sim-entrada`): caben dos y cada una lleva el
    suyo."""
    prefijo = f"sim-{numero}-"
    return {
        "sim-" + forma["name"][len(prefijo) :]: forma
        for forma in step["plot"]["sim"]
        if forma["name"].startswith(prefijo)
    }


def _niveles(step: dict, numero: int = 1) -> dict[str, float]:
    caja = _caja(step, numero)
    return {
        "entrada": caja["sim-entrada"]["y0"],
        "objetivo": caja["sim-objetivo"]["y1"],
        "stop": caja["sim-riesgo"]["y1"],
    }


def _rr(medido: float) -> str:
    """El R:R medido, escrito como lo escribe el explorador: hasta dos decimales,
    sin ceros de relleno y con coma."""
    texto = f"{medido:.2f}".rstrip("0").rstrip(".")
    return "1:" + texto.replace(".", ",")


def test_el_boton_arma_y_lo_dice_antes_de_plantar_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    armado = _step(_draw(run, tmp_path), "sim-armado")

    assert armado["simArmed"] == "long"
    assert not armado["plot"]["sim"], "armar no puede dibujar todavía ninguna caja"
    assert "SIMULADOR ARMADO (largo)" in armado["notes"]
    assert armado["simCursor"] == "crosshair"


def test_escape_desarma_sin_plantar(run: ImpulseRun, tmp_path: Path) -> None:
    desarmado = _step(_draw(run, tmp_path), "sim-desarmado")

    assert desarmado["simArmed"] is None
    assert not desarmado["plot"]["sim"]
    assert "SIMULADOR" not in desarmado["notes"]


def test_la_caja_se_planta_en_el_precio_del_clic(run: ImpulseRun, tmp_path: Path) -> None:
    """El recorrido pulsa en el centro del encuadre que él mismo ha fijado
    (1,05 → 1,35), así que la entrada tiene que caer justo en 1,2000."""
    largo = _step(_draw(run, tmp_path), "sim-largo")
    niveles = _niveles(largo)

    assert niveles["entrada"] == pytest.approx(1.2000, abs=1e-4)
    assert niveles["stop"] < niveles["entrada"] < niveles["objetivo"]
    assert _caja(largo)["sim-entrada"]["y1"] == niveles["entrada"], "la entrada es una línea"


def test_la_caja_dice_los_pips_de_cada_lado_y_el_ratio(
    run: ImpulseRun, tmp_path: Path
) -> None:
    caja = _caja(_step(_draw(run, tmp_path), "sim-largo"))

    # I.2: cada caja dice además lo que se juega con el capital puesto —50 $ al
    # 2 % son 1,00 $ de riesgo—, que es la razón de dibujarla.
    assert caja["sim-objetivo"]["label"] == "objetivo 300 pips · +2,00 $"
    assert caja["sim-riesgo"]["label"] == "riesgo 150 pips · -1,00 $"
    assert caja["sim-entrada"]["label"] == "LARGO · R:R 1:2"


def test_las_notas_dicen_que_la_caja_no_es_una_operacion(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "sim-largo")["notes"]

    assert "simulación LARGO" in notas
    assert "entrada 1.2000" in notas
    assert "stop 1.1850 (150 pips)" in notas
    assert "objetivo 1.2300 (300 pips)" in notas
    assert "· R:R 1:2 ·" in notas
    assert "R:R automático: es la distancia que hay dibujada" in notas
    assert "ES DIBUJO A MANO" in notas
    assert "no hay orden" in notas


def test_arrastrar_el_stop_no_mueve_ni_la_entrada_ni_el_objetivo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El stop es una decisión sola: mueve el riesgo y deja el objetivo donde
    está. El R:R no se defiende, se vuelve a medir, y por eso alejar el stop lo
    baja: es lo que ha pasado en el dibujo."""
    resultado = _draw(run, tmp_path)
    paso = _step(resultado, "sim-stop-arrastrado")
    antes = _niveles(_step(resultado, "sim-largo"))
    despues = _niveles(paso)

    assert despues["entrada"] == antes["entrada"]
    assert despues["stop"] < antes["stop"], "el arrastre iba hacia abajo: más riesgo"
    assert despues["objetivo"] == antes["objetivo"], "el objetivo no lo mueve el stop"
    # El dinero en juego no lo mueve el stop: el riesgo es del capital y lo que
    # cambia con los pips es el tamaño de la posición, no lo que se arriesga.
    # Los pips salen de la geometría del recorrido —40 px arrastrados sobre el
    # panel del precio, que con el RSI abajo se queda con el 72 % del alto—.
    assert _caja(paso)["sim-riesgo"]["label"] == "riesgo 403 pips · -1,00 $"
    medido = (despues["objetivo"] - despues["entrada"]) / (
        despues["entrada"] - despues["stop"]
    )
    assert medido < 1, "con más riesgo y el mismo objetivo el R:R baja"
    assert _caja(paso)["sim-entrada"]["label"] == "LARGO · R:R " + _rr(medido)


def test_arrastrar_la_entrada_mueve_la_caja_entera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La distancia al stop y al objetivo es lo que se acaba de decidir:
    recolocar la entrada no puede cambiarla por su cuenta."""
    resultado = _draw(run, tmp_path)
    antes = _niveles(_step(resultado, "sim-stop-arrastrado"))
    despues = _niveles(_step(resultado, "sim-entrada-arrastrada"))

    salto = despues["entrada"] - antes["entrada"]
    assert salto > 0, "el arrastre iba hacia arriba"
    assert despues["stop"] - antes["stop"] == pytest.approx(salto, abs=1e-4)
    assert despues["objetivo"] - antes["objetivo"] == pytest.approx(salto, abs=1e-4)
    assert (
        _caja(_step(resultado, "sim-entrada-arrastrada"))["sim-entrada"]["label"]
        == _caja(_step(resultado, "sim-stop-arrastrado"))["sim-entrada"]["label"]
    )


def test_en_corto_el_objetivo_va_por_debajo_de_la_entrada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    corto = _step(_draw(run, tmp_path), "sim-corto")
    niveles = _niveles(corto, 2)

    assert niveles["objetivo"] < niveles["entrada"] < niveles["stop"]
    assert _caja(corto, 2)["sim-entrada"]["label"].startswith("CORTO")
    assert "simulación CORTO" in corto["notes"]


def test_quitar_borra_la_caja_y_lo_que_decia_de_ella(
    run: ImpulseRun, tmp_path: Path
) -> None:
    quitado = _step(_draw(run, tmp_path), "sim-quitado")

    assert not quitado["plot"]["sim"]
    assert quitado["simClearDisabled"], "sin caja no hay nada que quitar"
    assert "simulación" not in quitado["notes"]


def test_la_caja_simulada_no_cuenta_como_capa_del_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Las tres formas de la caja no son sombreado del limbo: los recuentos de
    formas del motor tienen que seguir diciendo lo mismo con ella puesta."""
    resultado = _draw(run, tmp_path)

    assert _step(resultado, "sim-largo")["plot"]["shapes"] == (
        _step(resultado, "sim-quitado")["plot"]["shapes"]
    )


def test_arrastrar_el_objetivo_vuelve_a_medir_el_rr(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Manda la distancia que se ve: mover el objetivo cambia el R:R y no toca
    ni la entrada ni el stop."""
    resultado = _draw(run, tmp_path)
    antes = _niveles(_step(resultado, "sim-entrada-arrastrada"))
    paso = _step(resultado, "sim-objetivo-a-mano")
    despues = _niveles(paso)

    assert (despues["stop"], despues["entrada"]) == (antes["stop"], antes["entrada"])
    assert despues["objetivo"] < antes["objetivo"], "el arrastre acercaba el objetivo"
    medido = (despues["objetivo"] - despues["entrada"]) / (
        despues["entrada"] - despues["stop"]
    )
    assert _caja(paso)["sim-entrada"]["label"] == "LARGO · R:R " + _rr(medido)
    assert "automático" in paso["notes"]


def test_el_panel_mide_el_rr_de_la_caja_dibujada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El R:R no se pone: sale de la distancia del stop al objetivo. Recién
    plantada da el 1:2 con el que nace la caja; con el objetivo colocado a ojo,
    lo que haya, aunque no sea un número redondo."""
    resultado = _draw(run, tmp_path)
    plantada = _step(resultado, "sim-largo")
    a_mano = _step(resultado, "sim-objetivo-a-mano")
    niveles = _niveles(a_mano)
    medido = (niveles["objetivo"] - niveles["entrada"]) / (
        niveles["entrada"] - niveles["stop"]
    )

    assert plantada["simReadout"] == "R:R 1:2 · automático"
    assert plantada["simReadoutSource"] == "auto"
    assert "150 pips de riesgo contra 300 pips de objetivo" in plantada[
        "simReadoutTitle"
    ]
    assert "No hay ratio que poner a mano" in plantada["simReadoutTitle"]

    assert a_mano["simReadoutSource"] == "auto"
    texto, fuente = a_mano["simReadout"].split(" · ")
    assert fuente == "automático"
    # Un objetivo colocado a ojo no cae en un número redondo: el panel dice la
    # distancia que hay, con dos decimales y sin ceros de relleno.
    assert texto == "R:R " + _rr(medido)
    assert float(texto.replace("R:R 1:", "").replace(",", ".")) == pytest.approx(
        medido, abs=0.005
    )


def test_el_rr_se_mide_mientras_se_coloca_el_objetivo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El número se mira COLOCANDO: si sólo se pusiera al día al soltar, llegaría
    tarde a la única decisión que hay que tomar."""
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "sim-objetivo-a-mano")
    paso = _step(resultado, "sim-objetivo-en-vuelo")

    assert paso["simReadoutEnVuelo"] == paso["simReadout"]
    assert paso["simReadoutEnVuelo"] != antes["simReadout"]


def test_sin_caja_el_panel_no_dice_ningun_rr(run: ImpulseRun, tmp_path: Path) -> None:
    quitado = _step(_draw(run, tmp_path), "sim-quitado")

    assert quitado["simReadout"] == "R:R —"
    assert quitado["simReadoutSource"] == ""


def test_la_caja_nace_con_el_objetivo_a_dos_riesgos(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """No es un R:R elegido: es de dónde parte el arrastre, porque la caja tiene
    que salir con algo dibujado. Vale igual para la de después: cada caja nueva
    nace ahí, se haya arrastrado lo que se haya arrastrado en la anterior."""
    corto = _step(_draw(run, tmp_path), "sim-corto")
    niveles = _niveles(corto, 2)

    riesgo = niveles["stop"] - niveles["entrada"]
    assert niveles["entrada"] - niveles["objetivo"] == pytest.approx(2 * riesgo, abs=1e-4)
    assert _caja(corto, 2)["sim-entrada"]["label"] == "CORTO · caja 2 (activa) · R:R 1:2"
    assert corto["simReadout"] == "R:R 1:2 · automático · caja 2"


# --- I.1 · dos cajas a la vez -------------------------------------------------
#
# El propietario suele poner dos posiciones, así que caben dos cajas: largas,
# cortas o una de cada, numeradas por orden de plantado. Una es la ACTIVA —la
# última plantada o agarrada— y es la que cobra la cuenta y la que quita
# «Quitar». Con las dos puestas, los botones de armar se apagan.


def test_la_segunda_caja_se_planta_junto_a_la_primera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    corto = _step(resultado, "sim-corto")

    assert len(corto["plot"]["sim"]) == 6, "dos cajas son seis formas"
    assert _caja(corto, 1)["sim-entrada"]["label"] == "LARGO · caja 1 · R:R " + _rr(
        _ratio(_niveles(_step(resultado, "sim-objetivo-en-vuelo")))
    )
    assert "simulación LARGO (caja 1)" in corto["notes"]
    assert "simulación CORTO (caja 2, activa)" in corto["notes"]
    assert "caben dos cajas: la ACTIVA es la última plantada o agarrada" in corto["notes"]
    # La primera sigue donde estaba: plantar la segunda no la mueve.
    assert _niveles(corto, 1) == _niveles(_step(resultado, "sim-objetivo-en-vuelo"), 1)


def _ratio(niveles: dict[str, float]) -> float:
    return abs(niveles["objetivo"] - niveles["entrada"]) / abs(
        niveles["entrada"] - niveles["stop"]
    )


def test_quitar_se_lleva_la_activa_y_deja_la_otra(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    corto = _step(resultado, "sim-corto")
    una = _step(resultado, "sim-quitada-una")

    assert corto["simClearTitle"] == "Quita la caja activa (la 2); la otra se queda"
    assert len(una["plot"]["sim"]) == 3
    assert _caja(una, 1)["sim-entrada"]["label"].startswith("LARGO · R:R")
    assert _niveles(una, 1) == _niveles(corto, 1)
    # Sola, vuelve a hablar sin número: no hay con qué confundirla.
    assert "caja 1" not in una["notes"]
    assert una["simReadout"] == "R:R " + _rr(_ratio(_niveles(una))) + " · automático"
    assert not una["simClearDisabled"]
    assert una["simClearTitle"] == "Quita la caja simulada"


def test_con_dos_cajas_los_botones_de_armar_se_apagan_y_lo_dicen(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    dos = _step(resultado, "sim-dos-cajas")
    tercera = _step(resultado, "sim-tercera-no-cabe")

    assert len(dos["plot"]["sim"]) == 6
    assert dos["simButtonsDisabled"] == [True, True]
    assert all(
        title == "Ya hay dos cajas: cobra o quita una para plantar otra."
        for title in dos["simButtonTitles"]
    )
    assert dos["simReadout"] == "R:R 1:2 · automático · caja 2"
    # Con una sola, los botones están encendidos y con su título de siempre.
    una = _step(resultado, "cuenta-con-caja")
    assert una["simButtonsDisabled"] == [False, False]
    assert all("Caben dos" in title for title in una["simButtonTitles"])
    # Armar y pulsar con las dos puestas no planta nada ni arma nada.
    assert tercera["simArmed"] is None
    assert len(tercera["plot"]["sim"]) == 6
    assert tercera["plot"]["sim"] == dos["plot"]["sim"]


def test_agarrar_una_caja_la_vuelve_la_activa(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    dos = _step(resultado, "sim-dos-cajas")
    primera = _step(resultado, "sim-caja-1-activa")

    assert _caja(dos, 2)["sim-entrada"]["label"] == "LARGO · caja 2 (activa) · R:R 1:2"
    assert _caja(dos, 2)["sim-entrada"]["width"] > _caja(dos, 1)["sim-entrada"]["width"]
    assert _caja(primera, 1)["sim-entrada"]["label"] == "LARGO · caja 1 (activa) · R:R 1:2"
    assert _caja(primera, 2)["sim-entrada"]["label"] == "LARGO · caja 2 · R:R 1:2"
    assert primera["simReadout"] == "R:R 1:2 · automático · caja 1"
    assert "simulación LARGO (caja 1, activa)" in primera["notes"]
    # En este punto del recorrido la cuenta va a 100 $ con 5 $ fijos de riesgo.
    assert "la caja activa (la 1) se juega 5,00 $ para ganar 10,00 $" in primera["notes"]
    # Agarrarla sin moverla no la mueve.
    assert _niveles(primera, 1) == _niveles(dos, 1)


def test_cobrar_se_lleva_solo_la_activa(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "sim-caja-1-activa")
    cobrada = _step(resultado, "sim-cobrada-la-activa")
    devuelta = _step(resultado, "sim-devuelta-la-cobrada")

    assert _cuenta(cobrada)["summary"].startswith("110,00 $ · riesgo 5,00 $ · 1 operación")
    assert len(cobrada["plot"]["sim"]) == 3
    # La que queda es la que era la 2, ahora sola y activa.
    assert _niveles(cobrada, 1) == _niveles(antes, 2)
    assert not any(_cuenta(cobrada)["resultsDisabled"])
    assert cobrada["simButtonsDisabled"] == [False, False]
    # Deshacer la devuelve como caja 2 y activa, con su medida intacta.
    assert len(devuelta["plot"]["sim"]) == 6
    assert _niveles(devuelta, 2) == _niveles(antes, 1)
    assert _caja(devuelta, 2)["sim-entrada"]["label"].endswith("caja 2 (activa) · R:R 1:2")
    assert not _step(resultado, "sim-dos-quitadas")["plot"]["sim"]


# --- I.2 · la cuenta simulada -------------------------------------------------
#
# Un capital, un riesgo por operación y tres botones que apuntan la caja que hay
# dibujada. El recorrido del stub trabaja siempre sobre la misma caja —1:2 exacto
# sobre el encuadre 1,05 → 1,35—, así que las cifras se pueden comprobar a mano:
# 50 $ al 2 % son 1,00 $ de riesgo y 2,00 $ de objetivo, en la primera operación
# y en todas las demás: el porcentaje es del capital de partida y no compone.


def _cuenta(step: dict) -> dict:
    return step["account"]


def test_la_cuenta_sale_con_su_capital_y_sin_operaciones(
    run: ImpulseRun, tmp_path: Path
) -> None:
    cuenta = _cuenta(_step(_draw(run, tmp_path), "cuenta-sin-nada"))

    assert cuenta["initial"] == "50"
    assert cuenta["mode"] == "percent"
    assert cuenta["risk"] == "2"
    assert cuenta["summary"] == "50,00 $ · riesgo 1,00 $ · 0 operaciones"
    assert cuenta["undoDisabled"] and cuenta["resetDisabled"] and cuenta["copyDisabled"]


def test_sin_caja_dibujada_no_hay_nada_que_apuntar(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Lo que se cobra es SIEMPRE una caja concreta, con su R:R y su fecha."""
    resultado = _draw(run, tmp_path)

    assert all(_cuenta(_step(resultado, "cuenta-sin-nada"))["resultsDisabled"])
    assert not any(_cuenta(_step(resultado, "cuenta-con-caja"))["resultsDisabled"])
    assert "CUENTA SIMULADA" not in _step(resultado, "cuenta-sin-nada")["notes"]


def test_la_caja_dice_lo_que_se_juega_con_el_capital_puesto(
    run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-con-caja")

    assert _caja(paso)["sim-objetivo"]["label"].endswith("· +2,00 $")
    assert _caja(paso)["sim-riesgo"]["label"].endswith("· -1,00 $")
    assert "se juega 1,00 $ para ganar 2,00 $" in paso["notes"]


def test_una_ganada_suma_el_riesgo_por_el_ratio(run: ImpulseRun, tmp_path: Path) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-ganada")

    assert _cuenta(paso)["summary"] == "52,00 $ · riesgo 1,00 $ · 1 operación · +2,0 R"
    assert "capital 52,00 $ (partía de 50,00 $) · +2,00 $ (+4,0 %)" in paso["notes"]
    assert "1 operación apuntada (1 ganadas, 0 perdidas, 0 en break-even)" in paso["notes"]
    assert "acierto 100,0 %" in paso["notes"]
    # La caja se cobra y se va: dejarla puesta invita a apuntarla dos veces.
    assert not paso["plot"]["sim"]
    assert all(_cuenta(paso)["resultsDisabled"])


def test_el_riesgo_es_del_capital_de_partida_y_no_compone(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con 52,00 $ en la cuenta se sigue arriesgando el 2 % de los 50,00 $ que se
    pusieron: la apuesta no crece con las ganancias. Para subirla, se sube el
    capital."""
    paso = _step(_draw(run, tmp_path), "cuenta-perdida")

    assert _cuenta(paso)["summary"] == "51,00 $ · riesgo 1,00 $ · 2 operaciones · +1,0 R"
    assert "capital 51,00 $ (partía de 50,00 $) · +1,00 $ (+2,0 %)" in paso["notes"]
    assert "riesgo 2,0 % del capital de partida = 1,00 $ por operación" in paso["notes"]
    assert "acierto 50,0 %" in paso["notes"]
    assert "caída máxima 1,00 $ (1,9 %)" in paso["notes"]


def test_el_break_even_no_mueve_el_saldo_pero_cuenta_como_operacion(
    run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-break-even")

    assert _cuenta(paso)["summary"].startswith("51,00 $ · riesgo 1,00 $ · 3 operaciones")
    assert "3 operaciones apuntadas (1 ganadas, 1 perdidas, 1 en break-even)" in paso["notes"]
    # El acierto se cuenta sobre las decididas: un break-even no es un fallo.
    assert "acierto 50,0 % (el break-even no cuenta)" in paso["notes"]


def test_deshacer_devuelve_el_saldo_y_tambien_la_caja(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El error que se deshace suele ser haber pulsado el botón que no era:
    replantar el dibujo a mano para volver a cobrarlo sería perder la medida."""
    resultado = _draw(run, tmp_path)
    paso = _step(resultado, "cuenta-deshecha")

    assert _cuenta(paso)["summary"].startswith("51,00 $ · riesgo 1,00 $ · 2 operaciones")
    assert _niveles(paso) == _niveles(_step(resultado, "cuenta-con-caja"))


def test_copiar_se_lleva_la_configuracion_el_historial_y_las_estadisticas(
    run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-copiada")
    texto = paso["copiado"]

    assert "capital de partida 50,00 $ · riesgo 2,0 % del capital de partida" in texto
    assert "capital 51,00 $ · +1,00 $ (+2,0 %) · +1,0 R · 2 operaciones" in texto
    lineas = [linea for linea in texto.splitlines() if linea.startswith(("1 ", "2 "))]
    assert len(lineas) == 2
    assert "LARGO" in lineas[0] and "GANADA" in lineas[0] and "52,00 $" in lineas[0]
    assert "PERDIDA" in lineas[1] and "51,00 $" in lineas[1]
    assert "el motor no ve estas operaciones" in texto
    assert "historial copiado al portapapeles (2 operaciones)" in paso["notes"]


def test_cambiar_el_capital_vuelve_a_contar_la_curva_entera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """De cada operación se guarda su múltiplo de riesgo, no los euros: con 100 $
    la misma ganada y la misma perdida valen el doble."""
    paso = _step(_draw(run, tmp_path), "cuenta-capital-100")

    assert _cuenta(paso)["initial"] == "100"
    assert _cuenta(paso)["summary"] == "102,00 $ · riesgo 2,00 $ · 2 operaciones · +1,0 R"


def test_el_riesgo_en_dolares_fijos_no_compone(run: ImpulseRun, tmp_path: Path) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-riesgo-fijo")

    assert _cuenta(paso)["mode"] == "cash"
    assert _cuenta(paso)["summary"] == "105,00 $ · riesgo 5,00 $ · 2 operaciones · +1,0 R"
    assert "riesgo 5,00 $ fijos = 5,00 $ por operación" in paso["notes"]


def test_un_riesgo_imposible_no_se_acepta(run: ImpulseRun, tmp_path: Path) -> None:
    """El control no puede quedarse diciendo un riesgo que no está puesto."""
    paso = _step(_draw(run, tmp_path), "cuenta-riesgo-invalido")

    assert _cuenta(paso)["risk"] == "5"
    assert _cuenta(paso)["summary"].startswith("105,00 $ · riesgo 5,00 $")


def test_reiniciar_borra_las_operaciones_y_vuelve_al_capital_de_partida(
    run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(run, tmp_path), "cuenta-reiniciada")

    assert _cuenta(paso)["summary"] == "100,00 $ · riesgo 5,00 $ · 0 operaciones"
    assert _cuenta(paso)["undoDisabled"] and _cuenta(paso)["resetDisabled"]


def test_las_notas_dicen_que_la_cuenta_no_es_dinero(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "cuenta-ganada")["notes"]

    assert "NO ES DINERO" in notas
    assert "los apunta el propietario a mano" in notas
    assert "el motor no ve nada de esto" in notas


def _rgb(colour: str) -> tuple[int, int, int]:
    """El mismo tono, venga como `#rrggbb` o como `rgba(r,g,b,a)`."""
    if colour.startswith("#"):
        return tuple(int(colour[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
    numbers = colour[colour.index("(") + 1 : colour.index(")")].split(",")
    return tuple(int(value) for value in numbers[:3])  # type: ignore[return-value]


# --- I.3 · los recuadros a mano ------------------------------------------------
#
# Dos rectángulos —OB y FVG— que planta el PROPIETARIO para señalar dónde
# los ve. No los ha detectado nadie: en el proyecto no hay regla de ninguno de
# los tres. Lo que se comprueba aquí es que caen donde se pulsa, que se mueven
# como se dice, que cada nombre se distingue del otro y de todo lo que dibuja el
# motor, y que el estado deja claro de quién son.


def _recuadros(step: dict) -> list[dict]:
    return step["plot"]["rect"]


def _alto(recuadro: dict) -> float:
    return recuadro["y1"] - recuadro["y0"]


def test_el_boton_del_recuadro_arma_y_lo_dice_antes_de_plantar_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    armado = _step(_draw(run, tmp_path), "rect-armado")

    assert armado["rectArmed"] == "OB"
    assert not _recuadros(armado), "armar no puede dibujar todavía ningún recuadro"
    assert "RECUADRO DE OB ARMADO" in armado["notes"]
    assert armado["simCursor"] == "crosshair"


def test_escape_desarma_el_recuadro_sin_plantarlo(run: ImpulseRun, tmp_path: Path) -> None:
    desarmado = _step(_draw(run, tmp_path), "rect-desarmado")

    assert desarmado["rectArmed"] is None
    assert not _recuadros(desarmado)
    assert "ARMADO" not in desarmado["notes"]


def test_el_recuadro_se_planta_centrado_en_el_precio_del_clic(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El recorrido pulsa en el centro del encuadre que él mismo ha fijado
    (1,05 → 1,35), así que el recuadro tiene que salir centrado en 1,2000."""
    recuadros = _recuadros(_step(_draw(run, tmp_path), "rect-plantado"))

    assert len(recuadros) == 1
    recuadro = recuadros[0]
    centro = (recuadro["y0"] + recuadro["y1"]) / 2
    assert centro == pytest.approx(1.2000, abs=1e-3)
    assert recuadro["y1"] > recuadro["y0"], "el recuadro tiene alto"
    assert recuadro["x1"] > recuadro["x0"], "y ancho"
    assert recuadro["type"] == "rect"
    assert recuadro["label"] == "OB 1 (a mano)"


def test_el_recuadro_se_distingue_de_lo_que_dibuja_el_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Punteado y con un color que no usa ninguna capa calculada: lo que se ve
    así lo ha puesto una mano."""
    recuadro = _recuadros(_step(_draw(run, tmp_path), "rect-plantado"))[0]

    assert _rgb(recuadro["color"]) == _rgb(HAND_RECTS["OB"])
    assert recuadro["dash"] == "dot"
    del_motor = {_rgb(BULLISH), _rgb(BEARISH)} | {
        _rgb(colour) for colour in TIMEFRAME_COLORS.values()
    } | {_rgb(colour) for colour in PATTERN_COLORS.values()}
    a_mano = {_rgb(colour) for colour in HAND_RECTS.values()}
    assert len(a_mano) == 2, "cada nombre lleva su color"
    assert not a_mano & del_motor


def test_arrastrar_el_techo_no_mueve_el_suelo(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _recuadros(_step(resultado, "rect-plantado"))[0]
    despues = _recuadros(_step(resultado, "rect-techo-arrastrado"))[0]

    assert despues["y1"] > antes["y1"], "el arrastre iba hacia arriba"
    assert despues["y0"] == antes["y0"], "el suelo se queda donde estaba"
    assert (despues["x0"], despues["x1"]) == (antes["x0"], antes["x1"])


def test_arrastrar_por_dentro_mueve_el_recuadro_entero(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _recuadros(_step(resultado, "rect-techo-arrastrado"))[0]
    despues = _recuadros(_step(resultado, "rect-movido"))[0]

    assert despues["y1"] < antes["y1"] and despues["y0"] < antes["y0"], "bajó entero"
    assert _alto(despues) == pytest.approx(_alto(antes), abs=1e-3)
    assert despues["x0"] > antes["x0"] and despues["x1"] > antes["x1"], "y se fue a la derecha"


def test_se_pueden_marcar_recuadros_de_nombres_distintos(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """En un mismo gráfico hay el OB y el FVG: enseñarlos de uno en uno no dice
    lo que hay que decir, y cada nombre va con su color."""
    segundo = _step(_draw(run, tmp_path), "rect-segundo")
    recuadros = _recuadros(segundo)

    assert [recuadro["label"] for recuadro in recuadros] == [
        "OB 1 (a mano)",
        "FVG 1 (a mano)",
    ]
    assert _rgb(recuadros[0]["color"]) == _rgb(HAND_RECTS["OB"])
    assert _rgb(recuadros[1]["color"]) == _rgb(HAND_RECTS["FVG"])
    assert recuadros[1]["y1"] < recuadros[0]["y0"], "el segundo se plantó más abajo"
    assert "recuadros marcados a mano: 2 (1 de OB · 1 de FVG)" in segundo["notes"]


def test_los_recuadros_se_numeran_por_nombre(run: ImpulseRun, tmp_path: Path) -> None:
    """El segundo OB es «OB 2» aunque entre los dos se haya plantado un FVG: lo
    que se cuenta al mirarlos es cuántos hay de cada cosa."""
    tercero = _step(_draw(run, tmp_path), "rect-tercero")

    assert [recuadro["label"] for recuadro in _recuadros(tercero)] == [
        "OB 1 (a mano)",
        "FVG 1 (a mano)",
        "OB 2 (a mano)",
    ]
    assert "recuadros marcados a mano: 3 (2 de OB · 1 de FVG)" in tercero["notes"]


def test_quitar_el_ultimo_deja_los_demas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    deshecho = _step(resultado, "rect-deshecho")

    assert [recuadro["label"] for recuadro in _recuadros(deshecho)] == [
        "OB 1 (a mano)",
        "FVG 1 (a mano)",
    ]
    assert not deshecho["rectUndoDisabled"], "todavía quedan que quitar"


def test_quitar_todos_borra_los_recuadros_y_lo_que_decian(
    run: ImpulseRun, tmp_path: Path
) -> None:
    limpio = _step(_draw(run, tmp_path), "rect-limpio")

    assert not _recuadros(limpio)
    assert limpio["rectUndoDisabled"] and limpio["rectClearDisabled"], (
        "sin recuadros no hay nada que quitar"
    )
    assert "recuadros marcados a mano" not in limpio["notes"]


def test_las_notas_dicen_que_el_recuadro_no_lo_ha_detectado_el_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "rect-plantado")["notes"]

    assert "recuadros marcados a mano: 1 (1 de OB)" in notas
    assert "NO los ha detectado el motor" in notas
    assert "no hay regla de OB ni FVG en el proyecto" in notas


def test_los_recuadros_no_cuentan_como_capa_del_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Los rectángulos a mano no son sombreado del limbo: los recuentos de formas
    del motor tienen que seguir diciendo lo mismo con ellos puestos."""
    resultado = _draw(run, tmp_path)

    assert _step(resultado, "rect-tercero")["plot"]["shapes"] == (
        _step(resultado, "rect-limpio")["plot"]["shapes"]
    )


def test_sin_recuadros_los_botones_de_quitar_estan_apagados(
    run: ImpulseRun, tmp_path: Path
) -> None:
    vacio = _step(_draw(run, tmp_path), "rect-sin-nada")

    assert vacio["rectUndoDisabled"] and vacio["rectClearDisabled"]
    assert vacio["rectArmed"] is None


# --- I.4 · las líneas a mano ---------------------------------------------------
#
# Tres LÍNEAS —Diario, H4 y H1— que traza el PROPIETARIO para señalar el nivel
# que quiere explicar y de qué temporalidad es. No las ha calculado nadie: detrás
# no hay ninguna regla, y el color no es el que el motor usa por temporalidad. Lo
# que se comprueba aquí es que nacen horizontales donde se pulsa, que se inclinan
# y se mueven como se dice, que se distinguen de los recuadros y de todo lo que
# dibuja el motor, y que el estado deja claro de quién son. El alto y el bajo de
# Asia y de Londres ya no van aquí: los marca el motor (J.1).


def _lineas(step: dict) -> list[dict]:
    return step["plot"]["line"]


def _pendiente(linea: dict) -> float:
    return linea["y1"] - linea["y0"]


def test_el_boton_de_la_linea_arma_y_lo_dice_antes_de_trazar_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    armada = _step(_draw(run, tmp_path), "linea-armada")

    assert armada["lineArmed"] == DAILY
    assert not _lineas(armada), "armar no puede trazar todavía ninguna línea"
    assert "LÍNEA DE DIARIO ARMADA" in armada["notes"]
    assert armada["simCursor"] == "crosshair"


def test_escape_desarma_la_linea_sin_trazarla(run: ImpulseRun, tmp_path: Path) -> None:
    desarmada = _step(_draw(run, tmp_path), "linea-desarmada")

    assert desarmada["lineArmed"] is None
    assert not _lineas(desarmada)
    assert "ARMADA" not in desarmada["notes"]


def test_la_linea_nace_horizontal_al_precio_del_clic(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El recorrido pulsa en el centro del encuadre que él mismo ha fijado
    (1,05 → 1,35), así que la línea tiene que salir plana en 1,2000."""
    lineas = _lineas(_step(_draw(run, tmp_path), "linea-plantada"))

    assert len(lineas) == 1
    linea = lineas[0]
    assert linea["type"] == "line"
    assert linea["y0"] == pytest.approx(1.2000, abs=1e-3)
    assert linea["y1"] == linea["y0"], "nace horizontal"
    assert linea["x1"] > linea["x0"], "y con tramo"
    assert linea["label"] == "línea de Diario 1 (a mano)"


def test_la_linea_se_distingue_del_recuadro_y_de_lo_que_dibuja_el_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Continua y con un tono de la mano: lo punteado es recuadro y ninguna capa
    calculada usa estos colores. La línea del Diario NO va del color con el que
    el motor dibuja el Diario: se llaman igual, pero una la ha puesto una mano y
    la otra la ha calculado el motor."""
    resultado = _draw(run, tmp_path)
    linea = _lineas(_step(resultado, "linea-plantada"))[0]
    recuadro = _recuadros(_step(resultado, "rect-plantado"))[0]

    assert _rgb(linea["color"]) == _rgb(HAND_LINES[DAILY])
    assert _rgb(linea["color"]) != _rgb(TIMEFRAME_COLORS[DAILY])
    assert linea["dash"] is None, "la línea va continua"
    assert recuadro["dash"] == "dot", "y el recuadro punteado"
    del_motor = {_rgb(BULLISH), _rgb(BEARISH)} | {
        _rgb(colour) for colour in TIMEFRAME_COLORS.values()
    }
    a_mano = {_rgb(colour) for colour in HAND_LINES.values()}
    assert len(a_mano) == 3, "cada línea lleva su color"
    assert not a_mano & del_motor


def test_arrastrar_un_extremo_inclina_la_linea(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _lineas(_step(resultado, "linea-plantada"))[0]
    despues = _lineas(_step(resultado, "linea-inclinada"))[0]

    assert despues["y1"] > antes["y1"], "el arrastre subía el extremo derecho"
    assert despues["y0"] == antes["y0"], "el izquierdo se queda donde estaba"
    assert despues["x0"] == antes["x0"]


def test_arrastrar_el_trazo_mueve_la_linea_entera(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _lineas(_step(resultado, "linea-inclinada"))[0]
    despues = _lineas(_step(resultado, "linea-movida"))[0]

    assert despues["y0"] < antes["y0"] and despues["y1"] < antes["y1"], "bajó entera"
    assert _pendiente(despues) == pytest.approx(_pendiente(antes), abs=1e-3), (
        "moverla no puede cambiar cómo está inclinada"
    )
    assert despues["x0"] > antes["x0"] and despues["x1"] > antes["x1"], "y se fue a la derecha"


def test_se_trazan_lineas_de_colores_distintos(run: ImpulseRun, tmp_path: Path) -> None:
    """Marcar el nivel del Diario y el de H4 a la vez es justo para lo que están:
    cada una con su temporalidad y su color."""
    segunda = _step(_draw(run, tmp_path), "linea-segunda")
    lineas = _lineas(segunda)

    assert [linea["label"] for linea in lineas] == [
        "línea de Diario 1 (a mano)",
        "línea de H4 1 (a mano)",
    ]
    assert _rgb(lineas[0]["color"]) == _rgb(HAND_LINES[DAILY])
    assert _rgb(lineas[1]["color"]) == _rgb(HAND_LINES[H4])
    assert lineas[1]["y0"] > lineas[0]["y0"], "la segunda se trazó más arriba"
    assert "líneas marcadas a mano: 2 (1 Diario · 1 H4)" in segunda["notes"]


def test_las_lineas_se_numeran_por_temporalidad(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La segunda del Diario es «Diario 2» aunque entre las dos se haya trazado
    una de H4: lo que se cuenta al mirarlas es cuántas hay de cada una."""
    tercera = _step(_draw(run, tmp_path), "linea-tercera")

    assert [linea["label"] for linea in _lineas(tercera)] == [
        "línea de Diario 1 (a mano)",
        "línea de H4 1 (a mano)",
        "línea de Diario 2 (a mano)",
    ]
    assert "líneas marcadas a mano: 3 (2 Diario · 1 H4)" in tercera["notes"]


def test_quitar_la_ultima_linea_deja_las_demas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    deshecha = _step(resultado, "linea-deshecha")

    assert [linea["label"] for linea in _lineas(deshecha)] == [
        "línea de Diario 1 (a mano)",
        "línea de H4 1 (a mano)",
    ]
    assert not deshecha["lineUndoDisabled"], "todavía quedan que quitar"


def test_quitar_todas_borra_las_lineas_y_lo_que_decian(
    run: ImpulseRun, tmp_path: Path
) -> None:
    limpia = _step(_draw(run, tmp_path), "linea-limpia")

    assert not _lineas(limpia)
    assert limpia["lineUndoDisabled"] and limpia["lineClearDisabled"], (
        "sin líneas no hay nada que quitar"
    )
    assert "líneas marcadas a mano" not in limpia["notes"]


def test_las_notas_dicen_que_la_linea_no_la_ha_dibujado_el_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "linea-plantada")["notes"]

    assert "líneas marcadas a mano: 1 (1 Diario)" in notas
    assert "NO las ha dibujado el motor" in notas
    assert "no hay ninguna regla detrás" in notas


def test_las_lineas_no_cuentan_como_capa_del_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Los trazos a mano no son capa calculada: los recuentos de formas del motor
    tienen que seguir diciendo lo mismo con ellos puestos."""
    resultado = _draw(run, tmp_path)

    assert _step(resultado, "linea-tercera")["plot"]["shapes"] == (
        _step(resultado, "linea-limpia")["plot"]["shapes"]
    )


def test_sin_lineas_los_botones_de_quitar_estan_apagados(
    run: ImpulseRun, tmp_path: Path
) -> None:
    vacio = _step(_draw(run, tmp_path), "linea-sin-nada")

    assert vacio["lineUndoDisabled"] and vacio["lineClearDisabled"]
    assert vacio["lineArmed"] is None


# --- El RSI, siempre a la vista ------------------------------------------------
#
# Un panel propio DEBAJO del precio con el RSI de la temporalidad que se mira y
# las tres referencias del propietario. No es una capa que se pueda apagar: o
# está, o el explorador no lo trae. Lo calcula el MOTOR y viaja ya hecho, así que
# lo que se comprueba aquí es que llega alineado con las velas, que se dibuja en
# su panel sin comerse el del precio, que el hueco del arranque sigue siendo
# hueco y que la venda de la auditoría ciega también se lo lleva.


def _rsi_trace(step: dict) -> dict | None:
    esperado = f"RSI {RSI_PERIOD}"
    encontradas = [
        trace for trace in step["plot"]["traces"] if trace["name"] == esperado
    ]
    return encontradas[0] if encontradas else None


def test_el_payload_trae_una_lectura_de_rsi_por_vela(run: ImpulseRun) -> None:
    payload = build_payload(run)
    barras = payload["bars"][H4]

    assert len(barras["rsi"]) == len(barras["c"]), "una lectura por vela"
    assert barras["rsi"][: RSI_PERIOD] == [None] * RSI_PERIOD, (
        "las velas del arranque no tienen lectura: van como hueco, no como cero"
    )
    assert barras["rsi"][RSI_PERIOD] is not None
    assert all(
        0.0 <= value <= 100.0 for value in barras["rsi"] if value is not None
    )


def test_el_rsi_del_payload_es_el_del_motor(run: ImpulseRun) -> None:
    """El explorador no calcula indicadores: el número tiene que ser el mismo."""
    payload = build_payload(run)
    esperado = rsi(run.chart_bars[H4]["close"].to_numpy(dtype=float), RSI_PERIOD)
    dibujado = payload["bars"][H4]["rsi"]

    assert dibujado[-1] == pytest.approx(float(esperado[-1]), abs=1e-2)
    assert dibujado[RSI_PERIOD] == pytest.approx(float(esperado[RSI_PERIOD]), abs=1e-2)


def test_el_periodo_y_las_bandas_los_pone_el_motor(run: ImpulseRun) -> None:
    """Ni el 21 ni el 55 se escriben en el JavaScript: son decisión del propietario."""
    meta = build_payload(run)["meta"]

    assert meta["rsiPeriod"] == RSI_PERIOD
    assert meta["rsiBands"] == list(RSI_BANDS)


def test_el_rsi_se_dibuja_en_su_panel_sin_comerse_el_del_precio(
    run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(run, tmp_path), "rsi-en-diario")

    precio = paso["plot"]["priceDomain"]
    indice = paso["plot"]["rsiAxis"]
    assert indice is not None, "el RSI está siempre puesto"
    assert indice["domain"][1] <= precio[0], "los dos paneles no se pueden solapar"
    assert precio[1] == 1, "el precio se queda con la parte de arriba"
    # Las fechas van al pie de la figura, que ahora es el suelo del panel del RSI.
    assert paso["plot"]["xAnchor"] == "y2"


def test_el_eje_del_rsi_nombra_las_tres_referencias(
    run: ImpulseRun, tmp_path: Path
) -> None:
    indice = _step(_draw(run, tmp_path), "rsi-en-diario")["plot"]["rsiAxis"]

    assert indice["ticks"] == list(RSI_BANDS)
    assert indice["title"] == f"RSI {RSI_PERIOD}"


def test_las_tres_bandas_se_dibujan_y_la_del_medio_va_entera(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Van como trazas y no como formas para que entren en el autoescalado: si no,
    con el índice pegado a un extremo las tres podrían quedarse fuera."""
    traces = _step(_draw(run, tmp_path), "rsi-en-diario")["plot"]["traces"]
    bandas = {
        trace["name"]: trace
        for trace in traces
        if trace["name"].startswith("Referencia ")
    }

    for band in RSI_BANDS:
        assert f"Referencia {band}" in bandas, f"falta la referencia de {band}"
    assert bandas["Referencia 50"]["dash"] == "solid"
    assert bandas["Referencia 55"]["dash"] == "dot"
    assert bandas["Referencia 45"]["dash"] == "dot"


def test_el_rsi_esta_puesto_en_todos_los_graficos(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """No hay casilla que lo encienda: cambiar de temporalidad trae el suyo."""
    resultado = _draw(run, tmp_path)

    for label in ("rsi-en-diario", "rsi-en-h4"):
        paso = _step(resultado, label)
        trace = _rsi_trace(paso)
        assert trace is not None, f"sin RSI en {label}"
        assert trace["points"] > 0
        assert f"RSI {RSI_PERIOD}" in paso["notes"]
        assert "no decide nada" in paso["notes"]


def test_la_auditoria_ciega_tampoco_ensena_el_rsi(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Lo ha calculado el motor: enseñarlo antes de revelar invalidaría la prueba."""
    ciega = _step(_draw(run, tmp_path), "ciega")

    assert len(ciega["plot"]["traces"]) == 1, "no puede quedar ninguna capa del motor"
    assert ciega["plot"]["rsiAxis"] is None, "sin RSI, el precio recupera el alto"
    assert ciega["plot"]["priceDomain"] == [0, 1]


# --- I.5 · el Fibonacci a mano -------------------------------------------------
#
# La regla con la que el PROPIETARIO mide un retroceso. Dos clics —el primero
# clava el 0 y el segundo el 100—, y entre ellos los niveles que dice el payload,
# contados siempre del 0 al 100. No lo ha medido el motor: detrás no hay ninguna
# regla y no sale de la pantalla. Lo que se comprueba aquí es que hacen falta los
# dos clics, que los niveles caen donde deben, que se arrastra sin cambiar lo
# medido y que el estado deja claro de quién es.


def _fib(step: dict) -> list[dict]:
    return step["plot"]["fib"]


def _fib_level(step: dict, index: int, level: int) -> dict:
    encontrado = [
        shape for shape in _fib(step) if shape["name"] == f"fib-{index}-{level}"
    ]
    assert encontrado, f"no está el nivel {level} del Fibonacci {index}"
    return encontrado[0]


def test_los_niveles_del_fibonacci_los_pone_el_motor(run: ImpulseRun) -> None:
    """El 70, el 80 y el 90 son la lectura del propietario, no una constante del
    explorador: viajan en el payload como todo lo demás que él decide."""
    assert build_payload(run)["meta"]["fibLevels"] == list(FIB_LEVELS)


def test_el_boton_del_fibonacci_arma_y_lo_dice_antes_de_medir_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    armado = _step(_draw(run, tmp_path), "fib-armado")

    assert armado["fibArmed"] is True
    assert not _fib(armado), "armar no puede dibujar todavía ningún nivel"
    assert "FIBONACCI ARMADO" in armado["notes"]
    assert armado["simCursor"] == "crosshair"


def test_escape_desarma_el_fibonacci_sin_medir_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    desarmado = _step(_draw(run, tmp_path), "fib-desarmado")

    assert desarmado["fibArmed"] is False
    assert not _fib(desarmado)
    assert "FIBONACCI" not in desarmado["notes"]


def test_el_primer_clic_clava_el_cero_y_sigue_esperando_el_cien(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Un Fibonacci son dos clics: con uno solo no hay retroceso que medir."""
    medias = _step(_draw(run, tmp_path), "fib-cero-clavado")

    trazos = _fib(medias)
    assert len(trazos) == 1, "sólo el 0, a la espera del segundo clic"
    assert trazos[0]["name"] == "fib-draft"
    assert trazos[0]["dash"] == "dot"
    assert trazos[0]["y0"] == pytest.approx(1.11, abs=1e-3)
    assert medias["fibArmed"] is True, "el botón sigue armado hasta el segundo clic"
    assert "pulsa sobre el gráfico dónde va el 100" in medias["notes"]
    assert medias["fibUndoDisabled"] is False


def test_escape_suelta_el_cero_que_esperaba_su_segundo_clic(
    run: ImpulseRun, tmp_path: Path
) -> None:
    soltado = _step(_draw(run, tmp_path), "fib-cero-soltado")

    assert not _fib(soltado)
    assert soltado["fibArmed"] is False
    assert soltado["fibUndoDisabled"] is True


def test_el_fibonacci_mide_del_cero_al_cien_que_marcaron_los_clics(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El recorrido clava el 0 en 1,11 y el 100 en 1,29 sobre el encuadre que él
    mismo fijó (1,05 → 1,35): los retrocesos caen a esa fracción del tramo."""
    trazado = _step(_draw(run, tmp_path), "fib-trazado")

    assert len(_fib(trazado)) == len(FIB_LEVELS), "un trazo por nivel y ninguno más"
    cero = _fib_level(trazado, 0, 0)["y0"]
    cien = _fib_level(trazado, 0, 100)["y0"]
    assert cero == pytest.approx(1.11, abs=1e-3)
    assert cien == pytest.approx(1.29, abs=1e-3)
    for level in (70, 80, 90):
        esperado = cero + (cien - cero) * level / 100
        assert _fib_level(trazado, 0, level)["y0"] == pytest.approx(esperado, abs=1e-4)


def test_los_extremos_van_enteros_y_los_retrocesos_punteados(
    run: ImpulseRun, tmp_path: Path
) -> None:
    trazado = _step(_draw(run, tmp_path), "fib-trazado")

    assert _fib_level(trazado, 0, 0)["dash"] == "solid"
    assert _fib_level(trazado, 0, 100)["dash"] == "solid"
    for level in (70, 80, 90):
        assert _fib_level(trazado, 0, level)["dash"] == "dash"


def test_cada_nivel_dice_su_porcentaje_y_su_precio(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Cinco rayas sin número no miden nada: el porcentaje es la herramienta."""
    trazado = _step(_draw(run, tmp_path), "fib-trazado")

    assert "Fib 1 (a mano)" in _fib_level(trazado, 0, 0)["label"]
    for level in FIB_LEVELS:
        etiqueta = _fib_level(trazado, 0, level)["label"]
        assert f"{level} %" in etiqueta
        assert f"{_fib_level(trazado, 0, level)['y0']:.{DECIMALS}f}" in etiqueta


def test_el_fibonacci_va_en_gris_y_no_en_ninguno_de_los_colores_de_la_mano(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """No marca nada: es una regla con la que medir, y en gris no le quita sitio a
    ninguna capa ni se confunde con un OB o un FVG."""
    trazado = _step(_draw(run, tmp_path), "fib-trazado")

    for shape in _fib(trazado):
        assert shape["color"] == HAND_FIB
        assert shape["color"] not in HAND_RECTS.values()
        assert shape["color"] not in HAND_LINES.values()
        assert shape["color"] not in TIMEFRAME_COLORS.values()


def test_el_fibonacci_no_cuenta_como_capa_del_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Nadie ha medido ese retroceso: no puede sumar ni una forma de las que
    dibuja el motor, ni adelantar el reloj del replay."""
    resultado = _draw(run, tmp_path)
    limpio = _step(resultado, "fib-limpio")
    trazado = _step(resultado, "fib-trazado")

    assert trazado["plot"]["shapes"] == limpio["plot"]["shapes"]


def test_arrastrar_el_cien_arrastra_los_retrocesos_con_el(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "fib-trazado")
    despues = _step(resultado, "fib-cien-movido")

    assert _fib_level(despues, 0, 0)["y0"] == pytest.approx(
        _fib_level(antes, 0, 0)["y0"], abs=1e-4
    ), "el 0 no se mueve: se ha arrastrado la otra ancla"
    assert _fib_level(despues, 0, 100)["y0"] > _fib_level(antes, 0, 100)["y0"]
    for level in (70, 80, 90):
        assert _fib_level(despues, 0, level)["y0"] > _fib_level(antes, 0, level)["y0"]


def test_moverlo_por_dentro_no_cambia_el_retroceso_medido(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Lo que se acaba de medir es lo que se quiere explicar: recolocarlo para que
    se vea mejor no puede cambiarlo por su cuenta."""
    resultado = _draw(run, tmp_path)
    antes = _step(resultado, "fib-cien-movido")
    despues = _step(resultado, "fib-movido")

    def tramo(step: dict) -> float:
        return _fib_level(step, 0, 100)["y0"] - _fib_level(step, 0, 0)["y0"]

    assert tramo(despues) == pytest.approx(tramo(antes), abs=1e-3)
    assert _fib_level(despues, 0, 0)["y0"] < _fib_level(antes, 0, 0)["y0"], (
        "el conjunto entero baja con el arrastre"
    )


def test_el_cero_puede_ir_arriba_y_el_cien_abajo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La dirección la eligen los dos clics: en un retroceso bajista el 0 va
    arriba, y el 90 cae igual de cerca del 100, sólo que por debajo."""
    segundo = _step(_draw(run, tmp_path), "fib-segundo")

    cero = _fib_level(segundo, 1, 0)["y0"]
    cien = _fib_level(segundo, 1, 100)["y0"]
    assert cero > cien, "el 0 se clavó por encima del 100"
    assert _fib_level(segundo, 1, 90)["y0"] == pytest.approx(
        cero + (cien - cero) * 0.9, abs=1e-4
    )
    assert "Fib 2 (a mano)" in _fib_level(segundo, 1, 0)["label"]


def test_quitar_el_ultimo_deja_el_anterior_y_quitar_todos_no_deja_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    deshecho = _step(resultado, "fib-deshecho")
    limpio = _step(resultado, "fib-limpio")

    assert len(_fib(deshecho)) == len(FIB_LEVELS), "queda el primero"
    assert not _fib(limpio)
    assert limpio["fibUndoDisabled"] is True
    assert limpio["fibClearDisabled"] is True


def test_el_estado_dice_que_el_fibonacci_lo_ha_medido_una_mano(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Cinco rayas con porcentajes se leen como una medida que ha hecho alguien."""
    notas = _step(_draw(run, tmp_path), "fib-trazado")["notes"]

    assert "Fibonacci trazados a mano: 1" in notas
    assert "0, 70, 80, 90, 100 %" in notas
    assert "NO los ha calculado el motor" in notas
    assert "no salen de la pantalla" in notas


def test_sin_fibonacci_los_botones_de_quitar_estan_apagados(
    run: ImpulseRun, tmp_path: Path
) -> None:
    vacio = _step(_draw(run, tmp_path), "fib-sin-nada")

    assert vacio["fibUndoDisabled"] is True
    assert vacio["fibClearDisabled"] is True
    assert vacio["fibArmed"] is False


# --- J.1 · el alto y el bajo de Asia y de Londres ------------------------------
#
# Cada día, a las 7:58 del reloj de la pantalla (UTC-4 fijo), el MOTOR marca
# cuatro niveles: el alto y el bajo de Asia (20:00 → 01:00) y los de Londres
# (03:00 → 07:58) y los deja
# puestos hasta las 17:00. Es capa del motor —calculada en el dominio sobre el
# M1— y no dibujo del propietario. Lo que se comprueba aquí es que viaja ya
# calculada, que se dibuja donde y cuando dice, que SÓLO se dibuja la marca
# actual —la última puesta a la hora del borde derecho—, que se distingue de todo
# lo demás, que la casilla y el nivel de ruido la quitan de verdad, que el replay
# no la enseña antes de las 7:58 y que el estado dice qué se está viendo.

SESSION_TRACES = ("Alto Asia", "Bajo Asia", "Alto Londres", "Bajo Londres")


def _sesiones(step: dict) -> dict[str, dict]:
    return {
        trace["name"]: trace
        for trace in step["plot"]["traces"]
        if trace["name"] in SESSION_TRACES
    }


def _tramos(trace: dict) -> list[tuple[int, int, float]]:
    """Los segmentos (desde, hasta, precio) de una traza de líneas con nulos."""
    segments = trace["segments"]
    return [
        (_minute(segments[i][0]), _minute(segments[i + 1][0]), segments[i][1])
        for i in range(0, len(segments), 3)
    ]


def test_las_sesiones_viajan_calculadas_desde_el_dominio(run: ImpulseRun) -> None:
    """El explorador dibuja lo que le llega: ni un máximo se mide en JavaScript."""
    payload = build_payload(run)["sessions"]
    esperado = session_levels(run.chart_bars[M15]) if run.sessions is None else run.sessions

    # El reloj se escribe como en el resto del explorador: `UTC-4`, no el
    # nombre POSIX con el signo al revés.
    assert payload["rule"] == (
        "Asia 20:00 → 01:00 · Londres 03:00 → 07:58 · marca a las 07:58 · "
        "hasta las 17:00 · reloj UTC-4"
    )
    assert len(payload["days"]) == len(esperado) > 0
    primera = payload["days"][0]
    fila = esperado.iloc[0]
    assert primera["m"] == _minute(str(fila["marked_at"]))
    assert primera["u"] == _minute(str(fila["until"]))
    assert primera["ah"] == round(float(fila["asia_high"]), DECIMALS)
    assert primera["al"] == round(float(fila["asia_low"]), DECIMALS)
    assert primera["lh"] == round(float(fila["london_high"]), DECIMALS)
    assert primera["ll"] == round(float(fila["london_low"]), DECIMALS)
    assert primera["ahx"] == _minute(str(fila["asia_high_at"]))
    assert primera["llx"] == _minute(str(fila["london_low_at"]))
    # La marca es a las 7:58 y se retira a las 17:00: nueve horas y dos minutos
    # después. Y en el reloj de la pantalla, UTC-4 fijo: las 11:58 UTC siempre.
    assert all(day["u"] - day["m"] == 9 * 60 + 2 for day in payload["days"])
    assert all(day["m"] % 1440 == 11 * 60 + 58 for day in payload["days"])


def test_sin_el_m1_no_hay_sesiones_y_el_payload_lo_dice(context_run: ImpulseRun) -> None:
    """La corrida con contexto no recibe el histórico base: la capa va vacía, no
    inventada a partir de M15."""
    assert context_run.sessions is None
    assert build_payload(context_run)["sessions"] == {"rule": None, "days": []}


def _actual(payload: dict, hasta: int) -> dict | None:
    """La última marca puesta a ese minuto."""
    puestas = [day for day in payload["sessions"]["days"] if day["m"] <= hasta]
    return puestas[-1] if puestas else None


def test_solo_se_dibuja_la_marca_actual_de_la_marca_a_las_17(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Una marca por día tapa el precio en dos meses y no decide nada hoy: se
    dibuja la última puesta a la hora del borde derecho, y ninguna más."""
    resultado = _draw(run, tmp_path)
    con = _step(resultado, "con-sesiones")
    payload = build_payload(run)
    trazas = _sesiones(con)

    assert set(trazas) == set(SESSION_TRACES)
    hasta = _minute(con["to"] + " 23:59")
    actual = _actual(payload, hasta)
    assert actual is not None
    assert len([day for day in payload["sessions"]["days"] if day["m"] <= hasta]) > 1, (
        "la ventana tiene que dejar marcas anteriores fuera del dibujo"
    )
    for nombre, clave in zip(SESSION_TRACES, ("ah", "al", "lh", "ll"), strict=True):
        assert _tramos(trazas[nombre]) == [(actual["m"], actual["u"], actual[clave])], nombre


def test_cada_nivel_lleva_su_nombre_y_su_precio_escritos_en_la_marca(
    run: ImpulseRun, tmp_path: Path
) -> None:
    con = _step(_draw(run, tmp_path), "con-sesiones")
    alto = _sesiones(con)["Alto Asia"]

    assert alto["textposition"] == "top right"
    assert _sesiones(con)["Bajo Asia"]["textposition"] == "bottom right"
    etiquetas = [texto for texto in alto["captions"] if texto]
    assert etiquetas, "el nombre va escrito en el gráfico"
    assert all(re.fullmatch(r"Alto Asia \d+\.\d{4}", texto) for texto in etiquetas)
    hover = next(texto for texto in alto["hovers"] if texto)
    assert hover.startswith("ALTO DE ASIA ")
    assert "marcado a las " in hover
    assert "lo fijó la vela de " in hover
    assert "se retira a las " in hover


def test_un_tono_por_sesion_que_no_usa_nadie_mas(run: ImpulseRun, tmp_path: Path) -> None:
    """Marrón Asia, púrpura Londres; el alto y el bajo de la misma sesión
    comparten tono y se distinguen por el nombre. Ninguna otra capa —ni del
    motor ni de la mano— usa esos dos colores."""
    trazas = _sesiones(_step(_draw(run, tmp_path), "con-sesiones"))

    assert _rgb(trazas["Alto Asia"]["color"]) == _rgb(trazas["Bajo Asia"]["color"])
    assert _rgb(trazas["Alto Asia"]["color"]) == _rgb(SESSION_COLORS["asia"])
    assert _rgb(trazas["Alto Londres"]["color"]) == _rgb(trazas["Bajo Londres"]["color"])
    assert _rgb(trazas["Alto Londres"]["color"]) == _rgb(SESSION_COLORS["london"])
    assert _rgb(SESSION_COLORS["asia"]) != _rgb(SESSION_COLORS["london"])
    otros = {_rgb(BULLISH), _rgb(BEARISH), _rgb(HAND_FIB)}
    otros |= {_rgb(colour) for colour in TIMEFRAME_COLORS.values()}
    otros |= {_rgb(colour) for colour in HAND_RECTS.values()}
    otros |= {_rgb(colour) for colour in HAND_LINES.values()}
    otros |= {_rgb(colour) for colour in PATTERN_COLORS.values()}
    assert not {_rgb(colour) for colour in SESSION_COLORS.values()} & otros
    for trace in trazas.values():
        assert trace["dash"] is None, "el nivel marcado va continuo"


def test_el_punteado_va_de_la_vela_que_fijo_el_nivel_a_la_marca(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El precio ya había estado ahí antes de las 7:58, pero nadie lo había
    marcado: ese tramo se dibuja punteado, como el ancla y el extremo del ID."""
    con = _step(_draw(run, tmp_path), "con-sesiones")
    payload = build_payload(run)["sessions"]["days"]
    punteado = [
        trace for trace in con["plot"]["traces"] if trace["name"] == "Asia · de dónde sale"
    ]
    assert punteado and punteado[0]["dash"] == "dot"
    assert _rgb(punteado[0]["color"]) == _rgb(SESSION_COLORS["asia"])

    marcas = {day["m"]: day for day in payload}
    tramos = _tramos(punteado[0])
    assert len(tramos) == 2, "el alto y el bajo de la marca actual, y nada más"
    for desde, hasta, precio in tramos:
        day = marcas[hasta]
        assert desde in (day["ahx"], day["alx"])
        assert precio in (day["ah"], day["al"])
        assert desde < hasta, "la vela que fija el nivel va antes de la marca"


def test_la_casilla_quita_las_sesiones_y_el_nivel_limpio_tambien(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)

    assert not _sesiones(_step(resultado, "sin-sesiones"))
    assert not _sesiones(_step(resultado, "ruido-limpio"))
    assert _sesiones(_step(resultado, "ruido-normal"))
    assert _sesiones(_step(resultado, "ruido-todo"))
    assert _step(resultado, "ruido-de-salida")["boxes"]["layer-sessions"] is False


def test_la_auditoria_ciega_tampoco_ensena_las_sesiones(
    run: ImpulseRun, tmp_path: Path
) -> None:
    assert not _sesiones(_step(_draw(run, tmp_path), "ciega"))


def test_el_replay_no_marca_ninguna_sesion_antes_de_las_7_58(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """A cada paso se dibuja exactamente la última marca que el reloj ya ha
    visto poner, y no llega más allá del presente."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    comprobados = 0

    for paso in _replay_steps(resultado):
        reloj = _clock(payload, paso)
        actual = _actual(payload, reloj)
        trazas = _sesiones(paso)
        if actual is None or actual["ah"] is None:
            assert "Alto Asia" not in trazas, paso["label"]
            continue
        tramos = _tramos(trazas["Alto Asia"])
        assert [tramo[0] for tramo in tramos] == [actual["m"]], paso["label"]
        assert tramos[0][1] == min(actual["u"], reloj), paso["label"]
        comprobados += 1

    assert comprobados, "ningún paso del replay llegó a ver una marca"


def test_las_notas_dicen_cuantas_marcas_hay_y_con_que_regla(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    con = _step(resultado, "con-sesiones")

    assert "sesiones de Asia y Londres: sólo la marca ACTUAL, la de " in con["notes"]
    assert "las marcas anteriores no se dibujan: siguen en sesiones.csv" in con["notes"]
    assert "Londres 03:00 → 07:58 · marca a las 07:58" in con["notes"]
    assert "punteado desde la vela que fijó el nivel, continuo desde la marca" in con["notes"]
    sin = _step(resultado, "sin-sesiones")["notes"]
    assert "sesiones de Asia y Londres:" not in sin
    assert "capas apagadas: " in sin and "sesiones de Asia y Londres" in sin


def test_las_sesiones_no_se_dibujan_sin_datos_y_el_estado_lo_dice(
    context_run: ImpulseRun, tmp_path: Path
) -> None:
    con = _step(_draw(context_run, tmp_path), "con-sesiones")

    assert not _sesiones(con)
    assert "sesiones de Asia y Londres: sin datos" in con["notes"]


# --- K.1 · el OB y el FVG del motor dentro del ID ------------------------------
#
# El motor marca, dentro de cada ID, los OB y FVG en su dirección que solapan
# su rango, desde la vela previa a la pierna hasta la que lo mata: en el Diario
# y en H4 dentro de su propio ID, en H1 dentro del ID de H4. Es capa del motor
# —calculada en el dominio— y no dibujo del propietario. Lo que se comprueba es
# que viaja calculada, que cada patrón es un recuadro relleno con su tono y su
# nombre, que va de su vela a la que lo usó o mató a su ID, que la casilla y la
# ciega lo quitan, que obedece el filtro de ID visibles, que M15 dice que no
# marca nada, que el replay no lo enseña antes de saberlo y que el estado dice
# qué se está viendo.

PATTERN_TRACES = ("OB del motor", "FVG del motor")


def _patrones(step: dict) -> dict[str, dict]:
    return {
        trace["name"]: trace
        for trace in step["plot"]["traces"]
        if trace["name"] in PATTERN_TRACES
    }


def _cajas(step: dict) -> dict[str, list[tuple[str, str, float, float]]]:
    """Los recuadros de cada patrón, como (x0, x1, bajo, alto)."""
    return {
        name: _rectangulos(trace["segments"])
        for name, trace in _patrones(step).items()
    }


def _etiquetas(trace: dict) -> list[str]:
    return [texto for texto in (trace["captions"] or []) if texto]


def test_los_patrones_viajan_calculados_desde_el_dominio(run: ImpulseRun) -> None:
    """El explorador dibuja lo que le llega: ni un OB se busca en JavaScript."""
    payload = build_payload(run)

    assert list(payload["patterns"]) == [DAILY, H4, H1]
    assert payload["patterns"][H1]["idTimeframe"] == H4
    assert payload["patterns"][H4]["idTimeframe"] == H4
    assert payload["patterns"][DAILY]["idTimeframe"] == DAILY
    assert payload["meta"]["patternRule"] == run.pattern_rule.describe()
    assert "usado: el precio vuelve a entrar en la zona" in payload["meta"]["patternRule"]

    tabla = run.patterns[H1]
    assert list(tabla.columns) == list(PATTERN_COLUMNS)
    assert len(tabla) > 0, "la fixture tiene que producir patrones en H1"
    assert tabla["timeframe"].eq(H1).all() and tabla["id_timeframe"].eq(H4).all()
    lista = payload["patterns"][H1]["list"]
    assert len(lista) == len(tabla)
    primero, fila = lista[0], tabla.iloc[0]
    assert primero["id"] == int(fila["id_num"])
    assert primero["k"] == fila["tipo"] and primero["d"] == fila["direccion"]
    assert primero["x0"] == _minute(str(fila["ts_origen"]))
    assert primero["xk"] == _minute(str(fila["ts_conocido"]))
    assert primero["x1"] == (None if pd.isna(fila["ts_fin"]) else _minute(str(fila["ts_fin"])))
    assert primero["lo"] == round(float(fila["precio_bajo"]), DECIMALS)
    assert primero["hi"] == round(float(fila["precio_alto"]), DECIMALS)
    assert primero["u"] is bool(fila["usado"])
    # Cada ID de H1 apunta a un ID de H4 publicado, y el patrón se sabe con el
    # ID vivo o antes de que nazca, nunca después de que muera.
    ids = {impulse.id_num: impulse for impulse in run.analyses[H4].published}
    for item in lista:
        dueno = ids[item["id"]]
        assert item["x0"] <= item["xk"]
        assert item["lo"] <= item["hi"]
        if dueno.ts_end is not None:
            assert item["xk"] <= _minute(str(dueno.ts_end)) + payload["spans"][H4]


def test_cada_patron_es_un_recuadro_relleno_con_su_tono_y_su_nombre(
    run: ImpulseRun, tmp_path: Path
) -> None:
    con = _step(_draw(run, tmp_path), "con-patrones")
    trazas = _patrones(con)

    assert trazas, "la fixture tiene que dibujar algún patrón en H1"
    for name, trace in trazas.items():
        kind = name.split(" ")[0]
        assert trace["fill"] == "toself", "un patrón es una zona, no una línea"
        assert _rgb(trace["color"]) == _rgb(PATTERN_COLORS[kind])
        assert _rgb(trace["fillcolor"]) == _rgb(PATTERN_COLORS[kind])
        assert trace["dash"] is None
        assert trace["textposition"] == "bottom right"
    actual = _step(_draw(run, tmp_path), "patrones-id-actual")
    etiquetas = [texto for trace in _patrones(actual).values() for texto in _etiquetas(trace)]
    assert etiquetas, "el nombre va escrito dentro del recuadro"
    assert all(re.match(r"^(OB|FVG) H1 · ID H4 nº \d+", texto) for texto in etiquetas)
    globos = [
        texto
        for trace in _patrones(actual).values()
        for texto in (trace["hovers"] or [])
        if texto
    ]
    assert all(re.match(r"^(OB|FVG) DEL MOTOR · H1 · dentro del ID H4 nº \d+", g) for g in globos)
    assert all("se supo al cerrar la de " in g for g in globos)


def test_el_recuadro_va_de_la_vela_del_patron_a_la_que_lo_uso_o_mato_a_su_id(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con el periodo entero y todos los ID, se dibujan todos: cada recuadro va
    de su vela a `x1`, y el que sigue vivo llega al borde de la ventana."""
    con = _step(_draw(run, tmp_path), "con-patrones")
    payload = build_payload(run)["patterns"][H1]["list"]
    borde = _minute(con["to"] + " 23:59")
    esperado = {
        (item["k"], item["x0"], borde if item["x1"] is None else item["x1"], item["lo"], item["hi"])
        for item in payload
    }
    dibujado = {
        (name.split(" ")[0], _minute(x0), _minute(x1), lo, hi)
        for name, cajas in _cajas(con).items()
        for x0, x1, lo, hi in cajas
    }

    assert dibujado == esperado
    assert any(item["u"] for item in payload), "la fixture tiene que tener algún patrón usado"


def test_la_casilla_quita_los_patrones_y_el_estado_lo_dice(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    sin = _step(resultado, "sin-patrones")

    assert not _patrones(sin)
    assert sin["boxes"]["layer-patterns"] is False
    assert "capas apagadas: " in sin["notes"] and "OB y FVG del motor" in sin["notes"]
    assert "OB y FVG del motor:" not in sin["notes"]
    # Encendidos en los tres niveles de ruido: es lo que se está auditando.
    for label in ("ruido-limpio", "ruido-normal", "ruido-todo", "ruido-de-salida"):
        assert _step(resultado, label)["boxes"]["layer-patterns"] is True, label


def test_con_id_actual_solo_se_dibujan_los_del_id_actual(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    actual = _step(resultado, "patrones-id-actual")
    todos = _step(resultado, "con-patrones")
    payload = build_payload(run)
    borde = _minute(actual["to"] + " 23:59")
    vigente = [
        impulse["id"]
        for impulse in payload["impulses"][H4]["list"]
        if impulse["x0"] <= borde
    ][-1]
    etiquetas = [texto for trace in _patrones(actual).values() for texto in _etiquetas(trace)]
    ids = {int(re.search(r"nº (\d+)", texto).group(1)) for texto in etiquetas}  # type: ignore[union-attr]

    assert actual["visibleMode"] == "current"
    assert ids <= {vigente}
    assert sum(len(c) for c in _cajas(actual).values()) < sum(
        len(c) for c in _cajas(todos).values()
    )


def test_en_el_diario_y_en_h4_cuelgan_de_su_propio_id(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    diario = _step(resultado, "patrones-diario")
    h4 = _step(resultado, "patrones-h4")

    assert _patrones(diario) or run.patterns[DAILY].empty
    assert _patrones(h4), "la fixture tiene que dibujar algún patrón en H4"
    assert "dentro del ID de H4" in h4["notes"] and "a la vista en H4" in h4["notes"]
    assert "dentro del ID de Diario" in diario["notes"]
    for trace in _patrones(h4).values():
        for x0, x1, lo, hi in _rectangulos(trace["segments"]):
            assert lo < hi and x0 <= x1


def test_en_m15_no_se_marcan_y_el_estado_lo_dice(run: ImpulseRun, tmp_path: Path) -> None:
    m15 = _step(_draw(run, tmp_path), "patrones-m15")

    assert not _patrones(m15)
    assert "OB y FVG del motor: en M15 no se marcan (por ahora sólo en Diario, H4 y H1)" in (
        m15["notes"]
    )


def test_un_tono_por_patron_que_no_usa_nadie_mas(run: ImpulseRun, tmp_path: Path) -> None:
    """Ámbar el OB, índigo el FVG; ni la mano ni ninguna otra capa del motor."""
    assert set(PATTERN_COLORS) == {"OB", "FVG"}
    assert _rgb(PATTERN_COLORS["OB"]) != _rgb(PATTERN_COLORS["FVG"])
    otros = {_rgb(BULLISH), _rgb(BEARISH), _rgb(HAND_FIB)}
    otros |= {_rgb(colour) for colour in TIMEFRAME_COLORS.values()}
    otros |= {_rgb(colour) for colour in HAND_RECTS.values()}
    otros |= {_rgb(colour) for colour in HAND_LINES.values()}
    otros |= {_rgb(colour) for colour in SESSION_COLORS.values()}
    assert not {_rgb(colour) for colour in PATTERN_COLORS.values()} & otros
    # Y los recuadros a mano siguen distinguiéndose: punteados, en su tono.
    plantado = _recuadros(_step(_draw(run, tmp_path), "rect-plantado"))[0]
    assert plantado["dash"] == "dot"
    assert _rgb(plantado["color"]) not in {_rgb(c) for c in PATTERN_COLORS.values()}


def test_la_auditoria_ciega_tampoco_ensena_los_patrones(
    run: ImpulseRun, tmp_path: Path
) -> None:
    assert not _patrones(_step(_draw(run, tmp_path), "ciega"))


def test_el_replay_no_ensena_un_patron_antes_de_saberlo_ni_antes_de_su_id(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """A cada paso, cada recuadro dibujado ya se sabía —cerró la vela que lo
    hace saber— y su ID ya existía; y ninguno llega más allá del reloj."""
    resultado = _draw(run, tmp_path)
    payload = build_payload(run)
    comprobados = 0

    for paso in _replay_steps(resultado):
        chart = {"Diario": DAILY}.get(paso["chart"], paso["chart"])
        source = payload["patterns"].get(chart)
        if source is None:
            assert not _patrones(paso), paso["label"]
            continue
        por_origen = {(item["k"], item["x0"]): item for item in source["list"]}
        constituciones = {
            impulse["id"]: impulse["x0"]
            for impulse in payload["impulses"][source["idTimeframe"]]["list"]
        }
        span = payload["spans"][chart]
        span_id = payload["spans"][source["idTimeframe"]]
        reloj = _clock(payload, paso)
        for name, cajas in _cajas(paso).items():
            for x0, x1, _lo, _hi in cajas:
                item = por_origen[(name.split(" ")[0], _minute(x0))]
                assert item["xk"] + span <= reloj, paso["label"]
                assert constituciones[item["id"]] + span_id <= reloj, paso["label"]
                assert _minute(x1) <= reloj, paso["label"]
                comprobados += 1

    assert comprobados, "ningún paso del replay llegó a dibujar un patrón"


def test_las_notas_dicen_cuantos_hay_y_con_que_regla(run: ImpulseRun, tmp_path: Path) -> None:
    con = _step(_draw(run, tmp_path), "con-patrones")
    cajas = _cajas(con)
    ob = len(cajas.get("OB del motor", []))
    fvg = len(cajas.get("FVG del motor", []))

    assert f"OB y FVG del motor: {ob} OB y {fvg} FVG a la vista en H1, dentro del ID de H4" in (
        con["notes"]
    )
    assert run.pattern_rule.describe() in con["notes"]
    assert "obedecen el filtro de ID visibles · ámbar el OB, índigo el FVG" in con["notes"]
    assert "usados, dibujados hasta la vela que los usó" in con["notes"]


def test_los_patrones_no_se_dibujan_en_las_variantes_de_r36(
    run: ImpulseRun, variants: tuple[ModeVariant, ...], tmp_path: Path
) -> None:
    """Las otras corridas no traen patrones y sus ID no son los mismos: antes
    que dibujar los del modo base sobre otros ID, no se dibujan y se dice."""
    resultado = _draw(run, tmp_path, variants)
    otro = _step(resultado, "modo-L2_siguiente_barra")

    assert not _patrones(otro)
    assert "OB y FVG del motor: sólo están calculados para el modo base" in otro["notes"]
