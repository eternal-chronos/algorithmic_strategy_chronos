"""Día sintético calculado a mano: el test más valioso de la fase 1 (§4).

La serie está en `conftest.SYNTHETIC_DAY` y el resultado esperado se escribió a
mano **antes** de correr el motor. Cada aserción numérica de este fichero es una
comprobación contra ese cálculo manual, no contra la salida del código.

Los nueve casos obligatorios del §4 están cubiertos aquí:
  1. pierna bajista limpia + contraria -> ID bajista  ......... `test_id_bajista_*`
  2. limbo de 3 barras sin contraria  ........................ `test_limbo_de_tres_barras`
  3. contraria de 1 céntimo  ................................. `test_contraria_diminuta_*`
  4. doji en posición de constituir  ......................... `test_doji_no_constituye`
  5. ROTURA_A_FAVOR  ......................................... `test_eventos_de_rotura`
  6. ROTURA_EN_CONTRA  ....................................... `test_eventos_de_rotura`
  7. retroceso profundo dentro del rango  .................... `test_retroceso_profundo_*`
  8. barra que rompe y es contraria a la vez  ................ `test_rotura_antes_que_*`
  9. hueco de fin de semana  ................................. `test_hueco_de_fin_de_semana_*`
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import (
    BreakKind,
    ImpulseDirection,
    MachineState,
)
from tests.domain.structure.conftest import (
    H4,
    START,
    SYNTHETIC_DAY,
    SYNTHETIC_DAY_GAP_AFTER,
    run_detector,
)

#: Viernes 20:00 -> domingo 20:00: el mercado del oro cierra el fin de semana.
WEEKEND_GAP = timedelta(days=1, hours=20)


def stamp(index: int) -> datetime:
    """Marca de tiempo esperada de una barra, con el hueco de fin de semana."""
    offset = WEEKEND_GAP if index > SYNTHETIC_DAY_GAP_AFTER else timedelta(0)
    return START + H4 * index + offset


# --- Resultado global -------------------------------------------------------


def test_numero_de_impulsos_y_eventos(synthetic_day: DominantImpulseDetector) -> None:
    assert len(synthetic_day.impulses) == 5
    assert len(synthetic_day.events) == 4
    assert synthetic_day.diagnostics["dojis"] == 1
    assert synthetic_day.diagnostics["roturas_a_favor"] == 3
    assert synthetic_day.diagnostics["roturas_en_contra"] == 1


def test_tabla_de_impulsos_calculada_a_mano(synthetic_day: DominantImpulseDetector) -> None:
    """Ancla, extremo, dirección y momento de cada uno de los cinco impulsos."""
    esperado = [
        # (dirección, ancla A2, extremo, barra de constitución, barras de limbo)
        (ImpulseDirection.BAJISTA, 2000.00, 1970.00, 3, 3),
        (ImpulseDirection.BAJISTA, 1997.00, 1950.00, 10, 3),
        (ImpulseDirection.BAJISTA, 1950.01, 1945.00, 14, 2),
        (ImpulseDirection.ALCISTA, 1945.50, 1951.00, 16, 0),
        (ImpulseDirection.ALCISTA, 1955.00, 1960.00, 19, 1),
    ]
    assert len(synthetic_day.impulses) == len(esperado)
    for impulse, (direction, anchor, extreme, bar, limbo) in zip(
        synthetic_day.impulses, esperado, strict=True
    ):
        assert impulse.direction is direction
        assert impulse.anchor == pytest.approx(anchor)
        assert impulse.extreme == pytest.approx(extreme)
        assert impulse.ts_constitution == stamp(bar)
        assert impulse.limbo_bars == limbo


def test_barras_vigentes_de_cada_impulso(synthetic_day: DominantImpulseDetector) -> None:
    ultimo = len(SYNTHETIC_DAY) - 1
    assert [impulse.bars_alive(ultimo) for impulse in synthetic_day.impulses] == [3, 1, 1, 1, 0]
    assert [impulse.state for impulse in synthetic_day.impulses] == [
        "CERRADO", "CERRADO", "CERRADO", "CERRADO", "VIGENTE",
    ]


# --- Caso 1: pierna bajista limpia + vela contraria -------------------------


def test_id_bajista_se_constituye_en_la_vela_contraria(
    synthetic_day: DominantImpulseDetector,
) -> None:
    primero = synthetic_day.impulses[0]
    assert primero.direction is ImpulseDirection.BAJISTA
    # El ancla es el techo del cuerpo donde arrancó la pierna (b0), no el máximo
    # de las mechas ni el cierre anterior.
    assert primero.anchor_a2 == pytest.approx(2000.00)
    # El extremo se fija con lo alcanzado hasta b2, la vela ANTERIOR a la contraria.
    assert primero.extreme == pytest.approx(1970.00)
    assert primero.ts_constitution == stamp(3)
    assert primero.ts_leg_start == stamp(0)


def test_id_bajista_no_existe_antes_de_la_vela_contraria() -> None:
    """Momento 1 y momento 2 son distintos: la pierna sola no crea nada."""
    detector = run_detector(SYNTHETIC_DAY[:3])
    assert detector.impulses == ()
    assert detector.state is MachineState.LIMBO


# --- Caso 2: limbo de tres barras -------------------------------------------


def test_limbo_de_tres_barras(synthetic_day: DominantImpulseDetector) -> None:
    """Tras romper en b6, b7/b8/b9 siguen a favor y NO hay ID en ninguna."""
    for index in (6, 7, 8, 9):
        estado = synthetic_day.states[index]
        assert estado.state is MachineState.LIMBO, f"la barra {index} debería estar en limbo"
        assert estado.impulse_id is None
        assert estado.leg_direction is ImpulseDirection.BAJISTA
    assert synthetic_day.impulses[1].limbo_bars == 3
    assert synthetic_day.states[10].state is MachineState.ID_VIGENTE


# --- Caso 3: vela contraria diminuta ----------------------------------------


def test_contraria_diminuta_constituye_igual_que_una_grande(
    synthetic_day: DominantImpulseDetector,
) -> None:
    """Un cuerpo de 1 céntimo crea el ID: no hay umbral de tamaño (§2.2)."""
    segundo = synthetic_day.impulses[1]
    assert segundo.constituting_body_size == pytest.approx(0.01)
    assert segundo.ts_constitution == stamp(10)
    assert segundo.extreme == pytest.approx(1950.00)


def test_contraria_diminuta_y_grande_dan_el_mismo_id() -> None:
    minuscula = run_detector([(2000.0, 1990.0), (1990.0, 1980.0), (1980.0, 1980.01)])
    grande = run_detector([(2000.0, 1990.0), (1990.0, 1980.0), (1980.0, 1995.0)])
    assert len(minuscula.impulses) == len(grande.impulses) == 1
    assert minuscula.impulses[0].anchor == grande.impulses[0].anchor
    assert minuscula.impulses[0].extreme == grande.impulses[0].extreme
    assert minuscula.impulses[0].ts_constitution == grande.impulses[0].ts_constitution


# --- Caso 4: doji ------------------------------------------------------------


def test_doji_no_constituye_y_el_limbo_continua(synthetic_day: DominantImpulseDetector) -> None:
    assert synthetic_day.states[12].state is MachineState.LIMBO
    assert synthetic_day.states[12].impulse_id is None
    # El ID nace dos barras después, en b14, no en el doji.
    assert synthetic_day.impulses[2].ts_constitution == stamp(14)


def test_doji_aislado_no_crea_ni_rompe_nada() -> None:
    detector = run_detector([(2000.0, 1990.0), (1990.0, 1990.0), (1990.0, 1980.0)])
    assert detector.impulses == ()
    assert detector.diagnostics["dojis"] == 1


# --- Casos 5 y 6: los dos tipos de rotura -----------------------------------


def test_eventos_de_rotura(synthetic_day: DominantImpulseDetector) -> None:
    esperado = [
        # (barra, tipo, id roto, dirección de la pierna que queda en curso)
        (6, BreakKind.A_FAVOR, 1, ImpulseDirection.BAJISTA),
        (11, BreakKind.A_FAVOR, 2, ImpulseDirection.BAJISTA),
        (15, BreakKind.EN_CONTRA, 3, ImpulseDirection.ALCISTA),
        (17, BreakKind.A_FAVOR, 4, ImpulseDirection.ALCISTA),
    ]
    for event, (bar, kind, broken, new_direction) in zip(
        synthetic_day.events, esperado, strict=True
    ):
        assert event.timestamp == stamp(bar)
        assert event.kind is kind
        assert event.broken_id_num == broken
        assert event.new_leg_direction is new_direction


def test_rotura_a_favor_mantiene_la_direccion(synthetic_day: DominantImpulseDetector) -> None:
    roto = synthetic_day.impulses[0]
    nuevo = synthetic_day.impulses[1]
    assert roto.exit_break_kind is BreakKind.A_FAVOR
    assert nuevo.direction is roto.direction


def test_rotura_en_contra_invierte_la_direccion(synthetic_day: DominantImpulseDetector) -> None:
    roto = synthetic_day.impulses[2]
    nuevo = synthetic_day.impulses[3]
    assert roto.exit_break_kind is BreakKind.EN_CONTRA
    assert nuevo.direction is roto.direction.opposite()
    # §2.5: el ancla del nuevo ID sale del arranque de la pierna, no del extremo
    # del ID muerto. El suelo del nuevo alcista queda por encima del suelo roto.
    assert nuevo.anchor > roto.extreme


def test_rotura_es_estricta_tocar_el_nivel_no_rompe() -> None:
    """Cerrar exactamente en el extremo no es cerrar "más allá" de él."""
    justo = run_detector(
        [(2000.0, 1990.0), (1990.0, 1980.0), (1980.0, 1985.0), (1985.0, 1980.0)]
    )
    assert justo.events == ()  # cierre en 1980.00, el extremo exacto
    mas_alla = run_detector(
        [(2000.0, 1990.0), (1990.0, 1980.0), (1980.0, 1985.0), (1985.0, 1979.99)]
    )
    assert len(mas_alla.events) == 1


# --- Caso 7: retroceso profundo ---------------------------------------------


def test_retroceso_profundo_no_emite_nada(synthetic_day: DominantImpulseDetector) -> None:
    """b4 y b5 se acercan a los dos límites sin cruzarlos: el ID sigue vivo."""
    for index in (4, 5):
        estado = synthetic_day.states[index]
        assert estado.state is MachineState.ID_VIGENTE
        assert estado.impulse_id == 1
    assert synthetic_day.events[0].index == 6
    primero = synthetic_day.impulses[0]
    assert primero.contains(1998.00) and primero.contains(1972.00)


# --- Caso 8: la barra que rompe es además contraria -------------------------


def test_rotura_antes_que_constitucion(synthetic_day: DominantImpulseDetector) -> None:
    """b17 rompe a favor con cuerpo bajista, contrario a la pierna alcista nueva.

    Orden documentado: el estado se evalúa al principio de la barra, así que b17
    sólo rompe. La constitución llega en b19, no en b17.
    """
    assert synthetic_day.states[17].state is MachineState.LIMBO
    assert synthetic_day.states[17].impulse_id is None
    constituidos_hasta_b17 = [
        impulse for impulse in synthetic_day.impulses if impulse.index_constitution <= 17
    ]
    assert len(constituidos_hasta_b17) == 4  # ninguno nuevo en b17
    quinto = synthetic_day.impulses[4]
    assert quinto.ts_constitution == stamp(19)
    # La pierna arranca en la propia barra de rotura: era contraria a la pierna.
    assert quinto.index_leg_start == 17
    assert quinto.anchor_a2 == pytest.approx(1955.00)
    assert quinto.extreme == pytest.approx(1960.00)


def test_constitucion_inmediata(synthetic_day: DominantImpulseDetector) -> None:
    """b16 es contraria justo después de la rotura de b15: limbo de cero barras."""
    cuarto = synthetic_day.impulses[3]
    assert cuarto.limbo_bars == 0
    assert cuarto.index_constitution == 16
    assert synthetic_day.states[15].state is MachineState.LIMBO


# --- Caso 9: hueco de fin de semana -----------------------------------------


def test_hueco_de_fin_de_semana_no_altera_el_estado(
    synthetic_day: DominantImpulseDetector,
) -> None:
    """El hueco cae entre b5 y b6, justo donde se rompe el primer ID."""
    assert synthetic_day.states[5].timestamp.weekday() == 4  # viernes
    assert synthetic_day.states[6].timestamp.weekday() == 6  # domingo
    salto = synthetic_day.states[6].timestamp - synthetic_day.states[5].timestamp
    assert salto == timedelta(days=2)
    # El ID#1 se rompe con la barra del domingo como si no hubiera hueco.
    assert synthetic_day.events[0].timestamp == synthetic_day.states[6].timestamp
    assert synthetic_day.impulses[0].ts_end == synthetic_day.states[6].timestamp


def test_hueco_no_cambia_el_resultado_frente_a_la_serie_continua() -> None:
    """Sin hueco, los mismos cuerpos producen exactamente los mismos impulsos."""
    continua = run_detector(SYNTHETIC_DAY)
    con_hueco = run_detector(SYNTHETIC_DAY, gap_after=SYNTHETIC_DAY_GAP_AFTER)
    assert [
        (impulse.direction, impulse.anchor, impulse.extreme, impulse.index_constitution)
        for impulse in continua.impulses
    ] == [
        (impulse.direction, impulse.anchor, impulse.extreme, impulse.index_constitution)
        for impulse in con_hueco.impulses
    ]


# --- Determinismo y orden ---------------------------------------------------


def test_misma_entrada_misma_salida() -> None:
    primero = run_detector(SYNTHETIC_DAY)
    segundo = run_detector(SYNTHETIC_DAY)
    assert [impulse.anchor for impulse in primero.impulses] == [
        impulse.anchor for impulse in segundo.impulses
    ]
    assert primero.diagnostics == segundo.diagnostics


def test_barras_fuera_de_orden_son_un_error() -> None:
    from chronos.domain.structure.body import BodyBar
    from chronos.domain.structure.errors import StructureError

    detector = DominantImpulseDetector(timeframe="H4", warmup_bars=0)
    detector.process(BodyBar(timestamp=datetime(2024, 3, 8, 4, tzinfo=UTC), open=1.0, close=2.0))
    with pytest.raises(StructureError, match="fuera de orden"):
        detector.process(
            BodyBar(timestamp=datetime(2024, 3, 8, 0, tzinfo=UTC), open=1.0, close=2.0)
        )


# --- Nadie nace roto: la contraria que rompe en vez de constituir ------------

#: Pierna bajista limpia y una vela verde enorme que se la traga entera. Sin la
#: regla, esa vela constituía un ID bajista con el ancla en 95 y cerraba en 120:
#: nacía roto, la rotura no se juzgaba hasta b5 —que ya vuelve dentro— y el ID
#: sobrevivía apuntando al revés que el precio. Escrito a mano:
#:   b0 (100→90) bajista · semilla
#:   b1 (90→95)  contraria -> ID#1 bajista [ancla 100, extremo 90]
#:   b2 (95→85)  cierra bajo 90 -> ROTURA_A_FAVOR; abre pierna bajista [ancla 95]
#:   b3 (85→80)  estira el extremo a 80
#:   b4 (80→120) contraria y ENORME: 120 > 95, el ID nacería roto -> no constituye
#:   b5 (120→118) contraria a la pierna alcista -> ID#2 ALCISTA [ancla 80, extremo 120]
BORN_BROKEN = (
    (100.0, 90.0),
    (90.0, 95.0),
    (95.0, 85.0),
    (85.0, 80.0),
    (80.0, 120.0),
    (120.0, 118.0),
)


def test_la_contraria_que_dejaria_el_id_roto_no_constituye() -> None:
    detector = run_detector(BORN_BROKEN)

    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 1
    assert [impulse.index_constitution for impulse in detector.impulses] == [1, 5]
    # Ni un ID bajista nuevo en b4: la vela que se tragó la pierna no da a luz.
    assert detector.states[4].state is MachineState.LIMBO
    assert detector.states[4].impulse_id is None


def test_la_constitucion_abortada_queda_registrada_entera() -> None:
    aborted = run_detector(BORN_BROKEN).aborted_constitutions

    assert len(aborted) == 1
    fallida = aborted[0]
    assert fallida.index == 4
    assert fallida.timestamp == stamp(4)
    assert fallida.aborted_direction is ImpulseDirection.BAJISTA
    assert fallida.new_leg_direction is ImpulseDirection.ALCISTA
    assert fallida.close == pytest.approx(120.00)
    # Sin zonas manda la línea del ancla y las dos cifras coinciden.
    assert fallida.level == pytest.approx(95.00)
    assert fallida.line == pytest.approx(95.00)


def test_la_vela_abortada_gira_la_pierna_y_ancla_el_id_siguiente() -> None:
    """No se pierde: hace de vela de rotura, así que arranca la pierna contraria."""
    detector = run_detector(BORN_BROKEN)

    assert detector.states[4].leg_direction is ImpulseDirection.ALCISTA
    segundo = detector.impulses[1]
    assert segundo.direction is ImpulseDirection.ALCISTA
    assert segundo.index_leg_start == 4
    assert segundo.index_anchor == 4
    assert segundo.anchor == pytest.approx(80.00)
    assert segundo.extreme == pytest.approx(120.00)


def test_cerrar_justo_en_el_ancla_sigue_constituyendo() -> None:
    """"Más allá" es estricto también aquí: en el nivel exacto el ID sí nace."""
    en_el_nivel = (*BORN_BROKEN[:4], (80.0, 95.0), (95.0, 94.0))
    detector = run_detector(en_el_nivel)

    assert detector.diagnostics["constituciones_abortadas_por_nacer_roto"] == 0
    assert not detector.aborted_constitutions
    segundo = detector.impulses[1]
    assert segundo.direction is ImpulseDirection.BAJISTA
    assert segundo.index_constitution == 4
    assert segundo.anchor == pytest.approx(95.00)
