"""Las dos vías de confirmación de la fase 3.1, con los esperados a mano.

Los casos del día sintético que no son del turtle soup —ése tiene su propio
fichero, en `tests/domain/entries/`—:

    5. ID de H1 con OB formado y el precio llega al OB .. confirma, entrada en el OB
    6. ID de H1 SIN OB formado ......................... NO confirma
    7. OB formado pero el precio NO llega .............. NO confirma
    8. las dos vías a la vez ........................... según CONFIRM_PRIORITY
    9. lo que la 3.0 confirmaba por rechazo ............ la observación MUERE

Los casos 5, 6 y 7 se comprueban sobre la regla —`order_block_reached`— con
zonas escritas a mano: es donde su esperado se puede escribir sin ambigüedad. El
5 y el 9 se comprueban ADEMÁS de punta a punta, sobre las series sintéticas que
recorren el motor entero.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from chronos.application.entries.cascade import (
    build_cascade,
    order_block_reached,
    take_via,
)
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.synthetic_run import build_synthetic
from chronos.application.structure.zones import ImpulseZones
from chronos.domain.entries.enums import (
    ConfirmationKind,
    ConfirmMode,
    ConfirmPriority,
    EntryMode,
    GuardRail,
)
from chronos.domain.entries.synthetic_entries import (
    H4_ORDER_BLOCK_BROKEN_UP,
    H4_RETEST_UP,
)
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.zones import Zone, ZoneKind

UP = ImpulseDirection.ALCISTA

BIRTH = datetime(2024, 3, 4, 8, 0, tzinfo=UTC)
BEFORE = pd.Timestamp("2024-03-04 07:00", tz="UTC")
AFTER = pd.Timestamp("2024-03-04 09:00", tz="UTC")


def _order_block() -> Zone:
    """Un OB alcista de [1990, 2000] que nace a las 08:00."""
    return Zone(
        kind=ZoneKind.ORDER_BLOCK,
        id_num=1,
        timeframe="H1",
        direction=UP,
        index_defining=3,
        ts_defining=BIRTH,
        defining_body=BodyDirection.BEARISH,
        inner=2000.00,
        outer=1990.00,
        ts_outer_known=BIRTH,
        ts_birth=BIRTH,
        index_confirmation=4,
        ts_confirmation=BIRTH,
    )


def _zones(block: Zone | None) -> ImpulseZones:
    return ImpulseZones(
        id_num=1,
        timeframe="H1",
        direction=UP,
        year=2024,
        last=Zone(
            kind=ZoneKind.LAST,
            id_num=1,
            timeframe="H1",
            direction=UP,
            index_defining=3,
            ts_defining=BIRTH,
            defining_body=BodyDirection.BULLISH,
            inner=2010.00,
            outer=2012.00,
            ts_outer_known=BIRTH,
            ts_birth=BIRTH,
        ),
        order_block=block,
        atr=5.0,
        anchor=1990.0,
        extreme=2012.0,
        index_constitution=3,
        index_end=None,
        ts_constitution=BIRTH,
        ts_end=None,
        exit_break=None,
    )


# --- Caso 5 ------------------------------------------------------------------


def test_con_ob_formado_y_el_precio_dentro_confirma() -> None:
    """La vela de H1 baja a 1995, dentro de [1990, 2000]: la vía 2 confirma."""
    reached = order_block_reached(_zones(_order_block()), AFTER, high=2005.0, low=1995.0)

    assert reached is not None
    assert (reached.low, reached.high) == (1990.00, 2000.00)


def test_tocar_el_borde_del_ob_ya_es_llegar() -> None:
    """Los bordes cuentan, igual que en el contacto con la zona de H4."""
    assert (
        order_block_reached(_zones(_order_block()), AFTER, high=2005.0, low=2000.0)
        is not None
    )


def test_la_entrada_de_la_via_2_va_en_ese_mismo_ob() -> None:
    """De punta a punta: el sintético confirma por OB y entra en el OB de H1.

    La zona de entrada la elige `_h1_entry_zone`, que busca el OB del ID de H1
    vigente en la confirmación. Que sea el MISMO que confirmó no es casualidad:
    las dos cosas leen el mismo ID vigente y el mismo nacimiento.
    """
    synthetic = build_synthetic(H4_RETEST_UP)
    cascade = build_cascade(
        synthetic.run,
        synthetic.zones,
        synthetic.bars["M15"],
        EntriesConfig(enabled=True, allow_missing_ask=True),
    )
    confirmed = [
        signal
        for signal in cascade.signals
        if signal.confirmation.kind is ConfirmationKind.OB_H1
    ]

    assert confirmed
    for signal in confirmed:
        assert signal.confirmation.available == (ConfirmationKind.OB_H1,)
        assert signal.confirmation.id_num is not None


# --- Caso 6 ------------------------------------------------------------------


def test_un_id_de_h1_sin_ob_formado_no_confirma_nada() -> None:
    """Es el cambio de fondo de la fase: el ID solo dejó de valer."""
    assert order_block_reached(_zones(None), AFTER, high=2005.0, low=1995.0) is None


def test_un_ob_que_todavia_no_ha_nacido_no_confirma() -> None:
    """Un OB que se confirma después no estaba ahí para colocar ninguna entrada."""
    assert (
        order_block_reached(_zones(_order_block()), BEFORE, high=2005.0, low=1995.0)
        is None
    )


def test_sin_id_de_h1_vigente_no_hay_via_2() -> None:
    assert order_block_reached(None, AFTER, high=2005.0, low=1995.0) is None


# --- Caso 7 ------------------------------------------------------------------


def test_con_el_ob_formado_pero_sin_llegar_el_precio_no_confirma() -> None:
    """La vela se queda entre 2001 y 2008: el OB existe y nadie lo toca."""
    assert (
        order_block_reached(_zones(_order_block()), AFTER, high=2008.0, low=2001.0)
        is None
    )


# --- Caso 8 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("priority", "esperada"),
    [
        (ConfirmPriority.TURTLE_PRIMERO, ConfirmationKind.TURTLE_SOUP),
        (ConfirmPriority.OB_PRIMERO, ConfirmationKind.OB_H1),
    ],
)
def test_con_las_dos_vias_manda_confirm_priority(
    priority: ConfirmPriority, esperada: ConfirmationKind
) -> None:
    ambas = (ConfirmationKind.TURTLE_SOUP, ConfirmationKind.OB_H1)

    assert take_via(ambas, priority) is esperada


@pytest.mark.parametrize("priority", list(ConfirmPriority))
def test_con_una_sola_via_el_orden_no_pinta_nada(priority: ConfirmPriority) -> None:
    assert take_via((ConfirmationKind.TURTLE_SOUP,), priority) is ConfirmationKind.TURTLE_SOUP
    assert take_via((ConfirmationKind.OB_H1,), priority) is ConfirmationKind.OB_H1


def test_sin_ninguna_via_disponible_no_se_inventa_una() -> None:
    with pytest.raises(DomainError):
        take_via((), ConfirmPriority.TURTLE_PRIMERO)


# --- Caso 9 ------------------------------------------------------------------


def _outcomes(candles: tuple[tuple[float, float, float, float], ...], mode: ConfirmMode) -> dict:
    synthetic = build_synthetic(candles)
    cascade = build_cascade(
        synthetic.run,
        synthetic.zones,
        synthetic.bars["M15"],
        # ⚠️ `v31_contacto`: lo que se compara aquí es la 3.0 contra la 3.1, y las
        # dos operaban POR CONTACTO. Con el modo de la 3.2 no habría nada que
        # comparar, porque el contacto ya no abre ninguna operación.
        EntriesConfig(
            enabled=True,
            allow_missing_ask=True,
            confirm_mode=mode,
            entry_mode=EntryMode.V31_CONTACTO,
        ),
    )
    found: dict[tuple, str] = {}
    for signal in cascade.signals:
        observation = signal.observation
        found[
            (observation.id_num, observation.zone.value, observation.index_contact)
        ] = signal.confirmation.kind.value
    for item in cascade.discarded:
        observation = item.observation
        key = (observation.id_num, observation.zone.value, observation.index_contact)
        # Una observación que confirmó y murió después —sin OB con el que colocar
        # la entrada— sigue siendo una observación CONFIRMADA: lo que se compara
        # entre las dos fases es qué confirma, no qué llega a operación.
        found.setdefault(
            key,
            item.confirmation.kind.value
            if item.confirmation is not None
            else f"MUERE · {item.guard_rail.value}",
        )
    return found


def test_lo_que_la_30_confirmaba_por_rechazo_ahora_muere() -> None:
    """El OB del ID#2 de la serie del OB roto: la 3.0 lo tomaba, la 3.1 no.

    Es el caso 9 del día sintético, y es el cambio que más población mueve: en el
    histórico real el 95,5 % de las confirmaciones de la 3.0 venían por rechazo
    en unión.
    """
    antes = _outcomes(H4_ORDER_BLOCK_BROKEN_UP, ConfirmMode.V30_TRES_VIAS)
    ahora = _outcomes(H4_ORDER_BLOCK_BROKEN_UP, ConfirmMode.V31_DOS_VIAS)
    clave = (2, ZoneKind.ORDER_BLOCK.value, 8)

    assert antes[clave] == ConfirmationKind.RECHAZO.value
    assert ahora[clave] == f"MUERE · {GuardRail.SIN_CONFIRMACION_H1.value}"


def test_las_dos_corridas_ven_las_mismas_observaciones() -> None:
    """Lo único que cambia es qué confirma: las observaciones son las mismas.

    Si no lo fueran, la comparación entre las dos fases estaría emparejando
    cosas distintas y todas las tablas del informe mentirían.
    """
    antes = _outcomes(H4_ORDER_BLOCK_BROKEN_UP, ConfirmMode.V30_TRES_VIAS)
    ahora = _outcomes(H4_ORDER_BLOCK_BROKEN_UP, ConfirmMode.V31_DOS_VIAS)

    assert set(antes) == set(ahora)


# --- Las columnas informativas ----------------------------------------------


def test_r1_r2_y_r3_se_siguen_calculando_aunque_ya_no_confirmen() -> None:
    """Ninguna regla las lee y todas viajan al CSV: es lo que pide la fase."""
    synthetic = build_synthetic(H4_RETEST_UP)
    cascade = build_cascade(
        synthetic.run,
        synthetic.zones,
        synthetic.bars["M15"],
        EntriesConfig(enabled=True, allow_missing_ask=True),
    )

    assert cascade.signals
    for signal in cascade.signals:
        assert signal.confirmation.kind is not ConfirmationKind.RECHAZO
        assert set(signal.rejection_marks) == {
            "R1_mecha_en_zona_cierre_fuera",
            "R2_mecha_dominante_p60",
            "R2_mecha_dominante_p75",
            "R2_mecha_dominante_p90",
            "R3_cierre_en_extremo",
        }
