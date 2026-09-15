"""Medición de la firma de lateralización (sección D).

Lo primero que se comprueba es lo único que no puede fallar: que medir no
cambia nada. Después, que los recuentos cuadran con los contactos y que el
signo de la geometría distingue un toque de una rotura sin mirar más columnas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chronos.application.structure.config import (
    H4,
    AggregationConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.geometry import GEOMETRY_COLUMNS
from chronos.application.structure.lateralization import (
    DEGENERATE_RANGE_ATR,
    REVISIT_HORIZONS,
    SIGNATURE_MIN_TOUCHES,
    contacts_by_year,
    degenerate_verdict,
    detail,
    measure,
    populations,
    revisit_summary,
    signature_by_year,
)
from chronos.domain.structure.enums import ContactKind, ContactSide
from chronos.infrastructure.structure.aggregation import aggregate_all
from tests.conftest import make_m1_history


@pytest.fixture
def config() -> ImpulseConfig:
    return ImpulseConfig(
        data=StructureDataConfig(path="no-se-lee.parquet"),
        rules=ImpulseRulesConfig(warmup_bars=5),
    )


@pytest.fixture
def run(config: ImpulseConfig) -> ImpulseRun:
    history = make_m1_history(weeks=12)
    series = {
        timeframe: aggregated.frame
        for timeframe, aggregated in aggregate_all(
            history, AggregationConfig(), config.charts.charts
        ).items()
    }
    return DetectDominantImpulses(config).execute(series)


# --- Medir no cambia nada ---------------------------------------------------


def test_medir_los_contactos_no_toca_la_deteccion(run: ImpulseRun) -> None:
    antes = [
        (impulse.ts_constitution, impulse.anchor, impulse.extreme, impulse.ts_end)
        for impulse in run.analyses[H4].impulses
    ]
    measure(run)
    despues = [
        (impulse.ts_constitution, impulse.anchor, impulse.extreme, impulse.ts_end)
        for impulse in run.analyses[H4].impulses
    ]
    assert antes == despues


def test_con_el_modulo_apagado_no_se_mide_nada(config: ImpulseConfig) -> None:
    from dataclasses import replace

    apagado = DetectDominantImpulses(replace(config, enabled=False)).execute({})
    assert measure(apagado).per_timeframe == {}


# --- Contactos --------------------------------------------------------------


def test_los_contactos_caen_dentro_de_la_vigencia_del_id(run: ImpulseRun) -> None:
    """Ni antes de la constitución ni después de la rotura que lo mata."""
    analysis = run.analyses[H4]
    medicion = measure(run).per_timeframe[H4]
    limites = {
        impulse.id_num: (impulse.index_constitution, impulse.index_end)
        for impulse in analysis.impulses
    }
    ultimo = len(analysis.bars) - 1

    for item in medicion.impulses:
        primero, final = limites[item.id_num]
        for contacto in item.series.contacts:
            assert contacto.index > primero
            assert contacto.index <= (final if final is not None else ultimo)


def test_cada_id_cerrado_tiene_exactamente_una_rotura_real(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    for item in medicion.impulses:
        reales = sum(
            1
            for contacto in item.series.contacts
            if contacto.kind is ContactKind.ROTURA_REAL
        )
        assert reales == (0 if item.exit_break is None else 1)


def test_el_nivel_del_contacto_es_un_limite_del_id(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    for item in medicion.impulses:
        for contacto in item.series.contacts:
            esperado = item.upper if contacto.side is ContactSide.SUPERIOR else item.lower
            assert contacto.level == esperado


def test_la_firma_exige_los_dos_limites(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    for item in medicion.impulses:
        assert item.meets_signature == (
            item.touches_upper >= SIGNATURE_MIN_TOUCHES
            and item.touches_lower >= SIGNATURE_MIN_TOUCHES
        )


def test_la_acumulacion_empieza_en_el_toque_que_completa_la_firma(run: ImpulseRun) -> None:
    """`signature_index` es la barra del contacto que deja dos toques en cada
    límite: sólo lo tienen los ID que cumplen la firma, y es causal —a esa barra
    ya se han visto los cuatro toques y ninguno posterior lo mueve—."""
    medicion = measure(run).per_timeframe[H4]
    assert any(item.meets_signature for item in medicion.impulses), (
        "la fixture tiene que traer al menos un ID con la firma"
    )
    for item in medicion.impulses:
        onset = item.signature_index
        assert (onset is not None) == item.meets_signature
        if onset is None:
            continue
        before = [
            contact
            for contact in item.series.contacts
            if contact.index <= onset and contact.kind is not ContactKind.ROTURA_REAL
        ]
        arriba = sum(1 for contact in before if contact.side is ContactSide.SUPERIOR)
        abajo = sum(1 for contact in before if contact.side is ContactSide.INFERIOR)
        assert arriba >= SIGNATURE_MIN_TOUCHES and abajo >= SIGNATURE_MIN_TOUCHES
        # Y una barra antes todavía no se cumplía: es el PRIMER momento.
        earlier = [contact for contact in before if contact.index < onset]
        assert (
            sum(1 for c in earlier if c.side is ContactSide.SUPERIOR) < SIGNATURE_MIN_TOUCHES
            or sum(1 for c in earlier if c.side is ContactSide.INFERIOR) < SIGNATURE_MIN_TOUCHES
        )


def test_el_punto_medio_esta_entre_los_dos_limites(run: ImpulseRun) -> None:
    for item in measure(run).per_timeframe[H4].impulses:
        assert item.lower <= item.midpoint <= item.upper


# --- Geometría persistida (sección E) ---------------------------------------


def test_la_tabla_de_contactos_lleva_la_geometria(run: ImpulseRun) -> None:
    frame = measure(run).per_timeframe[H4].contacts
    assert set(GEOMETRY_COLUMNS) <= set(frame.columns)
    assert not frame.empty


def test_el_signo_del_cierre_distingue_el_toque_de_la_rotura(run: ImpulseRun) -> None:
    """Positivo = cerró fuera del límite; negativo o cero = cerró dentro."""
    frame = measure(run).per_timeframe[H4].contacts
    mechas = frame[frame["tipo_contacto"] == ContactKind.TOQUE_MECHA.value]
    reales = frame[frame["tipo_contacto"] == ContactKind.ROTURA_REAL.value]

    assert (mechas["cierre_mas_alla_usd"] <= 0).all()
    assert (reales["cierre_mas_alla_usd"] > 0).all()


def test_las_proporciones_de_la_vela_suman_uno(run: ImpulseRun) -> None:
    frame = measure(run).per_timeframe[H4].contacts
    total = frame["cuerpo_pct"] + frame["mecha_sup_pct"] + frame["mecha_inf_pct"]
    limpio = total[np.isfinite(total)]
    assert limpio.to_numpy() == pytest.approx(np.ones(len(limpio)))


# --- Tablas del informe -----------------------------------------------------


def test_el_censo_de_contactos_cuadra_con_los_impulsos(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    tabla = contacts_by_year(medicion)
    total = tabla[tabla["anio"] == "TOTAL"].iloc[0]

    assert int(total["impulsos"]) == len(medicion.impulses)
    assert int(total["mecha_sup"]) == sum(
        item.count(ContactKind.TOQUE_MECHA, ContactSide.SUPERIOR)
        for item in medicion.impulses
    )
    assert int(total["sin_contacto"]) == sum(
        1 for item in medicion.impulses if item.total_touches == 0
    )


def test_la_firma_por_año_cuadra_con_el_total(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    tabla = signature_by_year(medicion)
    total = tabla[tabla["anio"] == "TOTAL"].iloc[0]
    por_año = tabla[tabla["anio"] != "TOTAL"]

    assert int(total["cumplen_firma"]) == len(medicion.with_signature)
    assert int(por_año["impulsos"].sum()) == int(total["impulsos"])


def test_las_dos_poblaciones_reparten_todos_los_impulsos(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    tabla = populations(medicion)
    assert int(tabla["impulsos"].sum()) == len(medicion.impulses)


def test_el_veredicto_de_los_enanos_reparte_la_poblacion(run: ImpulseRun) -> None:
    medicion = measure(run).per_timeframe[H4]
    veredicto = degenerate_verdict(medicion)

    assert veredicto.degenerates + veredicto.non_degenerates == veredicto.impulses
    assert veredicto.next_to_signature <= veredicto.degenerates
    assert all(
        item.range_atr < DEGENERATE_RANGE_ATR for item in medicion.degenerates
    )


def test_las_vueltas_al_50_no_decrecen_con_el_horizonte(run: ImpulseRun) -> None:
    tabla = revisit_summary(measure(run).per_timeframe[H4])
    columnas = [f"vuelven_{horizon}" for horizon in REVISIT_HORIZONS]
    for fila in tabla.itertuples(index=False):
        valores = [getattr(fila, columna) for columna in columnas]
        assert valores == sorted(valores)


def test_la_ficha_detallada_trae_los_id_pedidos(run: ImpulseRun) -> None:
    analysis = run.analyses[H4]
    medicion = measure(run).per_timeframe[H4]
    pedidos = [item.id_num for item in medicion.impulses[:3]]
    ficha = detail(medicion, analysis, pedidos)

    assert list(ficha["id_num"]) == pedidos
    assert set(ficha["cumple_firma"]) <= {"sí", "no"}
    assert pd.api.types.is_datetime64_any_dtype(ficha["constitucion"])
