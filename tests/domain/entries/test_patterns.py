"""Los dos patrones de M15 con los que se afina la entrada: OB y FVG.

Velas escritas a mano, una por una: lo que se fija aquí es la geometría —qué
vela define la zona, dónde caen sus bordes y en cuál se supo que existía— y sobre
todo que **no se sabe antes de tiempo**. Un OB que se marcara en su propia vela
sería un patrón que sólo existe con el futuro delante.
"""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.entries.patterns import (
    PatternKind,
    PricePattern,
    fair_value_gaps,
    order_blocks,
    patterns_of,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError

BAJISTA = ImpulseDirection.BAJISTA
ALCISTA = ImpulseDirection.ALCISTA


def _series(velas: list[tuple[float, float, float, float]]) -> dict[str, np.ndarray]:
    """De una lista de (open, high, low, close) a los cuatro arrays."""
    matrix = np.array(velas, dtype=float)
    return {
        "open_": matrix[:, 0],
        "high": matrix[:, 1],
        "low": matrix[:, 2],
        "close": matrix[:, 3],
    }


# --- OB ---------------------------------------------------------------------


def test_el_ob_bajista_es_la_ultima_vela_verde_antes_de_la_caida() -> None:
    velas = _series(
        [
            (10.0, 10.5, 9.8, 10.4),  # 0 verde, pero el precio no se va
            (10.4, 11.0, 10.3, 10.9),  # 1 verde: el OB
            (10.9, 10.9, 10.0, 10.1),  # 2 roja, cierra por debajo del mínimo del OB
        ]
    )
    (ob,) = order_blocks(**velas, direction=BAJISTA, displacement=1)

    assert ob.kind is PatternKind.ORDER_BLOCK
    assert ob.index_origin == 1
    # Se sabe en la vela que se va, no en la del propio OB.
    assert ob.index_known == 2
    assert (ob.low, ob.high) == (10.3, 11.0)
    # Para vender, el precio vuelve desde abajo: encuentra antes el suelo.
    assert ob.near == 10.3
    assert ob.far == 11.0


def test_el_ob_alcista_es_su_espejo() -> None:
    velas = _series(
        [
            (10.0, 10.2, 9.5, 9.6),  # 0 roja: el OB
            (9.6, 10.4, 9.6, 10.3),  # 1 verde, cierra por encima del máximo
        ]
    )
    (ob,) = order_blocks(**velas, direction=ALCISTA, displacement=1)

    assert ob.index_origin == 0
    assert ob.index_known == 1
    assert (ob.near, ob.far) == (10.2, 9.5)


def test_sin_desplazamiento_no_hay_ob() -> None:
    """Una vela verde a la que no sigue ninguna caída es una vela verde."""
    velas = _series([(10.0, 10.5, 9.8, 10.4), (10.4, 10.6, 10.2, 10.5)])

    assert order_blocks(**velas, direction=BAJISTA, displacement=1) == ()


def test_el_desplazamiento_le_da_al_precio_las_velas_que_se_le_den() -> None:
    velas = _series(
        [
            (10.0, 11.0, 9.9, 10.9),  # 0 verde: el OB
            (10.9, 11.0, 10.5, 10.6),  # 1 baja, pero no llega
            (10.6, 10.7, 9.5, 9.6),  # 2 aquí sí cierra por debajo de 9,9
        ]
    )

    assert order_blocks(**velas, direction=BAJISTA, displacement=1) == ()
    (ob,) = order_blocks(**velas, direction=BAJISTA, displacement=2)
    assert (ob.index_origin, ob.index_known) == (0, 2)


def test_manda_la_primera_vela_que_se_va() -> None:
    """El patrón se fecha en la primera que lo confirma, no en la última."""
    velas = _series(
        [
            (10.0, 11.0, 9.9, 10.9),
            (10.9, 11.0, 9.0, 9.1),
            (9.1, 9.2, 8.0, 8.1),
        ]
    )
    (ob,) = order_blocks(**velas, direction=BAJISTA, displacement=3)

    assert ob.index_known == 1


def test_un_desplazamiento_imposible_falla_en_vez_de_devolver_nada() -> None:
    velas = _series([(10.0, 10.5, 9.8, 10.4)])

    with pytest.raises(StructureError):
        order_blocks(**velas, direction=BAJISTA, displacement=0)


# --- FVG --------------------------------------------------------------------


def test_el_fvg_bajista_es_el_hueco_entre_la_primera_y_la_tercera() -> None:
    high = np.array([11.0, 10.5, 9.4])
    low = np.array([10.0, 9.5, 9.0])
    (fvg,) = fair_value_gaps(high=high, low=low, direction=BAJISTA)

    assert fvg.kind is PatternKind.FAIR_VALUE_GAP
    assert fvg.index_origin == 1
    assert fvg.index_known == 2
    assert (fvg.low, fvg.high) == (9.4, 10.0)


def test_el_fvg_alcista_es_su_espejo() -> None:
    high = np.array([9.4, 10.0, 11.0])
    low = np.array([9.0, 9.5, 10.5])
    (fvg,) = fair_value_gaps(high=high, low=low, direction=ALCISTA)

    assert (fvg.low, fvg.high) == (9.4, 10.5)
    assert fvg.near == 10.5


def test_dos_velas_que_se_tocan_no_dejan_hueco() -> None:
    """"Más allá" es estricto aquí igual que en la rotura del módulo 1."""
    high = np.array([11.0, 10.5, 10.0])
    low = np.array([10.0, 9.5, 9.0])

    assert fair_value_gaps(high=high, low=low, direction=BAJISTA) == ()


def test_con_menos_de_tres_velas_no_hay_hueco_que_medir() -> None:
    assert fair_value_gaps(
        high=np.array([11.0, 10.0]), low=np.array([10.0, 9.0]), direction=BAJISTA
    ) == ()


# --- Los dos juntos ---------------------------------------------------------


def test_los_patrones_salen_en_el_orden_en_que_se_supieron() -> None:
    velas = _series(
        [
            (10.0, 11.0, 10.0, 10.9),  # 0 verde: OB
            (10.9, 11.0, 9.5, 9.6),  # 1 se va: OB conocido aquí
            (9.6, 9.8, 9.0, 9.1),  # 2 FVG con la 0: high[2] < low[0]
        ]
    )
    encontrados = patterns_of(**velas, direction=BAJISTA, displacement=1)

    assert [item.index_known for item in encontrados] == sorted(
        item.index_known for item in encontrados
    )
    assert {item.kind for item in encontrados} == {
        PatternKind.ORDER_BLOCK,
        PatternKind.FAIR_VALUE_GAP,
    }


def test_ningun_patron_se_conoce_antes_de_la_vela_que_lo_define() -> None:
    rng = np.random.default_rng(7)
    base = 1900 + np.cumsum(rng.normal(0, 0.5, 400))
    high = base + np.abs(rng.normal(0, 0.4, 400))
    low = base - np.abs(rng.normal(0, 0.4, 400))
    open_ = base + rng.normal(0, 0.1, 400)
    close = base + rng.normal(0, 0.1, 400)
    velas = {"open_": open_, "high": high, "low": low, "close": close}

    for direction in (BAJISTA, ALCISTA):
        encontrados = patterns_of(**velas, direction=direction)
        assert encontrados
        for item in encontrados:
            assert item.index_known >= item.index_origin
            assert item.high >= item.low


def test_un_patron_del_reves_no_se_construye() -> None:
    with pytest.raises(StructureError):
        PricePattern(
            kind=PatternKind.ORDER_BLOCK,
            direction=BAJISTA,
            index_origin=0,
            index_known=1,
            low=11.0,
            high=10.0,
        )


def test_un_patron_conocido_antes_de_existir_no_se_construye() -> None:
    with pytest.raises(StructureError):
        PricePattern(
            kind=PatternKind.FAIR_VALUE_GAP,
            direction=BAJISTA,
            index_origin=5,
            index_known=4,
            low=10.0,
            high=11.0,
        )
