"""La cascada H4 → H1 sobre una corrida completa. **Sólo señales.**

Lo que se comprueba aquí es sobre todo lo que la cascada **no** hace: no sale sin
zonas, no pide permiso al Diario para empezar, no mira nada que vaya en contra de
un OB diario vigente, no se sale de sus ventanas, no cuelga un paso de otro que no
existe y no cambia ni un número de las fases 1, 2.0 y 2.1. Que la geometría de
cada vía es la que dice el enunciado está fijado en `tests/domain/entries/` con
velas escritas a mano.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries.cascade import CascadeRun, CascadeStep, detect_cascade
from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    AggregationConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zone_signals import detect_zone_signals
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

CONFIRMATIONS = (CascadeStep.CONFIRMA_TURTLE, CascadeStep.CONFIRMA_OB_H1)


def _run(*, zones: bool) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5, break_by_zone=True),
        zones=ZonesConfig(enabled=zones),
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


def _of(cascade: CascadeRun, step: CascadeStep):
    return tuple(mark for mark in cascade.marks if mark.step is step)


def test_sin_zonas_no_hay_cascada() -> None:
    apagado = _run(zones=False)
    medida = detect_cascade(apagado, detect_zones(apagado))

    assert medida.enabled is False
    assert medida.empty


def test_la_corrida_sintetica_recorre_los_dos_escalones(cascade: CascadeRun) -> None:
    counts = cascade.counts()

    assert counts[CascadeStep.BUSCAR_H1.value] > 0
    assert sum(counts[step.value] for step in CONFIRMATIONS) > 0


def test_cada_paso_se_dibuja_en_su_grafico(cascade: CascadeRun) -> None:
    esperado = {
        CascadeStep.ZONA_DIARIA: DAILY,
        CascadeStep.H4_DESCARTADO: H4,
        CascadeStep.BUSCAR_H1: H4,
        CascadeStep.CONFIRMA_TURTLE: H1,
        CascadeStep.CONFIRMA_OB_H1: H1,
        CascadeStep.OB_H1_DESECHADO: H1,
    }
    for mark in cascade.marks:
        assert mark.chart == esperado[mark.step]


def _toques(run: ImpulseRun, zones: ZonesRun, timeframe: str) -> set:
    return {
        item.timestamp
        for item in detect_zone_signals(run, zones).per_timeframe[timeframe].items
        if item.kind is ZoneSignalKind.TOQUE_OB
    }


def test_la_cascada_empieza_en_un_toque_del_ob_de_h4(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """No se redefine el toque: es el `TOQUE_OB` que la fase 2.0 ya calculaba,
    y no cuelga de nada porque el Diario no es un escalón."""
    toques = _toques(run, zones, H4)

    for mark in _of(cascade, CascadeStep.BUSCAR_H1):
        assert mark.timestamp in toques
        assert mark.timeframe == H4
        assert mark.parent is None


def test_el_tramo_diario_no_abre_ninguna_busqueda(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """El Diario sólo prohíbe: nada cuelga de él salvo los descartes."""
    toques = _toques(run, zones, DAILY)
    tramos = {mark.seq for mark in _of(cascade, CascadeStep.ZONA_DIARIA)}

    for mark in _of(cascade, CascadeStep.ZONA_DIARIA):
        assert mark.timestamp in toques
        assert mark.timeframe == DAILY
        assert mark.parent is None
    for mark in cascade.marks:
        if mark.parent in tramos:
            assert mark.step is CascadeStep.H4_DESCARTADO


def test_sin_ob_diario_vigente_se_busca_en_las_dos_direcciones(
    cascade: CascadeRun,
) -> None:
    """El Diario no autoriza nada: fuera de sus zonas vale cualquier toque de H4."""
    tramos = _of(cascade, CascadeStep.ZONA_DIARIA)
    busquedas = _of(cascade, CascadeStep.BUSCAR_H1)

    assert {mark.direction for mark in busquedas} == {
        ImpulseDirection.ALCISTA,
        ImpulseDirection.BAJISTA,
    }
    # Y ninguna de ellas cae dentro de un tramo diario que la prohibiese.
    for mark in busquedas:
        for tramo in tramos:
            if tramo.direction is mark.direction:
                continue
            assert not (
                mark.timestamp >= tramo.timestamp
                and (tramo.window_end is None or mark.timestamp < tramo.window_end)
            )


def test_dentro_de_un_ob_diario_no_se_mira_nada_en_contra(cascade: CascadeRun) -> None:
    padres = {mark.seq: mark for mark in cascade.marks}

    descartes = _of(cascade, CascadeStep.H4_DESCARTADO)
    assert descartes
    for mark in descartes:
        tramo = padres[mark.parent]
        assert tramo.step is CascadeStep.ZONA_DIARIA
        assert mark.direction is not tramo.direction
        assert mark.timestamp >= tramo.timestamp
        assert tramo.window_end is None or mark.timestamp < tramo.window_end


def test_ningun_paso_cuelga_de_uno_que_no_existe(cascade: CascadeRun) -> None:
    conocidos = {mark.seq for mark in cascade.marks}

    for mark in cascade.marks:
        assert mark.parent is None or mark.parent in conocidos


def test_ningun_paso_se_sabe_antes_que_su_padre(cascade: CascadeRun, run: ImpulseRun) -> None:
    """Un paso se sabe cuando CIERRA la vela que lo mide, no cuando se abre.

    Los pasos de H1 se fechan en la apertura de su vela, y esa vela puede ser la
    misma que contiene el toque de H4 —el toque cae en un minuto cualquiera de
    ella—: lo que no puede es haber cerrado antes del toque, porque entonces el
    patrón se habría sabido antes de que hubiera nada que buscar.
    """
    padres = {mark.seq: mark for mark in cascade.marks}
    hourly = run.chart_bars[H1].index

    for mark in cascade.marks:
        if mark.parent is None:
            continue
        padre = padres[mark.parent]
        if mark.chart != H1:
            assert mark.timestamp >= padre.timestamp
            continue
        position = int(hourly.searchsorted(pd.Timestamp(mark.timestamp), side="right"))
        assert position < len(hourly), "la vela de la marca es la última: no se sabe su cierre"
        assert hourly[position] > padre.timestamp


def test_cada_paso_cae_dentro_de_la_ventana_que_lo_abrio(cascade: CascadeRun) -> None:
    """La ventana cubre `[toque, fin)`: al cerrarse ya no admite nada."""
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in cascade.marks:
        if mark.parent is None:
            continue
        padre = padres[mark.parent]
        if padre.window_end is None:
            continue
        # La confirmación se fecha en la APERTURA de su vela de H1, que cierra
        # después: lo que tiene que caer dentro de la ventana es esa apertura.
        assert mark.timestamp < padre.window_end


def test_la_ventana_se_cierra_cuando_el_precio_abandona_la_zona(
    cascade: CascadeRun, run: ImpulseRun
) -> None:
    cierres = {DAILY: run.analyses[DAILY].bars, H4: run.analyses[H4].bars}

    for mark in cascade.marks:
        if mark.window_end is None or mark.low is None or mark.high is None:
            continue
        bars = cierres[mark.timeframe]
        dentro = bars.loc[(bars.index >= mark.timestamp) & (bars.index < mark.window_end)]
        # Todas las velas de la ventana cerraron DENTRO de la zona salvo, como
        # mucho, la última: la que la cierra es justo la que se fue.
        assert all(mark.low <= close <= mark.high for close in dentro["close"].to_numpy()[:-1])


def test_una_confirmacion_por_busqueda_y_ni_una_mas(cascade: CascadeRun) -> None:
    """El primero que se consiga: turtle soup u OB de H1, nunca los dos."""
    por_padre: dict[int, int] = {}
    for mark in cascade.marks:
        if mark.step in CONFIRMATIONS:
            por_padre[mark.parent] = por_padre.get(mark.parent, 0) + 1

    assert por_padre
    assert max(por_padre.values()) == 1


def test_las_confirmaciones_cuelgan_de_una_busqueda_en_h1(cascade: CascadeRun) -> None:
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in cascade.marks:
        if mark.step in CONFIRMATIONS or mark.step is CascadeStep.OB_H1_DESECHADO:
            assert padres[mark.parent].step is CascadeStep.BUSCAR_H1
            assert mark.timeframe == H4


def test_el_ob_desechado_gasto_sus_dos_velas(cascade: CascadeRun) -> None:
    for mark in _of(cascade, CascadeStep.OB_H1_DESECHADO):
        assert mark.attempts == 2


def test_el_ob_de_h1_sabe_sobre_que_vela_se_mide(cascade: CascadeRun) -> None:
    """La caja del explorador se dibuja sobre la vela de referencia, que queda
    detrás de la marca y por eso viaja aparte."""
    cajas = _of(cascade, CascadeStep.CONFIRMA_OB_H1) + _of(
        cascade, CascadeStep.OB_H1_DESECHADO
    )

    assert cajas
    for mark in cajas:
        assert mark.zone_start is not None
        assert mark.zone_start < mark.timestamp
        assert mark.low is not None and mark.high is not None
        assert mark.low <= mark.level <= mark.high


def test_las_marcas_van_en_orden(cascade: CascadeRun) -> None:
    stamps = [mark.timestamp for mark in cascade.marks]

    assert stamps == sorted(stamps)


def test_la_cascada_no_mueve_ni_un_impulso(run: ImpulseRun, zones: ZonesRun) -> None:
    """Se calcula sobre el resultado ya cerrado: no hay nada que pueda mover."""
    antes = run.table().copy()
    detect_cascade(run, zones)

    pd.testing.assert_frame_equal(run.table(), antes)
