"""El rechazo en H4 de la fase 3.2, con los esperados escritos a mano.

Los casos 1, 2, 3, 4, 7 y 11 del día sintético viven aquí: son los que se pueden
comprobar **exactos** sobre una zona escrita a mano, sin depender de dónde
coloque el detector el ID.

Lo que se fija, además de las dos formas:

- que la **dirección** sale del tipo de zona y no del sesgo del ID: el UL
  rechazado opera EN CONTRA y el OB rechazado a favor;
- que las dos formas se evalúan **siempre**, aunque la primera ya rechace;
- que cerrar más allá del borde exterior NO es un rechazo, porque eso ya tiene
  nombre: es la rotura de la fase 2.1;
- y que todo lo anterior se mantiene reflejado, sin escribir una sola vela
  bajista a mano.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from chronos.application.entries.synthetic_run import frame_of
from chronos.domain.entries.enums import RejectionForm
from chronos.domain.entries.h4_rejection import (
    evaluate_rejection,
    first_rejection,
    rejection_direction,
    rejects_by_close,
    rejects_by_turtle_soup,
)
from chronos.domain.entries.synthetic_entries import (
    H4_NO_REJECTION_DOWN,
    H4_NO_REJECTION_UP,
    H4_REJECT_A_DOWN,
    H4_REJECT_A_UP,
    H4_REJECT_B_DOWN,
    H4_REJECT_B_UP,
    H4_REJECT_BOTH_DOWN,
    H4_REJECT_BOTH_UP,
    H4_REJECT_OB_A_DOWN,
    H4_REJECT_OB_A_UP,
    H4_REJECT_ORDER_DOWN,
    H4_REJECT_ORDER_UP,
    SYNTHETIC_ENTRY_START,
    reference_zone,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import CandleSeries, Zone, ZoneKind

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA
A = RejectionForm.A_CIERRE_FUERA
B = RejectionForm.B_TURTLE_SOUP


def series(candles: tuple[tuple[float, float, float, float], ...]) -> CandleSeries:
    return CandleSeries.of(frame_of(candles, SYNTHETIC_ENTRY_START, "4h"))


def scan(
    candles: tuple[tuple[float, float, float, float], ...], zone: Zone
) -> object | None:
    bars = series(candles)
    return first_rejection(
        bars, dict.fromkeys(range(len(bars)), zone), first=0, through=len(bars) - 1
    )


# --- La dirección: es la que decide el lado de la operación -------------------


def test_el_ul_se_rechaza_en_contra_del_id() -> None:
    """⚠️ La primera operación del proyecto que va contra el sesgo de H4.

    El UL se atraviesa a favor del ID, así que rechazarlo es irse al otro lado.
    En un ID alcista el rechazo del techo es una VENTA.
    """
    assert rejection_direction(reference_zone(ZoneKind.LAST, UP)) is DOWN
    assert rejection_direction(reference_zone(ZoneKind.LAST, DOWN)) is UP


def test_el_ob_se_rechaza_a_favor_del_id() -> None:
    """El OB se atraviesa en contra, así que rechazarlo es seguir con el ID."""
    assert rejection_direction(reference_zone(ZoneKind.ORDER_BLOCK, UP)) is UP
    assert rejection_direction(reference_zone(ZoneKind.ORDER_BLOCK, DOWN)) is DOWN


# --- Caso 1: el contacto no es un desenlace ----------------------------------


def test_tocar_la_zona_y_sostenerla_no_rechaza_nada() -> None:
    """Caso 1 del §5: es exactamente lo que la fase 3.1 sí operaba."""
    assert scan(H4_NO_REJECTION_UP, reference_zone(ZoneKind.LAST, UP)) is None


def test_el_espejo_del_caso_1() -> None:
    assert scan(H4_NO_REJECTION_DOWN, reference_zone(ZoneKind.LAST, DOWN)) is None


# --- Caso 2: la forma A ------------------------------------------------------


def test_la_forma_a_es_entrar_en_la_zona_y_cerrar_fuera() -> None:
    """Caso 2 del §5. Una sola vela, y la operación va en contra del ID."""
    found = scan(H4_REJECT_A_UP, reference_zone(ZoneKind.LAST, UP))

    assert found is not None
    assert found.index == 1
    assert found.form is A
    assert found.forms == (A,)
    assert found.direction is DOWN


def test_el_espejo_del_caso_2() -> None:
    found = scan(H4_REJECT_A_DOWN, reference_zone(ZoneKind.LAST, DOWN))

    assert found is not None
    assert found.index == 1
    assert found.forms == (A,)
    assert found.direction is UP


def test_cerrar_dentro_de_la_zona_no_es_rechazarla() -> None:
    """La vela la sostuvo. Es la mitad del caso 1 escrita sobre la regla sola."""
    zone = reference_zone(ZoneKind.LAST, UP)

    assert not rejects_by_close(zone, high=2011.0, low=2005.0, close=2010.5)


def test_cerrar_mas_alla_del_borde_exterior_no_es_un_rechazo() -> None:
    """Eso ya tiene nombre en el proyecto: es la ROTURA de la fase 2.1.

    Si aquí marcara, el mismo cierre sería a la vez rotura a favor del ID y
    rechazo en contra, y las dos ramas producirían señales opuestas sobre la
    misma vela.
    """
    zone = reference_zone(ZoneKind.LAST, UP)

    assert not rejects_by_close(zone, high=2013.0, low=2009.0, close=2012.5)


def test_sin_tocar_la_zona_no_hay_rechazo_aunque_el_cierre_este_fuera() -> None:
    zone = reference_zone(ZoneKind.LAST, UP)

    assert not rejects_by_close(zone, high=2009.0, low=2005.0, close=2006.0)


# --- Caso 3: la forma B ------------------------------------------------------


def test_la_forma_b_es_un_turtle_soup_de_h4_en_la_zona() -> None:
    """Caso 3 del §5. El cierre se queda DENTRO, así que la forma A no marca."""
    found = scan(H4_REJECT_B_UP, reference_zone(ZoneKind.LAST, UP))

    assert found is not None
    assert found.index == 1
    assert found.forms == (B,)
    assert found.direction is DOWN
    assert found.extreme == pytest.approx(2011.50)


def test_el_espejo_del_caso_3() -> None:
    found = scan(H4_REJECT_B_DOWN, reference_zone(ZoneKind.LAST, DOWN))

    assert found is not None
    assert found.forms == (B,)
    assert found.direction is UP


def test_el_turtle_soup_de_h4_tiene_que_ocurrir_dentro_de_la_zona() -> None:
    """Es el único añadido de la forma B sobre el patrón de H1.

    Con una zona lejos del precio el patrón sigue existiendo y no rechaza nada:
    lo que el §2 pide es un rechazo DE LA ZONA.
    """
    lejos = replace(reference_zone(ZoneKind.LAST, UP), inner=3000.00, outer=3002.00)

    assert rejects_by_turtle_soup(series(H4_REJECT_B_UP), 1, lejos) is None


def test_la_primera_vela_de_la_serie_no_puede_ser_un_turtle_soup() -> None:
    """No tiene anterior. Es un hecho de la serie, no una regla que se elija."""
    assert rejects_by_turtle_soup(series(H4_REJECT_B_UP), 0, reference_zone(ZoneKind.LAST, UP)) is None


def test_pedir_una_vela_que_no_existe_es_un_error_de_indice() -> None:
    with pytest.raises(StructureError):
        rejects_by_turtle_soup(series(H4_REJECT_B_UP), 9, reference_zone(ZoneKind.LAST, UP))


# --- Caso 4: las dos formas --------------------------------------------------


def test_las_dos_formas_en_la_misma_vela_se_registran_las_dos() -> None:
    """Caso 4a. En la 3.0 se cortaba al primer acierto y hubo que reconstruirlo."""
    found = scan(H4_REJECT_BOTH_UP, reference_zone(ZoneKind.LAST, UP))

    assert found is not None
    assert found.forms == (A, B)
    assert found.both_forms
    assert found.form is A


def test_el_espejo_del_caso_4a() -> None:
    found = scan(H4_REJECT_BOTH_DOWN, reference_zone(ZoneKind.LAST, DOWN))

    assert found is not None
    assert found.forms == (A, B)


def test_con_las_dos_en_velas_distintas_gana_la_primera() -> None:
    """Caso 4b. La B existe dos velas después y no decide nada."""
    bars = series(H4_REJECT_ORDER_UP)
    zone = reference_zone(ZoneKind.LAST, UP)
    found = first_rejection(bars, dict.fromkeys(range(len(bars)), zone), first=0, through=3)
    tarde = evaluate_rejection(bars, 3, zone)

    assert found is not None
    assert found.index == 1
    assert found.forms == (A,)
    assert tarde is not None
    assert tarde.forms == (B,)


def test_el_espejo_del_caso_4b() -> None:
    bars = series(H4_REJECT_ORDER_DOWN)
    zone = reference_zone(ZoneKind.LAST, DOWN)
    found = first_rejection(bars, dict.fromkeys(range(len(bars)), zone), first=0, through=3)

    assert found is not None
    assert found.index == 1


def test_la_ventana_empieza_en_la_vela_del_contacto_incluida() -> None:
    """Una vela puede tocar la zona y rechazarla en el mismo cierre.

    Exigir esperar a la siguiente sería una espera que el enunciado no pide.
    """
    bars = series(H4_REJECT_A_UP)
    zone = reference_zone(ZoneKind.LAST, UP)

    assert first_rejection(bars, dict.fromkeys(range(len(bars)), zone), first=1, through=1) is not None
    assert first_rejection(bars, dict.fromkeys(range(len(bars)), zone), first=0, through=0) is None


# --- Caso 7: el OB rechazado -------------------------------------------------


def test_el_ob_rechazado_opera_a_favor_del_id() -> None:
    """Caso 7 del §5. Misma regla que el UL, resultado contrario."""
    found = scan(H4_REJECT_OB_A_UP, reference_zone(ZoneKind.ORDER_BLOCK, UP))

    assert found is not None
    assert found.index == 1
    assert found.forms == (A,)
    assert found.direction is UP


def test_el_espejo_del_caso_7() -> None:
    found = scan(H4_REJECT_OB_A_DOWN, reference_zone(ZoneKind.ORDER_BLOCK, DOWN))

    assert found is not None
    assert found.direction is DOWN
