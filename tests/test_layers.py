"""La dirección de las dependencias, comprobada en vez de prometida.

`infrastructure → application → domain`. Nunca al revés. Y `domain/` sin red,
disco ni reloj: si algo de eso se cuela, la misma estrategia deja de correr
igual en backtest, paper y live.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "chronos"

#: Qué capas puede importar cada capa, además de sí misma.
ALLOWED = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "interface": {"domain", "application", "infrastructure"},
}

#: Módulos de la biblioteca estándar que atan el dominio al mundo exterior.
FORBIDDEN_IN_DOMAIN = {"pathlib", "os", "socket", "urllib", "http", "sqlite3", "requests", "httpx"}


def _modules(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_las_dependencias_apuntan_hacia_adentro(layer: str) -> None:
    permitidas = ALLOWED[layer] | {layer}
    for path in _modules(layer):
        for name in _imports(path):
            if not name.startswith("chronos."):
                continue
            importada = name.split(".")[1]
            assert importada in permitidas, (
                f"{path.relative_to(SRC)} importa {name}: "
                f"{layer} no puede depender de {importada}"
            )


def test_el_dominio_no_toca_red_ni_disco_ni_reloj() -> None:
    for path in _modules("domain"):
        nombres = {name.split(".")[0] for name in _imports(path)}
        prohibidos = nombres & FORBIDDEN_IN_DOMAIN
        assert not prohibidos, f"{path.relative_to(SRC)} importa {', '.join(sorted(prohibidos))}"

        fuente = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in fuente, f"{path.relative_to(SRC)} llama a datetime.now()"
        assert "Timestamp.now(" not in fuente, f"{path.relative_to(SRC)} llama a Timestamp.now()"


def test_la_geometria_no_la_puede_leer_ninguna_regla_del_dominio() -> None:
    """Sección E: los campos se persisten y no los lee el dominio.

    La regla de capas ya lo impide en general; esto lo deja escrito con nombre
    propio, porque es una promesa concreta hecha al propietario.
    """
    for path in _modules("domain"):
        assert "geometry" not in _imports(path), (
            f"{path.relative_to(SRC)} importa la geometría de la sección E: "
            "esos campos no puede leerlos ninguna regla"
        )


def test_el_detector_no_sabe_nada_de_la_medicion_de_contactos() -> None:
    """La detección de impulsos no cambia porque se midan contactos."""
    detector = SRC / "domain" / "structure" / "detector.py"
    importados = _imports(detector)

    assert not any("contacts" in name for name in importados)
    assert not any("lateralization" in name for name in importados)


def test_nadie_del_motor_lee_las_senales_de_zona() -> None:
    """Las señales de zona son dibujo: ningún módulo que decida puede importarlas.

    Se comprueba sobre `domain/` y `application/` enteros y no sólo sobre el
    detector: la promesa hecha al propietario es que encenderlas no mueve un
    impulso, ni una zona, ni una rotura.
    """
    permitido = {
        SRC / "domain" / "structure" / "zone_signals.py",
        SRC / "application" / "structure" / "zone_signals.py",
    }
    for layer in ("domain", "application"):
        for path in _modules(layer):
            if path in permitido:
                continue
            assert not any("zone_signals" in name for name in _imports(path)), (
                f"{path.relative_to(SRC)} importa las señales de zona"
            )
