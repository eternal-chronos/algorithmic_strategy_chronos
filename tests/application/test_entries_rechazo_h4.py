"""La fase 3.2 sobre la cascada entera: el contacto deja de ser señal.

Aquí se fijan los casos del día sintético que dependen de las cuatro
temporalidades y no de una zona escrita a mano —las formas del rechazo se prueban
en `tests/domain/entries/test_h4_rejection.py`—:

    1. contacto sin desenlace -> `contacto_sin_desenlace`, sin operación
    5. UL roto y retesteado ---> sigue operando a favor de la rotura
    8. OB roto ----------------> sin operación, la observación muere
    9. rechazo sin confirmación en H1 -> sin operación
   10. confirmación de H1 en la dirección contraria a la del rechazo
   11. todo lo anterior en bajista

Y lo que más importa de la fase: que la **dirección del rechazo se propaga** por
toda la cascada. Un UL rechazado en un ID alcista tiene que producir una VENTA,
con su stop arriba y su objetivo abajo, y eso se comprueba de punta a punta sobre
el ejecutor de M1.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries.cascade import CascadeRun, build_cascade
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.execution import M1Executor, trades_table
from chronos.application.entries.synthetic_run import build_synthetic, frame_of
from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    EntryMode,
    EntryTimeframe,
    GuardRail,
    Outcome,
    RejectionForm,
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
    H4_NO_RETEST_UP,
    H4_ORDER_BLOCK_BROKEN_DOWN,
    H4_ORDER_BLOCK_BROKEN_UP,
    H4_RETEST_DOWN,
    H4_RETEST_UP,
    M1_ENTRY_MIRRORED,
    M1_STOP_MIRRORED,
    M1_TARGET_DOWN,
    M1_TARGET_MIRRORED,
    SYNTHETIC_ENTRY_START,
)
from chronos.domain.instrument import InstrumentSpec
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA
Candles = tuple[tuple[float, float, float, float], ...]


def _observations(cascade: CascadeRun, zone: ZoneKind, outcome: Outcome) -> list:
    return [
        item
        for item in cascade.observations
        if item.zone is zone and item.outcome is outcome
    ]


def _rails(cascade: CascadeRun, zone: ZoneKind) -> set[str]:
    return {
        item.guard_rail.value
        for item in cascade.discarded
        if item.observation.zone is zone
    }


# --- La dirección: el UL rechazado va EN CONTRA del ID -----------------------


@pytest.mark.parametrize(
    ("candles", "id_direction", "trade_direction"),
    [(H4_RETEST_UP, UP, DOWN), (H4_RETEST_DOWN, DOWN, UP)],
)
def test_el_ul_rechazado_opera_en_contra_del_id(
    candles: Candles, id_direction: ImpulseDirection, trade_direction: ImpulseDirection
) -> None:
    """⚠️ La primera población del proyecto que va contra el sesgo de H4."""
    cascade = build_synthetic(candles).cascade
    rejected = _observations(cascade, ZoneKind.LAST, Outcome.RECHAZO)

    assert rejected
    for item in rejected:
        assert item.direction is id_direction
        assert item.direction_of_trade is trade_direction
        assert item.against_the_id


def test_el_rechazo_registra_la_forma_que_lo_disparo() -> None:
    """El §2 lo pide con todas las letras: en la 3.0 esa información se perdió."""
    cascade = build_synthetic(H4_RETEST_UP).cascade
    rejected = _observations(cascade, ZoneKind.LAST, Outcome.RECHAZO)

    assert rejected
    for item in rejected:
        assert item.rejection_form in tuple(RejectionForm)
        assert item.rejection_form in item.rejection_forms
        assert item.ts_rejection is not None


# --- Caso 5: la rotura y retesteo no se toca ---------------------------------


@pytest.mark.parametrize(
    ("candles", "direction"), [(H4_RETEST_UP, UP), (H4_RETEST_DOWN, DOWN)]
)
def test_la_rotura_con_retesteo_sigue_operando_a_favor_de_la_rotura(
    candles: Candles, direction: ImpulseDirection
) -> None:
    """La 3.2 no toca esta rama, y su dirección sigue siendo la del ID."""
    cascade = build_synthetic(candles).cascade
    retested = _observations(cascade, ZoneKind.LAST, Outcome.ROTURA_Y_RETESTEO)

    assert len(retested) == 1
    assert retested[0].direction_of_trade is direction
    assert not retested[0].against_the_id


def test_el_retesteo_es_lo_unico_que_llega_a_señal_en_la_serie_del_retesteo() -> None:
    """Caso 10: en la misma ventana hay confirmación de H1 en la dirección del ID.

    La rama de rechazo la busca en la CONTRARIA y no la ve, así que muere sin
    confirmar. No es un caso especial del código: la confirmación se busca en la
    dirección de la operación, y en la contraria no se busca nada.
    """
    cascade = build_synthetic(H4_RETEST_UP).cascade

    assert cascade.signals
    for signal in cascade.signals:
        assert signal.observation.outcome is Outcome.ROTURA_Y_RETESTEO
        assert signal.direction is UP
    assert GuardRail.SIN_CONFIRMACION_H1.value in _rails(cascade, ZoneKind.LAST)


# --- Caso 1: el contacto sin desenlace ---------------------------------------


@pytest.mark.parametrize(
    "candles", [H4_ORDER_BLOCK_BROKEN_UP, H4_ORDER_BLOCK_BROKEN_DOWN]
)
def test_el_contacto_sin_rechazo_muere_en_su_guardarrail(candles: Candles) -> None:
    """Caso 1 del §5: es exactamente la rama que la 3.1 operaba a ciegas."""
    cascade = build_synthetic(candles).cascade

    assert GuardRail.CONTACTO_SIN_DESENLACE.value in _rails(cascade, ZoneKind.ORDER_BLOCK)


# --- Caso 8: el OB roto ------------------------------------------------------


@pytest.mark.parametrize(
    "candles", [H4_ORDER_BLOCK_BROKEN_UP, H4_ORDER_BLOCK_BROKEN_DOWN]
)
def test_el_ob_roto_no_abre_ninguna_rama(candles: Candles) -> None:
    """El retesteo del OB está APARCADO a propósito: no se implementa."""
    cascade = build_synthetic(candles).cascade

    assert GuardRail.OB_ROTO.value in _rails(cascade, ZoneKind.ORDER_BLOCK)
    assert not _observations(cascade, ZoneKind.ORDER_BLOCK, Outcome.ROTURA_Y_RETESTEO)


# --- El embudo ---------------------------------------------------------------


def test_el_paso_del_rechazo_esta_en_el_embudo_y_es_cero_en_el_modo_de_regresion() -> None:
    """Con `v31_contacto` no se busca ningún rechazo, y el embudo lo dice.

    Poner ahí el número de contactos daría a entender que sí se buscó.
    """
    synthetic = build_synthetic(H4_NO_RETEST_UP)
    contacto = build_cascade(
        synthetic.run,
        synthetic.zones,
        synthetic.bars["M15"],
        EntriesConfig(
            enabled=True, allow_missing_ask=True, entry_mode=EntryMode.V31_CONTACTO
        ),
    )

    assert synthetic.cascade.funnel["rechazos_en_h4"] > 0
    assert contacto.funnel["rechazos_en_h4"] == 0
    assert contacto.funnel["zonas_tocadas"] == synthetic.cascade.funnel["zonas_tocadas"]


def test_el_modo_de_entrada_entra_en_el_hash_de_la_cascada() -> None:
    """Dos corridas que operan cosas distintas no pueden compartir identificador."""
    v32 = EntriesConfig(enabled=True)
    v31 = EntriesConfig(enabled=True, entry_mode=EntryMode.V31_CONTACTO)

    assert v32.fingerprint() != v31.fingerprint()


def test_el_modo_de_la_31_sigue_operando_por_contacto() -> None:
    """Es lo que el test de regresión necesita: la rama vieja, intacta."""
    synthetic = build_synthetic(H4_NO_RETEST_UP)
    contacto = build_cascade(
        synthetic.run,
        synthetic.zones,
        synthetic.bars["M15"],
        EntriesConfig(
            enabled=True, allow_missing_ask=True, entry_mode=EntryMode.V31_CONTACTO
        ),
    )
    respects = _observations(contacto, ZoneKind.LAST, Outcome.RESPETO)

    assert respects
    for item in respects:
        assert not item.against_the_id
        assert item.rejection_form is None


# --- La dirección llega hasta el stop, el objetivo y el ejecutor -------------


def _short_against_the_id(candles: Candles) -> Trade:
    """Una VENTA nacida de un UL rechazado en un ID ALCISTA, ejecutada sobre M1.

    La zona de entrada es [2000, 2010]: el stop va arriba, en 2010, el 1R son 10
    USD exactos y el objetivo cae 3,3 R por debajo de la entrada, en 1967.00
    clavado. Si la dirección de la operación se leyera del ID —alcista— el stop
    quedaría por debajo del precio y la operación se abriría del revés.
    """
    frame = frame_of(candles, SYNTHETIC_ENTRY_START, "1min")
    decision = (pd.Timestamp(SYNTHETIC_ENTRY_START) - pd.Timedelta(minutes=1)).to_pydatetime()
    zone = EntryZone(
        timeframe=EntryTimeframe.H1,
        inner=M1_ENTRY_MIRRORED,
        outer=M1_STOP_MIRRORED,
        index_defining=0,
        ts_defining=decision,
        index_confirmation=0,
        ts_confirmation=decision,
        id_num=1,
    )
    observation = Observation(
        timeframe="H4",
        id_num=1,
        direction=UP,
        zone=ZoneKind.LAST,
        zone_inner=2010.0,
        zone_outer=2012.0,
        index_contact=0,
        ts_contact=decision,
        ts_window_end=decision,
        daily=DailyContext.SIN_CONTEXTO,
        trade_direction=DOWN,
        index_rejection=0,
        ts_rejection=decision,
        rejection_form=RejectionForm.A_CIERRE_FUERA,
        rejection_forms=(RejectionForm.A_CIERRE_FUERA,),
    )
    signal = Signal(
        observation=observation,
        confirmation=Confirmation(
            kind=ConfirmationKind.TURTLE_SOUP, index=0, timestamp=decision
        ),
        entry_zone=zone,
        stop_options=((StopZone.H1, zone),),
        ts_decision=decision,
    )
    executor = M1Executor(
        frame,
        InstrumentSpec(symbol="SYNTH"),
        EntriesConfig(enabled=True, allow_missing_ask=True),
        has_ask=False,
    )
    return executor.execute(_cascade_of(signal)).trades[0]


def _cascade_of(signal: Signal) -> CascadeRun:
    return CascadeRun(
        enabled=True,
        config=EntriesConfig(enabled=True, allow_missing_ask=True),
        config_hash="",
        structure_hash="",
        signals=(signal,),
    )


def test_una_venta_contra_el_id_se_abre_como_venta() -> None:
    """De la observación al ejecutor, sin que ningún lado se quede sin invertir."""
    trade = _short_against_the_id(M1_TARGET_DOWN)

    assert not trade.is_long
    assert trade.direction is DOWN
    assert trade.entry_price == pytest.approx(M1_ENTRY_MIRRORED)
    assert trade.stop_price == pytest.approx(M1_STOP_MIRRORED)
    assert trade.stop_price > trade.entry_price
    assert trade.target_price == pytest.approx(M1_TARGET_MIRRORED)
    assert trade.target_price < trade.entry_price
    assert trade.risk_usd == pytest.approx(10.0)


def test_la_venta_contra_el_id_alcanza_su_objetivo_hacia_abajo() -> None:
    """El bruto es la constante del §3, y llega por el lado correcto."""
    trade = _short_against_the_id(M1_TARGET_DOWN)

    assert trade.outcome is TradeOutcome.OBJETIVO
    assert trade.gross_r == pytest.approx(TARGET_R)
    assert trade.net_r < trade.gross_r


def test_la_operacion_contra_el_id_sale_marcada_en_el_csv() -> None:
    """§6.4 — es una población nueva y el informe la aísla: tiene que ser filtrable."""
    table = trades_table((_short_against_the_id(M1_TARGET_DOWN),))
    row = table.iloc[0]

    assert bool(row["contra_id"])
    assert row["direccion"] == DOWN.value
    assert row["direccion_id_h4"] == UP.value
    assert row["rama"] == f"{ZoneKind.LAST.value} {Outcome.RECHAZO.value}"
    assert row["forma_rechazo"] == RejectionForm.A_CIERRE_FUERA.value
