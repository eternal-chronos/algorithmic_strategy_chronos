"""Las cinco auditorías por arquetipo del §9, documentadas una a una.

Cinco días-arquetipo con velas exactas. No es una muestra "representativa" ni una
selección de las mejores: es un ejemplar de cada forma que la cascada puede
tomar, elegido por un criterio escrito **antes** de mirar el resultado y el mismo
para todos —el primero cronológicamente que cumple la forma—, para que la
elección no la haga el resultado.

    1. el caso de manual ....... todo alineado, entrada limpia, objetivo alcanzado
    2. el caso en contra ....... todo alineado y salta el stop
    3. los guardarraíles ....... zona rota sin retesteo · sin confirmación en H1 ·
                                 sin OB en M15
    4. los bordes .............. contacto justo en el borde de la zona ·
                                 confirmación en la última barra posible
    5. el conflicto ............ el Diario dice una cosa y H4 la contraria

**Si un arquetipo no existe en la muestra, se declara. La ausencia es un dato**, y
rellenarla con el ejemplar "más parecido" sería convertir un hecho en una
opinión. Cada ficha dice si su arquetipo se encontró y, si no, por qué no puede
haberlo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pandas as pd

from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.execution import ExecutionRun
from chronos.domain.entries.enums import (
    ConfirmationKind,
    DailyContext,
    GuardRail,
    Outcome,
    TradeOutcome,
)
from chronos.domain.entries.signal import DiscardedSignal, Trade


@dataclass(frozen=True, slots=True)
class Archetype:
    """Un arquetipo: qué se buscaba, con qué criterio, y qué se encontró."""

    number: int
    title: str
    criterion: str
    #: `None` cuando el arquetipo **no existe** en la muestra. Es un resultado.
    trade: Trade | None = None
    discarded: DiscardedSignal | None = None
    #: Por qué no existe, cuando no existe. Vacío si sí existe.
    absence: str = ""
    #: Nombre del fichero de captura, para poder cruzarlo con `now/fase30/`.
    capture: str = ""

    @property
    def found(self) -> bool:
        return self.trade is not None or self.discarded is not None

    def narrative(self) -> tuple[str, ...]:
        """La ficha en prosa: contacto, observación, confirmación, entrada, salida."""
        if self.trade is not None:
            return _trade_narrative(self.trade)
        if self.discarded is not None:
            return _discarded_narrative(self.discarded)
        return (f"NO EXISTE EN LA MUESTRA. {self.absence}",)


def audit(cascade: CascadeRun, execution: ExecutionRun) -> tuple[Archetype, ...]:
    """Los cinco arquetipos del §9 sobre una corrida completa."""
    trades = execution.trades
    dead = (*cascade.discarded, *execution.discarded)
    return (
        _manual(trades),
        _against(trades),
        *_guard_rails(dead),
        *_edges(trades),
        _conflict(trades),
    )


# --- 1 y 2: el caso de manual y el caso en contra ----------------------------


def _aligned(trade: Trade) -> bool:
    """Todo alineado: contexto diario a favor, respeto de la zona y confirmación
    por estructura de H1 —ID u OB—, no por un rechazo."""
    return (
        trade.daily is DailyContext.A_FAVOR
        and trade.outcome_kind is Outcome.RESPETO
        and trade.signal.confirmation.kind is not ConfirmationKind.RECHAZO
    )


def _manual(trades: Sequence[Trade]) -> Archetype:
    found = _first(trades, lambda trade: _aligned(trade) and trade.outcome is TradeOutcome.OBJETIVO)
    return Archetype(
        number=1,
        title="El caso de manual: todo alineado y objetivo alcanzado",
        criterion=(
            "el PRIMERO cronológicamente con contexto diario a favor, desenlace de "
            "respeto, confirmación por ID u OB de H1 (no por rechazo) y objetivo "
            "alcanzado"
        ),
        trade=found,
        absence=(
            "No hay ninguna operación que reúna las cuatro condiciones a la vez y "
            "acabe en objetivo."
            if found is None
            else ""
        ),
        capture="arquetipo_1_manual",
    )


def _against(trades: Sequence[Trade]) -> Archetype:
    found = _first(
        trades,
        lambda trade: _aligned(trade)
        and trade.outcome in (TradeOutcome.STOP, TradeOutcome.STOP_MISMA_BARRA),
    )
    return Archetype(
        number=2,
        title="El caso en contra: todo alineado y salta el stop",
        criterion=(
            "el mismo criterio del arquetipo 1, cambiando sólo el desenlace. Es la "
            "pareja del anterior: mismas condiciones de entrada, futuro contrario"
        ),
        trade=found,
        absence=(
            "No hay ninguna operación con las cuatro condiciones que acabe en stop."
            if found is None
            else ""
        ),
        capture="arquetipo_2_en_contra",
    )


# --- 3: un rechazo por cada guardarraíl --------------------------------------

#: Los tres guardarraíles que el §9.3 nombra, con la explicación de qué mata cada
#: uno. El orden es el del enunciado.
_RAILS: tuple[tuple[GuardRail, str], ...] = (
    (
        GuardRail.ROTURA_SIN_RETESTEO,
        "la zona se rompió y el precio no volvió a testearla antes de que se "
        "constituyera el ID de H4 siguiente",
    ),
    (
        GuardRail.SIN_CONFIRMACION_H1,
        "la zona estuvo en observación y en H1 no apareció ni ID en la dirección, "
        "ni OB en la dirección, ni rechazo en ninguna de sus tres definiciones",
    ),
    (
        GuardRail.SIN_OB_M15,
        "H1 confirmó y en M15 no llegó a formarse ningún OB suelto: ninguna vela "
        "de la dirección superó, mecha incluida, a la contraria anterior",
    ),
)


def _dies_of(rail: GuardRail) -> Callable[[DiscardedSignal], bool]:
    """El predicado del guardarraíl, ligado a él y no a la variable del bucle."""
    return lambda item: item.guard_rail is rail


def _guard_rails(dead: Sequence[DiscardedSignal]) -> tuple[Archetype, ...]:
    built = []
    for position, (rail, why) in enumerate(_RAILS, start=1):
        found = _first(dead, _dies_of(rail))
        built.append(
            Archetype(
                number=3,
                title=f"Guardarraíl {position}/3 · {rail.value}",
                criterion=f"la PRIMERA señal descartada con motivo `{rail.value}`: {why}",
                discarded=found,
                absence=(
                    f"Ninguna señal murió por `{rail.value}` en todo el histórico."
                    if found is None
                    else ""
                ),
                capture=f"arquetipo_3_{rail.value}",
            )
        )
    return tuple(built)


# --- 4: los bordes -----------------------------------------------------------


def _edges(trades: Sequence[Trade]) -> tuple[Archetype, ...]:
    """Los dos bordes que el §9.4 nombra, cada uno con su ficha.

    El primero es geométrico: el contacto raspa el borde de la zona en vez de
    entrar en ella. El segundo es temporal: la confirmación llega en la última
    barra en que todavía se podía confirmar. Los dos son los sitios donde una
    desigualdad mal puesta —`<` en vez de `<=`— cambiaría el resultado sin que
    nada más lo delatara.
    """
    grazing = _first(trades, _grazes_the_edge)
    last_bar = _first(trades, _confirms_on_the_last_bar)
    return (
        Archetype(
            number=4,
            title="Borde 1/2 · el contacto raspa el borde de la zona",
            criterion=(
                "la PRIMERA operación cuyo contacto con la zona de H4 tocó su borde "
                "exterior o el interior sin entrar más allá de una milésima de la "
                "altura de la zona"
            ),
            trade=grazing,
            absence=(
                "Ningún contacto del histórico cayó tan pegado al borde."
                if grazing is None
                else ""
            ),
            capture="arquetipo_4_borde_de_zona",
        ),
        Archetype(
            number=4,
            title="Borde 2/2 · la confirmación llega en la última barra posible",
            criterion=(
                "la PRIMERA operación cuya confirmación de H1 cae en la última barra "
                "de H1 de la ventana de observación: una barra más tarde y la señal "
                "habría muerto en `sin_confirmacion_h1`"
            ),
            trade=last_bar,
            absence=(
                "Ninguna confirmación cayó en la última barra de su ventana."
                if last_bar is None
                else ""
            ),
            capture="arquetipo_4_ultima_barra",
        ),
    )


#: Milésimas de la altura de la zona por debajo de las cuales un contacto se
#: considera "en el borde". No es un parámetro de la estrategia: no interviene en
#: ninguna decisión, sólo elige qué operación se retrata.
_EDGE_TOLERANCE = 0.001


def _grazes_the_edge(trade: Trade) -> bool:
    observation = trade.signal.observation
    height = observation.zone_high - observation.zone_low
    if height <= 0:
        return False
    zone = trade.signal.entry_zone
    depth = min(
        abs(zone.inner - observation.zone_low), abs(zone.inner - observation.zone_high)
    )
    return depth / height <= _EDGE_TOLERANCE


def _confirms_on_the_last_bar(trade: Trade) -> bool:
    """La confirmación cae en la **última** barra de H1 de su ventana.

    Una barra más tarde y la señal habría muerto en `sin_confirmacion_h1`. La
    frontera viaja en la propia observación (`ts_window_end`), así que no hace
    falta volver a la corrida para saber dónde estaba.
    """
    window = trade.signal.observation.ts_window_end
    return window is not None and trade.signal.confirmation.timestamp == window


# --- 5: el conflicto Diario vs H4 --------------------------------------------


def _conflict(trades: Sequence[Trade]) -> Archetype:
    found = _first(trades, lambda trade: trade.daily is DailyContext.CONFLICTO)
    return Archetype(
        number=5,
        title="El conflicto: el Diario dice una cosa y H4 la contraria",
        criterion="la PRIMERA operación marcada con contexto diario en conflicto",
        trade=found,
        absence=(
            "No hubo ni un conflicto entre el Diario y H4 en todo el histórico."
            if found is None
            else ""
        ),
        capture="arquetipo_5_conflicto",
    )


# --- Apoyo -------------------------------------------------------------------


def _first[T](items: Sequence[T], predicate: Callable[[T], bool]) -> T | None:
    for item in items:
        if predicate(item):
            return item
    return None


def _trade_narrative(trade: Trade) -> tuple[str, ...]:
    observation = trade.signal.observation
    zone = trade.signal.entry_zone
    confirmation = trade.signal.confirmation
    return (
        f"Contacto     {observation.ts_contact:%Y-%m-%d %H:%M} · zona "
        f"{observation.zone.value} del ID {observation.id_num} de H4 "
        f"[{observation.zone_low:,.2f}, {observation.zone_high:,.2f}] · "
        f"dirección {observation.direction.value}",
        f"Observación  desenlace {observation.outcome.value}"
        + (
            f" · rotura {observation.ts_break:%Y-%m-%d %H:%M}"
            f" · retesteo {observation.ts_retest:%Y-%m-%d %H:%M}"
            if observation.ts_break is not None and observation.ts_retest is not None
            else ""
        ),
        f"Contexto     diario {observation.daily.value}"
        + (
            f" (ID {observation.daily_touch.id_num} del Diario, zona "
            f"{observation.daily_touch.zone.value}, "
            f"{observation.daily_touch.direction.value})"
            if observation.daily_touch is not None
            else ""
        ),
        f"Confirmación {confirmation.timestamp:%Y-%m-%d %H:%M} en H1 · "
        f"{confirmation.kind.value}"
        + (
            f" · rechazos: {', '.join(kind.value for kind in confirmation.rejections)}"
            if confirmation.rejections
            else ""
        ),
        f"Entrada      {trade.ts_entry:%Y-%m-%d %H:%M} al open de M1 · "
        f"{trade.entry_price:,.2f} · zona de {zone.timeframe.value} "
        f"[{zone.low:,.2f}, {zone.high:,.2f}]",
        f"Stop         {trade.stop_price:,.2f} (zona de {trade.stop_zone.value}) · "
        f"1R = {trade.risk_usd:,.2f} USD = {trade.risk_atr:.2f} ATR = "
        f"{trade.risk_pct_price:.3%} del precio",
        f"Objetivo     {trade.target_price:,.2f} (3,3 R fijo)",
        f"Desenlace    {trade.outcome.value}"
        + (f" el {trade.ts_exit:%Y-%m-%d %H:%M}" if trade.ts_exit is not None else "")
        + f" · bruto {trade.gross_r:+.2f} R · neto {trade.net_r:+.2f} R "
        f"(coste {trade.cost_r:.3f} R, {trade.nights} noches)",
    )


def _discarded_narrative(item: DiscardedSignal) -> tuple[str, ...]:
    observation = item.observation
    lines = [
        f"Contacto     {observation.ts_contact:%Y-%m-%d %H:%M} · zona "
        f"{observation.zone.value} del ID {observation.id_num} de H4 "
        f"[{observation.zone_low:,.2f}, {observation.zone_high:,.2f}] · "
        f"dirección {observation.direction.value}",
        f"Contexto     diario {observation.daily.value}",
    ]
    if item.confirmation is not None:
        lines.append(
            f"Confirmación {item.confirmation.timestamp:%Y-%m-%d %H:%M} en H1 · "
            f"{item.confirmation.kind.value}"
        )
    else:
        lines.append("Confirmación no llegó ninguna")
    lines.append(
        f"MUERE        {item.timestamp:%Y-%m-%d %H:%M} · guardarraíl "
        f"`{item.guard_rail.value}` · sin operación"
    )
    return tuple(lines)


def table(archetypes: Sequence[Archetype]) -> pd.DataFrame:
    """Los cinco arquetipos en una tabla, con las ausencias declaradas."""
    return pd.DataFrame(
        [
            {
                "n": item.number,
                "arquetipo": item.title,
                "encontrado": item.found,
                "cuando": (
                    item.trade.ts_entry
                    if item.trade is not None
                    else (item.discarded.timestamp if item.discarded is not None else None)
                ),
                "captura": item.capture,
                "ausencia": item.absence,
            }
            for item in archetypes
        ]
    )


__all__ = ["Archetype", "audit", "table"]
