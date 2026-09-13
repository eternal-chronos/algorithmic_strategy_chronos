"""Las entradas del 2026-09-13 sobre una corrida completa.

Lo que se comprueba es sobre todo lo que las entradas NO pueden hacer: no salen
sin zonas ni sin ID en H1 y M15, no se arman antes de saberse lo que las
justifica, no entran fuera de la franja de Nueva York, no viven más allá de las
16:00, no hay dos en un día y no mueven un solo impulso. Las cuentas de cada
regla —stop, objetivo, rotura del PUL— están en `tests/domain/entries`.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pandas as pd
import pytest

from chronos.application.entries.trades import (
    STOP_FROM_H1,
    STOP_FROM_M15,
    EntriesRun,
    detect_entries,
)
from chronos.application.entries.trading_day import TradingDay
from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M5,
    M15,
    AggregationConfig,
    ChartsConfig,
    EntriesConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.entries.rules import ContextState, EntryKind, OrderEnd, TradeOutcome
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history

#: El reparto del proyecto con las entradas: ID propio también en M15.
CHARTS = ChartsConfig(
    {DAILY: (DAILY,), H4: (H4,), H1: (H1, H4), M15: (M15, H1, H4), M5: (M15, H1, H4)}
)
NY = "America/New_York"


def _run(*, entries: bool = True, zones: bool = True, charts: ChartsConfig = CHARTS) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(
            warmup_bars=5, break_by_zone=True, break_against_by_zone=False
        ),
        charts=charts,
        zones=ZonesConfig(enabled=zones),
        entries=EntriesConfig(enabled=entries),
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
    return _run()


@pytest.fixture(scope="module")
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


@pytest.fixture(scope="module")
def entries(run: ImpulseRun, zones: ZonesRun) -> EntriesRun:
    return detect_entries(run, zones)


def _local(moment: object) -> pd.Timestamp:
    return pd.Timestamp(moment).tz_convert(NY)


# --- Cuándo no hay nada -------------------------------------------------------


def test_apagadas_no_emiten_nada(run: ImpulseRun, zones: ZonesRun) -> None:
    medida = detect_entries(run, zones, replace(run.config.entries, enabled=False))

    assert medida.enabled is False
    assert medida.empty


def test_sin_zonas_no_hay_entradas() -> None:
    apagado = _run(zones=False)

    assert detect_entries(apagado, detect_zones(apagado)).enabled is False


def test_sin_id_en_m15_no_hay_entradas() -> None:
    """El reparto sin detector en M15 no da para esto: no se emite nada."""
    sin_m15 = _run(
        charts=ChartsConfig(
            {DAILY: (DAILY,), H4: (H4,), H1: (H1, H4), M15: (H1, H4), M5: (H1, H4)}
        )
    )

    assert detect_entries(sin_m15, detect_zones(sin_m15)).enabled is False


# --- Lo que sale --------------------------------------------------------------


def test_el_historico_de_prueba_produce_entradas_de_los_dos_tipos(entries: EntriesRun) -> None:
    assert entries.enabled
    assert entries.source == M5
    tipos = {item.kind for item in entries.entries}
    assert tipos == {EntryKind.ROTURA_PUL, EntryKind.TOQUE_ZONA}
    assert entries.filled, "alguna tiene que llegar a entrar"


def test_la_rotura_del_pul_entra_en_contra_del_id_y_el_toque_a_favor(
    entries: EntriesRun,
) -> None:
    for item in entries.entries:
        if item.kind is EntryKind.ROTURA_PUL:
            assert item.direction is item.h1_direction.opposite()
            assert item.zone_kind is ZoneKind.PENULTIMATE
            # El límite va en el borde exterior del PUL: el más lejano del ID.
            assert item.entry in (item.zone_low, item.zone_high)
        else:
            assert item.direction is item.h1_direction
            assert item.zone_kind in (ZoneKind.PENULTIMATE, ZoneKind.ANTE_PENULTIMATE)


def test_el_objetivo_es_cuatro_veces_el_riesgo_y_el_stop_protege(entries: EntriesRun) -> None:
    for item in entries.entries:
        assert item.risk > 0
        if item.direction is ImpulseDirection.ALCISTA:
            assert item.stop < item.entry < item.target
        else:
            assert item.target < item.entry < item.stop
        assert abs(item.target - item.entry) == pytest.approx(4.0 * item.risk)


def test_el_stop_es_el_mas_cercano_entre_h1_y_m15(entries: EntriesRun) -> None:
    """Con la zona de M15 al otro lado de la entrada, gana la más cercana."""
    fuentes = {item.stop_source for item in entries.entries}
    assert fuentes <= {STOP_FROM_H1, STOP_FROM_M15}
    for item in entries.entries:
        if item.m15_zone_low is None or item.m15_zone_high is None:
            assert item.stop_source == STOP_FROM_H1
            continue
        h1_edges = (item.zone_low, item.zone_high)
        if item.direction is ImpulseDirection.BAJISTA:
            candidates = [c for c in (*h1_edges, item.m15_zone_high) if c > item.entry]
            assert item.stop == pytest.approx(min(candidates))
        else:
            candidates = [c for c in (*h1_edges, item.m15_zone_low) if c < item.entry]
            assert item.stop == pytest.approx(max(candidates))


# --- El reloj de Nueva York ---------------------------------------------------


def test_solo_se_arma_y_se_entra_dentro_de_la_franja(entries: EntriesRun) -> None:
    day = entries.day
    for item in entries.entries:
        armada = _local(item.ts_armed).time()
        assert day.start <= armada < day.end, item.ts_armed
        if item.ts_filled is not None:
            entrada = _local(item.ts_filled).time()
            assert day.start <= entrada < day.end, item.ts_filled
            assert item.ts_filled > item.ts_armed


def test_ninguna_posicion_sobrevive_a_las_16_00(entries: EntriesRun) -> None:
    for item in entries.filled:
        if item.outcome is TradeOutcome.ABIERTA:
            continue
        assert item.ts_closed is not None
        salida = _local(item.ts_closed)
        assert salida.time() < entries.day.flat_at, item
        assert salida.date() == _local(item.ts_filled).date()


def test_el_cierre_de_sesion_sale_al_precio_de_esa_vela(
    run: ImpulseRun, entries: EntriesRun
) -> None:
    finas = run.chart_bars[M5]
    cerradas = [item for item in entries.filled if item.outcome is TradeOutcome.CIERRE_SESION]
    for item in cerradas:
        assert item.exit_price == pytest.approx(float(finas.loc[pd.Timestamp(item.ts_closed), "close"]))
        assert item.r_multiple is not None


def test_una_operacion_por_dia(entries: EntriesRun) -> None:
    dias = [_local(item.ts_filled).date() for item in entries.filled]

    assert len(dias) == len(set(dias))


def test_los_limites_fuera_de_franja_se_quitan(entries: EntriesRun) -> None:
    for item in entries.entries:
        if item.cancelled_by is OrderEnd.FIN_FRANJA:
            assert item.ts_cancelled is not None
            assert _local(item.ts_cancelled).time() >= entries.day.end


# --- Nada se sabe antes de tiempo ---------------------------------------------


def test_el_limite_no_se_arma_antes_de_que_exista_el_id_de_h1(
    run: ImpulseRun, zones: ZonesRun, entries: EntriesRun
) -> None:
    """La vela de H1 que constituye el ID cierra una hora después de su etiqueta."""
    por_id = {zoned.id_num: zoned for zoned in zones.per_timeframe[H1].items}
    for item in entries.entries:
        zoned = por_id[item.h1_id]
        assert pd.Timestamp(item.ts_armed) >= pd.Timestamp(zoned.ts_constitution) + pd.Timedelta(
            minutes=60
        ) - pd.Timedelta(minutes=5)
        if zoned.ts_end is not None:
            assert pd.Timestamp(item.ts_armed) < pd.Timestamp(zoned.ts_end) + pd.Timedelta(
                minutes=60
            )


def test_la_rotura_del_pul_se_arma_despues_de_la_vela_que_lo_rompe(
    run: ImpulseRun, zones: ZonesRun, entries: EntriesRun
) -> None:
    velas = run.analyses[H1].bars
    por_id = {zoned.id_num: zoned for zoned in zones.per_timeframe[H1].items}
    roturas = [item for item in entries.entries if item.kind is EntryKind.ROTURA_PUL]
    assert roturas
    for item in roturas:
        zoned = por_id[item.h1_id]
        pul = zoned.penultimate
        assert pul is not None
        # Alguna vela de H1 cerrada ANTES de armar cerró más allá del borde exterior.
        antes = velas.loc[: pd.Timestamp(item.ts_armed) - pd.Timedelta(minutes=55)]
        vivas = antes.loc[pd.Timestamp(zoned.ts_constitution) :]
        if zoned.direction is ImpulseDirection.ALCISTA:
            assert (vivas["close"] < pul.outer).any()
        else:
            assert (vivas["close"] > pul.outer).any()


def test_medir_las_entradas_no_mueve_ni_un_impulso(run: ImpulseRun, zones: ZonesRun) -> None:
    antes = run.analyses[H1].table.copy()
    detect_entries(run, zones)

    pd.testing.assert_frame_equal(antes, run.analyses[H1].table)


# --- El contexto --------------------------------------------------------------


def test_los_tramos_de_busqueda_cubren_el_historico_sin_solaparse(
    entries: EntriesRun,
) -> None:
    tramos = entries.seeking
    assert tramos
    for anterior, siguiente in pairwise(tramos):
        assert anterior.end == siguiente.start
    assert tramos[-1].end is None


def test_cada_entrada_respeta_lo_que_se_buscaba(entries: EntriesRun) -> None:
    """La dirección de la entrada cae dentro del tramo vigente al armarla."""
    for item in entries.entries:
        tramo = [
            found
            for found in entries.seeking
            if found.start <= item.ts_armed and (found.end is None or item.ts_armed < found.end)
        ]
        assert len(tramo) == 1
        assert item.direction in tramo[0].allowed


def test_el_contexto_toma_los_cuatro_estados_o_los_declara(entries: EntriesRun) -> None:
    estados = {tramo.h4_state for tramo in entries.seeking if tramo.h4_state is not None}
    assert ContextState.LIBRE in estados
    assert ContextState.A_FAVOR in estados


def test_con_h4_a_favor_solo_se_busca_en_su_direccion(entries: EntriesRun) -> None:
    for tramo in entries.seeking:
        if tramo.h4_state is ContextState.A_FAVOR:
            assert len(tramo.allowed) <= 1
        if tramo.h4_state is ContextState.EN_CONTRA:
            assert len(tramo.allowed) <= 1


def test_el_recuento_declara_todos_los_finales(entries: EntriesRun) -> None:
    recuento = entries.counts()

    assert recuento["LIMITES"] == len(entries.entries)
    assert recuento["ENTRADAS"] == len(entries.filled)
    assert all(kind.value in recuento for kind in TradeOutcome)


# --- El día de operativa ------------------------------------------------------


def test_la_franja_sigue_el_horario_de_verano_de_nueva_york() -> None:
    day = TradingDay()
    invierno = pd.DatetimeIndex(["2024-01-15 06:59", "2024-01-15 07:00", "2024-01-15 17:00"], tz="UTC")
    verano = pd.DatetimeIndex(["2024-07-15 05:59", "2024-07-15 06:00", "2024-07-15 16:00"], tz="UTC")

    assert list(day.in_window(invierno)) == [False, True, False]
    assert list(day.in_window(verano)) == [False, True, False]


def test_el_cierre_es_la_ultima_vela_antes_de_las_16_00() -> None:
    day = TradingDay()
    velas = pd.date_range("2024-01-15 20:45", periods=5, freq="5min", tz="UTC")  # 15:45..16:05 NY

    assert list(day.flat(velas)) == [False, False, True, False, False]


def test_el_dia_se_cuenta_en_la_plaza() -> None:
    day = TradingDay()
    velas = pd.DatetimeIndex(["2024-01-15 03:00", "2024-01-15 05:00"], tz="UTC")  # 22:00 y 00:00 NY

    dias = day.days(velas)
    assert dias[0] != dias[1]
