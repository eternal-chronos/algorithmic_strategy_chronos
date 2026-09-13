"""La caja del 50 % del retroceso: del ancla del ID al PUL.

Lo que se comprueba es que la caja es la del mentor —ancla del ID nuevo y UL del
anterior, no ancla y extremo—, que sólo la llevan los ID con PUL, que sus toques
caen dentro de la vida del ID, que el paso de la tendencia sale sólo de los ID
anteriores y que la confluencia no puede saberse antes de que cierre la vela de
la temporalidad superior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
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
from chronos.application.structure.pullback import (
    CHAIN_LOOKBACK,
    PullbackBox,
    TrendPace,
    pullback_boxes,
    with_confluence,
)
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import ImpulseDirection
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
def boxes(run: ImpulseRun, zones: ZonesRun) -> tuple[PullbackBox, ...]:
    return pullback_boxes(run.analyses[H4], zones.per_timeframe[H4])


def test_solo_los_id_con_pul_llevan_caja(
    run: ImpulseRun, zones: ZonesRun, boxes: tuple[PullbackBox, ...]
) -> None:
    con_pul = {
        item.id_num
        for item in zones.per_timeframe[H4].items
        if item.has_penultimate and item.id_num in {i.id_num for i in run.analyses[H4].published}
    }
    assert con_pul, "la fixture tiene que traer ID con PUL"
    assert {box.id_num for box in boxes} == con_pul


def test_la_caja_va_del_ancla_al_borde_de_cuerpo_del_pul(
    zones: ZonesRun, boxes: tuple[PullbackBox, ...]
) -> None:
    """No es el 50 % del ID: el otro borde es el UL del ID anterior, no el extremo."""
    zoned = {item.id_num: item for item in zones.per_timeframe[H4].items}
    for box in boxes:
        pul = zoned[box.id_num].penultimate
        assert pul is not None
        assert {box.low, box.high} == {zoned[box.id_num].anchor, pul.outer}
        assert box.mid == pytest.approx((box.low + box.high) / 2)
        assert box.pul_inner == pul.inner


def test_los_toques_caen_dentro_de_la_vida_del_id(
    run: ImpulseRun, boxes: tuple[PullbackBox, ...]
) -> None:
    last = pd.Timestamp(run.analyses[H4].bars.index[-1]).to_pydatetime()
    for box in boxes:
        end = box.ts_end or last
        for stamp in (box.ts_pul_touch, box.ts_mid_touch, box.ts_retrace):
            if stamp is None:
                continue
            assert box.ts_constitution <= stamp <= end


def test_llegar_al_50_implica_haber_llegado_al_pul(boxes: tuple[PullbackBox, ...]) -> None:
    """El 50 % de la caja está más allá del PUL: no se llega a uno sin pasar el otro."""
    for box in boxes:
        if box.ts_mid_touch is not None:
            assert box.ts_pul_touch is not None
            assert box.ts_pul_touch <= box.ts_mid_touch


def test_el_primer_id_de_la_cadena_no_tiene_paso(boxes: tuple[PullbackBox, ...]) -> None:
    """Sin ID anteriores con caja no hay historia: no se inventa un paso."""
    assert any(box.pace is None for box in boxes)
    for box in boxes:
        if box.chain <= 1:
            assert box.pace is None


def test_el_paso_sale_de_los_id_anteriores_y_no_del_propio(
    boxes: tuple[PullbackBox, ...],
) -> None:
    by_id = {box.id_num: box for box in boxes}
    for box in boxes:
        anteriores = [
            by_id[box.id_num - back]
            for back in range(1, box.chain + 1)
            if box.id_num - back in by_id
        ][:CHAIN_LOOKBACK]
        if not anteriores:
            assert box.pace is None
            continue
        esperado = (
            TrendPace.SLOW if any(item.reached_mid for item in anteriores) else TrendPace.FAST
        )
        assert box.pace is esperado


def test_la_parte_devuelta_sale_de_la_pierna(boxes: tuple[PullbackBox, ...]) -> None:
    for box in boxes:
        assert box.leg_range >= 0
        assert box.retrace_range >= 0
        if box.leg_range > 0:
            assert box.retrace_share == pytest.approx(box.retrace_range / box.leg_range)


def test_en_h1_hay_cajas_que_confluyen_con_h4(run: ImpulseRun, zones: ZonesRun) -> None:
    h4 = pullback_boxes(run.analyses[H4], zones.per_timeframe[H4])
    h1 = pullback_boxes(run.analyses[H1], zones.per_timeframe[H1])
    marcadas = with_confluence(h1, h4, lower_span=timedelta(hours=1), upper_span=timedelta(hours=4))
    assert len(marcadas) == len(h1)
    con = [box for box in marcadas if box.confluence is not None]
    assert con, "la fixture tiene que dar al menos una confluencia"
    for box in con:
        assert box.confluence is not None
        assert box.confluence.timeframe == H4
        assert box.confluence.low <= box.mid <= box.confluence.high


def _box(
    timeframe: str,
    id_num: int,
    born: datetime,
    *,
    low: float,
    high: float,
    end: datetime | None = None,
) -> PullbackBox:
    return PullbackBox(
        timeframe=timeframe,
        id_num=id_num,
        direction=ImpulseDirection.ALCISTA,
        ts_constitution=born,
        ts_end=end,
        low=low,
        high=high,
        mid=(low + high) / 2,
        pul_inner=high,
        ts_pul_touch=None,
        ts_mid_touch=None,
        leg_range=10.0,
        leg_bars=3,
        retrace_range=0.0,
        retrace_bars=0,
        ts_retrace=None,
        chain=1,
        pace=None,
    )


def test_la_confluencia_no_se_sabe_antes_de_que_cierre_la_vela_superior() -> None:
    """La caja de H4 constituida a las 12:00 no se conoce hasta las 16:00: la de H1
    que nace a las 13:00 no puede llevarla."""
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    h4 = (_box(H4, 1, t0, low=100.0, high=110.0),)
    temprana = _box(H1, 1, t0 + timedelta(hours=1), low=103.0, high=107.0)
    tardia = _box(H1, 2, t0 + timedelta(hours=4), low=103.0, high=107.0)
    marcadas = with_confluence(
        (temprana, tardia), h4, lower_span=timedelta(hours=1), upper_span=timedelta(hours=4)
    )
    assert marcadas[0].confluence is None
    assert marcadas[1].confluence is not None
    assert marcadas[1].confluence.id_num == 1


def test_sin_encajar_no_hay_confluencia() -> None:
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    h4 = (_box(H4, 1, t0, low=100.0, high=110.0),)
    fuera = _box(H1, 1, t0 + timedelta(days=1), low=120.0, high=130.0)
    (marcada,) = with_confluence(
        (fuera,), h4, lower_span=timedelta(hours=1), upper_span=timedelta(hours=4)
    )
    assert marcada.confluence is None


def test_una_caja_superior_ya_muerta_no_confluye() -> None:
    t0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    h4 = (_box(H4, 1, t0, low=100.0, high=110.0, end=t0 + timedelta(days=1)),)
    despues = _box(H1, 1, t0 + timedelta(days=2), low=103.0, high=107.0)
    (marcada,) = with_confluence(
        (despues,), h4, lower_span=timedelta(hours=1), upper_span=timedelta(hours=4)
    )
    assert marcada.confluence is None
