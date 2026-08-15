"""Ejecución realista sobre M1 (§4) y los casos 1, 2 y 3 del §8.

La zona de entrada es [2000, 1990], así que el stop va en 1990, el 1R son **10
USD exactos** y el objetivo cae en **2033.00** clavado. Con esos números el
esperado de cada caso se escribe sin margen de interpretación, que es justo lo que
pide el §8.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from chronos.application.entries.config import EntriesConfig, EntryCosts
from chronos.application.entries.execution import M1Executor, trades_table
from chronos.application.entries.synthetic_run import frame_of
from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    EntryTimeframe,
    GuardRail,
    StopZone,
    TradeOutcome,
)
from chronos.domain.entries.signal import (
    TARGET_R,
    Confirmation,
    EntryZone,
    Observation,
    Signal,
    Trade,
)
from chronos.domain.entries.synthetic_entries import (
    M1_BOTH_UP,
    M1_ENTRY,
    M1_STOP,
    M1_STOP_UP,
    M1_TARGET,
    M1_TARGET_UP,
    SYNTHETIC_ENTRY_START,
)
from chronos.domain.errors import DomainError
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

UP = ImpulseDirection.ALCISTA
#: La decisión cae ANTES de la primera vela: la ejecución tiene que ir a su open.
DECISION = (pd.Timestamp(SYNTHETIC_ENTRY_START) - pd.Timedelta(minutes=1)).to_pydatetime()


class _Cascade:
    """Lo mínimo que el ejecutor necesita: una lista de señales."""

    def __init__(self, signals: tuple[Signal, ...]) -> None:
        self.signals = signals


def signal(*, inner: float = 2000.0, outer: float = 1990.0, long: bool = True) -> Signal:
    zone = EntryZone(
        timeframe=EntryTimeframe.H1,
        inner=inner,
        outer=outer,
        index_defining=0,
        ts_defining=DECISION,
        index_confirmation=0,
        ts_confirmation=DECISION,
        id_num=1,
    )
    direction = UP if long else UP.opposite()
    observation = Observation(
        timeframe="H4",
        id_num=1,
        direction=direction,
        zone=ZoneKind.LAST,
        zone_inner=inner,
        zone_outer=outer,
        index_contact=0,
        ts_contact=DECISION,
        ts_window_end=DECISION,
        daily=DailyContext.SIN_CONTEXTO,
    )
    return Signal(
        observation=observation,
        confirmation=Confirmation(kind=ConfirmationKind.RECHAZO, index=0, timestamp=DECISION),
        entry_zone=zone,
        stop_options=((StopZone.H1, zone),),
        ts_decision=DECISION,
    )


def execute(
    candles: tuple[tuple[float, float, float, float], ...],
    *,
    config: EntriesConfig | None = None,
    item: Signal | None = None,
    has_ask: bool = False,
) -> Trade:
    executor = M1Executor(
        frame_of(candles, SYNTHETIC_ENTRY_START, "1min"),
        InstrumentSpec(symbol="SYNTH"),
        config or EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=has_ask,
    )
    result = executor.execute(_Cascade((item or signal(),)))
    assert result.trades, "la señal no llegó a operación"
    return result.trades[0]


# --- Casos 1, 2 y 3 del §8 ---------------------------------------------------


def test_caso_1_objetivo_alcanzado() -> None:
    trade = execute(M1_TARGET_UP)

    assert trade.outcome is TradeOutcome.OBJETIVO
    assert trade.gross_r == pytest.approx(TARGET_R)
    assert trade.exit_price == pytest.approx(M1_TARGET)


def test_caso_2_el_stop_salta_primero() -> None:
    trade = execute(M1_STOP_UP)

    assert trade.outcome is TradeOutcome.STOP
    assert trade.gross_r == pytest.approx(-1.0)
    assert trade.exit_price == pytest.approx(M1_STOP)


def test_caso_3_con_los_dos_en_la_misma_barra_gana_el_stop() -> None:
    """§4 — regla intra-barra conservadora. Es una convención, no un dato.

    Se marca aparte para poder contar cuántas operaciones dependen de ella: si
    fueran muchas, el resultado estaría diciendo más sobre la convención que
    sobre la estrategia.
    """
    trade = execute(M1_BOTH_UP)

    assert trade.outcome is TradeOutcome.STOP_MISMA_BARRA
    assert trade.gross_r == pytest.approx(-1.0)
    assert trade.exit_price == pytest.approx(M1_STOP)


# --- El 1R, el objetivo y la entrada -----------------------------------------


def test_la_entrada_va_al_open_de_la_barra_m1_siguiente_a_la_decision() -> None:
    trade = execute(M1_TARGET_UP)

    assert trade.index_entry_m1 == 0
    assert trade.entry_price == pytest.approx(M1_ENTRY)
    assert trade.ts_entry == pd.Timestamp(SYNTHETIC_ENTRY_START).to_pydatetime()


def test_el_stop_va_en_el_borde_exterior_sin_holgura() -> None:
    """Versión 1 del stop, PRE-REGISTRADA. Cualquier colchón sería un parámetro
    que el propietario no ha decidido."""
    trade = execute(M1_TARGET_UP)

    assert trade.stop_price == pytest.approx(M1_STOP)
    assert trade.risk_usd == pytest.approx(10.0)


def test_el_objetivo_es_33_r_fijo() -> None:
    trade = execute(M1_TARGET_UP)
    assert trade.target_price == pytest.approx(M1_TARGET)
    assert (trade.target_price - trade.entry_price) / trade.risk_usd == pytest.approx(3.3)


def test_el_1r_va_en_las_tres_unidades() -> None:
    """§3 — USD, ATR y % del precio. Sin ATR de la temporalidad sale `NaN`, no cero."""
    trade = execute(M1_TARGET_UP)

    assert trade.risk_usd == pytest.approx(10.0)
    assert trade.risk_pct_price == pytest.approx(10.0 / 2000.0)
    assert pd.isna(trade.risk_atr)


# --- Los costes (§4) ---------------------------------------------------------


def test_el_neto_siempre_es_peor_que_el_bruto() -> None:
    for candles in (M1_TARGET_UP, M1_STOP_UP, M1_BOTH_UP):
        trade = execute(candles)
        assert trade.net_r < trade.gross_r
        assert trade.cost_r > 0


def test_el_coste_en_r_no_depende_del_riesgo_por_operacion() -> None:
    """Todos los costes escalan con el lotaje y el lotaje con el riesgo.

    Es lo que hace que la métrica en R sea invariante al sizing, que es
    exactamente lo que busca el sizing de investigación continuo del §4.
    """
    base = EntriesConfig(enabled=True, allow_missing_ask=True)
    small = execute(M1_TARGET_UP, config=replace(base, risk_per_trade_usd=100.0))
    large = execute(M1_TARGET_UP, config=replace(base, risk_per_trade_usd=50_000.0))

    assert small.cost_r == pytest.approx(large.cost_r)
    assert small.net_r == pytest.approx(large.net_r)
    assert small.lots != pytest.approx(large.lots)


def test_sin_costes_el_neto_es_el_bruto() -> None:
    free = EntriesConfig(
        enabled=True,
        allow_missing_ask=True,
        costs=EntryCosts(
            spread_points=0.0,
            slippage_points=0.0,
            commission_per_lot_per_side=0.0,
            swap_long_points=0.0,
            swap_short_points=0.0,
        ),
    )
    trade = execute(M1_TARGET_UP, config=free)

    assert trade.net_r == pytest.approx(trade.gross_r)
    assert trade.cost_r == pytest.approx(0.0)


def test_el_sizing_es_continuo_y_sin_lote_minimo() -> None:
    """Sin normalizar al lot step: con lotes reales la población de señales
    dependería del camino del equity, y el §4 pide que no dependa de nada."""
    tiny = signal(inner=2000.0, outer=1990.0)
    trade = execute(M1_TARGET_UP, item=tiny)
    instrument = InstrumentSpec(symbol="SYNTH")

    assert trade.lots == pytest.approx(1000.0 / (10.0 * instrument.contract_size))
    assert trade.lots < instrument.min_lot or trade.lots % instrument.lot_step != 0


# --- El guardarraíl del ask (§4) ---------------------------------------------


def test_sin_fichero_de_ask_y_sin_autorizacion_el_ejecutor_se_niega() -> None:
    with pytest.raises(DomainError, match="PARADA"):
        M1Executor(
            frame_of(M1_TARGET_UP, SYNTHETIC_ENTRY_START, "1min"),
            InstrumentSpec(symbol="SYNTH"),
            EntriesConfig(enabled=True, allow_missing_ask=False),
            has_ask=False,
        )


def test_la_asuncion_del_bid_se_declara_en_portada() -> None:
    executor = M1Executor(
        frame_of(M1_TARGET_UP, SYNTHETIC_ENTRY_START, "1min"),
        InstrumentSpec(symbol="SYNTH"),
        EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=False,
    )
    assert "NO HAY FICHERO DE ASK" in executor.price_side_note
    assert "bid" in executor.price_side_note


def test_con_los_dos_lados_la_portada_lo_dice_tambien() -> None:
    executor = M1Executor(
        frame_of(M1_TARGET_UP, SYNTHETIC_ENTRY_START, "1min"),
        InstrumentSpec(symbol="SYNTH"),
        EntriesConfig(enabled=True),
        has_ask=True,
    )
    assert "Longs al ask" in executor.price_side_note


# --- Los que no llegan a operación -------------------------------------------


def test_un_stop_del_lado_equivocado_se_descarta_en_vez_de_moverse() -> None:
    """Mover el stop sería inventar una regla que el propietario no ha dado."""
    executor = M1Executor(
        frame_of(M1_TARGET_UP, SYNTHETIC_ENTRY_START, "1min"),
        InstrumentSpec(symbol="SYNTH"),
        EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=False,
    )
    result = executor.execute(_Cascade((signal(inner=2000.0, outer=2010.0),)))

    assert not result.trades
    assert result.discarded[0].guard_rail is GuardRail.STOP_INVALIDO


def test_una_operacion_sin_desenlace_se_declara_abierta() -> None:
    """El histórico se acabó con la operación viva. No cuenta en la expectativa."""
    trade = execute(
        (
            (2000.00, 2001.00, 1999.00, 2000.50),
            (2000.50, 2001.00, 1999.50, 2000.00),
        )
    )
    assert trade.outcome is TradeOutcome.ABIERTA
    assert trade.outcome.is_resolved is False
    assert trade.ts_exit is None


# --- La tabla que alimenta todos los desgloses -------------------------------


def test_la_tabla_trae_todas_las_columnas_que_el_5_desglosa() -> None:
    trade = execute(M1_TARGET_UP)
    table = trades_table((trade,))

    for column in (
        "contexto_diario",
        "zona_h4",
        "desenlace_zona",
        "entrada_en",
        "stop_en",
        "direccion",
        "anio",
        "confirmacion",
        "bruto_r",
        "neto_r",
        "coste_r",
        "r_usd",
        "r_atr",
        "r_pct_precio",
    ):
        assert column in table.columns
