"""Señales de zona sobre una corrida completa. **Sólo dibujo.**

Lo que se comprueba aquí es sobre todo lo que las señales **no** hacen: no salen
sin zonas, no se adelantan al nacimiento de la zona a la que se refieren, no
sobreviven a la muerte de su ID y no cambian ni un número de las fases 1, 2.0 y
2.1. Que la geometría de cada tipo es la que dice el enunciado está fijado en
`tests/domain/structure/test_zone_signals.py` con casos a mano.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.structure.config import (
    AggregationConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zone_signals import ZoneSignalsRun, detect_zone_signals
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import BreakKind
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import ZoneKind
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

H4 = "H4"


def _run(*, zones: bool, break_by_zone: bool = False) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5, break_by_zone=break_by_zone),
        zones=ZonesConfig(enabled=zones),
    )
    history = make_m1_history(weeks=16)
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
def signals(run: ImpulseRun, zones: ZonesRun) -> ZoneSignalsRun:
    return detect_zone_signals(run, zones)


def test_sin_zonas_no_hay_senales() -> None:
    apagado = _run(zones=False)
    medida = detect_zone_signals(apagado, detect_zones(apagado))

    assert medida.enabled is False
    assert medida.per_timeframe == {}
    assert medida.empty


def test_solo_hay_senales_donde_hay_detector(signals: ZoneSignalsRun) -> None:
    """H1 y M15 no llevan ID propio, así que tampoco zonas ni señales."""
    assert set(signals.per_timeframe) == {"D", H4}
    assert not signals.empty


def test_los_tres_tipos_aparecen_en_el_historico(signals: ZoneSignalsRun) -> None:
    tipos = {item.kind for item in signals.per_timeframe[H4].items}

    assert ZoneSignalKind.TOQUE_OB in tipos
    assert ZoneSignalKind.RECHAZO_UL in tipos


def test_cada_senal_va_con_la_zona_que_le_toca(signals: ZoneSignalsRun) -> None:
    for item in signals.per_timeframe[H4].items:
        esperada = (
            ZoneKind.ORDER_BLOCK
            if item.kind is ZoneSignalKind.TOQUE_OB
            else ZoneKind.LAST
        )
        assert item.zone is esperada


def test_ninguna_senal_se_adelanta_a_su_zona(
    run: ImpulseRun, zones: ZonesRun, signals: ZoneSignalsRun
) -> None:
    """La zona tiene que existir —y haber cerrado su vela— antes de la señal."""
    for timeframe, medida in signals.per_timeframe.items():
        nacimiento = {
            (zoned.id_num, zone.kind): zone.ts_birth
            for zoned in zones.per_timeframe[timeframe].items
            for zone in zoned.zones()
        }
        for item in medida.items:
            assert item.timestamp > nacimiento[(item.id_num, item.zone)]


def test_ninguna_senal_sobrevive_a_su_id(
    zones: ZonesRun, signals: ZoneSignalsRun
) -> None:
    for timeframe, medida in signals.per_timeframe.items():
        fin = {
            zoned.id_num: zoned.ts_end
            for zoned in zones.per_timeframe[timeframe].items
        }
        for item in medida.items:
            final = fin[item.id_num]
            assert final is None or item.timestamp <= final


def test_los_ordinales_empiezan_en_uno_y_no_saltan(signals: ZoneSignalsRun) -> None:
    vistos: dict[tuple[int, ZoneSignalKind], int] = {}
    for item in signals.per_timeframe[H4].items:
        clave = (item.id_num, item.kind)
        vistos[clave] = vistos.get(clave, 0) + 1
        assert item.signal.ordinal == vistos[clave]


def test_con_la_regla_de_la_fase_21_toda_rotura_a_favor_deja_su_senal() -> None:
    """Morir a favor es cerrar más allá del borde exterior del UL: la señal es esa.

    Es la comprobación que ata las señales a lo que el motor ya decidió, sin
    que las señales hayan decidido nada: se calculan aparte y tienen que caer
    exactamente donde el detector cerró el ID.

    **Sobre los ID zonificados, que son los publicables.** Un ID de calentamiento
    muere igual y su rotura sale en los eventos, pero la fase 2.0 no le calcula
    zonas —no tendría con qué compararlas— así que no tiene UL que romper y no
    puede dejar señal. En el histórico real son 2 en D y 1 en H4, y el
    explorador tampoco los dibuja.
    """
    zoned = _run(zones=True, break_by_zone=True)
    zones = detect_zones(zoned)
    medida = detect_zone_signals(zoned, zones)
    zonificados = {item.id_num for item in zones.per_timeframe[H4].items}

    roturas = {
        (event.broken_id_num, pd.Timestamp(event.timestamp))
        for event in zoned.analyses[H4].events
        if event.kind is BreakKind.A_FAVOR and event.broken_id_num in zonificados
    }
    senales = {
        (item.id_num, pd.Timestamp(item.timestamp))
        for item in medida.per_timeframe[H4].items
        if item.kind is ZoneSignalKind.ROTURA_UL
    }

    assert roturas
    assert senales == roturas
    # Y al revés: ninguna señal de rotura que el detector no haya visto.
    assert all(
        not impulse.publishable
        for impulse in zoned.analyses[H4].impulses
        if impulse.exit_break_kind is BreakKind.A_FAVOR
        and (impulse.id_num, pd.Timestamp(impulse.ts_end)) not in senales
    )


def test_medir_las_senales_no_mueve_ni_un_impulso(run: ImpulseRun) -> None:
    """La promesa entera de esta capa, escrita como test."""
    antes = run.analyses[H4].table.copy()
    detect_zone_signals(run, detect_zones(run))

    pd.testing.assert_frame_equal(antes, run.analyses[H4].table)


def test_el_recuento_por_tipo_los_declara_todos(signals: ZoneSignalsRun) -> None:
    recuento = signals.per_timeframe[H4].counts()

    assert set(recuento) == {kind.value for kind in ZoneSignalKind}
    assert sum(recuento.values()) == len(signals.per_timeframe[H4].items)
