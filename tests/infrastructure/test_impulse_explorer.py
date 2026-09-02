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

from chronos.application.entries.cascade import CascadeRun, CascadeStep, detect_cascade
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
    with_hourly_structure,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.lateralization import measure
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import LegStartMode
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.infrastructure.reporting.impulse_explorer import (
    ASSETS,
    BEARISH,
    BULLISH,
    OB_MARK,
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
    return DetectDominantImpulses(config).execute(series, provenance="fixture sintética")


# --- Reparto de gráficos ----------------------------------------------------


def test_el_reparto_por_defecto_es_el_del_propietario(run: ImpulseRun) -> None:
    payload = build_payload(run)
    assert payload["charts"] == [DAILY, H4, H1, M15]
    assert payload["layout"] == {
        DAILY: [DAILY],
        H4: [H4, DAILY],
        H1: [H4],
        M15: [H4],
    }


def test_h1_y_m15_llevan_velas_pero_no_impulso_propio(run: ImpulseRun) -> None:
    """El ID vive sólo en el Diario y en H4: sobre las otras dos se dibuja el de H4."""
    payload = build_payload(run)
    assert {H1, M15} <= set(payload["bars"])
    assert not {H1, M15} & set(payload["impulses"])
    assert set(payload["impulses"]) == {DAILY, H4}


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


# --- La constitución que no fue ---------------------------------------------
#
# Una vela contraria que habría dado a luz un ID ya roto no constituye: rompe y
# gira la pierna. En el gráfico eso es un rombo que no está, así que la vela
# lleva marca propia —el reloj de arena— y va con el nivel que su cierre ya había
# dejado atrás.


def test_la_constitucion_abortada_viaja_con_lo_que_la_explica(run: ImpulseRun) -> None:
    abortadas = build_payload(run)["impulses"][H4]["aborted"]
    analysis = run.analyses[H4]

    assert len(abortadas) == len(analysis.aborted)
    assert abortadas, "el fixture tiene que traer alguna para que el test mida algo"
    assert set(abortadas[0]) == {"x", "y", "d", "next", "lvl", "ln", "src"}
    # Las dos direcciones son opuestas: la del ID que no nació y la de la pierna
    # que esa misma vela abre.
    assert all(item["d"] != item["next"] for item in abortadas)


def test_la_marca_de_la_constitucion_abortada_se_dibuja_y_se_apaga(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Va en la capa de constituciones y roturas, que es donde se la busca."""
    resultado = _draw(run, tmp_path)
    encendida = _trace_names(_step(resultado, "marcas-por-defecto"))
    apagada = _trace_names(_step(resultado, "sin-marcas"))

    assert any(nombre.startswith("Constitución abortada") for nombre in encendida), encendida
    assert not any(nombre.startswith("Constitución abortada") for nombre in apagada)
    assert not any(nombre.startswith("Constitución H4") for nombre in apagada)


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
    assert "M15: H4" in html
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
    cascade: CascadeRun | None = None,
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
                zones=zones,
                cascade=cascade,
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
    assert titulos["H4"] == "Dibuja el impulso de H4 y Diario. Atajo de teclado: 4"
    assert titulos["M15"] == "Dibuja el impulso de H4. Atajo de teclado: m"


def test_cada_grafico_dibuja_su_impulso_y_el_de_contexto(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    esperado = {
        "grafico-D": {"ID Diario"},
        "grafico-H4": {"ID H4", "ID Diario"},
        "grafico-H1": {"ID H4"},
        "grafico-M15": {"ID H4"},
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
    """En M15 los marcadores son los de H4, que es el único impulso que se ve."""
    nombres = [t["name"] for t in _step(_draw(run, tmp_path), "grafico-M15")["plot"]["traces"]]
    assert "Constitución H4" in nombres
    assert not any(nombre.startswith("Constitución M15") for nombre in nombres)


def test_las_capas_se_rehacen_al_cambiar_de_grafico(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "grafico-H4")["layerLabels"] == [
        "ID H4 (principal)",
        "ID Diario (contexto)",
    ]
    assert _step(resultado, "grafico-M15")["layerLabels"] == ["ID H4 (principal)"]


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


def test_las_teclas_d_4_1_m_cambian_la_temporalidad(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Una tecla por gráfico: d = Diario, 4 = H4, 1 = H1, m = M15."""
    resultado = _draw(run, tmp_path)
    assert _step(resultado, "teclado-tf-h4")["chart"] == H4
    assert _step(resultado, "teclado-tf-m15")["chart"] == M15
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


# --- El marco del ID, y las zonas UL y PUL que ya no se dibujan ---------------
#
# Las zonas siguen viajando en el payload —de ellas cuelgan las señales y la
# cascada— pero no se pintan: en su lugar cada ID lleva su recuadro, de la vela
# que lo constituye a la que lo mata y de su ancla a su extremo.


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


@pytest.fixture
def zones(run: ImpulseRun) -> ZonesRun:
    """Las zonas de la misma corrida. La fixture del módulo las trae apagadas."""
    return detect_zones(run, replace(run.config, zones=ZonesConfig(enabled=True)))


def test_sin_zonas_el_payload_no_las_declara(run: ImpulseRun) -> None:
    """Un explorador de la fase 1 sigue siendo exactamente el de antes."""
    payload = build_payload(run)
    assert payload["hasZones"] is False
    assert payload["impulses"][H4]["zones"] == []


def test_cada_zona_viaja_con_sus_dos_bordes(run: ImpulseRun, zones: ZonesRun) -> None:
    payload = build_payload(run, zones=zones)
    assert payload["hasZones"] is True

    registros = payload["impulses"][H4]["zones"]
    assert len(registros) == len(zones.per_timeframe[H4].table)
    for registro in registros:
        assert registro["k"] in {"UL", "PUL"}
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


def test_el_pul_cuelga_de_una_vela_anterior_a_su_id(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """La vela del PUL es la del extremo del ID anterior: queda siempre detrás."""
    registros = build_payload(run, zones=zones)["impulses"][H4]["zones"]
    uls = [item for item in registros if item["k"] == "UL"]
    puls = [item for item in registros if item["k"] == "PUL"]

    assert uls and puls
    # `xd` es la vela definitoria y `x0` el nacimiento: en el PUL nunca coinciden,
    # porque entre una y otro está la muerte del ID anterior.
    assert all(item["xd"] < item["x0"] for item in puls)


def test_los_id_sin_pul_no_viajan_como_zona(run: ImpulseRun, zones: ZonesRun) -> None:
    """Sin ID anterior no hay zona que dibujar: tampoco una a medias."""
    payload = build_payload(run, zones=zones)
    sin_pul = {zoned.id_num for zoned in zones.per_timeframe[H4].without_penultimate}
    con_pul = {
        item["id"] for item in payload["impulses"][H4]["zones"] if item["k"] == "PUL"
    }

    assert not (con_pul & sin_pul)


def test_las_zonas_ya_no_se_dibujan(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """Tapaban el precio: siguen en el payload, pero no hay ni una caja."""
    nombres = _trace_names(_step(_draw(run, tmp_path, zones=zones), "marco-por-defecto"))

    assert not any(nombre.startswith("Zona ") for nombre in nombres), nombres
    assert "PUL sin confirmar" not in nombres
    assert "Confirmación del PUL" not in nombres


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
        # El otro lado es el extremo vigente en ese tramo, no siempre el final.
        niveles = [step[1] for step in impulse.get("st") or [[0, impulse["e"]]]]
        assert (lo if impulse["a"] == hi else hi) in niveles
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
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    ciega = _step(_draw(run, tmp_path, zones=zones), "ciega")
    assert len(ciega["plot"]["traces"]) == 1, _trace_names(ciega)


def test_las_notas_dicen_de_quien_es_cada_marco(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "marco-por-defecto")["notes"]

    assert "marcos de ID dibujados" in notas
    assert "cada temporalidad con su color" in notas


def test_las_notas_dicen_que_las_zonas_ya_no_se_pintan(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """Quien viene de la fase 2.0 buscaría las cajas: su ausencia hay que decirla."""
    notas = _step(_draw(run, tmp_path, zones=zones), "marco-por-defecto")["notes"]

    assert "las zonas UL y PUL no se dibujan" in notas
    assert "siguen en los datos" in notas


def test_las_capas_de_zona_no_se_dibujan_en_otro_modo_de_r36(
    run: ImpulseRun,
    zones: ZonesRun,
    variants: tuple[ModeVariant, ...],
    tmp_path: Path,
) -> None:
    """Señales y cascada se calcularon sobre los impulsos del modo activo."""
    resultado = _draw(run, tmp_path, variants, zones=zones)
    otro = _step(resultado, "modo-L2_siguiente_barra")

    assert "no se dibujan en otro modo" in otro["notes"]
    # El marco no cuelga de las zonas: es el ID, y el ID lo tienen los tres modos.
    assert _marcos(_trace_names(otro))


def test_el_replay_no_dibuja_ninguna_capa_antes_de_tiempo(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """La misma frontera para todas, marco incluido: nada más allá del reloj."""
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
    """Las mismas velas con la rotura por zona, que es la que trae la capa.

    Van los **cuatro** gráficos del reparto y no sólo los dos que llevan
    detector: H1 y M15 no aportan impulso propio, pero sus pestañas del
    explorador tienen que dibujarse igual y con la fixture recortada a las
    temporalidades detectadas no llegaban a probarse.
    """
    tuned = replace(run.config, rules=replace(run.config.rules, break_by_zone=True))
    return DetectDominantImpulses(tuned).execute(
        dict(run.chart_bars), provenance="fixture sintética"
    )


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
        # cosas se tienen que poder leer en el globo sin abrir ningún CSV. El
        # cierre no tiene por qué caer dentro de la zona: el PUL vive en otra
        # vela y puede quedar entero por detrás de la línea del ancla.
        sube = registro["k"] == "favor" if registro["d"] == "alcista" else registro["k"] != "favor"
        if sube:
            assert registro["y"] > registro["ln"]
            assert registro["y"] <= registro["zo"]
        else:
            assert registro["y"] < registro["ln"]
            assert registro["y"] >= registro["zo"]
        assert registro["z"] in ("UL", "PUL")
        assert registro["k"] in ("favor", "contra")


def test_la_rotura_real_dice_si_fue_por_zona_o_por_linea(zoned_run: ImpulseRun) -> None:
    registros = build_payload(zoned_run)["impulses"][H4]["breaks"]
    fuentes = {item["src"] for item in registros}

    assert fuentes <= {"linea", "UL", "PUL"}
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


# --- Fase 2.1 · la escalera del extremo (§3.2) ------------------------------
#
# El extremo de un ID vigente se estira. Si el explorador dibuja una sola línea
# recta con el valor final, enseña desde la constitución un precio al que el
# mercado todavía no había llegado: el mismo lookahead que el tramo punteado de
# B.1 evita por el otro lado, y el que hizo que la línea de abajo de un ID
# bajista apareciera colgando por debajo de las velas.


def _extendidos(run: ImpulseRun, timeframe: str) -> list:
    return [
        impulse
        for impulse in run.analyses[timeframe].published
        if impulse.extreme_extensions
    ]


def _segments(step: dict, prefix: str) -> set[tuple[str, str, float]]:
    """Los tramos horizontales dibujados por las trazas cuyo nombre empieza así."""
    tramos: set[tuple[str, str, float]] = set()
    for trace in step["plot"]["traces"]:
        if not trace["name"].startswith(prefix) or not trace["segments"]:
            continue
        points = trace["segments"]
        for position in range(0, len(points) - 2, 3):
            start, end = points[position], points[position + 1]
            tramos.add((start[0], end[0], start[1]))
    return tramos


def _iso(minute: int) -> str:
    return (
        (pd.Timestamp("1970-01-01", tz="UTC") + pd.Timedelta(minutes=minute))
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def test_sin_extensiones_no_hay_escalera_que_declarar(run: ImpulseRun) -> None:
    payload = build_payload(run)
    assert payload["hasSteps"] is False
    assert all(
        "st" not in impulse
        for source in payload["impulses"].values()
        for impulse in source["list"]
    )


def test_el_extremo_estirado_viaja_con_todos_sus_escalones(zoned_run: ImpulseRun) -> None:
    payload = build_payload(zoned_run)
    assert payload["hasSteps"] is True

    registros = {item["id"]: item for item in payload["impulses"][H4]["list"]}
    extendidos = _extendidos(zoned_run, H4)
    assert extendidos, "la fixture tiene que estirar algún extremo"

    for impulse in extendidos:
        escalones = registros[impulse.id_num]["st"]
        assert len(escalones) == impulse.extreme_extensions + 1
        # El primero es el extremo con el que nació y el último, el del final.
        assert escalones[0][1] == pytest.approx(round(impulse.extreme_at_constitution, 4))
        assert escalones[-1][1] == pytest.approx(round(impulse.extreme, 4))
        # Y van en orden, cada uno más lejos que el anterior en la dirección del ID.
        precios = [precio for _, precio in escalones]
        assert precios == (
            sorted(precios) if impulse.direction.value == "alcista" else sorted(precios, reverse=True)
        )

    # Los que nunca se estiraron no la llevan: son un solo tramo y `e` basta.
    quietos = {
        impulse.id_num
        for impulse in zoned_run.analyses[H4].published
        if not impulse.extreme_extensions
    }
    assert all("st" not in registros[id_num] for id_num in quietos)


def test_el_ul_no_se_remarca_aunque_el_extremo_se_estire(zoned_run: ImpulseRun) -> None:
    """Un solo rectángulo de UL por ID, y el de la vela del extremo inicial.

    La línea del extremo sigue subiendo en escalera —eso no ha cambiado— pero la
    zona se marca una vez, al constituirse el ID, y ahí se queda hasta que muere.
    """
    zones = detect_zones(
        zoned_run, replace(zoned_run.config, zones=ZonesConfig(enabled=True))
    )
    payload = build_payload(zoned_run, zones=zones)
    registros = payload["impulses"][H4]["zones"]
    extendidos = _extendidos(zoned_run, H4)
    assert extendidos, "la fixture tiene que estirar algún extremo"

    for impulse in extendidos:
        tramos = [
            zone
            for zone in registros
            if zone["id"] == impulse.id_num and zone["k"] == "UL"
        ]
        assert len(tramos) == 1
        # Nace con el ID y su borde interior es la línea con la que nació, no la
        # que le quedó al morir.
        assert tramos[0]["x0"] == _minutos(impulse.ts_constitution)
        assert tramos[0]["xd"] == _minutos(impulse.ts_extreme_at_constitution)
        assert tramos[0]["i"] == pytest.approx(round(impulse.extreme_at_constitution, 4))
        assert tramos[0]["i"] != pytest.approx(round(impulse.extreme, 4))
        # Y ningún registro de zona lleva ya escalón: no hay escalera que contar.
        assert "n" not in tramos[0] and "ns" not in tramos[0]


def _minutos(stamp: object) -> int:
    return int(pd.Timestamp(stamp).value // 60_000_000_000)


def test_el_dibujo_no_adelanta_el_extremo_estirado(
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Ningún tramo de la línea puede llevar un nivel antes de que exista."""
    resultado = _draw(zoned_run, tmp_path)
    paso = _step(resultado, "escalera-por-defecto")
    dibujados = _segments(paso, "ID H4")
    assert dibujados

    registros = {item["id"]: item for item in build_payload(zoned_run)["impulses"][H4]["list"]}
    esperados: set[tuple[str, str, float]] = set()
    for item in registros.values():
        esperados.add((_iso(item["x0"]), _iso(item["x1"]), item["a"]))
        if "st" in item:
            for position, (minute, price) in enumerate(item["st"]):
                start = item["x0"] if position == 0 else minute
                siguiente = item["st"][position + 1] if position + 1 < len(item["st"]) else None
                esperados.add((_iso(start), _iso(siguiente[0] if siguiente else item["x1"]), price))
        else:
            esperados.add((_iso(item["x0"]), _iso(item["x1"]), item["e"]))

    assert dibujados <= esperados, sorted(dibujados - esperados)[:5]
    # Y el nivel final de un ID estirado NO se dibuja desde la constitución.
    estirado = registros[_extendidos(zoned_run, H4)[0].id_num]
    assert (_iso(estirado["x0"]), _iso(estirado["x1"]), estirado["e"]) not in dibujados
    assert (
        _iso(estirado["st"][-1][0]), _iso(estirado["x1"]), estirado["e"]
    ) in dibujados


def test_los_saltos_de_la_escalera_son_una_capa_propia(
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Se apagan las marcas; los escalones se dibujan igual, que no son una capa."""
    resultado = _draw(zoned_run, tmp_path)
    encendida = _step(resultado, "escalera-por-defecto")
    apagada = _step(resultado, "escalera-sin-saltos")

    assert any(nombre.startswith("Extremo estirado") for nombre in _trace_names(encendida))
    assert not any(nombre.startswith("Extremo estirado") for nombre in _trace_names(apagada))
    assert _segments(encendida, "ID H4") == _segments(apagada, "ID H4")
    assert "extremo estirado (§3.2)" in encendida["notes"]
    assert "capa de saltos APAGADA" in apagada["notes"]


def test_hay_una_marca_por_salto(zoned_run: ImpulseRun, tmp_path: Path) -> None:
    """Una por extensión, y también las del impulso de contexto: su línea del
    extremo es igual de escalera y su salto engaña igual."""
    paso = _step(_draw(zoned_run, tmp_path), "escalera-por-defecto")

    for timeframe, etiqueta in ((H4, "H4"), (DAILY, "Diario")):
        marcas = [
            trace for trace in paso["plot"]["traces"]
            if trace["name"].startswith("Extremo estirado · " + etiqueta)
            and trace["xs"] is not None
        ]
        saltos = sum(
            impulse.extreme_extensions for impulse in _extendidos(zoned_run, timeframe)
        )
        assert saltos, f"{timeframe} tiene que estirar algún extremo en la fixture"
        assert sum(len(trace["xs"]) for trace in marcas) == saltos


def test_sin_extensiones_la_capa_de_saltos_no_se_ofrece(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    encendida = _step(resultado, "escalera-por-defecto")
    assert not any(nombre.startswith("Extremo estirado") for nombre in _trace_names(encendida))
    assert "extremo estirado" not in encendida["notes"]


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
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Lo que se abre por defecto es el gráfico legible, no el que lo lleva todo."""
    resultado = _draw(zoned_run, tmp_path, zones=detect_zones(zoned_run))
    salida = _step(resultado, "ruido-de-salida")

    assert salida["noiseLevel"] == "clean"
    assert salida["visibleMode"] == "current"
    for casilla in LIMPIO_APAGADO:
        assert salida["boxes"][casilla] is False, casilla
    for casilla in LIMPIO_ENCENDIDO:
        assert salida["boxes"][casilla] is True, casilla


def test_el_nivel_limpio_dibuja_menos_que_el_normal(
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Y «Todo» más que los dos: los tres niveles se distinguen en el dibujo."""
    resultado = _draw(zoned_run, tmp_path, zones=detect_zones(zoned_run))

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
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Un gráfico con menos marcas tiene que decir cuáles se ha callado: si no,
    la ausencia de una capa se lee como que ahí no pasó nada."""
    resultado = _draw(zoned_run, tmp_path, zones=detect_zones(zoned_run))
    notas = _step(resultado, "ruido-limpio")["notes"]

    assert "RUIDO: Limpio" in notas
    assert "capas apagadas:" in notas
    for nombre in ("limbo", "contactos", "nivel 50 %"):
        assert nombre in notas, nombre
    assert "siguen en los datos y en los informes" in notas
    assert "todas las capas encendidas" in _step(resultado, "ruido-todo")["notes"]


# --- Señales de zona · toque del PUL, rechazo y rotura del UL -----------------
#
# Son DIBUJO: se calculan sobre las zonas de la fase 2.0 y no abren ni cierran
# nada. Lo que se comprueba aquí es que viajan enteras, que se pueden apagar y
# que el explorador dice cuántas hay y qué son.

SIGNAL_KINDS = tuple(kind.value for kind in ZoneSignalKind)


def _senales(step: dict) -> list[str]:
    return [
        nombre for nombre in _trace_names(step) if nombre.split(" ")[0] in SIGNAL_KINDS
    ]


def test_sin_zonas_el_payload_no_declara_senales(run: ImpulseRun) -> None:
    """Sin zonas no hay señales: la casilla ni se enseña."""
    payload = build_payload(run)

    assert payload["hasSignals"] is False
    assert payload["impulses"][H4]["signals"] == []


def test_cada_senal_viaja_con_lo_que_hace_falta_para_juzgarla(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """El globo tiene que poder leerse sin abrir ningún CSV."""
    payload = build_payload(run, zones=zones)
    registros = payload["impulses"][H4]["signals"]

    assert payload["hasSignals"] is True
    assert registros
    for registro in registros:
        assert registro["k"] in SIGNAL_KINDS
        assert registro["z"] == ("PUL" if registro["k"] == "TOQUE_PUL" else "UL")
        assert registro["d"] in ("alcista", "bajista")
        assert registro["n"] >= 1
        assert isinstance(registro["in"], bool)
        # El marcador se planta en el contacto salvo en la rotura, donde el
        # cierre ya está fuera de la zona y es lo que hay que enseñar.
        if registro["k"] == "ROTURA_UL":
            assert registro["y"] == registro["c"]


def test_el_toque_del_ob_viaja_con_la_vela_fina_que_lo_midio(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """`src` es lo que le dice al replay cuándo se supo cada señal.

    El toque del PUL se mide en M15 y no espera al cierre de H4; el rechazo y la
    rotura del UL necesitan un cierre y se quedan en la vela del ID.
    """
    payload = build_payload(run, zones=zones)
    registros = payload["impulses"][H4]["signals"]
    toques = [item for item in registros if item["k"] == "TOQUE_PUL"]

    assert toques
    assert {item["src"] for item in toques} == {"M15"}
    assert {item["src"] for item in registros if item["k"] != "TOQUE_PUL"} == {H4}
    assert payload["spans"]["M15"] < payload["spans"][H4]
    # La marca cae DENTRO de la vela de H4: su minuto no es el de ninguna vela.
    velas = set(payload["bars"][H4]["t"])
    assert any(item["x"] not in velas for item in toques)


def test_las_senales_son_de_los_id_de_su_temporalidad(
    run: ImpulseRun, zones: ZonesRun
) -> None:
    """H1 y M15 no llevan detector: tampoco zonas propias ni señales propias."""
    payload = build_payload(run, zones=zones)
    ids = {impulse["id"] for impulse in payload["impulses"][H4]["list"]}

    assert {item["id"] for item in payload["impulses"][H4]["signals"]} <= ids
    # Los gráficos de H1 y M15 existen, pero no llevan bloque de impulsos: sin
    # detector no hay ID, sin ID no hay zonas y sin zonas no hay señales.
    assert set(payload["impulses"]) == {DAILY, H4}
    assert H1 in payload["charts"]


def test_la_capa_de_senales_se_dibuja_y_se_apaga(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path, zones=zones)
    encendida = _senales(_step(resultado, "senales-por-defecto"))
    apagada = _senales(_step(resultado, "senales-apagadas"))

    assert encendida, _trace_names(_step(resultado, "senales-por-defecto"))
    assert not apagada


def test_sin_zonas_no_hay_ninguna_senal_que_dibujar(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """La casilla se esconde, así que los dos pasos tienen que salir iguales."""
    resultado = _draw(run, tmp_path)
    encendida = _trace_names(_step(resultado, "senales-por-defecto"))
    apagada = _trace_names(_step(resultado, "senales-apagadas"))

    assert encendida == apagada
    assert not _senales(_step(resultado, "senales-por-defecto"))


def test_las_notas_dicen_cuantas_senales_hay_y_que_son_dibujo(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """Una capa de señales que no dice que no opera se lee como una entrada."""
    notas = _step(_draw(run, tmp_path, zones=zones), "senales-por-defecto")["notes"]

    assert "señales de zona a la vista" in notas
    assert "no abren ni cierran nada" in notas
    for kind in SIGNAL_KINDS:
        assert kind in notas, kind


def test_cada_tipo_de_senal_tiene_su_propia_marca(
    run: ImpulseRun, zones: ZonesRun, tmp_path: Path
) -> None:
    """Lo nuevo tiene que distinguirse: una traza por tipo y su entrada propia
    en la leyenda."""
    nombres = _senales(_step(_draw(run, tmp_path, zones=zones), "senales-por-defecto"))

    assert len(nombres) == len(set(nombres))
    assert {nombre.split(" ")[0] for nombre in nombres} <= set(SIGNAL_KINDS)
    assert all(nombre.endswith(" H4") for nombre in nombres), nombres


def test_las_senales_no_se_dibujan_en_otro_modo_de_r36(
    run: ImpulseRun,
    zones: ZonesRun,
    variants: tuple[ModeVariant, ...],
    tmp_path: Path,
) -> None:
    """Salen de las zonas del modo activo: en otro serían señales de otros ID."""
    resultado = _draw(run, tmp_path, variants, zones=zones)

    assert not _senales(_step(resultado, "modo-L2_siguiente_barra"))


def test_con_la_regla_de_la_fase_21_la_rotura_del_ul_se_dibuja(
    zoned_run: ImpulseRun, tmp_path: Path
) -> None:
    """Con `break_by_zone` toda rotura a favor atraviesa el UL: hay que verla."""
    zones = detect_zones(zoned_run, replace(zoned_run.config, zones=ZonesConfig(enabled=True)))
    payload = build_payload(zoned_run, zones=zones)
    roturas = [
        item for item in payload["impulses"][H4]["signals"] if item["k"] == "ROTURA_UL"
    ]
    a_favor = [item for item in payload["impulses"][H4]["breaks"] if item["k"] == "favor"]

    assert roturas
    assert {item["x"] for item in roturas} == {item["x"] for item in a_favor}

    nombres = _senales(
        _step(_draw(zoned_run, tmp_path, zones=zones), "senales-por-defecto")
    )
    assert any(nombre.startswith("ROTURA_UL") for nombre in nombres), nombres


# --- La cascada H4 → H1, con el Diario de veto ------------------------------
#
# Son SEÑALES: se calculan sobre los toques que la fase 2.0 ya medía y no abren
# ni cierran nada. Lo que se comprueba aquí es que cada paso viaja al gráfico en
# el que se mira, que el PUL de H1 llega con su caja, que la capa se puede apagar
# y que el explorador dice cuántos pasos hay a la vista y qué son.

CASCADE_STEPS = tuple(step.value for step in CascadeStep)


@pytest.fixture
def entries_run() -> ImpulseRun:
    """La corrida de la fase 3.0: la misma fixture, pero con ID propio en H1.

    La cascada espera en H1 a que el ID se ponga en la dirección del de H4, así
    que sin detector en esa temporalidad no hay segundo escalón que dibujar.
    """
    history = make_m1_history(weeks=16)
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
        charts=with_hourly_structure(ChartsConfig()),
    )
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }
    return DetectDominantImpulses(config).execute(series, provenance="fixture sintética")


@pytest.fixture
def entries_zones(entries_run: ImpulseRun) -> ZonesRun:
    return detect_zones(entries_run, replace(entries_run.config, zones=ZonesConfig(enabled=True)))


@pytest.fixture
def cascade(entries_run: ImpulseRun, entries_zones: ZonesRun) -> CascadeRun:
    return detect_cascade(entries_run, entries_zones)


def _pasos(step: dict) -> list[str]:
    return [
        nombre for nombre in _trace_names(step) if nombre.split(" ")[0] in CASCADE_STEPS
    ]


def test_sin_cascada_el_payload_no_la_declara(run: ImpulseRun) -> None:
    """Sin ella la casilla ni se enseña: una capa vacía sólo hace dudar."""
    payload = build_payload(run)

    assert payload["hasCascade"] is False
    assert payload["cascade"] == {}
    assert payload["cascadeChain"] == {}


def test_la_cascada_viaja_por_grafico_y_no_por_temporalidad(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """El toque cuelga de un ID de H4 y la confirmación habla del ID de H1."""
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)

    assert payload["hasCascade"] is True
    assert set(payload["cascade"]) <= {DAILY, H4, H1}
    assert {item["k"] for item in payload["cascade"][DAILY]} == {"ZONA_DIARIA"}
    assert {item["k"] for item in payload["cascade"][H4]} <= {"BUSCAR_H1", "H4_DESCARTADO"}
    en_h1 = payload["cascade"].get(H1, [])
    assert en_h1
    assert {item["k"] for item in en_h1} == {"CONFIRMA_PUL_H1", "TOQUE_PUL_H1"}
    # La marca nombra al ID de H1, que es el impulso principal de ese gráfico:
    # por eso la capa puede obedecer el filtro de ID visibles.
    assert {item["tf"] for item in en_h1} == {H1}


def test_cada_paso_viaja_con_lo_que_hace_falta_para_juzgarlo(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    pasos = [item for marcas in payload["cascade"].values() for item in marcas]

    for item in pasos:
        assert {"x", "y", "k", "d", "tf", "id", "src", "seq", "lvl", "r", "c"} <= set(item)
        assert item["d"] in ("alcista", "bajista")
    # Las dos raíces no cuelgan de nada: el toque de H4 porque la cascada
    # empieza ahí, y el tramo diario porque no es un escalón sino un veto.
    conocidos = {item["seq"] for item in pasos}
    for item in pasos:
        if item["k"] in ("ZONA_DIARIA", "BUSCAR_H1"):
            assert "p" not in item
        else:
            assert item["p"] in conocidos


def test_la_ventana_dice_por_que_se_cerro(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """«Hasta aquí» no basta: sin el motivo no se puede auditar la regla.

    Son tres —romper el PUL, llegar al UL o morirse el ID— y el tramo diario
    lleva el suyo, que se mide distinto a propósito.
    """
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    busquedas = [
        item for item in payload["cascade"][H4] if item["k"] == "BUSCAR_H1"
    ]
    tramos = payload["cascade"][DAILY]

    assert busquedas
    for item in busquedas:
        assert ("why" in item) == ("end" in item)
        assert item.get("why", "ROTURA_PUL") in ("ROTURA_PUL", "TOQUE_UL", "MUERTE_ID")
    assert any(item.get("why") == "TOQUE_UL" for item in busquedas), (
        "ninguna búsqueda se cerró por el UL: la regla no se está dibujando"
    )
    for item in tramos:
        assert item.get("why", "SALIDA_ZONA") in ("SALIDA_ZONA", "MUERTE_ID")


def test_el_globo_de_la_ventana_cuenta_el_motivo(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    """Y lo cuenta en el navegador, que es donde el propietario lo lee."""
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)
    trazas = _step(resultado, f"cascada-{H4}")["plot"]["traces"]
    globos = [
        texto
        for traza in trazas
        if traza["name"].startswith("BUSCAR_H1")
        for texto in (traza.get("captions") or [])
    ]

    assert globos
    assert any("llegó al UL" in texto for texto in globos)
    assert any("salir del PUL a favor NO la cierra" in texto for texto in globos)


def test_la_cadena_deja_remontar_hasta_el_toque_de_h4(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """El padre de una marca de H1 se dibuja en otro gráfico: sin la cadena no
    habría forma de contar de dónde viene."""
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    chain = payload["cascadeChain"]

    esperado = {"CONFIRMA_PUL_H1": "BUSCAR_H1", "TOQUE_PUL_H1": "CONFIRMA_PUL_H1"}
    for item in payload["cascade"][H1]:
        assert chain[str(item["p"])][1] == esperado[item["k"]]
    # Y el descarte remonta al tramo diario que le prohibió mirar, que es lo
    # único que el Diario aporta a la cascada.
    for item in payload["cascade"][H4]:
        if item["k"] == "H4_DESCARTADO":
            assert chain[str(item["p"])][1] == "ZONA_DIARIA"


def test_el_ob_de_h1_viaja_con_la_vela_de_su_ancla(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """La caja se dibuja sobre una vela ANTERIOR a la marca —la del ancla del ID
    de H1—: sin ese extremo el rectángulo no se puede pintar."""
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    cajas = [
        item for item in payload["cascade"].get(H1, []) if item["k"] == "CONFIRMA_PUL_H1"
    ]

    assert cajas
    for item in cajas:
        assert item["x0"] < item["x"]
        assert item["lo"] <= item["lvl"] <= item["hi"]


def test_la_senal_de_h1_viaja_fechada_en_la_vela_fina(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """El toque del PUL de H1 no espera al cierre de su vela, igual que el de H4
    y el del Diario: va medido en M15 y por eso puede caer en mitad de una vela
    de H1. Si esperase al cierre, todas las marcas caerían en la rejilla horaria.

    Y no lleva `x0`: la caja del PUL ya la dibuja la confirmación, y repetirla
    sería pintar dos veces la misma zona.
    """
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    senales = [item for item in payload["cascade"][H1] if item["k"] == "TOQUE_PUL_H1"]

    assert senales
    for item in senales:
        assert item["src"] == M15
        assert "x0" not in item
        assert item["lo"] <= item["y"] <= item["hi"]
    assert any(item["x"] % 60 for item in senales), "todas caen en la apertura de una vela de H1"


def test_la_senal_del_toque_de_h1_se_dibuja_en_h1(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    """Capa propia y marca propia: la señal que cierra la cascada tiene que
    distinguirse de la confirmación que la trajo."""
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)

    assert f"TOQUE_PUL_H1 {H1}" in _trace_names(_step(resultado, f"cascada-{H1}"))
    assert not [
        nombre
        for nombre in _trace_names(_step(resultado, f"cascada-apagada-{H1}"))
        if nombre.startswith("TOQUE_PUL_H1")
    ]


def test_la_franja_de_operativa_viaja_en_el_payload(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """Como dato, no como texto montado: el explorador la escribe donde toca."""
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)

    assert payload["tradingWindow"] == {
        "tz": "America/New_York",
        "from": "03:00",
        "to": "12:00",
    }
    assert build_payload(entries_run)["tradingWindow"] == {}


def test_el_estado_dice_que_solo_se_opera_dentro_de_la_franja(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    """Lo descartado por la hora no se dibuja, así que un tramo sin marcas se
    leería como que la regla no encontró nada. El estado tiene que decirlo."""
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)
    notas = _step(resultado, f"cascada-{H1}")["notes"]

    assert "de 03:00 a 12:00 de America/New_York" in notas
    assert "no se busca ni se señala nada" in notas
    # Con la capa apagada no se habla de la cascada ni de su franja.
    assert "03:00" not in _step(resultado, f"cascada-apagada-{H1}")["notes"]


def test_las_marcas_no_llevan_el_texto_ya_montado(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun
) -> None:
    """Igual que el resto del payload: el globo se compone en el navegador."""
    payload = build_payload(entries_run, zones=entries_zones, cascade=cascade)
    pasos = [item for marcas in payload["cascade"].values() for item in marcas]

    assert all(not isinstance(valor, str) or len(valor) <= 16
               for item in pasos for valor in item.values())


def test_la_capa_de_cascada_se_dibuja_y_se_apaga(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)

    for grafico in (DAILY, H4, H1):
        encendida = _pasos(_step(resultado, f"cascada-{grafico}"))
        apagada = _pasos(_step(resultado, f"cascada-apagada-{grafico}"))
        assert encendida, _trace_names(_step(resultado, f"cascada-{grafico}"))
        assert not apagada


def test_la_ventana_de_busqueda_se_dibuja_con_los_pasos(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    """Una marca que dice cuándo se empezó a buscar pero no hasta cuándo no
    permite auditar la regla de la ventana."""
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)
    encendida = _trace_names(_step(resultado, f"cascada-{DAILY}"))
    apagada = _trace_names(_step(resultado, f"cascada-apagada-{DAILY}"))

    assert "ventana de búsqueda" in encendida
    assert "ventana de búsqueda" not in apagada


def test_el_ob_de_h1_se_dibuja_como_caja(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    """El PUL de H1 es el de su ID —la vela del ancla entera— y como zona se
    pinta: el marcador dice cuándo se supo y la caja, sobre qué."""
    resultado = _draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade)

    assert "PUL de H1" in _trace_names(_step(resultado, f"cascada-{H1}"))
    assert "PUL de H1" not in _trace_names(_step(resultado, f"cascada-apagada-{H1}"))


def test_sin_cascada_en_el_payload_la_capa_no_dibuja_nada(
    entries_run: ImpulseRun, entries_zones: ZonesRun, tmp_path: Path
) -> None:
    resultado = _draw(entries_run, tmp_path, zones=entries_zones)

    assert not _pasos(_step(resultado, f"cascada-{DAILY}"))
    assert not _pasos(_step(resultado, f"cascada-apagada-{DAILY}"))


def test_las_notas_dicen_cuantos_pasos_hay_y_que_son_senales(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    notas = _step(_draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade), f"cascada-{DAILY}")["notes"]

    assert "cascada a la vista" in notas
    assert "SON SEÑALES" in notas
    assert "no hay entradas" in notas


def test_la_cascada_no_se_dibuja_en_la_auditoria_ciega(
    entries_run: ImpulseRun, entries_zones: ZonesRun, cascade: CascadeRun, tmp_path: Path
) -> None:
    ciega = _step(_draw(entries_run, tmp_path, zones=entries_zones, cascade=cascade), "ciega")

    assert len(ciega["plot"]["traces"]) == 1


# --- I.1 · el simulador de entradas ------------------------------------------
#
# Dos botones que arman, un clic que planta la caja y arrastres que la mueven.
# Es lo único del gráfico que dibuja el propietario y no el motor, así que lo
# que se comprueba aquí es que cae donde se pulsa, que se mueve como se dice y
# que el estado deja claro que no es una operación.


def _caja(step: dict) -> dict[str, dict]:
    return {forma["name"]: forma for forma in step["plot"]["sim"]}


def _niveles(step: dict) -> dict[str, float]:
    caja = _caja(step)
    return {
        "entrada": caja["sim-entrada"]["y0"],
        "objetivo": caja["sim-objetivo"]["y1"],
        "stop": caja["sim-riesgo"]["y1"],
    }


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
    assert caja["sim-entrada"]["label"] == "LARGO · R:R 1:2,0"


def test_las_notas_dicen_que_la_caja_no_es_una_operacion(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "sim-largo")["notes"]

    assert "simulación LARGO" in notas
    assert "entrada 1.2000" in notas
    assert "stop 1.1850 (150 pips)" in notas
    assert "objetivo 1.2300 (300 pips)" in notas
    assert "R:R 1:2,0" in notas
    assert "ES DIBUJO A MANO" in notas
    assert "no hay orden" in notas


def test_arrastrar_el_stop_no_mueve_la_entrada_y_respeta_el_ratio(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Con un R:R fijo, el stop es lo que se decide y el objetivo su
    consecuencia: se va con él y el ratio no se mueve."""
    resultado = _draw(run, tmp_path)
    paso = _step(resultado, "sim-stop-arrastrado")
    antes = _niveles(_step(resultado, "sim-largo"))
    despues = _niveles(paso)

    assert despues["entrada"] == antes["entrada"]
    assert despues["stop"] < antes["stop"], "el arrastre iba hacia abajo: más riesgo"
    assert despues["objetivo"] > antes["objetivo"], "el objetivo sigue al stop"
    # El dinero en juego no lo mueve el stop: el riesgo es del capital y lo que
    # cambia con los pips es el tamaño de la posición, no lo que se arriesga.
    assert _caja(paso)["sim-riesgo"]["label"] == "riesgo 332 pips · -1,00 $"
    assert _caja(paso)["sim-entrada"]["label"] == "LARGO · R:R 1:2,0"
    assert paso["simRatio"] == "2"


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
    niveles = _niveles(corto)

    assert niveles["objetivo"] < niveles["entrada"] < niveles["stop"]
    assert _caja(corto)["sim-entrada"]["label"].startswith("CORTO")
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


def test_el_ratio_recoloca_el_objetivo_sin_tocar_el_stop(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _niveles(_step(resultado, "sim-entrada-arrastrada"))
    paso = _step(resultado, "sim-ratio-1-3")
    despues = _niveles(paso)

    assert paso["simRatio"] == "3"
    assert (despues["stop"], despues["entrada"]) == (antes["stop"], antes["entrada"])
    riesgo = despues["entrada"] - despues["stop"]
    assert despues["objetivo"] - despues["entrada"] == pytest.approx(3 * riesgo, abs=1e-4)
    assert _caja(paso)["sim-entrada"]["label"] == "LARGO · R:R 1:3,0"


def test_con_el_ratio_puesto_el_stop_arrastra_al_objetivo(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _niveles(_step(resultado, "sim-ratio-1-3"))
    paso = _step(resultado, "sim-stop-con-candado")
    despues = _niveles(paso)

    assert despues["stop"] > antes["stop"], "el arrastre iba hacia arriba: menos riesgo"
    assert despues["objetivo"] < antes["objetivo"], "el objetivo se acerca con él"
    riesgo = despues["entrada"] - despues["stop"]
    assert despues["objetivo"] - despues["entrada"] == pytest.approx(3 * riesgo, abs=1e-4)
    assert paso["simRatio"] == "3"


def test_arrastrar_el_objetivo_suelta_el_candado(run: ImpulseRun, tmp_path: Path) -> None:
    """Manda lo que se ve: si el objetivo se mueve a mano, ningún botón puede
    seguir diciendo que el ratio es suyo."""
    resultado = _draw(run, tmp_path)
    antes = _niveles(_step(resultado, "sim-stop-con-candado"))
    paso = _step(resultado, "sim-objetivo-a-mano")
    despues = _niveles(paso)

    assert paso["simRatio"] is None
    assert (despues["stop"], despues["entrada"]) == (antes["stop"], antes["entrada"])
    assert despues["objetivo"] < antes["objetivo"]
    assert "a mano" in paso["notes"]


def test_fijar_un_ratio_sin_caja_no_rompe_nada(run: ImpulseRun, tmp_path: Path) -> None:
    """Elegir el R:R antes de plantar es el gesto natural: el botón se queda
    pulsado y la caja siguiente nace con él."""
    paso = _step(_draw(run, tmp_path), "ratio-sin-caja")

    assert paso["simRatio"] == "2"
    assert not paso["plot"]["sim"]


def test_la_caja_nueva_se_planta_con_el_ratio_puesto(
    run: ImpulseRun, tmp_path: Path
) -> None:
    corto = _step(_draw(run, tmp_path), "sim-corto")
    niveles = _niveles(corto)

    assert corto["simRatio"] == "4"
    riesgo = niveles["stop"] - niveles["entrada"]
    assert niveles["entrada"] - niveles["objetivo"] == pytest.approx(4 * riesgo, abs=1e-4)
    assert _caja(corto)["sim-entrada"]["label"] == "CORTO · R:R 1:4,0"


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


# --- El reparto de los marcos y su color por temporalidad --------------------
#
# Cada gráfico dibuja el marco de su ID y el de la temporalidad que le toca: el
# del Diario sobre H4, el de H4 sobre H1 y —cuando H1 lleva detector, que es la
# fase 3.0— el de H1 sobre M15. Y el color lo pone la temporalidad, no la
# dirección: es lo que permite saber de quién es cada recuadro cuando en el
# mismo gráfico hay dos.


def _marco_de(step: dict, timeframe: str) -> list[dict]:
    return [
        trace
        for trace in step["plot"]["traces"]
        if trace["name"] == f"Marco ID {timeframe}"
        or trace["name"] == f"Marco ID {timeframe} (contexto)"
    ]


def test_el_grafico_fino_lleva_el_id_de_h1_en_la_fase_3(
    entries_run: ImpulseRun,
) -> None:
    """En M15 lo que se mira es el ID donde se fecha el toque, no el de H4."""
    payload = build_payload(entries_run)

    assert payload["layout"][M15] == [H1]
    assert payload["layout"][H1] == [H1, H4]
    assert payload["layout"][H4] == [H4, DAILY]


def test_en_m15_se_dibuja_el_marco_de_h1_y_no_el_de_h4(
    entries_run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(entries_run, tmp_path), f"marco-de-{M15}")

    assert _marco_de(paso, H1), _trace_names(paso)
    assert not _marco_de(paso, H4), _trace_names(paso)
    assert not _marco_de(paso, DAILY)


def test_en_h1_se_dibujan_el_suyo_y_el_de_h4(
    entries_run: ImpulseRun, tmp_path: Path
) -> None:
    paso = _step(_draw(entries_run, tmp_path), f"marco-de-{H1}")

    assert _marco_de(paso, H1), _trace_names(paso)
    assert _marco_de(paso, H4), _trace_names(paso)
    # El de H4 es el de la temporalidad de encima y se dice en el nombre.
    assert all("(contexto)" not in trace["name"] for trace in _marco_de(paso, H1))
    assert all("(contexto)" in trace["name"] for trace in _marco_de(paso, H4))
    assert all(trace["dash"] == "dot" for trace in _marco_de(paso, H4))


def test_cada_temporalidad_pinta_su_marco_de_su_color(
    entries_run: ImpulseRun, tmp_path: Path
) -> None:
    """Dos marcos del mismo gráfico no pueden salir del mismo color por ir los
    dos al alza: el color dice de QUIÉN es cada uno."""
    paso = _step(_draw(entries_run, tmp_path), f"marco-de-{H1}")
    palette = build_payload(entries_run)["colors"]["timeframes"]

    def color(timeframe: str) -> set[tuple[int, int, int]]:
        return {_rgb(trace["color"]) for trace in _marco_de(paso, timeframe)}

    assert color(H1) == {_rgb(palette[H1])}
    assert color(H4) == {_rgb(palette[H4])}
    assert color(H1) != color(H4)
    assert _rgb(palette[DAILY]) not in color(H1) | color(H4)


def test_las_notas_dicen_de_quien_son_los_marcos_de_este_grafico(
    entries_run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(entries_run, tmp_path)
    en_m15 = _step(resultado, f"marco-de-{M15}")["notes"]
    en_h1 = _step(resultado, f"marco-de-{H1}")["notes"]

    def dibujados(notas: str) -> str:
        return notas.split("marcos de ID dibujados: ")[1].split(" · ")[0]

    assert dibujados(en_m15).endswith(f"de {H1}"), dibujados(en_m15)
    assert f"de {H4}" not in dibujados(en_m15)
    assert f"de {H1}" in dibujados(en_h1) and f"de {H4}" in dibujados(en_h1)
    assert "cada temporalidad con su color" in en_h1


def _rgb(colour: str) -> tuple[int, int, int]:
    """El mismo tono, venga como `#rrggbb` o como `rgba(r,g,b,a)`."""
    if colour.startswith("#"):
        return tuple(int(colour[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
    numbers = colour[colour.index("(") + 1 : colour.index(")")].split(",")
    return tuple(int(value) for value in numbers[:3])  # type: ignore[return-value]


# --- I.3 · el recuadro del OB -------------------------------------------------
#
# Un rectángulo que planta el PROPIETARIO para señalar dónde ve un OB. No lo ha
# detectado nadie: en el proyecto no hay regla de OB. Lo que se comprueba aquí es
# que cae donde se pulsa, que se mueve como se dice, que se distingue de todo lo
# que dibuja el motor y que el estado deja claro de quién es.


def _recuadros(step: dict) -> list[dict]:
    return step["plot"]["ob"]


def _alto(recuadro: dict) -> float:
    return recuadro["y1"] - recuadro["y0"]


def test_el_boton_del_ob_arma_y_lo_dice_antes_de_plantar_nada(
    run: ImpulseRun, tmp_path: Path
) -> None:
    armado = _step(_draw(run, tmp_path), "ob-armado")

    assert armado["obArmed"]
    assert not _recuadros(armado), "armar no puede dibujar todavía ningún recuadro"
    assert "RECUADRO DE OB ARMADO" in armado["notes"]
    assert armado["simCursor"] == "crosshair"


def test_escape_desarma_el_recuadro_sin_plantarlo(run: ImpulseRun, tmp_path: Path) -> None:
    desarmado = _step(_draw(run, tmp_path), "ob-desarmado")

    assert not desarmado["obArmed"]
    assert not _recuadros(desarmado)
    assert "RECUADRO DE OB ARMADO" not in desarmado["notes"]


def test_el_recuadro_se_planta_centrado_en_el_precio_del_clic(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """El recorrido pulsa en el centro del encuadre que él mismo ha fijado
    (1,05 → 1,35), así que el recuadro tiene que salir centrado en 1,2000."""
    recuadros = _recuadros(_step(_draw(run, tmp_path), "ob-plantado"))

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
    """Magenta y punteado: el color no lo usa ninguna capa calculada, así que lo
    que se ve en magenta lo ha puesto una mano."""
    recuadro = _recuadros(_step(_draw(run, tmp_path), "ob-plantado"))[0]

    assert _rgb(recuadro["color"]) == _rgb(OB_MARK)
    assert recuadro["dash"] == "dot"
    del_motor = {_rgb(BULLISH), _rgb(BEARISH)} | {
        _rgb(colour) for colour in TIMEFRAME_COLORS.values()
    }
    assert _rgb(OB_MARK) not in del_motor


def test_arrastrar_el_techo_no_mueve_el_suelo(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    antes = _recuadros(_step(resultado, "ob-plantado"))[0]
    despues = _recuadros(_step(resultado, "ob-techo-arrastrado"))[0]

    assert despues["y1"] > antes["y1"], "el arrastre iba hacia arriba"
    assert despues["y0"] == antes["y0"], "el suelo se queda donde estaba"
    assert (despues["x0"], despues["x1"]) == (antes["x0"], antes["x1"])


def test_arrastrar_por_dentro_mueve_el_recuadro_entero(
    run: ImpulseRun, tmp_path: Path
) -> None:
    resultado = _draw(run, tmp_path)
    antes = _recuadros(_step(resultado, "ob-techo-arrastrado"))[0]
    despues = _recuadros(_step(resultado, "ob-movido"))[0]

    assert despues["y1"] < antes["y1"] and despues["y0"] < antes["y0"], "bajó entero"
    assert _alto(despues) == pytest.approx(_alto(antes), abs=1e-3)
    assert despues["x0"] > antes["x0"] and despues["x1"] > antes["x1"], "y se fue a la derecha"


def test_se_pueden_marcar_varios_recuadros(run: ImpulseRun, tmp_path: Path) -> None:
    """En un mismo gráfico hay el OB de H4 y el de H1: enseñarlos de uno en uno
    no dice lo que hay que decir."""
    segundo = _step(_draw(run, tmp_path), "ob-segundo")
    recuadros = _recuadros(segundo)

    assert [recuadro["label"] for recuadro in recuadros] == [
        "OB 1 (a mano)",
        "OB 2 (a mano)",
    ]
    assert recuadros[1]["y1"] < recuadros[0]["y0"], "el segundo se plantó más abajo"
    assert "OB marcados a mano: 2 recuadros" in segundo["notes"]


def test_quitar_el_ultimo_deja_los_demas(run: ImpulseRun, tmp_path: Path) -> None:
    resultado = _draw(run, tmp_path)
    deshecho = _step(resultado, "ob-deshecho")

    assert [recuadro["label"] for recuadro in _recuadros(deshecho)] == ["OB 1 (a mano)"]
    assert not deshecho["obUndoDisabled"], "todavía queda uno que quitar"


def test_quitar_todos_borra_los_recuadros_y_lo_que_decian(
    run: ImpulseRun, tmp_path: Path
) -> None:
    limpio = _step(_draw(run, tmp_path), "ob-limpio")

    assert not _recuadros(limpio)
    assert limpio["obUndoDisabled"] and limpio["obClearDisabled"], (
        "sin recuadros no hay nada que quitar"
    )
    assert "OB marcados a mano" not in limpio["notes"]


def test_las_notas_dicen_que_el_recuadro_no_lo_ha_detectado_el_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    notas = _step(_draw(run, tmp_path), "ob-plantado")["notes"]

    assert "OB marcados a mano: 1 recuadro" in notas
    assert "NO los ha detectado el motor" in notas
    assert "no hay regla de OB en el proyecto" in notas


def test_los_recuadros_no_cuentan_como_capa_del_motor(
    run: ImpulseRun, tmp_path: Path
) -> None:
    """Los rectángulos del OB no son sombreado del limbo: los recuentos de formas
    del motor tienen que seguir diciendo lo mismo con ellos puestos."""
    resultado = _draw(run, tmp_path)

    assert _step(resultado, "ob-segundo")["plot"]["shapes"] == (
        _step(resultado, "ob-limpio")["plot"]["shapes"]
    )


def test_sin_recuadros_los_botones_de_quitar_estan_apagados(
    run: ImpulseRun, tmp_path: Path
) -> None:
    vacio = _step(_draw(run, tmp_path), "ob-sin-nada")

    assert vacio["obUndoDisabled"] and vacio["obClearDisabled"]
    assert not vacio["obArmed"]
