"""La línea base de la fase 3.0, preservada por la fase 3.1.

La 3.1 saca del camino las tres vías de la 3.0 y pone dos. Lo que se fija aquí es
que **el modo de la 3.0 sigue produciendo la 3.0**: con
`CONFIRM_MODE = v30_tres_vias` tienen que salir sus 1.776 confirmaciones y sus
3.702 operaciones exactas. Si no salieran, lo que el informe compararía no sería
"la 3.0 contra la 3.1" sino un bug contra otra cosa.

También se fija lo que la comparación entre fases da por hecho y no se puede
suponer: que las dos corridas producen **las mismas observaciones**. La 3.1 sólo
toca la confirmación en H1, y las observaciones se recogen antes de mirar H1.

El recuento exige el histórico M1 real, así que se salta si no está descargado.
Lo que no depende de ningún dato —el YAML y los valores por defecto— se comprueba
siempre.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from chronos.application.entries.cascade import build_cascade
from chronos.application.entries.comparison import (
    check_observations_match,
    lost_confirmations,
    observation_key,
    priority_effect,
)
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.evidence import (
    PHASE30_CONFIRMATIONS,
    PHASE30_TRADES,
)
from chronos.application.entries.execution import M1Executor
from chronos.application.structure.config import M15, ImpulseConfig, ZonesConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.entries.enums import (
    ConfirmationKind,
    ConfirmMode,
    ConfirmPriority,
    EntryMode,
)
from chronos.domain.instrument import InstrumentSpec
from chronos.infrastructure.config.loader import load_impulse_config

CONFIG = Path("config/impulse.yaml")


@pytest.fixture(scope="module")
def config() -> ImpulseConfig:
    if not CONFIG.exists():
        pytest.skip("no hay config/impulse.yaml")
    return load_impulse_config(CONFIG)


# --- Lo que no depende del histórico -----------------------------------------


def test_el_yaml_trae_las_dos_vias_de_la_31(config: ImpulseConfig) -> None:
    """`v30_tres_vias` se conserva para regresión; no es la del proyecto."""
    assert config.entries.confirm_mode is ConfirmMode.V31_DOS_VIAS


def test_el_orden_entre_las_vias_esta_declarado(config: ImpulseConfig) -> None:
    """Hace falta un orden determinista, y se declara como parámetro abierto."""
    assert config.entries.confirm_priority in tuple(ConfirmPriority)
    abiertos = "\n".join(config.entries.open_decisions())
    assert "CONFIRM_PRIORITY" in abiertos


def test_las_dos_vias_estan_cerradas_por_el_enunciado(config: ImpulseConfig) -> None:
    """Y lo que se elimina, también: el ID solo y las tres definiciones."""
    cerrados = "\n".join(config.entries.closed_decisions())

    assert "DOS VÍAS Y SÓLO DOS" in cerrados
    assert "Un ID de H1 solo NO confirma" in cerrados
    assert "columnas informativas" in cerrados


def test_el_modo_y_el_orden_entran_en_el_hash_de_la_cascada(
    config: ImpulseConfig,
) -> None:
    """Dos corridas con vías distintas no pueden compartir identificador."""
    entries = config.entries
    otro_modo = replace(entries, confirm_mode=ConfirmMode.V30_TRES_VIAS)
    otro_orden = replace(entries, confirm_priority=ConfirmPriority.OB_PRIMERO)

    assert entries.fingerprint() != otro_modo.fingerprint()
    assert entries.fingerprint() != otro_orden.fingerprint()


# --- Lo que exige el histórico real ------------------------------------------


def _series(config: ImpulseConfig) -> dict[str, pd.DataFrame]:
    from chronos.infrastructure.structure.aggregation import aggregate
    from chronos.infrastructure.structure.loader import load_history

    if not Path(config.data.bid_path).exists():
        pytest.skip(f"no está el histórico {config.data.bid_path}")
    history = load_history(config.data, config.structure_side)
    charts = {*config.charts.detected, *config.charts.charts}
    return {
        timeframe: aggregate(history.frame, timeframe, config.aggregation).frame
        for timeframe in charts
    }


@pytest.fixture(scope="module")
def structure(config: ImpulseConfig) -> tuple[ImpulseRun, ZonesRun, dict[str, pd.DataFrame]]:
    tuned = replace(
        config,
        rules=replace(config.rules, break_by_zone=True),
        zones=ZonesConfig(enabled=True),
    )
    series = _series(tuned)
    run = DetectDominantImpulses(tuned).execute(series)
    return run, detect_zones(run), series


@pytest.fixture(scope="module")
def cascadas(
    structure: tuple[ImpulseRun, ZonesRun, dict[str, pd.DataFrame]],
) -> tuple[object, object]:
    run, zones, series = structure
    # ⚠️ `ENTRY_MODE = v31_contacto`: las dos líneas base de este fichero son las
    # de las fases 3.0 y 3.1, y las dos se operaban POR CONTACTO. Con el modo de
    # la 3.2 no se pueden reproducir y no deberían: el contacto dejó de operar.
    entries = EntriesConfig(
        enabled=True, allow_missing_ask=True, entry_mode=EntryMode.V31_CONTACTO
    )
    m15 = series.get(M15)
    return (
        build_cascade(
            run, zones, m15, replace(entries, confirm_mode=ConfirmMode.V30_TRES_VIAS)
        ),
        build_cascade(run, zones, m15, entries),
    )


def test_el_modo_v30_reproduce_las_confirmaciones_de_la_fase_30(
    cascadas: tuple[object, object],
) -> None:
    """1.776 confirmaciones. Si no salen, la refactorización de la 3.1 tiene un bug."""
    v30, _v31 = cascadas

    assert v30.funnel["confirman_en_h1"] == PHASE30_CONFIRMATIONS  # type: ignore[attr-defined]


def test_el_modo_v30_reproduce_las_operaciones_de_la_fase_30(
    config: ImpulseConfig,
    structure: tuple[ImpulseRun, ZonesRun, dict[str, pd.DataFrame]],
    cascadas: tuple[object, object],
) -> None:
    """3.702 operaciones, ejecutadas sobre el M1 real."""
    from chronos.infrastructure.structure.loader import load_history

    v30, _v31 = cascadas
    history = load_history(config.data, config.structure_side)
    execution = M1Executor(
        history.frame,
        InstrumentSpec(symbol=config.symbol),
        EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=history.has_ask,
    ).execute(v30)  # type: ignore[arg-type]

    assert len(execution.trades) == PHASE30_TRADES


def test_las_dos_corridas_producen_las_mismas_observaciones(
    cascadas: tuple[object, object],
) -> None:
    """Es lo que toda la comparación del informe da por hecho. Se comprueba."""
    v30, v31 = cascadas
    check_observations_match(v30, v31)  # type: ignore[arg-type]

    izquierda = [observation_key(item) for item in v30.observations]  # type: ignore[attr-defined]
    derecha = [observation_key(item) for item in v31.observations]  # type: ignore[attr-defined]
    assert izquierda == derecha


def test_la_31_no_confirma_por_ninguna_via_eliminada(
    cascadas: tuple[object, object],
) -> None:
    """Ni `id_h1` ni `rechazo` pueden aparecer: dejaron de ser vías."""
    _v30, v31 = cascadas
    vias = {signal.confirmation.kind for signal in v31.signals}  # type: ignore[attr-defined]

    assert vias <= {ConfirmationKind.TURTLE_SOUP, ConfirmationKind.OB_H1}


def test_r1_r2_y_r3_se_siguen_persistiendo(cascadas: tuple[object, object]) -> None:
    """Ya no deciden nada y siguen en el CSV: es lo que pide la fase."""
    _v30, v31 = cascadas

    assert v31.signals  # type: ignore[attr-defined]
    for signal in v31.signals:  # type: ignore[attr-defined]
        assert signal.rejection_marks


def test_la_31_pierde_confirmaciones_y_se_pueden_enumerar(
    cascadas: tuple[object, object],
) -> None:
    """El informe y las capturas se montan sobre esta lista: tiene que existir."""
    v30, v31 = cascadas
    lost = lost_confirmations(v30, v31)  # type: ignore[arg-type]

    assert lost
    assert all(item.via_before for item in lost)


def test_el_orden_entre_las_vias_se_mide_en_vez_de_razonarse(
    structure: tuple[ImpulseRun, ZonesRun, dict[str, pd.DataFrame]],
    cascadas: tuple[object, object],
) -> None:
    """Se corre la cascada con el orden invertido y se comparan las decisiones."""
    run, zones, series = structure
    _v30, v31 = cascadas
    effect = priority_effect(run, zones, series.get(M15), v31)  # type: ignore[arg-type]

    assert effect.total_confirmations > 0
    assert effect.both_available <= effect.total_confirmations
    assert effect.changed_via <= effect.both_available
