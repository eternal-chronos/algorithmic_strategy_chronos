"""La medida del recorrido de una señal. Aritmética, sin series ni relojes."""

from __future__ import annotations

import numpy as np
import pytest

from chronos.domain.entries.excursion import (
    first_adverse_close,
    measure_excursion,
)
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError

ALCISTA = ImpulseDirection.ALCISTA
BAJISTA = ImpulseDirection.BAJISTA


def _run(high, low, direction=ALCISTA, price=100.0, risk=10.0, targets=(1.0, 2.0, 3.0)):
    return measure_excursion(
        high=np.array(high, dtype=float),
        low=np.array(low, dtype=float),
        price=price,
        risk=risk,
        direction=direction,
        targets=targets,
    )


def test_el_recorrido_a_favor_va_en_multiplos_del_riesgo() -> None:
    medida = _run(high=[101, 108, 115], low=[99, 100, 108])

    assert medida.favor == pytest.approx(1.5)
    assert medida.against == pytest.approx(0.1)
    assert medida.bars == 3


def test_un_objetivo_se_alcanza_con_la_mecha_y_en_la_primera_vela_que_llega() -> None:
    medida = _run(high=[101, 110, 105, 121], low=[99, 100, 100, 100])

    #: 1R en la segunda vela, 2R en la cuarta, 3R nunca.
    assert medida.reached == (1, 3, None)


def test_en_un_bajista_a_favor_es_hacia_abajo() -> None:
    medida = _run(high=[101, 102], low=[95, 88], direction=BAJISTA)

    assert medida.favor == pytest.approx(1.2)
    assert medida.against == pytest.approx(0.2)
    assert medida.reached == (1, None, None)


def test_una_senal_que_nunca_estuvo_a_favor_da_recorrido_negativo() -> None:
    """No se recorta a cero: que el precio nunca subiera del contacto es el dato."""
    medida = _run(high=[99, 98], low=[97, 92])

    assert medida.favor == pytest.approx(-0.1)
    assert medida.reached == (None, None, None)


def test_sin_tramo_que_observar_no_se_inventa_ningun_recorrido() -> None:
    medida = _run(high=[], low=[])

    assert medida == medida.__class__(favor=0.0, against=0.0, bars=0, reached=(None, None, None))


def test_una_senal_sin_riesgo_no_se_puede_medir() -> None:
    with pytest.raises(StructureError):
        _run(high=[101], low=[99], risk=0.0)


def test_desmentir_es_cerrar_mas_alla_del_borde_exterior() -> None:
    close = np.array([95.0, 91.0, 89.0, 100.0])

    assert first_adverse_close(close, outer=90.0, direction=ALCISTA) == 2
    assert first_adverse_close(close, outer=80.0, direction=ALCISTA) is None
    assert first_adverse_close(close, outer=94.0, direction=BAJISTA) == 0
