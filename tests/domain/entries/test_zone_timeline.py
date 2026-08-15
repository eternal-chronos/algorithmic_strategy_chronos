"""La línea temporal del UL (§7): qué zona estaba vigente en cada barra.

Es la garantía anti-lookahead más importante de la fase 3, y la menos evidente.
Con la rotura por zona un ID vigente **extiende su extremo**, y cada extensión
mueve su UL. La fase 2.0 guarda una sola zona por impulso —la que le quedó al
morir— así que usar ésa para observar sería mirar al futuro justo en las velas
que más importan: las que extienden el extremo son las mismas que tocan la zona.

La serie es `SYNTHETIC_BREAK_UP`, la de la fase 2.1, ya auditada: su ID#1 nace
con el UL en [2010, 2012], lo estira dos veces y muere en b6.
"""

from __future__ import annotations

import pytest

from chronos.application.entries.synthetic_run import frame_of
from chronos.domain.entries.synthetic_entries import SYNTHETIC_ENTRY_START
from chronos.domain.entries.zone_timeline import (
    LastZoneTimeline,
    break_direction,
    breaks,
    first_retest_after_break,
    touches,
)
from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import AnchorMode, ImpulseDirection, SeedMode
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.synthetic_break import SYNTHETIC_BREAK_UP
from chronos.domain.structure.zone_break import ZoneBreakLevels
from chronos.domain.structure.zones import CandleSeries, ZoneKind, last_zone


@pytest.fixture
def series() -> CandleSeries:
    return CandleSeries.of(frame_of(SYNTHETIC_BREAK_UP, SYNTHETIC_ENTRY_START, "4h"))


@pytest.fixture
def impulse(series: CandleSeries) -> DominantImpulse:
    detector = DominantImpulseDetector(
        timeframe="H4",
        anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY,
        seed_mode=SeedMode.S2_FIRST_COUNTER_BAR,
        warmup_bars=0,
        break_by_zone=True,
        zone_levels=ZoneBreakLevels(series, timeframe="H4"),
    )
    detector.process_all(
        [
            BodyBar(
                timestamp=series.at(position),
                open=float(series.open[position]),
                close=float(series.close[position]),
            )
            for position in range(len(series))
        ]
    )
    return detector.impulses[0]


# --- La traza de extensiones -------------------------------------------------


def test_el_detector_registra_cada_extension_del_extremo(
    impulse: DominantImpulse,
) -> None:
    """La traza es aditiva: no cambia ni un impulso, sólo permite reconstruir el UL."""
    assert impulse.extreme_extensions == 2
    assert [item.index for item in impulse.extension_trail] == [4, 5]
    assert [item.price for item in impulse.extension_trail] == [2011.00, 2011.50]
    assert impulse.extreme_at_constitution == 2010.00
    assert impulse.index_extreme_at_constitution == 2


# --- El UL barra a barra -----------------------------------------------------


def test_el_ul_vigente_cambia_con_cada_extension(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """Los tres UL que el ID#1 tuvo, cada uno en su tramo. Escritos a mano.

    b3 nace con [2010, 2012]; b4 estira el extremo a 2011 y el UL pasa a
    [2011, 2011.90]; b5 lo estira a 2011.50 y el UL pasa a [2011.50, 2014].
    """
    timeline = LastZoneTimeline(series, impulse)
    for bar in range(impulse.index_constitution, 7):
        timeline.advance(bar)

    assert _borders(timeline, 3) == (2010.00, 2012.00)
    assert _borders(timeline, 4) == (2011.00, 2011.90)
    assert _borders(timeline, 5) == (2011.50, 2014.00)


def test_la_vela_de_margen_solo_cuenta_cuando_ha_cerrado(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """Ninguna vela puede cerrar más allá de su propia mecha.

    En b5 el extremo se estira y el UL es [2011.50, 2014] por la mecha de la
    propia b5. En b6 la mecha de b6 llega a 2016, y sólo **desde b6 cerrada** el
    borde exterior pasa a 2016. Juzgar b6 contra 2016 habría sido juzgarla contra
    un borde que ella misma acababa de fijar.
    """
    timeline = LastZoneTimeline(series, impulse)
    for bar in range(impulse.index_constitution, len(series)):
        timeline.advance(bar)

    assert _borders(timeline, 5) == (2011.50, 2014.00)
    assert _borders(timeline, 6) == (2011.50, 2016.00)


def test_al_final_de_su_vida_coincide_con_la_zona_de_la_fase_20(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """La línea temporal no inventa una zona nueva: reconstruye la misma."""
    timeline = LastZoneTimeline(series, impulse)
    for bar in range(impulse.index_constitution, len(series)):
        timeline.advance(bar)
    published = last_zone(
        series,
        id_num=impulse.id_num,
        timeframe=impulse.timeframe,
        direction=impulse.direction,
        index_extreme=impulse.index_extreme,
        ts_constitution=impulse.ts_constitution,
    )
    final = timeline.at(len(series) - 1)

    assert (final.inner, final.outer) == (published.inner, published.outer)


def test_los_tramos_cubren_la_vida_del_id_sin_huecos(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    segments = LastZoneTimeline(series, impulse).segments

    assert [segment.first for segment in segments] == [3, 4, 5]
    assert segments[0].last == 3
    assert segments[1].last == 4
    assert segments[-1].last == impulse.index_end


# --- Causalidad (§7) ---------------------------------------------------------


def test_pedir_el_ul_antes_de_que_el_id_exista_es_lookahead(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(len(series) - 1)
    with pytest.raises(LookaheadError):
        timeline.at(impulse.index_constitution - 1)


def test_pedir_el_ul_de_una_barra_que_no_ha_cerrado_es_lookahead(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(impulse.index_constitution)
    with pytest.raises(LookaheadError):
        timeline.at(impulse.index_constitution + 1)


def test_la_frontera_no_retrocede(series: CandleSeries, impulse: DominantImpulse) -> None:
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(5)
    with pytest.raises(StructureError):
        timeline.advance(4)


# --- Contacto y rotura -------------------------------------------------------


def test_tocar_es_alcanzar_cualquier_punto_de_la_zona(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """Basta el contacto: no hace falta cerrar dentro (§1.1)."""
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(3)
    zone = timeline.at(3)  # [2010, 2012]

    assert touches(zone, high=2011.0, low=2008.0) is True  # entra por abajo
    assert touches(zone, high=2010.0, low=2009.0) is True  # roza el borde interior
    assert touches(zone, high=2009.0, low=2008.0) is False  # se queda corta


def test_romper_es_cerrar_mas_alla_del_borde_exterior(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """La misma regla de la fase 2.1: atravesarla entera."""
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(3)
    zone = timeline.at(3)  # [2010, 2012], alcista

    assert breaks(zone, close=2012.01) is True
    assert breaks(zone, close=2012.00) is False  # el borde no basta: es estricto
    assert breaks(zone, close=2011.00) is False  # cerrar dentro no rompe


# --- Retesteo: la vela que rompe cierra primero ------------------------------


def _broken_zone(series: CandleSeries, impulse: DominantImpulse) -> object:
    """El UL contra el que se juzgó b6, que es el vigente al cierre de b5."""
    timeline = LastZoneTimeline(series, impulse)
    for bar in range(impulse.index_constitution, 6):
        timeline.advance(bar)
    return timeline.at(5)  # [2011.50, 2014]


def test_la_vela_que_rompe_no_se_retestea_a_si_misma(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """b6 atraviesa la zona entera, así que pasa por ella: no puede valer.

    Es la razón de ser de la regla. Si el retesteo pudiera ocurrir en la misma
    vela que rompió, **toda** rotura vendría con retesteo gratis —incluidos los
    toques de M15 o M1 de dentro de esa vela, que son los mismos precios— y la
    invalidación del §1.2 no descartaría nunca nada.
    """
    zone = _broken_zone(series, impulse)

    assert touches(zone, float(series.high[6]), float(series.low[6])) is True
    assert first_retest_after_break(series, zone, index_break=6, through=9) == 7


def test_la_ventana_del_retesteo_no_incluye_la_vela_de_la_rotura(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    zone = _broken_zone(series, impulse)

    assert first_retest_after_break(series, zone, index_break=6, through=6) is None


def test_sin_ninguna_vela_posterior_que_toque_no_hay_retesteo(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """b8 y b9 se quedan por encima del borde exterior: la observación muere."""
    zone = _broken_zone(series, impulse)

    assert touches(zone, float(series.high[8]), float(series.low[8])) is False
    assert touches(zone, float(series.high[9]), float(series.low[9])) is False
    assert first_retest_after_break(series, zone, index_break=7, through=9) is None


def test_la_ventana_no_se_sale_de_la_serie(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """Con `through` más allá del histórico se para en la última vela, no revienta."""
    zone = _broken_zone(series, impulse)

    assert first_retest_after_break(series, zone, index_break=6, through=10_000) == 7


def test_el_ul_se_cruza_a_favor_y_el_ob_en_contra(
    series: CandleSeries, impulse: DominantImpulse
) -> None:
    """De qué lado se rompe cada zona sale de la dirección, no de comparar bordes.

    En un ID de rango no positivo los dos bordes salen invertidos y compararlos
    daría la respuesta contraria.
    """
    timeline = LastZoneTimeline(series, impulse)
    timeline.advance(3)
    ul = timeline.at(3)

    assert ul.kind is ZoneKind.LAST
    assert break_direction(ul) is ImpulseDirection.ALCISTA


def _borders(timeline: LastZoneTimeline, bar: int) -> tuple[float, float]:
    zone = timeline.at(bar)
    return zone.inner, zone.outer
