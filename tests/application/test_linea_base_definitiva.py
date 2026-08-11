"""Línea base definitiva de la fase 1, fijada en un test.

Todos los parámetros que el propietario tenía que cerrar están cerrados: ancla
`A1_last_counter_body`, arranque de pierna `L1_actual`, sesión anclada a las
18:00 de Nueva York —para el diario y para H4— y lado `bid`. Este fichero fija
el recuento de impulsos y el `config_hash` de esa corrida para que cualquier
cambio futuro que los mueva salte de inmediato.

Las cifras anteriores quedan archivadas como provisionales y no se comprueban:
`4c299bcf2fba` con 477 / 2.068 / 7.416 impulsos (ancla A2 y corte diario en
00:00 UTC, con las velas fantasma del domingo dentro) y `368ad3617bd9` con
462 / 2.038 / 7.231 (ya con A1, todavía con el corte en 00:00).

El recuento exige el histórico M1 real, así que se salta si no está descargado.
El hash no depende de ningún dato y se comprueba siempre.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chronos.application.structure.config import ImpulseConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.application.structure.evidence import BASELINE_HASH, PHASE1_BASELINE
from chronos.domain.structure.enums import AnchorMode, LegStartMode
from chronos.infrastructure.config.loader import load_impulse_config

CONFIG = Path("config/impulse.yaml")

#: Impulsos **totales** (publicados o no) de la corrida definitiva.
BASELINE_IMPULSES = PHASE1_BASELINE

#: Los que salen del calentamiento y llegan a los informes y a las capturas.
BASELINE_PUBLISHED = {"D": 392, "H4": 1910, "H1": 7224}

#: La rejilla que produce esas velas. Sale del propietario, no del motor.
BASELINE_SESSION_START = "NY_18:00"


@pytest.fixture(scope="module")
def config() -> ImpulseConfig:
    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    return load_impulse_config(CONFIG)


def test_el_yaml_lleva_la_configuracion_definitiva(config: ImpulseConfig) -> None:
    """Los cuatro parámetros que el propietario cerró, tal como los cerró."""
    assert config.rules.anchor_mode is AnchorMode.A1_LAST_COUNTER_BODY
    assert config.rules.leg_start_mode is LegStartMode.L1_CURRENT
    assert config.aggregation.d_session_start == BASELINE_SESSION_START
    assert config.structure_side == "bid"
    # H4 arranca con la misma sesión: el offset fijo en UTC deja de contar.
    anchor = config.aggregation.session_anchor
    assert anchor is not None and anchor.timezone == "America/New_York"


def test_el_yaml_produce_el_hash_de_la_linea_base(config: ImpulseConfig) -> None:
    """`L1_actual` se omite del hash justamente para esto (ver `fingerprint`)."""
    assert config.fingerprint() == BASELINE_HASH


def test_los_otros_dos_modos_cambian_el_hash(config: ImpulseConfig) -> None:
    """Cambian el resultado, así que ninguna salida suya puede confundirse con la base."""
    hashes = {
        mode: replace(config, rules=replace(config.rules, leg_start_mode=mode)).fingerprint()
        for mode in LegStartMode
    }
    assert hashes[LegStartMode.L1_CURRENT] == BASELINE_HASH
    assert len(set(hashes.values())) == len(LegStartMode)


def test_el_modo_base_reproduce_el_recuento_de_la_linea_base(config: ImpulseConfig) -> None:
    from chronos.infrastructure.structure.aggregation import aggregate
    from chronos.infrastructure.structure.loader import load_history

    if not Path(config.data.bid_path).exists():
        pytest.skip(f"no está el histórico {config.data.bid_path}")

    history = load_history(config.data, config.structure_side)
    series = {
        timeframe: aggregate(history.frame, timeframe, config.aggregation).frame
        for timeframe in config.charts.detected
    }
    run = DetectDominantImpulses(config).execute(series)

    obtenido = {tf: len(analysis.impulses) for tf, analysis in run.analyses.items()}
    publicados = {tf: len(analysis.published) for tf, analysis in run.analyses.items()}
    assert obtenido == BASELINE_IMPULSES
    assert publicados == BASELINE_PUBLISHED
    assert run.config_hash == BASELINE_HASH
