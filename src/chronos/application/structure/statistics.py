"""Estadística descriptiva de los impulsos detectados (§5.2).

Todo va desglosado por año: el oro pasó de ~1.200 a ~4.300 USD en el histórico y
un agregado global mezcla regímenes que no son comparables. Por eso el rango se
reporta además en ATR y en % del precio, no sólo en dólares.

Aquí no se optimiza ni se puntúa nada: son los números que el propietario mira
antes de decidir si la regla escrita coincide con su ojo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.domain.structure.enums import BreakKind, ImpulseDirection, MachineState

TOTAL_ROW = "TOTAL"

#: Percentiles del tamaño del impulso (C.5). La cola baja va detallada porque es
#: donde vive la pregunta del propietario: cuántos impulsos son demasiado
#: pequeños para que su ojo los marque.
SIZE_PERCENTILES = (1, 5, 10, 25, 50, 75, 90)

#: Un cuerpo por debajo de esta fracción del ATR previo se cuenta aparte (C.7).
#: Sale del enunciado, no de una búsqueda de valores.
TINY_BODY_ATR = 0.1

#: Ventanas del latigazo direccional (C.8), en barras.
WHIPSAW_WINDOWS = (1, 2, 3, 5)

#: Marca del estado LIMBO en la tabla de sesgos simultáneos (C.4).
LIMBO_MARK = MachineState.LIMBO.value

#: La diferencia entre las dos candidaturas de ancla se declara despreciable si
#: su p90 no llega a este porcentaje del rango mediano de los impulsos. Es un
#: criterio de *informe* para no hacer perder el tiempo al propietario, no una
#: regla de la estrategia: los dos valores se imprimen igualmente.
NEGLIGIBLE_ANCHOR_FRACTION = 0.01


@dataclass(frozen=True, slots=True)
class TimeframeStatistics:
    """Tablas de una temporalidad, listas para renderizar."""

    timeframe: str
    impulses: int
    bars: int
    counts_by_year: pd.DataFrame
    id_duration: pd.DataFrame
    limbo_duration: pd.DataFrame
    #: C.3 — cuánto tiempo del histórico pasa el sistema sin sesgo, por año.
    limbo_share: pd.DataFrame
    range_usd: pd.DataFrame
    range_atr: pd.DataFrame
    range_pct: pd.DataFrame
    breaks_by_year: pd.DataFrame
    #: C.6 — roturas cruzadas con la dirección del ID roto.
    breaks_by_direction: pd.DataFrame
    bodies_by_year: pd.DataFrame
    #: C.7 — el cuerpo que constituye, medido en ATR previo.
    constituting_bodies: pd.DataFrame
    #: C.8 — cambios de sesgo que se deshacen a las pocas barras.
    whipsaws: pd.DataFrame
    whipsaw_episodes: pd.DataFrame
    anchor_gap: pd.DataFrame
    anchor_verdict: str
    tiny_body_threshold: float
    diagnostics: dict[str, int]


@dataclass(frozen=True, slots=True)
class ImpulseStatistics:
    per_timeframe: dict[str, TimeframeStatistics]
    #: C.4 — sesgos simultáneos de las tres temporalidades. Vacía si no hay al
    #: menos dos con detector propio.
    alignment: pd.DataFrame = field(default_factory=pd.DataFrame)


def summarize(run: ImpulseRun) -> ImpulseStatistics:
    """Calcula toda la estadística del informe. Con el módulo apagado, nada."""
    if not run.enabled:
        return ImpulseStatistics(per_timeframe={})
    return ImpulseStatistics(
        per_timeframe={
            timeframe: _summarize_timeframe(analysis)
            for timeframe, analysis in run.analyses.items()
        },
        alignment=simultaneous_bias(run),
    )


def _summarize_timeframe(analysis: TimeframeAnalysis) -> TimeframeStatistics:
    table = analysis.table.copy()
    if not table.empty:
        table["anio"] = pd.DatetimeIndex(table["ts_constitucion"]).year
        table["cuerpo_en_atr"] = _safe_division(
            table["cuerpo_vela_constituyente"], table["atr_previo"]
        )

    bars = analysis.bars
    body_size = (bars["close"] - bars["open"]).abs().to_numpy(dtype=float)
    non_doji = body_size[body_size > 0]
    threshold = float(np.percentile(non_doji, 10)) if non_doji.size else 0.0

    # Un doji en limbo no constituye, así que el limbo sigue una barra más: eso
    # es "prolongar el limbo" (C.7). Se cuenta aquí y no en el detector porque el
    # detector no se toca en esta fase.
    diagnostics = dict(analysis.diagnostics)
    diagnostics["dojis_en_limbo"] = _dojis_in_limbo(analysis, body_size)

    return TimeframeStatistics(
        timeframe=analysis.timeframe,
        impulses=len(table),
        bars=len(bars),
        counts_by_year=_counts_by_year(table),
        id_duration=_distribution_by_year(table, "n_barras_id", with_max=True),
        limbo_duration=_distribution_by_year(table, "n_barras_limbo", with_max=True),
        limbo_share=_limbo_share(analysis),
        range_usd=_percentiles_by_year(table, "rango_usd"),
        range_atr=_percentiles_by_year(table, "rango_atr"),
        range_pct=_percentiles_by_year(table, "rango_pct_precio"),
        breaks_by_year=_breaks_by_year(table),
        breaks_by_direction=_breaks_by_direction(table),
        bodies_by_year=_bodies_by_year(analysis, table, threshold),
        constituting_bodies=_constituting_bodies(table),
        whipsaws=_whipsaws_by_year(table),
        whipsaw_episodes=whipsaw_episodes(table),
        anchor_gap=_distribution_by_year(table, "diferencia_anclas_usd", with_max=True),
        anchor_verdict=_anchor_verdict(table),
        tiny_body_threshold=threshold,
        diagnostics=diagnostics,
    )


def _dojis_in_limbo(analysis: TimeframeAnalysis, body_size: np.ndarray) -> int:
    states = analysis.states
    if not states or body_size.size == 0:
        return 0
    in_limbo = np.array([state.state is MachineState.LIMBO for state in states])
    return int(np.count_nonzero((body_size == 0) & in_limbo[: body_size.size]))


# --- Tablas -----------------------------------------------------------------


def _counts_by_year(table: pd.DataFrame) -> pd.DataFrame:
    columns = ["anio", "alcistas", "bajistas", "total"]
    if table.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for year, group in table.groupby("anio", sort=True):
        rows.append(_direction_row(str(year), group))
    rows.append(_direction_row(TOTAL_ROW, table))
    return pd.DataFrame(rows, columns=columns)


def _direction_row(label: str, group: pd.DataFrame) -> dict[str, object]:
    return {
        "anio": label,
        "alcistas": int((group["direccion"] == ImpulseDirection.ALCISTA.value).sum()),
        "bajistas": int((group["direccion"] == ImpulseDirection.BAJISTA.value).sum()),
        "total": len(group),
    }


def _limbo_share(analysis: TimeframeAnalysis) -> pd.DataFrame:
    """C.3 — porcentaje de barras en LIMBO y episodios de limbo, por año.

    El episodio se cuenta en el año de su **primera** barra: un limbo que cruza
    el fin de año es uno, no dos.
    """
    columns = ["anio", "barras", "barras_limbo", "pct_limbo", "episodios"]
    states = analysis.states
    if not states:
        return pd.DataFrame(columns=columns)

    years = np.array([state.timestamp.year for state in states])
    in_limbo = np.array([state.state is MachineState.LIMBO for state in states])
    # Primera barra de cada racha de limbo: la que arranca un episodio.
    starts = in_limbo & ~np.concatenate(([False], in_limbo[:-1]))

    rows = []
    for year in sorted({int(value) for value in years}):
        mask = years == year
        rows.append(_limbo_row(str(year), in_limbo[mask], starts[mask]))
    rows.append(_limbo_row(TOTAL_ROW, in_limbo, starts))
    return pd.DataFrame(rows, columns=columns)


def _limbo_row(label: str, in_limbo: np.ndarray, starts: np.ndarray) -> dict[str, object]:
    bars = int(in_limbo.size)
    limbo = int(in_limbo.sum())
    return {
        "anio": label,
        "barras": bars,
        "barras_limbo": limbo,
        "pct_limbo": limbo / bars if bars else float("nan"),
        "episodios": int(starts.sum()),
    }


def _breaks_by_year(table: pd.DataFrame) -> pd.DataFrame:
    columns = ["anio", "a_favor", "en_contra", "sin_romper", "total", "ratio_favor_contra"]
    if table.empty:
        return pd.DataFrame(columns=columns)

    rows = [_break_row(str(year), group) for year, group in table.groupby("anio", sort=True)]
    rows.append(_break_row(TOTAL_ROW, table))
    return pd.DataFrame(rows, columns=columns)


def _breaks_by_direction(table: pd.DataFrame) -> pd.DataFrame:
    """C.6 — las mismas roturas, cruzadas con la dirección del ID roto."""
    columns = [
        "anio", "direccion", "a_favor", "en_contra", "sin_romper", "total", "ratio_favor_contra"
    ]
    if table.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for direction in (ImpulseDirection.ALCISTA, ImpulseDirection.BAJISTA):
        subset = table[table["direccion"] == direction.value]
        for year, group in subset.groupby("anio", sort=True):
            rows.append({"direccion": direction.value, **_break_row(str(year), group)})
        rows.append({"direccion": direction.value, **_break_row(TOTAL_ROW, subset)})
    return pd.DataFrame(rows, columns=columns)


def _break_row(label: str, group: pd.DataFrame) -> dict[str, object]:
    kinds = group["tipo_rotura_salida"]
    favor = int((kinds == BreakKind.A_FAVOR.value).sum())
    against = int((kinds == BreakKind.EN_CONTRA.value).sum())
    return {
        "anio": label,
        "a_favor": favor,
        "en_contra": against,
        "sin_romper": int(kinds.isna().sum()),
        "total": len(group),
        "ratio_favor_contra": favor / against if against else float("nan"),
    }


def _bodies_by_year(
    analysis: TimeframeAnalysis, table: pd.DataFrame, threshold: float
) -> pd.DataFrame:
    """Dojis del histórico y constituciones nacidas de una vela contraria mínima.

    Es el número que responde a la pregunta del propietario: ¿con qué frecuencia
    una vela contraria minúscula está creando impulsos que su ojo no marcaría?
    """
    columns = ["anio", "barras", "dojis", "impulsos", "por_cuerpo_minimo", "pct_minimo"]
    bars = analysis.bars
    if bars.empty:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame(
        {
            "anio": pd.DatetimeIndex(bars.index).year,
            "doji": (bars["close"].to_numpy(dtype=float) == bars["open"].to_numpy(dtype=float)),
        }
    )

    rows = []
    years = sorted(set(frame["anio"].unique()))
    for year in years:
        bar_group = frame[frame["anio"] == year]
        impulse_group = table[table["anio"] == year] if not table.empty else table
        rows.append(_body_row(str(year), bar_group, impulse_group, threshold))
    rows.append(_body_row(TOTAL_ROW, frame, table, threshold))
    return pd.DataFrame(rows, columns=columns)


def _body_row(
    label: str, bar_group: pd.DataFrame, impulse_group: pd.DataFrame, threshold: float
) -> dict[str, object]:
    impulses = len(impulse_group)
    if impulses and "cuerpo_vela_constituyente" in impulse_group.columns:
        tiny = int((impulse_group["cuerpo_vela_constituyente"] <= threshold).sum())
    else:
        tiny = 0
    return {
        "anio": label,
        "barras": len(bar_group),
        "dojis": int(bar_group["doji"].sum()),
        "impulsos": impulses,
        "por_cuerpo_minimo": tiny,
        "pct_minimo": (tiny / impulses) if impulses else float("nan"),
    }


def _constituting_bodies(table: pd.DataFrame) -> pd.DataFrame:
    """C.7 — el cuerpo de la vela que constituye, medido en ATR previo.

    La pregunta del propietario en una tabla: con qué frecuencia un cuerpo que
    en su pantalla no se ve está creando un impulso.
    """
    columns = ["anio", "n", "mediana", "p10", "p25", "bajo_0_1_atr", "pct_bajo_0_1_atr"]
    if table.empty or "cuerpo_en_atr" not in table.columns:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        values = _clean(group["cuerpo_en_atr"])
        tiny = int((values < TINY_BODY_ATR).sum())
        return {
            "anio": label,
            "n": int(values.size),
            "mediana": _percentile(values, 50),
            "p10": _percentile(values, 10),
            "p25": _percentile(values, 25),
            "bajo_0_1_atr": tiny,
            "pct_bajo_0_1_atr": tiny / values.size if values.size else float("nan"),
        }

    rows = [row(str(year), group) for year, group in table.groupby("anio", sort=True)]
    rows.append(row(TOTAL_ROW, table))
    return pd.DataFrame(rows, columns=columns)


def whipsaw_episodes(table: pd.DataFrame) -> pd.DataFrame:
    """C.8 — episodios en que el sesgo se va y vuelve, uno por fila.

    Un episodio son tres ID consecutivos con direcciones A, B, A: el sesgo se
    invirtió y se deshizo. Su duración es el número de barras que el sesgo B
    estuvo vigente, medido de constitución a constitución.
    """
    columns = ["anio", "id_ida", "id_vuelta", "direccion_original", "barras", "ts_ida"]
    if len(table) < 3:
        return pd.DataFrame(columns=columns)

    ordered = table.sort_values("ts_constitucion").reset_index(drop=True)
    directions = ordered["direccion"].to_numpy()
    stamps = pd.DatetimeIndex(ordered["ts_constitucion"])
    indices = ordered["indice_constitucion"].to_numpy(dtype=int)
    ids = ordered["id_num"].to_numpy(dtype=int)

    # Barras y no tiempo: entre el viernes y el domingo hay dos días de reloj y
    # cero barras, y lo que se está midiendo es cuántas velas duró el desvío.
    flips = np.flatnonzero(
        (directions[:-2] != directions[1:-1]) & (directions[2:] == directions[:-2])
    )
    rows = [
        {
            "anio": int(stamps[int(position) + 1].year),
            "id_ida": int(ids[position + 1]),
            "id_vuelta": int(ids[position + 2]),
            "direccion_original": directions[position],
            "barras": int(indices[position + 2] - indices[position + 1]),
            "ts_ida": stamps[int(position) + 1],
        }
        for position in flips
    ]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


def _whipsaws_by_year(table: pd.DataFrame) -> pd.DataFrame:
    """C.8 — recuento de latigazos por año y ventana."""
    columns = ["anio", "episodios", *[f"hasta_{window}" for window in WHIPSAW_WINDOWS]]
    episodes = whipsaw_episodes(table)
    if episodes.empty:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        bars = group["barras"].to_numpy(dtype=float)
        return {
            "anio": label,
            "episodios": len(group),
            **{
                f"hasta_{window}": int((bars <= window).sum())
                for window in WHIPSAW_WINDOWS
            },
        }

    rows = [row(str(year), group) for year, group in episodes.groupby("anio", sort=True)]
    rows.append(row(TOTAL_ROW, episodes))
    return pd.DataFrame(rows, columns=columns)


def _percentiles_by_year(table: pd.DataFrame, column: str) -> pd.DataFrame:
    """C.5 — la distribución completa del tamaño, con la cola baja detallada."""
    columns = ["anio", "n", *[f"p{value}" for value in SIZE_PERCENTILES]]
    if table.empty:
        return pd.DataFrame(columns=columns)

    def row(label: str, values: pd.Series) -> dict[str, object]:
        clean = _clean(values)
        return {
            "anio": label,
            "n": int(clean.size),
            **{f"p{value}": _percentile(clean, value) for value in SIZE_PERCENTILES},
        }

    rows = [
        row(str(year), group[column]) for year, group in table.groupby("anio", sort=True)
    ]
    rows.append(row(TOTAL_ROW, table[column]))
    return pd.DataFrame(rows, columns=columns)


def _distribution_by_year(
    table: pd.DataFrame, column: str, *, with_max: bool
) -> pd.DataFrame:
    columns = ["anio", "n", "mediana", "p10", "p90"] + (["maximo"] if with_max else [])
    if table.empty:
        return pd.DataFrame(columns=columns)

    rows = [
        _distribution_row(str(year), group[column], with_max=with_max)
        for year, group in table.groupby("anio", sort=True)
    ]
    rows.append(_distribution_row(TOTAL_ROW, table[column], with_max=with_max))
    return pd.DataFrame(rows, columns=columns)


def _distribution_row(label: str, values: pd.Series, *, with_max: bool) -> dict[str, object]:
    clean = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    clean = clean[np.isfinite(clean)]
    row: dict[str, object] = {
        "anio": label,
        "n": int(clean.size),
        "mediana": _percentile(clean, 50),
        "p10": _percentile(clean, 10),
        "p90": _percentile(clean, 90),
    }
    if with_max:
        row["maximo"] = float(clean.max()) if clean.size else float("nan")
    return row


def _percentile(values: np.ndarray, percentile: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, percentile))


def _clean(values: pd.Series) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return numeric[np.isfinite(numeric)]


def _safe_division(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    left = pd.to_numeric(numerator, errors="coerce")
    right = pd.to_numeric(denominator, errors="coerce")
    return left.where(right > 0).div(right.where(right > 0))


def simultaneous_bias(run: ImpulseRun) -> pd.DataFrame:
    """C.4 — qué hacen a la vez las tres temporalidades, por año.

    Todo se lee sobre la rejilla de la temporalidad **más fina** con detector: en
    cada uno de sus cierres se mira el último cierre disponible de las mayores.
    Nunca al revés, que sería preguntarle a la vela diaria qué hará dentro de
    unas horas.

    Es la métrica que acota cuántas oportunidades alineadas puede haber: si las
    tres coinciden en dirección el 8 % del tiempo, no hay más de un 8 % del
    histórico donde buscar una entrada alineada.
    """
    columns = ["anio", "barras", "pct_tres_vigentes", "pct_alguna_en_limbo", "pct_misma_direccion"]
    alignment = align_timeframes(run)
    if alignment is None:
        return pd.DataFrame(columns=columns)
    base, all_live, any_limbo, aligned = alignment

    years = base.year.to_numpy()
    rows = []
    for year in sorted({int(value) for value in years}):
        mask = years == year
        rows.append(_alignment_row(str(year), all_live[mask], any_limbo[mask], aligned[mask]))
    rows.append(_alignment_row(TOTAL_ROW, all_live, any_limbo, aligned))
    return pd.DataFrame(rows, columns=columns)


def align_timeframes(
    run: ImpulseRun,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, np.ndarray] | None:
    """Pone las temporalidades en una rejilla común y dice qué hacían a la vez.

    Devuelve las marcas de tiempo y tres máscaras: las tres con ID vigente,
    alguna en limbo, y las tres coincidiendo en dirección. `None` si no hay al
    menos dos temporalidades con detector.
    """
    detected = list(run.analyses)
    if len(detected) < 2:
        return None

    # De más fina a más gruesa: la primera manda la rejilla.
    ordered = sorted(detected, key=lambda name: len(run.analyses[name].states), reverse=True)
    base = pd.DatetimeIndex([state.timestamp for state in run.analyses[ordered[0]].states])
    frame = pd.DataFrame(index=base)
    for timeframe in ordered:
        series = _direction_series(run.analyses[timeframe])
        # `ffill` sobre las marcas de la rejilla fina: el estado vigente en un
        # cierre de H1 es el del último cierre de H4 o del diario, ya publicado.
        combined = pd.DatetimeIndex(base.union(pd.DatetimeIndex(series.index)))
        frame[timeframe] = series.reindex(combined).ffill().reindex(base)

    # Antes de que las tres hayan publicado su primer estado no hay nada que
    # comparar. Esas barras se descartan en vez de contarlas como limbo.
    known = frame.notna().all(axis=1).to_numpy()
    frame = frame[known]
    base = base[known]

    values = frame.to_numpy(dtype=object)
    in_limbo = values == LIMBO_MARK
    all_live = ~in_limbo.any(axis=1)
    aligned = all_live & (values == values[:, :1]).all(axis=1)
    return base, all_live, in_limbo.any(axis=1), aligned


def longest_aligned_stretch(run: ImpulseRun) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Tramo más largo con las tres temporalidades apuntando al mismo lado.

    Es el que se exporta como captura: si existe un sitio donde mirar entradas
    alineadas, es éste.
    """
    alignment = align_timeframes(run)
    if alignment is None:
        return None
    base, _, _, aligned = alignment
    if not aligned.any():
        return None

    best_start = best_length = current_start = current_length = 0
    for position, value in enumerate(aligned):
        if not value:
            current_length = 0
            continue
        current_start = position if current_length == 0 else current_start
        current_length += 1
        if current_length > best_length:
            best_start, best_length = current_start, current_length
    return base[best_start], base[best_start + best_length - 1]


def _direction_series(analysis: TimeframeAnalysis) -> pd.Series:
    """Dirección del ID vigente en cada cierre, o `LIMBO_MARK` si no hay sesgo.

    El limbo va como valor y no como hueco a propósito: el `ffill` que alinea las
    temporalidades rellena huecos, y un limbo rellenado se convertiría en un
    sesgo que nunca existió.
    """
    directions = {impulse.id_num: impulse.direction.value for impulse in analysis.impulses}
    return pd.Series(
        [
            directions.get(state.impulse_id, LIMBO_MARK)
            if state.impulse_id is not None
            else LIMBO_MARK
            for state in analysis.states
        ],
        index=pd.DatetimeIndex([state.timestamp for state in analysis.states]),
        dtype=object,
    )


def _alignment_row(
    label: str, all_live: np.ndarray, any_limbo: np.ndarray, aligned: np.ndarray
) -> dict[str, object]:
    bars = int(all_live.size)

    def share(mask: np.ndarray) -> float:
        return float(mask.sum()) / bars if bars else float("nan")

    return {
        "anio": label,
        "barras": bars,
        "pct_tres_vigentes": share(all_live),
        "pct_alguna_en_limbo": share(any_limbo),
        "pct_misma_direccion": share(aligned),
    }


def _anchor_verdict(table: pd.DataFrame) -> str:
    """Declara si la elección entre A1 y A2 cambia algo relevante (§5.2)."""
    if table.empty:
        return "Sin impulsos: no hay comparación posible."

    gaps = pd.to_numeric(table["diferencia_anclas_usd"], errors="coerce").to_numpy(dtype=float)
    gaps = gaps[np.isfinite(gaps)]
    ranges = pd.to_numeric(table["rango_usd"], errors="coerce").to_numpy(dtype=float)
    ranges = ranges[np.isfinite(ranges)]
    if gaps.size == 0 or ranges.size == 0:
        return "No hay pares (A1, A2) comparables: revisa `impulsos_sin_ancla_a1`."

    p90 = float(np.percentile(gaps, 90))
    median_range = float(np.median(ranges))
    if median_range <= 0:
        return "El rango mediano no es positivo: la comparación no es interpretable."

    differing = int((gaps > 0).sum())
    share = differing / gaps.size
    worst = float(gaps.max())

    # Las dos candidaturas caen en velas consecutivas, y en una serie sin huecos
    # el open de una vela es el close de la anterior: A1 y A2 son entonces el
    # mismo número. Sólo se separan cuando hay hueco entre las dos velas.
    context = (
        f"A1 y A2 son la misma cifra en {gaps.size - differing} de {gaps.size} impulsos "
        f"({1 - share:.1%}): salen de velas consecutivas y sin hueco el open de una es el "
        f"close de la otra. Difieren en {differing} ({share:.1%}), con un máximo de "
        f"{worst:.4f} USD."
    )

    fraction = p90 / median_range
    if fraction < NEGLIGIBLE_ANCHOR_FRACTION:
        return (
            f"DESPRECIABLE: el p90 de |A1 - A2| es {p90:.4f} USD, un {fraction:.3%} del rango "
            f"mediano ({median_range:.4f} USD). La elección de ANCHOR_MODE apenas cambia el "
            f"módulo.\n{context}\nAun así, revisa los impulsos que sí difieren antes de darla "
            "por irrelevante: son justo los que nacen tras un hueco."
        )
    return (
        f"RELEVANTE: el p90 de |A1 - A2| es {p90:.4f} USD, un {fraction:.2%} del rango mediano "
        f"({median_range:.4f} USD). Decide el propietario comparando con sus capturas.\n{context}"
    )


COUNTER_EXTREME_COLUMNS = (
    "temporalidad",
    "id_publicados",
    "extremo_color_contrario",
    "pct",
    "en_la_vela_de_arranque_de_pierna",
    "en_otra_vela",
)


def counter_colour_extremes(run: ImpulseRun) -> pd.DataFrame:
    """R-36 — ID cuyo extremo lo fijó una vela del color contrario al impulso.

    El defecto **no está corregido**: `A1_last_counter_body` arregla el ancla y
    nada más. La columna que importa es la última: la vela de arranque de pierna
    es la única que la máquina adopta sin mirar su color —cualquier otra vela
    contraria constituiría el ID en el acto—, así que si los casos se concentran
    ahí, la puerta por la que entran sigue siendo la de R-36 y no otra.
    """
    rows = []
    for timeframe, analysis in run.analyses.items():
        offenders = [
            impulse for impulse in analysis.published if impulse.extreme_on_counter_bar
        ]
        at_leg_start = sum(
            1 for impulse in offenders if impulse.index_extreme == impulse.index_leg_start
        )
        published = len(analysis.published)
        rows.append(
            {
                "temporalidad": timeframe,
                "id_publicados": published,
                "extremo_color_contrario": len(offenders),
                "pct": len(offenders) / published if published else float("nan"),
                "en_la_vela_de_arranque_de_pierna": at_leg_start,
                "en_otra_vela": len(offenders) - at_leg_start,
            }
        )
    return pd.DataFrame(rows, columns=list(COUNTER_EXTREME_COLUMNS))


def impulses_per_year(tables: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Utilidad para comparar temporalidades en una sola tabla."""
    frames = [table for table in tables if not table.empty]
    if not frames:
        return pd.DataFrame(columns=["anio", "timeframe", "impulsos"])
    combined = pd.concat(frames, ignore_index=True)
    combined["anio"] = pd.DatetimeIndex(combined["ts_constitucion"]).year
    grouped = combined.groupby(["anio", "timeframe"], sort=True).size().reset_index(name="impulsos")
    return grouped
