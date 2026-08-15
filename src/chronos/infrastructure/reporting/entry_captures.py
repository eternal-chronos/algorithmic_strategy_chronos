"""Capturas de la fase 3.1: cada operación sobre las velas de H4.

Cuatro lotes, los que pide la fase:

  1. veinte confirmaciones por **turtle soup** (vía 1);
  2. veinte confirmaciones por **OB de H1 alcanzado** (vía 2);
  3. veinte señales que **la 3.0 tomaba y la 3.1 descarta**;
  4. los cinco arquetipos.

Todos los lotes son los **primeros cronológicamente** de su clase, no una
selección: elegir "los mejores" convertiría la carpeta en un argumento en vez de
en una muestra. El criterio va escrito en el `LEEME.txt` y es el mismo para los
cuatro.

Cada imagen lleva sobreimpreso lo que hace falta para juzgarla sin abrir ningún
CSV: la zona de H4 que se observó, el contacto, la confirmación de H1, la
entrada, el stop, el objetivo y el desenlace, con los precios y las horas.

Las zonas se dibujan con la misma función que las capturas de la fase 2.0
(`zone_captures.draw_zones`). Enseñarlas con otro trazo obligaría al propietario
a comparar dos dibujos distintos de lo mismo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from chronos.application.entries import archetypes
from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.comparison import LostConfirmation
from chronos.application.entries.execution import ExecutionRun
from chronos.application.structure.config import H4
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.entries.enums import ConfirmationKind
from chronos.domain.entries.signal import DiscardedSignal, Trade
from chronos.infrastructure.reporting import theme
from chronos.infrastructure.reporting.impulse_captures import (
    CaptureRequest,
    bar_labels,
    window_figure,
)
from chronos.infrastructure.reporting.zone_captures import draw_zones

WIDTH, HEIGHT, SCALE = 1600, 900, 1
DECIMALS = 2

#: Barras de H4 de contexto a cada lado de la ventana de la operación.
CONTEXT_BARS = 20

#: Cuántas capturas de cada lote.
PER_VIA = 20
LOST = 20

#: Píxeles entre dos rótulos apilados. No es estética: sin separación fija, dos
#: hitos de la misma vela —o dos precios a medio dólar— salen escritos encima.
_LABEL_ROW_HEIGHT = 15

BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE
STOP_COLOUR = theme.NEGATIVE
TARGET_COLOUR = theme.POSITIVE


@dataclass(frozen=True, slots=True)
class EntryCapture:
    """Una imagen escrita, con su línea del `LEEME.txt`."""

    path: Path
    lot: str
    note: str


def write_entry_captures(
    run: ImpulseRun,
    zones: ZonesRun,
    cascade: CascadeRun,
    execution: ExecutionRun,
    folder: Path,
    lost: Sequence[LostConfirmation] = (),
) -> list[Path]:
    """Escribe los cuatro lotes de la fase y devuelve las rutas producidas."""
    analysis = run.analyses.get(H4)
    zoned = zones.per_timeframe.get(H4)
    if analysis is None or zoned is None:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    by_id = {item.id_num: item for item in zoned.items}

    written: list[Path] = []
    for lot, trades in _trade_lots(execution):
        for position, trade in enumerate(trades, start=1):
            _keep(
                written,
                _write_trade(analysis, by_id, trade, folder, f"{lot}_{position:02d}"),
            )
    for position, item in enumerate(lost[:LOST], start=1):
        _keep(
            written,
            _write_lost(analysis, by_id, item, folder, f"perdida_{position:02d}"),
        )
    for path in _write_archetypes(analysis, by_id, cascade, execution, folder):
        _keep(written, path)
    return written


def _keep(written: list[Path], path: Path | None) -> None:
    """Una captura que no se pudo dibujar no se apunta, y tampoco rompe el lote.

    Ocurre cuando el ID de la operación no está entre las zonas de H4 —un ID de
    calentamiento, que no se publica— y no es un fallo: el `LEEME.txt` cuenta las
    que hay, no las que se pidieron.
    """
    if path is not None:
        written.append(path)


# --- Qué capturar ------------------------------------------------------------


def _trade_lots(execution: ExecutionRun) -> list[tuple[str, list[Trade]]]:
    """Las primeras N de cada vía de confirmación, cronológicamente.

    Una operación por vía y no por desenlace: lo que esta fase cambia es **qué
    confirma**, así que la muestra tiene que enseñar cada vía y no cada final.
    Una vía sin ninguna operación sale con el lote vacío y se ve en el
    `LEEME.txt`, que cuenta las imágenes que hay.
    """
    return [
        (
            label,
            [
                trade
                for trade in execution.trades
                if trade.signal.confirmation.kind is kind
            ][:PER_VIA],
        )
        for label, kind in (
            ("turtle", ConfirmationKind.TURTLE_SOUP),
            ("ob", ConfirmationKind.OB_H1),
        )
    ]


# --- Cómo se dibuja ----------------------------------------------------------


def _write_trade(
    analysis: TimeframeAnalysis,
    by_id: dict[int, ImpulseZones],
    trade: Trade,
    folder: Path,
    name: str,
) -> Path | None:
    observation = trade.signal.observation
    zoned = by_id.get(observation.id_num)
    if zoned is None:
        return None

    figure, first, _last, labels = _window(
        analysis,
        zoned,
        name=name,
        title=(
            f"H4 · ID {observation.id_num} ({observation.direction.value}) · "
            f"zona {observation.zone.value} · {observation.outcome.value} · "
            f"{trade.outcome.value.upper()}"
        ),
        subtitle=_trade_subtitle(trade),
        from_index=observation.index_contact,
        until=trade.ts_exit or trade.ts_entry,
    )
    _levels(figure, trade, labels)
    _milestones(figure, analysis, trade, first, labels)
    path = folder / f"{name}_{trade.ts_entry:%Y%m%d_%H%M}.png"
    figure.write_image(str(path), width=WIDTH, height=HEIGHT, scale=SCALE)
    return path


def _write_discarded(
    analysis: TimeframeAnalysis,
    by_id: dict[int, ImpulseZones],
    item: DiscardedSignal,
    folder: Path,
    name: str,
) -> Path | None:
    observation = item.observation
    zoned = by_id.get(observation.id_num)
    if zoned is None:
        return None
    figure, _first, _last, labels = _window(
        analysis,
        zoned,
        name=name,
        title=(
            f"H4 · ID {observation.id_num} ({observation.direction.value}) · "
            f"zona {observation.zone.value} · SEÑAL DESCARTADA"
        ),
        subtitle=(
            f"guardarraíl <b>{item.guard_rail.value}</b> · "
            f"contacto {observation.ts_contact:%Y-%m-%d %H:%M} UTC · "
            f"muere {item.timestamp:%Y-%m-%d %H:%M} UTC · "
            f"contexto diario {observation.daily.value} · "
            + (
                f"confirmó en H1 por {item.confirmation.kind.value}"
                if item.confirmation is not None
                else "no llegó a confirmar en H1"
            )
        ),
        from_index=observation.index_contact,
        until=item.timestamp,
    )
    _vertical(figure, labels, analysis, observation.ts_contact, "contacto", theme.INK_MUTED)
    if item.confirmation is not None:
        _vertical(
            figure, labels, analysis, item.confirmation.timestamp, "confirma H1", BULLISH
        )
    _vertical(figure, labels, analysis, item.timestamp, "MUERE", STOP_COLOUR)
    path = folder / f"{name}_{item.timestamp:%Y%m%d_%H%M}.png"
    figure.write_image(str(path), width=WIDTH, height=HEIGHT, scale=SCALE)
    return path


def _write_lost(
    analysis: TimeframeAnalysis,
    by_id: dict[int, ImpulseZones],
    item: LostConfirmation,
    folder: Path,
    name: str,
) -> Path | None:
    """Una señal que la 3.0 tomaba y la 3.1 descarta, sobre sus velas de H4.

    La ventana llega hasta donde la 3.0 **confirmaba**, que es el instante que
    hay que auditar: ahí es donde la fase anterior habría entrado y donde el
    propietario tiene que juzgar si echa de menos la entrada o no.
    """
    zoned = by_id.get(item.id_num)
    if zoned is None:
        return None
    figure, _first, _last, labels = _window(
        analysis,
        zoned,
        name=name,
        title=(
            f"H4 · ID {item.id_num} ({item.direction.value}) · "
            f"zona {item.zone.value} · LA 3.0 ENTRABA AQUÍ · LA 3.1 NO"
        ),
        subtitle=(
            f"vía de la 3.0: <b>{item.via_v30.value}</b> · "
            f"confirmaba {item.ts_confirmation_v30:%Y-%m-%d %H:%M} UTC · "
            f"contacto {item.ts_contact:%Y-%m-%d %H:%M} UTC · "
            f"desenlace de la zona {item.outcome.value} · "
            + (
                f"en la 3.1 muere en `{item.rail_v31.value}`"
                if item.rail_v31 is not None
                else "en la 3.1 la observación sigue viva sin confirmar"
            )
        ),
        from_index=item.index_contact,
        until=item.ts_confirmation_v30,
    )
    _vertical(figure, labels, analysis, item.ts_contact, "contacto", theme.INK_MUTED)
    _vertical(
        figure,
        labels,
        analysis,
        item.ts_confirmation_v30,
        f"la 3.0 confirmaba ({item.via_v30.value})",
        STOP_COLOUR,
    )
    path = folder / f"{name}_{item.ts_confirmation_v30:%Y%m%d_%H%M}.png"
    figure.write_image(str(path), width=WIDTH, height=HEIGHT, scale=SCALE)
    return path


def _write_archetypes(
    analysis: TimeframeAnalysis,
    by_id: dict[int, ImpulseZones],
    cascade: CascadeRun,
    execution: ExecutionRun,
    folder: Path,
) -> list[Path | None]:
    """Los cinco del §9. **Los ausentes no se sustituyen**: se dejan sin imagen."""
    written: list[Path | None] = []
    for item in archetypes.audit(cascade, execution):
        if item.trade is not None:
            written.append(_write_trade(analysis, by_id, item.trade, folder, item.capture))
        elif item.discarded is not None:
            written.append(
                _write_discarded(analysis, by_id, item.discarded, folder, item.capture)
            )
    return written


def _window(
    analysis: TimeframeAnalysis,
    zoned: ImpulseZones,
    *,
    name: str,
    title: str,
    subtitle: str,
    from_index: int,
    until: datetime,
) -> tuple[go.Figure, int, int, list[str]]:
    """La ventana de H4 de una operación, con sus zonas ya dibujadas."""
    index = pd.DatetimeIndex(analysis.bars.index)
    last_index = len(index) - 1
    end = int(index.searchsorted(pd.Timestamp(until), side="right")) - 1
    end = max(min(end, last_index), from_index)
    first = min(from_index, zoned.last.index_defining, zoned.index_constitution)
    if zoned.order_block is not None:
        first = min(first, zoned.order_block.index_defining)

    request = CaptureRequest(
        name=name,
        timeframe=analysis.timeframe,
        first=first,
        last=end,
        title=title,
        subtitle=subtitle,
        highlight=(zoned.id_num,),
        context=CONTEXT_BARS,
    )
    figure = window_figure(analysis, request)
    window_first = max(0, first - CONTEXT_BARS)
    window_last = min(last_index, end + CONTEXT_BARS)
    labels = bar_labels(index[window_first : window_last + 1])
    draw_zones(figure, analysis, zoned, window_first, window_last, labels)
    return figure, window_first, window_last, labels


def _levels(figure: go.Figure, trade: Trade, labels: Sequence[str]) -> None:
    """Entrada, stop y objetivo como tres líneas horizontales rotuladas.

    Los rótulos van **separados en vertical** aunque los precios estén pegados:
    con un 1R de medio dólar las tres líneas caben en dos píxeles y los textos se
    montan unos encima de otros, que es la forma más rápida de que una captura de
    auditoría deje de servir para auditar.
    """
    for row, (price, text, colour, dash) in enumerate((
        (trade.target_price, "objetivo (3,3 R)", TARGET_COLOUR, "dash"),
        (trade.entry_price, "entrada", theme.INK_PRIMARY, "solid"),
        (trade.stop_price, "STOP (1R)", STOP_COLOUR, "dash"),
    )):
        figure.add_shape(
            type="line",
            x0=labels[0],
            x1=labels[-1],
            y0=price,
            y1=price,
            line={"color": colour, "width": 1.4, "dash": dash},
            layer="below",
        )
        figure.add_annotation(
            x=labels[-1],
            y=price,
            text=f"<b>{text}</b> {price:,.{DECIMALS}f}",
            showarrow=False,
            xanchor="right",
            yanchor="middle",
            # Los tres rótulos ocupan bandas fijas del alto del gráfico en vez de
            # seguir a su línea: la distancia entre ellos deja de depender del 1R.
            yshift=_LABEL_ROW_HEIGHT * (1 - row),
            bgcolor=theme.SURFACE,
            font={"size": 11, "color": colour},
        )


def _milestones(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    trade: Trade,
    first: int,
    labels: Sequence[str],
) -> None:
    """Contacto, confirmación, entrada y salida, cada uno en su vela de H4."""
    observation = trade.signal.observation
    marks = [
        (observation.ts_contact, "contacto", theme.INK_MUTED),
        (trade.signal.confirmation.timestamp, "confirma H1", BULLISH),
        (trade.ts_entry, "ENTRA", theme.INK_PRIMARY),
    ]
    if observation.ts_retest is not None:
        marks.insert(1, (observation.ts_retest, "retesteo", theme.SERIES[3]))
    if observation.ts_break is not None:
        marks.insert(1, (observation.ts_break, "rompe la zona", BEARISH))
    if trade.ts_exit is not None:
        marks.append(
            (
                trade.ts_exit,
                trade.outcome.value.upper(),
                TARGET_COLOUR if trade.outcome.is_win else STOP_COLOUR,
            )
        )
    # Los hitos de una operación caen casi siempre en la misma vela de H4 o en la
    # de al lado —la cascada entera puede resolverse en cuatro horas— así que los
    # rótulos se apilan por vela en vez de escribirse todos a la misma altura.
    rows: dict[int, int] = {}
    for stamp, text, colour in marks:
        position = _bar_of(analysis, stamp) - first
        # Se mira también a las velas vecinas: los rótulos van centrados sobre su
        # vela y son más anchos que ella, así que dos hitos en velas contiguas se
        # pisan igual que dos en la misma.
        row = max(rows.get(position + offset, -1) for offset in (-1, 0, 1)) + 1
        rows[position] = row
        _vertical(figure, labels, analysis, stamp, text, colour, first=first, row=row)


def _vertical(
    figure: go.Figure,
    labels: Sequence[str],
    analysis: TimeframeAnalysis,
    stamp: datetime,
    text: str,
    colour: str,
    *,
    first: int = 0,
    row: int = 0,
) -> None:
    """Una marca vertical sobre la vela de H4 que contiene ese instante.

    Se busca la vela **que ya había abierto** en ese instante: los hitos de la
    cascada caen en cierres de H1 o de M15, que están dentro de una vela de H4 y
    casi nunca en su borde. `row` apila los rótulos que caen en la misma vela.
    """
    offset = _bar_of(analysis, stamp) - first
    if offset < 0 or offset >= len(labels):
        return
    figure.add_vline(
        x=labels[offset],
        line={"color": colour, "width": 1, "dash": "dot"},
    )
    figure.add_annotation(
        x=labels[offset],
        y=1.0,
        yref="paper",
        text=f"<b>{text}</b>",
        showarrow=False,
        yanchor="bottom",
        yshift=_LABEL_ROW_HEIGHT * row,
        bgcolor=theme.SURFACE,
        font={"size": 10, "color": colour},
    )


def _bar_of(analysis: TimeframeAnalysis, stamp: datetime) -> int:
    """Vela de la temporalidad que ya había abierto en ese instante."""
    index = pd.DatetimeIndex(analysis.bars.index)
    return int(index.searchsorted(pd.Timestamp(stamp), side="right")) - 1


def _trade_subtitle(trade: Trade) -> str:
    observation = trade.signal.observation
    return (
        f"contacto {observation.ts_contact:%Y-%m-%d %H:%M} · "
        f"confirma {trade.signal.confirmation.timestamp:%Y-%m-%d %H:%M} "
        f"({trade.signal.confirmation.kind.value}) · "
        f"entra {trade.ts_entry:%Y-%m-%d %H:%M} al open de M1 en "
        f"{trade.entry_price:,.{DECIMALS}f}<br>"
        f"entrada en {trade.entry_timeframe.value} · stop en {trade.stop_zone.value} "
        f"({trade.stop_price:,.{DECIMALS}f}) · objetivo {trade.target_price:,.{DECIMALS}f} · "
        f"1R = {trade.risk_usd:,.2f} USD = {trade.risk_atr:.2f} ATR = "
        f"{trade.risk_pct_price:.3%} del precio<br>"
        f"contexto diario {observation.daily.value} · "
        f"desenlace <b>{trade.outcome.value}</b> · "
        f"bruto {trade.gross_r:+.2f} R · neto {trade.net_r:+.2f} R "
        f"(coste {trade.cost_r:.3f} R)"
    )


__all__ = ["LOST", "PER_VIA", "EntryCapture", "write_entry_captures"]
