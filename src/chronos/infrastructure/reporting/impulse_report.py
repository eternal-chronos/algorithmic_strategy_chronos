"""Informe de cierre de la fase 1 en texto plano (§5.2 y secciones A-E).

Se escribe para ser leído junto a las capturas de TradingView del propietario,
así que dice siempre tres cosas antes de cualquier número: sobre qué lado del
precio se calculó, con qué offset se agregaron las velas y qué parámetros siguen
abiertos.

El orden de las secciones es el del encargo de cierre —A verificación horaria,
B ancla, C censo, D lateralización, E geometría— para poder leerlo con el
enunciado al lado. Todo va desglosado por año: el oro pasó de ~1.200 a ~4.300
USD en el histórico y ninguna cifra en dólares es comparable entre extremos, por
eso el tamaño se da siempre también en ATR y en % del precio.

Aquí no se recomienda ningún umbral ni ningún valor. Se presentan números.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pandas as pd

from chronos.application.structure.anchor_comparison import AnchorComparison
from chronos.application.structure.baseline_comparison import (
    ALIGNMENT_METRIC,
    PROVISIONAL_HASH,
    BaselineComparison,
)
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.evidence import Evidence
from chronos.application.structure.geometry import GEOMETRY_COLUMNS
from chronos.application.structure.lateralization import (
    DEGENERATE_RANGE_ATR,
    REVISIT_HORIZONS,
    SIGNATURE_MIN_TOUCHES,
    LateralizationStudy,
    TimeframeLateralization,
    contacts_by_year,
    degenerate_verdict,
    detail,
    populations,
    revisit_summary,
    signature_by_year,
)
from chronos.application.structure.statistics import (
    SIZE_PERCENTILES,
    TINY_BODY_ATR,
    WHIPSAW_WINDOWS,
    ImpulseStatistics,
    TimeframeStatistics,
    counter_colour_extremes,
    summarize,
)
from chronos.application.structure.timezone_audit import SeasonPeaks, TimezoneAudit
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.ascii_table import render_rows, render_table, section

_COUNT = ",d"
_PRICE_FORMATS = {"mediana": ",.4f", "p10": ",.4f", "p90": ",.4f", "maximo": ",.4f", "n": _COUNT}
_BAR_FORMATS = {"mediana": ",.1f", "p10": ",.1f", "p90": ",.1f", "maximo": ",.0f", "n": _COUNT}
_SIZE_USD = {"n": _COUNT, **{f"p{value}": ",.3f" for value in SIZE_PERCENTILES}}
_SIZE_ATR = {"n": _COUNT, **{f"p{value}": ",.3f" for value in SIZE_PERCENTILES}}
_SIZE_PCT = {"n": _COUNT, **{f"p{value}": ".4%" for value in SIZE_PERCENTILES}}

#: Ficha detallada que pide D.6: diciembre de 2025 en Diario. Los ID se buscan
#: por fecha y no por número: cada cambio de configuración los renumera, y una
#: lista fija de números acaba señalando otro tramo del histórico o ninguno.
DETAIL_TIMEFRAME = "D"
DETAIL_MONTH = "2025-12"


def render_report(
    run: ImpulseRun,
    statistics: ImpulseStatistics | None = None,
    generated_at: datetime | None = None,
    *,
    anchors: AnchorComparison | None = None,
    lateralization: LateralizationStudy | None = None,
    baseline: BaselineComparison | None = None,
) -> str:
    """Devuelve el informe completo en texto plano."""
    if not run.enabled:
        return (
            "MÓDULO 1 · IMPULSO DOMINANTE\n"
            "============================\n\n"
            "El módulo está DESACTIVADO por configuración (`enabled: false`).\n"
            "No se ha procesado ninguna barra y no se emite ningún impulso.\n"
        )

    statistics = statistics or summarize(run)
    generated_at = generated_at or SystemClock().now()
    blocks = [
        _header(run, generated_at),
        _baseline_block(baseline),
        _timezone_block(run),
        _anchor_block(anchors),
        _aggregation_block(run),
        _open_decisions_block(run),
        _census_block(statistics),
        _counter_extreme_block(run),
    ]
    if lateralization is not None:
        blocks.append(_lateralization_block(run, lateralization))
    blocks += [_geometry_block(), _scope_block(run)]
    return "\n".join(blocks).strip() + "\n"


# --- Cabecera ---------------------------------------------------------------


def _header(run: ImpulseRun, generated_at: datetime) -> str:
    config = run.config
    rows = [
        ("Símbolo", config.symbol),
        ("Lado del precio (STRUCTURE_SIDE)", config.structure_side),
        ("Origen de los datos", run.provenance or "n/d"),
        ("Temporalidades", ", ".join(run.analyses)),
        ("Hash de configuración", run.config_hash),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    # Pares campo/valor en líneas sueltas y no en tabla: el origen de los datos
    # es una ruta larga y alinear su columna deformaría el informe entero.
    width = max(len(label) for label, _ in rows) + 2
    body = "\n".join(f"{label:<{width}}{value}" for label, value in rows)
    return (
        "MÓDULO 1 · IMPULSO DOMINANTE · CIERRE DE FASE 1\n"
        "==============================================\n\n"
        f"{body}\n\n"
        f"{_supersession_note(run)}\n\n"
        "Parámetros efectivos de esta corrida:\n\n"
        f"{_effective_parameters(run)}\n"
    )


def _supersession_note(run: ImpulseRun) -> str:
    """Deja escrito que los números anteriores ya no valen y por qué."""
    return (
        "ESTA CORRIDA SUSTITUYE A LAS ANTERIORES.\n"
        f"Los resultados publicados con el hash {PROVISIONAL_HASH} —D 477 / H4 2.068 /\n"
        "H1 7.416 impulsos— quedan archivados como PROVISIONALES: se calcularon con el\n"
        "ancla equivocada (A2_first_leg_bar) y con el corte diario en 00:00 UTC, que deja\n"
        "la hora de reapertura del domingo sola en una vela diaria propia. Las dos cosas\n"
        f"están corregidas aquí. Hash de la línea base definitiva: {run.config_hash}."
    )


# --- Antes y después --------------------------------------------------------


def _baseline_block(baseline: BaselineComparison | None) -> str:
    title = section("ANTES Y DESPUÉS · la línea base provisional frente a la definitiva")
    if baseline is None:
        return f"{title}\n\nNO EJECUTADA en esta corrida.\n"

    lines = [
        title,
        "",
        "Las dos columnas salen de dos ejecuciones completas del módulo sobre las mismas",
        "barras M1: no se ha copiado ninguna cifra del informe anterior. Lo que cambia",
        "entre ellas son dos cosas a la vez, y por eso se presentan juntas:",
        "",
        f"  provisional : {baseline.provisional_description}",
        f"  definitivo  : {baseline.definitive_description}",
        "",
        _archive_note(baseline),
        "",
        "El corte de sesión cambia las velas, así que en Diario y H4 no se comparan dos",
        "lecturas del mismo gráfico: se comparan dos gráficos. En H1 las velas son las",
        "mismas —la rejilla de horas en punto no depende del corte— y todo lo que se",
        "mueve ahí es cosa del ancla.",
        "",
    ]
    for timeframe, table in baseline.per_timeframe.items():
        lines += ["", f"--- {timeframe} ---", "", render_table(table), ""]
    lines += [
        "",
        "--- Las tres temporalidades a la vez ---",
        "",
        render_table(baseline.alignment),
        "",
        f"`{ALIGNMENT_METRIC}` se lee sobre la rejilla de H1, mirando en cada cierre el",
        "último cierre ya publicado de H4 y del diario.",
    ]
    return "\n".join(lines) + "\n"


def _archive_note(baseline: BaselineComparison) -> str:
    if baseline.provisional_matches_archive:
        return (
            f"La corrida provisional reconstruida da el hash {baseline.provisional_hash}, "
            "que es el\narchivado: la columna `provisional` es exactamente la que se "
            "publicó antes."
        )
    return (
        f"AVISO: la corrida provisional reconstruida da el hash {baseline.provisional_hash} y "
        f"el\narchivado era {PROVISIONAL_HASH}. La columna `provisional` NO es la que se "
        "publicó:\nalgo más ha cambiado en el módulo y la comparación no dice lo que "
        "parece decir."
    )


# --- A. Zona horaria --------------------------------------------------------


def _timezone_block(run: ImpulseRun) -> str:
    audit = run.audit
    title = section("A. Verificación empírica de zona horaria (§1.1)")
    if audit is None:
        return (
            f"{title}\n\n"
            "NO EJECUTADA. La fase 1 exige verificarla antes de calcular nada: un offset\n"
            "horario equivocado no da error, da impulsos distintos a los de la pantalla.\n"
        )

    lines = [
        title,
        "",
        f"Veredicto (A.1 y A.2, bloqueantes): {'OK' if audit.ok else 'FALLO'}",
        f"Barras del histórico: {audit.bars:,} de {audit.resolution_minutes} min   ·   "
        f"{audit.first_bar} → {audit.last_bar}",
        "",
        section("A.1 Hueco de fin de semana", level=2),
        "",
        "Debe empezar en viernes y terminar en domingo UTC. Las paradas que no lo hacen",
        "son festivos, que también cierran el mercado.",
        "",
        f"  paradas de más de 12 h detectadas : {audit.gaps_found:,}",
        f"  empiezan en viernes               : {audit.gaps_starting_friday:,}",
        f"  terminan en domingo               : {audit.gaps_ending_sunday:,}",
        "",
        render_rows(
            ["día de la semana", "inicios de parada", "finales de parada"],
            [
                [
                    day,
                    f"{audit.gap_start_weekday_counts.get(day, 0):,}",
                    f"{audit.gap_end_weekday_counts.get(day, 0):,}",
                ]
                for day in audit.gap_start_weekday_counts
            ],
        ),
        "",
        "Un fin de semana por año, para comprobarlo a mano:",
        "",
        _weekend_sample(audit),
        "",
        section("A.2 Pico de volatilidad diario", level=2),
        "",
        f"Máximo del rango medio por minuto: {audit.peak_minute_utc} UTC "
        f"({audit.peak_mean_range:.4f} USD de rango medio).",
        f"Hora UTC con más rango medio: {audit.peak_hour_utc}.",
        _resolution_note(audit),
        "",
        "Rango medio por hora UTC (todo el histórico):",
        "",
        _hour_table(audit),
        "",
        "Los cinco minutos con más rango medio:",
        "",
        render_rows(
            ["minuto UTC", "rango medio (USD)"],
            [[minute, f"{value:.5f}"] for minute, value in audit.top_minutes],
        ),
        "",
        section("A.3 El pico y el horario de verano de EE. UU.", level=2),
        "",
        "El dato macro que hace el pico sale a las 8:30 de Nueva York: las 13:30 UTC en",
        "invierno y las 12:30 en verano. Que el pico se mueva esa hora —y sólo esa— es la",
        "prueba de que el histórico está en UTC y no en una zona con cambio de hora.",
        "",
        _season_verdict(audit),
        "",
        _season_table(audit),
        "",
        "La columna `puesto` dice en qué posición del perfil queda el minuto esperado. Un",
        "año donde el esperado queda segundo por milésimas no es un año desplazado: es un",
        "año en que el dato de las 10:00 de Nueva York movió más el precio que el de las",
        "8:30. Un histórico con la hora mal puesta no deja el esperado en el puesto 2.",
        "",
        section("A.4 Parámetros efectivos de la corrida", level=2),
        "",
        _effective_parameters(run),
    ]
    if audit.problems:
        lines += ["", "PROBLEMAS DETECTADOS:"]
        lines += [f"  · {problem}" for problem in audit.problems]
    return "\n".join(lines) + "\n"


def _weekend_sample(audit: TimezoneAudit) -> str:
    gaps = audit.yearly_gaps or audit.sample_gaps
    if not gaps:
        return "  (ninguna)"
    return render_rows(
        ["última barra antes (UTC)", "día", "primera barra después (UTC)", "día", "horas"],
        [
            [
                str(gap.last_before),
                _weekday(gap.last_before),
                str(gap.first_after),
                _weekday(gap.first_after),
                f"{gap.hours:.1f}",
            ]
            for gap in gaps
        ],
    )


def _weekday(stamp: datetime) -> str:
    from chronos.application.structure.timezone_audit import WEEKDAY_NAMES

    return WEEKDAY_NAMES[stamp.weekday()]


def _hour_table(audit: TimezoneAudit) -> str:
    if not audit.hour_profile:
        return "(sin datos)"
    peak = max(audit.hour_profile)
    return render_rows(
        ["hora UTC", "rango medio (USD)", "perfil"],
        [
            [
                f"{hour:02d}:00",
                f"{value:.4f}",
                "#" * max(1, round(40 * value / peak)) if peak > 0 else "",
            ]
            for hour, value in enumerate(audit.hour_profile)
        ],
    )


def _season_verdict(audit: TimezoneAudit) -> str:
    shift = audit.dst_shift_minutes
    if shift is None:
        return (
            "El histórico no cubre los dos regímenes horarios: no hay desplazamiento que\n"
            "medir. Diagnóstico no concluyente (no bloquea la fase)."
        )
    whole = audit.whole_history_seasons
    assert whole is not None and whole.daylight is not None and whole.standard is not None
    verdict = "COHERENTE" if audit.dst_shift_ok else "REVISAR"
    return (
        f"{verdict}: sobre el histórico entero el pico de verano cae en "
        f"{whole.daylight.peak_minute_utc} UTC y el de invierno en "
        f"{whole.standard.peak_minute_utc} UTC ({shift} min de diferencia; se esperan 60)."
    )


def _season_table(audit: TimezoneAudit) -> str:
    rows: list[list[str]] = []
    for season in (*audit.seasons, audit.whole_history_seasons):
        if season is None:
            continue
        rows.extend(_season_rows(season))
    if not rows:
        return "(sin datos)"
    return render_rows(
        ["tramo", "régimen", "barras", "pico UTC", "esperado", "puesto", "rango medio"],
        rows,
    )


def _season_rows(season: SeasonPeaks) -> list[list[str]]:
    rows = []
    for label, peak in (("verano (EDT)", season.daylight), ("invierno (EST)", season.standard)):
        if peak is None:
            rows.append([season.label, label, "0", "n/d", "n/d", "n/d", "n/d"])
            continue
        rows.append(
            [
                season.label,
                label,
                f"{peak.bars:,}",
                peak.peak_minute_utc,
                peak.expected_minute_utc,
                f"{peak.expected_rank}",
                f"{peak.peak_mean_range:.4f}",
            ]
        )
    return rows


def _effective_parameters(run: ImpulseRun) -> str:
    config = run.config
    audit = run.audit
    anchor = config.aggregation.session_anchor
    rows = [
        ["D_SESSION_START", config.aggregation.describe_daily_start()],
        [
            "H4_ORIGIN",
            f"la misma sesión ({config.aggregation.describe_daily_start()}), troceada "
            "cada 4 h"
            if anchor is not None
            else f"rejilla UTC con offset {config.aggregation.h4_effective_offset_hours} h",
        ],
        [
            "H4_OFFSET_HOURS",
            f"{config.aggregation.h4_offset_hours}"
            + (" (IGNORADO: manda el ancla de sesión)" if anchor is not None else ""),
        ],
        ["Offset de H1", "0 h (rejilla de horas en punto, sin parámetro)"],
        ["Offset de M15", "0 min (cuartos de hora, sin parámetro)"],
        ["STRUCTURE_SIDE", config.structure_side],
        ["ANCHOR_MODE", config.rules.anchor_mode.value],
        ["LEG_START_MODE", config.rules.leg_start_mode.value],
        ["SEED_MODE", config.rules.seed_mode.value],
        ["DOJI_BREAK_MODE", config.rules.doji_break_mode.value],
        ["warmup_bars", f"{config.rules.warmup_bars}"],
        ["atr_period", f"{config.rules.atr_period}"],
        [
            "Rango del histórico",
            f"{audit.first_bar} → {audit.last_bar}" if audit else "n/d",
        ],
        ["Recorte configurado", f"start={config.data.start} · end={config.data.end}"],
        ["config_hash", run.config_hash],
        ["config_hash provisional (sustituido)", PROVISIONAL_HASH],
    ]
    return render_rows(["parámetro", "valor efectivo"], rows)


def _resolution_note(audit: TimezoneAudit) -> str:
    """Avisa cuando el histórico no da para la comprobación fina de §1.1."""
    if audit.resolution_minutes <= 1:
        return "Perfil calculado sobre barras de 1 minuto: la comprobación es la fina de §1.1."
    return (
        f"AVISO: el histórico tiene barras de {audit.resolution_minutes} min, así que el pico\n"
        f"sólo puede caer en múltiplos de ese paso y la tolerancia se ha ampliado a "
        f"±{audit.effective_tolerance_minutes} min.\n"
        "La comprobación fina de las 13:30 UTC exige un histórico M1 "
        "(`chronos data dukascopy -g m1`)."
    )


# --- B. Ancla ---------------------------------------------------------------


def _anchor_block(anchors: AnchorComparison | None) -> str:
    title = section("B. Cierre de R-02 — ancla A1 frente a A2")
    if anchors is None:
        return (
            f"{title}\n\n"
            "NO EJECUTADA: R-02 está cerrado. El propietario ha decidido "
            "A1_last_counter_body\nmirando sus capturas, así que la comparativa ya no "
            "decide nada. A2 sigue disponible\npor configuración para regresión, y la "
            "distancia entre las dos candidaturas dentro\nde esta misma corrida está en "
            "C.9.\n"
        )

    lines = [
        title,
        "",
        "El módulo se ha ejecutado ENTERO dos veces, una con cada modo de ancla, sobre las",
        "mismas velas. No se comparan dos números dentro de una corrida: se comparan dos",
        "corridas. El ancla es uno de los dos niveles que rompen el ID, así que moverla",
        "puede adelantar una ROTURA_EN_CONTRA y desplazar todo lo que venga detrás.",
        "",
        "La cifra decisiva es `rotura movida`: impulsos que nacen en el mismo instante en",
        "las dos corridas y sin embargo mueren en otro.",
        "",
        render_rows(
            [
                "temporalidad", "impulsos A1", "impulsos A2", "nacen igual",
                "ancla idéntica", "rotura movida", "no coinciden",
            ],
            [
                [
                    item.timeframe,
                    f"{item.impulses_a1:,}",
                    f"{item.impulses_a2:,}",
                    f"{item.aligned:,}",
                    f"{item.identical_anchor:,}",
                    f"{item.changed_break:,}",
                    f"{item.unaligned:,}",
                ]
                for item in anchors.per_timeframe.values()
            ],
        ),
        "",
    ]
    for item in anchors.per_timeframe.values():
        lines += [
            item.verdict,
            "",
            f"|A1 - A2| en {item.timeframe}, por año y en las tres unidades:",
            "",
            render_table(
                item.gap_by_year,
                formats={
                    "n": _COUNT,
                    "difieren": _COUNT,
                    "mediana_usd": ",.4f",
                    "p90_usd": ",.4f",
                    "max_usd": ",.4f",
                    "max_pct_precio": ".4%",
                    "max_atr": ",.3f",
                },
            ),
            "",
        ]
    lines += [
        "A1 y A2 salen de dos velas consecutivas, así que coinciden exactamente cuando el",
        "open de una es el close de la anterior. Conviene no dar eso por hecho: el",
        "descargador tira los minutos sin negociación, de modo que en este histórico la",
        "mayoría de las velas abre con un hueco respecto a la anterior y las dos",
        "candidaturas son números distintos. La columna `ancla idéntica` de la tabla de",
        "arriba dice en cuántos impulsos coinciden de verdad.",
    ]
    return "\n".join(lines) + "\n"


# --- Agregación y parámetros abiertos ---------------------------------------


def _aggregation_block(run: ImpulseRun) -> str:
    aggregation = run.config.aggregation
    lines = [
        section("Agregación M1 → H4 y Diario (§1.2)"),
        "",
        f"H4_OFFSET_HOURS  = {aggregation.h4_offset_hours} "
        f"(efectivo dentro del ciclo de 4 h: {aggregation.h4_effective_offset_hours})",
        f"D_SESSION_START  = {aggregation.describe_daily_start()}",
        "",
        "open = open de la primera M1 · high = máx de highs · low = mín de lows ·",
        "close = close de la última M1. La barra final incompleta se descarta.",
    ]
    if run.aggregation_notes:
        lines += ["", *[f"  · {note}" for note in run.aggregation_notes]]

    charts = run.config.charts
    lines += [
        "",
        "Reparto de gráficos (el primero de cada lista es el principal: de él salen",
        "el sombreado del limbo y los marcadores):",
        "",
        render_rows(
            ["gráfico", "impulsos que dibuja", "detector propio"],
            [
                [
                    chart,
                    " + ".join(charts.overlays(chart)),
                    "sí" if chart in charts.detected else "no",
                ]
                for chart in charts.charts
            ],
        ),
    ]
    return "\n".join(lines) + "\n"


def _open_decisions_block(run: ImpulseRun) -> str:
    lines = [
        section("Parámetros — los decide el propietario, no el motor"),
        "",
        "CERRADOS por el propietario (son los que definen esta línea base):",
        "",
    ]
    lines += [f"  · {decision}" for decision in run.config.closed_decisions()]
    lines += [
        "",
        "ABIERTOS todavía. El motor NO ha elegido ninguno por criterio propio: son los",
        "que había en la configuración.",
        "",
    ]
    lines += [f"  · {decision}" for decision in run.config.open_decisions()]
    return "\n".join(lines) + "\n"


# --- C. Censo y estadística -------------------------------------------------


def _census_block(statistics: ImpulseStatistics) -> str:
    per_timeframe = statistics.per_timeframe
    lines = [
        section("C. Censo y estadística de los impulsos"),
        "",
        "Una tabla por temporalidad y desglose por año en todo. Los impulsos del",
        "calentamiento no se publican y no entran en ninguna cifra de esta sección.",
        "",
        render_rows(
            ["temporalidad", "barras", "impulsos publicados"],
            [
                [name, f"{stats.bars:,}", f"{stats.impulses:,}"]
                for name, stats in per_timeframe.items()
            ],
        ),
        "",
        section("C.1 Censo por año y dirección", level=2),
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(
                stats.counts_by_year,
                formats={"alcistas": _COUNT, "bajistas": _COUNT, "total": _COUNT},
            ),
        ),
        section("C.2 Duración del ID vigente (barras)", level=2),
        *_per_timeframe(
            per_timeframe, lambda stats: render_table(stats.id_duration, formats=_BAR_FORMATS)
        ),
        section("C.3 Limbo: duración, peso en el histórico y episodios", level=2),
        "",
        "El limbo es un estado legítimo: el ID anterior ha muerto y el nuevo aún no",
        "existe. Lo que mide esta tabla es cuánto tiempo el sistema está sin sesgo.",
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(stats.limbo_duration, formats=_BAR_FORMATS)
            + "\n\n"
            + render_table(
                stats.limbo_share,
                formats={
                    "barras": _COUNT,
                    "barras_limbo": _COUNT,
                    "pct_limbo": ".2%",
                    "episodios": _COUNT,
                },
            ),
        ),
        section("C.4 Sesgos simultáneos de las tres temporalidades", level=2),
        "",
        "Leído sobre la rejilla de la temporalidad más fina: en cada uno de sus cierres se",
        "mira el último cierre ya publicado de las mayores, nunca el que aún no ha llegado.",
        "Es la métrica que acota cuántas oportunidades alineadas puede haber.",
        "",
        render_table(
            statistics.alignment,
            formats={
                "barras": _COUNT,
                "pct_tres_vigentes": ".2%",
                "pct_alguna_en_limbo": ".2%",
                "pct_misma_direccion": ".2%",
            },
        ),
        "",
        section("C.5 Tamaño del impulso", level=2),
        "",
        "En USD, en ATR previo y en % del precio, lado a lado por año. Las cifras en",
        "dólares de 2018 y de 2025 no son comparables entre sí; las de ATR sí.",
        *_per_timeframe(
            per_timeframe,
            lambda stats: "En USD:\n"
            + render_table(stats.range_usd, formats=_SIZE_USD)
            + "\n\nEn ATR previo:\n"
            + render_table(stats.range_atr, formats=_SIZE_ATR)
            + "\n\nEn % del precio del ancla:\n"
            + render_table(stats.range_pct, formats=_SIZE_PCT),
        ),
        section("C.6 Roturas de salida", level=2),
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(stats.breaks_by_year, formats=_BREAK_FORMATS)
            + "\n\nPor dirección del ID roto:\n"
            + render_table(stats.breaks_by_direction, formats=_BREAK_FORMATS),
        ),
        section("C.7 Las velas que constituyen", level=2),
        "",
        "Cuerpo de la vela contraria que da vida al ID, medido en ATR previo. La columna",
        f"`bajo_0_1_atr` cuenta los impulsos nacidos de un cuerpo menor que "
        f"{TINY_BODY_ATR:g} x ATR: velas que en la pantalla del propietario apenas se ven.",
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(
                stats.constituting_bodies,
                formats={
                    "n": _COUNT,
                    "mediana": ",.3f",
                    "p10": ",.3f",
                    "p25": ",.3f",
                    "bajo_0_1_atr": _COUNT,
                    "pct_bajo_0_1_atr": ".1%",
                },
            )
            + "\n\nDojis del histórico y cuerpos del decil más pequeño:\n"
            + render_table(
                stats.bodies_by_year,
                formats={
                    "barras": _COUNT,
                    "dojis": _COUNT,
                    "impulsos": _COUNT,
                    "por_cuerpo_minimo": _COUNT,
                    "pct_minimo": ".1%",
                },
            )
            + f"\n\nUmbral del decil (p10 de los cuerpos no nulos): "
            f"{stats.tiny_body_threshold:.4f} USD"
            + f"\nDojis que cayeron en limbo y por tanto lo prolongaron: "
            f"{stats.diagnostics.get('dojis_en_limbo', 0):,}",
        ),
        section("C.8 Latigazo direccional", level=2),
        "",
        "Un episodio son tres ID consecutivos con direcciones A, B, A: el sesgo se invirtió",
        "y se deshizo. Las columnas dicen en cuántas barras se deshizo, contando de",
        "constitución a constitución.",
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(
                stats.whipsaws,
                formats={
                    "episodios": _COUNT,
                    **{f"hasta_{window}": _COUNT for window in WHIPSAW_WINDOWS},
                },
            ),
        ),
        section("C.9 Anclas dentro de la corrida (extra: el cierre no lo pide)", level=2),
        "",
        "Distancia entre las dos candidaturas de ancla calculadas en la misma corrida. La",
        "comparación que decide R-02 es la de la sección B, que ejecuta el módulo dos veces.",
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_table(stats.anchor_gap, formats=_PRICE_FORMATS)
            + "\n\n"
            + stats.anchor_verdict,
        ),
        section("C.10 Diagnósticos del detector (extra: el cierre no lo pide)", level=2),
        *_per_timeframe(
            per_timeframe,
            lambda stats: render_rows(
                ["contador", "valor"],
                [[name, f"{value:,}"] for name, value in sorted(stats.diagnostics.items())],
            ),
        ),
    ]
    return "\n".join(lines) + "\n"


_BREAK_FORMATS = {
    "a_favor": _COUNT,
    "en_contra": _COUNT,
    "sin_romper": _COUNT,
    "total": _COUNT,
    "ratio_favor_contra": ",.2f",
}


def _per_timeframe(
    per_timeframe: dict[str, TimeframeStatistics],
    render: Callable[[TimeframeStatistics], str],
) -> list[str]:
    """Repite el mismo bloque para cada temporalidad, con su encabezado."""
    lines: list[str] = []
    for name, stats in per_timeframe.items():
        lines += ["", f"--- {name} ---", "", render(stats), ""]
    return lines


# --- R-36 -------------------------------------------------------------------


def _counter_extreme_block(run: ImpulseRun) -> str:
    frame = counter_colour_extremes(run)
    lines = [
        section("R-36 · Extremo sobre vela de color contrario — LIMITACIÓN CONOCIDA"),
        "",
        "El defecto NO está corregido y no se corrige en esta corrida. A1 arregla el",
        "ANCLA —con `A1_last_counter_body` su color es correcto por construcción— y no",
        "toca el EXTREMO, que es otro nivel y otra vela.",
        "",
        "La regla del propietario dice que el extremo de un impulso alcista lo fija una",
        "vela verde y el de uno bajista, una roja. Estos son los ID en los que no ocurre,",
        "con la configuración definitiva:",
        "",
        render_table(
            frame,
            formats={
                "id_publicados": _COUNT,
                "extremo_color_contrario": _COUNT,
                "pct": ".2%",
                "en_la_vela_de_arranque_de_pierna": _COUNT,
                "en_otra_vela": _COUNT,
            },
        ),
        "",
        "La vela de arranque de pierna es la única que la máquina adopta sin mirar su",
        "color: cualquier otra vela contraria constituiría el ID en el acto y no llegaría",
        "a fijar nada. Por eso la penúltima columna dice si la puerta por la que entran",
        "los casos sigue siendo la de R-36 (`LEG_START_MODE`) o si hay otra.",
        "",
        "Queda anotado como limitación conocida de la fase 1. `L2_siguiente_barra` y",
        "`L3_extremo_solo_color_valido` se midieron y se descartaron: L2 multiplicaba el",
        "defecto y L3 producía impulsos de rango negativo.",
    ]
    return "\n".join(lines) + "\n"


# --- D. Lateralización ------------------------------------------------------


def _lateralization_block(run: ImpulseRun, study: LateralizationStudy) -> str:
    lines = [
        section("D. Firma de lateralización (descriptiva: no cambia ninguna regla)"),
        "",
        "El propietario define la lateralización como: el precio toca la parte alta del ID,",
        "toca la parte baja, y repite. Los límites no son parámetros nuevos: son el ancla y",
        "el extremo que el módulo ya calcula.",
        "",
        "  TOQUE_MECHA     la barra alcanza el nivel pero cierra dentro del rango",
        "  ROTURA_FALLIDA  la barra cierra fuera y la siguiente vuelve a cerrar dentro",
        "  ROTURA_REAL     cierra fuera y no vuelve: es la rotura que mata al ID",
        "",
        "AVISO DE LECTURA, importante para todo lo que sigue: un ID muere en el primer",
        "cierre más allá de uno de sus límites, así que durante su vigencia una",
        "ROTURA_FALLIDA es casi imposible por construcción —sólo puede darla un doji con",
        "D1_doji_no_rompe—. Los recuentos de rotura fallida saldrán en cero o casi. No es",
        "un fallo de la medición: es lo que la regla escrita implica. Los contactos que",
        "quedan son, en la práctica, toques de mecha.",
        "",
        section("D.1 Contactos por año y por límite", level=2),
        "",
        "El detalle ID a ID —con la geometría de cada vela— está en `contactos.csv`.",
        "`sin_contacto` cuenta los ID que vivieron y murieron sin rozar sus límites.",
        *_per_lateralization(study, lambda m: render_table(
            contacts_by_year(m),
            formats={
                "impulsos": _COUNT,
                "mecha_sup": _COUNT,
                "mecha_inf": _COUNT,
                "fallida_sup": _COUNT,
                "fallida_inf": _COUNT,
                "mecha_por_id": ",.2f",
                "sin_contacto": _COUNT,
            },
        )),
        section(
            f"D.2 ID que cumplen la firma (≥{SIGNATURE_MIN_TOUCHES} contactos arriba "
            f"y ≥{SIGNATURE_MIN_TOUCHES} abajo)",
            level=2,
        ),
        *_per_lateralization(study, lambda m: render_table(
            signature_by_year(m),
            formats={
                "impulsos": _COUNT,
                "cumplen_firma": _COUNT,
                "pct": ".2%",
                "solo_arriba": _COUNT,
                "solo_abajo": _COUNT,
            },
        )),
        section("D.3 Las dos poblaciones cara a cara", level=2),
        *_per_lateralization(study, lambda m: render_table(
            populations(m),
            formats={
                "impulsos": _COUNT,
                "duracion_mediana": ",.1f",
                "rango_atr_mediano": ",.3f",
                "contactos_medianos": ",.1f",
                "salida_a_favor": _COUNT,
                "salida_en_contra": _COUNT,
                "sin_romper": _COUNT,
            },
        )),
        _degenerates_block(study),
        section("D.5 Vuelta al nivel del 50 % tras la rotura real", level=2),
        "",
        "El nivel del 50 % es el punto medio entre ancla y extremo. Se cuenta que el precio",
        "ha vuelto cuando una barra lo contiene (low ≤ 50 % ≤ high), dentro de las "
        f"{', '.join(str(h) for h in REVISIT_HORIZONS)} barras siguientes a la rotura.",
        "",
        "`vuelven_N` es la proporción que vuelve alguna vez; `toques_N` es cuántas barras",
        "de media pasan por el nivel. La segunda dice si el precio lo cruza y sigue o si",
        "se queda dando vueltas alrededor.",
        *_per_lateralization(study, lambda m: render_table(
            revisit_summary(m),
            formats={
                "impulsos": _COUNT,
                **{f"vuelven_{horizon}": ".1%" for horizon in REVISIT_HORIZONS},
                **{f"toques_{horizon}": ",.2f" for horizon in REVISIT_HORIZONS},
            },
        )),
        _detail_block(run, study),
    ]
    return "\n".join(lines) + "\n"


def _degenerates_block(study: LateralizationStudy) -> str:
    lines = [
        section("D.4 LA PREGUNTA CLAVE — los ID enanos, ¿síntoma o problema aparte?", level=2),
        "",
        f"De los ID con rango inferior a {DEGENERATE_RANGE_ATR:g} ATR, qué proporción tiene",
        "pegado —justo antes o justo después— un ID que sí cumple la firma de",
        "lateralización. Si es alta, los impulsos enanos son un síntoma de lateralización y",
        "no hacen falta umbrales; si es baja, son un problema aparte.",
        "",
        "La columna `resto` es el mismo cálculo sobre los ID que NO son enanos. Sin ella la",
        "cifra no se puede leer: si todos los ID tuvieran un vecino con firma, el dato de",
        "los enanos no diría nada.",
        "",
    ]
    rows = []
    for measurement in study.per_timeframe.values():
        verdict = degenerate_verdict(measurement)
        rows.append(
            [
                verdict.timeframe,
                f"{verdict.impulses:,}",
                f"{verdict.degenerates:,}",
                f"{verdict.degenerate_share:.1%}",
                f"{verdict.next_to_signature:,}",
                f"{verdict.share:.1%}",
                f"{verdict.baseline_share:.1%}",
            ]
        )
    lines += [
        render_rows(
            [
                "temporalidad", "impulsos", "enanos", "% enanos",
                "enanos con vecino con firma", "% enanos", "% resto",
            ],
            rows,
        ),
        "",
        "Se presenta la cifra; la decisión sobre si hace falta o no una palanca correctora",
        "es del propietario.",
    ]
    return "\n".join(lines) + "\n"


def _detail_block(run: ImpulseRun, study: LateralizationStudy) -> str:
    lines = [
        section(
            f"D.6 Ficha detallada: los ID de {DETAIL_MONTH} en {DETAIL_TIMEFRAME}",
            level=2,
        ),
        "",
    ]
    measurement = study.per_timeframe.get(DETAIL_TIMEFRAME)
    analysis = run.analyses.get(DETAIL_TIMEFRAME)
    if measurement is None or analysis is None:
        return "\n".join([*lines, "(esta corrida no incluye esa temporalidad)"]) + "\n"

    frame = detail(measurement, analysis, _detail_ids(analysis))
    if frame.empty:
        return "\n".join([*lines, f"(no hay ID publicados en {DETAIL_MONTH})"]) + "\n"

    lines += [
        render_table(
            frame,
            formats={
                "ancla": ",.4f",
                "extremo": ",.4f",
                "rango_atr": ",.3f",
                "mecha_sup": _COUNT,
                "mecha_inf": _COUNT,
                "fallida_sup": _COUNT,
                "fallida_inf": _COUNT,
            },
        ),
        "",
        _detail_verdict(frame),
    ]
    return "\n".join(lines) + "\n"


def _detail_ids(analysis: TimeframeAnalysis) -> tuple[int, ...]:
    """ID publicados que se constituyeron en el mes de la ficha.

    El mes se compara en UTC, que es la marca con la que se etiquetan las velas:
    con el corte anclado a la sesión, la vela del último domingo de noviembre ya
    lleva fecha de diciembre.
    """
    year, month = (int(part) for part in DETAIL_MONTH.split("-"))
    return tuple(
        impulse.id_num
        for impulse in analysis.published
        if (impulse.ts_constitution.year, impulse.ts_constitution.month) == (year, month)
    )


def _detail_verdict(frame: pd.DataFrame) -> str:
    meeting = int((frame["cumple_firma"] == "sí").sum())
    contacts = int(
        frame[["mecha_sup", "mecha_inf", "fallida_sup", "fallida_inf"]].to_numpy().sum()
    )
    return (
        f"El conjunto suma {contacts} contactos y {meeting} de los {len(frame)} ID cumplen la "
        "firma por separado. Cumplirla es cosa de cada ID: un tramo lateral repartido entre "
        "varios ID pequeños no la cumple en ninguno, y ése es justo el caso que D.4 mide."
    )


def _per_lateralization(
    study: LateralizationStudy, render: Callable[[TimeframeLateralization], str]
) -> list[str]:
    lines: list[str] = []
    for name, measurement in study.per_timeframe.items():
        lines += ["", f"--- {name} ---", "", render(measurement), ""]
    return lines


# --- E. Geometría -----------------------------------------------------------


def _geometry_block() -> str:
    return (
        section("E. Registro de geometría para el futuro")
        + "\n\n"
        + "Cada registro de rotura y de contacto lleva estos campos:\n\n"
        + "\n".join(f"  · {column}" for column in GEOMETRY_COLUMNS)
        + "\n\n"
        "Se persisten en `eventos_rotura.csv` y en `contactos.csv` y **no los lee ninguna\n"
        "regla del dominio**: se calculan en `application/structure/geometry.py`, que el\n"
        "paquete `domain/` no importa. Servirán para estudiar más adelante si un intento de\n"
        "rotura rechazado con mechazo es confirmación, sin recalcular el histórico entonces.\n\n"
        "`cierre_mas_alla_usd` va con signo: positivo si la vela cerró fuera del límite,\n"
        "negativo si cerró dentro. Así un TOQUE_MECHA se distingue de una ROTURA_REAL sin\n"
        "mirar ninguna otra columna.\n"
    )


def _scope_block(run: ImpulseRun) -> str:
    counts = " / ".join(
        f"{timeframe} {len(analysis.impulses):,}"
        for timeframe, analysis in run.analyses.items()
    )
    return (
        section("Alcance de esta fase")
        + "\n\n"
        + "Esta fase detecta el impulso dominante y nada más. No hay último/penúltimo,\n"
        "ni RSI, ni Fibonacci, ni patrones, ni zonas, ni señales, ni entradas, ni stops,\n"
        "ni targets, ni medición de rentabilidad. No se ha optimizado ningún parámetro\n"
        "y no se ha implementado ningún umbral de tamaño ni de distancia de rotura.\n\n"
        f"Línea base definitiva de esta corrida ({run.config_hash}): {counts} impulsos\n"
        "detectados. Cualquier cambio que la mueva es una regresión, y hay un test que\n"
        "la fija.\n"
    )


def render_evidence(evidence: Evidence) -> str:
    """Sección G en texto plano: esperado y obtenido, uno al lado del otro."""
    blocks = [
        section("G. Evidencia de las comprobaciones de la fase 1"),
        "",
        "Cada línea trae el valor esperado —escrito a mano antes de correr el motor— y",
        "el obtenido en esta misma corrida. Sin resúmenes: las dos columnas.",
        "",
    ]
    for group in evidence.groups:
        blocks += [
            section(f"{group.title}   [{'OK' if group.ok else 'FALLO'}]", level=2),
            "",
            group.note,
            "",
            render_rows(
                ["", "caso", "esperado", "obtenido"],
                [
                    ["ok" if check.ok else "FALLO", check.name, check.expected, check.obtained]
                    for check in group.checks
                ],
            ),
            "",
        ]
    veredicto = "TODO OK" if evidence.ok else f"{len(evidence.failures)} COMPROBACIONES FALLAN"
    blocks.append(f"Veredicto de la sección G: {veredicto}")
    return "\n".join(blocks) + "\n"


def render_impulse_table(table: pd.DataFrame, limit: int = 40) -> str:
    """Vista rápida de la tabla de impulsos para el terminal."""
    if table.empty:
        return "(sin impulsos)"
    columns = [
        "id_num", "timeframe", "direccion", "ts_constitucion", "ts_fin",
        "n_barras_limbo", "n_barras_id", "precio_ancla", "precio_extremo",
        "rango_usd", "tipo_rotura_salida", "estado",
    ]
    view = table[columns].tail(limit)
    return render_table(
        view,
        formats={
            "precio_ancla": ",.4f",
            "precio_extremo": ",.4f",
            "rango_usd": ",.4f",
            "n_barras_limbo": _COUNT,
            "n_barras_id": _COUNT,
        },
    )
