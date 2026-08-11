"""Cierre de R-02: ¿cambia algo elegir A1 en vez de A2? (sección B).

Hasta ahora el informe comparaba las dos candidaturas de ancla *dentro de una
misma corrida*: calculaba A1 y A2 en cada constitución y restaba. Eso mide la
distancia entre los dos números, pero no responde a la pregunta que importa,
porque el ancla es uno de los dos niveles que rompen el ID: moverla puede
adelantar o retrasar una `ROTURA_EN_CONTRA` y, a partir de ahí, cambiar toda la
secuencia.

Aquí se ejecuta el módulo **entero, dos veces**, una con cada modo, y se comparan
los resultados impulso a impulso. La cifra decisiva es cuántos impulsos cambian
el momento de su rotura: si es cero, la elección es cosmética y se declara como
tal; si es material, decide el propietario.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.structure.config import ImpulseConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.domain.structure.enums import AnchorMode


@dataclass(frozen=True, slots=True)
class TimeframeAnchorComparison:
    """Las dos corridas de una temporalidad, cara a cara."""

    timeframe: str
    impulses_a1: int
    impulses_a2: int
    #: Impulsos que nacen en el mismo instante en las dos corridas.
    aligned: int
    #: De los alineados, los que además tienen el ancla en el mismo precio exacto.
    identical_anchor: int
    #: **La cifra de la sección B**: alineados cuyo momento de rotura cambia.
    changed_break: int
    #: Impulsos que ni siquiera nacen en el mismo sitio en las dos corridas. Una
    #: vez que una rotura se mueve, todo lo que viene detrás se desplaza con ella.
    unaligned: int
    #: Primer instante en el que las dos corridas dejan de contar lo mismo.
    first_divergence: datetime | None
    gap_by_year: pd.DataFrame

    @property
    def is_cosmetic(self) -> bool:
        """`True` si la elección de ancla no mueve ninguna rotura ni ningún impulso."""
        return self.changed_break == 0 and self.unaligned == 0

    @property
    def verdict(self) -> str:
        if self.is_cosmetic:
            return (
                f"COSMÉTICA en {self.timeframe}: las dos corridas producen los mismos "
                f"{self.aligned:,} impulsos, en los mismos instantes y con las mismas roturas. "
                f"El ancla cae en el mismo precio exacto en {self.identical_anchor:,} de ellos."
            )
        return (
            f"MATERIAL en {self.timeframe}: {self.changed_break:,} impulsos alineados cambian el "
            f"momento de su rotura y otros {self.unaligned:,} ni siquiera nacen en el mismo sitio. "
            f"El recuento pasa de {self.impulses_a1:,} (A1) a {self.impulses_a2:,} (A2). "
            f"Primera divergencia: {self.first_divergence}. Decide el propietario."
        )


@dataclass(frozen=True, slots=True)
class AnchorComparison:
    per_timeframe: dict[str, TimeframeAnchorComparison]

    @property
    def all_cosmetic(self) -> bool:
        return all(item.is_cosmetic for item in self.per_timeframe.values())


def compare_anchor_modes(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> AnchorComparison:
    """Corre el módulo con A1 y con A2 sobre las mismas velas y compara."""
    runs = {
        mode: DetectDominantImpulses(
            replace(config, rules=replace(config.rules, anchor_mode=mode))
        ).execute(series)
        for mode in (AnchorMode.A1_LAST_COUNTER_BODY, AnchorMode.A2_FIRST_LEG_BAR)
    }
    return AnchorComparison(
        per_timeframe={
            timeframe: _compare_timeframe(timeframe, runs)
            for timeframe in runs[AnchorMode.A2_FIRST_LEG_BAR].analyses
        }
    )


def _compare_timeframe(
    timeframe: str, runs: dict[AnchorMode, ImpulseRun]
) -> TimeframeAnchorComparison:
    """Compara **todos** los impulsos, no sólo los publicados.

    Filtrar por `publishable` mezclaría dos cosas distintas: un impulso que no se
    publica porque cae en el calentamiento y uno que no existe porque la otra
    corrida rompió antes. Lo que se está midiendo es lo segundo.
    """
    left = runs[AnchorMode.A1_LAST_COUNTER_BODY].analyses[timeframe].impulses
    right = runs[AnchorMode.A2_FIRST_LEG_BAR].analyses[timeframe].impulses
    # Emparejados por instante de constitución y no por `id_num`: en cuanto una
    # rotura se mueve, la numeración de todo lo que viene detrás se desplaza y
    # comparar por número diría que cambió todo cuando puede no haber cambiado.
    by_birth = {impulse.ts_constitution: impulse for impulse in left}

    aligned = identical = moved = 0
    divergences: list[datetime] = []
    for candidate in right:
        twin = by_birth.get(candidate.ts_constitution)
        if twin is None:
            divergences.append(candidate.ts_constitution)
            continue
        aligned += 1
        identical += int(twin.anchor == candidate.anchor)
        if twin.ts_end != candidate.ts_end:
            moved += 1
            divergences.append(candidate.ts_constitution)

    return TimeframeAnchorComparison(
        timeframe=timeframe,
        impulses_a1=len(left),
        impulses_a2=len(right),
        aligned=aligned,
        identical_anchor=identical,
        changed_break=moved,
        unaligned=max(len(left), len(right)) - aligned,
        first_divergence=min(divergences) if divergences else None,
        gap_by_year=_gap_by_year(runs[AnchorMode.A2_FIRST_LEG_BAR].analyses[timeframe].table),
    )


def _gap_by_year(table: pd.DataFrame) -> pd.DataFrame:
    """|A1 - A2| por año, en USD, en % del precio y en ATR.

    Los dólares no son comparables entre 2018 y 2025 —el oro pasó de ~1.200 a
    ~4.300—, así que la misma diferencia va siempre en las tres unidades.
    """
    columns = [
        "anio", "n", "difieren", "mediana_usd", "p90_usd", "max_usd",
        "max_pct_precio", "max_atr",
    ]
    if table.empty:
        return pd.DataFrame(columns=columns)

    frame = pd.DataFrame(
        {
            "anio": pd.DatetimeIndex(table["ts_constitucion"]).year,
            "usd": pd.to_numeric(table["diferencia_anclas_usd"], errors="coerce"),
            "precio": pd.to_numeric(table["precio_ancla"], errors="coerce").abs(),
            "atr": pd.to_numeric(table["atr_previo"], errors="coerce"),
        }
    )
    frame["pct"] = frame["usd"] / frame["precio"].where(frame["precio"] > 0)
    frame["en_atr"] = frame["usd"] / frame["atr"].where(frame["atr"] > 0)

    rows = [_gap_row(str(year), group) for year, group in frame.groupby("anio", sort=True)]
    rows.append(_gap_row("TOTAL", frame))
    return pd.DataFrame(rows, columns=columns)


def _gap_row(label: str, group: pd.DataFrame) -> dict[str, object]:
    usd = group["usd"].to_numpy(dtype=float)
    usd = usd[np.isfinite(usd)]
    return {
        "anio": label,
        "n": int(usd.size),
        "difieren": int((usd > 0).sum()),
        "mediana_usd": float(np.median(usd)) if usd.size else float("nan"),
        "p90_usd": float(np.percentile(usd, 90)) if usd.size else float("nan"),
        "max_usd": float(usd.max()) if usd.size else float("nan"),
        "max_pct_precio": _max(group["pct"]),
        "max_atr": _max(group["en_atr"]),
    }


def _max(values: pd.Series) -> float:
    clean = values.to_numpy(dtype=float)
    clean = clean[np.isfinite(clean)]
    return float(clean.max()) if clean.size else float("nan")
