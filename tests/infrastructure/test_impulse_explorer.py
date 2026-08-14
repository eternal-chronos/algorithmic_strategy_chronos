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
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.lateralization import measure
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import LegStartMode
from chronos.infrastructure.reporting.impulse_explorer import (
    ASSETS,
    ModeVariant,
    bar_counts,
    build_payload,
    payload_size,
    render_explorer,
)
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
    return DetectDominantImpulses(config).execute(series, provenance="fixture sintética")


# --- Reparto de gráficos ----------------------------------------------------


def test_el_reparto_por_defecto_es_el_del_propietario(run: ImpulseRun) -> None:
    payload = build_payload(run)
    assert payload["charts"] == [DAILY, H4, H1, M15]
    assert payload["layout"] == {
        DAILY: [DAILY],
        H4: [H4, DAILY],
        H1: [H1, H4],
        M15: [H1],
    }


def test_m15_lleva_velas_pero_no_impulso_propio(run: ImpulseRun) -> None:
    """Es temporalidad de ejecución: sobre ella sólo se dibuja el impulso de H1."""
    payload = build_payload(run)
    assert M15 in payload["bars"]
    assert M15 not in payload["impulses"]
    assert set(payload["impulses"]) == {DAILY, H4, H1}


def test_cada_grafico_tiene_sus_velas(run: ImpulseRun) -> None:
    counts = bar_counts(build_payload(run))
    assert set(counts) == {DAILY, H4, H1, M15}
    assert counts[M15] > counts[H1] > counts[H4] > counts[DAILY]


def test_un_grafico_sin_velas_no_llega_a_ofrecerse(run: ImpulseRun) -> None:
    """Con un histórico H1 no hay M15: su pestaña no puede quedarse esperando."""
    sin_m15 = ImpulseRun(
        enabled=True,
        config=run.config,
        config_hash=run.config_hash,
        analyses=run.analyses,
        chart_bars={chart: frame for chart, frame in run.chart_bars.items() if chart != M15},
    )
    payload = build_payload(sin_m15)

    assert payload["charts"] == [DAILY, H4, H1]
    assert M15 not in payload["layout"]
    assert M15 not in payload["bars"]


def test_no_se_puede_superponer_una_temporalidad_inferior() -> None:
    with pytest.raises(Exception, match="temporalidad superior"):
        ChartsConfig({H4: (H4, M15)})


def test_una_temporalidad_no_soportada_se_rechaza() -> None:
    with pytest.raises(Exception, match="no soportada"):
        ChartsConfig({"M5": ("M5",)})


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
    assert set(payload["bars"][H4]) == {"truncated", "total", "t", "o", "h", "l", "c"}
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
    assert "H4: H4 + Diario" in html
    assert "M15: H1" in html
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
    zones: ZonesRun | None = None,
) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está disponible: no se puede ejecutar el JavaScript")

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            build_payload(
                run, lateralization=measure(run), variants=variants, zones=zones
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
    assert resultado["chartTabs"] == ["Diario", "H4", "H1", "M15"]
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
    assert titulos["H4"] == "Dibuja el impulso de H4 y Diario"
    assert titulos["M15"] == "Dibuja el impulso de H1"


def test_cada_grafico_dibuja_su_impulso_y_el_de_contexto(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    esperado = {
        "grafico-D": {"ID Diario"},
        "grafico-H4": {"ID H4", "ID Diario"},
        "grafico-H1": {"ID H1", "ID H4"},
        "grafico-M15": {"ID H1"},
    }
    for label, temporalidades in esperado.items():
        nombres = [trace["name"] for trace in _step(resultado, label)["plot"]["traces"]]
        dibujadas = {
            " ".join(nombre.split()[:2]) for nombre in nombres if nombre.startswith("ID ")
        }
        assert dibujadas == temporalidades, f"{label}: {nombres}"


def test_el_contexto_va_punteado_y_el_principal_continuo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    trazas = _step(_draw(run, tmp_path), "grafico-H4")["plot"]["traces"]
    propias = [t for t in trazas if t["name"].startswith("ID H4")]
    contexto = [t for t in trazas if t["name"].startswith("ID Diario")]

    assert propias and contexto
    assert all(t["dash"] == "solid" for t in propias)
    assert all(t["dash"] == "dot" for t in contexto)
    assert all("(contexto)" in t["name"] for t in contexto)


def test_los_marcadores_son_solo_del_impulso_principal(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """En M15 los marcadores son los de H1, que es el único impulso que se ve."""
    nombres = [t["name"] for t in _step(_draw(run, tmp_path), "grafico-M15")["plot"]["traces"]]
    assert "Constitución H1" in nombres
    assert not any(nombre.startswith("Constitución M15") for nombre in nombres)


def test_las_capas_se_rehacen_al_cambiar_de_grafico(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "grafico-H4")["layerLabels"] == [
        "ID H4 (principal)",
        "ID Diario (contexto)",
    ]
    assert _step(resultado, "grafico-M15")["layerLabels"] == ["ID H1 (principal)"]


def test_apagar_el_impulso_principal_deja_el_contexto(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
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
    assert run.config.reporting.session_timezone in detalle
    assert re.search(r"O \d+\.\d{4} · H \d+\.\d{4}", detalle)


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
    assert spans == {DAILY: 1440, H4: 240, H1: 60, M15: 15}


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
    assert cierre + payload["spans"][DAILY] > reloj, "la vela diaria siguiente aún no cerró"


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


# --- Fase 2.0 · las capas de zonas UL y OB ----------------------------------


@pytest.fixture
def zones(run: ImpulseRun) -> ZonesRun:
    """Las zonas de la misma corrida. La fixture del módulo las trae apagadas."""
    return detect_zones(run, replace(run.config, zones=ZonesConfig(enabled=True)))


def test_sin_zonas_el_payload_no_las_declara(run: ImpulseRun) -> None:
    """Un explorador de la fase 1 sigue siendo exactamente el de antes."""
    payload = build_payload(run)
    assert payload["hasZones"] is False
    assert payload["impulses"][H4]["zones"] == []
    assert payload["impulses"][H4]["obCandidates"] == []


def test_cada_zona_viaja_con_sus_dos_bordes(run: ImpulseRun, zones: ZonesRun) -> None:
    payload = build_payload(run, zones=zones)
    assert payload["hasZones"] is True

    registros = payload["impulses"][H4]["zones"]
    assert len(registros) == len(zones.per_timeframe[H4].table)
    for registro in registros:
        assert registro["k"] in {"UL", "OB"}
        assert registro["lo"] <= registro["hi"]
        assert {registro["i"], registro["o"]} == {registro["lo"], registro["hi"]} or (
            registro["i"] == registro["o"]
        )


def test_la_zona_no_nace_antes_que_su_vela_definitoria(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """El contorno atenuado va de `xd` a `x0`: si no, no habría nada que atenuar."""
    for registro in build_payload(run, zones=zones)["impulses"][H4]["zones"]:
        assert registro["xd"] <= registro["x0"]
        assert registro["x0"] <= registro["x1"]


def test_solo_el_ob_viaja_con_su_confirmacion(run: ImpulseRun, zones: ZonesRun) -> None:
    registros = build_payload(run, zones=zones)["impulses"][H4]["zones"]
    uls = [item for item in registros if item["k"] == "UL"]
    obs = [item for item in registros if item["k"] == "OB"]

    assert uls and obs
    assert all(item["xc"] is None for item in uls)
    assert all(item["xc"] is not None for item in obs)
    # Y el nacimiento del OB nunca precede a la vela que lo confirmó.
    assert all(item["x0"] >= item["xc"] for item in obs)


def test_los_id_sin_ob_viajan_como_candidatos(run: ImpulseRun, zones: ZonesRun) -> None:
    """La zona no existe, pero la vela candidata hay que poder verla (§8)."""
    payload = build_payload(run, zones=zones)
    candidatos = payload["impulses"][H4]["obCandidates"]
    sin_ob = {zoned.id_num for zoned in zones.per_timeframe[H4].without_order_block}

    assert {item["id"] for item in candidatos} == sin_ob
    con_ob = {
        item["id"] for item in payload["impulses"][H4]["zones"] if item["k"] == "OB"
    }
    assert not (con_ob & sin_ob)


def test_las_dos_capas_se_dibujan_por_defecto(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    nombres = _trace_names(_step(_draw(run, tmp_path, zones=zones), "zonas-por-defecto"))
    assert any(nombre.startswith("Zona UL") for nombre in nombres), nombres
    assert any(nombre.startswith("Zona OB") for nombre in nombres), nombres


def test_las_dos_capas_se_encienden_por_separado(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, zones=zones)
    solo_ul = _trace_names(_step(resultado, "zonas-solo-ul"))
    solo_ob = _trace_names(_step(resultado, "zonas-solo-ob"))
    apagadas = _trace_names(_step(resultado, "zonas-apagadas"))

    assert any(nombre.startswith("Zona UL") for nombre in solo_ul)
    assert not any(nombre.startswith("Zona OB") for nombre in solo_ul)
    assert any(nombre.startswith("Zona OB") for nombre in solo_ob)
    assert not any(nombre.startswith("Zona UL") for nombre in solo_ob)
    assert not any(nombre.startswith("Zona ") for nombre in apagadas)


def test_la_zona_que_existe_va_rellena_y_la_candidata_no(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """§8: el OB sin confirmar se dibuja con otro borde porque no es una zona."""
    trazas = _step(_draw(run, tmp_path, zones=zones), "zonas-por-defecto")["plot"]["traces"]
    reales = [t for t in trazas if t["name"].startswith("Zona ")]
    candidatas = [t for t in trazas if t["name"] == "OB sin confirmar"]

    assert reales
    assert all(t["fill"] == "toself" for t in reales)
    assert all(t["dash"] in (None, "solid") for t in reales)
    if candidatas:
        assert all(t["fill"] == "none" for t in candidatas)
        assert all(t["dash"] == "dot" for t in candidatas)


def test_el_tramo_anterior_al_nacimiento_va_sin_relleno(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """Antes de nacer la zona no existe: se insinúa, no se pinta."""
    trazas = _step(_draw(run, tmp_path, zones=zones), "zonas-por-defecto")["plot"]["traces"]
    previos = [t for t in trazas if t["name"].startswith("Antes de existir")]

    assert previos, "falta el contorno de la vela que define la zona"
    assert all(t["fill"] == "none" for t in previos)
    assert all(t["dash"] == "dot" for t in previos)


def test_hay_marcador_sobre_la_vela_que_confirma(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    nombres = _trace_names(_step(_draw(run, tmp_path, zones=zones), "zonas-por-defecto"))
    assert "Confirmación del OB" in nombres


def test_el_filtro_de_id_visibles_recorta_tambien_las_zonas(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, zones=zones)

    def puntos(label: str) -> int:
        return sum(
            trace["points"]
            for trace in _step(resultado, label)["plot"]["traces"]
            if trace["name"].startswith("Zona ")
        )

    assert puntos("zonas-ids-current") < puntos("zonas-ids-pair") < puntos("zonas-ids-all")


def test_las_notas_declaran_que_las_zonas_no_rompen_nada(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path, zones=zones), "zonas-por-defecto")["notes"]
    assert "zonas dibujadas" in notas
    assert "no rompen nada" in notas


def test_la_auditoria_ciega_tampoco_ensena_las_zonas(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    ciega = _step(_draw(run, tmp_path, zones=zones), "ciega")
    assert len(ciega["plot"]["traces"]) == 1, _trace_names(ciega)


def test_las_zonas_no_se_dibujan_en_otro_modo_de_r36(
    run: ImpulseRun,
    zones: ZonesRun,
    variants: tuple[ModeVariant, ...],
    tmp_path: Path,
) -> None:
    """Se calcularon sobre los impulsos del modo activo: en otro serían de otros ID."""
    resultado = _draw(run, tmp_path, variants, zones=zones)
    otro = _step(resultado, "modo-L2_siguiente_barra")

    assert not any(nombre.startswith("Zona ") for nombre in _trace_names(otro))
    assert "no se dibujan en otro modo" in otro["notes"]


def test_el_replay_no_dibuja_una_zona_antes_de_que_nazca(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """La misma frontera que el resto de capas: nada más allá del reloj."""
    resultado = _draw(run, tmp_path, zones=zones)
    payload = build_payload(run, lateralization=measure(run), zones=zones)
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


# --- Fase 2.1 · la capa de roturas evitadas ---------------------------------


@pytest.fixture
def zoned_run(run: ImpulseRun) -> ImpulseRun:
    """Las mismas velas con la rotura por zona, que es la que trae la capa."""
    series = {timeframe: analysis.bars for timeframe, analysis in run.analyses.items()}
    tuned = replace(run.config, rules=replace(run.config.rules, break_by_zone=True))
    return DetectDominantImpulses(tuned).execute(series, provenance="fixture sintética")


def test_sin_la_regla_nueva_el_payload_no_declara_la_capa(run: ImpulseRun) -> None:
    payload = build_payload(run)

    assert payload["hasAvoided"] is False
    assert payload["meta"]["breakByZone"] is False
    assert payload["impulses"][H4]["avoided"] == []
    # Y toda rotura viaja como "por línea", que es lo que la fase 1 hacía.
    assert all(item["src"] == "linea" for item in payload["impulses"][H4]["breaks"])


def test_cada_rotura_evitada_viaja_con_su_zona_y_su_linea(zoned_run: ImpulseRun) -> None:
    payload = build_payload(zoned_run)

    assert payload["hasAvoided"] is True
    assert payload["meta"]["breakByZone"] is True
    registros = payload["impulses"][H4]["avoided"]
    assert len(registros) == len(zoned_run.analyses[H4].avoided)
    for registro in registros:
        # Cerró más allá de la línea y sin llegar al borde exterior: las dos
        # cosas se tienen que poder leer en el globo sin abrir ningún CSV.
        bajo, alto = sorted((registro["zi"], registro["zo"]))
        assert bajo <= registro["y"] <= alto
        assert registro["z"] in ("UL", "OB")
        assert registro["k"] in ("favor", "contra")


def test_la_rotura_real_dice_si_fue_por_zona_o_por_linea(zoned_run: ImpulseRun) -> None:
    registros = build_payload(zoned_run)["impulses"][H4]["breaks"]
    fuentes = {item["src"] for item in registros}

    assert fuentes <= {"linea", "UL", "OB"}
    # El UL existe siempre, así que ninguna rotura a favor puede ser por línea.
    assert all(
        item["src"] == "UL" for item in registros if item["k"] == "favor"
    )


def test_la_capa_de_evitadas_se_dibuja_y_se_apaga(
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(zoned_run, tmp_path)
    encendida = _trace_names(_step(resultado, "evitadas-por-defecto"))
    apagada = _trace_names(_step(resultado, "evitadas-apagadas"))

    assert any(nombre.startswith("ROTURA EVITADA") for nombre in encendida), encendida
    assert not any(nombre.startswith("ROTURA EVITADA") for nombre in apagada)
    assert "roturas evitadas a la vista" in _step(resultado, "evitadas-por-defecto")["notes"]


def test_sin_la_regla_nueva_no_hay_nada_que_dibujar(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La casilla se esconde, así que los dos pasos tienen que salir iguales."""
    resultado = _draw(run, tmp_path)
    encendida = _trace_names(_step(resultado, "evitadas-por-defecto"))
    apagada = _trace_names(_step(resultado, "evitadas-apagadas"))

    assert encendida == apagada
    assert not any(nombre.startswith("ROTURA EVITADA") for nombre in encendida)
