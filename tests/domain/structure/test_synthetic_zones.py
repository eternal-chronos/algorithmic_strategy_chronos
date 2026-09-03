"""Día sintético de la fase 2.0 (§6): las zonas calculadas a mano.

La serie está en `domain/structure/synthetic_zones.py` y **cada cifra de este
fichero se escribió antes de correr el motor**, leyendo la serie vela a vela.
No hay ni una aserción copiada de una salida.

Los casos del §6 y dónde se comprueban:

  1. UL de mecha normal ................... `test_ul_de_mecha_normal`
  2. UL que se extiende a la siguiente .... `test_ul_que_se_extiende`
  3. UL que NO se extiende ................ `test_ul_que_no_se_extiende*`
  4. UL de altura cero .................... `test_ul_de_altura_cero`
  5. PUL = el UL anterior hecho cuerpo .... `test_el_pul_es_el_cuerpo_del_extremo_anterior`
  6. ID sin PUL ........................... `test_el_primer_id_no_tiene_pul`
  7. UL y PUL sobre la misma vela ......... `test_el_ul_y_el_pul_se_reparten_la_vela`
  8. Los mismos casos en bajista .......... `test_la_serie_bajista_es_el_espejo_exacto`
"""

from __future__ import annotations

import pytest

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.synthetic_zones import (
    MIRROR_CENTRE,
    SYNTHETIC_ZONES_DOWN,
    SYNTHETIC_ZONES_UP,
)
from chronos.domain.structure.zones import ZoneKind
from tests.domain.structure.conftest import ZonedImpulse, run_zones


@pytest.fixture
def alcista() -> list[ZonedImpulse]:
    return run_zones(SYNTHETIC_ZONES_UP)[1]


@pytest.fixture
def bajista() -> list[ZonedImpulse]:
    return run_zones(SYNTHETIC_ZONES_DOWN)[1]


# --- Lo que el detector encuentra en la serie -------------------------------


def test_la_serie_produce_los_cinco_impulsos_de_diseno(alcista: list[ZonedImpulse]) -> None:
    """Sin esto, ningún caso de abajo cae sobre el ID que se diseñó para él."""
    assert len(alcista) == 5
    assert all(item.impulse.direction is ImpulseDirection.ALCISTA for item in alcista)
    assert all(item.impulse.publishable for item in alcista)

    # (constitución, fin, ancla, extremo, vela del ancla, vela del extremo)
    esperado = [
        (3, 4, 1995.00, 2010.00, 0, 2),
        (6, 7, 2009.00, 2019.00, 3, 5),
        (9, 11, 2018.50, 2026.00, 6, 8),
        (13, 17, 2023.50, 2034.00, 10, 12),
        (18, 19, 2033.60, 2039.00, 16, 17),
    ]
    obtenido = [
        (
            item.impulse.index_constitution,
            item.impulse.index_end,
            item.impulse.anchor,
            item.impulse.extreme,
            item.impulse.index_anchor,
            item.impulse.index_extreme,
        )
        for item in alcista
    ]
    assert obtenido == pytest.approx(esperado)


# --- Caso 1 y 3: UL de mecha normal, sin extender ---------------------------


def test_ul_de_mecha_normal(alcista: list[ZonedImpulse]) -> None:
    """ID#1: el extremo lo fija b2, cuyo cuerpo llega a 2010 y su mecha a 2012."""
    zona = alcista[0].last

    assert zona.kind is ZoneKind.LAST
    assert zona.index_defining == 2
    assert zona.inner == pytest.approx(2010.00)
    assert zona.outer == pytest.approx(2012.00)
    assert zona.height == pytest.approx(2.00)
    assert not zona.is_flat


def test_el_borde_interior_del_ul_es_la_linea_del_id(alcista: list[ZonedImpulse]) -> None:
    """§2: la zona ocupa el tramo de mecha y nunca cubre el cuerpo."""
    for item in alcista:
        assert item.last.inner == pytest.approx(item.impulse.extreme)


def test_ul_que_no_se_extiende_por_mecha_menor(alcista: list[ZonedImpulse]) -> None:
    """ID#1: b3 llega a 2011, por debajo de los 2012 de b2. No hay margen."""
    assert alcista[0].last.extended is False
    assert alcista[0].last.outer == pytest.approx(2012.00)


def test_ul_que_no_se_extiende_con_empate_exacto(alcista: list[ZonedImpulse]) -> None:
    """ID#3: b9 llega justo a 2026, la misma mecha que b8.

    "Más allá" es estricto en todo el módulo: un empate no extiende nada.
    """
    assert alcista[2].last.extended is False
    assert alcista[2].last.outer == pytest.approx(2026.00)


# --- Caso 2: UL que se extiende --------------------------------------------


def test_ul_que_se_extiende(alcista: list[ZonedImpulse]) -> None:
    """ID#2: el extremo lo fija b5 (mecha 2020) y b6 llega a 2023."""
    zona = alcista[1].last

    assert zona.index_defining == 5
    assert zona.extended is True
    assert zona.inner == pytest.approx(2019.00)  # el interior NO se mueve
    assert zona.outer == pytest.approx(2023.00)
    assert zona.height == pytest.approx(4.00)


def test_la_extension_es_de_una_sola_vela(alcista: list[ZonedImpulse]) -> None:
    """b7 llega a 2024, más lejos todavía, y no se tiene en cuenta."""
    assert alcista[1].last.outer == pytest.approx(2023.00)


def test_el_borde_exterior_extendido_lo_fija_la_vela_de_margen(
    alcista: list[ZonedImpulse],
) -> None:
    """Y por eso no es legible hasta que esa vela cierra."""
    zonas = run_zones(SYNTHETIC_ZONES_UP)
    series = zonas[0]
    zona = alcista[1].last

    assert zona.ts_outer_known == series.at(6)
    assert zona.ts_defining == series.at(5)


# --- Caso 4: UL de altura cero ----------------------------------------------


def test_ul_de_altura_cero(alcista: list[ZonedImpulse]) -> None:
    """ID#3: b8 cierra en 2026, su propio máximo. No hay mecha superior."""
    zona = alcista[2].last

    assert zona.index_defining == 8
    assert zona.inner == pytest.approx(2026.00)
    assert zona.outer == pytest.approx(2026.00)
    assert zona.height == pytest.approx(0.00)
    assert zona.is_flat


def test_la_zona_plana_sigue_existiendo(alcista: list[ZonedImpulse]) -> None:
    """Se conserva como zona degenerada: el ID tiene UL, lo que no tiene es mecha.

    Sus dos bordes caen exactamente sobre la línea del ID, así que con la regla
    de la fase 2.1 se comportaría igual que la línea de la fase 1.
    """
    zona = alcista[2].last
    assert zona.contains(2026.00)
    assert not zona.contains(2026.01)
    assert zona.low == zona.high == pytest.approx(alcista[2].impulse.extreme)


# --- Caso 5: el PUL sale de la vela del extremo anterior ---------------------
#
# En esta serie los cinco ID van al alza —cada uno nace de la ROTURA A FAVOR del
# anterior—, así que todos los PUL son de MECHA: la del extremo anterior apunta
# hacia el ID nuevo y es lo primero que el precio encuentra al volver. El PUL de
# CUERPO —el ID anterior iba al revés— se prueba en `test_zones.py` y en la serie
# `SYNTHETIC_SAME_UP` de la fase 2.1.


def test_el_pul_es_la_mecha_del_extremo_anterior_cuando_iba_igual(
    alcista: list[ZonedImpulse],
) -> None:
    """ID#2: su PUL sale de b2, la vela con la que se constituyó el ID#1.

    El ID#1 iba en su MISMO sentido, así que la zona es su mecha —el UL viejo tal
    cual— y no el cuerpo: interior la punta (2012) y exterior el borde del cuerpo
    (2010), que es el que hay que cruzar para dejarla atrás bajando.
    """
    item = alcista[1]
    zona = item.penultimate

    assert zona is not None
    assert zona.index_defining == 2 == alcista[0].impulse.index_extreme
    assert zona.inner == pytest.approx(2012.00)  # punta de la mecha: lo primero
    assert zona.outer == pytest.approx(2010.00)  # borde del cuerpo: lo que hay que cruzar
    assert zona.height == pytest.approx(2.00)


def test_cada_pul_hereda_la_vela_del_ul_anterior(alcista: list[ZonedImpulse]) -> None:
    """La cadena entera, escrita a mano leyendo la serie."""
    esperado = [
        (2, 2012.00, 2010.00),  # ID#2 <- extremo del ID#1 en b2
        (5, 2020.00, 2019.00),  # ID#3 <- extremo del ID#2 en b5
        (8, 2026.00, 2026.00),  # ID#4 <- extremo del ID#3 en b8: sin mecha, altura cero
        (12, 2035.00, 2034.00),  # ID#5 <- extremo del ID#4 en b12
    ]
    for item, (vela, interior, exterior) in zip(alcista[1:], esperado, strict=True):
        zona = item.penultimate
        assert zona is not None
        assert zona.index_defining == vela
        assert zona.inner == pytest.approx(interior)
        assert zona.outer == pytest.approx(exterior)


def test_el_pul_de_mecha_no_cubre_el_cuerpo_de_su_vela(
    alcista: list[ZonedImpulse],
) -> None:
    """b2 va de 2004 a 2012; el PUL del ID#2 sólo toma la mecha [2010, 2012]."""
    zona = alcista[1].penultimate

    assert zona is not None
    assert zona.contains(2011.00)
    assert not zona.contains(2007.00)  # el cuerpo: queda por detrás de la zona
    assert not zona.contains(2004.50)  # la mecha de abajo: de nadie


def test_el_pul_nace_con_su_id_y_no_antes(alcista: list[ZonedImpulse]) -> None:
    """La vela es b2, pero la zona es del ID#2 y el ID#2 nace en b6."""
    series, _ = run_zones(SYNTHETIC_ZONES_UP)
    zona = alcista[1].penultimate

    assert zona is not None
    assert zona.ts_defining == series.at(2)
    assert zona.ts_birth == series.at(6) == alcista[1].impulse.ts_constitution
    assert zona.ts_outer_known == series.at(2)


# --- Caso 6: ID sin PUL ------------------------------------------------------


def test_el_primer_id_no_tiene_pul(alcista: list[ZonedImpulse]) -> None:
    """El ID#1 no tiene ID anterior del que sacarlo. No es un fallo."""
    assert alcista[0].penultimate is None
    assert alcista[0].impulse.index_penultimate is None


def test_el_id_sin_pul_si_tiene_ul(alcista: list[ZonedImpulse]) -> None:
    """Son dos zonas independientes: que falte el PUL no deja al ID sin UL."""
    item = alcista[0]
    assert item.last.inner == pytest.approx(2010.00)
    assert item.last.outer == pytest.approx(2012.00)


def test_solo_el_primero_se_queda_sin_pul(alcista: list[ZonedImpulse]) -> None:
    assert [item.penultimate is None for item in alcista] == [True, False, False, False, False]


# --- Caso 7: el UL y el PUL se reparten la misma vela ------------------------


def test_el_pul_del_id_siguiente_es_el_ul_viejo_tal_cual(
    alcista: list[ZonedImpulse],
) -> None:
    """b2 lleva el UL del ID#1, y el ID#2 —que va igual— hereda ESA MISMA zona.

    Los bordes son los mismos y lo que se invierte es cuál es interior: el UL se
    recorre hacia arriba (cuerpo -> punta) y el PUL hacia abajo (punta ->
    cuerpo), porque la rotura a favor sube y la rotura en contra baja.
    """
    ul = alcista[0].last
    pul = alcista[1].penultimate

    assert pul is not None
    assert ul.index_defining == pul.index_defining == 2
    assert ul.low == pytest.approx(2010.00) and ul.high == pytest.approx(2012.00)
    assert (pul.low, pul.high) == (ul.low, ul.high)
    assert (pul.inner, pul.outer) == (ul.outer, ul.inner)


def test_el_color_de_la_constituyente_es_siempre_el_contrario(
    alcista: list[ZonedImpulse], bajista: list[ZonedImpulse]
) -> None:
    """La vela que constituye es siempre del color contrario al impulso.

    No depende de ninguna zona, pero es la regla que hace que la vela del UL y
    la de la constitución nunca sean la misma.
    """
    series_up, _ = run_zones(SYNTHETIC_ZONES_UP)
    series_down, _ = run_zones(SYNTHETIC_ZONES_DOWN)
    for series, items, contrario in (
        (series_up, alcista, BodyDirection.BEARISH),
        (series_down, bajista, BodyDirection.BULLISH),
    ):
        for item in items:
            assert series.direction_of(item.impulse.index_constitution) is contrario


# --- Caso 8: simetría exacta ------------------------------------------------


def test_la_serie_bajista_es_el_espejo_exacto(
    alcista: list[ZonedImpulse], bajista: list[ZonedImpulse]
) -> None:
    """Todas las comparaciones del módulo son estrictas y el espejo las invierte
    a la vez, así que cualquier asimetría sería un fallo del código."""
    assert len(bajista) == len(alcista) == 5

    def reflejo(precio: float) -> float:
        return 2 * MIRROR_CENTRE - precio

    for arriba, abajo in zip(alcista, bajista, strict=True):
        assert abajo.impulse.direction is ImpulseDirection.BAJISTA
        assert abajo.impulse.index_constitution == arriba.impulse.index_constitution
        assert abajo.impulse.index_end == arriba.impulse.index_end
        assert abajo.impulse.anchor == pytest.approx(reflejo(arriba.impulse.anchor))
        assert abajo.impulse.extreme == pytest.approx(reflejo(arriba.impulse.extreme))

        assert abajo.last.inner == pytest.approx(reflejo(arriba.last.inner))
        assert abajo.last.outer == pytest.approx(reflejo(arriba.last.outer))
        assert abajo.last.extended is arriba.last.extended
        assert abajo.last.is_flat is arriba.last.is_flat
        assert abajo.last.height == pytest.approx(arriba.last.height)

        assert (abajo.penultimate is None) is (arriba.penultimate is None)
        if arriba.penultimate is not None and abajo.penultimate is not None:
            assert abajo.penultimate.inner == pytest.approx(reflejo(arriba.penultimate.inner))
            assert abajo.penultimate.outer == pytest.approx(reflejo(arriba.penultimate.outer))
            assert abajo.penultimate.height == pytest.approx(arriba.penultimate.height)
            assert abajo.penultimate.index_defining == arriba.penultimate.index_defining


def test_los_casos_del_enunciado_caen_tambien_en_bajista(
    bajista: list[ZonedImpulse],
) -> None:
    """Los mismos cinco casos construibles, leídos del lado bajista."""
    assert bajista[0].last.extended is False and bajista[0].last.height == pytest.approx(2.00)
    assert bajista[1].last.extended is True and bajista[1].last.height == pytest.approx(4.00)
    assert bajista[2].last.is_flat
    pul = bajista[3].penultimate
    assert pul is not None and pul.index_defining == 8
    assert bajista[0].penultimate is None
