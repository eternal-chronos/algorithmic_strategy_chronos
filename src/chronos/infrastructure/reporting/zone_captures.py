"""Capturas de la fase 2.0 (§8): las zonas UL y OB sobre las velas.

Cinco lotes, los que pidió el propietario para auditar de un vistazo:

  1. diez ID alcistas y diez bajistas con las dos zonas bien formadas;
  2. los diez UL que se estiraron a la vela siguiente;
  3. los diez ID que murieron sin que su OB llegara a confirmarse;
  4. los cinco UL de altura cero, si los hay;
  5. los cinco OB más altos en ATR y los cinco más bajos.

Cada imagen lleva sobreimpreso lo que hace falta para juzgarla sin abrir ningún
CSV, y el `LEEME.txt` repite lo mismo en una línea por fichero.

El OB va con **borde discontinuo mientras no está confirmado** y sólido cuando
lo está. Aunque un OB sin confirmar no exista como zona, el propietario necesita
ver dónde estaba la vela candidata: por eso se dibuja, y por eso se distingue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, ZonesRun
from chronos.domain.structure.zones import Zone, ZoneKind
from chronos.infrastructure.reporting import theme
from chronos.infrastructure.reporting.impulse_captures import (
    CaptureRequest,
    bar_labels,
    window_figure,
)
from chronos.infrastructure.reporting.timezones import session_label

#: §8.1 pide ±20 barras de contexto.
CONTEXT_BARS = 20

WIDTH, HEIGHT, SCALE = 1600, 900, 1
DECIMALS = 4

BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE

#: Cuántas capturas de cada lote.
WELL_FORMED = 10
EXTENDED = 10
WITHOUT_ORDER_BLOCK = 10
FLAT = 5
EXTREME_ORDER_BLOCKS = 5

#: De dónde se sacan los casos, en este orden, hasta llenar la cuota. H4 primero
#: porque es la temporalidad que el propietario audita; las otras dos completan
#: el lote cuando H4 no tiene suficientes casos, y el `LEEME` dice cuál es cuál.
TIMEFRAME_ORDER = ("H4", "D", "H1")


@dataclass(frozen=True, slots=True)
class ZoneCapture:
    """Una imagen escrita, con lo que va en su línea del `LEEME.txt`."""

    path: Path
    lot: str
    timeframe: str
    id_num: int
    direction: str
    constituted: pd.Timestamp
    ul_usd: float
    ul_atr: float
    ob_usd: float | None
    ob_atr: float | None
    note: str


def write_zone_captures(
    run: ImpulseRun, zones: ZonesRun, folder: Path, *, session_timezone: str = "UTC"
) -> list[ZoneCapture]:
    """Escribe los cinco lotes del §8 y devuelve lo que hace falta para el LEEME."""
    if not zones.enabled:
        return []
    folder.mkdir(parents=True, exist_ok=True)

    written: list[ZoneCapture] = []
    for lot, picks in _lots(zones):
        for position, (timeframe, zoned) in enumerate(picks, start=1):
            analysis = run.analyses.get(timeframe)
            if analysis is None:
                continue
            written.append(
                _write_one(analysis, zoned, folder, lot, position, session_timezone)
            )
    _write_readme(written, folder)
    return written


# --- Qué capturar -----------------------------------------------------------

Pick = tuple[str, ImpulseZones]


def _lots(zones: ZonesRun) -> list[tuple[str, list[Pick]]]:
    return [
        ("bien_formados_alcista", _well_formed(zones, "alcista")),
        ("bien_formados_bajista", _well_formed(zones, "bajista")),
        ("ul_extendido", _extended(zones)),
        ("sin_ob", _without_order_block(zones)),
        ("ul_altura_cero", _flat(zones)),
        ("ob_mas_alto", _extreme_order_blocks(zones, biggest=True)),
        ("ob_mas_bajo", _extreme_order_blocks(zones, biggest=False)),
    ]


def _candidates(zones: ZonesRun) -> list[Pick]:
    """Todos los ID con zonas, con H4 delante."""
    ordered: list[Pick] = []
    for timeframe in TIMEFRAME_ORDER:
        item = zones.per_timeframe.get(timeframe)
        if item is None:
            continue
        ordered += [(timeframe, zoned) for zoned in item.items]
    return ordered


def _well_formed(zones: ZonesRun, direction: str) -> list[Pick]:
    """§8.1 — ID con las dos zonas presentes y el UL con mecha de verdad.

    "Bien formado" no es "el más grande": se eligen los que quedan **más cerca de
    la mediana** de altura del UL en ATR, para que el propietario audite casos
    representativos y no los diez más espectaculares del histórico.
    """
    pool = [
        (timeframe, zoned)
        for timeframe, zoned in _candidates(zones)
        if timeframe == "H4"
        and zoned.direction.value == direction
        and zoned.has_order_block
        and not zoned.last.is_flat
        and pd.notna(zoned.atr)
        and zoned.atr > 0
    ]
    if not pool:
        return []
    heights = pd.Series([zoned.last.height / zoned.atr for _, zoned in pool])
    median = float(heights.median())
    ordered = sorted(pool, key=lambda pick: abs(pick[1].last.height / pick[1].atr - median))
    return ordered[:WELL_FORMED]


def _extended(zones: ZonesRun) -> list[Pick]:
    """§8.2 — los UL que se estiraron, los más altos primero.

    En ATR y no en dólares, como todo lo demás de esta fase: ordenar por dólares
    pondría el diario delante siempre —sus velas son más grandes— y el lote
    dejaría de enseñar los casos más llamativos para enseñar la temporalidad más
    gruesa.
    """
    pool = [
        (timeframe, zoned)
        for timeframe, zoned in _candidates(zones)
        if zoned.last.extended and pd.notna(zoned.atr) and zoned.atr > 0
    ]
    ordered = sorted(pool, key=lambda pick: -pick[1].last.height / pick[1].atr)
    return ordered[:EXTENDED]


def _without_order_block(zones: ZonesRun) -> list[Pick]:
    """§8.3 — los ID que murieron sin OB confirmado. Son la cifra de 7.1."""
    pool = [
        (timeframe, zoned)
        for timeframe, zoned in _candidates(zones)
        if not zoned.has_order_block
    ]
    return pool[:WITHOUT_ORDER_BLOCK]


def _flat(zones: ZonesRun) -> list[Pick]:
    """§8.4 — los UL de altura cero, «si los hay»."""
    pool = [
        (timeframe, zoned) for timeframe, zoned in _candidates(zones) if zoned.last.is_flat
    ]
    return pool[:FLAT]


def _extreme_order_blocks(zones: ZonesRun, *, biggest: bool) -> list[Pick]:
    """§8.5 — los OB más altos y los más bajos, medidos en ATR."""
    pool = [
        (timeframe, zoned)
        for timeframe, zoned in _candidates(zones)
        if zoned.has_order_block and pd.notna(zoned.atr) and zoned.atr > 0
    ]
    if not pool:
        return []
    ordered = sorted(
        pool,
        key=lambda pick: pick[1].order_block.height / pick[1].atr,  # type: ignore[union-attr]
        reverse=biggest,
    )
    return ordered[:EXTREME_ORDER_BLOCKS]


# --- Dibujo -----------------------------------------------------------------


def _write_one(
    analysis: TimeframeAnalysis,
    zoned: ImpulseZones,
    folder: Path,
    lot: str,
    position: int,
    session_timezone: str,
) -> ZoneCapture:
    figure, note = _figure(analysis, zoned, session_timezone)
    stamp = pd.Timestamp(zoned.ts_constitution)
    name = f"{lot}_{position:02d}_{analysis.timeframe}_id{zoned.id_num}_{stamp:%Y%m%d}"
    path = folder / f"{name}.png"
    figure.write_image(str(path), width=WIDTH, height=HEIGHT, scale=SCALE)

    block = zoned.order_block
    return ZoneCapture(
        path=path,
        lot=lot,
        timeframe=analysis.timeframe,
        id_num=zoned.id_num,
        direction=zoned.direction.value,
        constituted=stamp,
        ul_usd=zoned.last.height,
        ul_atr=_ratio(zoned.last.height, zoned.atr),
        ob_usd=None if block is None else block.height,
        ob_atr=None if block is None else _ratio(block.height, zoned.atr),
        note=note,
    )


def _figure(
    analysis: TimeframeAnalysis, zoned: ImpulseZones, session_timezone: str
) -> tuple[go.Figure, str]:
    last_index = len(analysis.bars) - 1
    end = zoned.index_end if zoned.index_end is not None else last_index
    # La ventana arranca en la vela más antigua que define una zona: sin ella la
    # captura enseñaría un rectángulo que empieza fuera del encuadre. En los ID
    # sin OB confirmado la vela que hay que encajar es la del **ancla**, que es
    # justo la que el lote de §8.3 va a enseñar.
    first = min(zoned.index_constitution, zoned.last.index_defining)
    if zoned.order_block is not None:
        first = min(first, zoned.order_block.index_defining)
    else:
        anchor = _anchor_index(analysis, zoned)
        if anchor is not None:
            first = min(first, anchor)

    request = CaptureRequest(
        name=f"zonas_id{zoned.id_num}",
        timeframe=analysis.timeframe,
        first=first,
        last=end,
        title=(
            f"{analysis.timeframe} · ID {zoned.id_num} ({zoned.direction.value}) · "
            f"zonas UL y OB · constituido {zoned.ts_constitution:%Y-%m-%d %H:%M} UTC"
        ),
        subtitle=_subtitle(zoned, session_timezone),
        highlight=(zoned.id_num,),
        context=CONTEXT_BARS,
    )
    figure = window_figure(analysis, request)

    window_first = max(0, request.first - request.context)
    window_last = min(last_index, request.last + request.context)
    labels = bar_labels(pd.DatetimeIndex(analysis.bars.index[window_first : window_last + 1]))
    note = draw_zones(figure, analysis, zoned, window_first, window_last, labels)
    return figure, note


def draw_zones(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    zoned: ImpulseZones,
    first: int,
    last: int,
    labels: Sequence[str],
) -> str:
    """Dibuja el UL y el OB de un ID sobre una captura ya montada.

    Pública porque las capturas de la fase 2.1 dibujan exactamente las mismas dos
    zonas: son las que ahora deciden la rotura, y enseñarlas con otro trazo
    obligaría al propietario a comparar dos dibujos distintos de lo mismo.
    """
    colour = BULLISH if zoned.direction.value == "alcista" else BEARISH
    notes: list[str] = []

    ul = zoned.last
    _rectangle(figure, ul, zoned, first, last, labels, colour, dashed=False)
    _annotate(
        figure,
        labels,
        ul.index_defining - first,
        ul.outer,
        f"<b>UL {ul.inner:,.{DECIMALS}f} → {ul.outer:,.{DECIMALS}f}</b>"
        + ("<br>extendida a la vela siguiente" if ul.extended else "")
        + ("<br>ALTURA CERO: la vela no dejó mecha" if ul.is_flat else ""),
        colour,
        above=zoned.direction.value == "alcista",
    )
    if ul.extended:
        notes.append("UL extendido a la vela siguiente")
    if ul.is_flat:
        notes.append("UL de altura cero")

    block = zoned.order_block
    if block is not None:
        _rectangle(figure, block, zoned, first, last, labels, colour, dashed=False)
        _annotate(
            figure,
            labels,
            block.index_defining - first,
            block.outer,
            f"<b>OB {block.low:,.{DECIMALS}f} → {block.high:,.{DECIMALS}f}</b>"
            f"<br>confirmado {block.ts_confirmation:%Y-%m-%d %H:%M} UTC",
            colour,
            above=zoned.direction.value != "alcista",
        )
        _confirmation_marker(figure, analysis, block, first, last, labels)
    else:
        # Sin confirmar el OB no existe como zona, pero el propietario necesita
        # ver dónde estaba la vela candidata: se dibuja con borde discontinuo.
        _candidate_rectangle(figure, analysis, zoned, first, last, labels)
        notes.append("murió sin OB confirmado (la vela candidata va punteada)")

    return " · ".join(notes) if notes else "UL y OB bien formados"


def _birth_index(zoned: ImpulseZones, zone: Zone) -> int:
    """Barra en la que la zona empieza a existir.

    El UL nace con la constitución del ID; el OB, cuando además se confirma. La
    vela que define la zona es anterior a las dos, y ese tramo NO es zona.
    """
    if zone.kind is ZoneKind.LAST or zone.index_confirmation is None:
        return zoned.index_constitution
    return max(zoned.index_constitution, zone.index_confirmation)


def _rectangle(
    figure: go.Figure,
    zone: Zone,
    zoned: ImpulseZones,
    first: int,
    last: int,
    labels: Sequence[str],
    colour: str,
    *,
    dashed: bool,
) -> None:
    """Dos tramos, igual que las líneas del ID en B.1 y que el explorador.

    Relleno sólido desde que la zona **nace** hasta que muere el ID —que es
    exactamente cuando existe— y contorno atenuado desde la vela que la define
    hasta ese nacimiento. Sin esa distinción el dibujo diría que la zona estaba
    ahí antes de tiempo, que es justo lo contrario de lo que ocurre.
    """
    end = min(zoned.index_end if zoned.index_end is not None else last, last)
    birth = _birth_index(zoned, zone)
    defining = zone.index_defining

    if birth > defining:
        _band(
            figure, zone, colour,
            labels, first, max(defining, first), min(birth, last),
            fill=False, dashed=True,
        )
    _band(
        figure, zone, colour,
        labels, first, max(birth, first), end,
        fill=True, dashed=dashed,
    )


def _band(
    figure: go.Figure,
    zone: Zone,
    colour: str,
    labels: Sequence[str],
    first: int,
    start: int,
    end: int,
    *,
    fill: bool,
    dashed: bool,
) -> None:
    if end < start or not 0 <= start - first < len(labels):
        return
    end = min(end, first + len(labels) - 1)
    # Una zona plana no tiene rectángulo que dibujar: se traza como línea, que es
    # exactamente lo que es.
    if zone.is_flat:
        figure.add_shape(
            type="line",
            x0=labels[start - first],
            x1=labels[end - first],
            y0=zone.inner,
            y1=zone.inner,
            line={
                "color": colour,
                "width": 2.4 if fill else 1.2,
                "dash": "solid" if fill else "dot",
            },
            opacity=1.0 if fill else 0.4,
            layer="above",
        )
        return
    figure.add_shape(
        type="rect",
        x0=labels[start - first],
        x1=labels[end - first],
        y0=zone.low,
        y1=zone.high,
        fillcolor=colour if fill else "rgba(0,0,0,0)",
        opacity=(0.18 if zone.kind is ZoneKind.ORDER_BLOCK else 0.28) if fill else 0.4,
        line={
            "color": colour,
            "width": 1.4 if fill else 1.0,
            "dash": ("dash" if dashed else "solid") if fill else "dot",
        },
        layer="below",
    )


def _candidate_rectangle(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    zoned: ImpulseZones,
    first: int,
    last: int,
    labels: Sequence[str],
) -> None:
    """La vela del ancla de un ID sin OB: dónde *habría* estado la zona."""
    index = _anchor_index(analysis, zoned)
    if index is None or not first <= index <= last:
        return
    end = min(zoned.index_end if zoned.index_end is not None else last, last)
    bar = analysis.bars.iloc[index]
    figure.add_shape(
        type="rect",
        x0=labels[index - first],
        x1=labels[max(end, index) - first],
        y0=float(bar["low"]),
        y1=float(bar["high"]),
        fillcolor="rgba(0,0,0,0)",
        line={"color": theme.INK_MUTED, "width": 1.6, "dash": "dot"},
        layer="below",
    )
    _annotate(
        figure,
        labels,
        index - first,
        float(bar["low"]) if zoned.direction.value == "alcista" else float(bar["high"]),
        "<b>OB SIN CONFIRMAR</b><br>ninguna vela del color del impulso<br>"
        "superó esta mecha antes de morir el ID",
        theme.INK_MUTED,
        above=zoned.direction.value != "alcista",
    )


def _anchor_index(analysis: TimeframeAnalysis, zoned: ImpulseZones) -> int | None:
    for impulse in analysis.impulses:
        if impulse.id_num == zoned.id_num:
            return impulse.index_anchor
    return None


def _confirmation_marker(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    zone: Zone,
    first: int,
    last: int,
    labels: Sequence[str],
) -> None:
    """§8 — un marcador sobre la vela que confirma cada OB."""
    index = zone.index_confirmation
    if index is None or not first <= index <= last:
        return
    bar = analysis.bars.iloc[index]
    level = float(bar["high"] if zone.direction.value == "alcista" else bar["low"])
    figure.add_trace(
        go.Scatter(
            x=[labels[index - first]],
            y=[level],
            mode="markers",
            marker={
                "symbol": "star",
                "size": 15,
                "color": theme.INK_PRIMARY,
                "line": {"color": theme.SURFACE, "width": 1},
            },
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_annotation(
        x=labels[index - first],
        y=level,
        text="confirma el OB",
        showarrow=False,
        yshift=18 if zone.direction.value == "alcista" else -18,
        font={"size": 10, "color": theme.INK_PRIMARY},
    )


def _annotate(
    figure: go.Figure,
    labels: Sequence[str],
    position: int,
    level: float,
    text: str,
    colour: str,
    *,
    above: bool,
) -> None:
    if not 0 <= position < len(labels):
        return
    figure.add_annotation(
        x=labels[position],
        y=level,
        text=text,
        showarrow=True,
        arrowhead=2,
        arrowsize=1,
        arrowwidth=1.2,
        arrowcolor=colour,
        ax=0,
        ay=-58 if above else 58,
        align="left",
        bordercolor=colour,
        borderwidth=1,
        borderpad=4,
        bgcolor=theme.SURFACE,
        font={"size": 11, "color": colour},
    )


def _subtitle(zoned: ImpulseZones, session_timezone: str) -> str:
    """Todo lo que hace falta para juzgar la captura sin abrir ningún CSV."""
    stamp = pd.Timestamp(zoned.ts_constitution)
    local = stamp.tz_convert(session_timezone)
    zona = session_label(session_timezone)
    block = zoned.order_block
    ob = (
        "sin OB confirmado"
        if block is None
        else (
            f"OB {block.height:,.2f} USD = {_ratio(block.height, zoned.atr):.3f} ATR · "
            f"confirmado {zoned.bars_from_ob_candle} barras después de su vela"
        )
    )
    return (
        f"UL {zoned.last.height:,.2f} USD = {_ratio(zoned.last.height, zoned.atr):.3f} ATR"
        f"{' (extendido)' if zoned.last.extended else ''}"
        f"{' (altura cero)' if zoned.last.is_flat else ''} · {ob} · "
        f"constituido {stamp:%Y-%m-%d %H:%M} UTC = {local:%Y-%m-%d %H:%M} {zona} · "
        f"salida {zoned.exit_break.value if zoned.exit_break else 'sigue vigente'}"
    )


def _ratio(numerator: float, denominator: float) -> float:
    if denominator is None or not pd.notna(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator


# --- LEEME ------------------------------------------------------------------


def _write_readme(captures: Sequence[ZoneCapture], folder: Path) -> None:
    """Una línea por imagen: fichero, ID, fecha, dirección y las dos alturas."""
    lines = [
        "CAPTURAS DE LA FASE 2.0 · ZONAS UL Y OB",
        "=======================================",
        "",
        "Una línea por imagen. Las alturas van en USD y en ATR: el oro pasó de ~1.200 a",
        "~4.300 USD en el histórico y los dólares solos no comparan nada entre años.",
        "",
        "El OB va con borde sólido cuando está confirmado y PUNTEADO cuando el ID murió",
        "sin que llegara a confirmarse: esa zona no existe, pero la vela candidata sí, y",
        "hay que poder verla. La estrella marca la vela que confirma cada OB.",
        "",
        "Los lotes son los cinco del §8:",
        "  bien_formados_*  diez alcistas y diez bajistas de H4 con las dos zonas y el UL",
        "                   con mecha. Se eligen los MÁS CERCANOS A LA MEDIANA de altura",
        "                   del UL en ATR, no los mayores: son casos representativos.",
        "  ul_extendido     los que se estiraron a la vela siguiente",
        "  sin_ob           los que murieron sin OB confirmado (la cifra de 7.1)",
        "  ul_altura_cero   los UL cuya vela del extremo no dejó mecha",
        "  ob_mas_alto/bajo los OB extremos por altura en ATR",
        "",
    ]
    if not captures:
        lines.append("(no se escribió ninguna captura)")
    for capture in captures:
        ob = (
            "sin OB confirmado"
            if capture.ob_usd is None
            else f"OB {capture.ob_usd:,.2f} USD / {capture.ob_atr:.3f} ATR"
        )
        lines.append(
            f"{capture.path.name}  ·  {capture.timeframe} ID {capture.id_num}  ·  "
            f"{capture.constituted:%Y-%m-%d %H:%M} UTC  ·  {capture.direction}  ·  "
            f"UL {capture.ul_usd:,.2f} USD / {capture.ul_atr:.3f} ATR  ·  {ob}  ·  "
            f"{capture.note}"
        )
    (folder / "LEEME.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["ZoneCapture", "draw_zones", "write_zone_captures"]
