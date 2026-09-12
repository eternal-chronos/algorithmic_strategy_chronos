"""El OB y el FVG dentro del ID (K.1).

Las velas se escriben enteras —open, high, low, close— porque aquí, a
diferencia del módulo 1, las mechas sí cuentan: la zona del OB es la vela
entera y el hueco del FVG se mide entre mechas. Los impulsos se construyen a
mano con los índices que el detector habría puesto: lo que se prueba es la
regla del propietario sobre un ID dado, no el detector.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import BodyDirection, BreakKind, ImpulseDirection
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.patterns import (
    PATTERN_COLUMNS,
    PatternRule,
    patterns_inside,
)

START = pd.Timestamp("2024-01-08 00:00", tz="UTC")
H4 = timedelta(hours=4)
H1 = timedelta(hours=1)

Bar = tuple[float, float, float, float]


def _bars(rows: list[Bar], *, step: timedelta = H4, start: pd.Timestamp = START) -> pd.DataFrame:
    index = pd.DatetimeIndex([start + step * position for position in range(len(rows))])
    return pd.DataFrame(
        {
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": 1.0,
        },
        index=index,
    )


def _impulse(
    bars: pd.DataFrame,
    *,
    id_num: int = 1,
    direction: ImpulseDirection = ImpulseDirection.ALCISTA,
    leg_start: int,
    constitution: int,
    anchor: float,
    extreme: float,
    end: int | None = None,
    timeframe: str = "H4",
) -> DominantImpulse:
    index = pd.DatetimeIndex(bars.index)
    impulse = DominantImpulse(
        id_num=id_num,
        timeframe=timeframe,
        direction=direction,
        ts_previous_break=None,
        ts_constitution=index[constitution].to_pydatetime(),
        index_constitution=constitution,
        ts_leg_start=index[leg_start].to_pydatetime(),
        index_leg_start=leg_start,
        anchor=anchor,
        extreme=extreme,
        anchor_a1=None,
        anchor_a2=anchor,
        index_anchor=leg_start,
        ts_anchor=index[leg_start].to_pydatetime(),
        index_extreme=constitution - 1,
        ts_extreme=index[constitution - 1].to_pydatetime(),
        extreme_bar_direction=BodyDirection.BULLISH,
        limbo_bars=constitution - leg_start,
        constituting_body_size=1.0,
    )
    if end is not None:
        impulse.close(timestamp=index[end].to_pydatetime(), index=end, kind=BreakKind.A_FAVOR)
    return impulse


#: Una pierna alcista con el OB en la vela previa al arranque y un FVG en la
#: propia pierna, el ID que constituye y la vela que lo rompe a favor.
#:
#:   0 verde   · antes de todo
#:   1 ROJA    · el OB "donde arranca el ID": zona [98, 101]
#:   2 verde   · arranca la pierna; cierra en 105 > 101 y el OB se sabe aquí
#:   3 verde   · low 104 > high[1] = 101: FVG [101, 104], se sabe aquí
#:   4 verde   · fija el extremo por cuerpo en 111
#:   5 roja    · constituye el ID: ancla 99 (A2), extremo 111
#:   6 roja    · retrocede hasta 103,5: entra en el FVG, no en el OB. Y es a su
#:               vez un OB [103,5, 109], porque la 7 cierra por encima de ella:
#:               el OB "donde arranca" el ID siguiente
#:   7 verde   · cierra en 112 > 111: mata al ID
#:   8 verde   · ya fuera
#:   9 roja    · constituye el siguiente
LEG: list[Bar] = [
    (100.0, 101.0, 99.0, 100.5),
    (100.5, 101.0, 98.0, 99.0),
    (99.0, 106.0, 98.5, 105.0),
    (105.0, 110.0, 104.0, 109.0),
    (109.0, 112.0, 105.0, 111.0),
    (111.0, 112.0, 107.0, 108.0),
    (108.0, 109.0, 103.5, 105.0),
    (105.0, 113.0, 104.5, 112.0),
    (112.0, 114.0, 111.0, 113.0),
    (113.0, 114.0, 110.0, 111.0),
]


@pytest.fixture
def leg() -> pd.DataFrame:
    return _bars(LEG)


def _first(leg: pd.DataFrame, *, end: int | None = 7) -> DominantImpulse:
    return _impulse(leg, leg_start=2, constitution=5, anchor=99.0, extreme=111.0, end=end)


def test_el_ob_del_arranque_y_el_fvg_de_la_pierna_caen_dentro_del_id(leg: pd.DataFrame) -> None:
    found = patterns_inside(leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg)

    assert list(found.columns) == list(PATTERN_COLUMNS)
    assert found["tipo"].tolist() == ["OB", "FVG", "OB"]
    assert found["indice_origen"].tolist() == [1, 2, 6]
    assert found["id_num"].tolist() == [1, 1, 1]
    assert found["direccion"].tolist() == ["alcista"] * 3
    assert found["timeframe"].tolist() == ["H4"] * 3
    assert found["id_timeframe"].tolist() == ["H4"] * 3

    ob = found.iloc[0]
    assert ob["indice_origen"] == 1
    assert ob["indice_conocido"] == 2
    assert (ob["precio_bajo"], ob["precio_alto"]) == (98.0, 101.0)
    assert ob["ts_origen"] == START + H4
    assert ob["ts_conocido"] == START + 2 * H4

    fvg = found.iloc[1]
    assert fvg["indice_origen"] == 2
    assert fvg["indice_conocido"] == 3
    assert (fvg["precio_bajo"], fvg["precio_alto"]) == (101.0, 104.0)


def test_el_ob_del_arranque_asoma_por_debajo_del_ancla_y_cuenta_igual(leg: pd.DataFrame) -> None:
    """Solapar el rango basta: el OB [98, 101] sobresale del ancla en 99."""
    found = patterns_inside(leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg)
    ob = found[found["tipo"] == "OB"].iloc[0]

    assert ob["precio_bajo"] < 99.0 < ob["precio_alto"]


def test_el_fvg_se_usa_cuando_el_precio_vuelve_a_entrar_y_el_ob_no(leg: pd.DataFrame) -> None:
    found = patterns_inside(leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg)
    ob, fvg = found.iloc[0], found.iloc[1]

    # La vela 6 baja hasta 103,5: toca el FVG [101, 104] y no llega al OB.
    assert bool(fvg["usado"]) is True
    assert fvg["indice_usado"] == 6
    assert fvg["ts_usado"] == START + 6 * H4
    assert fvg["ts_fin"] == fvg["ts_usado"]
    assert bool(ob["usado"]) is False
    assert pd.isna(ob["ts_usado"])
    # El OB muere con el ID, en la vela que lo rompe.
    assert ob["ts_fin"] == START + 7 * H4
    assert bool(ob["vivo"]) is False


def test_con_el_id_vivo_el_patron_sin_usar_sigue_vivo(leg: pd.DataFrame) -> None:
    found = patterns_inside(
        leg, timeframe="H4", impulses=[_first(leg, end=None)], id_bars=leg
    )
    ob = found[found["tipo"] == "OB"].iloc[0]

    assert bool(ob["vivo"]) is True
    assert pd.isna(ob["ts_fin"])
    # Y el usado no está vivo aunque el ID lo esté.
    assert bool(found[found["tipo"] == "FVG"].iloc[0]["vivo"]) is False


def test_lo_que_se_sabe_despues_de_la_muerte_del_id_no_es_suyo(leg: pd.DataFrame) -> None:
    """La vela 5 es roja y la 8 cierra por encima de su máximo: es un OB, pero
    se sabe en la 8, cuando el ID ya murió en la 7."""
    found = patterns_inside(leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg)

    assert 5 not in found["indice_origen"].tolist()


def test_los_patrones_en_contra_del_id_no_se_marcan(leg: pd.DataFrame) -> None:
    """La misma ventana y el mismo rango leídos como ID bajista: el OB y el FVG
    alcistas que hay ahí no son suyos, y bajistas no hay ninguno."""
    bearish = _impulse(
        leg,
        direction=ImpulseDirection.BAJISTA,
        leg_start=2,
        constitution=5,
        anchor=111.0,
        extreme=99.0,
        end=7,
    )
    alcista = patterns_inside(leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg)
    bajista = patterns_inside(leg, timeframe="H4", impulses=[bearish], id_bars=leg)

    assert len(alcista) == 3
    assert bajista.empty


def test_un_patron_fuera_del_rango_de_precio_no_se_marca(leg: pd.DataFrame) -> None:
    """El mismo ID con el ancla subida a 110: los tres quedan por debajo."""
    narrow = _impulse(leg, leg_start=2, constitution=5, anchor=110.0, extreme=111.0, end=7)
    found = patterns_inside(leg, timeframe="H4", impulses=[narrow], id_bars=leg)

    assert found["tipo"].tolist() == []


def test_el_patron_compartido_es_del_ultimo_id_que_lo_reclama(leg: pd.DataFrame) -> None:
    """La vela 6 es roja y la 7 cierra por encima de ella: un OB [103,5, 109]
    que cae en la ventana del primer ID (muere en la 7) y en la del segundo
    (su pierna arranca en la 7, la ventana en la 6). Es del segundo."""
    first = _first(leg)
    second = _impulse(
        leg, id_num=2, leg_start=7, constitution=9, anchor=105.0, extreme=113.0
    )
    found = patterns_inside(leg, timeframe="H4", impulses=[first, second], id_bars=leg)
    shared = found[found["indice_origen"] == 6]

    assert len(shared) == 1
    assert shared.iloc[0]["id_num"] == 2
    assert shared.iloc[0]["tipo"] == "OB"
    assert bool(shared.iloc[0]["vivo"]) is True
    # Y el primero se queda con los suyos.
    assert found[found["id_num"] == 1]["indice_origen"].tolist() == [1, 2]


def test_la_tabla_va_ordenada_por_cuando_se_supo_cada_patron(leg: pd.DataFrame) -> None:
    first = _first(leg)
    second = _impulse(
        leg, id_num=2, leg_start=7, constitution=9, anchor=105.0, extreme=113.0
    )
    found = patterns_inside(leg, timeframe="H4", impulses=[second, first], id_bars=leg)

    assert found["ts_conocido"].is_monotonic_increasing


# --- H1 dentro del ID de H4 ---------------------------------------------------


def test_en_h1_se_marcan_los_patrones_dentro_de_la_ventana_del_id_de_h4() -> None:
    """Cuatro velas de H4 y dieciséis de H1. El ID de H4 arranca su pierna en
    la vela 2, así que su ventana empieza en la 1 (H1 de la 4 en adelante). El
    FVG de H1 en las velas 1-2-3 queda fuera; el de las 5-6-7, dentro."""
    h4 = _bars(
        [
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 101.0, 98.0, 99.0),
            (99.0, 106.0, 98.5, 105.0),
            (105.0, 106.0, 103.0, 104.0),
        ]
    )
    flat: Bar = (100.0, 100.5, 99.5, 100.0)
    h1_rows: list[Bar] = [flat] * 16
    # FVG alcista fuera de la ventana: velas 1, 2, 3 de H1.
    h1_rows[1] = (99.0, 99.5, 98.5, 99.2)
    h1_rows[2] = (99.2, 101.0, 99.0, 100.8)
    h1_rows[3] = (100.8, 101.5, 100.2, 101.0)
    # FVG alcista dentro: velas 5, 6, 7 (hueco entre high[5] = 100,5 y low[7] = 102).
    h1_rows[5] = (100.0, 100.5, 99.5, 100.2)
    h1_rows[6] = (100.2, 103.0, 100.0, 102.8)
    h1_rows[7] = (102.8, 104.0, 102.0, 103.5)
    h1 = _bars(h1_rows, step=H1)
    impulse = _impulse(h4, leg_start=2, constitution=3, anchor=99.0, extreme=105.0)

    found = patterns_inside(h1, timeframe="H1", impulses=[impulse], id_bars=h4)
    gaps = found[found["tipo"] == "FVG"]

    assert gaps["indice_origen"].tolist() == [6]
    assert gaps["timeframe"].tolist() == ["H1"]
    assert gaps["id_timeframe"].tolist() == ["H4"]
    assert gaps.iloc[0]["ts_origen"] == START + 6 * H1
    assert (gaps.iloc[0]["precio_bajo"], gaps.iloc[0]["precio_alto"]) == (100.5, 102.0)


def test_la_ventana_de_h1_termina_con_la_vela_de_h4_que_mata_al_id() -> None:
    """El ID de H4 muere en su vela 3: las H1 de esa vela (12 a 15) siguen
    dentro; las de la vela 4 de H4, ya no."""
    h4 = _bars([(100.0, 101.0, 99.0, 100.5)] * 5)
    flat: Bar = (100.0, 100.5, 99.5, 100.0)
    h1_rows: list[Bar] = [flat] * 20
    # Un OB alcista que se sabe en la H1 15 (última de la vela 3 de H4)...
    h1_rows[13] = (100.0, 100.5, 99.5, 99.8)
    h1_rows[14] = (99.8, 100.4, 99.6, 100.2)
    h1_rows[15] = (100.2, 101.5, 100.0, 101.0)
    # ...y otro que se sabe en la H1 16, ya en la vela 4 de H4.
    h1_rows[16] = (101.0, 101.2, 100.6, 100.8)
    h1_rows[17] = (100.8, 102.0, 100.7, 101.8)
    h1 = _bars(h1_rows, step=H1)
    impulse = _impulse(h4, leg_start=1, constitution=2, anchor=99.0, extreme=105.0, end=3)

    found = patterns_inside(h1, timeframe="H1", impulses=[impulse], id_bars=h4)
    blocks = found[found["tipo"] == "OB"]

    assert blocks["indice_origen"].tolist() == [13]
    assert blocks.iloc[0]["indice_conocido"] == 15
    # Muere con el ID: en la vela 3 de H4.
    assert blocks.iloc[0]["ts_fin"] == START + 3 * H4


# --- Bordes -------------------------------------------------------------------


def test_sin_velas_o_sin_impulsos_la_tabla_va_vacia_con_sus_columnas(leg: pd.DataFrame) -> None:
    empty = patterns_inside(leg.iloc[:0], timeframe="H4", impulses=[_first(leg)], id_bars=leg)
    none = patterns_inside(leg, timeframe="H4", impulses=[], id_bars=leg)

    assert list(empty.columns) == list(PATTERN_COLUMNS) and empty.empty
    assert list(none.columns) == list(PATTERN_COLUMNS) and none.empty


def test_una_sola_vela_no_da_patrones(leg: pd.DataFrame) -> None:
    single = leg.iloc[:1]
    impulse = _impulse(single, leg_start=0, constitution=0, anchor=99.0, extreme=111.0)

    assert patterns_inside(single, timeframe="H4", impulses=[impulse], id_bars=single).empty


def test_el_indice_tiene_que_ser_tz_aware(leg: pd.DataFrame) -> None:
    naive = leg.tz_localize(None)

    with pytest.raises(DomainError):
        patterns_inside(naive, timeframe="H4", impulses=[_first(leg)], id_bars=leg)


def test_el_desplazamiento_del_ob_tiene_que_ser_al_menos_una_vela() -> None:
    with pytest.raises(DomainError):
        PatternRule(displacement=0)


def test_con_desplazamiento_de_una_vela_el_ob_exige_que_la_siguiente_se_vaya(
    leg: pd.DataFrame,
) -> None:
    """La vela 1 lo cumple (la 2 cierra en 105 > 101). Con más velas de margen
    no cambia nada aquí; lo que se comprueba es que el parámetro llega."""
    strict = patterns_inside(
        leg, timeframe="H4", impulses=[_first(leg)], id_bars=leg, rule=PatternRule(displacement=1)
    )

    assert strict[strict["tipo"] == "OB"]["indice_origen"].tolist() == [1, 6]


def test_un_hueco_que_se_toca_justo_no_es_fvg() -> None:
    """`low[i+1] == high[i-1]`: sin hueco estricto no hay FVG."""
    bars = _bars(
        [
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 103.0, 100.0, 102.5),
            (102.5, 105.0, 101.0, 104.0),
            (104.0, 105.0, 103.0, 103.5),
        ]
    )
    impulse = _impulse(bars, leg_start=1, constitution=3, anchor=100.0, extreme=104.0)

    found = patterns_inside(bars, timeframe="H4", impulses=[impulse], id_bars=bars)

    assert "FVG" not in found["tipo"].tolist()
