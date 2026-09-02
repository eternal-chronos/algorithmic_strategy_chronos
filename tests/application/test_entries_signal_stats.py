"""La estadística de las señales de la cascada. **Sigue sin haber entradas.**

Lo que se comprueba aquí es que la medida no se inventa nada: que no empieza a
mirar antes de que la señal se supiera, que el embudo dice lo que la cascada ya
había marcado, que los cortes parten la población sin perder ni duplicar una
señal, y que medir no mueve ni una marca de la fase 3.0.
"""

from __future__ import annotations

import pytest

from chronos.application.entries.cascade import CascadeRun, CascadeStep, detect_cascade
from chronos.application.entries.signal_stats import (
    FAST_BARS,
    G_AZAR,
    G_SENAL,
    SignalStudy,
    _Study,
    study_signals,
)
from chronos.application.structure.config import (
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
    with_hourly_structure,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

PARES = (
    ("ID de H1 nace después del toque", "ID de H1 ya venía alineado"),
    ("PUL de H1 solapa el de H4", "PUL de H1 fuera del de H4"),
    (f"señal en ≤{FAST_BARS} velas H1", f"señal en >{FAST_BARS} velas H1"),
    ("a favor del ID diario", "sin ID diario a favor"),
    ("alcista", "bajista"),
)


def _run(*, zones: bool) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5, break_by_zone=True),
        zones=ZonesConfig(enabled=zones),
        charts=with_hourly_structure(ChartsConfig()),
    )
    history = make_m1_history(weeks=40)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }
    return DetectDominantImpulses(config).execute(series)


@pytest.fixture(scope="module")
def run() -> ImpulseRun:
    return _run(zones=True)


@pytest.fixture(scope="module")
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


@pytest.fixture(scope="module")
def cascade(run: ImpulseRun, zones: ZonesRun) -> CascadeRun:
    return detect_cascade(run, zones)


@pytest.fixture(scope="module")
def study(run: ImpulseRun, zones: ZonesRun, cascade: CascadeRun) -> SignalStudy:
    return study_signals(run, zones, cascade)


def _group(study: SignalStudy, title: str, name: str):
    block = next(item for item in study.sections if item.title == title)
    return next(group for group in block.groups if group.name == name)


def test_sin_cascada_no_hay_estadistica() -> None:
    apagado = _run(zones=False)
    medida = study_signals(apagado, detect_zones(apagado), detect_cascade(apagado, None))

    assert medida.enabled is False
    assert medida.sections == ()


def test_el_embudo_dice_lo_que_la_cascada_ya_habia_marcado(
    study: SignalStudy, cascade: CascadeRun
) -> None:
    counts = cascade.counts()
    embudo = study.funnel

    assert embudo is not None
    assert embudo.searched == counts[CascadeStep.BUSCAR_H1.value]
    assert embudo.vetoed == counts[CascadeStep.H4_DESCARTADO.value]
    assert embudo.confirmed == counts[CascadeStep.CONFIRMA_PUL_H1.value]
    assert embudo.signalled == counts[CascadeStep.TOQUE_PUL_H1.value]
    assert embudo.touches == embudo.searched + embudo.vetoed


def test_la_corrida_sintetica_produce_senales_que_medir(study: SignalStudy) -> None:
    assert _group(study, "Dónde confirmar en H1", G_SENAL).n > 0


def test_cada_corte_parte_la_senal_sin_perder_ni_duplicar(study: SignalStudy) -> None:
    total = _group(study, "Dónde confirmar en H1", G_SENAL).n
    cortes = {
        group.name: group.n
        for group in next(
            block for block in study.sections
            if block.title == "Cortes de la señal del propietario"
        ).groups
    }

    for izquierda, derecha in PARES:
        assert cortes.get(izquierda, 0) + cortes.get(derecha, 0) == total, (
            f"el corte {izquierda} / {derecha} no cuadra con las {total} señales"
        )


def test_los_objetivos_van_en_orden(study: SignalStudy) -> None:
    """Llegar a 2R exige haber pasado por 1R: la fracción no puede subir."""
    for block in study.sections:
        for group in block.groups:
            assert list(group.hit) == sorted(group.hit, reverse=True), group.name
            assert group.hit[0] + group.dead <= 1.0 + 1e-9, group.name


def test_la_observacion_no_empieza_antes_de_que_la_senal_se_sepa(
    run: ImpulseRun, zones: ZonesRun, cascade: CascadeRun
) -> None:
    """La única forma de mirar al futuro aquí sería medir la vela de la señal."""
    estudio = _Study(run, zones, cascade, cap_bars=96, targets=(1.0,), seed=1)

    for name, points in estudio._populations().items():
        for point in points:
            #: La línea de azar se fecha en la apertura de la vela que mira, así
            #: que ahí el instante de la señal y el del arranque coinciden.
            if name == G_AZAR:
                assert point.observe_from == point.timestamp
            else:
                assert point.observe_from > point.timestamp, name
            assert point.risk > 0, name


def test_por_ano_no_se_pierde_ninguna_senal(study: SignalStudy) -> None:
    total = _group(study, "Dónde confirmar en H1", G_SENAL).n
    anios = next(
        block for block in study.sections if block.title == "La señal año a año"
    ).groups

    assert sum(group.n for group in anios) == total


def test_medir_no_mueve_ni_una_marca(
    study: SignalStudy, cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    assert detect_cascade(run, zones).counts() == cascade.counts()
    assert study.enabled is True
