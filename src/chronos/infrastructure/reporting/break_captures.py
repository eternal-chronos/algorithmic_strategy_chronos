"""Capturas de la fase 2.1 (§7): las roturas que la zona ha evitado.

Cinco lotes, en el orden en que el propietario los va a mirar:

  1. diez ID de H4 que sobreviven a roturas que antes los habrían matado;
  2. los cinco ID de mayor duración bajo la regla nueva, con todas sus roturas
     evitadas marcadas;
  3. cinco roturas por LÍNEA por no haber PUL en ese lado;
  4. los conflictos de evaluación simultánea —todos si son pocos, diez si son
     muchos— y, si no hay ninguno, se dice en el `LEEME` en vez de callarlo;
  5. el mismo tramo bajo las dos reglas, lado a lado, en Diario y H4.

Dos capas nuevas sobre el dibujo de la fase 2.0:

- **la rotura evitada**, un círculo con aspa sobre el cierre de la vela que bajo
  la regla antigua habría matado el ID, con la línea que cruzó y el borde que no
  llegó a cruzar. Es el mismo símbolo que la capa «Roturas evitadas» del
  explorador: se audita en los dos sitios y no debería haber dos dibujos de lo
  mismo;
- **el motivo de la rotura**, que distingue morir atravesando una zona de morir
  por línea porque en ese lado no había zona.

Las dos zonas se dibujan con `draw_zones`, el mismo trazo de la fase 2.0: son las
mismas zonas y enseñarlas de otra forma obligaría a comparar dos dibujos de lo
mismo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.zones import ImpulseZones, ZonesRun, detect_zones
from chronos.domain.structure.enums import BreakKind, BreakLevelSource
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.zone_break import AvoidedBreak
from chronos.infrastructure.reporting import theme
from chronos.infrastructure.reporting.impulse_captures import (
    CaptureRequest,
    bar_labels,
    window_figure,
)
from chronos.infrastructure.reporting.zone_captures import draw_zones

#: §7.1 pide ±25 barras de contexto.
CONTEXT_BARS = 25

WIDTH, HEIGHT, SCALE = 1600, 900, 1

BULLISH = theme.SERIES[2]
BEARISH = theme.NEGATIVE

#: Cuántas capturas de cada lote.
SURVIVORS = 10
LONGEST = 5
BY_LINE = 5
CONFLICTS = 10

#: Cuántas roturas evitadas se rotulan con texto. Las demás llevan su marca y su
#: segmento: veinte cuadros de texto no se leen, tapan las velas que hay que ver.
MAX_AVOIDED_ANNOTATIONS = 2

#: Temporalidad que el propietario audita.
AUDIT_TIMEFRAME = "H4"
#: Las dos que llevan la comparación lado a lado del §7.5.
SIDE_BY_SIDE_TIMEFRAMES = ("D", "H4")


@dataclass(frozen=True, slots=True)
class BreakCapture:
    """Una imagen escrita, con lo que va en su línea del `LEEME.txt`."""

    path: Path
    lot: str
    timeframe: str
    id_num: int
    direction: str
    constituted: pd.Timestamp
    #: Barras que habría durado el ID si la primera rotura evitada lo hubiera
    #: matado, que es lo que la regla antigua habría hecho con esas mismas velas.
    bars_old: int
    bars_new: int
    avoided: int
    reason: str


def write_break_captures(
    baseline: ImpulseRun,
    zoned: ImpulseRun,
    alternative: ImpulseRun | None,
    folder: Path,
) -> list[BreakCapture]:
    """Escribe los cinco lotes del §7 y devuelve lo que hace falta para el LEEME."""
    folder.mkdir(parents=True, exist_ok=True)
    zones = detect_zones(zoned)

    written: list[BreakCapture] = []
    for lot, picks in _lots(zoned):
        for position, (timeframe, impulse) in enumerate(picks, start=1):
            analysis = zoned.analyses.get(timeframe)
            if analysis is None:
                continue
            written.append(
                _write_one(analysis, zones, impulse, folder, lot, position)
            )
    written += _side_by_side(baseline, zoned, folder)
    _write_readme(written, zoned, alternative, folder)
    return written


# --- Qué capturar -----------------------------------------------------------

Pick = tuple[str, DominantImpulse]


def _lots(zoned: ImpulseRun) -> list[tuple[str, list[Pick]]]:
    return [
        ("sobrevive", _survivors(zoned)),
        ("mas_larga", _longest(zoned)),
        ("rotura_por_linea", _broken_by_line(zoned)),
        ("conflicto_solape", _conflicts(zoned)),
    ]


def _avoided_by_id(analysis: TimeframeAnalysis) -> dict[int, list[AvoidedBreak]]:
    grouped: dict[int, list[AvoidedBreak]] = {}
    for item in analysis.avoided:
        grouped.setdefault(item.id_num, []).append(item)
    return grouped


def _survivors(zoned: ImpulseRun) -> list[Pick]:
    """§7.1 — diez ID de H4 que sobreviven a roturas que antes los habrían matado.

    Se ordenan por cuántas roturas evitaron y, con empate, por cuánto ganaron en
    barras: son los casos donde la diferencia entre las dos reglas se ve mejor.
    """
    analysis = zoned.analyses.get(AUDIT_TIMEFRAME)
    if analysis is None:
        return []
    grouped = _avoided_by_id(analysis)
    published = {impulse.id_num: impulse for impulse in analysis.published}
    candidates = [
        (published[id_num], items)
        for id_num, items in grouped.items()
        if id_num in published
    ]
    candidates.sort(
        key=lambda pair: (len(pair[1]), _bars_gained(pair[0], pair[1])), reverse=True
    )
    return [(AUDIT_TIMEFRAME, impulse) for impulse, _ in candidates[:SURVIVORS]]


def _longest(zoned: ImpulseRun) -> list[Pick]:
    """§7.2 — los cinco ID de mayor duración bajo la regla nueva."""
    analysis = zoned.analyses.get(AUDIT_TIMEFRAME)
    if analysis is None:
        return []
    last_index = len(analysis.bars) - 1
    ordered = sorted(
        analysis.published, key=lambda impulse: impulse.bars_alive(last_index), reverse=True
    )
    return [(AUDIT_TIMEFRAME, impulse) for impulse in ordered[:LONGEST]]


def _broken_by_line(zoned: ImpulseRun) -> list[Pick]:
    """§7.3 — cinco ID que murieron por línea porque no tenían PUL.

    Se buscan primero en H4 y se completan con las otras temporalidades: son
    pocos, y el `LEEME` dice de cuál es cada uno.
    """
    picks: list[Pick] = []
    for timeframe in (AUDIT_TIMEFRAME, "D", "H1"):
        analysis = zoned.analyses.get(timeframe)
        if analysis is None:
            continue
        picks += [
            (timeframe, impulse)
            for impulse in analysis.published
            if impulse.exit_level_source is BreakLevelSource.LINE
            and impulse.exit_break_kind is BreakKind.EN_CONTRA
        ]
    return picks[:BY_LINE]


def _conflicts(zoned: ImpulseRun) -> list[Pick]:
    """§7.4 — los ID que murieron en una vela que cumplía las dos condiciones.

    El detector cuenta los conflictos; aquí se localizan los ID afectados. Si el
    recuento es cero no hay nada que capturar y el `LEEME` lo dice.
    """
    picks: list[Pick] = []
    for timeframe, analysis in zoned.analyses.items():
        if not analysis.diagnostics.get("conflictos_de_solape"):
            continue
        # Un conflicto sólo puede darse con el rango del ID no positivo: son esos
        # los que hay que enseñar, y son los mismos que el detector contó.
        picks += [
            (timeframe, impulse)
            for impulse in analysis.published
            if impulse.range_usd <= 0
        ]
    return picks[:CONFLICTS]


# --- Una captura ------------------------------------------------------------


def _write_one(
    analysis: TimeframeAnalysis,
    zones: ZonesRun,
    impulse: DominantImpulse,
    folder: Path,
    lot: str,
    position: int,
) -> BreakCapture:
    avoided = [item for item in analysis.avoided if item.id_num == impulse.id_num]
    figure, reason = _figure(analysis, zones, impulse, avoided)
    name = (
        f"{lot}_{position:02d}_{analysis.timeframe}_id{impulse.id_num}_"
        f"{impulse.ts_constitution:%Y%m%d}"
    )
    destination = folder / f"{name}.png"
    figure.write_image(str(destination), width=WIDTH, height=HEIGHT, scale=SCALE)
    last_index = len(analysis.bars) - 1
    return BreakCapture(
        path=destination,
        lot=lot,
        timeframe=analysis.timeframe,
        id_num=impulse.id_num,
        direction=impulse.direction.value,
        constituted=pd.Timestamp(impulse.ts_constitution),
        bars_old=_bars_under_old_rule(impulse, avoided, last_index),
        bars_new=impulse.bars_alive(last_index),
        avoided=len(avoided),
        reason=reason,
    )


def _figure(
    analysis: TimeframeAnalysis,
    zones: ZonesRun,
    impulse: DominantImpulse,
    avoided: Sequence[AvoidedBreak],
) -> tuple[go.Figure, str]:
    last_index = len(analysis.bars) - 1
    end = impulse.index_end if impulse.index_end is not None else last_index
    request = CaptureRequest(
        name=f"rotura_id{impulse.id_num}",
        timeframe=analysis.timeframe,
        first=min(impulse.index_constitution, impulse.index_anchor),
        last=end,
        title=(
            f"{analysis.timeframe} · ID {impulse.id_num} ({impulse.direction.value}) · "
            f"rotura por zona · constituido {impulse.ts_constitution:%Y-%m-%d %H:%M} UTC"
        ),
        subtitle=_subtitle(impulse, avoided, last_index),
        highlight=(impulse.id_num,),
        context=CONTEXT_BARS,
    )
    figure = window_figure(analysis, request)

    first = max(0, request.first - request.context)
    last = min(last_index, request.last + request.context)
    labels = bar_labels(pd.DatetimeIndex(analysis.bars.index[first : last + 1]))

    zoned = _zones_of(zones, analysis.timeframe, impulse.id_num)
    if zoned is not None:
        draw_zones(figure, zoned, first, last, labels)
    _draw_avoided(figure, avoided, first, last, labels)
    reason = _draw_break(figure, analysis, impulse, first, last, labels)
    return figure, reason


def _zones_of(zones: ZonesRun, timeframe: str, id_num: int) -> ImpulseZones | None:
    item = zones.per_timeframe.get(timeframe)
    if item is None:
        return None
    return next((zoned for zoned in item.items if zoned.id_num == id_num), None)


def _draw_avoided(
    figure: go.Figure,
    avoided: Sequence[AvoidedBreak],
    first: int,
    last: int,
    labels: Sequence[str],
) -> None:
    """La capa que el propietario audita primero: las velas que la zona salvó."""
    inside = [item for item in avoided if first <= item.index <= last]
    if not inside:
        return

    figure.add_trace(
        go.Scatter(
            x=[labels[item.index - first] for item in inside],
            y=[item.close for item in inside],
            mode="markers",
            # El mismo símbolo que la capa «Roturas evitadas» del explorador: el
            # propietario audita en los dos sitios y no debería tener que
            # aprenderse dos dibujos de lo mismo.
            marker={
                "symbol": "circle-x",
                "size": 13,
                "color": theme.INK_PRIMARY,
                "line": {"color": theme.SURFACE, "width": 1},
            },
            hoverinfo="skip",
            showlegend=False,
        )
    )
    for item in inside:
        # El tramo entre el cierre y la línea que cruzó: es exactamente lo que la
        # regla antigua contaba como rotura y la nueva ya no.
        figure.add_shape(
            type="line",
            x0=labels[item.index - first],
            x1=labels[item.index - first],
            y0=item.line,
            y1=item.close,
            line={"color": theme.INK_PRIMARY, "width": 2.4},
            layer="above",
        )

    # Un ID puede acumular veinte roturas evitadas y veinte cuadros de texto no se
    # leen: tapan las velas que hay que mirar. Se rotula la primera y la última
    # —el principio y el final del tramo que la regla nueva ha ganado— y el resto
    # se queda con su aspa y su segmento, que es lo que hay que ver.
    for position in _annotated(inside):
        item = inside[position]
        figure.add_annotation(
            x=labels[item.index - first],
            y=item.close,
            text=(
                f"<b>ROTURA EVITADA</b> ({item.kind.value})<br>"
                f"cierre {item.close:,.2f} · línea {item.line:,.2f} · "
                f"borde {item.zone_outer:,.2f}"
                + ("<br>el extremo se extiende aquí" if item.extended_extreme else "")
            ),
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1.2,
            arrowcolor=theme.INK_PRIMARY,
            ax=0,
            ay=-70 if item.kind is BreakKind.A_FAVOR else 70,
            align="left",
            bordercolor=theme.INK_PRIMARY,
            borderwidth=1,
            borderpad=4,
            bgcolor=theme.SURFACE,
            font={"size": 10, "color": theme.INK_PRIMARY},
        )
    if len(inside) > MAX_AVOIDED_ANNOTATIONS:
        figure.add_annotation(
            xref="paper",
            yref="paper",
            x=0.01,
            y=0.99,
            text=(
                f"<b>{len(inside)} roturas evitadas</b> en el tramo · "
                "rotuladas la primera y la última; el resto llevan su marca"
            ),
            showarrow=False,
            align="left",
            bordercolor=theme.INK_PRIMARY,
            borderwidth=1,
            borderpad=4,
            bgcolor=theme.SURFACE,
            font={"size": 11, "color": theme.INK_PRIMARY},
        )


def _annotated(inside: Sequence[AvoidedBreak]) -> tuple[int, ...]:
    if len(inside) <= MAX_AVOIDED_ANNOTATIONS:
        return tuple(range(len(inside)))
    return (0, len(inside) - 1)


def _draw_break(
    figure: go.Figure,
    analysis: TimeframeAnalysis,
    impulse: DominantImpulse,
    first: int,
    last: int,
    labels: Sequence[str],
) -> str:
    """§7 — distinguir la rotura por zona de la rotura por línea."""
    index = impulse.index_end
    if index is None or impulse.exit_break_kind is None:
        return "sigue vigente al final del histórico"

    source = impulse.exit_level_source or BreakLevelSource.LINE
    by_zone = source is not BreakLevelSource.LINE
    reason = (
        f"{impulse.exit_break_kind.value} por {'zona ' + source.value if by_zone else 'LÍNEA'}"
        + ("" if by_zone else " (sin PUL en ese lado)")
    )
    if not first <= index <= last:
        return reason

    bar = analysis.bars.iloc[index]
    close = float(bar["close"])
    figure.add_trace(
        go.Scatter(
            x=[labels[index - first]],
            y=[close],
            mode="markers",
            marker={
                # Rombo relleno para la zona, cuadrado hueco para la línea: se
                # distinguen de un vistazo y en blanco y negro.
                "symbol": "diamond" if by_zone else "square-open",
                "size": 15,
                "color": BULLISH if impulse.direction.value == "alcista" else BEARISH,
                "line": {"color": theme.SURFACE, "width": 1},
            },
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_annotation(
        x=labels[index - first],
        y=close,
        text=f"<b>{reason}</b>",
        showarrow=False,
        yshift=-20 if impulse.direction.value == "alcista" else 20,
        font={"size": 11, "color": theme.INK_PRIMARY},
    )
    return reason


def _subtitle(
    impulse: DominantImpulse, avoided: Sequence[AvoidedBreak], last_index: int
) -> str:
    old = _bars_under_old_rule(impulse, avoided, last_index)
    new = impulse.bars_alive(last_index)
    extensions = (
        f"extremo {impulse.extreme_at_constitution:,.2f} → {impulse.extreme:,.2f} "
        f"en {impulse.extreme_extensions} extensiones"
        if impulse.extreme_extensions
        else "extremo sin mover"
    )
    return (
        f"ancla {impulse.anchor:,.4f} · {extensions} · "
        f"{len(avoided)} roturas evitadas · "
        f"duración {old} barras con la regla antigua → {new} con la nueva"
    )


def _bars_under_old_rule(
    impulse: DominantImpulse, avoided: Sequence[AvoidedBreak], last_index: int
) -> int:
    """Barras que habría vivido el ID si su primera rotura evitada lo hubiera matado.

    No es "la duración del ID equivalente en la corrida antigua": ese ID puede no
    existir, porque salvar una rotura renumera todo lo que viene después. Es la
    pregunta que sí tiene respuesta sobre estas velas —cuándo habría muerto éste—
    y es la que hace comparables las dos columnas del `LEEME`.
    """
    if not avoided:
        return impulse.bars_alive(last_index)
    return max(0, min(item.index for item in avoided) - impulse.index_constitution)


def _bars_gained(impulse: DominantImpulse, avoided: Sequence[AvoidedBreak]) -> int:
    if not avoided:
        return 0
    end = impulse.index_end if impulse.index_end is not None else impulse.index_constitution
    return max(0, end - min(item.index for item in avoided))


# --- §7.5 El mismo tramo bajo las dos reglas --------------------------------


def _side_by_side(
    baseline: ImpulseRun, zoned: ImpulseRun, folder: Path
) -> list[BreakCapture]:
    """El mismo tramo de gráfico bajo las dos reglas, con la misma escala.

    Comparar dos dibujos con escalas distintas no compara nada, así que el rango
    vertical se calcula una vez sobre las velas —que son las mismas en las dos
    corridas— y se impone a las dos imágenes.
    """
    written: list[BreakCapture] = []
    for timeframe in SIDE_BY_SIDE_TIMEFRAMES:
        after = zoned.analyses.get(timeframe)
        before = baseline.analyses.get(timeframe)
        if after is None or before is None:
            continue
        window = _busiest_window(after)
        if window is None:
            continue
        first, last, id_num = window

        chunk = after.bars.iloc[first : last + 1]
        low, high = float(chunk["low"].min()), float(chunk["high"].max())
        margin = 0.04 * (high - low)
        stamp = pd.Timestamp(after.bars.index[first])

        for label, analysis, rule in (
            ("antigua", before, "BREAK_BY_ZONE = false · rotura por LÍNEA"),
            ("nueva", after, "BREAK_BY_ZONE = true · rotura por ZONA"),
        ):
            request = CaptureRequest(
                name=f"lado_a_lado_{timeframe}_{label}",
                timeframe=timeframe,
                first=first,
                last=last,
                title=f"{timeframe} · el mismo tramo con la regla {label.upper()}",
                subtitle=(
                    f"{rule} · {stamp:%Y-%m-%d} → "
                    f"{pd.Timestamp(after.bars.index[last]):%Y-%m-%d} · "
                    f"{_impulses_in(analysis, first, last)} ID en el tramo"
                ),
                context=0,
            )
            figure = window_figure(
                analysis, request, y_range=(low - margin, high + margin)
            )
            if analysis is after:
                labels = bar_labels(
                    pd.DatetimeIndex(after.bars.index[first : last + 1])
                )
                _draw_avoided(
                    figure,
                    [item for item in after.avoided if first <= item.index <= last],
                    first,
                    last,
                    labels,
                )
            destination = folder / f"{request.name}.png"
            figure.write_image(str(destination), width=WIDTH, height=HEIGHT, scale=SCALE)
            written.append(
                BreakCapture(
                    path=destination,
                    lot="lado_a_lado",
                    timeframe=timeframe,
                    id_num=id_num,
                    direction="—",
                    constituted=stamp,
                    bars_old=_impulses_in(before, first, last),
                    bars_new=_impulses_in(after, first, last),
                    avoided=sum(
                        1 for item in after.avoided if first <= item.index <= last
                    ),
                    reason=f"regla {label}; las marcas de evitada sólo van sobre la nueva",
                )
            )
    return written


def _busiest_window(analysis: TimeframeAnalysis) -> tuple[int, int, int] | None:
    """El ID con más roturas evitadas, con contexto: es el tramo que más cambia."""
    grouped = _avoided_by_id(analysis)
    if not grouped:
        return None
    id_num = max(grouped, key=lambda key: len(grouped[key]))
    impulse = next(
        (item for item in analysis.published if item.id_num == id_num), None
    )
    if impulse is None:
        return None
    last_index = len(analysis.bars) - 1
    end = impulse.index_end if impulse.index_end is not None else last_index
    return (
        max(0, impulse.index_constitution - CONTEXT_BARS),
        min(last_index, end + CONTEXT_BARS),
        id_num,
    )


def _impulses_in(analysis: TimeframeAnalysis, first: int, last: int) -> int:
    return sum(
        1
        for impulse in analysis.published
        if impulse.index_constitution <= last
        and (impulse.index_end if impulse.index_end is not None else last) >= first
    )


# --- LEEME ------------------------------------------------------------------


def _write_readme(
    captures: Sequence[BreakCapture],
    zoned: ImpulseRun,
    alternative: ImpulseRun | None,
    folder: Path,
) -> None:
    conflicts = sum(
        analysis.diagnostics.get("conflictos_de_solape", 0)
        for analysis in zoned.analyses.values()
    )
    lines = [
        "CAPTURAS DE LA FASE 2.1 · ROTURA DEL ID POR ZONA",
        "================================================",
        "",
        "Una línea por imagen. Las dos columnas de duración son:",
        "",
        "  antigua  barras que el ID habría vivido si su PRIMERA rotura evitada lo",
        "           hubiera matado, que es lo que la regla antigua habría hecho con",
        "           estas mismas velas. No es 'el ID equivalente de la corrida",
        "           antigua': ese ID puede no existir, porque salvar una rotura",
        "           renumera todo lo que viene después.",
        "  nueva    barras que ha vivido de verdad con la regla nueva.",
        "",
        "En las imágenes:",
        "",
        "  círculo con aspa  rotura EVITADA: la vela cerró más allá de la línea del ID",
        "                    pero sin atravesar la zona entera. El segmento vertical va",
        "                    del cierre a la línea que cruzó. Es el mismo símbolo que la",
        "                    capa «Roturas evitadas» del explorador.",
        "  rombo relleno     el ID murió atravesando una zona (UL o PUL).",
        "  cuadrado hueco    el ID murió por LÍNEA, porque en ese lado no había zona:",
        "                    ese lado no tenía PUL.",
        "  bandas de color   las zonas UL y PUL, con el mismo trazo de la fase 2.0.",
        "",
        "Los lotes son los cinco del §7:",
        "  sobrevive         diez ID de H4 que sobreviven a roturas que antes los",
        "                    habrían matado, con ±25 barras de contexto",
        "  mas_larga         los cinco ID de mayor duración bajo la regla nueva",
        "  rotura_por_linea  cinco ID muertos por línea por no tener PUL",
        "  conflicto_solape  velas que cumplían las DOS condiciones de rotura a la vez",
        "  lado_a_lado       el mismo tramo bajo las dos reglas, en Diario y H4, con la",
        "                    misma escala vertical",
        "",
    ]

    if conflicts == 0:
        lines += [
            "SOBRE EL LOTE conflicto_solape: no hay ni una imagen, y no por falta de",
            "búsqueda. El conflicto de evaluación simultánea NO se da ni una vez en el",
            "histórico. Para que una vela cumpla las dos condiciones hacen falta los dos",
            "bordes exteriores INVERTIDOS, y eso es lo contrario de que las zonas se",
            "solapen: en un ID alcista se cumple siempre",
            "",
            "    borde exterior del UL >= extremo > ancla >= borde exterior del PUL",
            "",
            "mientras el rango del ID sea positivo. Con la regla nueva no queda ningún",
            "impulso de rango no positivo, así que no queda ningún conflicto posible.",
        ]
        if alternative is not None:
            lines.append(
                f"Los dos OVERLAP_PRIORITY producen la misma historia (hash "
                f"{zoned.config_hash} y {alternative.config_hash}, mismos impulsos)."
            )
        lines.append("")

    if not captures:
        lines.append("(no se escribió ninguna captura)")
    for capture in captures:
        if capture.lot == "lado_a_lado":
            # Aquí no hay un ID que dure más: hay un tramo con menos impulsos.
            # Reutilizar las columnas de duración diría otra cosa.
            lines.append(
                f"{capture.path.name}  ·  {capture.timeframe}  ·  tramo desde "
                f"{capture.constituted:%Y-%m-%d %H:%M} UTC  ·  "
                f"{capture.bars_old} ID con la regla antigua → {capture.bars_new} con la "
                f"nueva  ·  {capture.avoided} roturas evitadas en el tramo  ·  "
                f"{capture.reason}"
            )
            continue
        lines.append(
            f"{capture.path.name}  ·  {capture.timeframe} ID {capture.id_num}  ·  "
            f"{capture.constituted:%Y-%m-%d %H:%M} UTC  ·  {capture.direction}  ·  "
            f"duración {capture.bars_old} → {capture.bars_new} barras  ·  "
            f"{capture.avoided} roturas evitadas  ·  {capture.reason}"
        )
    (folder / "LEEME.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["BreakCapture", "write_break_captures"]
