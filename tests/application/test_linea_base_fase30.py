"""§0 — la línea base de la fase 2.1, preservada por la fase 3.

Lo que se fija aquí es que **la cascada lee la estructura y no la toca**. Con
`entries.enabled: false` la corrida tiene que reproducir `801951b9cc26` con
D 239 / H4 1.214 / H1 4.148 detectados y 233 / 1.211 / 4.141 publicados, y con
las señales encendidas esos mismos números tienen que salir idénticos: encender
la fase 3 no puede mover un solo impulso.

También se fija que la traza de extensiones del extremo —aditiva, la que la fase
3 necesita para reconstruir el UL sin mirar al futuro— no ha cambiado nada del
detector. Si lo hubiera cambiado, estos recuentos se moverían.

El recuento exige el histórico M1 real, así que se salta si no está descargado.
Los hashes no dependen de ningún dato y se comprueban siempre.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chronos.application.entries.cascade import build_cascade
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.evidence import (
    PHASE21_DETECTED,
    PHASE21_HASH,
    PHASE21_PUBLISHED,
)
from chronos.application.structure.config import ImpulseConfig, ZonesConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.evidence import BASELINE_HASH
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.infrastructure.config.loader import load_impulse_config

CONFIG = Path("config/impulse.yaml")


@pytest.fixture(scope="module")
def config() -> ImpulseConfig:
    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    return load_impulse_config(CONFIG)


def _phase21(config: ImpulseConfig) -> ImpulseConfig:
    return replace(
        config,
        rules=replace(config.rules, break_by_zone=True),
        zones=ZonesConfig(enabled=True),
    )


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


@pytest.fixture(scope="module")
def structure(config: ImpulseConfig) -> tuple[ImpulseRun, ZonesRun]:
    tuned = _phase21(config)
    run = DetectDominantImpulses(tuned).execute(_series(tuned))  # type: ignore[arg-type]
    return run, detect_zones(run)


# --- El interruptor ----------------------------------------------------------


def test_el_yaml_trae_las_senales_apagadas(config: ImpulseConfig) -> None:
    """Es el primer módulo que emite señales: va apagado por defecto."""
    assert config.entries.enabled is False


def test_el_yaml_no_autoriza_correr_sin_fichero_de_ask(config: ImpulseConfig) -> None:
    """§4 — sin ask el comando se detiene y avisa. La autorización es explícita."""
    assert config.entries.allow_missing_ask is False


def test_la_fase_3_no_entra_en_el_hash_de_la_estructura(config: ImpulseConfig) -> None:
    """La cascada no puede mover un impulso, así que no identifica una corrida.

    Mismo criterio que las zonas de la fase 2.0: el `config_hash` sigue
    significando exactamente lo que significaba, los parámetros que mueven un
    impulso, y las corridas archivadas siguen siendo comparables.
    """
    assert config.fingerprint() == BASELINE_HASH
    encendida = replace(config, entries=replace(config.entries, enabled=True))
    assert encendida.fingerprint() == BASELINE_HASH


# --- La línea base de la fase 2.1 --------------------------------------------


def test_la_estructura_sigue_siendo_la_de_la_fase_21(
    structure: tuple[ImpulseRun, ZonesRun],
) -> None:
    run, _zones = structure
    detected = {tf: len(analysis.impulses) for tf, analysis in run.analyses.items()}
    published = {tf: len(analysis.published) for tf, analysis in run.analyses.items()}

    assert run.config_hash == PHASE21_HASH
    assert detected == PHASE21_DETECTED
    assert published == PHASE21_PUBLISHED


def test_con_las_senales_apagadas_no_se_emite_nada(
    structure: tuple[ImpulseRun, ZonesRun],
) -> None:
    run, zones = structure
    off = build_cascade(run, zones, None, EntriesConfig(enabled=False))

    assert off.emits_nothing
    assert off.observations == ()
    assert off.signals == ()
    assert off.discarded == ()
    assert off.funnel == {}


def test_encender_las_senales_no_mueve_un_solo_impulso(
    config: ImpulseConfig, structure: tuple[ImpulseRun, ZonesRun]
) -> None:
    """La cascada recorre lo que la fase 2.1 dejó; no vuelve a ejecutar nada."""
    run, zones = structure
    before = [
        (impulse.id_num, impulse.index_constitution, impulse.index_end, impulse.extreme)
        for analysis in run.analyses.values()
        for impulse in analysis.impulses
    ]
    build_cascade(
        run, zones, None, EntriesConfig(enabled=True, allow_missing_ask=True)
    )
    after = [
        (impulse.id_num, impulse.index_constitution, impulse.index_end, impulse.extreme)
        for analysis in run.analyses.values()
        for impulse in analysis.impulses
    ]

    assert before == after


# --- La traza de extensiones es aditiva --------------------------------------


def test_la_traza_de_extensiones_cuadra_con_el_contador(
    structure: tuple[ImpulseRun, ZonesRun],
) -> None:
    """La lista nueva y el contador que ya existía tienen que decir lo mismo.

    Si divergieran, la reconstrucción del UL de la fase 3 estaría mirando otra
    historia que la que el detector vivió.
    """
    run, _zones = structure
    for analysis in run.analyses.values():
        for impulse in analysis.impulses:
            assert len(impulse.extension_trail) == impulse.extreme_extensions
            if impulse.extension_trail:
                assert impulse.extension_trail[-1].price == impulse.extreme
                assert impulse.extension_trail[-1].index == impulse.index_extreme


def test_el_extremo_inicial_queda_registrado(
    structure: tuple[ImpulseRun, ZonesRun],
) -> None:
    run, _zones = structure
    for analysis in run.analyses.values():
        for impulse in analysis.impulses:
            assert impulse.index_extreme_at_constitution >= 0
            if not impulse.extension_trail:
                assert impulse.index_extreme_at_constitution == impulse.index_extreme
                assert impulse.extreme_at_constitution == impulse.extreme
