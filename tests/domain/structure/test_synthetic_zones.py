"""Día sintético de la fase 2.0 (§6): las zonas calculadas a mano.

La serie está en `domain/structure/synthetic_zones.py` y **cada cifra de este
fichero se escribió antes de correr el motor**, leyendo la serie vela a vela.
No hay ni una aserción copiada de una salida.

Los ocho casos del §6 y dónde se comprueban:

  1. UL de mecha normal ................... `test_ul_de_mecha_normal`
  2. UL que se extiende a la siguiente .... `test_ul_que_se_extiende`
  3. UL que NO se extiende ................ `test_ul_que_no_se_extiende*`
  4. UL de altura cero .................... `test_ul_de_altura_cero`
  5. OB confirmado dos barras después ..... `test_ob_que_tarda_dos_barras`
  6. OB que nunca se confirma ............. `test_ob_que_nunca_se_confirma`
  7. OB confirmado por la constituyente ... `test_la_vela_que_constituye_nunca_confirma`
  8. Doji en posición de OB ............... `test_doji_en_posicion_de_ob_con_a2`
  9. Los mismos casos en bajista .......... `test_la_serie_bajista_es_el_espejo_exacto`

El caso 7 se comprueba **por imposibilidad**, que es el único resultado honesto:
la vela que constituye es siempre del color contrario al impulso y la
confirmación exige el color del impulso, así que ninguna vela puede hacer las
dos cosas. El test lo fija sobre la serie entera para que, si algún día alguien
cambia la regla, salte aquí.
"""

from __future__ import annotations

import pytest

from chronos.domain.structure.enums import AnchorMode, BodyDirection, ImpulseDirection
from chronos.domain.structure.synthetic_zones import (
    MIRROR_CENTRE,
    SYNTHETIC_DOJI_OB,
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


# --- Caso 5: OB que tarda dos barras en confirmarse -------------------------


def test_ob_que_tarda_dos_barras(alcista: list[ZonedImpulse]) -> None:
    """ID#4: el ancla es b10, con la mecha en 2045. Nadie la supera hasta b15."""
    item = alcista[3]
    zona = item.order_block

    assert zona is not None
    assert zona.index_defining == 10
    assert zona.index_confirmation == 15
    assert item.impulse.index_constitution == 13
    # b14 llega a 2040 y no basta; b15 llega a 2046 y sí.
    assert zona.index_confirmation - item.impulse.index_constitution == 2


def test_el_ob_cubre_la_vela_entera(alcista: list[ZonedImpulse]) -> None:
    """A diferencia del UL, el OB sí cubre el cuerpo: b10 va de 2023 a 2045."""
    zona = alcista[3].order_block

    assert zona is not None
    assert zona.inner == pytest.approx(2045.00)  # lo primero que encuentra la caída
    assert zona.outer == pytest.approx(2023.00)  # lo que hay que cruzar para salir
    assert zona.height == pytest.approx(22.00)
    assert zona.low == pytest.approx(2023.00)
    assert zona.high == pytest.approx(2045.00)


def test_un_ob_confirmado_tarde_nace_al_confirmarse(alcista: list[ZonedImpulse]) -> None:
    """El ID#4 nace en b13 pero su OB no existe hasta b15."""
    series, _ = run_zones(SYNTHETIC_ZONES_UP)
    zona = alcista[3].order_block

    assert zona is not None
    assert zona.ts_confirmation == series.at(15)
    assert zona.ts_birth == series.at(15)
    assert zona.ts_birth > alcista[3].impulse.ts_constitution


def test_el_ob_confirmado_pronto_nace_con_el_id(alcista: list[ZonedImpulse]) -> None:
    """Lo corriente: el ancla se supera dentro de la pierna, antes de constituir.

    Ahí manda la constitución, porque durante el limbo no existe ninguna zona.
    """
    series, _ = run_zones(SYNTHETIC_ZONES_UP)
    zona = alcista[0].order_block

    assert zona is not None
    assert zona.index_confirmation == 1
    assert zona.ts_confirmation == series.at(1)
    assert zona.ts_birth == series.at(3) == alcista[0].impulse.ts_constitution


# --- Caso 6: OB que nunca se confirma ---------------------------------------


def test_ob_que_nunca_se_confirma(alcista: list[ZonedImpulse]) -> None:
    """ID#5: su ancla es b16, con la mecha en 2200. El ID muere en b19."""
    item = alcista[4]

    assert item.impulse.index_anchor == 16
    assert item.impulse.index_end == 19
    assert item.order_block is None


def test_el_id_sin_ob_si_tiene_ul(alcista: list[ZonedImpulse]) -> None:
    """Son dos zonas independientes: que falte el OB no deja al ID sin UL."""
    item = alcista[4]
    assert item.last.inner == pytest.approx(2039.00)
    assert item.last.outer == pytest.approx(2040.00)


# --- Caso 7: imposible por construcción -------------------------------------


def test_la_vela_que_constituye_nunca_confirma(alcista: list[ZonedImpulse]) -> None:
    """§6.7 pedía este caso y **no se puede construir**.

    La vela que constituye un ID es la primera contraria a la pierna, y la
    dirección del ID es la de la pierna: en un ID alcista constituye una roja.
    La confirmación exige una vela del color del impulso, verde en un ID alcista.
    Las dos condiciones se excluyen.
    """
    for item in alcista:
        if item.order_block is None:
            continue
        assert item.order_block.index_confirmation != item.impulse.index_constitution


def test_el_color_de_la_constituyente_es_siempre_el_contrario(
    alcista: list[ZonedImpulse], bajista: list[ZonedImpulse]
) -> None:
    """La razón de fondo del test anterior, medida en las dos direcciones."""
    series_up, _ = run_zones(SYNTHETIC_ZONES_UP)
    series_down, _ = run_zones(SYNTHETIC_ZONES_DOWN)

    for series, items, contrario in (
        (series_up, alcista, BodyDirection.BEARISH),
        (series_down, bajista, BodyDirection.BULLISH),
    ):
        for item in items:
            assert series.direction_of(item.impulse.index_constitution) is contrario


# --- Caso 8: doji en posición de OB (sólo con ancla A2) ---------------------


def test_doji_en_posicion_de_ob_con_a2() -> None:
    """Con A2 el ancla es la primera vela de la pierna, y los dojis no la cortan."""
    series, items = run_zones(SYNTHETIC_DOJI_OB, anchor_mode=AnchorMode.A2_FIRST_LEG_BAR)
    item = items[1]
    zona = item.order_block

    assert item.impulse.index_anchor == 4
    assert series.direction_of(4) is BodyDirection.DOJI
    assert zona is not None
    # El color del OB no interviene en la regla: la zona es la vela entera y la
    # confirmación mira el color de quien la supera, no el suyo.
    assert zona.inner == pytest.approx(2009.50)
    assert zona.outer == pytest.approx(2008.50)
    assert zona.height == pytest.approx(1.00)
    assert zona.index_confirmation == 5


def test_con_el_ancla_del_proyecto_el_ob_nunca_es_un_doji() -> None:
    """A1 busca la última vela *contraria*, y un doji no lo es. Mismas velas."""
    series, items = run_zones(SYNTHETIC_DOJI_OB, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    item = items[1]

    assert item.impulse.index_anchor == 3  # se salta el doji de b4
    assert series.direction_of(3) is BodyDirection.BEARISH
    assert item.order_block is not None
    assert item.order_block.height == pytest.approx(3.00)  # b3 va de 2008 a 2011


# --- Caso 9: simetría exacta ------------------------------------------------


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

        assert (abajo.order_block is None) is (arriba.order_block is None)
        if arriba.order_block is not None and abajo.order_block is not None:
            assert abajo.order_block.inner == pytest.approx(reflejo(arriba.order_block.inner))
            assert abajo.order_block.outer == pytest.approx(reflejo(arriba.order_block.outer))
            assert abajo.order_block.height == pytest.approx(arriba.order_block.height)
            assert (
                abajo.order_block.index_confirmation == arriba.order_block.index_confirmation
            )


def test_los_casos_del_enunciado_caen_tambien_en_bajista(
    bajista: list[ZonedImpulse],
) -> None:
    """Los mismos cinco casos construibles, leídos del lado bajista."""
    assert bajista[0].last.extended is False and bajista[0].last.height == pytest.approx(2.00)
    assert bajista[1].last.extended is True and bajista[1].last.height == pytest.approx(4.00)
    assert bajista[2].last.is_flat
    ob = bajista[3].order_block
    assert ob is not None and ob.index_confirmation == 15
    assert bajista[4].order_block is None
