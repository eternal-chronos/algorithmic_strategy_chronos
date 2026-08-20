"""Capturas exportadas del módulo 1 (F.4).

El explorador HTML sirve para navegar; estas imágenes sirven para adjuntar. Son
los casos concretos que el propietario ha pedido ver de un vistazo:

  · los 20 ID más pequeños por rango en ATR, en Diario y en H4;
  · los 10 que mejor cumplen la firma de lateralización, en Diario y en H4;
  · los 10 latigazos más severos;
  · un tramo con las temporalidades detectadas alineadas;
  · diciembre de 2025 en Diario con la capa de contactos encendida.

Cada imagen lleva sobreimpreso lo que hace falta para juzgarla sin abrir ningún
CSV: número de ID, dirección, rango en USD y en ATR, duración y tipo de rotura.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.lateralization import (
    LateralizationStudy,
    TimeframeLateralization,
)
from chronos.application.structure.statistics import ImpulseStatistics, longest_aligned_stretch
from chronos.domain.structure.enums import ContactKind
from chronos.domain.structure.impulse import DominantImpulse
from chronos.infrastructure.reporting import theme

#: Barras de contexto a cada lado del tramo que se está enseñando.
CONTEXT_BARS = 25

#: Cuántas capturas de cada tipo pide F.4.
SMALLEST = 20
BEST_SIGNATURE = 10
WORST_WHIPSAWS = 10

#: Temporalidades que llevan capturas de ID pequeños y de firma.
CAPTURE_TIMEFRAMES = ("D", "H4")

WIDTH, HEIGHT, SCALE = 1600, 900, 1

BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Una imagen por producir: qué temporalidad, qué ventana y qué contar."""

    name: str
    timeframe: str
    first: int
    last: int
    title: str
    subtitle: str
    highlight: tuple[int, ...] = ()
    #: Barras de contexto a cada lado. Un ID de dos velas no se juzga sin ellas;
    #: un tramo de dos meses ya trae su propio contexto dentro.
    context: int = CONTEXT_BARS


def write_captures(
    run: ImpulseRun,
    statistics: ImpulseStatistics,
    lateralization: LateralizationStudy,
    folder: Path,
) -> list[Path]:
    """Escribe todas las capturas de F.4 y devuelve las rutas producidas."""
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for request in _requests(run, statistics, lateralization):
        analysis = run.analyses.get(request.timeframe)
        if analysis is None:
            continue
        measurement = lateralization.per_timeframe.get(request.timeframe)
        figure = _figure(analysis, measurement, request)
        path = folder / f"{request.name}.png"
        figure.write_image(str(path), width=WIDTH, height=HEIGHT, scale=SCALE)
        written.append(path)
    return written


# --- Qué capturar -----------------------------------------------------------


def window_figure(
    analysis: TimeframeAnalysis,
    request: CaptureRequest,
    *,
    measurement: TimeframeLateralization | None = None,
    y_range: tuple[float, float] | None = None,
) -> go.Figure:
    """Una captura suelta, fuera del lote de F.4.

    `y_range` fuerza la misma escala vertical en varias imágenes: comparar dos
    cortes de sesión con escalas distintas no compara nada.
    """
    figure = _figure(analysis, measurement, request)
    if y_range is not None:
        figure.update_yaxes(range=list(y_range))
    return figure


def id_card_figure(
    analysis: TimeframeAnalysis,
    impulse: DominantImpulse,
    *,
    context: int = CONTEXT_BARS,
    subtitle: str = "",
) -> go.Figure:
    """Ficha de un ID concreto: su vida entera y las velas que lo definen.

    Sobre la captura normal se añade lo que no se puede leer de una línea
    horizontal: **qué vela** fija el ancla y cuál el extremo, con su color. Es la
    comprobación de la regla del propietario —ancla en vela contraria (R-02),
    extremo en vela del color del impulso (R-36)— hecha sobre el gráfico.
    """
    last_index = len(analysis.bars) - 1
    end = impulse.index_end if impulse.index_end is not None else last_index
    request = CaptureRequest(
        name=f"id{impulse.id_num}",
        timeframe=analysis.timeframe,
        first=impulse.index_constitution,
        last=end,
        title=(
            f"{analysis.timeframe} · ID {impulse.id_num} ({impulse.direction.value}) · "
            f"constituido {impulse.ts_constitution:%Y-%m-%d}"
        ),
        subtitle=subtitle,
        highlight=(impulse.id_num,),
        context=context,
    )
    figure = _figure(analysis, None, request)

    first = max(0, request.first - context)
    last = min(last_index, request.last + context)
    labels = bar_labels(pd.DatetimeIndex(analysis.bars.index[first : last + 1]))
    colour = BULLISH if impulse.direction.value == "alcista" else BEARISH
    for position, level, name in (
        (impulse.index_anchor, impulse.anchor, "ancla"),
        (impulse.index_extreme, impulse.extreme, "extremo"),
    ):
        if not first <= position <= last:
            continue
        bar = analysis.bars.iloc[position]
        body = float(bar["close"]) - float(bar["open"])
        tone = "verde" if body > 0 else ("roja" if body < 0 else "doji")
        # El nivel ya estaba en el precio antes de que el ID naciera: se prolonga
        # atenuado desde la vela que lo fija hasta la constitución. Sin esto, un
        # ID de una sola barra dibuja dos líneas de un centímetro.
        figure.add_trace(
            go.Scatter(
                x=[labels[position - first], labels[impulse.index_constitution - first]],
                y=[level, level],
                mode="lines",
                line={"color": colour, "width": 1.4, "dash": "dash"},
                opacity=0.5,
                hoverinfo="skip",
                showlegend=False,
            )
        )
        figure.add_annotation(
            x=labels[position - first],
            y=level,
            text=(
                f"<b>{name} {level:,.4f}</b><br>vela "
                f"{analysis.bars.index[position]:%Y-%m-%d} · {tone}"
            ),
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1.2,
            arrowcolor=colour,
            ax=0,
            ay=-58 if name == "extremo" else 58,
            align="left",
            bordercolor=colour,
            borderwidth=1,
            borderpad=4,
            bgcolor=theme.SURFACE,
            font={"size": 11, "color": colour},
        )
    return figure


def _requests(
    run: ImpulseRun, statistics: ImpulseStatistics, lateralization: LateralizationStudy
) -> list[CaptureRequest]:
    requests: list[CaptureRequest] = []
    for timeframe in CAPTURE_TIMEFRAMES:
        analysis = run.analyses.get(timeframe)
        measurement = lateralization.per_timeframe.get(timeframe)
        if analysis is None or measurement is None:
            continue
        requests += _smallest_requests(analysis, measurement)
        requests += _signature_requests(analysis, measurement)
    requests += _whipsaw_requests(run, statistics)
    requests += _aligned_requests(run)
    requests += _december_requests(run)
    return requests


@dataclass(frozen=True, slots=True)
class _Row:
    """Una fila de la tabla de impulsos, ya tipada.

    La tabla es un `DataFrame` y sus columnas son objetos: leerlas por atributo
    esparce conversiones por todo el módulo. Se convierte una vez, aquí.
    """

    id_num: int
    direction: str
    first: int
    last: int
    range_usd: float
    range_atr: float
    bars_alive: int
    exit_break: str | None


def _rows(table: pd.DataFrame) -> list[_Row]:
    if table.empty:
        return []
    ends = table["indice_fin"].to_numpy()
    starts = table["indice_constitucion"].to_numpy(dtype=int)
    exits = table["tipo_rotura_salida"].tolist()
    return [
        _Row(
            id_num=int(id_num),
            direction=str(direction),
            first=int(starts[position]),
            # Un ID que sigue vigente no tiene barra de fin: se enseña su
            # constitución y el contexto se encarga del resto.
            last=int(ends[position]) if pd.notna(ends[position]) else int(starts[position]),
            range_usd=float(range_usd),
            range_atr=float(range_atr),
            bars_alive=int(bars),
            exit_break=None if pd.isna(exits[position]) else str(exits[position]),
        )
        for position, (id_num, direction, range_usd, range_atr, bars) in enumerate(
            zip(
                table["id_num"].to_numpy(dtype=int),
                table["direccion"].tolist(),
                table["rango_usd"].to_numpy(dtype=float),
                table["rango_atr"].to_numpy(dtype=float),
                table["n_barras_id"].to_numpy(dtype=int),
                strict=True,
            )
        )
    ]


def _smallest_requests(
    analysis: TimeframeAnalysis, measurement: TimeframeLateralization
) -> list[CaptureRequest]:
    rows = [row for row in _rows(analysis.table) if np.isfinite(row.range_atr)]
    ordered = sorted(rows, key=lambda row: row.range_atr)[:SMALLEST]
    return [
        CaptureRequest(
            name=f"pequenos_{analysis.timeframe}_{position:02d}_id{row.id_num}",
            timeframe=analysis.timeframe,
            first=row.first,
            last=row.last,
            title=f"{analysis.timeframe} · ID {row.id_num} · rango {row.range_atr:.3f} ATR",
            subtitle=_impulse_caption(row, measurement),
            highlight=(row.id_num,),
        )
        for position, row in enumerate(ordered, start=1)
    ]


def _signature_requests(
    analysis: TimeframeAnalysis, measurement: TimeframeLateralization
) -> list[CaptureRequest]:
    best = sorted(
        measurement.with_signature, key=lambda item: item.total_touches, reverse=True
    )[:BEST_SIGNATURE]
    rows = {row.id_num: row for row in _rows(analysis.table)}
    requests = []
    for position, item in enumerate(best, start=1):
        row = rows.get(item.id_num)
        if row is None:
            continue
        requests.append(
            CaptureRequest(
                name=f"firma_{analysis.timeframe}_{position:02d}_id{item.id_num}",
                timeframe=analysis.timeframe,
                first=row.first,
                last=row.last,
                title=(
                    f"{analysis.timeframe} · ID {item.id_num} · firma de lateralización "
                    f"({item.touches_upper} arriba / {item.touches_lower} abajo)"
                ),
                subtitle=_impulse_caption(row, measurement),
                highlight=(item.id_num,),
            )
        )
    return requests


def _whipsaw_requests(
    run: ImpulseRun, statistics: ImpulseStatistics
) -> list[CaptureRequest]:
    """Los latigazos más severos: los que se deshacen en menos barras."""
    requests: list[CaptureRequest] = []
    for timeframe, stats in statistics.per_timeframe.items():
        episodes = stats.whipsaw_episodes
        if episodes.empty:
            continue
        rows = {row.id_num: row for row in _rows(run.analyses[timeframe].table)}
        worst = episodes.nsmallest(WORST_WHIPSAWS, "barras")
        going = worst["id_ida"].to_numpy(dtype=int)
        back = worst["id_vuelta"].to_numpy(dtype=int)
        bars = worst["barras"].to_numpy(dtype=int)
        directions = worst["direccion_original"].tolist()

        for position in range(len(worst)):
            first_row, last_row = rows.get(int(going[position])), rows.get(int(back[position]))
            if first_row is None or last_row is None:
                continue
            requests.append(
                CaptureRequest(
                    name=f"latigazo_{timeframe}_{position + 1:02d}_id{going[position]}",
                    timeframe=timeframe,
                    first=first_row.first,
                    last=last_row.first,
                    title=(
                        f"{timeframe} · latigazo de {bars[position]} barras · "
                        f"ID {going[position]} → {back[position]}"
                    ),
                    subtitle=(
                        f"El sesgo era {directions[position]}, se invirtió con el ID "
                        f"{going[position]} y volvió con el {back[position]} "
                        f"{bars[position]} barras después."
                    ),
                    highlight=(int(going[position]), int(back[position])),
                )
            )
        # Un solo bloque de latigazos, el de la temporalidad más gruesa que los
        # tenga: diez por cada una serían treinta imágenes de lo mismo.
        if requests:
            break
    return requests


def _aligned_requests(run: ImpulseRun) -> list[CaptureRequest]:
    stretch = longest_aligned_stretch(run)
    if stretch is None:
        return []
    start, end = stretch
    requests = []
    for timeframe, analysis in run.analyses.items():
        window = _window_of(analysis, start, end)
        if window is None:
            continue
        first, last = window
        requests.append(
            CaptureRequest(
                name=f"alineadas_{timeframe}",
                timeframe=timeframe,
                first=first,
                last=last,
                title=f"{timeframe} · tramo con las temporalidades detectadas alineadas",
                context=CONTEXT_BARS if timeframe == "D" else 0,
                subtitle=(
                    f"Tramo más largo del histórico con Diario, H4 y H1 en la misma "
                    f"dirección: {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC."
                ),
            )
        )
    return requests


def _december_requests(run: ImpulseRun) -> list[CaptureRequest]:
    """Diciembre de 2025 en Diario, con los contactos encendidos (D.6)."""
    analysis = run.analyses.get("D")
    if analysis is None:
        return []
    window = _window_of(
        analysis,
        pd.Timestamp("2025-11-25", tz="UTC"),
        pd.Timestamp("2025-12-31 23:59", tz="UTC"),
    )
    if window is None:
        return []
    first, last = window
    return [
        CaptureRequest(
            name="diciembre_2025_D",
            timeframe="D",
            first=first,
            last=last,
            title="Diario · diciembre de 2025 con la capa de contactos",
            context=3,
            subtitle=(
                "La ficha de D.6: los ID 471 a 475, sus contactos con los límites y sus "
                "roturas."
            ),
        )
    ]


def _window_of(
    analysis: TimeframeAnalysis, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[int, int] | None:
    index = pd.DatetimeIndex(analysis.bars.index)
    inside = np.flatnonzero((index >= start) & (index <= end))
    if inside.size == 0:
        return None
    return int(inside[0]), int(inside[-1])


def _bars(count: int) -> str:
    return "1 barra" if count == 1 else f"{count} barras"


def _impulse_caption(row: _Row, measurement: TimeframeLateralization) -> str:
    """Lo que hace falta para juzgar un ID sin abrir ningún CSV."""
    contacts = {item.id_num: item for item in measurement.impulses}
    item = contacts.get(row.id_num)
    touches = (
        f" · contactos {item.touches_upper} arriba / {item.touches_lower} abajo"
        if item is not None
        else ""
    )
    return (
        f"ID {row.id_num} ({row.direction}) · rango {row.range_usd:,.2f} USD "
        f"= {row.range_atr:.3f} ATR · {_bars(row.bars_alive)} vigente · "
        f"salida {row.exit_break or 'sin romper'}{touches}"
    )


# --- Dibujo -----------------------------------------------------------------


def _figure(
    analysis: TimeframeAnalysis,
    measurement: TimeframeLateralization | None,
    request: CaptureRequest,
) -> go.Figure:
    bars = analysis.bars
    first = max(0, request.first - request.context)
    last = min(len(bars) - 1, request.last + request.context)
    window = bars.iloc[first : last + 1]
    # Eje de categorías con una etiqueta por vela: así el fin de semana no abre
    # un hueco vacío y la captura se parece a la pantalla del propietario. Todas
    # las trazas tienen que usar exactamente estas etiquetas o caerían fuera.
    labels = bar_labels(pd.DatetimeIndex(window.index))

    figure = go.Figure(
        go.Candlestick(
            x=labels,
            open=window["open"],
            high=window["high"],
            low=window["low"],
            close=window["close"],
            increasing={"line": {"color": BULLISH, "width": 1}, "fillcolor": BULLISH},
            decreasing={"line": {"color": BEARISH, "width": 1}, "fillcolor": BEARISH},
            name=analysis.timeframe,
            showlegend=False,
        )
    )
    _draw_impulses(figure, analysis, first, last, labels, request.highlight)
    if measurement is not None:
        _draw_contacts(figure, measurement, analysis, first, last, labels)

    figure.update_layout(
        title={
            "text": f"{request.title}<br><sub>{request.subtitle}</sub>",
            "font": {"size": 17},
        },
        template="plotly_white",
        paper_bgcolor=theme.SURFACE,
        plot_bgcolor=theme.SURFACE,
        font={"family": theme.FONT_FAMILY, "size": 12, "color": theme.INK_PRIMARY},
        margin={"l": 70, "r": 24, "t": 84, "b": 48},
        xaxis={
            "rangeslider": {"visible": False},
            "gridcolor": theme.GRIDLINE,
            "title": {"text": "UTC"},
            # Sin huecos de fin de semana: la captura tiene que parecerse a la
            # pantalla del propietario, no a un calendario.
            "type": "category",
            "tickangle": -45,
            "nticks": 18,
        },
        yaxis={"gridcolor": theme.GRIDLINE, "tickformat": ".2f"},
        showlegend=False,
    )
    return figure


def bar_labels(index: pd.DatetimeIndex) -> list[str]:
    """Etiquetas del eje de categorías: una por vela, en UTC.

    Todas las trazas de una captura tienen que usar exactamente estas cadenas
    o caerían fuera del eje. Es pública porque las capturas de la fase 2.0
    dibujan sus zonas sobre las mismas velas y necesitan las mismas etiquetas.
    """
    return [stamp.strftime("%Y-%m-%d %H:%M") for stamp in index]


def _draw_impulses(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    first: int,
    last: int,
    labels: list[str],
    highlight: tuple[int, ...],
) -> None:
    for impulse in analysis.impulses:
        if not impulse.publishable:
            continue
        start = impulse.index_constitution
        end = impulse.index_end if impulse.index_end is not None else last
        if end < first or start > last:
            continue
        left = labels[max(start, first) - first]
        right = labels[min(end, last) - first]
        colour = BULLISH if impulse.direction.value == "alcista" else BEARISH
        strong = not highlight or impulse.id_num in highlight
        middle = (impulse.anchor + impulse.extreme) / 2.0

        for level, dash, width in (
            (impulse.anchor, "solid", 2.2),
            (impulse.extreme, "solid", 2.2),
            (middle, "dot", 1.0),
        ):
            figure.add_trace(
                go.Scatter(
                    x=[left, right],
                    y=[level, level],
                    mode="lines",
                    line={
                        "color": theme.INK_MUTED if dash == "dot" else colour,
                        "width": width if strong else 1.0,
                        "dash": dash,
                    },
                    opacity=1.0 if strong else 0.4,
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        if strong and first <= start <= last:
            figure.add_annotation(
                x=left,
                y=impulse.extreme,
                text=f"ID {impulse.id_num}",
                showarrow=False,
                yshift=12 if impulse.direction.value == "alcista" else -12,
                font={"size": 11, "color": colour},
            )


def _draw_contacts(
    figure: go.Figure,
    measurement: TimeframeLateralization,
    analysis: TimeframeAnalysis,
    first: int,
    last: int,
    labels: list[str],
) -> None:
    frame = measurement.contacts
    if frame.empty:
        return
    index = pd.DatetimeIndex(analysis.bars.index)
    inside = frame[
        (pd.DatetimeIndex(frame["timestamp"]) >= index[first])
        & (pd.DatetimeIndex(frame["timestamp"]) <= index[last])
    ]
    if inside.empty:
        return

    positions = {label: label for label in labels}
    for kind, symbol in (
        (ContactKind.TOQUE_MECHA, "circle-open"),
        (ContactKind.ROTURA_FALLIDA, "square-open"),
    ):
        subset = inside[inside["tipo_contacto"] == kind.value]
        if subset.empty:
            continue
        marks = bar_labels(pd.DatetimeIndex(subset["timestamp"]))
        figure.add_trace(
            go.Scatter(
                x=[positions[mark] for mark in marks if mark in positions],
                y=[
                    level
                    for mark, level in zip(marks, subset["nivel"], strict=True)
                    if mark in positions
                ],
                mode="markers",
                marker={"symbol": symbol, "size": 11, "color": theme.INK_PRIMARY},
                hoverinfo="skip",
                showlegend=False,
            )
        )
