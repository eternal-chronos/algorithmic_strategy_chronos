"""Medición de la firma de lateralización (sección D). Descriptiva, no decide nada.

El propietario define la lateralización como *el precio toca la parte alta del
ID, toca la parte baja, y repite: dos toques en cada extremo sin romper ninguno*.
Los límites del rango no son parámetros nuevos —son el `ancla` y el `extremo` que
el módulo ya calcula— y el umbral de dos toques es su definición, no un valor
optimizado por el motor.

Nada de lo que se mide aquí entra en la detección: la tabla de impulsos, los
eventos de rotura y el estado barra a barra salen exactamente iguales con este
módulo dentro que fuera. Lo que sale de aquí son números para decidir, y la
decisión es del propietario.

La cifra que manda es **D.4**: qué proporción de los ID enanos —rango por debajo
de 0,25 ATR— cae pegada a un ID que sí cumple la firma. Si es alta, los impulsos
minúsculos son un síntoma de lateralización y no hace falta ningún umbral de
tamaño; si es baja, son un problema aparte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.geometry import (
    GEOMETRY_COLUMNS,
    atr_by_bar,
    geometry_of,
)
from chronos.domain.structure.contacts import ContactSeries, classify_contacts, touches
from chronos.domain.structure.enums import ContactKind, ContactSide

#: Contactos que el propietario exige en **cada** límite para hablar de
#: lateralización. Es su definición, literal; el motor no la ha ajustado.
SIGNATURE_MIN_TOUCHES = 2

#: Por debajo de este rango en ATR, el ID es "degenerado" a efectos de D.4. Sale
#: del enunciado, no de una búsqueda de parámetros.
DEGENERATE_RANGE_ATR = 0.25

#: Horizontes en barras para la vuelta al 50 % tras la rotura real (D.5).
REVISIT_HORIZONS = (5, 20, 60)

CONTACT_COLUMNS = (
    "timeframe",
    "id_num",
    "direccion",
    "timestamp",
    "tipo_contacto",
    "limite",
    "nivel",
    "cierre",
    *GEOMETRY_COLUMNS,
)


@dataclass(frozen=True, slots=True)
class ImpulseContacts:
    """Todo lo que se sabe de un ID en cuanto a contactos con sus límites."""

    timeframe: str
    id_num: int
    year: int
    direction: str
    upper: float
    lower: float
    range_atr: float
    bars_alive: int
    exit_break: str | None
    series: ContactSeries

    @property
    def midpoint(self) -> float:
        """Nivel del 50 % del ID: el punto medio entre ancla y extremo (D.5)."""
        return (self.upper + self.lower) / 2.0

    @property
    def touches_upper(self) -> int:
        return touches(self.series, ContactSide.SUPERIOR)

    @property
    def touches_lower(self) -> int:
        return touches(self.series, ContactSide.INFERIOR)

    @property
    def total_touches(self) -> int:
        return self.touches_upper + self.touches_lower

    @property
    def meets_signature(self) -> bool:
        """Dos contactos arriba y dos abajo antes de la rotura real."""
        return (
            self.touches_upper >= SIGNATURE_MIN_TOUCHES
            and self.touches_lower >= SIGNATURE_MIN_TOUCHES
        )

    @property
    def is_degenerate(self) -> bool:
        return bool(np.isfinite(self.range_atr) and self.range_atr < DEGENERATE_RANGE_ATR)

    @property
    def signature_index(self) -> int | None:
        """La barra en que se cumple la firma: el segundo toque del límite que
        más tardó en tenerlos. Desde ahí el ID está en ACUMULACIÓN.

        Es causal: en esa barra ya se han visto dos toques arriba y dos abajo,
        y ninguno posterior cambia el momento en que se cumplió. `None` si el
        ID murió sin llegar a cumplirla.
        """
        seen = {ContactSide.SUPERIOR: 0, ContactSide.INFERIOR: 0}
        for contact in self.series.contacts:
            if contact.kind is ContactKind.ROTURA_REAL:
                continue
            seen[contact.side] += 1
            if all(count >= SIGNATURE_MIN_TOUCHES for count in seen.values()):
                return contact.index
        return None

    def count(self, kind: ContactKind, side: ContactSide) -> int:
        return sum(
            1
            for contact in self.series.contacts
            if contact.kind is kind and contact.side is side
        )


@dataclass(frozen=True, slots=True)
class TimeframeLateralization:
    """Medición de una temporalidad, más la tabla de contactos que se persiste."""

    timeframe: str
    impulses: tuple[ImpulseContacts, ...]
    #: Un registro por contacto, con la geometría de la sección E.
    contacts: pd.DataFrame
    #: Vueltas al 50 % tras la rotura real, por horizonte (D.5).
    revisits: pd.DataFrame

    @property
    def with_signature(self) -> tuple[ImpulseContacts, ...]:
        return tuple(item for item in self.impulses if item.meets_signature)

    @property
    def degenerates(self) -> tuple[ImpulseContacts, ...]:
        return tuple(item for item in self.impulses if item.is_degenerate)


@dataclass(frozen=True, slots=True)
class LateralizationStudy:
    per_timeframe: dict[str, TimeframeLateralization]


def measure(run: ImpulseRun) -> LateralizationStudy:
    """Mide los contactos de todos los ID de la corrida. Con el módulo apagado, nada."""
    if not run.enabled:
        return LateralizationStudy(per_timeframe={})
    return LateralizationStudy(
        per_timeframe={
            timeframe: _measure_timeframe(analysis)
            for timeframe, analysis in run.analyses.items()
        }
    )


# --- Medición ---------------------------------------------------------------


def _measure_timeframe(analysis: TimeframeAnalysis) -> TimeframeLateralization:
    bars = analysis.bars
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    last_index = len(bars) - 1
    atr_by_id = _atr_by_id(analysis)

    measured: list[ImpulseContacts] = []
    for impulse in analysis.impulses:
        if not impulse.publishable:
            continue
        upper, lower = max(impulse.anchor, impulse.extreme), min(impulse.anchor, impulse.extreme)
        end = impulse.index_end if impulse.index_end is not None else last_index
        series = classify_contacts(
            high=high,
            low=low,
            close=close,
            upper=upper,
            lower=lower,
            # La barra de constitución no cuenta: el rango nace en su cierre.
            first=impulse.index_constitution + 1,
            last=end,
            broke_at=impulse.index_end,
        )
        measured.append(
            ImpulseContacts(
                timeframe=analysis.timeframe,
                id_num=impulse.id_num,
                year=impulse.ts_constitution.year,
                direction=impulse.direction.value,
                upper=upper,
                lower=lower,
                range_atr=atr_by_id.get(impulse.id_num, float("nan")),
                bars_alive=impulse.bars_alive(last_index),
                exit_break=impulse.exit_break_kind.value if impulse.exit_break_kind else None,
                series=series,
            )
        )

    return TimeframeLateralization(
        timeframe=analysis.timeframe,
        impulses=tuple(measured),
        contacts=_contacts_frame(analysis, measured),
        revisits=_revisits(analysis, measured),
    )


def _atr_by_id(analysis: TimeframeAnalysis) -> dict[int, float]:
    table = analysis.table
    if table.empty:
        return {}
    return dict(
        zip(
            table["id_num"].astype(int),
            pd.to_numeric(table["rango_atr"], errors="coerce"),
            strict=True,
        )
    )


def _contacts_frame(
    analysis: TimeframeAnalysis, measured: Sequence[ImpulseContacts]
) -> pd.DataFrame:
    """Un registro por contacto con la geometría de la sección E adosada."""
    rows = [
        (item.timeframe, item.id_num, item.direction, contact)
        for item in measured
        for contact in item.series.contacts
    ]
    if not rows:
        return pd.DataFrame(columns=list(CONTACT_COLUMNS))

    bars = analysis.bars
    stamps = pd.DatetimeIndex(bars.index)
    closes = bars["close"].to_numpy(dtype=float)
    indices = np.array([contact.index for *_, contact in rows], dtype=int)
    levels = np.array([contact.level for *_, contact in rows], dtype=float)
    upper = np.array(
        [contact.side is ContactSide.SUPERIOR for *_, contact in rows], dtype=bool
    )
    atr = atr_by_bar(analysis.table, stamps)[indices]

    frame = pd.DataFrame(
        {
            "timeframe": [timeframe for timeframe, *_ in rows],
            "id_num": [id_num for _, id_num, *_ in rows],
            "direccion": [direction for _, _, direction, _ in rows],
            "timestamp": stamps[indices],
            "tipo_contacto": [contact.kind.value for *_, contact in rows],
            "limite": [contact.side.value for *_, contact in rows],
            "nivel": levels,
            "cierre": closes[indices],
        }
    )
    geometry = geometry_of(bars, indices, levels=levels, beyond_upper=upper, atr=atr)
    return pd.concat([frame, geometry], axis=1)[list(CONTACT_COLUMNS)]


def _revisits(
    analysis: TimeframeAnalysis, measured: Sequence[ImpulseContacts]
) -> pd.DataFrame:
    """Vueltas al nivel del 50 % después de la rotura real (D.5).

    "Volver al nivel" es que la barra lo contenga: `low <= 50 % <= high`. Se
    cuentan las barras que lo tocan, no los cruces, porque es lo que se puede
    verificar mirando el gráfico.
    """
    columns = ["id_num", "cumple_firma", "punto_medio", *[f"toques_{n}" for n in REVISIT_HORIZONS]]
    bars = analysis.bars
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    ends = {
        impulse.id_num: impulse.index_end
        for impulse in analysis.impulses
        if impulse.index_end is not None
    }

    rows = []
    for item in measured:
        end = ends.get(item.id_num)
        if end is None:
            continue  # ID todavía vigente: no hay rotura real de la que medir
        row: dict[str, object] = {
            "id_num": item.id_num,
            "cumple_firma": item.meets_signature,
            "punto_medio": item.midpoint,
        }
        for horizon in REVISIT_HORIZONS:
            window = slice(end + 1, min(end + 1 + horizon, len(high)))
            row[f"toques_{horizon}"] = int(
                np.count_nonzero((low[window] <= item.midpoint) & (high[window] >= item.midpoint))
            )
        rows.append(row)

    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


# --- Tablas del informe -----------------------------------------------------


def contacts_by_year(measurement: TimeframeLateralization) -> pd.DataFrame:
    """D.1 — contactos por año, separando límite superior e inferior."""
    columns = [
        "anio", "impulsos", "mecha_sup", "mecha_inf", "fallida_sup", "fallida_inf",
        "mecha_por_id", "sin_contacto",
    ]
    if not measurement.impulses:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseContacts]) -> dict[str, object]:
        wicks_up = sum(item.count(ContactKind.TOQUE_MECHA, ContactSide.SUPERIOR) for item in group)
        wicks_down = sum(
            item.count(ContactKind.TOQUE_MECHA, ContactSide.INFERIOR) for item in group
        )
        return {
            "anio": label,
            "impulsos": len(group),
            "mecha_sup": wicks_up,
            "mecha_inf": wicks_down,
            "fallida_sup": sum(
                item.count(ContactKind.ROTURA_FALLIDA, ContactSide.SUPERIOR) for item in group
            ),
            "fallida_inf": sum(
                item.count(ContactKind.ROTURA_FALLIDA, ContactSide.INFERIOR) for item in group
            ),
            "mecha_por_id": (wicks_up + wicks_down) / len(group) if group else float("nan"),
            "sin_contacto": sum(1 for item in group if item.total_touches == 0),
        }

    rows = [row(str(year), group) for year, group in _by_year(measurement.impulses)]
    rows.append(row("TOTAL", measurement.impulses))
    return pd.DataFrame(rows, columns=columns)


def signature_by_year(measurement: TimeframeLateralization) -> pd.DataFrame:
    """D.2 — cuántos ID cumplen la firma del propietario, por año."""
    columns = ["anio", "impulsos", "cumplen_firma", "pct", "solo_arriba", "solo_abajo"]
    if not measurement.impulses:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseContacts]) -> dict[str, object]:
        meeting = sum(1 for item in group if item.meets_signature)
        return {
            "anio": label,
            "impulsos": len(group),
            "cumplen_firma": meeting,
            "pct": meeting / len(group) if group else float("nan"),
            "solo_arriba": sum(
                1
                for item in group
                if item.touches_upper >= SIGNATURE_MIN_TOUCHES
                and item.touches_lower < SIGNATURE_MIN_TOUCHES
            ),
            "solo_abajo": sum(
                1
                for item in group
                if item.touches_lower >= SIGNATURE_MIN_TOUCHES
                and item.touches_upper < SIGNATURE_MIN_TOUCHES
            ),
        }

    rows = [row(str(year), group) for year, group in _by_year(measurement.impulses)]
    rows.append(row("TOTAL", measurement.impulses))
    return pd.DataFrame(rows, columns=columns)


def populations(measurement: TimeframeLateralization) -> pd.DataFrame:
    """D.3 — las dos poblaciones cara a cara: cumplen la firma y no la cumplen."""
    columns = [
        "poblacion", "impulsos", "duracion_mediana", "rango_atr_mediano",
        "contactos_medianos", "salida_a_favor", "salida_en_contra", "sin_romper",
    ]
    groups = [
        ("cumple la firma", measurement.with_signature),
        ("no la cumple", tuple(i for i in measurement.impulses if not i.meets_signature)),
    ]
    rows: list[dict[str, object]] = []
    for label, group in groups:
        if not group:
            rows.append({**dict.fromkeys(columns, 0), "poblacion": label})
            continue
        rows.append(
            {
                "poblacion": label,
                "impulsos": len(group),
                "duracion_mediana": float(np.median([item.bars_alive for item in group])),
                "rango_atr_mediano": _median([item.range_atr for item in group]),
                "contactos_medianos": float(np.median([item.total_touches for item in group])),
                "salida_a_favor": sum(1 for item in group if item.exit_break == "ROTURA_A_FAVOR"),
                "salida_en_contra": sum(
                    1 for item in group if item.exit_break == "ROTURA_EN_CONTRA"
                ),
                "sin_romper": sum(1 for item in group if item.exit_break is None),
            }
        )
    return pd.DataFrame(rows, columns=columns)


@dataclass(frozen=True, slots=True)
class DegenerateVerdict:
    """D.4 — la cifra que decide si hará falta una palanca correctora."""

    timeframe: str
    degenerates: int
    impulses: int
    #: Degenerados con un vecino inmediato (anterior o posterior) que cumple la firma.
    next_to_signature: int
    #: Mismo cálculo sobre los ID no degenerados: sin él la cifra no dice nada,
    #: porque si *todos* los ID tienen un vecino con firma el dato es trivial.
    non_degenerates_next_to_signature: int
    non_degenerates: int

    @property
    def share(self) -> float:
        return self.next_to_signature / self.degenerates if self.degenerates else float("nan")

    @property
    def baseline_share(self) -> float:
        if not self.non_degenerates:
            return float("nan")
        return self.non_degenerates_next_to_signature / self.non_degenerates

    @property
    def degenerate_share(self) -> float:
        return self.degenerates / self.impulses if self.impulses else float("nan")


def degenerate_verdict(measurement: TimeframeLateralization) -> DegenerateVerdict:
    """D.4 — ¿los ID enanos son un síntoma de lateralización o un problema aparte?"""
    impulses = measurement.impulses
    signature = {item.id_num for item in impulses if item.meets_signature}
    by_id = {item.id_num: item for item in impulses}

    def has_neighbour(item: ImpulseContacts) -> bool:
        return any(
            neighbour in signature and neighbour in by_id
            for neighbour in (item.id_num - 1, item.id_num + 1)
        )

    degenerates = [item for item in impulses if item.is_degenerate]
    others = [item for item in impulses if not item.is_degenerate]
    return DegenerateVerdict(
        timeframe=measurement.timeframe,
        degenerates=len(degenerates),
        impulses=len(impulses),
        next_to_signature=sum(1 for item in degenerates if has_neighbour(item)),
        non_degenerates=len(others),
        non_degenerates_next_to_signature=sum(1 for item in others if has_neighbour(item)),
    )


def revisit_summary(measurement: TimeframeLateralization) -> pd.DataFrame:
    """D.5 — vuelta al nivel del 50 % tras la rotura real, por horizonte."""
    columns = [
        "poblacion",
        "impulsos",
        *[f"vuelven_{n}" for n in REVISIT_HORIZONS],
        *[f"toques_{n}" for n in REVISIT_HORIZONS],
    ]
    frame = measurement.revisits
    if frame.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for label, subset in (
        ("cumple la firma", frame[frame["cumple_firma"]]),
        ("no la cumple", frame[~frame["cumple_firma"]]),
        ("todos", frame),
    ):
        row: dict[str, object] = {"poblacion": label, "impulsos": len(subset)}
        for horizon in REVISIT_HORIZONS:
            touches_at = subset[f"toques_{horizon}"]
            hits = int((touches_at > 0).sum())
            # Dos cifras distintas y las dos importan: cuántos vuelven alguna vez
            # y cuántas barras se quedan alrededor del nivel los que vuelven.
            row[f"vuelven_{horizon}"] = hits / len(subset) if len(subset) else float("nan")
            row[f"toques_{horizon}"] = (
                float(touches_at.mean()) if len(subset) else float("nan")
            )
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def detail(
    measurement: TimeframeLateralization,
    analysis: TimeframeAnalysis,
    id_numbers: Sequence[int],
) -> pd.DataFrame:
    """D.6 — ficha de unos ID concretos con sus contactos ya clasificados."""
    columns = [
        "id_num", "direccion", "constitucion", "fin", "ancla", "extremo", "rango_atr",
        "mecha_sup", "mecha_inf", "fallida_sup", "fallida_inf", "cumple_firma", "salida",
    ]
    wanted = set(id_numbers)
    by_id = {impulse.id_num: impulse for impulse in analysis.impulses}
    rows = []
    for item in measurement.impulses:
        if item.id_num not in wanted:
            continue
        impulse = by_id[item.id_num]
        rows.append(
            {
                "id_num": item.id_num,
                "direccion": item.direction,
                "constitucion": pd.Timestamp(impulse.ts_constitution),
                "fin": pd.Timestamp(impulse.ts_end) if impulse.ts_end else None,
                "ancla": impulse.anchor,
                "extremo": impulse.extreme,
                "rango_atr": item.range_atr,
                "mecha_sup": item.count(ContactKind.TOQUE_MECHA, ContactSide.SUPERIOR),
                "mecha_inf": item.count(ContactKind.TOQUE_MECHA, ContactSide.INFERIOR),
                "fallida_sup": item.count(ContactKind.ROTURA_FALLIDA, ContactSide.SUPERIOR),
                "fallida_inf": item.count(ContactKind.ROTURA_FALLIDA, ContactSide.INFERIOR),
                "cumple_firma": "sí" if item.meets_signature else "no",
                "salida": item.exit_break or "vigente",
            }
        )
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


# --- Apoyo ------------------------------------------------------------------


def _by_year(
    impulses: Sequence[ImpulseContacts],
) -> list[tuple[int, tuple[ImpulseContacts, ...]]]:
    years = sorted({item.year for item in impulses})
    return [
        (year, tuple(item for item in impulses if item.year == year)) for year in years
    ]


def _median(values: Sequence[float]) -> float:
    clean = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    return float(np.median(clean)) if clean.size else float("nan")
