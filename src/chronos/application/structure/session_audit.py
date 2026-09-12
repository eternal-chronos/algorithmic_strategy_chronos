"""Censo de velas cortas y su impacto sobre la estructura (B.1 y B.2).

El mercado de oro abre el domingo a las 23:00 UTC. Con `D_SESSION_START` en
00:00 esa hora no cabe en la vela del lunes: se queda sola en una vela diaria
propia, con una sola hora de mercado dentro. Esa vela tiene color y cuerpo, así
que para la máquina es una vela como cualquier otra —abre piernas, fija anclas y
extremos, constituye y rompe—, y el histórico acaba con seis velas diarias por
semana donde un gráfico de oro tiene cinco.

Aquí sólo se **mide**: cuántas velas contienen menos mercado del que su
temporalidad promete y qué papel juegan en los impulsos que ya salieron del
detector. Nada de esto entra en la detección ni elige ningún corte de sesión.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from chronos.application.structure.config import DAILY, H1, H4, M5, M15
from chronos.application.structure.detect_impulses import TimeframeAnalysis
from chronos.application.structure.lateralization import DEGENERATE_RANGE_ATR
from chronos.application.structure.statistics import whipsaw_episodes
from chronos.domain.structure.enums import MachineState

#: Minutos de mercado que promete cada temporalidad cuando la vela está completa.
EXPECTED_MINUTES: dict[str, int] = {M5: 5, M15: 15, H1: 60, H4: 240, DAILY: 1440}

#: Por debajo de esta fracción de sus minutos, la vela se cuenta como corta. Sale
#: del enunciado del propietario, no de una búsqueda de umbrales.
SHORT_FRACTION = 0.25

#: Percentiles del reparto de minutos por vela que pide B.1.
MINUTE_PERCENTILES = (1, 5, 10)

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

#: Los cuatro papeles que una vela puede jugar en un ID (B.2).
ROLES = ("ancla", "extremo", "constitucion", "rotura")

CENSUS_COLUMNS = (
    "anio",
    "velas",
    "semanas",
    "velas_por_semana",
    "p1",
    "p5",
    "p10",
    "mediana",
    "cortas",
    "pct_cortas",
)

WEEKDAY_COLUMNS = ("dia", "velas", "cortas", "pct_de_las_cortas")

ROLE_COLUMNS = ("id_num", "ts_constitucion", *[f"{role}_corta" for role in ROLES], "algun_papel")


@dataclass(frozen=True, slots=True)
class TimeframeSessionAudit:
    """Censo e impacto de una temporalidad."""

    timeframe: str
    expected_minutes: int
    bars: int
    short_bars: int
    #: Un registro por año más la fila TOTAL.
    census: pd.DataFrame
    #: En qué día de la semana caen las velas cortas.
    by_weekday: pd.DataFrame
    #: Un registro por ID publicado con sus cuatro papeles marcados.
    roles: pd.DataFrame
    #: Recuentos de B.2, ya agregados.
    impact: dict[str, float]

    @property
    def short_share(self) -> float:
        return self.short_bars / self.bars if self.bars else float("nan")


@dataclass(frozen=True, slots=True)
class SessionAudit:
    per_timeframe: dict[str, TimeframeSessionAudit]


def short_bars(minutes: np.ndarray, timeframe: str) -> np.ndarray:
    """Máscara de las velas con menos del 25 % de sus minutos."""
    return np.asarray(minutes) < SHORT_FRACTION * EXPECTED_MINUTES[timeframe]


def audit_session(
    analyses: Mapping[str, TimeframeAnalysis], minutes: Mapping[str, np.ndarray]
) -> SessionAudit:
    """Censo e impacto de cada temporalidad con detector propio."""
    return SessionAudit(
        per_timeframe={
            timeframe: audit_timeframe(analysis, np.asarray(minutes[timeframe]))
            for timeframe, analysis in analyses.items()
            if timeframe in minutes
        }
    )


def audit_timeframe(analysis: TimeframeAnalysis, minutes: np.ndarray) -> TimeframeSessionAudit:
    index = pd.DatetimeIndex(analysis.bars.index)
    if len(minutes) != len(index):
        raise ValueError(
            f"{analysis.timeframe}: {len(minutes)} recuentos de minutos para "
            f"{len(index)} velas"
        )
    short = short_bars(minutes, analysis.timeframe)
    roles = _roles(analysis, short)
    return TimeframeSessionAudit(
        timeframe=analysis.timeframe,
        expected_minutes=EXPECTED_MINUTES[analysis.timeframe],
        bars=len(index),
        short_bars=int(short.sum()),
        census=_census(index, minutes, short),
        by_weekday=_by_weekday(index, short),
        roles=roles,
        impact=_impact(analysis, roles),
    )


# --- B.1 --------------------------------------------------------------------


def _census(index: pd.DatetimeIndex, minutes: np.ndarray, short: np.ndarray) -> pd.DataFrame:
    def row(label: str, mask: np.ndarray) -> dict[str, object]:
        sample = minutes[mask]
        weeks = _weeks_active(index[mask])
        count = int(mask.sum())
        short_count = int(short[mask].sum())
        percentiles = {
            f"p{value}": float(np.percentile(sample, value)) if sample.size else float("nan")
            for value in MINUTE_PERCENTILES
        }
        return {
            "anio": label,
            "velas": count,
            "semanas": weeks,
            "velas_por_semana": count / weeks if weeks else float("nan"),
            **percentiles,
            "mediana": float(np.median(sample)) if sample.size else float("nan"),
            "cortas": short_count,
            "pct_cortas": 100.0 * short_count / count if count else float("nan"),
        }

    years = index.year.to_numpy()
    rows = [row(str(year), years == year) for year in sorted({int(v) for v in years})]
    rows.append(row("TOTAL", np.ones(len(index), dtype=bool)))
    return pd.DataFrame(rows, columns=list(CENSUS_COLUMNS))


def _weeks_active(index: pd.DatetimeIndex) -> int:
    """Semanas ISO con al menos una vela.

    Dividir por 52 daría un promedio falso: el histórico no empieza ni termina
    en frontera de año, y lo que se compara es "velas por semana de mercado".
    """
    if index.empty:
        return 0
    iso = index.isocalendar()
    return len(set(zip(iso["year"], iso["week"], strict=True)))


def _by_weekday(index: pd.DatetimeIndex, short: np.ndarray) -> pd.DataFrame:
    weekdays = index.dayofweek.to_numpy()
    total_short = int(short.sum())
    rows = [
        {
            "dia": name,
            "velas": int((weekdays == number).sum()),
            "cortas": int((weekdays[short] == number).sum()),
            "pct_de_las_cortas": (
                100.0 * int((weekdays[short] == number).sum()) / total_short
                if total_short
                else float("nan")
            ),
        }
        for number, name in enumerate(WEEKDAYS)
    ]
    return pd.DataFrame(rows, columns=list(WEEKDAY_COLUMNS))


# --- B.2 --------------------------------------------------------------------


def _roles(analysis: TimeframeAnalysis, short: np.ndarray) -> pd.DataFrame:
    """Qué papel juega una vela corta en cada ID publicado.

    Se leen las posiciones que el detector ya guardó —ancla, extremo,
    constitución y rotura— en vez de buscar qué vela coincide con cada precio,
    que con empates no da una respuesta única.
    """
    rows = [
        {
            "id_num": impulse.id_num,
            "ts_constitucion": impulse.ts_constitution,
            "ancla_corta": bool(short[impulse.index_anchor]),
            "extremo_corta": bool(short[impulse.index_extreme]),
            "constitucion_corta": bool(short[impulse.index_constitution]),
            # Un ID todavía vigente no lo ha roto ninguna vela.
            "rotura_corta": (
                bool(short[impulse.index_end]) if impulse.index_end is not None else False
            ),
        }
        for impulse in analysis.published
    ]
    frame = pd.DataFrame(rows, columns=list(ROLE_COLUMNS[:-1]))
    marks = [f"{role}_corta" for role in ROLES]
    frame["algun_papel"] = frame[marks].any(axis=1) if not frame.empty else pd.Series(dtype=bool)
    return frame


def _impact(analysis: TimeframeAnalysis, roles: pd.DataFrame) -> dict[str, float]:
    table = analysis.table
    if roles.empty:
        return {f"{role}_en_vela_corta": 0 for role in ROLES}

    by_id = roles.set_index("id_num")
    dwarfs = table.loc[table["rango_atr"] < DEGENERATE_RANGE_ATR, "id_num"].to_numpy()
    dwarfs_touched = int(by_id.reindex(dwarfs)["algun_papel"].fillna(False).sum())

    episodes = whipsaw_episodes(table)
    if episodes.empty:
        whipsaws = 0
        whipsaws_touched = 0
    else:
        whipsaws = len(episodes)
        # El episodio es ida y vuelta: basta con que una vela corta intervenga en
        # cualquiera de los dos ID que lo forman.
        going = by_id.reindex(episodes["id_ida"])["algun_papel"].fillna(False).to_numpy()
        back = by_id.reindex(episodes["id_vuelta"])["algun_papel"].fillna(False).to_numpy()
        whipsaws_touched = int((going | back).sum())

    impact: dict[str, float] = {
        "id_publicados": len(roles),
        **{f"{role}_en_vela_corta": int(roles[f"{role}_corta"].sum()) for role in ROLES},
        "id_con_algun_papel": int(roles["algun_papel"].sum()),
        "id_enanos": len(dwarfs),
        "id_enanos_con_vela_corta": dwarfs_touched,
        "pct_enanos_con_vela_corta": _share(dwarfs_touched, len(dwarfs)),
        "episodios_latigazo": whipsaws,
        "latigazos_con_vela_corta": whipsaws_touched,
        "pct_latigazos_con_vela_corta": _share(whipsaws_touched, whipsaws),
    }
    return impact


def _share(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else float("nan")


# --- B.3 --------------------------------------------------------------------

CUT_COLUMNS = (
    "corte",
    "n_velas",
    "velas_por_semana",
    "n_velas_cortas",
    "n_ID",
    "dur_mediana_ID",
    "pct_barras_limbo",
    "n_ID_bajo_0.25ATR",
    "n_episodios_latigazo",
)


def cut_metrics(label: str, analysis: TimeframeAnalysis, minutes: np.ndarray) -> dict[str, object]:
    """Una fila de la comparativa de cortes de sesión."""
    index = pd.DatetimeIndex(analysis.bars.index)
    table = analysis.table
    durations = table["n_barras_id"].to_numpy(dtype=float)
    in_limbo = np.array([state.state is MachineState.LIMBO for state in analysis.states])
    weeks = _weeks_active(index)
    return {
        "corte": label,
        "n_velas": len(index),
        "velas_por_semana": len(index) / weeks if weeks else float("nan"),
        "n_velas_cortas": int(short_bars(minutes, analysis.timeframe).sum()),
        "n_ID": len(table),
        "dur_mediana_ID": float(np.median(durations)) if durations.size else float("nan"),
        "pct_barras_limbo": 100.0 * float(in_limbo.mean()) if in_limbo.size else float("nan"),
        "n_ID_bajo_0.25ATR": int((table["rango_atr"] < DEGENERATE_RANGE_ATR).sum()),
        "n_episodios_latigazo": len(whipsaw_episodes(table)),
    }


def equivalent_cuts(bars_by_cut: Mapping[str, pd.DataFrame]) -> list[tuple[str, ...]]:
    """Agrupa los cortes que producen exactamente las mismas velas.

    Dos cortes distintos pueden dar el mismo gráfico: si el corte cae dentro de
    una parada del mercado, mover la frontera por ese hueco no reparte ni un
    minuto de forma distinta y sólo cambia la marca de tiempo con que se etiqueta
    la vela. Detectarlo importa porque significa que **esos cortes no se pueden
    distinguir mirando el OHLC**, por muchas fechas que se comparen.

    Se comparan los valores, no el índice: la etiqueta es justo lo que cambia.
    """
    groups: dict[tuple[int, bytes], list[str]] = {}
    for label, bars in bars_by_cut.items():
        values = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)
        key = (len(bars), values.tobytes())
        groups.setdefault(key, []).append(label)
    return [tuple(labels) for labels in groups.values() if len(labels) > 1]


# --- B.4 --------------------------------------------------------------------

CONTRAST_COLUMNS = (
    "fecha_sesion",
    "corte",
    "etiqueta_utc",
    "open",
    "high",
    "low",
    "close",
    "minutos_m1",
)

DECIMALS = 4


def session_dates(index: pd.DatetimeIndex) -> np.ndarray:
    """Fecha de sesión de cada vela diaria.

    Un corte a las 21, 22 o 23 h UTC etiqueta la vela el día **anterior** al que
    el propietario ve en su gráfico: para su bróker esa sesión ya es la del día
    siguiente. Sin esta traducción, comparar la vela "del 8 de marzo" contra
    TradingView compararía dos días distintos.
    """
    shift = pd.to_timedelta((index.hour >= 12).astype(int), unit="D")
    return (index.normalize() + shift).date


def contrast_rows(
    label: str, bars: pd.DataFrame, minutes: np.ndarray, dates: Sequence[str]
) -> list[dict[str, object]]:
    """OHLC de las velas diarias pedidas, para cotejarlas contra TradingView."""
    index = pd.DatetimeIndex(bars.index)
    sessions = session_dates(index)
    rows: list[dict[str, object]] = []
    for date in dates:
        target = pd.Timestamp(date).date()
        found = np.flatnonzero(sessions == target)
        if found.size == 0:
            rows.append({"fecha_sesion": date, "corte": label, "etiqueta_utc": "sin vela"})
            continue
        position = int(found[0])
        bar = bars.iloc[position]
        rows.append(
            {
                "fecha_sesion": date,
                "corte": label,
                "etiqueta_utc": str(index[position]),
                **{
                    field: round(float(bar[field]), DECIMALS)
                    for field in ("open", "high", "low", "close")
                },
                "minutos_m1": int(minutes[position]),
            }
        )
    return rows


# --- Parte A ----------------------------------------------------------------

COLOUR_COLUMNS = (
    "temporalidad",
    "id_publicados",
    "ancla_contraria",
    "ancla_a_favor",
    "ancla_doji",
    "extremo_color_contrario",
)


def colour_audit(analysis: TimeframeAnalysis) -> dict[str, object]:
    """Color de la vela que fija el ancla y de la que fija el extremo.

    La regla del propietario (R-02) sitúa el ancla en la última vela **contraria**
    previa a la pierna, de modo que con A1 su color es correcto por construcción;
    con A2 el ancla sale de la primera vela de la pierna, que en las roturas
    adoptadas por `L1_actual` puede venir del color equivocado. El extremo es otra
    cosa (R-36) y el modo de ancla no lo toca: se cuenta aparte.
    """
    bars = analysis.bars
    bodies = (bars["close"] - bars["open"]).to_numpy(dtype=float)
    counter = same = doji = 0
    for impulse in analysis.published:
        body = bodies[impulse.index_anchor]
        if body == 0:
            doji += 1
        elif (body > 0) == (impulse.direction.value == "alcista"):
            same += 1
        else:
            counter += 1
    return {
        "temporalidad": analysis.timeframe,
        "id_publicados": len(analysis.published),
        "ancla_contraria": counter,
        "ancla_a_favor": same,
        "ancla_doji": doji,
        "extremo_color_contrario": sum(
            1 for impulse in analysis.published if impulse.extreme_on_counter_bar
        ),
    }
