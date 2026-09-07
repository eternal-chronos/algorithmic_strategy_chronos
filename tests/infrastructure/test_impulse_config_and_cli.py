"""Configuración del módulo 1 y su CLI.

Lo que se fija aquí es la puerta de §1.1: si la verificación de zona horaria
falla, la fase se detiene. No es una advertencia decorativa —un offset horario
equivocado produce impulsos distintos a los que el propietario ve— y por eso
saltárselo exige un flag explícito.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from chronos.application.structure.config import ImpulseConfig
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import AnchorMode, DojiBreakMode, SeedMode
from chronos.infrastructure.config.loader import ConfigError, load_impulse_config
from chronos.interface.cli import app
from tests.conftest import make_m1_history

runner = CliRunner()


# --- Configuración ----------------------------------------------------------


def test_el_yaml_del_proyecto_carga_y_declara_los_parametros_abiertos() -> None:
    config = load_impulse_config(Path("config/impulse.yaml"))

    assert config.enabled is True
    assert config.symbol == "XAUUSD"
    assert config.structure_side == "bid"
    assert config.rules.anchor_mode is AnchorMode.A1_LAST_COUNTER_BODY
    assert config.rules.seed_mode is SeedMode.S2_FIRST_COUNTER_BAR
    assert config.rules.doji_break_mode is DojiBreakMode.D1_NEUTRAL
    assert config.rules.warmup_bars == 50
    cerradas = " ".join(config.closed_decisions())
    for parametro in ("ANCHOR_MODE", "LEG_START_MODE", "D_SESSION_START", "STRUCTURE_SIDE"):
        assert parametro in cerradas
    abiertas = " ".join(config.open_decisions())
    for parametro in ("SEED_MODE", "DOJI_BREAK_MODE"):
        assert parametro in abiertas


def test_el_reparto_de_graficos_del_yaml_es_el_del_propietario() -> None:
    """Cada uno con el suyo, y H1 y M15 con el de H4.

    El ID vive sólo en el Diario y en H4: a H1 y a M15 no se les marca ID. Y el
    Diario se dibuja SÓLO en su gráfico: en H4 no se ve nada suyo.
    """
    charts = load_impulse_config(Path("config/impulse.yaml")).charts

    assert charts.charts == ("D", "H4", "H1", "M15")
    assert charts.overlays("D") == ("D",)
    assert charts.overlays("H4") == ("H4",)
    assert charts.overlays("H1") == ("H4",)
    assert charts.overlays("M15") == ("H4",)
    # H1 y M15 se dibujan pero no llevan detector propio.
    assert charts.detected == ("D", "H4")


def test_un_reparto_invalido_se_rechaza_al_cargar(tmp_path: Path) -> None:
    ruta = tmp_path / "malo.yaml"
    ruta.write_text(yaml.safe_dump({"charts": {"H4": ["H4", "M15"]}}), encoding="utf-8")
    with pytest.raises(DomainError, match="temporalidad superior"):
        load_impulse_config(ruta)


def test_se_puede_declarar_otro_reparto(tmp_path: Path) -> None:
    ruta = tmp_path / "otro.yaml"
    ruta.write_text(yaml.safe_dump({"charts": {"H1": ["H1", "D"]}}), encoding="utf-8")
    charts = load_impulse_config(ruta).charts
    assert charts.overlays("H1") == ("H1", "D")
    assert charts.detected == ("D", "H1")


def test_un_modo_inexistente_es_un_error_de_configuracion(tmp_path: Path) -> None:
    ruta = tmp_path / "malo.yaml"
    ruta.write_text(
        yaml.safe_dump({"rules": {"anchor_mode": "A3_lo_que_me_parezca"}}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="anchor_mode"):
        load_impulse_config(ruta)


def test_una_clave_desconocida_no_pasa_en_silencio(tmp_path: Path) -> None:
    ruta = tmp_path / "malo.yaml"
    ruta.write_text(yaml.safe_dump({"rules": {"counter_body_k": 0.5}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="counter_body_k"):
        load_impulse_config(ruta)


# --- CLI --------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Histórico correcto en UTC y su configuración apuntando a él."""
    _write_run(tmp_path, make_m1_history(weeks=10))
    return tmp_path


def _write_run(root: Path, history: pd.DataFrame, **overrides: object) -> Path:
    data_path = root / "bid.parquet"
    history.to_parquet(data_path)
    config = {
        "symbol": "XAUUSD",
        "structure_side": "bid",
        "data": {"bid_path": str(data_path)},
        "rules": {"warmup_bars": 5},
        # Sin capturas: kaleido abre un navegador headless por imagen y estos
        # tests son de la CLI, no del renderizador.
        "reporting": {"output_dir": str(root / "out"), "captures": False},
        **overrides,
    }
    path = root / "impulse.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_verify_tz_aprueba_un_historico_en_utc(workspace: Path) -> None:
    result = runner.invoke(
        app, ["structure", "verify-tz", "--config", str(workspace / "impulse.yaml")]
    )
    assert result.exit_code == 0
    assert "OK" in result.stdout


def test_verify_tz_falla_con_un_historico_desplazado(tmp_path: Path) -> None:
    config = _write_run(tmp_path, make_m1_history(weeks=10, shift_hours=5))
    result = runner.invoke(app, ["structure", "verify-tz", "--config", str(config)])
    assert result.exit_code == 1
    assert "FALLO" in result.stdout


def test_detect_se_detiene_si_la_zona_horaria_no_cuadra(tmp_path: Path) -> None:
    config = _write_run(tmp_path, make_m1_history(weeks=10, shift_hours=5))
    result = runner.invoke(app, ["structure", "detect", "--config", str(config)])

    assert result.exit_code == 1
    assert "FASE 1 DETENIDA" in result.stdout
    assert not (tmp_path / "out").exists(), "no debe escribir informes si la fase se detiene"


def test_detect_continua_con_el_flag_explicito_y_avisa(tmp_path: Path) -> None:
    config = _write_run(tmp_path, make_m1_history(weeks=10, shift_hours=5))
    result = runner.invoke(
        app, ["structure", "detect", "--config", str(config), "--skip-tz-audit"]
    )

    assert result.exit_code == 0
    assert "AVISO" in result.stdout
    assert "NO son auditables" in result.stdout


def test_detect_escribe_la_carpeta_completa(workspace: Path) -> None:
    result = runner.invoke(
        app, ["structure", "detect", "--config", str(workspace / "impulse.yaml")]
    )
    assert result.exit_code == 0

    carpetas = list((workspace / "out").iterdir())
    assert len(carpetas) == 1
    ficheros = {fichero.name for fichero in carpetas[0].iterdir()}
    assert "impulsos.csv" in ficheros
    assert "contactos.csv" in ficheros
    assert "reporte.txt" in ficheros
    assert "explorador.html" in ficheros
    assert "capturas" not in ficheros, "esta corrida las lleva desactivadas"


def test_el_informe_de_la_cli_trae_las_secciones_del_cierre(workspace: Path) -> None:
    runner.invoke(app, ["structure", "detect", "--config", str(workspace / "impulse.yaml")])
    carpeta = next((workspace / "out").iterdir())
    informe = (carpeta / "reporte.txt").read_text(encoding="utf-8")

    for seccion in (
        "A. Verificación empírica de zona horaria",
        "B. Cierre de R-02",
        "C. Censo y estadística",
        "D. Firma de lateralización",
        "E. Registro de geometría",
    ):
        assert seccion in informe


def test_la_evidencia_se_imprime_con_las_dos_columnas(workspace: Path) -> None:
    result = runner.invoke(
        app, ["structure", "evidencia", "--config", str(workspace / "impulse.yaml")]
    )

    # El histórico de la fixture no es el real, así que la regresión contra la
    # línea base falla a propósito: lo que se comprueba es que se imprime.
    assert "G.1 Día sintético" in result.stdout
    assert "esperado" in result.stdout and "obtenido" in result.stdout


def test_detect_con_el_modulo_apagado_no_escribe_nada(tmp_path: Path) -> None:
    config = _write_run(tmp_path, make_m1_history(weeks=10), enabled=False)
    result = runner.invoke(app, ["structure", "detect", "--config", str(config)])

    assert result.exit_code == 0
    assert "desactivado" in result.stdout
    assert not (tmp_path / "out").exists()


def test_detect_omite_el_grafico_que_el_historico_no_da_para_construir(
    tmp_path: Path,
) -> None:
    """Con un histórico H1 hay diario, H4 y H1, pero no M15.

    Fabricar velas M15 a partir de velas de una hora sería inventarse datos; y
    abortar la fase entera por un gráfico que no lleva impulso propio sería peor
    que trabajar con lo que hay. Se omite y se dice.
    """
    hourly = (
        make_m1_history(weeks=10)
        .resample("1h", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    config = _write_run(tmp_path, hourly)
    result = runner.invoke(app, ["structure", "detect", "--config", str(config)])

    assert result.exit_code == 0
    assert "se omite el gráfico M15" in result.stdout
    carpeta = next(iter((tmp_path / "out").iterdir()))
    tabla = pd.read_csv(carpeta / "impulsos.csv")
    # H1 se dibuja, pero el ID sólo se marca en el diario y en H4.
    assert set(tabla["timeframe"]) == {"D", "H4"}
    assert "M15" in (carpeta / "reporte.txt").read_text(encoding="utf-8")


def test_detect_no_omite_una_temporalidad_que_lleva_impulso(tmp_path: Path) -> None:
    """Si falta H4, su impulso no se puede dibujar en ningún gráfico: eso sí es fatal."""
    daily = (
        make_m1_history(weeks=10)
        .resample("1D", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open"])
    )
    config = _write_run(tmp_path, daily)
    # Con velas diarias la verificación horaria no puede pasar —no hay minutos
    # que medir— y saltaría antes que lo que este test quiere comprobar.
    result = runner.invoke(
        app, ["structure", "detect", "--config", str(config), "--skip-tz-audit"]
    )

    assert result.exit_code == 1
    # `rich` parte las líneas largas: se compara un fragmento que no se rompe.
    assert "construir H4" in result.stdout
    assert not (tmp_path / "out").exists()


def test_detect_avisa_si_falta_el_historico(tmp_path: Path) -> None:
    path = tmp_path / "impulse.yaml"
    path.write_text(
        yaml.safe_dump({"data": {"bid_path": str(tmp_path / "no-existe.parquet")}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["structure", "detect", "--config", str(path)])
    assert result.exit_code == 1
    assert "No se encontró el histórico" in result.stdout


def test_el_hash_de_la_corrida_queda_en_el_csv_y_en_run_json(workspace: Path) -> None:
    runner.invoke(app, ["structure", "detect", "--config", str(workspace / "impulse.yaml")])
    carpeta = next(iter((workspace / "out").iterdir()))

    tabla = pd.read_csv(carpeta / "impulsos.csv")
    esperado = ImpulseConfig(
        data=load_impulse_config(workspace / "impulse.yaml").data,
        rules=load_impulse_config(workspace / "impulse.yaml").rules,
    ).fingerprint()
    assert set(tabla["config_hash"]) == {esperado}
    assert esperado in (carpeta / "run.json").read_text(encoding="utf-8")


# --- Fase 3.0 · con qué regla de rotura corre la cascada ---------------------


def test_las_entradas_corren_con_el_ul_a_favor_y_el_ancla_en_contra(
    workspace: Path,
) -> None:
    """La regla del propietario desde la fase 3.0: el ID no cambia mientras una
    vela no CIERRE más allá del UL entero, y en contra manda la línea del ancla.

    La regla la fija la corrida de la fase, no el YAML: el explorador la declara
    en su cabecera y es ahí donde se comprueba, porque es lo que lee quien audita.
    """
    salida = workspace / "fase30"
    resultado = runner.invoke(
        app,
        [
            "structure",
            "entradas",
            "--config",
            str(workspace / "impulse.yaml"),
            "--salida",
            str(salida),
        ],
    )

    assert resultado.exit_code == 0, resultado.stdout
    assert "el UL mandando a favor, el ancla en contra" in resultado.stdout
    explorador = (salida / "explorador_entradas.html").read_text(encoding="utf-8")
    assert "rotura por el UL a favor y por línea del ancla en contra" in explorador
    assert "rotura por ZONA" not in explorador
    assert '"breakAgainstByZone":false' in explorador
    # Y las zonas siguen encendidas: de ellas cuelgan el toque del PUL y el veto.
    assert '"hasZones":true' in explorador


# --- Fase 3.1 · las operaciones ----------------------------------------------


def test_las_operaciones_salen_dibujadas_y_sin_una_sola_metrica(
    workspace: Path,
) -> None:
    """Lo que se entrega es el dibujo: ni R esperada, ni aciertos, ni curva.

    Lo pidió el propietario y no es cosmético: hasta que las entradas estén
    ajustadas, un número sólo diría lo buena que es una regla a medio escribir.
    """
    salida = workspace / "fase31"
    resultado = runner.invoke(
        app,
        [
            "structure",
            "operaciones",
            "--config",
            str(workspace / "impulse.yaml"),
            "--salida",
            str(salida),
        ],
    )

    assert resultado.exit_code == 0, resultado.stdout
    assert "SIN MÉTRICAS" in resultado.stdout
    # El cierre del viernes se declara: es la única regla que cierra una
    # operación sin que el precio haya llegado a ningún sitio.
    assert "cierra el mercado" in resultado.stdout
    explorador = (salida / "explorador_operaciones.html").read_text(encoding="utf-8")
    assert '"hasEntries":' in explorador
    assert '"riskReward":3' in explorador
    assert '"marketWeek":' in explorador
    # La cuenta con la que se auditan viaja con la corrida, no con el explorador.
    assert '"account":{"initial":50.0,"mode":"percent","risk":17.0}' in explorador
