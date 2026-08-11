"""Clasificación de contactos con los límites del ID (sección D).

Los casos están escritos a mano con números redondos: un toque de mecha, un
cierre justo en el nivel, una rotura real y el único camino por el que puede
aparecer una rotura fallida durante la vigencia de un ID.

Nada de esto entra en la detección de impulsos: son medidas.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.structure.contacts import classify_contacts, touches
from chronos.domain.structure.enums import ContactKind, ContactSide
from chronos.domain.structure.errors import StructureError

UPPER, LOWER = 100.0, 90.0


def series(bars: list[tuple[float, float, float]]) -> dict[str, np.ndarray]:
    """Cada barra es (high, low, close)."""
    array = np.array(bars, dtype=float)
    return {"high": array[:, 0], "low": array[:, 1], "close": array[:, 2]}


def classify(
    bars: list[tuple[float, float, float]], *, broke_at: int | None = None, first: int = 0
):
    return classify_contacts(
        **series(bars), upper=UPPER, lower=LOWER, first=first, last=len(bars) - 1,
        broke_at=broke_at,
    )


# --- Las tres categorías ----------------------------------------------------


def test_la_mecha_toca_arriba_y_la_vela_cierra_dentro() -> None:
    resultado = classify([(100.5, 95.0, 99.0)])
    assert len(resultado.contacts) == 1
    contacto = resultado.contacts[0]
    assert contacto.kind is ContactKind.TOQUE_MECHA
    assert contacto.side is ContactSide.SUPERIOR
    assert contacto.level == UPPER


def test_la_mecha_toca_abajo_y_la_vela_cierra_dentro() -> None:
    contacto = classify([(98.0, 89.5, 92.0)]).contacts[0]
    assert contacto.kind is ContactKind.TOQUE_MECHA
    assert contacto.side is ContactSide.INFERIOR


def test_una_vela_dentro_del_rango_no_produce_ningun_contacto() -> None:
    assert classify([(99.0, 91.0, 95.0)]).contacts == ()


def test_cerrar_justo_en_el_nivel_es_cerrar_dentro() -> None:
    """La misma desigualdad estricta que usa la rotura: tocar no es romper."""
    contacto = classify([(100.0, 95.0, 100.0)]).contacts[0]
    assert contacto.kind is ContactKind.TOQUE_MECHA


def test_la_rotura_real_es_la_barra_que_el_detector_dio_por_rota() -> None:
    resultado = classify([(99.0, 95.0, 97.0), (101.0, 96.0, 100.5)], broke_at=1)
    tipos = [contacto.kind for contacto in resultado.contacts]
    assert tipos == [ContactKind.ROTURA_REAL]
    assert resultado.contacts[0].index == 1


def test_la_rotura_fallida_necesita_que_la_siguiente_vuelva_dentro() -> None:
    """Sólo la da un doji con D1: la barra 0 cierra fuera y el ID sigue vivo."""
    resultado = classify([(101.0, 96.0, 100.5), (99.0, 95.0, 97.0)], broke_at=None)
    assert [contacto.kind for contacto in resultado.contacts] == [ContactKind.ROTURA_FALLIDA]
    assert resultado.unclassified == 0


def test_cerrar_fuera_sin_volver_y_sin_rotura_no_se_clasifica() -> None:
    """No se le inventa categoría: se cuenta aparte y el informe lo declara."""
    resultado = classify([(101.0, 96.0, 100.5), (102.0, 99.0, 101.0)], broke_at=None)
    assert resultado.contacts == ()
    assert resultado.unclassified == 2


# --- Recuento para la firma -------------------------------------------------


def test_la_firma_cuenta_mechas_y_fallidas_pero_no_la_rotura_real() -> None:
    resultado = classify(
        [
            (100.5, 95.0, 99.0),   # mecha arriba
            (98.0, 89.5, 92.0),    # mecha abajo
            (100.2, 96.0, 98.0),   # mecha arriba
            (97.0, 89.0, 91.0),    # mecha abajo
            (101.0, 97.0, 100.9),  # ROTURA_REAL arriba
        ],
        broke_at=4,
    )
    assert touches(resultado, ContactSide.SUPERIOR) == 2
    assert touches(resultado, ContactSide.INFERIOR) == 2
    assert len(resultado.contacts) == 5


def test_una_barra_puede_tocar_los_dos_limites() -> None:
    resultado = classify([(101.0, 89.0, 95.0)])
    assert {contacto.side for contacto in resultado.contacts} == {
        ContactSide.SUPERIOR,
        ContactSide.INFERIOR,
    }
    assert all(contacto.kind is ContactKind.TOQUE_MECHA for contacto in resultado.contacts)


# --- Ventana y bordes -------------------------------------------------------


def test_las_barras_anteriores_al_tramo_no_se_miran() -> None:
    """El rango nace en el cierre de la constitución: antes no hay qué tocar."""
    bars = [(101.0, 95.0, 99.0), (99.0, 91.0, 95.0)]
    assert classify(bars, first=1).contacts == ()


def test_un_tramo_vacio_no_da_contactos() -> None:
    resultado = classify_contacts(
        **series([(99.0, 91.0, 95.0)]), upper=UPPER, lower=LOWER, first=1, last=0
    )
    assert resultado.contacts == ()


def test_los_limites_cruzados_son_un_error() -> None:
    with pytest.raises(StructureError, match="cruzados"):
        classify_contacts(
            **series([(99.0, 91.0, 95.0)]), upper=LOWER, lower=UPPER, first=0, last=0
        )


def test_un_tramo_fuera_de_la_serie_es_un_error() -> None:
    with pytest.raises(StructureError, match="fuera de la serie"):
        classify_contacts(
            **series([(99.0, 91.0, 95.0)]), upper=UPPER, lower=LOWER, first=0, last=5
        )
