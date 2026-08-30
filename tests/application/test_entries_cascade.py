"""La cascada H4 → H1 sobre una corrida completa. **Sólo señales.**

Lo que se comprueba aquí es sobre todo lo que la cascada **no** hace: no sale sin
zonas ni sin el ID de H1, no pide permiso al Diario para empezar, no mira nada que
vaya en contra de un OB diario vigente, no se sale de sus ventanas, no marca un OB
de H1 antes de que exista, no señala un toque de ese OB antes de haberlo marcado
ni después de morir el ID que lo trajo, no espera al cierre de la vela de H1 para
cobrar ese toque, no cuelga un paso de otro que no existe y no cambia ni un número
de las fases 1, 2.0 y 2.1.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chronos.application.entries.cascade import (
    CascadeMark,
    CascadeRun,
    CascadeStep,
    detect_cascade,
)
from chronos.application.structure.config import (
    DAILY,
    H1,
    H4,
    M15,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    ZonesConfig,
    with_hourly_structure,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.zone_signals import detect_zone_signals
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zone_signals import ZoneSignalKind
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


def _run(*, zones: bool, hourly_id: bool = True) -> ImpulseRun:
    config = ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5, break_by_zone=True),
        zones=ZonesConfig(enabled=zones),
        # La fase 3.0 corre con ID propio en H1: es el segundo escalón.
        charts=with_hourly_structure(ChartsConfig()) if hourly_id else ChartsConfig(),
    )
    history = make_m1_history(weeks=40)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }
    return DetectDominantImpulses(config).execute(series)


@pytest.fixture(scope="module")
def run() -> ImpulseRun:
    return _run(zones=True)


@pytest.fixture(scope="module")
def zones(run: ImpulseRun) -> ZonesRun:
    return detect_zones(run)


@pytest.fixture(scope="module")
def cascade(run: ImpulseRun, zones: ZonesRun) -> CascadeRun:
    return detect_cascade(run, zones)


def _of(cascade: CascadeRun, step: CascadeStep):
    return tuple(mark for mark in cascade.marks if mark.step is step)


def test_sin_zonas_no_hay_cascada() -> None:
    apagado = _run(zones=False)
    medida = detect_cascade(apagado, detect_zones(apagado))

    assert medida.enabled is False
    assert medida.empty


def test_sin_id_en_h1_no_hay_cascada() -> None:
    """El segundo escalón es un ID de verdad: sin detector en H1 no hay cascada.

    Es el reparto de las fases 1 y 2, donde H1 sólo lleva dibujado el ID de H4.
    """
    sin_h1 = _run(zones=True, hourly_id=False)
    medida = detect_cascade(sin_h1, detect_zones(sin_h1))

    assert medida.enabled is False
    assert medida.empty


def test_la_corrida_sintetica_recorre_los_dos_escalones(cascade: CascadeRun) -> None:
    counts = cascade.counts()

    assert counts[CascadeStep.BUSCAR_H1.value] > 0
    assert counts[CascadeStep.CONFIRMA_OB_H1.value] > 0
    assert counts[CascadeStep.TOQUE_OB_H1.value] > 0


def test_cada_paso_se_dibuja_en_su_grafico(cascade: CascadeRun) -> None:
    esperado = {
        CascadeStep.ZONA_DIARIA: DAILY,
        CascadeStep.H4_DESCARTADO: H4,
        CascadeStep.BUSCAR_H1: H4,
        CascadeStep.CONFIRMA_OB_H1: H1,
        CascadeStep.TOQUE_OB_H1: H1,
    }
    for mark in cascade.marks:
        assert mark.chart == esperado[mark.step]


def _toques(run: ImpulseRun, zones: ZonesRun, timeframe: str) -> set:
    return {
        item.timestamp
        for item in detect_zone_signals(run, zones).per_timeframe[timeframe].items
        if item.kind is ZoneSignalKind.TOQUE_OB
    }


def test_la_cascada_empieza_en_un_toque_del_ob_de_h4(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """No se redefine el toque: es el `TOQUE_OB` que la fase 2.0 ya calculaba,
    y no cuelga de nada porque el Diario no es un escalón."""
    toques = _toques(run, zones, H4)

    for mark in _of(cascade, CascadeStep.BUSCAR_H1):
        assert mark.timestamp in toques
        assert mark.timeframe == H4
        assert mark.parent is None


def test_el_tramo_diario_no_abre_ninguna_busqueda(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """El Diario sólo prohíbe: nada cuelga de él salvo los descartes."""
    toques = _toques(run, zones, DAILY)
    tramos = {mark.seq for mark in _of(cascade, CascadeStep.ZONA_DIARIA)}

    for mark in _of(cascade, CascadeStep.ZONA_DIARIA):
        assert mark.timestamp in toques
        assert mark.timeframe == DAILY
        assert mark.parent is None
    for mark in cascade.marks:
        if mark.parent in tramos:
            assert mark.step is CascadeStep.H4_DESCARTADO


def test_sin_ob_diario_vigente_se_busca_en_las_dos_direcciones(
    cascade: CascadeRun,
) -> None:
    """El Diario no autoriza nada: fuera de sus zonas vale cualquier toque de H4."""
    tramos = _of(cascade, CascadeStep.ZONA_DIARIA)
    busquedas = _of(cascade, CascadeStep.BUSCAR_H1)

    assert {mark.direction for mark in busquedas} == {
        ImpulseDirection.ALCISTA,
        ImpulseDirection.BAJISTA,
    }
    # Y ninguna de ellas cae dentro de un tramo diario que la prohibiese.
    for mark in busquedas:
        for tramo in tramos:
            if tramo.direction is mark.direction:
                continue
            assert not (
                mark.timestamp >= tramo.timestamp
                and (tramo.window_end is None or mark.timestamp < tramo.window_end)
            )


def test_dentro_de_un_ob_diario_no_se_mira_nada_en_contra(cascade: CascadeRun) -> None:
    padres = {mark.seq: mark for mark in cascade.marks}

    descartes = _of(cascade, CascadeStep.H4_DESCARTADO)
    assert descartes
    for mark in descartes:
        tramo = padres[mark.parent]
        assert tramo.step is CascadeStep.ZONA_DIARIA
        assert mark.direction is not tramo.direction
        assert mark.timestamp >= tramo.timestamp
        assert tramo.window_end is None or mark.timestamp < tramo.window_end


def test_ningun_paso_cuelga_de_uno_que_no_existe(cascade: CascadeRun) -> None:
    conocidos = {mark.seq for mark in cascade.marks}

    for mark in cascade.marks:
        assert mark.parent is None or mark.parent in conocidos


def test_ningun_paso_se_sabe_antes_que_su_padre(cascade: CascadeRun, run: ImpulseRun) -> None:
    """Un paso se sabe cuando CIERRA la vela que lo mide, no cuando se abre.

    La confirmación se mide en la vela de H1 y se fecha en su apertura, y esa
    vela puede ser la misma que contiene el toque de H4 —el toque cae en un
    minuto cualquiera de ella—: lo que no puede es haber cerrado antes del
    toque, porque entonces se habría sabido antes de que hubiera nada que
    buscar. Un paso medido en la vela FINA —los dos toques del OB— se sabe en el
    instante en que se fecha, así que le basta con no adelantarse a su padre.
    """
    padres = {mark.seq: mark for mark in cascade.marks}
    hourly = run.chart_bars[H1].index

    for mark in cascade.marks:
        if mark.parent is None:
            continue
        padre = padres[mark.parent]
        if mark.source != mark.chart:
            assert mark.timestamp >= padre.timestamp
            continue
        position = int(hourly.searchsorted(pd.Timestamp(mark.timestamp), side="right"))
        assert position < len(hourly), "la vela de la marca es la última: no se sabe su cierre"
        assert hourly[position] > padre.timestamp


def test_cada_paso_cae_dentro_de_la_ventana_que_lo_abrio(cascade: CascadeRun) -> None:
    """La ventana cubre `[toque, fin)`: al cerrarse ya no admite nada."""
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in cascade.marks:
        if mark.parent is None:
            continue
        padre = padres[mark.parent]
        if padre.window_end is None:
            continue
        # La confirmación se fecha en la APERTURA de su vela de H1, que cierra
        # después: lo que tiene que caer dentro de la ventana es esa apertura.
        assert mark.timestamp < padre.window_end


def test_el_tramo_diario_dura_lo_que_el_precio_esta_dentro_de_la_zona(
    cascade: CascadeRun, run: ImpulseRun
) -> None:
    """El veto se mide distinto que la búsqueda: aquí sí manda estar dentro."""
    bars = run.analyses[DAILY].bars

    for mark in _of(cascade, CascadeStep.ZONA_DIARIA):
        if mark.window_end is None or mark.low is None or mark.high is None:
            continue
        dentro = bars.loc[(bars.index >= mark.timestamp) & (bars.index < mark.window_end)]
        # Todas las velas del tramo cerraron DENTRO de la zona salvo, como
        # mucho, la última: la que lo cierra es justo la que se fue.
        assert all(mark.low <= close <= mark.high for close in dentro["close"].to_numpy()[:-1])


def _fuera_por_el_exterior(cierres, mark: CascadeMark):
    exterior = mark.low if mark.direction is ImpulseDirection.ALCISTA else mark.high
    if mark.direction is ImpulseDirection.ALCISTA:
        return cierres < exterior
    return cierres > exterior


def test_irse_del_ob_de_h4_a_favor_ya_no_cierra_la_busqueda(
    cascade: CascadeRun, run: ImpulseRun
) -> None:
    """La ventana la cierran romper el OB o llegar al UL, no irse hacia arriba."""
    bars = run.analyses[H4].bars
    sobrevividas = 0

    for mark in _of(cascade, CascadeStep.BUSCAR_H1):
        if mark.window_end is None or mark.low is None or mark.high is None:
            continue
        dentro = bars.loc[(bars.index >= mark.timestamp) & (bars.index < mark.window_end)]
        cierres = dentro["close"].to_numpy()
        if mark.direction is ImpulseDirection.ALCISTA:
            a_favor = cierres > mark.high
        else:
            a_favor = cierres < mark.low
        if a_favor.any():
            sobrevividas += 1
        # Ninguna vela rompió el OB dentro de la ventana salvo, como mucho, la
        # última: la que la cierra es justo la que lo atravesó.
        assert not _fuera_por_el_exterior(cierres, mark)[:-1].any()

    assert sobrevividas, "ninguna búsqueda sobrevivió a salirse a favor: no se prueba nada"


def test_la_busqueda_se_cierra_al_llegar_al_ul(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """Llegar al UL es tocarlo, y no se espera al cierre de la vela de H4."""
    fine = run.chart_bars[M15]
    ul = {item.id_num: item.last for item in zones.per_timeframe[H4].items}
    aperturas = set(run.analyses[H4].bars.index)
    a_media_vela = 0

    for mark in _of(cascade, CascadeStep.BUSCAR_H1):
        if mark.window_end is None:
            continue
        zona = ul[mark.id_num]
        dentro = fine.loc[(fine.index >= mark.timestamp) & (fine.index < mark.window_end)]
        if mark.direction is ImpulseDirection.ALCISTA:
            llega = dentro["high"].to_numpy() >= zona.inner
        else:
            llega = dentro["low"].to_numpy() <= zona.inner
        assert not llega.any(), f"la búsqueda {mark.seq} siguió abierta después del UL"
        if mark.window_end not in aperturas:
            a_media_vela += 1

    assert a_media_vela, "ninguna ventana se cortó a media vela: el UL no está cerrando nada"


def test_una_confirmacion_por_busqueda_y_ni_una_mas(cascade: CascadeRun) -> None:
    """La primera cierra la búsqueda: un OB de H1 por ventana y ni uno más."""
    por_padre: dict[int, int] = {}
    for mark in _of(cascade, CascadeStep.CONFIRMA_OB_H1):
        por_padre[mark.parent] = por_padre.get(mark.parent, 0) + 1

    assert por_padre
    assert max(por_padre.values()) == 1


def test_las_confirmaciones_cuelgan_de_una_busqueda_y_hablan_del_id_de_h1(
    cascade: CascadeRun,
) -> None:
    """La marca cuelga del toque de H4 y nombra al ID de H1: son las dos cosas
    que hacen falta para remontarla, y cada una vive en un sitio."""
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in _of(cascade, CascadeStep.CONFIRMA_OB_H1):
        padre = padres[mark.parent]
        assert padre.step is CascadeStep.BUSCAR_H1
        assert padre.timeframe == H4
        assert mark.timeframe == H1


def test_el_ob_marcado_es_el_del_id_de_h1_alineado(
    cascade: CascadeRun, zones: ZonesRun
) -> None:
    """Lo que se marca no es un patrón de velas: es el OB del ID de H1, con sus
    bordes y su vela del ancla, y el ID va en la dirección del de H4."""
    por_id = {item.id_num: item for item in zones.per_timeframe[H1].items}

    marcas = _of(cascade, CascadeStep.CONFIRMA_OB_H1)
    assert marcas
    for mark in marcas:
        zoned = por_id[mark.id_num]
        block = zoned.order_block
        assert block is not None
        assert zoned.direction is mark.direction
        assert (mark.low, mark.high, mark.level) == (block.low, block.high, block.inner)
        assert mark.zone_start == block.ts_defining
        assert mark.zone_start < mark.timestamp


def test_el_ob_de_h1_no_se_marca_antes_de_existir(
    cascade: CascadeRun, zones: ZonesRun, run: ImpulseRun
) -> None:
    """La zona nace con lo último que llegue: la constitución del ID de H1 o la
    confirmación de su OB. Marcarla antes sería mirar al futuro."""
    por_id = {item.id_num: item for item in zones.per_timeframe[H1].items}
    hourly = run.analyses[H1].bars.index

    for mark in _of(cascade, CascadeStep.CONFIRMA_OB_H1):
        zoned = por_id[mark.id_num]
        assert mark.timestamp >= zoned.ts_constitution
        assert mark.timestamp >= zoned.order_block.ts_birth
        # Y el ID seguía vivo: la marca cae en su tramo, no después de morir.
        assert zoned.ts_end is None or mark.timestamp <= zoned.ts_end
        assert pd.Timestamp(mark.timestamp) in hourly


def test_la_senal_es_el_toque_del_ob_de_h1_y_cuelga_de_su_confirmacion(
    cascade: CascadeRun, run: ImpulseRun, zones: ZonesRun
) -> None:
    """El paso que cierra la cascada no redefine nada: es el `TOQUE_OB` que la
    fase 2.0 ya calcula, leído sobre el OB del ID de H1 que se marcó."""
    toques = _toques(run, zones, H1)
    padres = {mark.seq: mark for mark in cascade.marks}

    marcas = _of(cascade, CascadeStep.TOQUE_OB_H1)
    assert marcas
    for mark in marcas:
        padre = padres[mark.parent]
        assert padre.step is CascadeStep.CONFIRMA_OB_H1
        assert mark.timeframe == H1
        assert mark.id_num == padre.id_num
        assert mark.direction is padre.direction
        assert (mark.low, mark.high, mark.level) == (padre.low, padre.high, padre.level)
        assert mark.timestamp in toques


def test_el_toque_del_ob_de_h1_no_espera_al_cierre_de_la_vela(
    cascade: CascadeRun, run: ImpulseRun
) -> None:
    """Igual que en H4 y en el Diario: la señal salta en cuanto el precio entra
    en la zona, fechada en la vela fina, no en el cierre de la de H1."""
    hourly = run.chart_bars[H1].index
    fina = run.chart_bars[M15].index

    marcas = _of(cascade, CascadeStep.TOQUE_OB_H1)
    assert marcas
    for mark in marcas:
        assert mark.source == M15
        assert pd.Timestamp(mark.timestamp) in fina
    # Y no todas caen en la apertura de una vela de H1: si esperasen al cierre,
    # todas coincidirían con la rejilla de H1 y no habría nada que auditar.
    assert any(pd.Timestamp(mark.timestamp) not in hourly for mark in marcas)


def test_un_toque_por_confirmacion_y_ni_uno_mas(cascade: CascadeRun) -> None:
    por_padre: dict[int, int] = {}
    for mark in _of(cascade, CascadeStep.TOQUE_OB_H1):
        por_padre[mark.parent] = por_padre.get(mark.parent, 0) + 1

    assert por_padre
    assert max(por_padre.values()) == 1


def test_el_toque_muere_con_la_ventana_de_h4(
    cascade: CascadeRun, zones: ZonesRun
) -> None:
    """Cerrada la búsqueda no hay señal, aunque el ID de H1 siga vivo.

    Lo atan las dos cosas y manda la que llegue antes: por abajo, que el OB de
    H1 exista; por arriba, la ventana que mandó bajar a buscarlo.
    """
    por_id = {item.id_num: item for item in zones.per_timeframe[H1].items}
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in _of(cascade, CascadeStep.TOQUE_OB_H1):
        zoned = por_id[mark.id_num]
        assert mark.timestamp >= zoned.order_block.ts_birth
        assert zoned.ts_end is None or mark.timestamp <= zoned.ts_end
        busqueda = padres[padres[mark.parent].parent]
        assert busqueda.window_end is None or mark.timestamp < busqueda.window_end


def test_el_toque_no_se_marca_antes_de_que_se_marcara_el_ob(
    cascade: CascadeRun,
) -> None:
    """La señal es tocar una zona ya marcada: antes de eso no hay nada que tocar."""
    padres = {mark.seq: mark for mark in cascade.marks}

    for mark in _of(cascade, CascadeStep.TOQUE_OB_H1):
        confirmacion = padres[mark.parent]
        assert mark.timestamp >= confirmacion.timestamp
        assert mark.timestamp >= padres[confirmacion.parent].timestamp


def test_las_marcas_van_en_orden(cascade: CascadeRun) -> None:
    stamps = [mark.timestamp for mark in cascade.marks]

    assert stamps == sorted(stamps)


def test_la_cascada_no_mueve_ni_un_impulso(run: ImpulseRun, zones: ZonesRun) -> None:
    """Se calcula sobre el resultado ya cerrado: no hay nada que pueda mover."""
    antes = run.table().copy()
    detect_cascade(run, zones)

    pd.testing.assert_frame_equal(run.table(), antes)
