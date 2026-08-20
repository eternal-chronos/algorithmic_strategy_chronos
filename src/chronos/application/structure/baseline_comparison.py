"""Antes y después: la línea base provisional frente a la definitiva.

La fase 1 se calculó primero con el ancla equivocada (`A2_first_leg_bar`) y con
el corte diario en `00:00` UTC, que dejaba la hora de reapertura del domingo
sola en una vela diaria propia. Esas dos cosas se corrigieron a la vez, así que
la única forma de leer los números nuevos es tener los viejos al lado.

Aquí no se recalcula ninguna cifra a mano ni se copia ninguna del informe
anterior: se **vuelve a ejecutar el módulo entero** con la configuración
provisional sobre las mismas barras M1 y se comparan las dos corridas. Cuesta
una corrida más y es la única forma de que la tabla no envejezca.

El hash de la configuración provisional está fijado en `PROVISIONAL_HASH`: si la
reconstrucción dejara de producirlo, la comparación estaría midiendo otra cosa y
el informe lo dice en vez de callarse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from chronos.application.structure.config import AggregationConfig, ImpulseConfig
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import DEGENERATE_RANGE_ATR
from chronos.application.structure.statistics import align_timeframes, whipsaw_episodes
from chronos.domain.structure.enums import AnchorMode, MachineState

#: Agregación con la que se publicaron los números provisionales: día natural en
#: UTC, sin ancla de sesión, y por tanto con la vela fantasma del domingo dentro.
PROVISIONAL_AGGREGATION = AggregationConfig(h4_offset_hours=0, d_session_start="00:00")

#: Ancla con la que se publicaron los números provisionales.
PROVISIONAL_ANCHOR = AnchorMode.A2_FIRST_LEG_BAR

#: Hash de esa configuración. Era `4c299bcf2fba` cuando H1 llevaba detector: las
#: temporalidades detectadas entran en el hash, así que quitar el ID de H1 lo
#: mueve sin mover un solo impulso del diario ni de H4.
PROVISIONAL_HASH = "e2e974c7704c"

#: Métricas de la tabla de antes y después, en el orden en que se presentan.
#: El texto es el que pidió el propietario; el formato dice cómo se imprime cada
#: una y si la diferencia se lee en unidades o en puntos porcentuales.
METRICS: tuple[tuple[str, str, str], ...] = (
    ("id_publicados", "nº de ID publicados", "count"),
    ("id_totales", "nº de ID detectados (con calentamiento)", "count"),
    ("velas", "velas del histórico", "count"),
    ("duracion_mediana", "duración mediana del ID (barras)", "bars"),
    ("pct_limbo", "% de barras en limbo", "pct"),
    ("id_enanos", "nº de ID bajo 0,25 ATR", "count"),
    ("episodios_latigazo", "nº de episodios de latigazo", "count"),
    ("extremo_color_contrario", "nº de ID con extremo sobre vela contraria", "count"),
)

#: La alineación no es de una temporalidad: es de todas a la vez.
ALIGNMENT_METRIC = "% de tiempo con las temporalidades detectadas alineadas"
ALIGNMENT_LIVE_METRIC = "% de tiempo con las temporalidades detectadas vigentes"


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """Las dos corridas, ya medidas y puestas una al lado de la otra."""

    provisional_hash: str
    definitive_hash: str
    #: `True` si la reconstrucción de la corrida provisional dio el hash archivado.
    provisional_matches_archive: bool
    provisional_description: str
    definitive_description: str
    #: Una tabla por temporalidad, con columnas métrica/provisional/definitivo/diferencia.
    per_timeframe: dict[str, pd.DataFrame]
    #: La fila de alineación de las temporalidades detectadas, que no es de ninguna.
    alignment: pd.DataFrame


def provisional_config(config: ImpulseConfig) -> ImpulseConfig:
    """La misma configuración, con el ancla y la rejilla que se usaron antes."""
    return replace(
        config,
        aggregation=PROVISIONAL_AGGREGATION,
        rules=replace(config.rules, anchor_mode=PROVISIONAL_ANCHOR),
    )


def compare(definitive: ImpulseRun, provisional: ImpulseRun) -> BaselineComparison:
    """Pone las dos corridas lado a lado, temporalidad por temporalidad."""
    shared = [
        timeframe for timeframe in definitive.analyses if timeframe in provisional.analyses
    ]
    return BaselineComparison(
        provisional_hash=provisional.config_hash,
        definitive_hash=definitive.config_hash,
        provisional_matches_archive=provisional.config_hash == PROVISIONAL_HASH,
        provisional_description=_describe(provisional),
        definitive_description=_describe(definitive),
        per_timeframe={
            timeframe: _table(
                measure(provisional.analyses[timeframe]),
                measure(definitive.analyses[timeframe]),
            )
            for timeframe in shared
        },
        alignment=_alignment_table(provisional, definitive),
    )


def measure(analysis: TimeframeAnalysis) -> dict[str, float]:
    """Las métricas de la tabla, calculadas sobre una temporalidad ya detectada."""
    table = analysis.table
    durations = _numeric(table, "n_barras_id")
    in_limbo = np.array([state.state is MachineState.LIMBO for state in analysis.states])
    ranges = _numeric(table, "rango_atr")
    return {
        "id_publicados": float(len(analysis.published)),
        "id_totales": float(len(analysis.impulses)),
        "velas": float(len(analysis.bars)),
        "duracion_mediana": float(np.median(durations)) if durations.size else float("nan"),
        "pct_limbo": 100.0 * float(in_limbo.mean()) if in_limbo.size else float("nan"),
        "id_enanos": float(np.count_nonzero(ranges < DEGENERATE_RANGE_ATR)),
        "episodios_latigazo": float(len(whipsaw_episodes(table))),
        "extremo_color_contrario": float(
            sum(1 for impulse in analysis.published if impulse.extreme_on_counter_bar)
        ),
    }


def _numeric(table: pd.DataFrame, column: str) -> np.ndarray:
    """Columna de la tabla de impulsos como float, sin nulos. Vacía si no está."""
    if column not in table.columns:
        return np.empty(0, dtype=float)
    values = pd.to_numeric(table[column], errors="coerce").to_numpy(dtype=float)
    return values[np.isfinite(values)]


COLUMNS = ("metrica", "provisional", "definitivo", "diferencia")


def _table(before: Mapping[str, float], after: Mapping[str, float]) -> pd.DataFrame:
    rows = [
        _row(label, before[key], after[key], kind) for key, label, kind in METRICS
    ]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def _row(label: str, before: float, after: float, kind: str) -> dict[str, str]:
    return {
        "metrica": label,
        "provisional": _format(before, kind),
        "definitivo": _format(after, kind),
        "diferencia": _difference(before, after, kind),
    }


def _format(value: float, kind: str) -> str:
    if not np.isfinite(value):
        return "n/d"
    if kind == "count":
        return f"{round(value):,}"
    if kind == "pct":
        return f"{value:.2f} %"
    return f"{value:,.1f}"


def _difference(before: float, after: float, kind: str) -> str:
    if not (np.isfinite(before) and np.isfinite(after)):
        return "n/d"
    delta = after - before
    if kind == "pct":
        # Puntos porcentuales: restar dos porcentajes no da un porcentaje.
        return f"{delta:+.2f} pp"
    if kind == "count":
        share = f" ({delta / before:+.1%})" if before else ""
        return f"{round(delta):+,}{share}"
    return f"{delta:+,.1f}"


def _alignment_table(provisional: ImpulseRun, definitive: ImpulseRun) -> pd.DataFrame:
    before = _alignment_shares(provisional)
    after = _alignment_shares(definitive)
    rows = [
        _row(ALIGNMENT_LIVE_METRIC, before[0], after[0], "pct"),
        _row(ALIGNMENT_METRIC, before[1], after[1], "pct"),
    ]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def _alignment_shares(run: ImpulseRun) -> tuple[float, float]:
    """Porcentaje de barras con las tres vigentes y con las tres en la misma dirección."""
    alignment = align_timeframes(run)
    if alignment is None:
        return float("nan"), float("nan")
    _base, all_live, _any_limbo, aligned = alignment
    if all_live.size == 0:
        return float("nan"), float("nan")
    return 100.0 * float(all_live.mean()), 100.0 * float(aligned.mean())


def _describe(run: ImpulseRun) -> str:
    aggregation = run.config.aggregation
    return (
        f"ANCHOR_MODE = {run.config.rules.anchor_mode.value} · "
        f"LEG_START_MODE = {run.config.rules.leg_start_mode.value} · "
        f"D_SESSION_START = {aggregation.describe_daily_start()} · "
        f"hash {run.config_hash}"
    )


__all__ = [
    "ALIGNMENT_METRIC",
    "METRICS",
    "PROVISIONAL_AGGREGATION",
    "PROVISIONAL_ANCHOR",
    "PROVISIONAL_HASH",
    "BaselineComparison",
    "compare",
    "measure",
    "provisional_config",
]
