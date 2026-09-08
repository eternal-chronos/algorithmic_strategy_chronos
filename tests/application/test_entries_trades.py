"""Fase 3.1 — las entradas, sobre una corrida completa.

Lo que se comprueba aquí es sobre todo lo que las entradas **no** hacen: no salen
sin zonas ni sin el ID de H1, no van nunca contra el régimen que dice H4, no
abren dos a la vez, no repiten ID de H1, no entran antes de que exista el límite,
no ponen el objetivo a otra distancia que 1:3, no arman fuera de la franja de
operativa, **no sobreviven al cierre del viernes** y no cambian ni un número de las
fases 1, 2.0, 2.1 y 3.0.

Los datos son sintéticos y sus resultados no significan nada: lo que se fija son
las reglas, no la rentabilidad.
"""

from __future__ import annotations

from itertools import pairwise

import pandas as pd
import pytest

from chronos.application.entries.cascade import detect_cascade

# `_Entries` y `_bar_of` son privados a propósito: la regla del patrón de detrás
# es "sólo si en la zona no había nada EN ESA VELA", y eso no se puede comprobar
# desde fuera con la lista de operaciones: hace falta preguntarle al recorrido
# qué tenía a mano en esa vela.
from chronos.application.entries.trades import (
    REJECTION_OFFSET,
    EntriesConfig,
    EntriesRun,
    Entry,
    EntryForm,
    OrderEnd,
    RegimeEdge,
    RegimeKind,
    TradeOutcome,
    _bar_of,
    _Entries,
    detect_entries,
)
from chronos.application.entries.trading_window import (
    MARKET_WEEK,
    TRADING_WINDOW,
    MarketWeek,
)
from chronos.application.structure.config import (
    H1,
    H4,
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
    with_hourly_structure,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zone_signals import detect_zone_signals
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.entries.patterns import PatternKind
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import Zone
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


def _run(*, zones: bool = True, hourly_id: bool = True) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5, break_by_zone=True),
        zones=ZonesConfig(enabled=zones),
        charts=with_hourly_structure(ChartsConfig()) if hourly_id else ChartsConfig(),
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
    return _run()


@pytest.fixture(scope="module")
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


@pytest.fixture(scope="module")
def trades(run: ImpulseRun, zones: ZonesRun) -> EntriesRun:
    return detect_entries(run, zones)


# --- Lo que hace falta para que exista --------------------------------------


def test_sin_zonas_no_hay_entradas() -> None:
    apagado = _run(zones=False)
    medida = detect_entries(apagado, detect_zones(apagado))

    assert medida.enabled is False
    assert medida.empty


def test_sin_id_en_h1_no_hay_entradas() -> None:
    """El setup lo arma H1: sin detector propio ahí no hay segundo escalón."""
    sin_h1 = _run(hourly_id=False)
    medida = detect_entries(sin_h1, detect_zones(sin_h1))

    assert medida.enabled is False
    assert medida.empty


def test_la_corrida_produce_operaciones(trades: EntriesRun) -> None:
    assert trades.enabled is True
    assert trades.entries, "la fixture tiene que llegar a poner algún límite"
    assert trades.filled, "y alguno tiene que llenarse"
    assert trades.regimes


# --- El régimen de H4 -------------------------------------------------------


def test_el_regimen_arranca_buscando_en_contra_del_id_de_h4(trades: EntriesRun) -> None:
    """Recién constituido el ID, el precio todavía no ha vuelto a su zona."""
    primeros = {}
    for regime in trades.regimes:
        primeros.setdefault(regime.h4_id, regime)

    for regime in primeros.values():
        if regime.opened_by is not RegimeEdge.CONSTITUCION_H4:
            continue
        assert regime.kind is RegimeKind.HACIA_ZONA
        assert regime.direction is regime.h4_direction.opposite()


def test_cada_tramo_busca_hacia_donde_va_el_precio(trades: EntriesRun) -> None:
    for regime in trades.regimes:
        if regime.kind is RegimeKind.HACIA_ZONA:
            assert regime.direction is regime.h4_direction.opposite()
        elif regime.kind is RegimeKind.HACIA_UL:
            assert regime.direction is regime.h4_direction
        else:
            # En el UL se espera al cierre de la vela de H4: no se busca nada.
            assert regime.direction is None


def test_los_tramos_de_un_mismo_id_van_en_cadena_y_sin_solaparse(
    trades: EntriesRun,
) -> None:
    por_id: dict[int, list] = {}
    for regime in trades.regimes:
        por_id.setdefault(regime.h4_id, []).append(regime)

    for tramos in por_id.values():
        for antes, despues in pairwise(tramos):
            assert antes.end is not None
            # El tramo siguiente empieza donde acaba el anterior: lo que decide
            # el cambio se sabe al cerrar esa vela.
            assert despues.start == antes.end
            assert despues.opened_by is antes.closed_by


def test_el_tramo_del_ul_solo_se_cierra_con_el_cierre_de_la_vela_de_h4(
    trades: EntriesRun,
) -> None:
    mudos = [item for item in trades.regimes if item.kind is RegimeKind.EN_UL]

    assert mudos
    for regime in mudos:
        assert regime.opened_by is RegimeEdge.TOQUE_UL
        assert regime.closed_by in (
            RegimeEdge.RECHAZO_UL,
            RegimeEdge.ROTURA_UL,
            RegimeEdge.MUERTE_ID_H4,
            None,
        )


def test_tras_romper_el_ul_ese_id_no_vuelve_a_buscar_nada(trades: EntriesRun) -> None:
    """Atravesado el UL entero, el ID de H4 se acabó: no hay régimen que seguir."""
    por_id: dict[int, list] = {}
    for regime in trades.regimes:
        por_id.setdefault(regime.h4_id, []).append(regime)

    for tramos in por_id.values():
        roturas = [
            position
            for position, item in enumerate(tramos)
            if item.closed_by is RegimeEdge.ROTURA_UL
        ]
        for position in roturas:
            assert position == len(tramos) - 1


# --- Las entradas -----------------------------------------------------------


def test_ninguna_entrada_va_contra_el_regimen_de_su_tramo(trades: EntriesRun) -> None:
    for entry in trades.entries:
        cubren = [
            regime
            for regime in trades.regimes
            if regime.h4_id == entry.h4_id
            and regime.start <= entry.ts_armed
            and (regime.end is None or entry.ts_armed <= regime.end)
        ]
        assert cubren, entry
        assert any(regime.direction is entry.direction for regime in cubren), entry


def test_el_objetivo_esta_siempre_a_tres_veces_el_riesgo(trades: EntriesRun) -> None:
    for entry in trades.entries:
        assert entry.risk > 0
        assert abs(entry.target - entry.entry) == pytest.approx(3 * entry.risk, rel=1e-9)
        if entry.direction is ImpulseDirection.BAJISTA:
            assert entry.target < entry.entry < entry.stop
        else:
            assert entry.stop < entry.entry < entry.target


def _zona_de(zones: ZonesRun, entry: Entry) -> Zone | None:
    """La zona de H1 de la que cuelga la entrada: el UL o la de en contra."""
    zoned = {item.id_num: item for item in zones.per_timeframe[H1].items}[entry.h1_id]
    return zoned.last if entry.form is EntryForm.RECHAZO_UL else zoned.against


def _es_rechazo_con_ob(entry: Entry) -> bool:
    return (
        entry.form is EntryForm.RECHAZO_UL
        and entry.pattern is PatternKind.ORDER_BLOCK
    )


def test_el_stop_va_a_un_borde_de_la_zona_de_h1(
    trades: EntriesRun, zones: ZonesRun
) -> None:
    """El sitio más cercano en el que el setup deja de existir.

    El borde EXTERIOR en todo menos en el rechazo del UL afinado con un OB, que
    va al INTERIOR: ahí la punta de la mecha del UL queda demasiado lejos.
    """
    rechazos = 0

    for entry in trades.entries:
        # El patrón de detrás lleva el stop a su propio borde: tiene su test.
        if entry.form is EntryForm.ZONA_ATRAS:
            continue
        zone = _zona_de(zones, entry)
        assert zone is not None
        if _es_rechazo_con_ob(entry):
            assert entry.stop == pytest.approx(zone.inner)
            rechazos += 1
        else:
            assert entry.stop == pytest.approx(zone.outer)
        assert (entry.zone_low, entry.zone_high) == (zone.low, zone.high)

    assert rechazos, "la corrida tiene que traer algún rechazo afinado con un OB"


def test_el_stop_del_rechazo_con_ob_esta_mas_cerca_que_la_punta_del_ul(
    trades: EntriesRun, zones: ZonesRun
) -> None:
    """Es la razón del cambio: al final del UL había mucha distancia."""
    acortados = 0

    for entry in trades.entries:
        if not _es_rechazo_con_ob(entry):
            continue
        zone = _zona_de(zones, entry)
        assert zone is not None
        antes = abs(entry.entry - zone.outer)
        assert entry.risk <= antes
        # La zona plana —la vela del extremo no tenía mecha— es el único caso en
        # el que los dos bordes valen lo mismo y el stop no se mueve.
        if not zone.is_flat:
            assert entry.risk < antes
            acortados += 1

    assert acortados, "algún rechazo con OB tiene que haber acortado el stop"


def test_el_limite_esta_en_el_borde_cercano_del_patron_de_m15(
    trades: EntriesRun,
) -> None:
    """Vale para todo menos para el rechazo con OB, que tiene su propia regla."""
    for entry in trades.entries:
        if entry.form is EntryForm.RECHAZO_UL and entry.pattern is PatternKind.ORDER_BLOCK:
            continue
        borde = (
            entry.pattern_low
            if entry.direction is ImpulseDirection.BAJISTA
            else entry.pattern_high
        )
        assert entry.entry == pytest.approx(borde)


def test_el_limite_del_rechazo_con_ob_sale_de_la_vela_que_lo_crea(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    """El rechazo no se vende arriba del bloque: se vende justo por delante del
    cierre de la vela que se fue y convirtió a la anterior en OB.

    Lo pidió el propietario mirando la nº 855: el OB sobresalía por encima del UL
    y el límite quedaba en un precio al que el mercado ya no volvía.
    """
    velas = run.chart_bars[M15]
    medidos = 0

    for entry in trades.entries:
        if entry.form is not EntryForm.RECHAZO_UL or entry.pattern is not PatternKind.ORDER_BLOCK:
            continue
        cierre = float(velas.loc[entry.ts_pattern_known, "close"])
        signo = 1 if entry.direction is ImpulseDirection.BAJISTA else -1
        assert entry.entry == pytest.approx(cierre + signo * REJECTION_OFFSET)
        # Y por delante del precio: en una venta, por encima de donde cerró.
        assert signo * (entry.entry - cierre) > 0
        # La vela que lo crea nunca es anterior a la que lo define.
        assert entry.ts_pattern_known >= entry.ts_pattern
        medidos += 1

    assert medidos, "la corrida tiene que traer algún rechazo afinado con un OB"


def test_el_rechazo_con_fvg_y_la_forma_zona_no_cambian(trades: EntriesRun) -> None:
    """El cambio es del rechazo con OB y de nada más."""
    for entry in trades.entries:
        if entry.form is EntryForm.RECHAZO_UL and entry.pattern is PatternKind.ORDER_BLOCK:
            continue
        dentro = entry.pattern_low <= entry.entry <= entry.pattern_high
        assert dentro, entry


def test_el_patron_cae_dentro_de_la_zona_de_h1(trades: EntriesRun) -> None:
    """La entrada se afina DENTRO de la zona, no en cualquier sitio de M15.

    Salvo cuando dentro no había nada: ésa es la forma `ZONA_ATRAS` y va aparte.
    """
    for entry in trades.entries:
        if entry.form is EntryForm.ZONA_ATRAS:
            continue
        assert entry.pattern_low <= entry.zone_high
        assert entry.zone_low <= entry.pattern_high


# --- El patrón de DETRÁS de la zona -----------------------------------------


def _atras(trades: EntriesRun) -> list[Entry]:
    return [item for item in trades.entries if item.form is EntryForm.ZONA_ATRAS]


def test_la_corrida_trae_patrones_de_detras_de_la_zona(trades: EntriesRun) -> None:
    """Sin ninguno, los tests de esta sección no comprobarían nada."""
    assert _atras(trades)


def test_el_patron_de_detras_queda_entero_al_otro_lado_del_borde_exterior(
    trades: EntriesRun, zones: ZonesRun
) -> None:
    """Detrás es detrás: por debajo del suelo en una compra y por encima del
    techo en una venta. Uno que rozara la zona habría entrado por la vía normal.
    """
    for entry in _atras(trades):
        zone = _zona_de(zones, entry)
        assert zone is not None
        if entry.direction is ImpulseDirection.ALCISTA:
            assert entry.pattern_high < zone.outer
        else:
            assert entry.pattern_low > zone.outer
        # Y por tanto no solapa la zona por ningún lado.
        assert entry.pattern_high < entry.zone_low or entry.pattern_low > entry.zone_high


def test_el_stop_del_patron_de_detras_va_a_su_borde_lejano(
    trades: EntriesRun,
) -> None:
    """El borde de la zona no sirve ahí: queda al otro lado del límite."""
    for entry in _atras(trades):
        lejano = (
            entry.pattern_high
            if entry.direction is ImpulseDirection.BAJISTA
            else entry.pattern_low
        )
        cercano = (
            entry.pattern_low
            if entry.direction is ImpulseDirection.BAJISTA
            else entry.pattern_high
        )
        assert entry.stop == pytest.approx(lejano)
        assert entry.entry == pytest.approx(cercano)
        assert entry.risk == pytest.approx(entry.pattern_high - entry.pattern_low)


def test_el_patron_de_detras_se_conocia_antes_de_armar_el_limite(
    trades: EntriesRun,
) -> None:
    """El sitio se elige con lo que había, no con lo que llegó después."""
    for entry in _atras(trades):
        assert entry.ts_pattern_known <= entry.ts_armed


def test_solo_se_busca_detras_si_en_la_zona_no_habia_nada_en_esa_vela(
    run: ImpulseRun, zones: ZonesRun, trades: EntriesRun
) -> None:
    """La regla entera: detrás es el recambio, no una alternativa a elegir.

    Se comprueba vela a vela y no sobre la vida del ID: que más tarde nazca un
    patrón dentro de la zona no invalida el límite que se puso cuando no había
    ninguno.
    """
    builder = _Entries(
        run,
        zones,
        detect_zone_signals(run, zones),
        TRADING_WINDOW,
        MARKET_WEEK,
        EntriesConfig(),
    )
    posiciones = {
        item.id_num: position
        for position, item in enumerate(zones.per_timeframe[H1].items)
    }

    for entry in _atras(trades):
        position = posiciones[entry.h1_id]
        index = _bar_of(builder._fine, entry.ts_armed)
        dentro = builder._pick_among(
            builder._candidates_of(position),
            index,
            entry.direction,
            float(builder._fine.close[index]),
            behind=False,
        )
        assert dentro is None, entry


def test_el_setup_del_rechazo_solo_sale_de_un_id_de_h1_que_va_al_reves(
    trades: EntriesRun, zones: ZonesRun
) -> None:
    por_id = {item.id_num: item for item in zones.per_timeframe[H1].items}

    for entry in trades.entries:
        zoned = por_id[entry.h1_id]
        if entry.form is EntryForm.RECHAZO_UL:
            assert zoned.direction is entry.direction.opposite()
        else:
            # ZONA y ZONA_ATRAS son el mismo setup: cambia dónde está el patrón.
            assert zoned.direction is entry.direction


def test_nada_ocurre_antes_de_que_el_limite_exista(trades: EntriesRun) -> None:
    for entry in trades.entries:
        assert entry.ts_pattern <= entry.ts_pattern_known <= entry.ts_armed
        if entry.ts_filled is not None:
            # Un límite no se llena en la vela en la que se pone: se pone AL
            # CERRAR esa vela.
            assert entry.ts_filled > entry.ts_armed
        if entry.ts_closed is not None:
            assert entry.ts_filled is not None
            assert entry.ts_closed >= entry.ts_filled


def test_un_limite_o_entra_o_se_quita_pero_no_las_dos_cosas(trades: EntriesRun) -> None:
    for entry in trades.entries:
        assert (entry.ts_filled is None) != (entry.cancelled_by is None), entry
        if entry.cancelled_by is not None:
            assert entry.ts_cancelled is not None
            assert entry.outcome is None


def test_nunca_hay_dos_operaciones_vivas_a_la_vez(trades: EntriesRun) -> None:
    abiertas = sorted(
        (entry.ts_filled, entry.ts_closed)
        for entry in trades.filled
        if entry.ts_filled is not None
    )
    for antes, despues in pairwise(abiertas):
        assert antes[1] is not None, "sólo la última puede quedarse abierta"
        assert antes[1] <= despues[0]


def test_una_operacion_por_id_de_h1_y_ni_una_mas(trades: EntriesRun) -> None:
    entrados = [entry.h1_id for entry in trades.filled]

    assert len(entrados) == len(set(entrados))


def test_los_limites_se_arman_dentro_de_la_franja_de_operativa(
    trades: EntriesRun,
) -> None:
    for entry in trades.entries:
        assert TRADING_WINDOW.contains(entry.ts_armed), entry.ts_armed


def test_la_operacion_se_cierra_en_el_stop_en_el_objetivo_o_el_viernes(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    velas = run.chart_bars[M15]

    for entry in trades.filled:
        if entry.outcome is TradeOutcome.OBJETIVO:
            assert entry.exit_price == pytest.approx(entry.target)
        elif entry.outcome is TradeOutcome.STOP:
            assert entry.exit_price == pytest.approx(entry.stop)
        elif entry.outcome is TradeOutcome.CIERRE_SEMANAL:
            assert entry.ts_closed is not None
            assert entry.exit_price == pytest.approx(
                float(velas.loc[entry.ts_closed, "close"])
            )
        else:
            assert entry.outcome is TradeOutcome.ABIERTA
            assert entry.ts_closed is None


def test_las_operaciones_salen_en_orden(trades: EntriesRun) -> None:
    marcas = [entry.ts_armed for entry in trades.entries]

    assert marcas == sorted(marcas)
    assert [entry.seq for entry in trades.entries] == list(
        range(1, len(trades.entries) + 1)
    )


def test_el_recuento_cuadra_con_las_operaciones(trades: EntriesRun) -> None:
    counts = trades.counts()

    assert counts["LIMITES"] == len(trades.entries)
    assert counts["ENTRADAS"] == len(trades.filled)
    salidas = (
        counts["OBJETIVO"]
        + counts["STOP"]
        + counts["CIERRE_SEMANAL"]
        + counts["ABIERTA"]
    )
    assert salidas == counts["ENTRADAS"]


# --- El cierre del viernes --------------------------------------------------


def _cierres_semanales(run: ImpulseRun) -> pd.DatetimeIndex:
    """Las velas de M15 en las que cierra la semana de mercado."""
    velas = pd.DatetimeIndex(run.chart_bars[M15].index)
    return velas[MARKET_WEEK.closes(velas)]


def test_ninguna_posicion_sigue_viva_despues_del_cierre_del_viernes(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    """El fin de semana no se aguanta nada: ni una posición ni media."""
    cierres = _cierres_semanales(run)
    assert len(cierres), "el histórico sintético tiene que traer viernes"

    for entry in trades.filled:
        vivos = cierres[cierres >= entry.ts_filled]
        if entry.ts_closed is not None:
            vivos = vivos[vivos < entry.ts_closed]
        assert vivos.empty, entry


def test_ningun_limite_sigue_puesto_despues_del_cierre_del_viernes(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    cierres = _cierres_semanales(run)

    for entry in trades.entries:
        fin = entry.ts_filled if entry.ts_filled is not None else entry.ts_cancelled
        assert fin is not None
        puestos = cierres[(cierres >= entry.ts_armed) & (cierres < fin)]
        assert puestos.empty, entry


def test_el_cierre_del_viernes_paga_el_precio_de_esa_vela_y_no_manda_sobre_el_stop(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    """Se sale al cierre de la última vela de la semana, no en el stop ni en el
    objetivo: si esa vela hubiera llegado a uno de los dos, habría acabado ahí."""
    velas = run.chart_bars[M15]
    cierres = set(_cierres_semanales(run))
    medidos = 0

    for entry in trades.filled:
        if entry.outcome is not TradeOutcome.CIERRE_SEMANAL:
            continue
        assert entry.ts_closed in cierres
        vela = velas.loc[entry.ts_closed]
        assert entry.exit_price == pytest.approx(float(vela["close"]))
        if entry.direction is ImpulseDirection.BAJISTA:
            assert float(vela["high"]) < entry.stop
            assert float(vela["low"]) > entry.target
        else:
            assert float(vela["low"]) > entry.stop
            assert float(vela["high"]) < entry.target
        medidos += 1

    assert medidos, "la corrida tiene que cerrar alguna operación el viernes"


def test_los_limites_quitados_el_viernes_se_quitan_en_la_vela_del_cierre(
    run: ImpulseRun, trades: EntriesRun
) -> None:
    cierres = set(_cierres_semanales(run))

    for entry in trades.entries:
        if entry.cancelled_by is not OrderEnd.CIERRE_SEMANAL:
            continue
        assert entry.ts_cancelled in cierres
        assert entry.ts_filled is None


def test_la_semana_de_mercado_acaba_en_la_ultima_vela_antes_del_viernes() -> None:
    """La hora es la de la plaza: en enero son las 22:00 UTC y en julio las 21:00."""
    invierno = pd.date_range("2024-01-01", periods=4 * 24 * 10, freq="15min", tz="UTC")
    verano = pd.date_range("2024-07-01", periods=4 * 24 * 10, freq="15min", tz="UTC")

    assert list(invierno[MARKET_WEEK.closes(invierno)]) == [
        pd.Timestamp("2024-01-05 21:45", tz="UTC")
    ]
    assert list(verano[MARKET_WEEK.closes(verano)]) == [
        pd.Timestamp("2024-07-05 20:45", tz="UTC")
    ]


def test_el_cierre_semanal_cae_en_la_ultima_vela_que_haya_antes_del_corte() -> None:
    """Con el hueco del fin de semana no hay vela a las 17:00: manda la anterior."""
    semana = pd.date_range("2024-01-01", periods=4 * 24 * 10, freq="15min", tz="UTC")
    con_hueco = semana[
        (semana < pd.Timestamp("2024-01-05 21:00", tz="UTC"))
        | (semana >= pd.Timestamp("2024-01-07 23:00", tz="UTC"))
    ]

    assert list(con_hueco[MARKET_WEEK.closes(con_hueco)]) == [
        pd.Timestamp("2024-01-05 20:45", tz="UTC")
    ]


def test_la_ultima_vela_del_historico_no_es_un_cierre_semanal() -> None:
    """Ahí no ha cerrado el mercado: se han acabado los datos."""
    hasta_el_viernes = pd.date_range(
        "2024-01-01", "2024-01-05 21:45", freq="15min", tz="UTC"
    )

    assert not MARKET_WEEK.closes(hasta_el_viernes).any()
    assert not MARKET_WEEK.closes(pd.DatetimeIndex([], tz="UTC")).any()


def test_el_cierre_semanal_viaja_con_la_corrida(trades: EntriesRun) -> None:
    """El explorador lo escribe: sin la hora, un cierre del viernes no se audita."""
    assert trades.market_week == MARKET_WEEK
    assert trades.market_week.label == "viernes a las 17:00 de America/New_York"
    assert MarketWeek(close=MARKET_WEEK.close).timezone == "America/New_York"


# --- Lo que no toca ---------------------------------------------------------


def test_las_entradas_no_mueven_ni_un_impulso_ni_una_zona_ni_la_cascada(
    run: ImpulseRun, zones: ZonesRun, trades: EntriesRun
) -> None:
    """Se calculan DESPUÉS, sobre el resultado ya cerrado: no hay nada que mover."""
    otra = _run()
    otras_zonas = detect_zones(otra)

    assert trades.enabled
    for timeframe in (H4, H1):
        assert len(run.analyses[timeframe].impulses) == len(otra.analyses[timeframe].impulses)
        assert len(zones.per_timeframe[timeframe].items) == len(
            otras_zonas.per_timeframe[timeframe].items
        )
    assert detect_cascade(run, zones).counts() == detect_cascade(otra, otras_zonas).counts()
