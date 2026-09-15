"""El OB y el FVG dentro del ID de H4 (K.2).

Las velas se escriben enteras —open, high, low, close— porque aquí, a
diferencia del módulo 1, las mechas sí cuentan: el OB se confirma cuando un
cierre rompe la mecha y el hueco del FVG se mide entre mechas. Los impulsos se
construyen a mano con los índices que el detector habría puesto: lo que se
prueba es la regla del propietario sobre un ID dado, no el detector. Los cortes
del día llegan como máscara, igual que al dominio.

El caso central reproduce sus cuatro capturas del 16-01-2023 en H4.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import BodyDirection, BreakKind, ImpulseDirection
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.patterns import (
    PATTERN_COLUMNS,
    PatternEnd,
    PatternKind,
    PatternRule,
    patterns_inside,
)

START = pd.Timestamp("2023-01-13 06:00", tz="UTC")
H4 = timedelta(hours=4)

Bar = tuple[float, float, float, float]


def _bars(rows: list[Bar], *, start: pd.Timestamp = START) -> pd.DataFrame:
    index = pd.DatetimeIndex([start + H4 * position for position in range(len(rows))], tz="UTC")
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
    anchor_bar: int,
    leg_start: int,
    constitution: int,
    anchor: float,
    extreme: float,
    end: int | None = None,
    publishable: bool = True,
) -> DominantImpulse:
    index = pd.DatetimeIndex(bars.index)
    impulse = DominantImpulse(
        id_num=id_num,
        timeframe="H4",
        direction=direction,
        ts_previous_break=None,
        ts_constitution=index[constitution].to_pydatetime(),
        index_constitution=constitution,
        ts_leg_start=index[leg_start].to_pydatetime(),
        index_leg_start=leg_start,
        anchor=anchor,
        extreme=extreme,
        anchor_a1=anchor,
        anchor_a2=anchor,
        index_anchor=anchor_bar,
        ts_anchor=index[anchor_bar].to_pydatetime(),
        index_extreme=constitution - 1,
        ts_extreme=index[constitution - 1].to_pydatetime(),
        extreme_bar_direction=BodyDirection.BULLISH,
        limbo_bars=constitution - leg_start,
        constituting_body_size=1.0,
        publishable=publishable,
    )
    if end is not None:
        kind = BreakKind.A_FAVOR
        impulse.close(timestamp=index[end].to_pydatetime(), index=end, kind=kind)
    return impulse


def _mask(total: int, *cuts: int) -> np.ndarray:
    marking = np.zeros(total, dtype=bool)
    marking[list(cuts)] = True
    return marking


# --- El ejemplo del propietario (16-01-2023, H4) -------------------------------
#
# Velas de H4, con el reloj de cTrader (UTC-4) al lado:
#   0  roja   (viernes)
#   1  verde
#   2  roja   ← vela del ANCLA: su cuerpo [1900, 1904.5] es el OB alcista «A 1»
#   3  verde  ← la pierna: cierra por encima de la mecha de la 2 → el OB se sabe
#   4  verde  ← deja un FVG alcista [1906.5, 1916.5] (mechas de la 2 y la 4)
#   5  verde  domingo 22:00 UTC (18:00 UTC-4): cuerpo [1919.6, 1924.1], el extremo
#   6  roja   lunes 02:00 UTC (22:00 UTC-4): constituye el ID; cierra a la 01:59 → CORTE
#   7  roja   06:00 UTC (02:00 UTC-4): la mecha pasa por debajo de la 5, el cierre no → CORTE
#   8  roja   10:00 UTC (06:00 UTC-4): cierra por debajo de la mecha de la 5 → OB bajista «A 2» → CORTE
#   9  verde  cierra por encima del extremo: mata al ID
OWNER_BARS: list[Bar] = [
    (1903.0, 1904.0, 1897.0, 1898.0),
    (1898.0, 1904.5, 1897.5, 1904.0),
    (1904.5, 1906.5, 1899.5, 1900.0),
    (1900.0, 1917.5, 1899.8, 1917.0),
    (1917.0, 1921.5, 1916.5, 1919.5),
    (1919.6, 1925.0, 1917.0, 1924.1),
    (1924.115, 1928.885, 1916.748, 1919.415),
    (1919.47, 1920.78, 1910.82, 1917.54),
    (1917.5, 1918.0, 1911.0, 1913.0),
    (1913.0, 1930.0, 1912.0, 1927.0),
]


@pytest.fixture
def owner_bars() -> pd.DataFrame:
    return _bars(OWNER_BARS)


@pytest.fixture
def owner_impulse(owner_bars: pd.DataFrame) -> DominantImpulse:
    return _impulse(
        owner_bars, anchor_bar=2, leg_start=3, constitution=6, anchor=1900.0, extreme=1924.1, end=9
    )


def _owner_patterns(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse, **kwargs: object
) -> pd.DataFrame:
    return patterns_inside(
        owner_bars,
        timeframe="H4",
        impulses=[owner_impulse],
        marking=_mask(len(owner_bars), 6, 7, 8),
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_la_1_59_se_marca_el_ob_del_arranque_y_no_el_fvg_grande(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """Su primera captura: al cerrar la vela de las 22 (UTC-4) se marca el OB
    alcista del ancla, con el cuerpo y no las mechas. El FVG de la pierna no:
    deja menos de la mitad del ID de recorrido hasta el extremo."""
    frame = _owner_patterns(owner_bars, owner_impulse)
    marcados_al_corte = frame[frame["indice_marcado"] == 6]

    assert list(frame.columns) == list(PATTERN_COLUMNS)
    assert len(marcados_al_corte) == 1
    ob = marcados_al_corte.iloc[0]
    assert ob["tipo"] == PatternKind.ORDER_BLOCK.value
    assert ob["direccion"] == ImpulseDirection.ALCISTA.value
    assert (ob["indice_origen"], ob["indice_conocido"], ob["indice_marcado"]) == (2, 3, 6)
    assert (ob["precio_bajo"], ob["precio_alto"]) == (1900.0, 1904.5)
    assert ob["id_num"] == 1 and ob["id_timeframe"] == "H4" and ob["timeframe"] == "H4"
    assert ob["ts_origen"] == owner_bars.index[2] and ob["ts_marcado"] == owner_bars.index[6]
    assert PatternKind.FAIR_VALUE_GAP.value not in set(frame["tipo"])


def test_con_recorrido_de_sobra_el_fvg_si_se_marca(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """El mismo hueco con el umbral a cero: es del ID y se marca al mismo corte."""
    frame = _owner_patterns(owner_bars, owner_impulse, rule=PatternRule(min_fvg_room=0.0))
    fvg = frame[frame["tipo"] == PatternKind.FAIR_VALUE_GAP.value]

    assert len(fvg) == 1
    fila = fvg.iloc[0]
    assert (fila["indice_origen"], fila["indice_conocido"], fila["indice_marcado"]) == (3, 4, 6)
    assert (fila["precio_bajo"], fila["precio_alto"]) == (1906.5, 1916.5)
    assert fila["direccion"] == ImpulseDirection.ALCISTA.value


def test_la_mecha_no_confirma_el_ob_hasta_que_lo_rompe_un_cierre(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """Su tercera y cuarta capturas: al cerrar la vela de las 2 (UTC-4) la mecha
    ya ha pasado por debajo de la verde del extremo, pero no se marca nada. Al
    cerrar la de las 6 el cierre la rompe y el OB bajista se marca, cuerpo
    [1919.6, 1924.1], pegado al extremo del ID."""
    frame = _owner_patterns(owner_bars, owner_impulse)

    assert frame[frame["indice_marcado"] == 7].empty
    al_tercer_corte = frame[frame["indice_marcado"] == 8]
    assert len(al_tercer_corte) == 1
    ob = al_tercer_corte.iloc[0]
    assert ob["tipo"] == PatternKind.ORDER_BLOCK.value
    assert ob["direccion"] == ImpulseDirection.BAJISTA.value
    assert (ob["indice_origen"], ob["indice_conocido"]) == (5, 8)
    assert (ob["precio_bajo"], ob["precio_alto"]) == (1919.6, 1924.1)


def test_el_ob_se_desmarca_con_el_cierre_que_lo_atraviesa_y_el_otro_con_su_id(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """La vela 9 cierra por encima del extremo: rompe el OB bajista por su
    borde alto y mata al ID, con lo que el OB alcista del ancla muere con él."""
    frame = _owner_patterns(owner_bars, owner_impulse).set_index("indice_origen")

    alcista, bajista = frame.loc[2], frame.loc[5]
    assert bajista["indice_fin"] == 9 and bajista["motivo_fin"] == PatternEnd.ROTURA.value
    assert alcista["indice_fin"] == 9 and alcista["motivo_fin"] == PatternEnd.MUERTE_DEL_ID.value
    assert not alcista["vivo"] and not bajista["vivo"]
    assert alcista["ts_fin"] == owner_bars.index[9]


def test_con_el_id_vivo_y_sin_rotura_el_patron_sigue_marcado(owner_bars: pd.DataFrame) -> None:
    sin_final = owner_bars.iloc[:9]
    impulse = _impulse(
        sin_final, anchor_bar=2, leg_start=3, constitution=6, anchor=1900.0, extreme=1924.1
    )
    frame = patterns_inside(
        sin_final, timeframe="H4", impulses=[impulse], marking=_mask(9, 6, 7, 8)
    )

    assert frame["vivo"].all()
    assert frame["ts_fin"].isna().all() and frame["indice_fin"].isna().all()
    assert frame["motivo_fin"].isna().all()


def test_lo_marcado_en_un_corte_no_cambia_al_llegar_mas_velas(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """No-look-ahead: con el histórico truncado justo después del primer corte,
    lo marcado hasta ahí es lo mismo que con el histórico entero; sólo cambia
    lo que se sabe después —cuándo acaba—."""
    truncated = owner_bars.iloc[:7]
    impulse = _impulse(
        truncated, anchor_bar=2, leg_start=3, constitution=6, anchor=1900.0, extreme=1924.1
    )
    parcial = patterns_inside(
        truncated, timeframe="H4", impulses=[impulse], marking=_mask(7, 6)
    )
    completo = _owner_patterns(owner_bars, owner_impulse)
    hasta_el_corte = completo[completo["indice_marcado"] <= 6].reset_index(drop=True)

    estables = [
        "tipo", "direccion", "indice_origen", "indice_conocido", "indice_marcado",
        "precio_bajo", "precio_alto",
    ]
    pd.testing.assert_frame_equal(parcial[estables], hasta_el_corte[estables])


# --- Cortes y ventana ----------------------------------------------------------


def test_lo_que_se_forma_entre_dos_cortes_espera_al_siguiente(
    owner_bars: pd.DataFrame, owner_impulse: DominantImpulse
) -> None:
    """El OB bajista se sabe al cerrar la vela 8; si ese cierre no es corte, se
    marca en el siguiente."""
    frame = patterns_inside(
        owner_bars,
        timeframe="H4",
        impulses=[owner_impulse],
        marking=_mask(len(owner_bars), 6, 7),
    )

    assert set(frame["indice_origen"]) == {2}

    # Con un corte más tarde, ya con la vela 8 cerrada.
    later = _bars([*OWNER_BARS[:9], (1913.0, 1915.0, 1912.0, 1914.0), *OWNER_BARS[9:]])
    impulse = _impulse(
        later, anchor_bar=2, leg_start=3, constitution=6, anchor=1900.0, extreme=1924.1, end=10
    )
    frame = patterns_inside(
        later, timeframe="H4", impulses=[impulse], marking=_mask(len(later), 6, 7, 9)
    )
    bajista = frame[frame["indice_origen"] == 5].iloc[0]
    assert (bajista["indice_conocido"], bajista["indice_marcado"]) == (8, 9)


def test_lo_que_se_forma_y_se_rompe_entre_dos_cortes_no_se_marca_nunca() -> None:
    """Un OB alcista que se sabe en la vela 3 y cuyo suelo atraviesa un cierre en
    la 4 no llega vivo al corte de la 5. (La verde de la 3, que ese mismo cierre
    confirma como OB bajista, sí: cabe justo en el ID.)"""
    bars = _bars(
        [
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 101.0, 99.5, 100.0),
            (100.0, 100.5, 98.0, 98.5),  # roja: candidata a OB alcista [98.5, 100]
            (98.5, 102.0, 98.4, 101.5),  # cierra por encima de su mecha: se sabe
            (101.5, 101.6, 97.0, 97.5),  # cierra por debajo del cuerpo: rota
            (97.5, 99.0, 97.0, 98.0),  # corte
            (98.0, 98.5, 96.0, 96.5),
        ]
    )
    impulse = _impulse(
        bars, anchor_bar=2, leg_start=3, constitution=4, anchor=98.5, extreme=101.5
    )
    frame = patterns_inside(bars, timeframe="H4", impulses=[impulse], marking=_mask(7, 5))

    assert 2 not in set(frame["indice_origen"])
    assert list(frame["indice_origen"]) == [3]
    assert frame.iloc[0]["direccion"] == ImpulseDirection.BAJISTA.value


def test_un_patron_anterior_a_la_vela_del_ancla_no_es_de_este_id(
    owner_bars: pd.DataFrame,
) -> None:
    """La roja de la vela 0 también tiene un cierre por encima de su mecha (la
    verde de la 1), pero es anterior al ancla: no se marca aunque cupiera."""
    impulse = _impulse(
        owner_bars,
        anchor_bar=2,
        leg_start=3,
        constitution=6,
        anchor=1897.0,  # un ancla más baja, para que el cuerpo de la 0 cupiera
        extreme=1924.1,
        end=9,
    )
    frame = patterns_inside(
        owner_bars, timeframe="H4", impulses=[impulse], marking=_mask(10, 6, 7, 8)
    )

    assert 0 not in set(frame["indice_origen"])


def test_lo_que_asoma_fuera_del_rango_del_id_no_se_marca(owner_bars: pd.DataFrame) -> None:
    """Con el ancla por encima del suelo del OB del arranque, el OB ya no cabe."""
    impulse = _impulse(
        owner_bars,
        anchor_bar=2,
        leg_start=3,
        constitution=6,
        anchor=1901.0,
        extreme=1924.1,
        end=9,
    )
    frame = patterns_inside(
        owner_bars, timeframe="H4", impulses=[impulse], marking=_mask(10, 6, 7, 8)
    )

    assert 2 not in set(frame["indice_origen"])


def test_una_contraria_intermedia_deja_a_la_anterior_sin_ob() -> None:
    """Dos rojas seguidas y una verde que cierra por encima de las dos: el OB
    es la última roja, la anterior no se marca."""
    bars = _bars(
        [
            (100.0, 101.0, 99.0, 100.5),
            (100.5, 100.8, 99.0, 99.2),  # roja
            (99.2, 99.6, 98.0, 98.4),  # roja: la última contraria
            (98.4, 103.0, 98.3, 102.8),  # verde: cierra por encima de las dos
            (102.8, 103.2, 101.0, 101.5),  # roja: constituye
            (101.5, 102.0, 100.5, 101.0),  # corte
        ]
    )
    impulse = _impulse(
        bars, anchor_bar=2, leg_start=3, constitution=4, anchor=98.4, extreme=102.8
    )
    frame = patterns_inside(bars, timeframe="H4", impulses=[impulse], marking=_mask(6, 5))
    obs = frame[frame["tipo"] == PatternKind.ORDER_BLOCK.value]

    assert set(obs["indice_origen"]) == {2}


def test_muerto_con_su_id_puede_volver_a_marcarse_bajo_el_siguiente() -> None:
    """Lo marcado muere con su ID; si cabe en el que viene después, está
    formado desde su vela del ancla y sigue sin romperse, se marca otra vez con
    el nuevo número. Lo anterior al ancla nueva, no."""
    bars = _bars(
        [
            (100.0, 100.5, 99.0, 99.2),  # 0 roja: ancla del ID 1 y OB alcista [99.2, 100]
            (99.2, 104.0, 99.1, 103.5),  # 1 verde: la pierna
            (103.5, 103.5, 102.0, 102.5),  # 2 roja: constituye el ID 1 [99.2, 103.5]
            (102.5, 102.6, 101.8, 102.0),  # 3 roja: OB alcista [102, 102.5]; ancla del ID 2; corte
            (102.0, 103.0, 101.9, 102.9),  # 4 verde: confirma el OB de la 3
            (102.9, 103.45, 103.1, 103.4),  # 5 verde: deja un FVG [102.6, 103.1]; corte
            (103.4, 106.2, 103.3, 106.0),  # 6 verde: rompe el ID 1 a favor
            (106.0, 106.1, 105.3, 105.5),  # 7 roja: constituye el ID 2 [102, 106]; corte
        ]
    )
    first = _impulse(
        bars, id_num=1, anchor_bar=0, leg_start=1, constitution=2, anchor=99.2, extreme=103.5, end=6
    )
    second = _impulse(
        bars, id_num=2, anchor_bar=3, leg_start=4, constitution=7, anchor=102.0, extreme=106.0
    )
    frame = patterns_inside(
        bars,
        timeframe="H4",
        impulses=[first, second],
        marking=_mask(8, 3, 5, 7),
        rule=PatternRule(min_fvg_room=0.0),
    )
    por_origen = {
        origen: grupo.sort_values("indice_marcado")
        for origen, grupo in frame.groupby("indice_origen")
    }

    # El OB del arranque del ID 1: suyo, muere con él y no vuelve (es anterior
    # al ancla del ID 2).
    assert list(por_origen[0]["id_num"]) == [1]
    assert por_origen[0].iloc[0]["motivo_fin"] == PatternEnd.MUERTE_DEL_ID.value
    # El OB de la vela 3 y el FVG de la 4: marcados bajo el ID 1, muertos con
    # él en la vela 6 y marcados otra vez bajo el ID 2 en el corte de la 7.
    for origen, tipo in ((3, PatternKind.ORDER_BLOCK), (4, PatternKind.FAIR_VALUE_GAP)):
        filas = por_origen[origen]
        assert list(filas["tipo"]) == [tipo.value, tipo.value], origen
        assert list(filas["id_num"]) == [1, 2], origen
        assert list(filas["indice_marcado"]) == [5, 7], origen
        assert filas.iloc[0]["indice_fin"] == 6
        assert filas.iloc[0]["motivo_fin"] == PatternEnd.MUERTE_DEL_ID.value
        assert filas.iloc[1]["vivo"]


def test_sin_id_vigente_en_el_corte_no_se_marca_nada(owner_bars: pd.DataFrame) -> None:
    impulse = _impulse(
        owner_bars, anchor_bar=2, leg_start=3, constitution=6, anchor=1900.0, extreme=1924.1, end=9
    )
    # Cortes antes de constituirse y después de morir.
    frame = patterns_inside(
        owner_bars, timeframe="H4", impulses=[impulse], marking=_mask(10, 3, 4, 5, 9)
    )

    assert frame.empty


def test_un_impulso_no_publicable_no_marca(owner_bars: pd.DataFrame) -> None:
    impulse = _impulse(
        owner_bars,
        anchor_bar=2,
        leg_start=3,
        constitution=6,
        anchor=1900.0,
        extreme=1924.1,
        publishable=False,
    )
    frame = patterns_inside(
        owner_bars, timeframe="H4", impulses=[impulse], marking=_mask(10, 6, 7, 8)
    )

    assert frame.empty


# --- Casos límite ----------------------------------------------------------------


def test_sin_velas_o_con_una_no_hay_patrones() -> None:
    vacio = _bars([])
    assert patterns_inside(vacio, timeframe="H4", impulses=[], marking=_mask(0)).empty
    una = _bars([(100.0, 101.0, 99.0, 100.5)])
    assert patterns_inside(una, timeframe="H4", impulses=[], marking=_mask(1)).empty


def test_la_mascara_tiene_que_medir_lo_que_las_velas(owner_bars: pd.DataFrame) -> None:
    with pytest.raises(DomainError, match="máscara"):
        patterns_inside(owner_bars, timeframe="H4", impulses=[], marking=_mask(3))


def test_las_velas_exigen_indice_utc_y_columnas() -> None:
    naive = _bars([(1.0, 2.0, 0.5, 1.5)] * 3)
    naive.index = pd.DatetimeIndex(naive.index).tz_localize(None)
    with pytest.raises(DomainError, match="tz-aware"):
        patterns_inside(naive, timeframe="H4", impulses=[], marking=_mask(3))
    with pytest.raises(DomainError, match="columnas"):
        patterns_inside(
            _bars([(1.0, 2.0, 0.5, 1.5)] * 3).drop(columns=["high"]),
            timeframe="H4",
            impulses=[],
            marking=_mask(3),
        )


def test_la_regla_valida_sus_parametros() -> None:
    with pytest.raises(DomainError):
        PatternRule(marking_slots=())
    with pytest.raises(DomainError):
        PatternRule(marking_slots=(-1,))
    with pytest.raises(DomainError):
        PatternRule(min_fvg_room=1.5)


def test_la_regla_dice_en_que_velas_del_dia_marca() -> None:
    rule = PatternRule()
    assert rule.marking_slots == (1, 2, 3)
    assert rule.marks_at(np.array([0, 1, 2, 3, 4, 5, 0])).tolist() == [
        False, True, True, True, False, False, False,
    ]
    assert "2.ª, 3.ª, 4.ª vela" in rule.describe()
    assert "50%" in rule.describe()
