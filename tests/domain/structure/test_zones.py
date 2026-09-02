"""Geometría de las zonas y su garantía anti-lookahead (§5).

Un guardarraíl que nunca se ha visto saltar no es un guardarraíl: los tres
primeros tests provocan `LookaheadError` a propósito por las tres vías que pide
el enunciado, y el cuarto cubre la que salió de implementarlo —preguntar por un
PUL que no existe porque el ID no tiene ninguno detrás—.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.synthetic_zones import SYNTHETIC_ZONES_UP
from chronos.domain.structure.zones import (
    CandleSeries,
    ZoneBook,
    ZoneKind,
    last_zone,
    penultimate_zone,
)
from tests.domain.structure.conftest import make_series, run_zones

# --- 1. Una zona antes de que nazca -----------------------------------------


def test_pedir_una_zona_antes_de_su_nacimiento_lanza_lookahead() -> None:
    """Durante el limbo no existe ninguna zona: la del ID#1 nace en b3."""
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    zona = items[0].last

    assert zona.ts_birth == series.at(3)
    with pytest.raises(LookaheadError, match="no existe"):
        zona.borders_at(series.at(2))


def test_la_zona_si_es_legible_desde_su_nacimiento() -> None:
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    interior, exterior = items[0].last.borders_at(series.at(3))

    assert interior == pytest.approx(2010.00)
    assert exterior == pytest.approx(2012.00)


def test_el_libro_de_zonas_tambien_lo_impide() -> None:
    """La fase 2.1 leerá por aquí, así que la garantía tiene que estar en la puerta."""
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    libro = ZoneBook("H4", tuple(items[0].zones_tuple()))

    with pytest.raises(LookaheadError):
        libro.of(1, ZoneKind.LAST, at=series.at(2))
    assert libro.of(1, ZoneKind.LAST, at=series.at(3)).inner == pytest.approx(2010.00)


# --- 2. Un PUL antes de que nazca su ID --------------------------------------


def test_pedir_un_pul_antes_de_que_nazca_su_id_lanza_lookahead() -> None:
    """La vela del PUL del ID#2 es b2, pero el ID no nace hasta b6."""
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    zona = items[1].penultimate

    assert zona is not None
    assert zona.index_defining == 2
    assert zona.ts_birth == series.at(6)
    for barra in (2, 3, 5):
        with pytest.raises(LookaheadError, match="no existe"):
            zona.borders_at(series.at(barra))
    assert zona.borders_at(series.at(6)) == (pytest.approx(2010.00), pytest.approx(2005.00))


def test_un_id_sin_pul_no_se_puede_consultar() -> None:
    """El ID#1 es el primero del histórico. Preguntarlo tiene que doler."""
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    assert items[0].penultimate is None

    libro = ZoneBook("H4", tuple(items[0].zones_tuple()))
    libro.record_missing_penultimate(1)

    with pytest.raises(LookaheadError, match="no tiene PUL"):
        libro.of(1, ZoneKind.PENULTIMATE, at=series.at(19))


# --- 3. La extensión del UL antes de que cierre su vela ---------------------


def test_la_extension_del_ul_no_se_puede_consultar_antes_de_tiempo() -> None:
    """El UL del ID#2 llega a 2020 con b5 y a 2023 con b6.

    Al cierre de b5 el borde exterior de la zona **todavía no es** 2023: eso no
    se sabe hasta que b6 cierra. Preguntarlo antes es mirar al futuro.
    """
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    zona = items[1].last

    assert zona.extended is True
    assert zona.ts_outer_known == series.at(6)
    with pytest.raises(LookaheadError, match="margen del UL"):
        zona.outer_at(series.at(5))
    assert zona.outer_at(series.at(6)) == pytest.approx(2023.00)


def test_sin_extension_el_exterior_se_conoce_con_la_propia_vela() -> None:
    """El contraste: el UL del ID#1 no se estira, así que b2 ya lo fija entero."""
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    zona = items[0].last

    assert zona.extended is False
    assert zona.ts_outer_known == series.at(2)
    assert zona.outer_at(series.at(2)) == pytest.approx(2012.00)


def test_una_marca_naif_se_rechaza() -> None:
    _, items = run_zones(SYNTHETIC_ZONES_UP)
    with pytest.raises(StructureError, match="tz-aware"):
        items[0].last.borders_at(datetime(2024, 3, 9, 0, 0))


# --- Geometría ---------------------------------------------------------------


def test_el_ul_alcista_va_del_cuerpo_a_la_mecha_hacia_arriba() -> None:
    series = make_series([(100.0, 110.0, 95.0, 105.0), (105.0, 106.0, 100.0, 101.0)])
    zona = last_zone(
        series,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona.inner == pytest.approx(105.0)  # max(open, close)
    assert zona.outer == pytest.approx(110.0)  # high
    assert zona.height == pytest.approx(5.0)


def test_el_ul_bajista_va_del_cuerpo_a_la_mecha_hacia_abajo() -> None:
    series = make_series([(105.0, 110.0, 95.0, 100.0), (100.0, 104.0, 99.0, 103.0)])
    zona = last_zone(
        series,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection.BAJISTA,
        index_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona.inner == pytest.approx(100.0)  # min(open, close)
    assert zona.outer == pytest.approx(95.0)  # low
    assert zona.height == pytest.approx(5.0)


def test_la_zona_ul_nunca_cubre_el_cuerpo() -> None:
    """Es la diferencia con el PUL, y hay que poder verla escrita."""
    series = make_series([(100.0, 110.0, 95.0, 105.0), (105.0, 106.0, 100.0, 101.0)])
    zona = last_zone(
        series,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_extreme=0,
        ts_constitution=series.at(1),
    )

    assert not zona.contains(104.99)  # dentro del cuerpo
    assert zona.contains(105.0)  # el borde interior es la línea del ID
    assert zona.contains(110.0)


def test_el_pul_alcista_es_el_cuerpo_de_la_vela_del_minimo_anterior() -> None:
    """Vela roja: el precio baja y encuentra primero el borde alto del cuerpo."""
    series = make_series(
        [
            (100.0, 102.0, 90.0, 92.0),  # roja: cuerpo [92, 100], mechas fuera
            (92.0, 99.0, 91.0, 98.0),
        ]
    )
    zona = penultimate_zone(
        series,
        id_num=2,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_previous_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona is not None
    assert zona.inner == pytest.approx(100.0)  # max(open, close)
    assert zona.outer == pytest.approx(92.0)  # min(open, close)
    assert zona.height == pytest.approx(8.0)


def test_el_pul_bajista_es_el_cuerpo_de_la_vela_del_maximo_anterior() -> None:
    """Vela verde: el precio sube y encuentra primero el borde bajo del cuerpo."""
    series = make_series(
        [
            (92.0, 102.0, 90.0, 100.0),  # verde: cuerpo [92, 100]
            (100.0, 101.0, 93.0, 94.0),
        ]
    )
    zona = penultimate_zone(
        series,
        id_num=2,
        timeframe="H4",
        direction=ImpulseDirection.BAJISTA,
        index_previous_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona is not None
    assert zona.inner == pytest.approx(92.0)
    assert zona.outer == pytest.approx(100.0)


def test_el_pul_no_cubre_ninguna_mecha() -> None:
    """El UL toma la punta y el PUL la base: juntos, del `open` a la mecha."""
    series = make_series([(100.0, 102.0, 90.0, 92.0), (92.0, 99.0, 91.0, 98.0)])
    zona = penultimate_zone(
        series,
        id_num=2,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_previous_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona is not None
    assert zona.contains(96.0)  # el cuerpo SÍ entra, al revés que en el UL
    assert not zona.contains(101.0)  # la mecha de arriba, no
    assert not zona.contains(91.0)  # la de abajo, tampoco


def test_sin_id_anterior_no_hay_pul() -> None:
    """El primero del histórico: no es un fallo, es que no hay de dónde sacarlo."""
    series = make_series([(100.0, 102.0, 90.0, 92.0)])
    zona = penultimate_zone(
        series,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_previous_extreme=None,
        ts_constitution=series.at(0),
    )

    assert zona is None


def test_un_pul_de_altura_cero_se_conserva_como_zona_degenerada() -> None:
    """La vela abrió y cerró en el mismo precio: la zona existe y mide cero."""
    series = make_series([(100.0, 105.0, 95.0, 100.0), (100.0, 104.0, 99.0, 103.0)])
    zona = penultimate_zone(
        series,
        id_num=2,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_previous_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona is not None
    assert zona.is_flat
    assert zona.height == pytest.approx(0.0)


def test_el_pul_no_espera_a_ninguna_confirmacion() -> None:
    """Su vela cerró antes de que el ID naciera: nace con él y ahí se queda."""
    series = make_series([(100.0, 102.0, 90.0, 92.0), (92.0, 99.0, 91.0, 98.0)])
    zona = penultimate_zone(
        series,
        id_num=2,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_previous_extreme=0,
        ts_constitution=series.at(1),
    )

    assert zona is not None
    assert zona.ts_birth == series.at(1)
    assert zona.ts_outer_known == series.at(0)


def test_el_ultimo_extremo_de_la_serie_no_tiene_vela_de_margen() -> None:
    """Sin vela siguiente no hay extensión posible, y no puede reventar."""
    series = make_series([(100.0, 110.0, 95.0, 105.0)])
    zona = last_zone(
        series,
        id_num=1,
        timeframe="H4",
        direction=ImpulseDirection.ALCISTA,
        index_extreme=0,
        ts_constitution=series.at(0),
    )

    assert zona.extended is False
    assert zona.outer == pytest.approx(110.0)


# --- El libro ----------------------------------------------------------------


def test_el_libro_devuelve_lo_que_ya_habia_nacido() -> None:
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    todas = tuple(zona for item in items for zona in item.zones_tuple())
    libro = ZoneBook("H4", todas)

    vivas = libro.alive_at(series.at(6))
    # El ID#1 no tiene PUL: es el primero del histórico.
    assert {(zona.id_num, zona.kind) for zona in vivas} == {
        (1, ZoneKind.LAST),
        (2, ZoneKind.LAST),
        (2, ZoneKind.PENULTIMATE),
    }


def test_las_series_descuadradas_se_rechazan_al_construir() -> None:
    with pytest.raises(StructureError, match="no miden lo mismo"):
        CandleSeries(
            timestamps=make_series([(1.0, 2.0, 0.5, 1.5)]).timestamps,
            open=make_series([(1.0, 2.0, 0.5, 1.5)]).open,
            high=make_series([(1.0, 2.0, 0.5, 1.5), (1.0, 2.0, 0.5, 1.5)]).high,
            low=make_series([(1.0, 2.0, 0.5, 1.5)]).low,
            close=make_series([(1.0, 2.0, 0.5, 1.5)]).close,
        )


def test_una_zona_fuera_del_libro_no_se_confunde_con_una_no_nacida() -> None:
    series, items = run_zones(SYNTHETIC_ZONES_UP)
    libro = ZoneBook("H4", tuple(items[0].zones_tuple()))

    with pytest.raises(StructureError, match="No hay zona"):
        libro.of(99, ZoneKind.LAST, at=series.at(19))
