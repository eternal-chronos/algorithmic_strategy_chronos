"""El día sintético del §8 sobre la cascada entera, con los esperados a mano.

Las velas de H4 se escriben a mano y todo lo demás recorre el motor de verdad:
se parten minuto a minuto, se agregan con el agregador del proyecto, se detectan
los impulsos con el detector de la fase 2.1 y se calculan las zonas con el de la
2.0. Si el sintético pasara con un montaje propio y el histórico real fallara, la
prueba no habría probado nada.

Los casos que se fijan aquí son los del §8 que dependen de las cuatro
temporalidades: 4 (rotura y retesteo), 5 (rotura sin retesteo), 6 (el OB no tiene
variante de retesteo), 7 y 8 (el contexto diario) y 10 (los espejos).
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries.cascade import build_cascade, daily_context
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.synthetic_run import build_synthetic
from chronos.domain.entries.enums import DailyContext, GuardRail, Outcome
from chronos.domain.entries.signal import DailyTouch
from chronos.domain.entries.synthetic_entries import (
    H4_NO_RETEST_DOWN,
    H4_NO_RETEST_UP,
    H4_ORDER_BLOCK_BROKEN_DOWN,
    H4_ORDER_BLOCK_BROKEN_UP,
    H4_RETEST_DOWN,
    H4_RETEST_UP,
)
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

UP = ImpulseDirection.ALCISTA
DOWN = ImpulseDirection.BAJISTA


def _retests(cascade: object) -> list[object]:
    return [
        item
        for item in cascade.observations  # type: ignore[attr-defined]
        if item.outcome is Outcome.ROTURA_Y_RETESTEO
    ]


def _rails(cascade: object) -> set[str]:
    return {item.guard_rail.value for item in cascade.discarded}  # type: ignore[attr-defined]


# --- El sintético reproduce las velas escritas a mano ------------------------


def test_las_cuatro_temporalidades_son_la_misma_historia() -> None:
    """H1, M15 y M1 recomponen las velas de H4, y el diario las agrupa de seis en seis.

    Sin esto la prueba estaría midiendo la derivación de velas y no la cascada.
    La consistencia de `explode` se fija aparte, en `tests/domain/entries`.
    """
    synthetic = build_synthetic(H4_RETEST_UP)
    h4 = synthetic.bars["H4"]

    assert len(h4) == len(H4_RETEST_UP)
    for position, (open_, high, low, close) in enumerate(H4_RETEST_UP):
        row = h4.iloc[position]
        assert (row["open"], row["high"], row["low"], row["close"]) == pytest.approx(
            (open_, high, low, close)
        )
    for timeframe, factor in (("H1", 4), ("M15", 16)):
        smaller = synthetic.bars[timeframe]
        assert len(smaller) == len(h4) * factor
        for position in range(len(h4)):
            chunk = smaller.iloc[position * factor : (position + 1) * factor]
            assert chunk["open"].iloc[0] == pytest.approx(h4["open"].iloc[position])
            assert chunk["close"].iloc[-1] == pytest.approx(h4["close"].iloc[position])
            assert chunk["high"].max() == pytest.approx(h4["high"].iloc[position])
            assert chunk["low"].min() == pytest.approx(h4["low"].iloc[position])


# --- Caso 4 del §8 -----------------------------------------------------------


def test_el_ul_roto_y_retesteado_produce_observacion() -> None:
    """b6 atraviesa el UL [2011.50, 2014] y b7 vuelve a meterse dentro."""
    retests = _retests(build_synthetic(H4_RETEST_UP).cascade)

    assert len(retests) == 1
    assert retests[0].zone is ZoneKind.LAST
    assert retests[0].index_break == 6
    assert retests[0].index_retest == 7


def test_se_opera_a_favor_de_la_rotura() -> None:
    """En un ID alcista la rotura a favor es al alza: se opera al alza."""
    retest = _retests(build_synthetic(H4_RETEST_UP).cascade)[0]
    assert retest.direction is UP


def test_la_zona_que_se_retestea_es_la_que_se_rompio() -> None:
    """No la redibujada con la vela de la rotura, que la agrandaría hasta 2016.

    La vela que rompe el UL suele ser también su vela de margen: redibujar la
    zona con ella la estira hasta contener el cierre que la atravesó, y el
    retesteo pasaría a medirse contra un nivel que nunca gobernó.
    """
    retest = _retests(build_synthetic(H4_RETEST_UP).cascade)[0]

    assert (retest.zone_inner, retest.zone_outer) == (2011.50, 2014.00)


def test_el_retesteo_produce_senal() -> None:
    signals = [
        signal
        for signal in build_synthetic(H4_RETEST_UP).cascade.signals
        if signal.observation.outcome is Outcome.ROTURA_Y_RETESTEO
    ]
    assert signals
    assert all(signal.direction is UP for signal in signals)


# --- Caso 5 del §8 -----------------------------------------------------------


def test_sin_retesteo_la_observacion_muere() -> None:
    """b7 se queda en 2014.50, por encima del borde: no toca y no vuelve."""
    cascade = build_synthetic(H4_NO_RETEST_UP).cascade

    assert _retests(cascade) == []
    assert GuardRail.ROTURA_SIN_RETESTEO.value in _rails(cascade)


def test_sin_retesteo_no_hay_operacion_de_esa_rama() -> None:
    cascade = build_synthetic(H4_NO_RETEST_UP).cascade
    assert not [
        signal
        for signal in cascade.signals
        if signal.observation.outcome is Outcome.ROTURA_Y_RETESTEO
    ]


def test_un_toque_en_temporalidad_menor_dentro_de_la_vela_que_rompio_no_vale() -> None:
    """La vela que rompe tiene que CERRAR primero, y el retesteo lo hace otra.

    b6 atraviesa el UL [2011.50, 2014] y por tanto pasa por él: dentro de esa
    vela de H4 hay velas de H1 y de M15 que lo tocan, y ninguna vale. El
    retesteo sólo lo valida una vela de la temporalidad del ID —H4— posterior, y
    en esta serie no la hay: b7 se queda en 2014.50.

    Sin la regla el resultado sería el contrario en toda la población: la vela
    que atraviesa una zona entera siempre la toca, así que ninguna rotura
    llegaría a morir por `rotura_sin_retesteo`.
    """
    synthetic = build_synthetic(H4_NO_RETEST_UP)
    inner, outer = 2011.50, 2014.00

    def _touching(timeframe: str, first: int, last: int) -> int:
        bars = synthetic.bars[timeframe].iloc[first:last]
        return int(((bars["low"] <= outer) & (bars["high"] >= inner)).sum())

    # Dentro de la vela que rompe (b6) las temporalidades menores tocan la zona…
    assert _touching("H1", 6 * 4, 7 * 4) > 0
    assert _touching("M15", 6 * 16, 7 * 16) > 0
    # …y aun así la observación muere sin retesteo.
    assert _retests(synthetic.cascade) == []
    assert GuardRail.ROTURA_SIN_RETESTEO.value in _rails(synthetic.cascade)


# --- Caso 6 del §8 -----------------------------------------------------------


def test_en_el_ob_la_rotura_invalida_siempre() -> None:
    """§1.2, literal: en el OB no hay variante de retesteo y no se inventa una."""
    cascade = build_synthetic(H4_ORDER_BLOCK_BROKEN_UP).cascade
    broken = [
        item
        for item in cascade.discarded
        if item.guard_rail is GuardRail.OB_ROTO
    ]

    assert len(broken) == 1
    assert broken[0].observation.zone is ZoneKind.ORDER_BLOCK
    assert not [
        item for item in _retests(cascade) if item.zone is ZoneKind.ORDER_BLOCK
    ]


def test_el_ob_roto_si_llego_a_estar_en_observacion() -> None:
    """Muere de rotura, no de no haberse tocado nunca: c4 sí lo tocó.

    El contacto se busca entre las observaciones **y** entre las descartadas: en
    la fase 3.2 un contacto que no llega a rechazo no abre observación, y aun así
    sigue siendo el mismo contacto. Lo que se fija aquí es que el OB **del ID#1**
    se tocó en c4, y eso no depende del modo de entrada.

    El otro contacto con un OB de esta serie es el del **ID#2** en c8 —la vela
    del caso 6, que entra en su OB [2004, 2012] y sobrevive porque está
    confirmado— y no tiene nada que ver con la rotura de arriba. Va en el
    esperado con su `id_num` para que no se pueda confundir con ella.
    """
    cascade = build_synthetic(H4_ORDER_BLOCK_BROKEN_UP).cascade
    contacts = {
        (item.id_num, item.index_contact)
        for item in cascade.observations
        if item.zone is ZoneKind.ORDER_BLOCK
    } | {
        (item.observation.id_num, item.observation.index_contact)
        for item in cascade.discarded
        if item.observation.zone is ZoneKind.ORDER_BLOCK
    }

    assert contacts == {(1, 4), (2, 8)}


# --- Casos 7 y 8 del §8 ------------------------------------------------------


@pytest.fixture
def touch() -> DailyTouch:
    return DailyTouch(
        id_num=7,
        direction=UP,
        zone=ZoneKind.LAST,
        index=0,
        timestamp=pd.Timestamp("2024-03-04", tz="UTC").to_pydatetime(),
        index_expiry=9,
        ts_expiry=pd.Timestamp("2024-03-20", tz="UTC").to_pydatetime(),
    )


def test_contexto_diario_a_favor(touch: DailyTouch) -> None:
    at = pd.Timestamp("2024-03-10", tz="UTC")
    context, found = daily_context((touch,), at, UP)

    assert context is DailyContext.A_FAVOR
    assert found is touch


def test_el_diario_contra_h4_manda_h4_y_se_registra(touch: DailyTouch) -> None:
    """§1.1 — el conflicto se registra en la señal; **no la descarta**.

    Es una afirmación del propietario que el desglose del §5.1 puede desmentir, y
    para desmentirla hacen falta las operaciones en conflicto, no su ausencia.
    """
    at = pd.Timestamp("2024-03-10", tz="UTC")
    context, found = daily_context((touch,), at, DOWN)

    assert context is DailyContext.CONFLICTO
    assert context.is_active is True
    assert found is touch


def test_muerto_el_id_diario_se_acaba_el_contexto(touch: DailyTouch) -> None:
    at = pd.Timestamp("2024-03-25", tz="UTC")
    context, found = daily_context((touch,), at, UP)

    assert context is DailyContext.SIN_CONTEXTO
    assert found is None


def test_el_contexto_no_existe_antes_de_su_contacto(touch: DailyTouch) -> None:
    at = pd.Timestamp("2024-03-01", tz="UTC")
    assert daily_context((touch,), at, UP)[0] is DailyContext.SIN_CONTEXTO


def test_sin_contacto_no_hay_contexto() -> None:
    at = pd.Timestamp("2024-03-10", tz="UTC")
    assert daily_context((), at, UP)[0] is DailyContext.SIN_CONTEXTO


# --- Caso 10 del §8: los espejos ---------------------------------------------


@pytest.mark.parametrize(
    ("up", "down"),
    [
        (H4_RETEST_UP, H4_RETEST_DOWN),
        (H4_NO_RETEST_UP, H4_NO_RETEST_DOWN),
        (H4_ORDER_BLOCK_BROKEN_UP, H4_ORDER_BLOCK_BROKEN_DOWN),
    ],
)
def test_el_espejo_produce_la_historia_espejo(
    up: tuple[tuple[float, float, float, float], ...],
    down: tuple[tuple[float, float, float, float], ...],
) -> None:
    """Todas las desigualdades del módulo son estrictas y la reflexión las invierte
    a la vez, así que cualquier asimetría es un fallo del código."""
    straight = build_synthetic(up).cascade
    reflected = build_synthetic(down).cascade

    assert len(straight.observations) == len(reflected.observations)
    for left, right in zip(straight.observations, reflected.observations, strict=True):
        assert left.id_num == right.id_num
        assert left.zone is right.zone
        assert left.outcome is right.outcome
        assert left.index_contact == right.index_contact
        assert left.index_retest == right.index_retest
        assert left.direction is right.direction.opposite()
    assert _rails(straight) == _rails(reflected)


# --- El interruptor y el guardarraíl de la estructura ------------------------


def test_con_las_senales_apagadas_no_se_emite_nada() -> None:
    synthetic = build_synthetic(H4_RETEST_UP)
    off = build_cascade(
        synthetic.run, synthetic.zones, synthetic.bars["M15"], EntriesConfig(enabled=False)
    )

    assert off.emits_nothing
    assert off.observations == ()
    assert off.signals == ()
    assert off.discarded == ()
    assert off.funnel == {}


def test_la_cascada_se_niega_a_correr_sobre_la_rotura_por_linea() -> None:
    """No es lookahead en el tiempo: es lookahead de reglas, y se corta igual de seco.

    Con `break_by_zone: false` las zonas no deciden nada, así que observar una
    zona rota leería una historia que ese motor nunca vivió.
    """
    from dataclasses import replace

    from chronos.application.structure.detect_impulses import DetectDominantImpulses
    from chronos.application.structure.zones import detect_zones

    synthetic = build_synthetic(H4_RETEST_UP)
    line = replace(
        synthetic.run.config,
        rules=replace(synthetic.run.config.rules, break_by_zone=False),
    )
    by_line = DetectDominantImpulses(line).execute(
        {timeframe: synthetic.bars[timeframe] for timeframe in line.charts.detected}
    )

    with pytest.raises(DomainError, match="break_by_zone"):
        build_cascade(
            by_line,
            detect_zones(by_line, line),
            synthetic.bars["M15"],
            EntriesConfig(enabled=True, allow_missing_ask=True),
        )
