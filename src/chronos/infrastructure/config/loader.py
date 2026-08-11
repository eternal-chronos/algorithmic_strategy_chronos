"""Carga de configuración desde YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from chronos.application.backtest.config import BacktestConfig
from chronos.application.structure.config import ImpulseConfig
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.config.schema import BacktestSchema, InstrumentSchema
from chronos.infrastructure.config.structure_schema import ImpulseSchema

CONFIG_DIR = Path("config")
INSTRUMENTS_DIR = CONFIG_DIR / "instruments"


class ConfigError(Exception):
    """Error de configuración con contexto de fichero."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"No existe el fichero de configuración: {path}")
    with path.open("r", encoding="utf-8") as handle:
        content = yaml.safe_load(handle)
    if not isinstance(content, dict):
        raise ConfigError(f"{path} no contiene un mapeo YAML válido")
    return content


def load_instrument(name_or_path: str, base_dir: Path | None = None) -> InstrumentSpec:
    """Carga la ficha del símbolo por nombre (`xauusd`) o por ruta explícita."""
    candidate = Path(name_or_path)
    if candidate.suffix not in (".yaml", ".yml"):
        root = base_dir or INSTRUMENTS_DIR
        candidate = root / f"{name_or_path.lower()}.yaml"
    try:
        return InstrumentSchema(**_read_yaml(candidate)).to_domain()
    except ValidationError as error:
        raise ConfigError(f"Ficha de instrumento inválida ({candidate}):\n{error}") from error


def load_backtest_config(path: str | Path) -> BacktestConfig:
    """Carga y valida la configuración de una corrida de backtest."""
    file_path = Path(path)
    try:
        return BacktestSchema(**_read_yaml(file_path)).to_domain()
    except ValidationError as error:
        raise ConfigError(f"Configuración de backtest inválida ({file_path}):\n{error}") from error


def load_impulse_config(path: str | Path) -> ImpulseConfig:
    """Carga y valida la configuración del módulo 1 (impulso dominante)."""
    file_path = Path(path)
    try:
        return ImpulseSchema(**_read_yaml(file_path)).to_domain()
    except ValidationError as error:
        raise ConfigError(
            f"Configuración de impulso dominante inválida ({file_path}):\n{error}"
        ) from error


def load_run(
    config_path: str | Path,
    instruments_dir: Path | None = None,
) -> tuple[BacktestConfig, InstrumentSpec]:
    """Devuelve la pareja (configuración, instrumento) lista para el motor."""
    config = load_backtest_config(config_path)
    spec = load_instrument(config.instrument, instruments_dir)
    return config, spec
