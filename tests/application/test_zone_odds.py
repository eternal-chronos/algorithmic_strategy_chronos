"""La probabilidad de zona de una corrida: toques con su desenlace, sabidos a tiempo.

Lo que se comprueba es que cada zona lleva sus números, que la cuenta de la
estructura sólo mira ID muertos antes de nacer la zona (y por tanto nunca al
propio ID), que el total del histórico cuadra con los toques uno a uno, y que
el precio a secas está orientado a favor del ID de la zona.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M5,
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zone_odds import (
    TimeframeOdds,
    ZoneOdds,
    ordinal_bucket,
    zone_odds,
)
from chronos.application.structure.zone_signals import ZoneSignalsRun, detect_zone_signals
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import BreakKind, ImpulseDirection
from chronos.domain.structure.zone_odds import band_visits
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.domain.structure.zones import ZoneKind
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

CHARTS = ChartsConfig({DAILY: (DAILY,), H4: (H4,), H1: (H1, H4), M15: (H1, H4), M5: (H1, H4)})


@pytest.fixture(scope="module")
def run() -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
        zones=ZonesConfig(enabled=True),
        charts=CHARTS,
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
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


@pytest.fixture(scope="module")
def signals(run: ImpulseRun, zones: ZonesRun) -> ZoneSignalsRun:
    return detect_zone_signals(run, zones)


@pytest.fixture(scope="module")
def odds(run: ImpulseRun, zones: ZonesRun, signals: ZoneSignalsRun) -> TimeframeOdds:
    return zone_odds(run.analyses[H1], zones.per_timeframe[H1], signals.per_timeframe[H1])


def test_cada_zona_lleva_sus_numeros(zones: ZonesRun, odds: TimeframeOdds) -> None:
    esperadas = [
        (zoned.id_num, zone.kind)
        for zoned in zones.per_timeframe[H1].items
        for zone in zoned.zones()
    ]
    assert [(item.id_num, item.kind) for item in odds.items] == esperadas
    for item in odds.items:
        assert item.low <= item.high
        assert item.known_first.n <= item.known_all.n
        assert item.known_first.k <= item.known_all.k


def test_lo_sabido_al_nacer_solo_cuenta_id_muertos_antes(
    zones: ZonesRun, signals: ZoneSignalsRun, odds: TimeframeOdds
) -> None:
    """Se recuenta a mano, toque a toque, y tiene que dar lo mismo."""
    by_id = {zoned.id_num: zoned for zoned in zones.per_timeframe[H1].items}
    toques = [
        (item.zone, by_id[item.id_num], item.signal.ordinal)
        for item in signals.per_timeframe[H1].items
        if item.kind in (ZoneSignalKind.TOQUE_PUL, ZoneSignalKind.RECHAZO_UL)
    ]
    assert any(item.known_all.n for item in odds.items), "la fixture tiene que dar historia"
    for item in odds.items:
        alike = [
            (zoned, ordinal)
            for kind, zoned, ordinal in toques
            if kind is item.kind
            and zoned.direction is item.direction
            and zoned.exit_break is not None
            and zoned.ts_end is not None
            and zoned.ts_end <= item.ts_birth
        ]
        assert item.known_all.n == len(alike)
        assert item.known_all.k == sum(
            1 for zoned, _ in alike if zoned.exit_break is BreakKind.A_FAVOR
        )
        assert item.known_first.n == sum(1 for _, ordinal in alike if ordinal == 1)
        # El propio ID muere después de que nazca su zona: nunca se cuenta a sí mismo.
        assert all(zoned.id_num != item.id_num for zoned, _ in alike)


def test_el_total_del_historico_cuadra_con_los_toques(
    zones: ZonesRun, signals: ZoneSignalsRun, odds: TimeframeOdds
) -> None:
    by_id = {zoned.id_num: zoned for zoned in zones.per_timeframe[H1].items}
    resueltos = [
        (item.zone, by_id[item.id_num].direction, item.signal.ordinal, by_id[item.id_num].exit_break)
        for item in signals.per_timeframe[H1].items
        if item.kind in (ZoneSignalKind.TOQUE_PUL, ZoneSignalKind.RECHAZO_UL)
        and by_id[item.id_num].exit_break is not None
    ]
    assert odds.totals, "la fixture tiene que dar toques resueltos"
    assert sum(group.all_touches.n for group in odds.totals) == len(resueltos)
    for group in odds.totals:
        mios = [r for r in resueltos if r[0] is group.kind and r[1] is group.direction]
        assert group.all_touches.n == len(mios)
        assert group.all_touches.k == sum(1 for r in mios if r[3] is BreakKind.A_FAVOR)
        assert sum(t.n for t in group.by_ordinal.values()) == group.all_touches.n
        for bucket, tally in group.by_ordinal.items():
            assert tally.n == sum(1 for r in mios if ordinal_bucket(r[2]) == bucket)


def test_la_rotura_del_ul_no_es_un_toque(signals: ZoneSignalsRun, odds: TimeframeOdds) -> None:
    """Es el desenlace, no un toque: contarla sumaría un caso que siempre es a favor."""
    roturas = sum(
        1 for item in signals.per_timeframe[H1].items if item.kind is ZoneSignalKind.ROTURA_UL
    )
    toques = sum(
        1
        for item in signals.per_timeframe[H1].items
        if item.kind in (ZoneSignalKind.TOQUE_PUL, ZoneSignalKind.RECHAZO_UL)
    )
    assert roturas, "la fixture tiene que romper algún UL"
    assert sum(group.all_touches.n for group in odds.totals) <= toques


def test_el_precio_a_secas_mira_solo_antes_de_nacer_y_a_favor_del_id(
    run: ImpulseRun, zones: ZonesRun, odds: TimeframeOdds
) -> None:
    bars = run.analyses[H1].bars
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    by_id = {zoned.id_num: zoned for zoned in zones.per_timeframe[H1].items}
    assert any(item.price_approach.n for item in odds.items), "la fixture tiene que dar visitas"
    for item in odds.items:
        zoned = by_id[item.id_num]
        visits = band_visits(
            high=highs, low=lows, close=closes,
            low_edge=item.low, high_edge=item.high,
            until=zoned.index_constitution,
        )
        # El UL se busca en la dirección del ID y la zona en contra al revés.
        towards = (
            item.direction if item.kind is ZoneKind.LAST else item.direction.opposite()
        )
        approach = visits.from_below if towards is ImpulseDirection.ALCISTA else visits.from_above
        other = visits.from_above if towards is ImpulseDirection.ALCISTA else visits.from_below
        assert item.price_approach.n == approach.n and item.price_other.n == other.n
        if item.direction is ImpulseDirection.ALCISTA:
            assert item.price_approach.k == approach.k
        else:
            # En un ID bajista, a favor es salir por ABAJO: el complementario.
            assert item.price_approach.k == approach.n - approach.k
            assert item.price_other.k == other.n - other.k


def test_un_id_vivo_no_tiene_desenlace(zones: ZonesRun, odds: TimeframeOdds) -> None:
    vivos = {zoned.id_num for zoned in zones.per_timeframe[H1].items if zoned.exit_break is None}
    if not vivos:
        pytest.skip("la fixture no dejó ningún ID vivo")
    ultimo = max(vivos)
    # Los números de la zona del ID vivo se calculan igual (con lo anterior),
    # pero ningún total incluye sus toques: no se sabe cómo acaba.
    assert any(isinstance(item, ZoneOdds) and item.id_num == ultimo for item in odds.items)
    assert all(item.ts_end is not None or item.id_num in vivos for item in odds.items)


def test_sin_senales_no_hay_historia_pero_si_precio(run: ImpulseRun, zones: ZonesRun) -> None:
    from chronos.application.structure.zone_signals import TimeframeSignals

    vacio = zone_odds(
        run.analyses[H1], zones.per_timeframe[H1], TimeframeSignals(timeframe=H1, items=())
    )
    assert vacio.totals == ()
    assert all(item.known_all.empty and item.known_first.empty for item in vacio.items)
    assert any(not item.price_approach.empty for item in vacio.items)
    assert np.all([item.low <= item.high for item in vacio.items])
