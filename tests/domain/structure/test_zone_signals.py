"""Las tres señales de zona, con casos escritos a mano (números redondos).

Toque del OB, rechazo del UL y rotura del UL. Nada de esto entra en la detección
ni en la regla de rotura: son marcas para el dibujo, y el test de capas comprueba
aparte que ningún módulo del motor las importa.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zone_signals import (
    ZoneSignalKind,
    classify_zone_signals,
)
from chronos.domain.structure.zones import Zone, ZoneKind

WHEN = datetime(2020, 1, 1, tzinfo=UTC)


def zone(kind: ZoneKind, *, inner: float, outer: float, direction: str = "alcista") -> Zone:
    return Zone(
        kind=kind,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection(direction),
        index_defining=0,
        ts_defining=WHEN,
        defining_body=BodyDirection.BULLISH,
        inner=inner,
        outer=outer,
        ts_outer_known=WHEN,
        ts_birth=WHEN,
    )


def classify(bars: list[tuple[float, float, float]], target: Zone, *, first: int = 0):
    """Cada barra es (high, low, close)."""
    array = np.array(bars, dtype=float)
    return classify_zone_signals(
        high=array[:, 0],
        low=array[:, 1],
        close=array[:, 2],
        zone=target,
        first=first,
        last=len(bars) - 1,
    )


#: UL de un ID alcista: del techo del cuerpo (100) a la punta de la mecha (102).
UL_UP = zone(ZoneKind.LAST, inner=100.0, outer=102.0)
#: OB de un ID alcista: la vela del ancla entera, de 92 (exterior) a 95 (interior).
OB_UP = zone(ZoneKind.ORDER_BLOCK, inner=95.0, outer=92.0)


# --- Toque del OB -----------------------------------------------------------


def test_la_mecha_que_entra_en_el_ob_es_un_toque() -> None:
    señales = classify([(98.0, 94.0, 97.0)], OB_UP)

    assert [item.kind for item in señales] == [ZoneSignalKind.TOQUE_OB]
    assert señales[0].zone is ZoneKind.ORDER_BLOCK
    assert señales[0].reach == pytest.approx(94.0)
    assert señales[0].touch == pytest.approx(94.0)
    assert señales[0].inside is False
    assert señales[0].level == pytest.approx(95.0), "el borde que encuentra primero"


def test_rozar_el_borde_del_ob_ya_es_tocarlo() -> None:
    """Bordes incluidos, como `Zone.contains`: llegar al borde es llegar."""
    assert classify([(99.0, 95.0, 98.0)], OB_UP)[0].kind is ZoneSignalKind.TOQUE_OB
    assert classify([(99.0, 95.01, 98.0)], OB_UP) == ()


def test_atravesar_el_ob_entero_sigue_siendo_un_toque() -> None:
    """No se inventa una rotura del OB: ésa es la rotura en contra que ya se marca."""
    señal = classify([(98.0, 90.0, 91.0)], OB_UP)[0]

    assert señal.kind is ZoneSignalKind.TOQUE_OB
    assert señal.reach == pytest.approx(90.0)
    # El marcador se planta en la zona, no colgando por debajo de ella.
    assert señal.touch == pytest.approx(92.0)


def test_los_toques_se_numeran_dentro_del_id() -> None:
    señales = classify([(98.0, 94.0, 97.0), (99.0, 97.0, 98.0), (98.0, 93.0, 96.0)], OB_UP)

    assert [item.ordinal for item in señales] == [1, 2]
    assert [item.index for item in señales] == [0, 2]


# --- Rechazo y rotura del UL ------------------------------------------------


def test_llegar_al_ul_y_cerrar_por_debajo_es_un_rechazo() -> None:
    señal = classify([(101.5, 98.0, 99.5)], UL_UP)[0]

    assert señal.kind is ZoneSignalKind.RECHAZO_UL
    assert señal.reach == pytest.approx(101.5)
    assert señal.inside is False


def test_cerrar_dentro_del_ul_tambien_es_un_rechazo() -> None:
    """Es la rotura evitada de la fase 2.1: entró en la zona y no la atravesó."""
    señal = classify([(101.5, 98.0, 101.0)], UL_UP)[0]

    assert señal.kind is ZoneSignalKind.RECHAZO_UL
    assert señal.inside is True


def test_cerrar_justo_en_el_borde_exterior_no_rompe() -> None:
    """La misma desigualdad estricta de la rotura: cerrar en el nivel es cerrar dentro."""
    assert classify([(102.5, 98.0, 102.0)], UL_UP)[0].kind is ZoneSignalKind.RECHAZO_UL


def test_cerrar_mas_alla_del_borde_exterior_es_la_rotura() -> None:
    señal = classify([(103.0, 98.0, 102.5)], UL_UP)[0]

    assert señal.kind is ZoneSignalKind.ROTURA_UL
    assert señal.level == pytest.approx(102.0), "en la rotura manda el borde exterior"
    assert señal.close == pytest.approx(102.5)


def test_una_vela_que_rompe_no_rechaza() -> None:
    assert len(classify([(103.0, 98.0, 102.5)], UL_UP)) == 1


def test_saltarse_la_zona_entera_de_un_hueco_tambien_es_romperla() -> None:
    """La vela abre por encima del UL y ni lo roza: romperlo es cerrar más allá."""
    señal = classify([(104.0, 103.0, 103.5)], UL_UP)[0]

    assert señal.kind is ZoneSignalKind.ROTURA_UL


def test_una_vela_que_no_llega_al_ul_no_produce_nada() -> None:
    assert classify([(99.0, 95.0, 98.0)], UL_UP) == ()


def test_el_ul_de_altura_cero_se_comporta_como_la_linea() -> None:
    """Los dos bordes en el mismo precio: tocarlo es rechazarlo, pasarlo es romperlo."""
    plano = zone(ZoneKind.LAST, inner=100.0, outer=100.0)

    assert classify([(100.0, 98.0, 99.0)], plano)[0].kind is ZoneSignalKind.RECHAZO_UL
    assert classify([(101.0, 98.0, 100.5)], plano)[0].kind is ZoneSignalKind.ROTURA_UL


# --- Un ID bajista ----------------------------------------------------------


def test_en_un_id_bajista_todo_va_al_reves() -> None:
    ul = zone(ZoneKind.LAST, inner=100.0, outer=98.0, direction="bajista")
    ob = zone(ZoneKind.ORDER_BLOCK, inner=105.0, outer=108.0, direction="bajista")

    assert classify([(101.0, 99.0, 99.5)], ul)[0].kind is ZoneSignalKind.RECHAZO_UL
    assert classify([(101.0, 97.0, 97.5)], ul)[0].kind is ZoneSignalKind.ROTURA_UL
    toque = classify([(106.0, 102.0, 103.0)], ob)[0]
    assert toque.kind is ZoneSignalKind.TOQUE_OB
    assert toque.reach == pytest.approx(106.0), "el OB de un ID bajista se busca hacia arriba"


# --- Tramo y validación ------------------------------------------------------


def test_el_tramo_empieza_donde_dice_quien_llama() -> None:
    """La barra anterior al arranque no se mira: ahí la zona todavía no existía."""
    bars = [(101.5, 98.0, 99.5), (101.5, 98.0, 99.5)]

    assert len(classify(bars, UL_UP)) == 2
    assert [item.index for item in classify(bars, UL_UP, first=1)] == [1]


def test_un_tramo_vacio_no_produce_señales() -> None:
    assert classify([(101.5, 98.0, 99.5)], UL_UP, first=1) == ()


def test_las_series_tienen_que_medir_lo_mismo() -> None:
    with pytest.raises(StructureError, match="no miden lo mismo"):
        classify_zone_signals(
            high=np.array([1.0, 2.0]),
            low=np.array([1.0]),
            close=np.array([1.0]),
            zone=UL_UP,
            first=0,
            last=0,
        )


def test_un_tramo_fuera_de_la_serie_se_rechaza() -> None:
    with pytest.raises(StructureError, match="fuera de la serie"):
        classify_zone_signals(
            high=np.array([1.0]),
            low=np.array([1.0]),
            close=np.array([1.0]),
            zone=UL_UP,
            first=0,
            last=5,
        )
