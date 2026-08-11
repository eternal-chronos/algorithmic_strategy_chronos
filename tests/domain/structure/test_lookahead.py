"""Garantía anti-lookahead (§3).

Un guardarraíl que nunca se ha visto saltar no es un guardarraíl: estos tests
provocan `LookaheadError` a propósito por las tres vías del enunciado, y además
comprueban la propiedad estructural de la que sale la garantía —que el extremo
de un impulso no se conoce hasta que cierra la vela contraria—.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from chronos.application.structure.causal import PriorBarAtr
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import MachineState, SeedMode
from chronos.domain.structure.errors import LookaheadError, StructureError
from tests.domain.structure.conftest import SYNTHETIC_DAY, make_bars, run_detector

# --- 1. El extremo de un ID que aún no está constituido ---------------------


def test_pedir_el_extremo_en_limbo_lanza_lookahead() -> None:
    """En limbo la pierna sigue avanzando: su extremo todavía no existe."""
    detector = run_detector(SYNTHETIC_DAY[:3])  # pierna bajista sin contraria
    assert detector.state is MachineState.LIMBO
    with pytest.raises(LookaheadError, match="todavía no está fijado"):
        detector.current_impulse_extreme()


def test_el_extremo_si_es_legible_una_vez_constituido() -> None:
    detector = run_detector(SYNTHETIC_DAY[:4])
    assert detector.state is MachineState.ID_VIGENTE
    assert detector.current_impulse_extreme() == pytest.approx(1970.00)


def test_tras_una_rotura_vuelve_a_ser_ilegible() -> None:
    detector = run_detector(SYNTHETIC_DAY[:7])  # b6 rompe y abre limbo
    assert detector.state is MachineState.LIMBO
    with pytest.raises(LookaheadError):
        detector.current_impulse_extreme()


# --- 2. El estado en un instante posterior a la última barra ----------------


def test_consultar_el_estado_en_el_futuro_lanza_lookahead(
    synthetic_day: DominantImpulseDetector,
) -> None:
    frontera = synthetic_day.states[-1].timestamp
    with pytest.raises(LookaheadError, match="posterior a la última barra"):
        synthetic_day.state_at(frontera + timedelta(minutes=1))


def test_consultar_el_estado_en_el_pasado_es_legitimo(
    synthetic_day: DominantImpulseDetector,
) -> None:
    en_limbo = synthetic_day.states[8].timestamp
    assert synthetic_day.state_at(en_limbo).state is MachineState.LIMBO
    # A mitad de barra se devuelve el último cierre disponible, no el siguiente.
    assert synthetic_day.state_at(en_limbo + timedelta(hours=1)).index == 8


def test_estado_anterior_al_inicio_de_la_serie_no_es_lookahead_sino_error(
    synthetic_day: DominantImpulseDetector,
) -> None:
    with pytest.raises(StructureError, match="anterior al inicio"):
        synthetic_day.state_at(datetime(2000, 1, 1, tzinfo=UTC))


# --- 3. Series rodantes (ATR) ----------------------------------------------


def _atr(bars: int = 40) -> PriorBarAtr:
    rng = np.arange(float(bars))
    high = 2000.0 + rng
    low = 1990.0 + rng
    close = 1995.0 + rng
    return PriorBarAtr(high, low, close, period=14, label="ATR(14) H4")


def test_leer_el_atr_mas_alla_de_la_frontera_lanza_lookahead() -> None:
    atr = _atr()
    atr.advance(20)
    assert np.isfinite(atr.at(20))
    with pytest.raises(LookaheadError, match="se pidió el índice 21"):
        atr.at(21)


def test_el_atr_de_una_barra_no_incluye_esa_barra() -> None:
    """El valor del índice `i` sólo puede depender de `[0, i-1]`.

    Se comprueba de la única forma que no admite discusión: cambiar el futuro y
    verificar que el pasado no se mueve.
    """
    bars = 40
    rng = np.arange(float(bars))
    high, low, close = 2000.0 + rng, 1990.0 + rng, 1995.0 + rng

    original = PriorBarAtr(high, low, close, period=14)
    alterado_high = high.copy()
    alterado_high[25:] += 500.0  # explosión de volatilidad a partir de la barra 25
    alterado = PriorBarAtr(alterado_high, low, close, period=14)

    for index in range(26):
        original.advance(index)
        alterado.advance(index)
        izquierda, derecha = original.at(index), alterado.at(index)
        if np.isnan(izquierda):
            assert np.isnan(derecha)
        else:
            assert izquierda == pytest.approx(derecha)


def test_la_frontera_de_una_serie_causal_no_retrocede() -> None:
    atr = _atr()
    atr.advance(10)
    with pytest.raises(StructureError, match="no retrocede"):
        atr.advance(9)


# --- Propiedad estructural: la regla del propietario ya es causal -----------


def test_el_extremo_no_depende_de_barras_posteriores_a_la_constitucion() -> None:
    """Procesar más barras no reescribe ningún impulso ya constituido."""
    parcial = run_detector(SYNTHETIC_DAY[:11])
    completo = run_detector(SYNTHETIC_DAY)
    for antes, despues in zip(parcial.impulses, completo.impulses, strict=False):
        assert antes.anchor == despues.anchor
        assert antes.extreme == despues.extreme
        assert antes.ts_constitution == despues.ts_constitution


def test_procesar_barra_a_barra_da_lo_mismo_que_de_golpe() -> None:
    de_golpe = run_detector(SYNTHETIC_DAY)
    incremental = DominantImpulseDetector(
        timeframe="H4", seed_mode=SeedMode.S1_FIRST_NON_DOJI, warmup_bars=0
    )
    for bar in make_bars(SYNTHETIC_DAY):
        incremental.process(bar)
    assert [impulse.extreme for impulse in incremental.impulses] == [
        impulse.extreme for impulse in de_golpe.impulses
    ]
