"""Antes y después de la fase 2.1: la rotura por línea frente a la rotura por zona.

Salvar una rotura cambia toda la historia posterior —el ID sigue vivo, su extremo
puede extenderse, el siguiente nace en otro sitio y con otra numeración—, así que
aquí no se reetiqueta ninguna tabla: se **vuelve a ejecutar el módulo entero**
con cada regla sobre las mismas velas y se comparan las dos corridas. Es la misma
disciplina que la tabla de antes y después de la fase 1, y por la misma razón:
una comparación reconstruida a mano envejece en cuanto cambia cualquier otra cosa.

Todo va desglosado por año y siempre con la columna de la línea base al lado. El
oro pasó de ~1.200 a ~4.300 USD en el histórico: ningún tamaño en dólares es
comparable entre extremos, y por eso el rango se da además en ATR y en porcentaje
del precio.

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se calculan
números; decide el propietario.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import DEGENERATE_RANGE_ATR
from chronos.application.structure.statistics import (
    WHIPSAW_WINDOWS,
    simultaneous_bias,
    whipsaw_episodes,
)
from chronos.application.structure.zones import ZonesRun, overlaps
from chronos.domain.structure.enums import BreakKind, ImpulseDirection, MachineState

TOTAL_ROW = "TOTAL"

#: Percentiles del tamaño del ID que pide el §6.4.
SIZE_PERCENTILES = (10, 25, 50, 75, 90)

#: Estimación publicada en la fase 2.0 (§7.7): porcentaje de roturas del
#: histórico de entonces que cerraron **dentro** de la zona que les tocaba y que
#: por tanto habrían sobrevivido en el instante de su rotura. Se compara con lo
#: que ha pasado de verdad al re-ejecutar (§6.11). **No se espera que coincidan**:
#: la estimación no podía tener en cuenta que salvar una rotura cambia toda la
#: historia posterior.
PHASE20_SURVIVAL_ESTIMATE: dict[str, float] = {"D": 0.441, "H4": 0.419, "H1": 0.458}

#: Las tres unidades en que se da el tamaño del ID.
SIZE_UNITS = (
    ("usd", "rango_usd"),
    ("atr", "rango_atr"),
    ("pct", "rango_pct_precio"),
)


@dataclass(frozen=True, slots=True)
class TimeframeBreakComparison:
    """Las once tablas del §6 para una temporalidad."""

    timeframe: str
    census: pd.DataFrame
    duration: pd.DataFrame
    limbo: pd.DataFrame
    size: pd.DataFrame
    dwarfs: pd.DataFrame
    whipsaws: pd.DataFrame
    breaks: pd.DataFrame
    overlap: pd.DataFrame
    counter_colour_extremes: pd.DataFrame
    survival_contrast: pd.DataFrame


@dataclass(frozen=True, slots=True)
class BreakRuleComparison:
    """Las dos corridas, ya medidas y puestas una al lado de la otra."""

    baseline_hash: str
    zoned_hash: str
    #: Hash de la corrida con el otro `OVERLAP_PRIORITY`, si se ha ejecutado.
    alternative_hash: str | None
    baseline_description: str
    zoned_description: str
    per_timeframe: dict[str, TimeframeBreakComparison]
    #: §6.9 — no es de ninguna temporalidad: es de las tres a la vez.
    alignment: pd.DataFrame
    #: §6.8 — efecto real de `OVERLAP_PRIORITY` sobre el histórico.
    overlap_priority_effect: pd.DataFrame


def compare(
    baseline: ImpulseRun,
    zoned: ImpulseRun,
    *,
    baseline_zones: ZonesRun,
    zoned_zones: ZonesRun,
    alternative: ImpulseRun | None = None,
) -> BreakRuleComparison:
    """Compara las dos reglas sobre las mismas velas, temporalidad por temporalidad."""
    shared = [name for name in zoned.analyses if name in baseline.analyses]
    return BreakRuleComparison(
        baseline_hash=baseline.config_hash,
        zoned_hash=zoned.config_hash,
        alternative_hash=None if alternative is None else alternative.config_hash,
        baseline_description=_describe(baseline),
        zoned_description=_describe(zoned),
        per_timeframe={
            timeframe: _compare_timeframe(
                baseline.analyses[timeframe],
                zoned.analyses[timeframe],
                baseline_zones=baseline_zones,
                zoned_zones=zoned_zones,
            )
            for timeframe in shared
        },
        alignment=_alignment(baseline, zoned),
        overlap_priority_effect=_priority_effect(zoned, alternative),
    )


def _compare_timeframe(
    before: TimeframeAnalysis,
    after: TimeframeAnalysis,
    *,
    baseline_zones: ZonesRun,
    zoned_zones: ZonesRun,
) -> TimeframeBreakComparison:
    return TimeframeBreakComparison(
        timeframe=after.timeframe,
        census=census(before, after),
        duration=duration(before, after),
        limbo=limbo(before, after),
        size=size(before, after),
        dwarfs=dwarfs(before, after),
        whipsaws=whipsaws(before, after),
        breaks=breaks(after),
        overlap=overlap(after.timeframe, baseline_zones, zoned_zones),
        counter_colour_extremes=counter_colour_extremes(before, after),
        survival_contrast=survival_contrast(before, after),
    )


# --- 6.1 Censo ---------------------------------------------------------------


def census(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.1 — cuántos ID había y cuántos hay, por año."""
    columns = [
        "anio", "antes", "despues", "diferencia", "pct",
        "alcistas_antes", "alcistas_despues", "bajistas_antes", "bajistas_despues",
    ]

    def row(label: str, left: pd.DataFrame, right: pd.DataFrame) -> dict[str, object]:
        return {
            "anio": label,
            "antes": len(left),
            "despues": len(right),
            "diferencia": len(right) - len(left),
            "pct": _share(len(right) - len(left), len(left)),
            "alcistas_antes": _count_direction(left, ImpulseDirection.ALCISTA),
            "alcistas_despues": _count_direction(right, ImpulseDirection.ALCISTA),
            "bajistas_antes": _count_direction(left, ImpulseDirection.BAJISTA),
            "bajistas_despues": _count_direction(right, ImpulseDirection.BAJISTA),
        }

    return _paired_by_year(before, after, row, columns)


def _count_direction(table: pd.DataFrame, direction: ImpulseDirection) -> int:
    if table.empty:
        return 0
    return int((table["direccion"] == direction.value).sum())


# --- 6.2 Duración ------------------------------------------------------------


def duration(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.2 — barras que vive un ID. Se espera que suba."""
    return _distribution_pair(before, after, "n_barras_id")


# --- 6.3 Limbo ---------------------------------------------------------------


def limbo(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.3 — cuánto tiempo pasa el sistema sin sesgo, y en cuántos tramos.

    El episodio se cuenta en el año de su **primera** barra: un limbo que cruza
    el fin de año es uno, no dos.
    """
    columns = [
        "anio", "barras",
        "barras_limbo_antes", "barras_limbo_despues",
        "pct_antes", "pct_despues", "dif_pp",
        "episodios_antes", "episodios_despues",
        "mediana_antes", "mediana_despues", "maximo_antes", "maximo_despues",
    ]
    left = _limbo_profile(before)
    right = _limbo_profile(after)
    if left is None or right is None:
        return pd.DataFrame(columns=columns)

    years = sorted(set(left.years) | set(right.years))
    rows = [_limbo_row(str(year), left, right, year) for year in years]
    rows.append(_limbo_row(TOTAL_ROW, left, right, None))
    return pd.DataFrame(rows, columns=columns)


@dataclass(frozen=True, slots=True)
class _LimboProfile:
    years: np.ndarray
    in_limbo: np.ndarray
    #: Año e índice de arranque de cada episodio, con su longitud en barras.
    episode_year: np.ndarray
    episode_length: np.ndarray


def _limbo_profile(analysis: TimeframeAnalysis) -> _LimboProfile | None:
    states = analysis.states
    if not states:
        return None
    years = np.array([state.timestamp.year for state in states])
    in_limbo = np.array([state.state is MachineState.LIMBO for state in states])

    lengths: list[int] = []
    episode_years: list[int] = []
    run = 0
    for position, value in enumerate(in_limbo):
        if value:
            if run == 0:
                episode_years.append(int(years[position]))
            run += 1
            continue
        if run:
            lengths.append(run)
            run = 0
    if run:
        lengths.append(run)
    return _LimboProfile(
        years=years,
        in_limbo=in_limbo,
        episode_year=np.array(episode_years, dtype=int),
        episode_length=np.array(lengths, dtype=int),
    )


def _limbo_row(
    label: str, left: _LimboProfile, right: _LimboProfile, year: int | None
) -> dict[str, object]:
    left_mask = np.ones_like(left.in_limbo) if year is None else left.years == year
    right_mask = np.ones_like(right.in_limbo) if year is None else right.years == year
    left_bars = left.in_limbo[left_mask]
    right_bars = right.in_limbo[right_mask]
    left_episodes = (
        left.episode_length
        if year is None
        else left.episode_length[left.episode_year == year]
    )
    right_episodes = (
        right.episode_length
        if year is None
        else right.episode_length[right.episode_year == year]
    )
    before_share = _share(int(left_bars.sum()), int(left_bars.size))
    after_share = _share(int(right_bars.sum()), int(right_bars.size))
    return {
        "anio": label,
        # Las velas son las mismas en las dos reglas: se imprimen una vez.
        "barras": int(right_bars.size),
        "barras_limbo_antes": int(left_bars.sum()),
        "barras_limbo_despues": int(right_bars.sum()),
        "pct_antes": before_share,
        "pct_despues": after_share,
        "dif_pp": 100.0 * (after_share - before_share),
        "episodios_antes": int(left_episodes.size),
        "episodios_despues": int(right_episodes.size),
        "mediana_antes": _percentile(left_episodes.astype(float), 50),
        "mediana_despues": _percentile(right_episodes.astype(float), 50),
        "maximo_antes": float(left_episodes.max()) if left_episodes.size else float("nan"),
        "maximo_despues": float(right_episodes.max()) if right_episodes.size else float("nan"),
    }


# --- 6.4 Tamaño --------------------------------------------------------------


def size(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.4 — rango del ID en USD, ATR y % del precio. Percentiles antes y después."""
    columns = ["anio", "unidad", "n_antes", "n_despues"]
    for value in SIZE_PERCENTILES:
        columns += [f"p{value}_antes", f"p{value}_despues"]

    left = _with_year(before.table)
    right = _with_year(after.table)
    if left.empty and right.empty:
        return pd.DataFrame(columns=columns)

    years = sorted(set(left.get("anio", pd.Series(dtype=int))) | set(right.get("anio", pd.Series(dtype=int))))
    rows = []
    for label, mask_left, mask_right in _year_slices(left, right, years):
        for unit, column in SIZE_UNITS:
            before_values = _clean(mask_left[column]) if not mask_left.empty else _empty()
            after_values = _clean(mask_right[column]) if not mask_right.empty else _empty()
            row: dict[str, object] = {
                "anio": label,
                "unidad": unit,
                "n_antes": int(before_values.size),
                "n_despues": int(after_values.size),
            }
            for value in SIZE_PERCENTILES:
                row[f"p{value}_antes"] = _percentile(before_values, value)
                row[f"p{value}_despues"] = _percentile(after_values, value)
            rows.append(row)
    return pd.DataFrame(rows, columns=columns)


# --- 6.5 Los enanos ----------------------------------------------------------


def dwarfs(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.5 — ID de rango menor que 0,25 ATR. **La cifra que manda de la fase.**

    Es el problema que se persigue desde la fase 1: impulsos tan pequeños que el
    ojo del propietario no los marcaría. Se cuentan en absoluto y como fracción
    de los ID del año, porque la regla nueva reduce también el denominador.
    """
    columns = [
        "anio", "id_antes", "id_despues",
        "enanos_antes", "enanos_despues", "diferencia", "pct_variacion",
        "share_antes", "share_despues",
    ]

    def row(label: str, left: pd.DataFrame, right: pd.DataFrame) -> dict[str, object]:
        before_tiny = _count_dwarfs(left)
        after_tiny = _count_dwarfs(right)
        return {
            "anio": label,
            "id_antes": len(left),
            "id_despues": len(right),
            "enanos_antes": before_tiny,
            "enanos_despues": after_tiny,
            "diferencia": after_tiny - before_tiny,
            "pct_variacion": _share(after_tiny - before_tiny, before_tiny),
            "share_antes": _share(before_tiny, len(left)),
            "share_despues": _share(after_tiny, len(right)),
        }

    return _paired_by_year(before, after, row, columns)


def _count_dwarfs(table: pd.DataFrame) -> int:
    if table.empty or "rango_atr" not in table.columns:
        return 0
    values = _clean(table["rango_atr"])
    return int(np.count_nonzero(values < DEGENERATE_RANGE_ATR))


# --- 6.6 Latigazo ------------------------------------------------------------


def whipsaws(before: TimeframeAnalysis, after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.6 — episodios en que el sesgo se invierte y se deshace a las pocas barras."""
    columns = ["anio", "episodios_antes", "episodios_despues"]
    for window in WHIPSAW_WINDOWS:
        columns += [f"hasta_{window}_antes", f"hasta_{window}_despues"]

    left = whipsaw_episodes(before.table)
    right = whipsaw_episodes(after.table)
    if left.empty and right.empty:
        return pd.DataFrame(columns=columns)

    years = sorted(
        set(left["anio"] if not left.empty else []) | set(right["anio"] if not right.empty else [])
    )
    rows = []
    for label, group_left, group_right in _year_slices(left, right, years):
        row: dict[str, object] = {
            "anio": label,
            "episodios_antes": len(group_left),
            "episodios_despues": len(group_right),
        }
        for window in WHIPSAW_WINDOWS:
            row[f"hasta_{window}_antes"] = _within(group_left, window)
            row[f"hasta_{window}_despues"] = _within(group_right, window)
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def _within(episodes: pd.DataFrame, window: int) -> int:
    if episodes.empty:
        return 0
    return int((_clean(episodes["barras"]) <= window).sum())


# --- 6.7 Roturas -------------------------------------------------------------


def breaks(after: TimeframeAnalysis) -> pd.DataFrame:
    """§6.7 — de qué murió cada ID con la regla nueva: por zona o por línea.

    "Por línea" con la regla nueva significa una sola cosa: que en ese lado no
    había zona que sustituyera a la línea. Sólo le puede pasar al lado en contra
    del primer ID del histórico, que no tiene PUL, porque el UL existe siempre.
    """
    columns = [
        "tipo_rotura", "total", "por_zona", "por_linea", "pct_por_linea", "evitadas",
    ]
    events = after.events
    if not events:
        return pd.DataFrame(columns=columns)

    avoided = after.avoided
    rows = []
    for kind in (BreakKind.A_FAVOR, BreakKind.EN_CONTRA):
        subset = [event for event in events if event.kind is kind]
        by_zone = sum(1 for event in subset if event.by_zone)
        by_line = len(subset) - by_zone
        rows.append(
            {
                "tipo_rotura": kind.value,
                "total": len(subset),
                "por_zona": by_zone,
                "por_linea": by_line,
                "pct_por_linea": _share(by_line, len(subset)),
                "evitadas": sum(1 for item in avoided if item.kind is kind),
            }
        )
    total_zone = sum(1 for event in events if event.by_zone)
    rows.append(
        {
            "tipo_rotura": TOTAL_ROW,
            "total": len(events),
            "por_zona": total_zone,
            "por_linea": len(events) - total_zone,
            "pct_por_linea": _share(len(events) - total_zone, len(events)),
            "evitadas": len(avoided),
        }
    )
    return pd.DataFrame(rows, columns=columns)


# --- 6.8 Zonas solapadas -----------------------------------------------------


def overlap(
    timeframe: str,
    baseline_zones: ZonesRun,
    zoned_zones: ZonesRun,
) -> pd.DataFrame:
    """§6.8 — ID con las dos zonas solapadas en precio, antes y después.

    La hipótesis a comprobar es que buena parte de esos impulsos enanos
    **desaparecen solos**, porque nacían de roturas prematuras que la regla nueva
    ya no permite.

    `conflictos` cuenta otra cosa y va al lado a propósito: las velas en que se
    cumplieron **las dos** condiciones de rotura a la vez, que es donde
    `OVERLAP_PRIORITY` decide algo. Solaparse y entrar en conflicto son
    excluyentes: para cumplir las dos condiciones hacen falta los dos bordes
    exteriores invertidos, y unas zonas que se pisan los tienen en el otro orden.
    """
    columns = [
        "anio", "con_pul_antes", "con_pul_despues",
        "solapadas_antes", "solapadas_despues",
        "pct_antes", "pct_despues", "diferencia",
    ]
    left = _overlap_table(baseline_zones, timeframe)
    right = _overlap_table(zoned_zones, timeframe)
    if left.empty and right.empty:
        return pd.DataFrame(columns=columns)

    merged = left.merge(right, on="anio", how="outer", suffixes=("_antes", "_despues"))
    merged = merged.fillna(0)
    merged["diferencia"] = merged["se_solapan_despues"] - merged["se_solapan_antes"]
    frame = pd.DataFrame(
        {
            "anio": merged["anio"],
            "con_pul_antes": merged["pares_antes"].astype(int),
            "con_pul_despues": merged["pares_despues"].astype(int),
            "solapadas_antes": merged["se_solapan_antes"].astype(int),
            "solapadas_despues": merged["se_solapan_despues"].astype(int),
            "pct_antes": merged["pct_solape_antes"],
            "pct_despues": merged["pct_solape_despues"],
            "diferencia": merged["diferencia"].astype(int),
        }
    )
    # El TOTAL de la fase 2.0 va como etiqueta, así que se deja al final.
    ordered = frame[frame["anio"] != TOTAL_ROW].sort_values("anio")
    return pd.concat(
        [ordered, frame[frame["anio"] == TOTAL_ROW]], ignore_index=True
    )[columns]


def _overlap_table(zones: ZonesRun, timeframe: str) -> pd.DataFrame:
    item = zones.per_timeframe.get(timeframe)
    if item is None:
        return pd.DataFrame(columns=["anio", "pares", "se_solapan", "pct_solape"])
    return overlaps(item)


def _priority_effect(
    zoned: ImpulseRun, alternative: ImpulseRun | None
) -> pd.DataFrame:
    """§6.8 — qué cambia de verdad al invertir `OVERLAP_PRIORITY`.

    No basta con contar los conflictos: hay que correr el módulo entero con el
    otro orden y comparar, porque un solo conflicto cambia la dirección de la
    pierna y con ella toda la historia posterior.
    """
    columns = [
        "temporalidad", "conflictos", "id_orden_1", "id_orden_2", "diferencia", "identicos",
    ]
    rows = []
    for timeframe, analysis in zoned.analyses.items():
        conflicts = analysis.diagnostics.get("conflictos_de_solape", 0)
        other = None if alternative is None else alternative.analyses.get(timeframe)
        rows.append(
            {
                "temporalidad": timeframe,
                "conflictos": conflicts,
                "id_orden_1": len(analysis.impulses),
                "id_orden_2": None if other is None else len(other.impulses),
                "diferencia": (
                    None if other is None else len(other.impulses) - len(analysis.impulses)
                ),
                "identicos": None if other is None else _same_history(analysis, other),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _same_history(left: TimeframeAnalysis, right: TimeframeAnalysis) -> bool:
    """`True` si las dos corridas producen exactamente los mismos impulsos."""
    if len(left.impulses) != len(right.impulses):
        return False
    return all(
        one.direction is other.direction
        and one.index_constitution == other.index_constitution
        and one.index_end == other.index_end
        and one.anchor == other.anchor
        and one.extreme == other.extreme
        for one, other in zip(left.impulses, right.impulses, strict=True)
    )


# --- 6.9 Sesgos simultáneos --------------------------------------------------


def _alignment(baseline: ImpulseRun, zoned: ImpulseRun) -> pd.DataFrame:
    """§6.9 — qué hacen a la vez las temporalidades detectadas, antes y después."""
    columns = [
        "anio", "barras_antes", "barras_despues",
        "vigentes_antes", "vigentes_despues",
        "limbo_antes", "limbo_despues",
        "alineadas_antes", "alineadas_despues",
    ]
    left = _alignment_by_year(baseline)
    right = _alignment_by_year(zoned)
    if left.empty and right.empty:
        return pd.DataFrame(columns=columns)

    merged = left.merge(right, on="anio", how="outer", suffixes=("_antes", "_despues"))
    frame = pd.DataFrame(
        {
            "anio": merged["anio"],
            "barras_antes": merged["barras_antes"],
            "barras_despues": merged["barras_despues"],
            "vigentes_antes": merged["pct_todas_vigentes_antes"],
            "vigentes_despues": merged["pct_todas_vigentes_despues"],
            "limbo_antes": merged["pct_alguna_en_limbo_antes"],
            "limbo_despues": merged["pct_alguna_en_limbo_despues"],
            "alineadas_antes": merged["pct_misma_direccion_antes"],
            "alineadas_despues": merged["pct_misma_direccion_despues"],
        }
    )
    ordered = frame[frame["anio"] != TOTAL_ROW].sort_values("anio")
    return pd.concat(
        [ordered, frame[frame["anio"] == TOTAL_ROW]], ignore_index=True
    )[columns]


def _alignment_by_year(run: ImpulseRun) -> pd.DataFrame:
    return simultaneous_bias(run)


# --- 6.10 R-36 ---------------------------------------------------------------


def counter_colour_extremes(
    before: TimeframeAnalysis, after: TimeframeAnalysis
) -> pd.DataFrame:
    """§6.10 — ID cuyo extremo lo fijó una vela del color contrario al impulso.

    El defecto **no está corregido** y esta fase no lo toca. Lo que sí cambia es
    el reparto: con la regla nueva el extremo puede moverse estando el ID ya
    vigente, así que aparece una tercera puerta —la vela que lo estiró— que en la
    fase 1 no existía.
    """
    columns = [
        "poblacion", "id_publicados", "extremo_color_contrario", "pct",
        "en_arranque_de_pierna", "en_vela_que_extendio", "en_otra_vela",
    ]
    rows = [
        _counter_colour_row("antes (por línea)", before),
        _counter_colour_row("después (por zona)", after),
    ]
    return pd.DataFrame(rows, columns=columns)


def _counter_colour_row(label: str, analysis: TimeframeAnalysis) -> dict[str, object]:
    published = analysis.published
    offenders = [impulse for impulse in published if impulse.extreme_on_counter_bar]
    at_leg_start = sum(
        1 for impulse in offenders if impulse.index_extreme == impulse.index_leg_start
    )
    extended = sum(
        1
        for impulse in offenders
        if impulse.extreme_extensions > 0 and impulse.index_extreme > impulse.index_constitution
    )
    return {
        "poblacion": label,
        "id_publicados": len(published),
        "extremo_color_contrario": len(offenders),
        "pct": _share(len(offenders), len(published)),
        "en_arranque_de_pierna": at_leg_start,
        "en_vela_que_extendio": extended,
        "en_otra_vela": len(offenders) - at_leg_start - extended,
    }


# --- 6.11 Contraste con la estimación de la fase 2.0 -------------------------


def survival_contrast(
    before: TimeframeAnalysis, after: TimeframeAnalysis
) -> pd.DataFrame:
    """§6.11 — la estimación de la fase 2.0 frente a lo que ha ocurrido.

    La fase 2.0 miró, rotura a rotura del histórico **de entonces**, si el cierre
    que mató al ID se había quedado dentro de la zona que le tocaba. Eso mide
    cuántas roturas habrían sobrevivido *en su instante*, con todo lo demás igual.
    Al re-ejecutar, cada rotura salvada cambia lo que viene después: el ID sigue
    vivo, extiende su extremo y se enfrenta a velas distintas. Las dos cifras
    miden cosas distintas y se ponen al lado para que se vea cuánto.
    """
    columns = ["concepto", "valor", "unidad", "nota"]
    estimated = PHASE20_SURVIVAL_ESTIMATE.get(after.timeframe, float("nan"))
    old_breaks = len(before.events)
    new_breaks = len(after.events)
    avoided = len(after.avoided)
    rows = [
        {
            "concepto": "roturas con la regla antigua",
            "valor": float(old_breaks),
            "unidad": "roturas",
            "nota": "línea base: una por cada ID que murió",
        },
        {
            "concepto": "estimación fase 2.0: % que sobreviviría",
            "valor": estimated,
            "unidad": "%",
            "nota": "cierres que se quedaron dentro de su zona, sin re-ejecutar",
        },
        {
            "concepto": "roturas que la estimación salvaba",
            "valor": estimated * old_breaks,
            "unidad": "roturas",
            "nota": "el mismo % aplicado a las roturas de la línea base",
        },
        {
            "concepto": "roturas evitadas de verdad",
            "valor": float(avoided),
            "unidad": "roturas",
            "nota": "velas salvadas al re-ejecutar; una misma vela salva un ID distinto",
        },
        {
            "concepto": "roturas con la regla nueva",
            "valor": float(new_breaks),
            "unidad": "roturas",
            "nota": "una por cada ID que muere; hay menos ID, así que hay menos",
        },
        {
            "concepto": "reducción real de roturas",
            "valor": _share(old_breaks - new_breaks, old_breaks),
            "unidad": "%",
            "nota": "lo que de verdad ha bajado el recuento de roturas",
        },
    ]
    return pd.DataFrame(rows, columns=columns)


# --- Tablas que se persisten -------------------------------------------------

#: Un registro por vela salvada (§7). Es la capa que el propietario audita
#: primero, así que sale a CSV con todo lo que hace falta para localizarla en el
#: gráfico sin cruzar ninguna otra tabla.
AVOIDED_COLUMNS = (
    "timeframe",
    "anio",
    "id_num",
    "direccion_id",
    "tipo_rotura_evitada",
    "ts",
    "indice",
    "cierre",
    "linea",
    "zona",
    "borde_interior",
    "borde_exterior",
    "distancia_a_la_linea",
    "margen_hasta_el_borde",
    "extendio_el_extremo",
)


def avoided_table(run: ImpulseRun) -> pd.DataFrame:
    """Todas las roturas evitadas de la corrida, ya ordenadas."""
    rows = [
        {
            "timeframe": item.timeframe,
            "anio": item.timestamp.year,
            "id_num": item.id_num,
            "direccion_id": item.direction.value,
            "tipo_rotura_evitada": item.kind.value,
            "ts": item.timestamp,
            "indice": item.index,
            "cierre": item.close,
            "linea": item.line,
            "zona": item.zone.value,
            "borde_interior": item.zone_inner,
            "borde_exterior": item.zone_outer,
            # Cuánto se pasó de la línea y cuánto le faltó para atravesar la zona.
            # Las dos en valor absoluto: el signo lo da la dirección del ID.
            "distancia_a_la_linea": abs(item.close - item.line),
            "margen_hasta_el_borde": abs(item.zone_outer - item.close),
            "extendio_el_extremo": item.extended_extreme,
        }
        for analysis in run.analyses.values()
        for item in analysis.avoided
    ]
    if not rows:
        return pd.DataFrame(columns=list(AVOIDED_COLUMNS))
    return pd.DataFrame(rows)[list(AVOIDED_COLUMNS)].sort_values(
        ["timeframe", "ts", "id_num"]
    ).reset_index(drop=True)


#: Un registro por rotura real, con el nivel que la produjo (§6.7).
BREAK_COLUMNS = (
    "timeframe",
    "anio",
    "id_num",
    "tipo_rotura",
    "origen_nivel",
    "ts",
    "indice",
    "cierre",
    "nivel",
    "linea",
)


def break_table(run: ImpulseRun) -> pd.DataFrame:
    """Todas las roturas de la corrida, con el nivel que las produjo."""
    rows = [
        {
            "timeframe": event.timeframe,
            "anio": event.timestamp.year,
            "id_num": event.broken_id_num,
            "tipo_rotura": event.kind.value,
            "origen_nivel": event.level_source.value,
            "ts": event.timestamp,
            "indice": event.index,
            "cierre": event.close,
            "nivel": event.level,
            "linea": event.line if event.line is not None else event.level,
        }
        for analysis in run.analyses.values()
        for event in analysis.events
    ]
    if not rows:
        return pd.DataFrame(columns=list(BREAK_COLUMNS))
    return pd.DataFrame(rows)[list(BREAK_COLUMNS)].sort_values(
        ["timeframe", "ts"]
    ).reset_index(drop=True)


# --- Apoyo -------------------------------------------------------------------


def _paired_by_year(
    before: TimeframeAnalysis,
    after: TimeframeAnalysis,
    row: Callable[[str, pd.DataFrame, pd.DataFrame], dict[str, object]],
    columns: Sequence[str],
) -> pd.DataFrame:
    left = _with_year(before.table)
    right = _with_year(after.table)
    if left.empty and right.empty:
        return pd.DataFrame(columns=list(columns))
    years = sorted(
        set(left["anio"] if not left.empty else []) | set(right["anio"] if not right.empty else [])
    )
    rows = [
        row(label, group_left, group_right)
        for label, group_left, group_right in _year_slices(left, right, years)
    ]
    return pd.DataFrame(rows, columns=list(columns))


def _distribution_pair(
    before: TimeframeAnalysis, after: TimeframeAnalysis, column: str
) -> pd.DataFrame:
    columns = [
        "anio", "n_antes", "n_despues",
        "mediana_antes", "mediana_despues",
        "p10_antes", "p10_despues", "p90_antes", "p90_despues",
        "maximo_antes", "maximo_despues",
    ]

    def row(label: str, left: pd.DataFrame, right: pd.DataFrame) -> dict[str, object]:
        before_values = _clean(left[column]) if not left.empty else _empty()
        after_values = _clean(right[column]) if not right.empty else _empty()
        return {
            "anio": label,
            "n_antes": int(before_values.size),
            "n_despues": int(after_values.size),
            "mediana_antes": _percentile(before_values, 50),
            "mediana_despues": _percentile(after_values, 50),
            "p10_antes": _percentile(before_values, 10),
            "p10_despues": _percentile(after_values, 10),
            "p90_antes": _percentile(before_values, 90),
            "p90_despues": _percentile(after_values, 90),
            "maximo_antes": float(before_values.max()) if before_values.size else float("nan"),
            "maximo_despues": float(after_values.max()) if after_values.size else float("nan"),
        }

    return _paired_by_year(before, after, row, columns)


def _year_slices(
    left: pd.DataFrame, right: pd.DataFrame, years: Sequence[int]
) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    """Cada año con sus dos grupos, y el TOTAL al final."""
    slices = [
        (
            str(year),
            left[left["anio"] == year] if not left.empty else left,
            right[right["anio"] == year] if not right.empty else right,
        )
        for year in years
    ]
    slices.append((TOTAL_ROW, left, right))
    return slices


def _with_year(table: pd.DataFrame) -> pd.DataFrame:
    if table.empty:
        return table
    if "anio" in table.columns:
        return table
    return table.assign(anio=pd.DatetimeIndex(table["ts_constitucion"]).year)


def _describe(run: ImpulseRun) -> str:
    rules = run.config.rules
    regla = "por ZONA" if rules.break_by_zone else "por LÍNEA"
    orden = f" · OVERLAP_PRIORITY = {rules.overlap_priority.value}" if rules.break_by_zone else ""
    return (
        f"BREAK_BY_ZONE = {str(rules.break_by_zone).lower()} (rotura {regla}){orden} · "
        f"ANCHOR_MODE = {rules.anchor_mode.value} · "
        f"LEG_START_MODE = {rules.leg_start_mode.value} · hash {run.config_hash}"
    )


def _share(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def _clean(values: pd.Series) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return numeric[np.isfinite(numeric)]


def _empty() -> np.ndarray:
    return np.empty(0, dtype=float)


def _percentile(values: np.ndarray, percentile: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, percentile))


__all__ = [
    "AVOIDED_COLUMNS",
    "BREAK_COLUMNS",
    "PHASE20_SURVIVAL_ESTIMATE",
    "SIZE_PERCENTILES",
    "BreakRuleComparison",
    "TimeframeBreakComparison",
    "avoided_table",
    "break_table",
    "breaks",
    "census",
    "compare",
    "counter_colour_extremes",
    "duration",
    "dwarfs",
    "limbo",
    "overlap",
    "size",
    "survival_contrast",
    "whipsaws",
]
