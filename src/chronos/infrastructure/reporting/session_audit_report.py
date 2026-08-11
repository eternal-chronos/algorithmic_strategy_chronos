"""Página HTML del censo de velas cortas y los cortes de sesión (B.1 a B.5).

Un único fichero autocontenido —tablas y capturas embebidas en base64— que se
abre con doble clic o se sube tal cual a la web del propietario. No hay
recomendación de corte en ninguna parte: sólo los números y las cuatro imágenes
del mismo tramo, para que decida mirando su TradingView.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from chronos.application.structure.session_audit import SessionAudit

#: Columnas que se imprimen con decimales en vez de como enteros.
_DECIMAL_HINTS = ("pct", "mediana", "p1", "p5", "p10", "velas_por_semana", "dur_")

_OHLC = ("open", "high", "low", "close")

#: Decimales del OHLC, los mismos que usa el explorador.
DECIMALS = 4


@dataclass(frozen=True, slots=True)
class Section:
    """Un bloque del informe: título, texto de contexto y una tabla."""

    title: str
    note: str
    table: pd.DataFrame


@dataclass(frozen=True, slots=True)
class SessionAuditReport:
    """Todo lo que la página enseña. La compone la CLI; aquí sólo se dibuja."""

    subtitle: str
    #: Parte A: recuento con A1 adoptado y estado de la contaminación de color.
    colour_a1: pd.DataFrame
    colour_a2: pd.DataFrame
    audit: SessionAudit
    cuts_daily: pd.DataFrame
    cuts_h4: pd.DataFrame
    contrast: pd.DataFrame
    captures: Sequence[Path] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)


def render_session_audit(report: SessionAuditReport) -> str:
    """Devuelve el HTML completo, sin ninguna dependencia externa."""
    blocks = [
        _block(
            "A · R-02: el ancla va en la última vela contraria (A1)",
            "Recuento con <code>ANCHOR_MODE = A1_last_counter_body</code> y "
            "<code>LEG_START_MODE = L1_actual</code>. <em>ancla contraria</em> es lo que la "
            "regla del propietario exige: con A1 el color del ancla es correcto por "
            "construcción. El <em>extremo</em> es otra cosa (R-36) y el modo de ancla no lo "
            "toca; la última columna dice cuántos casos siguen existiendo.",
            _table(report.colour_a1),
        ),
        _block(
            "A · la misma corrida con A2, para regresión",
            "El modo anterior del proyecto, que queda disponible por configuración.",
            _table(report.colour_a2),
        ),
    ]

    for timeframe, item in report.audit.per_timeframe.items():
        blocks.append(
            _block(
                f"B.1 · censo de velas cortas — {timeframe}",
                f"Una vela {timeframe} completa contiene {item.expected_minutes:,} minutos de "
                f"M1. Se cuenta como <strong>corta</strong> la que contiene menos del 25 % "
                f"({item.expected_minutes // 4:,} minutos). Total: "
                f"<strong>{item.short_bars:,}</strong> de {item.bars:,} velas "
                f"({item.short_share:.1%}).",
                _table(item.census) + _table(item.by_weekday),
            )
        )

    blocks.append(
        _block(
            "B.2 · impacto sobre la estructura",
            "Papeles que una vela corta puede jugar en un ID: fijar el <em>ancla</em>, fijar "
            "el <em>extremo</em>, ser la vela contraria que lo <em>constituye</em> o ser la "
            "que lo <em>rompe</em>. Un episodio de latigazo cuenta si interviene una vela "
            "corta en el ID de ida o en el de vuelta.",
            _table(_impact_table(report.audit)),
        )
    )

    blocks.append(
        _block(
            "B.3 · cortes alternativos de sesión",
            "Cada fila es una reconstrucción completa de las velas y una re-ejecución del "
            "detector con <code>ANCHOR_MODE = A1</code>. Arriba el diario por "
            "<code>D_SESSION_START</code>; abajo H4 por <code>H4_OFFSET_HOURS</code>.",
            _table(report.cuts_daily) + _table(report.cuts_h4),
        )
    )

    blocks.append(
        _block(
            "B.4 · contraste contra TradingView",
            f"OHLC a cuatro decimales de las {_sessions(report.contrast)} velas diarias "
            "pedidas, en cada corte. <em>fecha_sesion</em> es el día que el propietario ve "
            "en su gráfico; <em>etiqueta_utc</em> es la marca UTC con la que el motor la "
            "guarda, que en los cortes de tarde cae en el día anterior.",
            _table(report.contrast),
        )
    )

    if report.captures:
        blocks.append(
            _block(
                "B.5 · el mismo tramo en los cuatro cortes",
                "Diario, 2019-03-01 a 2019-03-20, misma escala vertical en las cuatro. El "
                "número de velas cambia porque es justo lo que está en discusión.",
                "".join(_image(path) for path in report.captures),
            )
        )

    notes = "".join(f"<li>{html.escape(note)}</li>" for note in report.notes)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return _PAGE.format(
        subtitle=html.escape(report.subtitle),
        blocks="\n".join(blocks),
        notes=f"<ul class='notes'>{notes}</ul>" if notes else "",
        generated=html.escape(generated),
    )


# --- Dibujo -----------------------------------------------------------------


def _block(title: str, note: str, body: str) -> str:
    return (
        f"<section><h2>{html.escape(title)}</h2>"
        f"<p class='note'>{note}</p>{body}</section>"
    )


def _table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p class='note'>Sin datos.</p>"
    head = "".join(f"<th>{html.escape(str(column))}</th>" for column in frame.columns)
    rows = []
    for _, row in frame.iterrows():
        cells = "".join(
            f"<td>{html.escape(_cell(column, row[column]))}</td>" for column in frame.columns
        )
        emphasis = " class='total'" if str(row.iloc[0]) == "TOTAL" else ""
        rows.append(f"<tr{emphasis}>{cells}</tr>")
    return (
        "<div class='scroll'><table><thead><tr>"
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _cell(column: str, value: object) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, bool | np.bool_):
        return "sí" if value else "no"
    if isinstance(value, float | np.floating):
        # El OHLC va sin separador de millares y a cuatro decimales: se lee
        # contra la ventana de datos de TradingView, no contra otra tabla.
        if column in _OHLC:
            return f"{float(value):.{DECIMALS}f}"
        if not any(hint in column for hint in _DECIMAL_HINTS) and float(value).is_integer():
            return f"{int(value):,}"
        return f"{float(value):,.2f}"
    if isinstance(value, int | np.integer):
        return f"{int(value):,}"
    return str(value)


def _sessions(contrast: pd.DataFrame) -> int:
    """Cuántas sesiones distintas trae la tabla de contraste."""
    return 0 if contrast.empty else int(contrast["fecha_sesion"].nunique())


def _impact_table(audit: SessionAudit) -> pd.DataFrame:
    rows = [
        {"temporalidad": timeframe, "velas_cortas": item.short_bars, **item.impact}
        for timeframe, item in audit.per_timeframe.items()
    ]
    return pd.DataFrame(rows)


def _image(path: Path) -> str:
    """La imagen va embebida: la página tiene que funcionar sola."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f"<figure><img alt='{html.escape(path.stem)}' "
        f"src='data:image/png;base64,{encoded}'>"
        f"<figcaption>{html.escape(path.name)}</figcaption></figure>"
    )


_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Velas cortas y cortes de sesión · impulso dominante</title>
<style>
:root {{
  color-scheme: light;
  --surface: #fcfcfb; --page: #f9f9f7; --ink: #0b0b0b;
  --secondary: #52514e; --muted: #898781; --grid: #e1e0d9;
  --font: system-ui, -apple-system, "Segoe UI", sans-serif;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 24px 20px 56px; background: var(--page); color: var(--ink);
        font-family: var(--font); }}
.wrap {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 21px; margin: 0 0 4px; }}
h2 {{ font-size: 15px; margin: 0 0 6px; }}
.subtitle {{ color: var(--muted); font-size: 13px; margin: 0 0 24px; }}
section {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 10px;
           padding: 16px 18px; margin-bottom: 18px; }}
.note {{ color: var(--secondary); font-size: 13px; line-height: 1.55; margin: 0 0 12px; }}
.scroll {{ overflow-x: auto; margin-bottom: 10px; }}
table {{ border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }}
th, td {{ padding: 5px 10px; text-align: right; white-space: nowrap;
          border-bottom: 1px solid var(--grid); }}
th:first-child, td:first-child {{ text-align: left; }}
thead th {{ color: var(--secondary); font-weight: 600; text-transform: uppercase;
            font-size: 10.5px; letter-spacing: .06em; }}
tr.total td {{ font-weight: 600; border-top: 1px solid var(--ink); }}
code {{ background: var(--page); border: 1px solid var(--grid); border-radius: 4px;
        padding: 1px 5px; font-size: 12px; }}
figure {{ margin: 0 0 18px; }}
figure img {{ width: 100%; height: auto; border: 1px solid var(--grid); border-radius: 8px;
              display: block; }}
figcaption {{ color: var(--muted); font-size: 12px; margin-top: 6px; }}
ul.notes {{ color: var(--secondary); font-size: 12.5px; line-height: 1.6; padding-left: 18px; }}
footer {{ color: var(--muted); font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Velas cortas del domingo y cortes de sesión</h1>
    <p class="subtitle">{subtitle}</p>
  </header>
{blocks}
  <footer>
    {notes}
    <p>Generado el {generated}. El motor no recomienda ningún corte de sesión: decide el
       propietario comparando el OHLC contra su TradingView.</p>
  </footer>
</div>
</body>
</html>
"""
