"""Informe de la fase 2.1 en texto plano (§6): la rotura del ID por zona.

Se lee en un terminal, se pega en un correo y se archiva junto a las capturas, así
que va en ancho fijo y sin colores. Cada tabla lleva **la columna de la línea base
al lado**: sin ella los números nuevos no dicen nada.

Tres secciones van marcadas como prioritarias porque el propietario las pidió así:
6.5 (los enanos), 6.6 (el latigazo) y 6.8 (las zonas solapadas).

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se presentan
números; decide el propietario.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from chronos.application.structure.break_comparison import (
    PHASE20_SURVIVAL_ESTIMATE,
    SIZE_PERCENTILES,
    BreakRuleComparison,
    TimeframeBreakComparison,
)
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.ascii_table import render_table, section

_COUNT = ",d"
_PCT = ".1%"
_SIGNED_PCT = "+.1%"
_BARS = ",.1f"

_CENSUS = {
    "antes": _COUNT, "despues": _COUNT, "diferencia": "+,d", "pct": _SIGNED_PCT,
    "alcistas_antes": _COUNT, "alcistas_despues": _COUNT,
    "bajistas_antes": _COUNT, "bajistas_despues": _COUNT,
}
_DURATION = {
    "n_antes": _COUNT, "n_despues": _COUNT,
    "mediana_antes": _BARS, "mediana_despues": _BARS,
    "p10_antes": _BARS, "p10_despues": _BARS,
    "p90_antes": _BARS, "p90_despues": _BARS,
    "maximo_antes": ",.0f", "maximo_despues": ",.0f",
}
_LIMBO = {
    "barras": _COUNT, "barras_limbo_antes": _COUNT, "barras_limbo_despues": _COUNT,
    "pct_antes": _PCT, "pct_despues": _PCT, "dif_pp": "+.2f",
    "episodios_antes": _COUNT, "episodios_despues": _COUNT,
    "mediana_antes": _BARS, "mediana_despues": _BARS,
    "maximo_antes": ",.0f", "maximo_despues": ",.0f",
}
_DWARFS = {
    "id_antes": _COUNT, "id_despues": _COUNT,
    "enanos_antes": _COUNT, "enanos_despues": _COUNT,
    "diferencia": "+,d", "pct_variacion": _SIGNED_PCT,
    "share_antes": _PCT, "share_despues": _PCT,
}
_BREAKS = {
    "total": _COUNT, "por_zona": _COUNT, "por_linea": _COUNT,
    "pct_por_linea": _PCT, "evitadas": _COUNT,
}
_OVERLAP = {
    "con_ob_antes": _COUNT, "con_ob_despues": _COUNT,
    "solapadas_antes": _COUNT, "solapadas_despues": _COUNT,
    "pct_antes": _PCT, "pct_despues": _PCT, "diferencia": "+,d",
}
_PRIORITY = {"conflictos": _COUNT, "id_orden_1": _COUNT, "id_orden_2": _COUNT, "diferencia": "+,d"}
_ALIGNMENT = {
    "barras_antes": _COUNT, "barras_despues": _COUNT,
    "vigentes_antes": _PCT, "vigentes_despues": _PCT,
    "limbo_antes": _PCT, "limbo_despues": _PCT,
    "alineadas_antes": _PCT, "alineadas_despues": _PCT,
}
_COUNTER = {
    "id_publicados": _COUNT, "extremo_color_contrario": _COUNT, "pct": _PCT,
    "en_arranque_de_pierna": _COUNT, "en_vela_que_extendio": _COUNT, "en_otra_vela": _COUNT,
}


def _whipsaw_formats(columns: pd.Index) -> dict[str, str]:
    return {str(column): _COUNT for column in columns if str(column) != "anio"}


def _size_formats(unit: str) -> dict[str, str]:
    digits = {"usd": ",.2f", "atr": ",.3f", "pct": ".4%"}[unit]
    formats = {"n_antes": _COUNT, "n_despues": _COUNT}
    for value in SIZE_PERCENTILES:
        formats[f"p{value}_antes"] = digits
        formats[f"p{value}_despues"] = digits
    return formats


def render_break_report(
    baseline: ImpulseRun,
    zoned: ImpulseRun,
    comparison: BreakRuleComparison,
    *,
    regression_ok: bool,
    regression_note: str,
    generated_at: datetime | None = None,
) -> str:
    """Devuelve el informe completo de la fase 2.1 en texto plano."""
    generated_at = generated_at or SystemClock().now()
    blocks = [
        _header(zoned, comparison, generated_at),
        _regression_block(regression_ok, regression_note),
        _scope_block(),
        _counts_block(baseline, zoned),
    ]
    for timeframe, item in comparison.per_timeframe.items():
        blocks.append(_timeframe_block(timeframe, item))
    blocks.append(_alignment_block(comparison))
    blocks.append(_priority_block(comparison))
    return "\n".join(blocks).strip() + "\n"


# --- Cabecera ---------------------------------------------------------------


def _header(
    zoned: ImpulseRun,
    comparison: BreakRuleComparison,
    generated_at: datetime,
) -> str:
    config = zoned.config
    rows = [
        ("Símbolo", config.symbol),
        ("Lado del precio (STRUCTURE_SIDE)", config.structure_side),
        ("Origen de los datos", zoned.provenance or "n/d"),
        ("Temporalidades", ", ".join(comparison.per_timeframe)),
        ("Regla ANTIGUA (línea base)", comparison.baseline_description),
        ("Regla NUEVA", comparison.zoned_description),
        ("Hash de la línea base", comparison.baseline_hash),
        ("Hash de la línea base NUEVA", comparison.zoned_hash),
        (
            "Hash con el otro OVERLAP_PRIORITY",
            comparison.alternative_hash or "no se ha ejecutado",
        ),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    width = max(len(label) for label, _ in rows) + 2
    body = "\n".join(f"{label:<{width}}{value}" for label, value in rows)
    return (
        "FASE 2.1 · ROTURA DEL ID POR ZONA\n"
        "=================================\n\n"
        f"{body}\n"
    )


def _regression_block(ok: bool, note: str) -> str:
    verdict = "PASA" if ok else "NO PASA"
    return (
        f"{section('0. Regresión con BREAK_BY_ZONE = false')}\n\n"
        "El interruptor apagado tiene que reproducir la línea base byte a byte. Si no\n"
        "sale idéntico, hay un bug en la refactorización y no un resultado.\n\n"
        f"  VEREDICTO: {verdict}\n"
        f"  {note}\n"
    )


def _scope_block() -> str:
    return (
        f"{section('Alcance de esta fase')}\n\n"
        "Cambia la REGLA DE ROTURA del impulso dominante y nada más. No se ha tocado\n"
        "cómo se detectan las zonas UL y OB (fase 2.0, ya auditada), ni el ancla, ni el\n"
        "modo de arranque de pierna, ni el corte de sesión. No hay FVG, ni señales, ni\n"
        "entradas, ni stops, ni targets, ni optimización de ningún parámetro.\n\n"
        "La regla nueva, literal:\n\n"
        "  lado a favor (extremo) -> manda el UL, que existe siempre\n"
        "  lado en contra (ancla) -> manda el OB si está confirmado; si no, la línea\n"
        "  romper una zona     -> CERRAR más allá de su borde EXTERIOR, atravesándola\n"
        "                         entera. Perforarla con mecha y cerrar dentro no rompe;\n"
        "                         cerrar dentro tampoco.\n\n"
        "No es un filtro posterior: es un cambio en la máquina de estados. Salvar una\n"
        "rotura deja el ID vivo, su extremo puede extenderse y el siguiente nace en otro\n"
        "sitio y con otra numeración. Las dos corridas de este informe son dos\n"
        "ejecuciones completas del módulo sobre las mismas velas.\n"
    )


def _counts_block(baseline: ImpulseRun, zoned: ImpulseRun) -> str:
    rows = []
    for timeframe, analysis in zoned.analyses.items():
        before = baseline.analyses[timeframe]
        rows.append(
            {
                "temporalidad": timeframe,
                "velas": len(analysis.bars),
                "detectados_antes": len(before.impulses),
                "detectados_despues": len(analysis.impulses),
                "publicados_antes": len(before.published),
                "publicados_despues": len(analysis.published),
                "variacion": _ratio(len(analysis.impulses), len(before.impulses)),
            }
        )
    formats = {
        "velas": _COUNT,
        "detectados_antes": _COUNT, "detectados_despues": _COUNT,
        "publicados_antes": _COUNT, "publicados_despues": _COUNT,
        "variacion": _SIGNED_PCT,
    }
    return (
        f"{section('La nueva línea base')}\n\n"
        f"{render_table(pd.DataFrame(rows), formats=formats)}\n"
    )


# --- Una temporalidad -------------------------------------------------------


def _timeframe_block(timeframe: str, item: TimeframeBreakComparison) -> str:
    parts = [
        section(f"Temporalidad {timeframe}"),
        "",
        section("6.1 · Censo de impulsos", level=2),
        render_table(item.census, formats=_CENSUS),
        "",
        section("6.2 · Duración del ID en barras", level=2),
        "Se espera que suba: los impulsos viven más.",
        render_table(item.duration, formats=_DURATION),
        "",
        section("6.3 · Limbo", level=2),
        "Las velas son las mismas en las dos reglas; lo que cambia es cuántas caen en limbo.",
        render_table(item.limbo, formats=_LIMBO),
        "",
        section("6.4 · Tamaño del ID (rango)", level=2),
        _size_tables(item.size),
        "",
        section("6.5 · LOS ENANOS · PRIORITARIA", level=2),
        (
            "ID de rango menor que 0,25 ATR. Es la cifra que dice si la regla nueva\n"
            "resuelve el problema que se persigue desde la fase 1. `share` es la\n"
            "fracción sobre los ID del año: la regla nueva baja también el denominador."
        ),
        render_table(item.dwarfs, formats=_DWARFS),
        "",
        section("6.6 · LATIGAZO · PRIORITARIA", level=2),
        (
            "Episodios en que el sesgo se invierte y se deshace: tres ID consecutivos\n"
            "con direcciones A, B, A. La duración es en barras del sesgo intermedio."
        ),
        render_table(item.whipsaws, formats=_whipsaw_formats(item.whipsaws.columns)),
        "",
        section("6.7 · Roturas: por zona y por línea", level=2),
        (
            "Con la regla nueva, morir 'por línea' significa una sola cosa: que en ese\n"
            "lado no había zona que sustituyera a la línea. Sólo le puede pasar al lado\n"
            "en contra de un ID cuyo OB nunca llegó a confirmarse; el UL existe siempre."
        ),
        render_table(item.breaks, formats=_BREAKS),
        "",
        section("6.8 · ZONAS SOLAPADAS · PRIORITARIA", level=2),
        (
            "ID cuyas zonas UL y OB se pisan en precio, antes y después. La hipótesis a\n"
            "comprobar es que esos impulsos enanos desaparecen solos, porque nacían de\n"
            "roturas prematuras que la regla nueva ya no permite."
        ),
        render_table(item.overlap, formats=_OVERLAP),
        "",
        section("6.10 · Extremo sobre vela de color contrario (R-36)", level=2),
        (
            "El defecto sigue sin corregirse; esta fase no lo toca. Lo que cambia es el\n"
            "reparto: con la regla nueva el extremo puede moverse con el ID ya vigente,\n"
            "así que aparece una puerta que en la fase 1 no existía."
        ),
        render_table(item.counter_colour_extremes, formats=_COUNTER),
        "",
        section("6.11 · Contraste con la estimación de la fase 2.0", level=2),
        _survival_note(timeframe),
        render_table(_formatted_survival(item.survival_contrast)),
        "",
    ]
    return "\n".join(parts)


def _formatted_survival(frame: pd.DataFrame) -> pd.DataFrame:
    """Mezclar recuentos y porcentajes en una columna exige formatear por fila."""
    if frame.empty:
        return frame
    return frame.assign(
        valor=[
            f"{value:.1%}" if unit == "%" else f"{value:,.0f}"
            for value, unit in zip(frame["valor"], frame["unidad"], strict=True)
        ]
    ).drop(columns=["unidad"])


def _size_tables(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "(sin datos)"
    blocks = []
    for unit, label in (
        ("usd", "USD"),
        ("atr", "ATR previo"),
        ("pct", "% del precio del ancla"),
    ):
        subset = frame[frame["unidad"] == unit].drop(columns=["unidad"])
        blocks.append(f"[{label}]\n{render_table(subset, formats=_size_formats(unit))}")
    return "\n\n".join(blocks)


def _survival_note(timeframe: str) -> str:
    estimate = PHASE20_SURVIVAL_ESTIMATE.get(timeframe)
    share = "n/d" if estimate is None else f"{estimate:.1%}"
    return (
        f"La fase 2.0 estimó que el {share} de las roturas de {timeframe} habrían\n"
        "sobrevivido. Esa estimación miró, rotura a rotura del histórico de entonces, si\n"
        "el cierre que mató al ID se había quedado dentro de su zona: mide cuántas\n"
        "roturas se salvaban EN SU INSTANTE, con todo lo demás igual. Al re-ejecutar,\n"
        "cada rotura salvada cambia lo que viene después —el ID sigue vivo, extiende su\n"
        "extremo y se enfrenta a velas distintas—, así que las dos cifras miden cosas\n"
        "distintas y no se espera que coincidan."
    )


# --- Las temporalidades detectadas a la vez ---------------------------------


def _alignment_block(comparison: BreakRuleComparison) -> str:
    return (
        f"{section('6.9 · Sesgos simultáneos de las temporalidades detectadas')}\n\n"
        "Todo se lee sobre la rejilla de la temporalidad más fina con detector: en cada\n"
        "uno de sus cierres se mira el último cierre disponible de las mayores. Nunca al\n"
        "revés, que sería preguntarle a la vela diaria qué hará dentro de unas horas.\n\n"
        f"{render_table(comparison.alignment, formats=_ALIGNMENT)}\n"
    )


def _priority_block(comparison: BreakRuleComparison) -> str:
    effect = comparison.overlap_priority_effect
    conflicts = (
        int(pd.to_numeric(effect["conflictos"], errors="coerce").fillna(0).sum())
        if not effect.empty
        else 0
    )
    verdict = (
        "El conflicto no se da ni una vez en el histórico: la elección de\n"
        "  OVERLAP_PRIORITY es COSMÉTICA sobre estos datos. Se declara como tal y los dos\n"
        "  órdenes quedan implementados."
        if conflicts == 0
        else (
            f"El conflicto se da {conflicts:,} veces. La elección de OVERLAP_PRIORITY NO es\n"
            "  cosmética: decide de qué muere el ID y con ello la dirección de la pierna."
        )
    )
    return (
        f"{section('6.8 bis · Efecto real de OVERLAP_PRIORITY')}\n\n"
        "Que dos zonas se solapen en precio NO basta para que una vela cumpla las dos\n"
        "condiciones de rotura. Para cumplirlas hacen falta los dos bordes exteriores\n"
        "INVERTIDOS, y unas zonas que se pisan los tienen justo en el otro orden. En un\n"
        "ID alcista se cumple siempre\n\n"
        "    borde exterior del UL >= extremo > ancla >= borde exterior del OB\n\n"
        "mientras el rango del ID sea positivo, y ninguna vela puede cerrar por encima\n"
        "del primero y por debajo del segundo a la vez. El orden sólo decide algo con\n"
        "impulsos de rango NO POSITIVO, que es lo que produce un hueco que se salta el\n"
        "ancla.\n\n"
        f"{render_table(effect, formats=_PRIORITY)}\n\n"
        f"  {verdict}\n"
    )


def _ratio(after: int, before: int) -> float:
    return (after - before) / before if before else float("nan")


__all__ = ["render_break_report"]
