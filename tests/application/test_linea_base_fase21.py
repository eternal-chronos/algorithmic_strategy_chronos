"""Línea base de la fase 2.1: la rotura del ID por zona, fijada en un test.

Dos cosas se fijan aquí y las dos son obligatorias.

La primera es la **regresión** que exige el §3 del enunciado: con
`BREAK_BY_ZONE = false` el sistema tiene que producir exactamente
`config_hash = f2f2a87f8efe`, D 401 / H4 1.914 detectados y 392 / 1.910
publicados. Si no sale idéntico hay un bug en la refactorización y no un
resultado.

La segunda es la **línea base nueva**, la que produce `BREAK_BY_ZONE = true`.
Se archiva igual que se archivó la de la fase 1 para que cualquier cambio futuro
que la mueva salte de inmediato.

El recuento exige el histórico M1 real, así que se salta si no está descargado.
Los hashes no dependen de ningún dato y se comprueban siempre.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chronos.application.structure.break_evidence import PHASE1_BASELINE_PUBLISHED
from chronos.application.structure.config import ImpulseConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.evidence import BASELINE_HASH, PHASE1_BASELINE
from chronos.domain.structure.enums import OverlapPriority
from chronos.infrastructure.config.loader import load_impulse_config

CONFIG = Path("config/impulse.yaml")

#: Hash de la configuración con la rotura por zona y el orden `a_favor_primero`.
PHASE21_HASH = "00e7013d627b"

#: El mismo con `en_contra_primero`. Produce la misma historia sobre este
#: histórico —el conflicto de solape no se da ni una vez— pero el hash cambia
#: igualmente: identifica los parámetros, no el resultado.
PHASE21_HASH_EN_CONTRA = "d54dd285c451"

#: Impulsos **totales** (publicados o no) de la corrida con la regla nueva. Se
#: movieron al congelar el UL: con la zona quieta el borde exterior a favor deja
#: de alejarse en cada rechazo, así que los ID mueren antes por ese lado.
PHASE21_BASELINE = {"D": 251, "H4": 1184}

#: Los que salen del calentamiento y llegan a los informes y a las capturas.
PHASE21_PUBLISHED = {"D": 245, "H4": 1181}


@pytest.fixture(scope="module")
def config() -> ImpulseConfig:
    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    return load_impulse_config(CONFIG)


def _series(config: ImpulseConfig) -> dict[str, object]:
    from chronos.infrastructure.structure.aggregation import aggregate
    from chronos.infrastructure.structure.loader import load_history

    if not Path(config.data.bid_path).exists():
        pytest.skip(f"no está el histórico {config.data.bid_path}")
    history = load_history(config.data, config.structure_side)
    return {
        timeframe: aggregate(history.frame, timeframe, config.aggregation).frame
        for timeframe in config.charts.detected
    }


def _run(config: ImpulseConfig, *, break_by_zone: bool, **rules: object) -> ImpulseRun:
    tuned = replace(
        config,
        rules=replace(config.rules, break_by_zone=break_by_zone, **rules),  # type: ignore[arg-type]
    )
    return DetectDominantImpulses(tuned).execute(_series(tuned))  # type: ignore[arg-type]


# --- La regresión que exige el §3 -------------------------------------------


def test_el_yaml_trae_la_regla_nueva_apagada(config: ImpulseConfig) -> None:
    """Es el primer cambio de comportamiento del proyecto: va apagado por defecto."""
    assert config.rules.break_by_zone is False
    assert config.rules.overlap_priority is OverlapPriority.A_FAVOR_FIRST


def test_apagada_reproduce_el_hash_de_la_linea_base(config: ImpulseConfig) -> None:
    assert config.fingerprint() == BASELINE_HASH


def test_apagada_reproduce_el_recuento_de_la_linea_base(config: ImpulseConfig) -> None:
    run = _run(config, break_by_zone=False)
    detected = {tf: len(analysis.impulses) for tf, analysis in run.analyses.items()}
    published = {tf: len(analysis.published) for tf, analysis in run.analyses.items()}

    assert detected == PHASE1_BASELINE
    assert published == PHASE1_BASELINE_PUBLISHED
    assert run.config_hash == BASELINE_HASH


# --- La línea base nueva -----------------------------------------------------


def test_encendida_produce_un_hash_distinto(config: ImpulseConfig) -> None:
    """Ninguna salida de la regla nueva puede confundirse con la de la vieja."""
    turned_on = replace(config, rules=replace(config.rules, break_by_zone=True))
    assert turned_on.fingerprint() == PHASE21_HASH
    assert turned_on.fingerprint() != BASELINE_HASH

    other = replace(
        turned_on,
        rules=replace(
            turned_on.rules, overlap_priority=OverlapPriority.EN_CONTRA_FIRST
        ),
    )
    assert other.fingerprint() == PHASE21_HASH_EN_CONTRA


def test_encendida_produce_la_linea_base_nueva(config: ImpulseConfig) -> None:
    run = _run(config, break_by_zone=True)
    detected = {tf: len(analysis.impulses) for tf, analysis in run.analyses.items()}
    published = {tf: len(analysis.published) for tf, analysis in run.analyses.items()}

    assert detected == PHASE21_BASELINE
    assert published == PHASE21_PUBLISHED
    assert run.config_hash == PHASE21_HASH


def test_el_orden_de_solape_no_cambia_nada_sobre_este_historico(
    config: ImpulseConfig,
) -> None:
    """§2 — la medición que decide si `OVERLAP_PRIORITY` es cosmética.

    El conflicto de evaluación simultánea exige los dos bordes exteriores
    invertidos, y eso sólo pasa con impulsos de rango no positivo. Con la regla
    nueva no queda ninguno, así que no queda ningún conflicto: las dos corridas
    son la misma historia. Si algún día dejaran de serlo, este test lo dice.
    """
    one = _run(config, break_by_zone=True)
    other = _run(
        config, break_by_zone=True, overlap_priority=OverlapPriority.EN_CONTRA_FIRST
    )

    for timeframe, analysis in one.analyses.items():
        assert analysis.diagnostics["conflictos_de_solape"] == 0
        mirror = other.analyses[timeframe]
        assert len(analysis.impulses) == len(mirror.impulses)
        for left, right in zip(analysis.impulses, mirror.impulses, strict=True):
            assert left.direction is right.direction
            assert left.index_constitution == right.index_constitution
            assert left.index_end == right.index_end
            assert left.anchor == right.anchor
            assert left.extreme == right.extreme
