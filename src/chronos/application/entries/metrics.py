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
    ConfirmationKind,
    DailyContext,
    EntryTimeframe,
    GuardRail,
    Outcome,
    RejectionForm,
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

    Una columna que no está en la tabla devuelve el desglose vacío en vez de
    reventar: hay corridas —las de la fase anterior, cargadas de un CSV viejo—
    que no traen las columnas que la 3.2 añadió, y el informe tiene que poder
    ponerlas al lado igualmente.
    """
    if trades.empty or column not in trades.columns:
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


# --- Fase 3.1: la columna de la 3.0 al lado ----------------------------------

#: Lo que se compara entre las dos fases. No son todas las columnas de
#: `METRIC_COLUMNS` a propósito: una tabla con veintiocho columnas no se lee en
#: un terminal, y estas cinco son las que contestan a "¿qué cambió?".
COMPARED: tuple[str, ...] = (
    "n",
    "win_rate",
    "expectativa_bruta_r",
    "expectativa_neta_r",
    "coste_medio_r",
)


#: Sufijos de las dos columnas comparadas. La fase nueva primero, la anterior
#: detrás, que es como se lee "qué cambió".
NEW = "32"
OLD = "31"


def side_by_side(
    new: pd.DataFrame,
    old: pd.DataFrame,
    columns: Sequence[str] = COMPARED,
    *,
    new_suffix: str = NEW,
    old_suffix: str = OLD,
) -> pd.DataFrame:
    """El mismo desglose de las dos fases, población a población.

    Las poblaciones de la fase anterior que la nueva ya no tiene **no se
    esconden**: salen con la columna nueva vacía, que en la 3.2 es exactamente el
    dato interesante —`UL respeto` desaparece entera—.
    """
    if new.empty and old.empty:
        return pd.DataFrame(
            columns=["poblacion", *(f"{name}_{new_suffix}" for name in columns)]
        )
    left = new.set_index("poblacion") if not new.empty else pd.DataFrame()
    right = old.set_index("poblacion") if not old.empty else pd.DataFrame()
    order = [*left.index] + [value for value in right.index if value not in set(left.index)]
    rows = []
    for label in order:
        row: dict[str, object] = {"poblacion": label}
        for name in columns:
            row[f"{name}_{new_suffix}"] = (
                left.at[label, name] if label in left.index and name in left else float("nan")
            )
            row[f"{name}_{old_suffix}"] = (
                right.at[label, name] if label in right.index and name in right else float("nan")
            )
        rows.append(row)
    return pd.DataFrame(rows)


def by_year_and_direction(trades: pd.DataFrame) -> pd.DataFrame:
    """⚠️ Largos y cortos, **por separado y por año** (§5.6 de la fase 3.1).

    Es la tabla que hace falta para no confundir un fallo del setup con el hecho
    de que el oro subió de ~1.200 a ~4.300 USD durante todo el histórico. Se
    presenta y **no se interpreta**: aquí no se propone filtrar por dirección ni
    se saca ninguna conclusión de ella.
    """
    if trades.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    rows = []
    for direction in ImpulseDirection:
        subset = trades[trades["direccion"] == direction.value]
        if subset.empty:
            continue
        for year, group in subset.groupby("anio", sort=True):
            rows.append(metrics(f"{direction.value} · {year}", group))
        rows.append(metrics(f"{direction.value} · TOTAL", subset))
    rows.append(metrics(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def by_via(trades: pd.DataFrame) -> pd.DataFrame:
    """§5.2 de la 3.1 — cada vía de confirmación por separado, con su intervalo."""
    return breakdown(
        trades, "confirmacion", tuple(value.value for value in ConfirmationKind)
    )


# --- Fase 3.2 -----------------------------------------------------------------

#: §6.2 — las tres ramas que la 3.2 deja vivas, en el orden del enunciado. Se
#: escriben aquí y no se derivan de los datos para que una rama vacía salga
#: igualmente: cero operaciones en una rama es un dato, no una fila que sobra.
BRANCHES: tuple[str, ...] = (
    f"{ZoneKind.LAST.value} {Outcome.RECHAZO.value}",
    f"{ZoneKind.LAST.value} {Outcome.ROTURA_Y_RETESTEO.value}",
    f"{ZoneKind.ORDER_BLOCK.value} {Outcome.RECHAZO.value}",
    #: Sólo la produce `ENTRY_MODE = v31_contacto`. Se deja en el orden para que
    #: la tabla de la 3.1 y la de la 3.2 se puedan poner una al lado de la otra.
    f"{ZoneKind.LAST.value} {Outcome.RESPETO.value}",
    f"{ZoneKind.ORDER_BLOCK.value} {Outcome.RESPETO.value}",
)


def by_branch(trades: pd.DataFrame) -> pd.DataFrame:
    """§6.2 — **cada rama por separado**: n, expectativa neta y bruta, WR e IC95.

    Las tres ramas de la 3.2 son poblaciones distintas y no se promedian: la de
    UL rechazado va en contra del ID, la de rotura y retesteo a favor de la
    rotura y la de OB rechazado a favor del ID. Un número agregado sobre las tres
    mezclaría tres estrategias.
    """
    return breakdown(trades, "rama", BRANCHES)


def by_rejection_form(trades: pd.DataFrame) -> pd.DataFrame:
    """§6.3 — los rechazos de H4 por forma, con las coincidencias aparte.

    Tres poblaciones y **no** son una partición: la fila `las dos formas` está
    contenida en las otras dos, porque una vela puede cumplir A y B a la vez. Se
    presenta así a propósito: lo que el §6.3 pide es cuántas hay de cada una y
    cuántas veces coincidían, no repartirlas.

    Cuando coinciden, la decisión es idéntica —misma vela, misma dirección— así
    que la etiqueta de disparo es cosmética y la fila de coincidencias es la que
    lo demuestra.
    """
    columns = list(METRIC_COLUMNS)
    if trades.empty or "forma_rechazo" not in trades.columns:
        return pd.DataFrame(columns=columns)
    rejected = trades[trades["forma_rechazo"].notna()]
    if rejected.empty:
        return pd.DataFrame(columns=columns)
    available = rejected["formas_rechazo_disponibles"].fillna("").astype(str)
    rows = [
        metrics(
            f"disponible · {form.value}",
            rejected[available.str.contains(form.value, regex=False)],
        )
        for form in RejectionForm
    ]
    rows += [
        metrics(f"disparó · {form.value}", rejected[rejected["forma_rechazo"] == form.value])
        for form in RejectionForm
    ]
    rows.append(
        metrics("las dos formas en la misma vela", rejected[available.str.contains("|", regex=False)])
    )
    rows.append(metrics(TOTAL_ROW, rejected))
    return pd.DataFrame(rows, columns=columns)


def rejection_form_counts(trades: pd.DataFrame) -> pd.DataFrame:
    """§6.3 — el recuento puro: cuántas de cada forma y cuántas coincidencias."""
    columns = ["forma", "disponible", "disparo", "pct_de_los_rechazos"]
    if trades.empty or "forma_rechazo" not in trades.columns:
        return pd.DataFrame(columns=columns)
    rejected = trades[trades["forma_rechazo"].notna()]
    if rejected.empty:
        return pd.DataFrame(columns=columns)
    available = rejected["formas_rechazo_disponibles"].fillna("").astype(str)
    total = len(rejected)
    rows = [
        {
            "forma": form.value,
            "disponible": int(available.str.contains(form.value, regex=False).sum()),
            "disparo": int((rejected["forma_rechazo"] == form.value).sum()),
            "pct_de_los_rechazos": float(
                available.str.contains(form.value, regex=False).mean()
            ),
        }
        for form in RejectionForm
    ]
    both = int(available.str.contains("|", regex=False).sum())
    rows.append(
        {
            "forma": "las dos a la vez",
            "disponible": both,
            "disparo": both,
            "pct_de_los_rechazos": both / total if total else float("nan"),
        }
    )
    rows.append(
        {
            "forma": TOTAL_ROW,
            "disponible": total,
            "disparo": total,
            "pct_de_los_rechazos": 1.0,
        }
    )
    return pd.DataFrame(rows, columns=columns)


def against_the_id(trades: pd.DataFrame) -> pd.DataFrame:
    """⚠️ §6.4 — las operaciones EN CONTRA del ID de H4, aisladas.

    Es una población **nueva** del proyecto: hasta la 3.1 ninguna operación iba
    contra el sesgo de H4. Se presenta aparte y no se interpreta.
    """
    if trades.empty or "contra_id" not in trades.columns:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    flag = trades["contra_id"].fillna(False).astype(bool)
    rows = [
        metrics("EN CONTRA del ID de H4 (UL rechazado)", trades[flag]),
        metrics("a favor del ID / de la rotura", trades[~flag]),
        metrics(TOTAL_ROW, trades),
    ]
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def weekend_gap(trades: pd.DataFrame) -> pd.DataFrame:
    """⚠️ §6.7 — decisión y ejecución separadas por el hueco de fin de semana.

    **No se corrige nada**: es una decisión del propietario. Aquí sólo se separa
    la población y se pone su expectativa al lado de la del resto.
    """
    if trades.empty or "hueco_finde" not in trades.columns:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    flag = trades["hueco_finde"].fillna(False).astype(bool)
    rows = [
        metrics("con hueco de fin de semana", trades[flag]),
        metrics("sin hueco", trades[~flag]),
        metrics(TOTAL_ROW, trades),
    ]
    return pd.DataFrame(rows, columns=list(METRIC_COLUMNS))


def weekend_gap_by_via(trades: pd.DataFrame) -> pd.DataFrame:
    """§6.7 — el mismo corte por vía de confirmación y por rama.

    En la 3.1 el efecto se vio por vía —turtle -0,399 R y OB -0,761 R frente a
    -0,079 R y -0,185 R del resto— así que el desglose se conserva igual, con la
    rama añadida porque en la 3.2 son poblaciones distintas.
    """
    columns = ["poblacion", "n_con_hueco", "neto_con_hueco_r", "n_sin", "neto_sin_r"]
    if trades.empty or "hueco_finde" not in trades.columns:
        return pd.DataFrame(columns=columns)
    flag = trades["hueco_finde"].fillna(False).astype(bool)

    def row(label: str, group: pd.DataFrame, mask: pd.Series) -> dict[str, object]:
        with_gap = metrics(label, group[mask])
        without = metrics(label, group[~mask])
        return {
            "poblacion": label,
            "n_con_hueco": with_gap["n"],
            "neto_con_hueco_r": with_gap["expectativa_neta_r"],
            "n_sin": without["n"],
            "neto_sin_r": without["expectativa_neta_r"],
        }

    rows = [
        row(f"vía {value}", trades[trades["confirmacion"] == value], flag[trades["confirmacion"] == value])
        for value in sorted(trades["confirmacion"].dropna().unique())
    ]
    rows += [
        row(f"rama {value}", trades[trades["rama"] == value], flag[trades["rama"] == value])
        for value in BRANCHES
        if "rama" in trades.columns and (trades["rama"] == value).any()
    ]
    rows.append(row(TOTAL_ROW, trades, flag))
    return pd.DataFrame(rows, columns=columns)


def cost_by_via_and_stop(trades: pd.DataFrame) -> pd.DataFrame:
    """§5.5 de la 3.1 — coste por operación en R, por vía y por configuración.

    El coste no es un residuo: en la 3.0 el stop de M15 costaba 0,191 R por
    operación, que sobre una expectativa que se mide en centésimas de R decide el
    signo. Va con su desglose y con el percentil 90, porque la media sola esconde
    las operaciones con el 1R minúsculo.
    """
    columns = ["poblacion", "n", "coste_medio_r", "coste_p90_r", "coste_maximo_r"]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        cost = pd.to_numeric(group["coste_r"], errors="coerce").to_numpy(dtype=float)
        cost = cost[np.isfinite(cost)]
        return {
            "poblacion": label,
            "n": len(group),
            "coste_medio_r": float(cost.mean()) if cost.size else float("nan"),
            "coste_p90_r": float(np.percentile(cost, 90)) if cost.size else float("nan"),
            "coste_maximo_r": float(cost.max()) if cost.size else float("nan"),
        }

    rows = [
        row(f"rama {value}", trades[trades["rama"] == value])
        for value in BRANCHES
        if "rama" in trades.columns and (trades["rama"] == value).any()
    ]
    rows += [
        row(f"vía {value}", trades[trades["confirmacion"] == value])
        for value in sorted(trades["confirmacion"].dropna().unique())
    ]
    rows += [
        row(f"entrada {entry} · stop {stop}", group)
        for (entry, stop), group in trades.groupby(["entrada_en", "stop_en"], sort=True)
    ]
    rows += [
        row(f"vía {via} · stop {stop}", group)
        for (via, stop), group in trades.groupby(["confirmacion", "stop_en"], sort=True)
    ]
    rows.append(row(TOTAL_ROW, trades))
    return pd.DataFrame(rows, columns=columns)


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
    ("5.3b · Rama (zona y desenlace)", "rama", BRANCHES),
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
    # Fase 3.2. Los dos cortes nuevos van con los ocho de siempre y no en una
    # sección aparte: el enunciado pide reportar el grupo del hueco de fin de
    # semana "en todos los desgloses", y un desglose que sólo existe en su propia
    # página no está en los demás.
    tables.append(("5.9 · Dirección frente al ID de H4", against_the_id(trades)))
    tables.append(("5.10 · Hueco de fin de semana", weekend_gap(trades)))
    return tables


def by_rejection(
    trades: pd.DataFrame, grid: Sequence[int] = REJECTION_PERCENTILES
) -> pd.DataFrame:
    """§5.8 — las tres definiciones de rechazo, **ninguna adoptada**.

    Las poblaciones **se solapan**: una misma vela puede marcar las tres, y las
    filas no suman al total. Es a propósito: lo que el §2 pide es ver cuánto se
    parecen, no repartirlas.

    **En la 3.1 las tres son informativas.** Ya no confirman nada, así que el
    desglose se hace sobre TODAS las operaciones: dice qué habría marcado la vela
    que confirmó, sin que eso haya decidido nada. En la 3.0, donde el rechazo sí
    era una vía, sólo entran las operaciones que confirmaron por rechazo y las
    demás se cuentan aparte. La población se elige mirando los datos, no un
    parámetro: si no hay ni una confirmación por rechazo, no hay nada que aislar.
    """
    if trades.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    was_a_via = bool((trades["confirmacion"] == "rechazo").any())
    population = trades[trades["confirmacion"] == "rechazo"] if was_a_via else trades
    rows: list[dict[str, object]] = []
    for column, label in _rejection_columns(grid):
        if column not in trades.columns:
            continue
        subset = population[population[column].fillna(False).astype(bool)]
        rows.append(metrics(label, subset))
    if was_a_via:
        rows.append(
            metrics(
                "confirmadas SIN rechazo (ID u OB de H1)",
                trades[trades["confirmacion"] != "rechazo"],
            )
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
    # Misma elección de población que `by_rejection`: con el rechazo ya sin ser
    # una vía, el solape se mide sobre todas las velas que confirmaron.
    rejections = (
        trades[trades["confirmacion"] == "rechazo"]
        if (trades["confirmacion"] == "rechazo").any()
        else trades
    )
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
    "BRANCHES",
    "BREAKDOWNS",
    "COMPARED",
    "METRIC_COLUMNS",
    "NEW",
    "OLD",
    "TOTAL_ROW",
    "against_the_id",
    "all_breakdowns",
    "break_even_note",
    "breakdown",
    "by_branch",
    "by_rejection",
    "by_rejection_form",
    "by_via",
    "by_year",
    "by_year_and_direction",
    "configurations",
    "cost_by_via_and_stop",
    "frequency",
    "funnel",
    "guard_rails",
    "metrics",
    "rejection_form_counts",
    "rejection_overlap",
    "risk_distribution",
    "side_by_side",
    "weekend_gap",
    "weekend_gap_by_via",
]
