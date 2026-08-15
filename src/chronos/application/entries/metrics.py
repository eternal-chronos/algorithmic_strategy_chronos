"""Métricas de la fase 3, **siempre en R, netas y brutas** (§5 y §6).

Dos reglas que este fichero cumple sin excepción:

1. **Nunca un número agregado sin su desglose.** Un promedio que mezcla largos
   con cortos, respetos con retesteos y 2018 con 2025 no dice nada. Todas las
   funciones de aquí devuelven tablas con la población delante.
2. **Bruto y neto, los dos.** La diferencia entre ellos es lo que diagnostica si
   un efecto es real o aritmética de costes. Dar sólo el neto esconde el
   diagnóstico; dar sólo el bruto esconde la cuenta.

No hay ninguna función que puntúe la estrategia. No se dice si un número es bueno
ni se recomienda un parámetro: eso lo decide el propietario.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from chronos.application.entries.cascade import FUNNEL_STEPS, CascadeRun
from chronos.application.entries.execution import ExecutionRun
from chronos.domain.entries.enums import (
    DailyContext,
    EntryTimeframe,
    GuardRail,
    Outcome,
    RejectionKind,
    StopZone,
    TradeOutcome,
)
from chronos.domain.entries.rejection import REJECTION_PERCENTILES
from chronos.domain.entries.signal import TARGET_R
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

TOTAL_ROW = "TOTAL"

#: Columnas de todo bloque de métricas. El orden es el del §6 y no cambia entre
#: desgloses: la tabla de largos se lee igual que la de 2019.
METRIC_COLUMNS: tuple[str, ...] = (
    "poblacion",
    "n",
    "abiertas",
    "aciertos",
    "win_rate",
    "expectativa_bruta_r",
    "expectativa_neta_r",
    "ic95_bajo_r",
    "ic95_alto_r",
    "payoff_medio_r",
    "coste_medio_r",
    "coste_p90_r",
    "racha_ganadora",
    "racha_perdedora",
)

#: 1,96 sigmas. El intervalo es normal-asintótico sobre la media del neto en R:
#: con una distribución de dos puntos (-1 y +3,3) y n grande es una aproximación
#: razonable, y se dice que es aproximada en vez de disfrazarla de exacta.
_Z95 = 1.959963984540054


# --- §6 Métricas -------------------------------------------------------------


def metrics(label: str, trades: pd.DataFrame) -> dict[str, object]:
    """El bloque de métricas de una población. Bruto y neto, siempre los dos."""
    resolved = trades[trades["desenlace"] != TradeOutcome.ABIERTA.value]
    net = resolved["neto_r"].to_numpy(dtype=float)
    gross = resolved["bruto_r"].to_numpy(dtype=float)
    cost = resolved["coste_r"].to_numpy(dtype=float)
    wins = resolved["desenlace"] == TradeOutcome.OBJETIVO.value
    count = int(net.size)
    low, high = _interval(net)

    return {
        "poblacion": label,
        "n": count,
        "abiertas": int(len(trades) - count),
        "aciertos": int(wins.sum()),
        "win_rate": float(wins.mean()) if count else float("nan"),
        "expectativa_bruta_r": float(gross.mean()) if count else float("nan"),
        "expectativa_neta_r": float(net.mean()) if count else float("nan"),
        "ic95_bajo_r": low,
        "ic95_alto_r": high,
        # Con objetivo fijo el payoff es una constante de la especificación
        # (3,3 / 1) salvo por los costes; se calcula igualmente porque el neto sí
        # lo mueve y porque un payoff distinto de 3,3 delataría un fallo.
        "payoff_medio_r": _payoff(net, wins.to_numpy(dtype=bool)),
        "coste_medio_r": float(cost.mean()) if count else float("nan"),
        "coste_p90_r": float(np.percentile(cost, 90)) if count else float("nan"),
        "racha_ganadora": _streak(wins.to_numpy(dtype=bool), True),
        "racha_perdedora": _streak(wins.to_numpy(dtype=bool), False),
    }


def _interval(values: np.ndarray) -> tuple[float, float]:
    """IC del 95 % de la expectativa por operación, normal-asintótico.

    Aproximado a propósito y declarado como tal: con menos de ~30 operaciones no
    significa gran cosa, y el §6 pide el intervalo justamente para que se vea
    cuándo la población es demasiado pequeña para decir nada.
    """
    if values.size < 2:
        return float("nan"), float("nan")
    error = _Z95 * float(values.std(ddof=1)) / float(np.sqrt(values.size))
    mean = float(values.mean())
    return mean - error, mean + error


def _payoff(net: np.ndarray, wins: np.ndarray) -> float:
    if not wins.any() or wins.all():
        return float("nan")
    return float(net[wins].mean() / abs(net[~wins].mean()))


def _streak(wins: np.ndarray, value: bool) -> int:
    longest = 0
    current = 0
    for won in wins:
        if bool(won) is value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def breakdown(
    trades: pd.DataFrame,
    column: str,
    order: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Las métricas de §6 desglosadas por una columna cualquiera de §5.

    La fila TOTAL va al final y **no sustituye** a las demás: está para cuadrar
    los recuentos, no para leerse sola.
    """
    if trades.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    values = list(order) if order is not None else sorted(
        str(value) for value in trades[column].dropna().unique()
    )
    rows = [
        metrics(str(value), trades[trades[column] == value])
        for value in values
        if (trades[column] == value).any()
    ]
    rows.append(metrics(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def by_year(trades: pd.DataFrame) -> pd.DataFrame:
    """§5.7 — un bloque por año. La única forma de ver si algo se sostiene."""
    if trades.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    rows = [
        metrics(str(year), group)
        for year, group in trades.groupby("anio", sort=True)
    ]
    rows.append(metrics(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


#: Los ocho desgloses obligatorios del §5, con el orden en que se imprimen y las
#: poblaciones que cada uno tiene que enseñar aunque salgan vacías: una categoría
#: con cero operaciones es un dato, y esconderla la convierte en un olvido.
BREAKDOWNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "5.1 · Con contexto diario / sin contexto diario",
        "contexto_diario",
        tuple(value.value for value in DailyContext),
    ),
    ("5.2 · Tipo de zona", "zona_h4", tuple(value.value for value in ZoneKind)),
    ("5.3 · Desenlace de la zona", "desenlace_zona", tuple(value.value for value in Outcome)),
    (
        "5.4 · Entrada en H1 / entrada en M15",
        "entrada_en",
        tuple(value.value for value in EntryTimeframe),
    ),
    ("5.5 · Stop en H1 / stop en M15", "stop_en", tuple(value.value for value in StopZone)),
    (
        "5.6 · Dirección",
        "direccion",
        tuple(value.value for value in ImpulseDirection),
    ),
)


def all_breakdowns(trades: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Los desgloses del §5, en orden. El §5.7 y el §5.8 tienen función propia."""
    tables = [(title, breakdown(trades, column, order)) for title, column, order in BREAKDOWNS]
    tables.append(("5.7 · Año", by_year(trades)))
    tables.append(("5.8 · Definición de rechazo", by_rejection(trades)))
    return tables


def by_rejection(
    trades: pd.DataFrame, grid: Sequence[int] = REJECTION_PERCENTILES
) -> pd.DataFrame:
    """§5.8 — las tres definiciones de rechazo, **ninguna adoptada**.

    Las poblaciones **se solapan**: una misma vela puede marcar las tres, y las
    filas no suman al total. Es a propósito: lo que el §2 pide es ver cuánto se
    parecen, no repartirlas.

    Sólo entran las operaciones cuya confirmación fue un rechazo. Las que
    confirmaron por ID de H1 o por OB de H1 no tienen definición que desglosar y
    se cuentan aparte, en su propia fila.
    """
    if trades.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    rejections = trades[trades["confirmacion"] == "rechazo"]
    rows: list[dict[str, object]] = []
    for column, label in _rejection_columns(grid):
        if column not in trades.columns:
            continue
        subset = rejections[rejections[column].fillna(False).astype(bool)]
        rows.append(metrics(label, subset))
    rows.append(
        metrics("confirmadas SIN rechazo (ID u OB de H1)", trades[trades["confirmacion"] != "rechazo"])
    )
    rows.append(metrics(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def _rejection_columns(grid: Sequence[int]) -> list[tuple[str, str]]:
    columns = [
        (RejectionKind.R1_MECHA_EN_ZONA.value, RejectionKind.R1_MECHA_EN_ZONA.value)
    ]
    columns += [
        (
            f"{RejectionKind.R2_MECHA_DOMINANTE.value}_p{percentile}",
            f"{RejectionKind.R2_MECHA_DOMINANTE.value} (P{percentile})",
        )
        for percentile in grid
    ]
    columns.append(
        (RejectionKind.R3_CIERRE_EN_EXTREMO.value, RejectionKind.R3_CIERRE_EN_EXTREMO.value)
    )
    return columns


def rejection_overlap(
    trades: pd.DataFrame, grid: Sequence[int] = REJECTION_PERCENTILES
) -> pd.DataFrame:
    """§2 — cuántos rechazos marca cada definición y cuánto se solapan.

    La diagonal es cuántas marcó cada una; fuera de la diagonal, cuántas marcaron
    las dos a la vez. Es la tabla con la que el propietario ve si las tres están
    diciendo lo mismo con tres nombres o si son de verdad tres criterios.
    """
    columns = [column for column, _ in _rejection_columns(grid) if column in trades.columns]
    labels = [label for column, label in _rejection_columns(grid) if column in trades.columns]
    if not columns or trades.empty:
        return pd.DataFrame()
    rejections = trades[trades["confirmacion"] == "rechazo"]
    matrix = {
        label: [
            int(
                (
                    rejections[left].fillna(False).astype(bool)
                    & rejections[right].fillna(False).astype(bool)
                ).sum()
            )
            for right in columns
        ]
        for label, left in zip(labels, columns, strict=True)
    }
    return pd.DataFrame(matrix, index=labels).T


# --- §3 y §6 Distribución del 1R ---------------------------------------------

_RISK_UNITS = (("usd", "r_usd"), ("atr", "r_atr"), ("pct_precio", "r_pct_precio"))
_RISK_PERCENTILES = (10, 25, 50, 75, 90)


def risk_distribution(trades: pd.DataFrame, by: str = "anio") -> pd.DataFrame:
    """§3 — el 1R en USD, en ATR y en % del precio. **Control de sanidad.**

    Es lo que dice si la fórmula del stop aterriza en una banda operable o
    produce stops de un dólar que la horquilla se come. Los dólares solos no
    comparan 2018 con 2025 —el oro pasó de ~1.200 a ~4.300—, así que las tres
    unidades son obligatorias y ninguna es decorativa.
    """
    columns = [
        "poblacion",
        "n",
        *(f"{name}_p{value}" for name, _ in _RISK_UNITS for value in _RISK_PERCENTILES),
        "usd_minimo",
        "bajo_una_horquilla",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        item: dict[str, object] = {"poblacion": label, "n": len(group)}
        for name, column in _RISK_UNITS:
            values = pd.to_numeric(group[column], errors="coerce").to_numpy(dtype=float)
            values = values[np.isfinite(values)]
            for percentile in _RISK_PERCENTILES:
                item[f"{name}_p{percentile}"] = (
                    float(np.percentile(values, percentile)) if values.size else float("nan")
                )
        usd = pd.to_numeric(group["r_usd"], errors="coerce").to_numpy(dtype=float)
        usd = usd[np.isfinite(usd)]
        item["usd_minimo"] = float(usd.min()) if usd.size else float("nan")
        item["bajo_una_horquilla"] = int((usd < _SPREAD_USD).sum()) if usd.size else 0
        return item

    rows = [row(str(value), group) for value, group in trades.groupby(by, sort=True)]
    rows.append(row(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=columns)


#: Una horquilla de 20 puntos con tick 0,01 son 0,20 USD. Es la referencia del
#: recuento "stops que la horquilla se come" y va marcada VERIFICAR como todo lo
#: demás: si la horquilla real es otra, el recuento es otro.
_SPREAD_USD = 0.20


# --- §6 Embudo y frecuencia --------------------------------------------------


def funnel(cascade: CascadeRun, execution: ExecutionRun) -> pd.DataFrame:
    """§6 — cuántos contactos, cuántas observaciones, dónde se cae cada una.

    El embudo es el diagnóstico que dice si la estrategia está seleccionando o
    simplemente no encuentra nada. Se lee de arriba abajo y cada paso trae su
    porcentaje sobre el anterior y sobre el primero.

    **Dos pasos suben en vez de bajar, y no es un error de recuento.** Una misma
    zona tocada produce hasta **dos** observaciones —la de respeto y la de rotura
    y retesteo, que ocurren en momentos distintos—, y una misma señal produce
    hasta **dos** operaciones, una por variante de stop. El embudo no es una
    partición: es la cascada, y la cascada se abre en abanico en esos dos sitios.
    Los porcentajes se dejan como salen, sin normalizar, porque normalizarlos
    escondería justo eso.
    """
    steps = [(step, int(cascade.funnel.get(step, 0))) for step in FUNNEL_STEPS]
    steps.append(("se_ejecutan", len(execution.trades)))
    steps.append(
        ("con_desenlace", sum(1 for trade in execution.trades if trade.outcome.is_resolved))
    )
    first = steps[0][1] or 1
    rows = []
    previous = None
    for name, value in steps:
        rows.append(
            {
                "paso": name,
                "n": value,
                "pct_sobre_el_anterior": (
                    value / previous if previous else float("nan")
                ),
                "pct_sobre_el_primero": value / first,
            }
        )
        previous = value
    return pd.DataFrame(rows)


def guard_rails(cascade: CascadeRun, execution: ExecutionRun) -> pd.DataFrame:
    """§6 y §10 — dónde muere cada señal descartada.

    Cuenta **motivos**, no zonas. Una misma zona de H4 puede aportar dos: uno por
    su observación de respeto —que muere cuando muere el ID— y otro por su rama
    de rotura y retesteo, que es un momento distinto y una señal distinta. Sumar
    los dos y compararlos con el número de zonas no cuadra, y no tiene por qué.
    """
    counts = {rail.value: 0 for rail in GuardRail}
    for item in (*cascade.discarded, *execution.discarded):
        counts[item.guard_rail.value] += 1
    total = sum(counts.values()) or 1
    return pd.DataFrame(
        [
            {"guardarrail": name, "n": value, "pct": value / total}
            for name, value in counts.items()
        ]
    )


def frequency(trades: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
    """§6 — operaciones por semana y % de semanas sin ninguna señal.

    Las semanas se cuentan sobre el calendario completo del histórico y no sobre
    las semanas en que hubo operaciones: si no, el porcentaje de semanas vacías
    saldría siempre cero por construcción.
    """
    columns = [
        "poblacion", "semanas", "operaciones", "por_semana", "semanas_vacias", "pct_vacias"
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    calendar = pd.period_range(first, last, freq="W")
    weeks = pd.PeriodIndex(pd.DatetimeIndex(trades["ts_entrada"]), freq="W")

    def row(label: str, span: pd.PeriodIndex, taken: pd.PeriodIndex) -> dict[str, object]:
        total = len(span)
        occupied = len(set(taken) & set(span))
        return {
            "poblacion": label,
            "semanas": total,
            "operaciones": len(taken),
            "por_semana": len(taken) / total if total else float("nan"),
            "semanas_vacias": total - occupied,
            "pct_vacias": (total - occupied) / total if total else float("nan"),
        }

    rows = [
        row(
            str(year),
            calendar[calendar.year == year],
            pd.PeriodIndex(pd.DatetimeIndex(group["ts_entrada"]), freq="W"),
        )
        for year, group in trades.groupby("anio", sort=True)
    ]
    rows.append(row(TOTAL_ROW, calendar, weeks))
    return pd.DataFrame(rows, columns=columns)


def configurations(execution: ExecutionRun) -> pd.DataFrame:
    """Las combinaciones (entrada, stop) que existen, medidas por separado (§5).

    La combinación *entrada en H1 con stop de M15* no aparece y no es un olvido:
    la zona de M15 se forma **después** de decidir la entrada de H1, así que su
    stop no se puede leer en el instante de decidir. Es un caso límite del §5.5 y
    se declara en vez de rellenarse.
    """
    rows = []
    for entry in EntryTimeframe:
        for stop in StopZone:
            trades = execution.by_configuration(entry, stop)
            if not trades:
                continue
            frame = pd.DataFrame(
                {
                    "neto_r": [trade.net_r for trade in trades],
                    "bruto_r": [trade.gross_r for trade in trades],
                    "coste_r": [trade.cost_r for trade in trades],
                    "desenlace": [trade.outcome.value for trade in trades],
                }
            )
            rows.append(metrics(f"entrada {entry.value} · stop {stop.value}", frame))
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def break_even_note() -> str:
    """El punto de equilibrio bruto que fija el §3, escrito una sola vez."""
    return (
        f"Objetivo fijo 1 : {TARGET_R:g} R. Punto de equilibrio BRUTO: "
        f"{1 / (1 + TARGET_R):.1%} de aciertos. En neto hace falta más, y cuánto "
        "más lo dice la columna de coste medio en R."
    )


__all__ = [
    "BREAKDOWNS",
    "METRIC_COLUMNS",
    "TOTAL_ROW",
    "all_breakdowns",
    "break_even_note",
    "breakdown",
    "by_rejection",
    "by_year",
    "configurations",
    "frequency",
    "funnel",
    "guard_rails",
    "metrics",
    "rejection_overlap",
    "risk_distribution",
]
