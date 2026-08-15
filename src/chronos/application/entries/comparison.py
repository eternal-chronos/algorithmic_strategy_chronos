"""Fase 3.1 contra fase 3.0: las dos corridas, una al lado de la otra.

La 3.1 cambia **una sola cosa** —qué confirma en H1— y todo lo demás se queda
igual: el mismo detector, las mismas zonas, la misma regla de rotura, la misma
ejecución y el mismo objetivo. Por eso la comparación se puede hacer observación
a observación: las dos corridas producen **exactamente las mismas
observaciones**, porque las observaciones se recogen antes de mirar H1. Lo único
que cambia es cuáles de ellas confirman.

Esa es la razón de que aquí no haya ningún emparejamiento aproximado ni ninguna
heurística: dos observaciones son la misma si coinciden en ID, tipo de zona,
vela de contacto y vela de retesteo. Si esa clave dejara de ser única, la
comparación entera estaría mintiendo, así que se comprueba.

**Aquí no se interpreta nada.** Se cuenta qué se perdió y qué se ganó; qué
significa lo decide el propietario.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import pandas as pd

from chronos.application.entries.cascade import FUNNEL_STEPS, CascadeRun, build_cascade
from chronos.application.entries.execution import ExecutionRun
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zones import ZonesRun
from chronos.domain.entries.enums import (
    ConfirmationKind,
    ConfirmPriority,
    GuardRail,
    Outcome,
)
from chronos.domain.entries.signal import Confirmation, Observation
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.zones import ZoneKind

#: La clave de una observación: lo que la identifica entre corridas.
ObservationKey = tuple[int, str, int, int | None]


@dataclass(frozen=True, slots=True)
class PhaseRun:
    """Una corrida completa de una fase: cascada, ejecución y tabla de operaciones.

    Existe para que el informe pueda pedir "lo mismo, de la otra fase" sin
    arrastrar tres argumentos paralelos que se pueden desparejar.
    """

    cascade: CascadeRun
    execution: ExecutionRun
    trades: pd.DataFrame

    @property
    def label(self) -> str:
        return self.cascade.config.confirm_mode.value


def observation_key(observation: Observation) -> ObservationKey:
    """ID de H4, tipo de zona, vela de contacto y vela de retesteo.

    El retesteo entra en la clave porque una misma zona tocada produce hasta dos
    observaciones —la de respeto y la de rotura y retesteo— con el mismo contacto
    y desenlaces distintos. Sin él las dos colapsarían en una.
    """
    return (
        observation.id_num,
        observation.zone.value,
        observation.index_contact,
        observation.index_retest,
    )


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    """Qué le pasó a una observación en una corrida: confirmó, o murió y dónde."""

    observation: Observation
    confirmation: Confirmation | None
    guard_rail: GuardRail | None

    @property
    def confirmed(self) -> bool:
        return self.confirmation is not None


def outcomes_by_observation(cascade: CascadeRun) -> dict[ObservationKey, ObservationOutcome]:
    """Una entrada por observación, con lo que le pasó.

    Una observación puede aparecer en varias señales —una por variante de
    entrada— y en varias descartadas. Todas comparten la misma confirmación, así
    que la primera que se encuentra vale; lo que no vale es contarla dos veces.
    """
    found: dict[ObservationKey, ObservationOutcome] = {}
    for signal in cascade.signals:
        key = observation_key(signal.observation)
        found.setdefault(
            key,
            ObservationOutcome(signal.observation, signal.confirmation, None),
        )
    for item in cascade.discarded:
        key = observation_key(item.observation)
        previous = found.get(key)
        if previous is not None and previous.confirmed:
            continue
        found[key] = ObservationOutcome(
            item.observation, item.confirmation, item.guard_rail
        )
    return found


@dataclass(frozen=True, slots=True)
class LostConfirmation:
    """Una observación que la 3.0 confirmaba y la 3.1 ya no.

    Es lo primero que el propietario quiere auditar, así que viaja con la vía por
    la que confirmaba antes y con el guardarraíl en el que muere ahora.
    """

    id_num: int
    direction: ImpulseDirection
    zone: ZoneKind
    outcome: Outcome
    index_contact: int
    ts_contact: datetime
    zone_inner: float
    zone_outer: float
    #: Por dónde confirmaba en la 3.0, y cuándo.
    via_v30: ConfirmationKind
    ts_confirmation_v30: datetime
    #: Dónde muere en la 3.1. `None` sólo si la observación desapareciera, que no
    #: puede pasar: las dos corridas producen las mismas observaciones.
    rail_v31: GuardRail | None


def lost_confirmations(
    v30: CascadeRun, v31: CascadeRun
) -> tuple[LostConfirmation, ...]:
    """Las que confirmaban antes y ahora no. En orden cronológico de contacto."""
    before = outcomes_by_observation(v30)
    after = outcomes_by_observation(v31)
    lost = []
    for key, item in before.items():
        if item.confirmation is None:
            continue
        now = after.get(key)
        if now is not None and now.confirmed:
            continue
        observation = item.observation
        lost.append(
            LostConfirmation(
                id_num=observation.id_num,
                direction=observation.direction,
                zone=observation.zone,
                outcome=observation.outcome,
                index_contact=observation.index_contact,
                ts_contact=observation.ts_contact,
                zone_inner=observation.zone_inner,
                zone_outer=observation.zone_outer,
                via_v30=item.confirmation.kind,
                ts_confirmation_v30=item.confirmation.timestamp,
                rail_v31=None if now is None else now.guard_rail,
            )
        )
    return tuple(sorted(lost, key=lambda item: item.ts_confirmation_v30))


def gained_confirmations(
    v30: CascadeRun, v31: CascadeRun
) -> tuple[ObservationOutcome, ...]:
    """Las que la 3.1 confirma y la 3.0 no. Existen: el OB alcanzado no es el OB
    al nacer, y una observación puede llegar al OB mucho después de que naciera."""
    before = outcomes_by_observation(v30)
    after = outcomes_by_observation(v31)
    return tuple(
        item
        for key, item in after.items()
        if item.confirmed and not (key in before and before[key].confirmed)
    )


def check_observations_match(v30: CascadeRun, v31: CascadeRun) -> None:
    """Las dos corridas tienen que producir las MISMAS observaciones.

    Si no coincidieran, el cambio de la 3.1 habría tocado algo por encima de H1 y
    toda la comparación estaría emparejando cosas distintas. Se comprueba en vez
    de suponerse.
    """
    left = {observation_key(item) for item in v30.observations}
    right = {observation_key(item) for item in v31.observations}
    if left != right:
        raise DomainError(
            "Las dos corridas no producen las mismas observaciones: "
            f"{len(left - right)} sólo en v30 y {len(right - left)} sólo en v31. "
            "El cambio de la fase 3.1 sólo puede afectar a la confirmación en H1."
        )


# --- Embudo ------------------------------------------------------------------


def funnel_comparison(
    v30: CascadeRun, v31: CascadeRun, execution_v30: ExecutionRun, execution_v31: ExecutionRun
) -> pd.DataFrame:
    """El embudo entero de las dos corridas, paso a paso y con la diferencia."""
    steps = [
        *((step, step) for step in FUNNEL_STEPS),
        ("se_ejecutan", None),
        ("con_desenlace", None),
    ]
    rows = []
    for name, key in steps:
        if key is not None:
            before = int(v30.funnel.get(key, 0))
            after = int(v31.funnel.get(key, 0))
        elif name == "se_ejecutan":
            before, after = len(execution_v30.trades), len(execution_v31.trades)
        else:
            before = sum(1 for trade in execution_v30.trades if trade.outcome.is_resolved)
            after = sum(1 for trade in execution_v31.trades if trade.outcome.is_resolved)
        rows.append(
            {
                "paso": name,
                "n_30": before,
                "n_31": after,
                "diferencia": after - before,
                "pct_de_la_30": after / before if before else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def lost_table(lost: Sequence[LostConfirmation]) -> pd.DataFrame:
    """Las confirmaciones perdidas, desglosadas por vía de la 3.0 y guardarraíl."""
    if not lost:
        return pd.DataFrame(columns=["via_30", "muere_en_31", "n"])
    frame = pd.DataFrame(
        [
            {
                "via_30": item.via_v30.value,
                "muere_en_31": "(sigue viva)" if item.rail_v31 is None else item.rail_v31.value,
            }
            for item in lost
        ]
    )
    counted = (
        frame.groupby(["via_30", "muere_en_31"], sort=True)
        .size()
        .reset_index(name="n")
    )
    total = pd.DataFrame([{"via_30": "TOTAL", "muere_en_31": "", "n": len(lost)}])
    return pd.concat([counted, total], ignore_index=True)


def lost_detail(lost: Sequence[LostConfirmation]) -> pd.DataFrame:
    """Una fila por confirmación perdida, para el CSV y para las capturas."""
    return pd.DataFrame(
        [
            {
                "ts_confirmacion_30": item.ts_confirmation_v30,
                "via_30": item.via_v30.value,
                "muere_en_31": None if item.rail_v31 is None else item.rail_v31.value,
                "id_h4": item.id_num,
                "zona_h4": item.zone.value,
                "desenlace_zona": item.outcome.value,
                "direccion": item.direction.value,
                "ts_contacto": item.ts_contact,
                "zona_interior": item.zone_inner,
                "zona_exterior": item.zone_outer,
            }
            for item in lost
        ]
    )


# --- CONFIRM_PRIORITY --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriorityEffect:
    """Qué cambia al invertir `CONFIRM_PRIORITY`. Con cero cambios, es cosmético."""

    priority: ConfirmPriority
    other: ConfirmPriority
    #: Confirmaciones en las que las DOS vías estaban disponibles a la vez.
    both_available: int
    total_confirmations: int
    #: Confirmaciones —**observaciones**, no señales— cuya vía cambia al
    #: invertir el orden. Misma unidad que `both_available`.
    changed_via: int
    #: Señales cuya decisión —instante, zona de entrada o stop— cambia. Si es 0,
    #: el orden no mueve ni una operación y se declara cosmético.
    changed_decision: int

    @property
    def cosmetic(self) -> bool:
        return self.changed_decision == 0

    def describe(self) -> str:
        verdict = (
            "COSMÉTICO: no cambia ni una operación, sólo la etiqueta de la vía"
            if self.cosmetic
            else f"CAMBIA {self.changed_decision} decisiones de entrada"
        )
        return (
            f"{self.both_available:,} de {self.total_confirmations:,} confirmaciones "
            f"tenían las dos vías disponibles; invertir el orden cambia la vía en "
            f"{self.changed_via:,} de ellas. {verdict}."
        )


def priority_effect(
    run: ImpulseRun,
    zones: ZonesRun,
    m15_bars: pd.DataFrame | None,
    cascade: CascadeRun,
) -> PriorityEffect:
    """Corre la cascada con el orden invertido y compara señal a señal.

    Es la única forma honesta de contestar a "¿importa el orden?": se corre y se
    mira, en vez de razonar sobre el código. La corrida alternativa no se
    ejecuta sobre M1 —lo que se compara es la **decisión**, y si la decisión es
    idéntica la operación no puede ser otra.

    **Las vías cambiadas se cuentan por OBSERVACIÓN y no por señal.** Una misma
    observación produce hasta dos señales —una por variante de entrada— que
    comparten confirmación, así que contarlas por señal daría el doble y esa
    cifra no se podría comparar con las confirmaciones que tenían las dos vías
    disponibles, que son observaciones. Dos unidades distintas en la misma frase
    es una frase que engaña.
    """
    config = cascade.config
    other = (
        ConfirmPriority.OB_PRIMERO
        if config.confirm_priority is ConfirmPriority.TURTLE_PRIMERO
        else ConfirmPriority.TURTLE_PRIMERO
    )
    swapped = build_cascade(
        run, zones, m15_bars, replace(config, confirm_priority=other)
    )
    mine = {
        (observation_key(signal.observation), signal.entry_zone.timeframe.value): signal
        for signal in cascade.signals
    }
    theirs = {
        (observation_key(signal.observation), signal.entry_zone.timeframe.value): signal
        for signal in swapped.signals
    }
    changed: set[ObservationKey] = set()
    changed_decision = 0
    for key, signal in mine.items():
        twin = theirs.get(key)
        if twin is None:
            changed_decision += 1
            continue
        if twin.confirmation.kind is not signal.confirmation.kind:
            changed.add(key[0])
        if (
            twin.ts_decision != signal.ts_decision
            or twin.entry_zone.inner != signal.entry_zone.inner
            or twin.entry_zone.outer != signal.entry_zone.outer
            or len(twin.stop_options) != len(signal.stop_options)
        ):
            changed_decision += 1
    changed_decision += len(set(theirs) - set(mine))

    confirmations = [
        item.confirmation
        for item in outcomes_by_observation(cascade).values()
        if item.confirmation is not None
    ]
    return PriorityEffect(
        priority=config.confirm_priority,
        other=other,
        both_available=sum(1 for item in confirmations if item.both_available),
        total_confirmations=len(confirmations),
        changed_via=len(changed),
        changed_decision=changed_decision,
    )


def confirmations_by_via(cascade: CascadeRun) -> pd.DataFrame:
    """Cuántas observaciones confirmó cada vía, y con cuántas coincidencias."""
    confirmations = [
        item.confirmation
        for item in outcomes_by_observation(cascade).values()
        if item.confirmation is not None
    ]
    if not confirmations:
        return pd.DataFrame(columns=["via", "n", "pct", "con_la_otra_via_disponible"])
    total = len(confirmations)
    rows = []
    for kind in ConfirmationKind:
        taken = [item for item in confirmations if item.kind is kind]
        if not taken:
            continue
        rows.append(
            {
                "via": kind.value,
                "n": len(taken),
                "pct": len(taken) / total,
                "con_la_otra_via_disponible": sum(
                    1 for item in taken if item.both_available
                ),
            }
        )
    rows.append(
        {
            "via": "TOTAL",
            "n": total,
            "pct": 1.0,
            "con_la_otra_via_disponible": sum(
                1 for item in confirmations if item.both_available
            ),
        }
    )
    return pd.DataFrame(rows)


__all__ = [
    "LostConfirmation",
    "ObservationOutcome",
    "PhaseRun",
    "PriorityEffect",
    "check_observations_match",
    "confirmations_by_via",
    "funnel_comparison",
    "gained_confirmations",
    "lost_confirmations",
    "lost_detail",
    "lost_table",
    "observation_key",
    "outcomes_by_observation",
    "priority_effect",
]
