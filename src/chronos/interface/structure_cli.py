"""CLI del módulo 1: impulso dominante.

Punto de composición de la fase 1: aquí se juntan configuración, carga de bid/ask,
verificación de zona horaria, agregación M1 → H4/D, detector e informes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from chronos.application.entries.trades import EntriesRun, detect_entries
from chronos.application.structure import (
    baseline_comparison,
    break_comparison,
    break_evidence,
    zone_evidence,
)
from chronos.application.structure.anchor_comparison import AnchorComparison, compare_anchor_modes
from chronos.application.structure.baseline_comparison import (
    BaselineComparison,
    provisional_config,
)
from chronos.application.structure.config import (
    DAILY,
    H4,
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ZonesConfig,
)
from chronos.application.structure.detect_impulses import DetectDominantImpulses, ImpulseRun
from chronos.application.structure.evidence import BASELINE_HASH, PHASE1_BASELINE, collect
from chronos.application.structure.lateralization import measure
from chronos.application.structure.session_audit import (
    SessionAudit,
    audit_session,
    colour_audit,
    contrast_rows,
    cut_metrics,
    equivalent_cuts,
)
from chronos.application.structure.statistics import summarize
from chronos.application.structure.timezone_audit import TimezoneAudit, audit_timezone
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import AnchorMode, LegStartMode, OverlapPriority
from chronos.infrastructure.config.loader import ConfigError, load_impulse_config
from chronos.infrastructure.reporting.break_captures import write_break_captures
from chronos.infrastructure.reporting.break_report import render_break_report
from chronos.infrastructure.reporting.impulse_captures import (
    CaptureRequest,
    id_card_figure,
    window_figure,
)
from chronos.infrastructure.reporting.impulse_explorer import ModeVariant, render_explorer
from chronos.infrastructure.reporting.impulse_report import render_evidence, render_report
from chronos.infrastructure.reporting.impulse_writer import ImpulseReportWriter
from chronos.infrastructure.reporting.session_audit_report import (
    SessionAuditReport,
    render_session_audit,
)
from chronos.infrastructure.reporting.zone_captures import write_zone_captures
from chronos.infrastructure.reporting.zone_report import render_zone_report
from chronos.infrastructure.structure.aggregation import (
    AggregatedSeries,
    aggregate,
    minutes_covered,
)
from chronos.infrastructure.structure.loader import SidedHistory, load_history

structure_app = typer.Typer(
    help="Módulo 1: estructura de mercado (impulso dominante).", no_args_is_help=True
)
console = Console()

DEFAULT_CONFIG = Path("config/impulse.yaml")


@structure_app.command("verify-tz")
def verify_timezone(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Verifica empíricamente la zona horaria del histórico (§1.1).

    Dos hechos que no dependen de ninguna convención del bróker: el hueco semanal
    debe caer sábado y domingo UTC, y el rango medio por minuto debe tener un
    pico marcado hacia las 13:30 UTC.
    """
    with _handled():
        run_config = load_impulse_config(config)
        history = load_history(run_config.data, run_config.structure_side)
        audit = audit_timezone(history.frame, run_config.timezone_audit)
        _print_audit(audit, history)
        if not audit.ok:
            raise typer.Exit(code=1)


@structure_app.command("evidencia")
def evidence(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Imprime la evidencia de la sección G: esperado y obtenido, lado a lado.

    Los mismos casos que cubre pytest, pero con las dos columnas a la vista y
    corridos sobre el histórico de verdad: el día sintético en las tres
    temporalidades, las tres vías del `LookaheadError`, el módulo apagado, el
    determinismo, la independencia entre temporalidades y la línea base.
    """
    with _handled():
        run_config = load_impulse_config(config)
        history = load_history(run_config.data, run_config.structure_side)
        series, _ = _aggregate_available(history, run_config)
        collected = collect(
            run_config,
            {
                timeframe: aggregated.frame
                for timeframe, aggregated in series.items()
                if timeframe in run_config.charts.detected
            },
        )
        console.print(render_evidence(collected))
        if not collected.ok:
            raise typer.Exit(code=1)


@structure_app.command("detect")
def detect(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    report: Annotated[bool, typer.Option("--report/--no-report")] = True,
    skip_tz_audit: Annotated[
        bool,
        typer.Option(
            "--skip-tz-audit",
            help="Continuar aunque la verificación de zona horaria falle (NO recomendado)",
        ),
    ] = False,
    compare_anchors: Annotated[
        bool,
        typer.Option(
            "--comparativa-anclas/--sin-comparativa-anclas",
            help=(
                "Ejecutar la sección B (A1 frente a A2). R-02 está cerrado, así que "
                "sólo sirve para regresión. Cuesta una corrida más."
            ),
        ),
    ] = False,
    compare_baseline: Annotated[
        bool,
        typer.Option(
            "--antes-y-despues/--sin-antes-y-despues",
            help=(
                "Volver a ejecutar el módulo con la configuración provisional "
                "(A2 + corte 00:00) para la tabla de antes y después."
            ),
        ),
    ] = True,
    compare_leg_modes: Annotated[
        bool,
        typer.Option(
            "--modos-r36/--sin-modos-r36",
            help=(
                "Embeber en el explorador los tres LEG_START_MODE para poder "
                "alternarlos sobre las mismas velas. Cuesta dos corridas más."
            ),
        ),
    ] = True,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Detecta los impulsos dominantes de cada temporalidad y escribe los informes."""
    with _handled():
        run_config = load_impulse_config(config)
        if not run_config.enabled:
            console.print(
                "[yellow]El módulo de impulso dominante está desactivado "
                "(`enabled: false`). No se emite nada.[/yellow]"
            )
            return

        history = load_history(run_config.data, run_config.structure_side)
        audit = _audit_or_stop(run_config, history, skip_tz_audit)

        series, skipped = _aggregate_available(history, run_config)
        notes = [aggregated.description for aggregated in series.values()]
        notes += skipped
        notes += [
            f"{aggregated.timeframe}: descartada la barra incompleta del final "
            f"({aggregated.dropped_incomplete})"
            for aggregated in series.values()
            if aggregated.dropped_incomplete is not None
        ]

        aggregated_series = {
            timeframe: aggregated.frame for timeframe, aggregated in series.items()
        }
        run = DetectDominantImpulses(run_config).execute(
            aggregated_series,
            audit=audit,
            provenance=f"{history.provenance} · lado efectivo: {history.side}",
            aggregation_notes=notes,
        )
        statistics = summarize(run)
        lateralization = measure(run)
        # Fase 2.0. Respeta el YAML sin discutir: con `zones.enabled: false` esto
        # devuelve vacío y la corrida sale exactamente como en la fase 1.
        zones = detect_zones(run)
        # Las entradas del 2026-09-13: con `entries.enabled: false` no hay ni una
        # y la corrida sale como antes. Sólo se cuentan; no se mide rentabilidad.
        entries = detect_entries(run, zones)
        # §B: el módulo entero con el otro modo de ancla. R-02 está cerrado, así
        # que por defecto no se ejecuta; queda disponible para regresión.
        anchors = (
            compare_anchor_modes(run_config, aggregated_series) if compare_anchors else None
        )
        # Antes y después: el módulo entero con la configuración provisional
        # (ancla A2 y corte diario en 00:00), reconstruyendo sus velas.
        baseline = (
            _baseline_comparison(run_config, history, run) if compare_baseline else None
        )
        # R-36: el explorador lleva dentro los tres modos de arranque de pierna
        # para que el propietario los alterne sobre las mismas velas. Son dos
        # corridas más del módulo, igual que la comparativa de anclas.
        variants = (
            _leg_start_variants(run_config, aggregated_series) if compare_leg_modes else ()
        )

        if not quiet:
            _print_summary(run)
            _print_anchor_verdicts(anchors)
            _print_baseline(baseline)
            _print_leg_start_modes(variants)
            _print_zones(zones)
            _print_entries(entries)
        if report:
            folder = ImpulseReportWriter(run_config.reporting.output_dir).write(
                run,
                statistics=statistics,
                anchors=anchors,
                lateralization=lateralization,
                baseline=baseline,
                variants=variants,
                zones=zones,
                entries=entries,
            )
            if folder is not None:
                console.print(f"\nInforme: [bold]{folder / 'reporte.txt'}[/bold]")
                console.print(f"Explorador: [bold]{folder / 'explorador.html'}[/bold]")
                if zones.enabled:
                    console.print(f"Zonas: [bold]{folder / 'reporte_zonas.txt'}[/bold]")
        else:
            console.print(
                render_report(
                    run,
                    statistics,
                    anchors=anchors,
                    lateralization=lateralization,
                    baseline=baseline,
                )
            )


@structure_app.command("zonas-evidencia")
def zones_evidence(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Evidencia de la fase 2.0: esperado y obtenido lado a lado (§9.3).

    Los mismos casos que cubre pytest, pero con las dos columnas a la vista y
    corridos sobre el histórico de verdad: el día sintético del §6 en sus dos
    direcciones, las cuatro vías del `LookaheadError`,
    el apagado y la regresión de la línea base **con las zonas encendidas**.
    """
    with _handled():
        run_config = load_impulse_config(config)
        history = load_history(run_config.data, run_config.structure_side)
        series, _ = _aggregate_available(history, run_config)
        collected = zone_evidence.collect(
            run_config,
            {
                timeframe: aggregated.frame
                for timeframe, aggregated in series.items()
                if timeframe in run_config.charts.detected
            },
        )
        console.print(render_evidence(collected, "Z. Evidencia de la fase 2.0 · zonas UL y PUL"))
        if not collected.ok:
            raise typer.Exit(code=1)


@structure_app.command("zonas")
def zones_command(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option("--salida", "-o")] = Path("now/fase20"),
    captures: Annotated[bool, typer.Option("--capturas/--sin-capturas")] = True,
    respect_config: Annotated[
        bool,
        typer.Option(
            "--respetar-config/--forzar-zonas",
            help=(
                "Obedecer `zones.enabled` del YAML en vez de encender las zonas para "
                "esta corrida. Con las zonas apagadas el comando no tiene nada que hacer."
            ),
        ),
    ] = False,
    skip_tz_audit: Annotated[bool, typer.Option("--skip-tz-audit")] = False,
) -> None:
    """Fase 2.0: detecta y dibuja las zonas UL y PUL de cada impulso (§7 y §8).

    **Sólo detección.** No cambia la detección del impulso dominante, no cambia
    la regla de rotura —que sigue siendo por línea— y no emite ninguna señal. La
    corrida verifica de paso que el recuento de impulsos y el `config_hash` son
    los mismos con las zonas puestas que sin ellas.

    Este comando enciende las zonas para su propia corrida, porque correrlo con
    ellas apagadas no produciría nada; `chronos structure detect` sigue
    obedeciendo el YAML sin discutir. Con `--respetar-config` se comporta igual
    que `detect`.
    """
    with _handled():
        run_config = load_impulse_config(config)
        if not run_config.enabled:
            console.print(
                "[yellow]El módulo de impulso dominante está desactivado "
                "(`enabled: false`). Sin impulsos no hay zonas.[/yellow]"
            )
            return
        if not respect_config and not run_config.zones.enabled:
            console.print(
                "[dim]Zonas encendidas para esta corrida (el YAML las trae apagadas). "
                "Con --respetar-config se obedece el fichero.[/dim]"
            )
            run_config = replace(run_config, zones=ZonesConfig(enabled=True))
        if not run_config.zones.enabled:
            console.print(
                "[yellow]`zones.enabled: false` y se ha pedido respetar la "
                "configuración: no hay nada que calcular.[/yellow]"
            )
            return

        history = load_history(run_config.data, run_config.structure_side)
        audit = _audit_or_stop(run_config, history, skip_tz_audit)
        series, skipped = _aggregate_available(history, run_config)
        aggregated = {tf: item.frame for tf, item in series.items()}

        run = DetectDominantImpulses(run_config).execute(
            aggregated,
            audit=audit,
            provenance=f"{history.provenance} · lado efectivo: {history.side}",
            aggregation_notes=[item.description for item in series.values()] + skipped,
        )
        zones = detect_zones(run)

        # La comprobación que el enunciado exige a la vista: con las zonas
        # apagadas sobre las mismas velas tienen que salir los mismos impulsos.
        without = DetectDominantImpulses(
            replace(run_config, zones=ZonesConfig(enabled=False))
        ).execute(aggregated)
        _print_zone_regression(run, without)
        _print_zones(zones)

        output.mkdir(parents=True, exist_ok=True)
        report = output / "reporte_zonas.txt"
        report.write_text(render_zone_report(run, zones), encoding="utf-8")
        # §9.3: el esperado y el obtenido, lado a lado, en la misma carpeta que
        # los números. Es el mismo panel que imprime `zonas-evidencia`.
        evidence = zone_evidence.collect(run_config, aggregated)
        (output / "evidencia_zonas.txt").write_text(
            render_evidence(evidence, "Z. Evidencia de la fase 2.0 · zonas UL y PUL"), encoding="utf-8"
        )
        if not evidence.ok:
            console.print(
                "\n[bold red]La evidencia de la fase 2.0 NO pasa.[/bold red] "
                f"Revisa {output / 'evidencia_zonas.txt'}."
            )
        zones.table().to_csv(output / "zonas.csv", index=False)
        zones.survivals().to_csv(output / "roturas_y_zonas.csv", index=False)
        console.print(f"\nInforme: [bold]{report}[/bold]")
        console.print(f"Tabla de zonas: [bold]{output / 'zonas.csv'}[/bold]")

        # El explorador con las dos capas encendidas, para que la carpeta de la
        # fase 2.0 se pueda auditar sin abrir la del informe de la fase 1.
        explorer = output / "explorador_zonas.html"
        explorer.write_text(
            render_explorer(
                run,
                run_config.reporting.max_explorer_bars,
                lateralization=measure(run),
                zones=zones,
            ),
            encoding="utf-8",
        )
        console.print(f"Explorador: [bold]{explorer}[/bold]")

        if captures:
            console.print("[dim]Capturas (Kaleido abre un navegador por imagen)...[/dim]")
            written = write_zone_captures(
                run, zones, output, session_timezone=run_config.reporting.session_timezone
            )
            console.print(f"  [dim]{len(written)} capturas en {output}[/dim]")
            console.print(f"Índice de capturas: [bold]{output / 'LEEME.txt'}[/bold]")


def _print_zone_regression(run: ImpulseRun, without: ImpulseRun) -> None:
    """Las zonas no pueden mover ni un impulso, y se enseña en vez de prometerlo."""
    table = Table(box=None, pad_edge=False)
    table.add_column("Temporalidad", style="dim")
    table.add_column("Impulsos con zonas", justify="right")
    table.add_column("Impulsos sin zonas", justify="right")
    table.add_column("", justify="left")
    ok = run.config_hash == without.config_hash
    for timeframe, analysis in run.analyses.items():
        other = len(without.analyses[timeframe].impulses)
        same = len(analysis.impulses) == other
        ok = ok and same
        table.add_row(
            timeframe,
            f"{len(analysis.impulses):,}",
            f"{other:,}",
            "[green]igual[/green]" if same else "[bold red]DISTINTO[/bold red]",
        )
    console.print("\n[bold]Las zonas no tocan la detección:[/bold]")
    console.print(table)
    console.print(
        f"[dim]hash con zonas {run.config_hash} · sin zonas {without.config_hash}[/dim]"
    )
    if not ok:
        console.print(
            "[bold red]FALLO:[/bold red] encender las zonas ha movido la detección. "
            "Eso es un defecto de la fase 2.0, no un resultado."
        )
        raise typer.Exit(code=1)


def _print_entries(entries: EntriesRun) -> None:
    """Cuántos límites y cuántas operaciones. Ni un porcentaje: se auditan en el dibujo."""
    if not entries.enabled:
        return
    counts = entries.counts()
    console.print(
        f"\nEntradas ({entries.day.label}, 1:{entries.risk_reward:g}, en {entries.source}): "
        f"{counts['LIMITES']:,} límites, {counts['ENTRADAS']:,} operaciones. "
        "Se ven en el explorador y en entradas.csv."
    )


def _print_zones(zones: ZonesRun) -> None:
    if not zones.enabled:
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("Temporalidad", style="dim")
    table.add_column("ID", justify="right")
    table.add_column("Con PUL", justify="right")
    table.add_column("Con APUL", justify="right")
    table.add_column("APUL heredado", justify="right")
    table.add_column("APUL extremo contrario", justify="right")
    table.add_column("SIN ZONA", justify="right")
    table.add_column("UL altura cero", justify="right")
    table.add_column("UL extendidos", justify="right")
    for timeframe, item in zones.per_timeframe.items():
        total = len(item.items)
        without = len(item.without_against_zone)
        table.add_row(
            timeframe,
            f"{total:,}",
            f"{len(item.with_penultimate):,}",
            f"{len(item.with_ante_penultimate):,}",
            f"{len(item.with_inherited_ante_penultimate):,}",
            f"{len(item.with_counter_extreme_ante_penultimate):,}",
            f"{without:,} ({without / total:.1%})" if total else "—",
            f"{sum(1 for zoned in item.items if zoned.last.is_flat):,}",
            f"{sum(1 for zoned in item.items if zoned.last.extended):,}",
        )
    console.print("\n[bold]Zonas de la fase 2.0:[/bold]")
    console.print(table)
    console.print(
        "[dim]El % SIN ZONA es la cifra que manda: en la fase 2.1 esos ID —los que no "
        "tienen ID anterior— se romperán por línea en el lado en contra.[/dim]"
    )


@structure_app.command("rotura-por-zona")
def break_by_zone_command(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option("--salida", "-o")] = Path("now/fase21"),
    captures: Annotated[bool, typer.Option("--capturas/--sin-capturas")] = True,
    skip_tz_audit: Annotated[bool, typer.Option("--skip-tz-audit")] = False,
) -> None:
    """Fase 2.1: la zona decide la vida y la muerte del impulso (§6, §7 y §8).

    Ejecuta el módulo **entero** dos veces sobre las mismas velas —con la rotura
    por línea y con la rotura por zona— y las compara. No reetiqueta ninguna
    tabla: salvar una rotura cambia toda la historia posterior.

    Antes de nada verifica que con `BREAK_BY_ZONE = false` sale la línea base
    exacta. Si no sale, **para y avisa**: eso sería un bug de la refactorización
    y no un resultado.

    El comando enciende la regla nueva para su propia corrida, porque correrlo
    con ella apagada no compararía nada; `chronos structure detect` sigue
    obedeciendo el YAML sin discutir.
    """
    with _handled():
        run_config = load_impulse_config(config)
        if not run_config.enabled:
            console.print(
                "[yellow]El módulo de impulso dominante está desactivado "
                "(`enabled: false`). No hay nada que romper.[/yellow]"
            )
            return

        history = load_history(run_config.data, run_config.structure_side)
        audit = _audit_or_stop(run_config, history, skip_tz_audit)
        series, skipped = _aggregate_available(history, run_config)
        aggregated = {tf: item.frame for tf, item in series.items()}
        notes = [item.description for item in series.values()] + skipped
        provenance = f"{history.provenance} · lado efectivo: {history.side}"

        # Las zonas se encienden en las dos corridas: la fase 2.0 mide y dibuja,
        # y su medición del solape hace falta con las dos reglas para el §6.8.
        base_config = replace(
            run_config,
            rules=replace(run_config.rules, break_by_zone=False),
            zones=ZonesConfig(enabled=True),
        )
        zoned_config = replace(
            run_config,
            rules=replace(run_config.rules, break_by_zone=True),
            zones=ZonesConfig(enabled=True),
        )
        other = (
            OverlapPriority.EN_CONTRA_FIRST
            if run_config.rules.overlap_priority is OverlapPriority.A_FAVOR_FIRST
            else OverlapPriority.A_FAVOR_FIRST
        )
        alternative_config = replace(
            zoned_config, rules=replace(zoned_config.rules, overlap_priority=other)
        )

        console.print("[dim]Corrida con la rotura por LÍNEA (línea base)...[/dim]")
        baseline = DetectDominantImpulses(base_config).execute(
            aggregated, audit=audit, provenance=provenance, aggregation_notes=notes
        )
        ok, note = _print_break_regression(baseline)
        if not ok:
            raise typer.Exit(code=1)

        console.print("[dim]Corrida con la rotura por ZONA...[/dim]")
        zoned = DetectDominantImpulses(zoned_config).execute(
            aggregated, audit=audit, provenance=provenance, aggregation_notes=notes
        )
        console.print(
            f"[dim]Corrida con el otro OVERLAP_PRIORITY ({other.value})...[/dim]"
        )
        alternative = DetectDominantImpulses(alternative_config).execute(aggregated)

        comparison = break_comparison.compare(
            baseline,
            zoned,
            baseline_zones=detect_zones(baseline),
            zoned_zones=detect_zones(zoned),
            alternative=alternative,
        )
        _print_break_summary(baseline, zoned, comparison)

        output.mkdir(parents=True, exist_ok=True)
        report = output / "reporte_rotura_por_zona.txt"
        report.write_text(
            render_break_report(
                baseline,
                zoned,
                comparison,
                regression_ok=ok,
                regression_note=note,
            ),
            encoding="utf-8",
        )

        evidence = break_evidence.collect(zoned_config, aggregated)
        (output / "evidencia_rotura_por_zona.txt").write_text(
            render_evidence(evidence, break_evidence.TITLE), encoding="utf-8"
        )
        if not evidence.ok:
            console.print(
                "\n[bold red]La evidencia de la fase 2.1 NO pasa.[/bold red] "
                f"Revisa {output / 'evidencia_rotura_por_zona.txt'}."
            )

        zoned.table().to_csv(output / "impulsos_regla_nueva.csv", index=False)
        baseline.table().to_csv(output / "impulsos_linea_base.csv", index=False)
        break_comparison.avoided_table(zoned).to_csv(
            output / "roturas_evitadas.csv", index=False
        )
        break_comparison.break_table(zoned).to_csv(
            output / "roturas_regla_nueva.csv", index=False
        )
        console.print(f"\nInforme: [bold]{report}[/bold]")
        console.print(f"Evidencia: [bold]{output / 'evidencia_rotura_por_zona.txt'}[/bold]")
        console.print(f"Roturas evitadas: [bold]{output / 'roturas_evitadas.csv'}[/bold]")

        explorer = output / "explorador_rotura_por_zona.html"
        explorer.write_text(
            render_explorer(
                zoned,
                run_config.reporting.max_explorer_bars,
                lateralization=measure(zoned),
                zones=detect_zones(zoned),
            ),
            encoding="utf-8",
        )
        console.print(f"Explorador: [bold]{explorer}[/bold]")

        if captures:
            console.print("[dim]Capturas (Kaleido abre un navegador por imagen)...[/dim]")
            written = write_break_captures(baseline, zoned, alternative, output)
            console.print(f"  [dim]{len(written)} capturas en {output}[/dim]")
            console.print(f"Índice de capturas: [bold]{output / 'LEEME.txt'}[/bold]")


def _print_break_regression(baseline: ImpulseRun) -> tuple[bool, str]:
    """§3: con la regla apagada tienen que salir la línea base y su hash."""
    detected = {tf: len(a.impulses) for tf, a in baseline.analyses.items()}
    published = {tf: len(a.published) for tf, a in baseline.analyses.items()}
    expected_detected = {tf: PHASE1_BASELINE[tf] for tf in detected if tf in PHASE1_BASELINE}
    expected_published = {
        tf: break_evidence.PHASE1_BASELINE_PUBLISHED[tf]
        for tf in published
        if tf in break_evidence.PHASE1_BASELINE_PUBLISHED
    }
    ok = (
        baseline.config_hash == BASELINE_HASH
        and detected == expected_detected
        and published == expected_published
    )

    table = Table(box=None, pad_edge=False)
    table.add_column("Concepto", style="dim")
    table.add_column("Esperado")
    table.add_column("Obtenido")
    table.add_column("", justify="left")
    for label, expected, obtained in (
        ("config_hash", BASELINE_HASH, baseline.config_hash),
        ("detectados", _counts(expected_detected), _counts(detected)),
        ("publicados", _counts(expected_published), _counts(published)),
    ):
        same = expected == obtained
        table.add_row(
            label,
            expected,
            obtained,
            "[green]igual[/green]" if same else "[bold red]DISTINTO[/bold red]",
        )
    console.print("\n[bold]Regresión con BREAK_BY_ZONE = false:[/bold]")
    console.print(table)

    note = (
        f"hash {baseline.config_hash} · detectados {_counts(detected)} · "
        f"publicados {_counts(published)}"
    )
    if not ok:
        console.print(
            "\n[bold red]PARADA.[/bold red] Con la regla apagada no sale la línea base. "
            "Eso es un bug de la refactorización, no un resultado: no se sigue."
        )
    return ok, note


def _counts(counts: dict[str, int]) -> str:
    return " / ".join(f"{tf} {value:,}" for tf, value in counts.items())


def _print_break_summary(
    baseline: ImpulseRun,
    zoned: ImpulseRun,
    comparison: break_comparison.BreakRuleComparison,
) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Temporalidad", style="dim")
    table.add_column("ID antes", justify="right")
    table.add_column("ID después", justify="right")
    table.add_column("Variación", justify="right")
    table.add_column("Evitadas", justify="right")
    table.add_column("Rotura por línea", justify="right")
    table.add_column("Conflictos", justify="right")
    for timeframe, analysis in zoned.analyses.items():
        before = len(baseline.analyses[timeframe].impulses)
        after = len(analysis.impulses)
        diagnostics = analysis.diagnostics
        by_line = (
            diagnostics["roturas_a_favor_por_linea"]
            + diagnostics["roturas_en_contra_por_linea"]
        )
        table.add_row(
            timeframe,
            f"{before:,}",
            f"{after:,}",
            f"{(after - before) / before:+.1%}" if before else "n/d",
            f"{len(analysis.avoided):,}",
            f"{by_line:,}",
            f"{diagnostics['conflictos_de_solape']:,}",
        )
    console.print("\n[bold]Rotura por zona · la nueva línea base:[/bold]")
    console.print(table)
    console.print(
        f"[dim]hash antes {comparison.baseline_hash} · después {comparison.zoned_hash} · "
        f"otro orden {comparison.alternative_hash}[/dim]"
    )


@structure_app.command("ficha")
def id_cards(
    ids: Annotated[list[int], typer.Option("--id", help="ID a retratar; repetible")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    timeframe: Annotated[str, typer.Option("--temporalidad", "-t")] = DAILY,
    output: Annotated[Path, typer.Option("--salida", "-o")] = Path("now"),
    prefix: Annotated[str, typer.Option("--prefijo")] = "ficha",
    context: Annotated[int, typer.Option("--contexto")] = 25,
) -> None:
    """Una captura por ID: su vida entera y las velas que fijan ancla y extremo.

    Los ID salen de la configuración vigente, así que cambiar `ANCHOR_MODE` los
    renumera. La ventana va de la constitución a la rotura, con `--contexto`
    velas a cada lado.
    """
    with _handled():
        run_config = load_impulse_config(config)
        history = load_history(run_config.data, run_config.structure_side)
        run = _run_only(run_config, history.frame, (timeframe,))
        analysis = run.analyses[timeframe]
        by_id = {impulse.id_num: impulse for impulse in analysis.published}

        missing = [number for number in ids if number not in by_id]
        if missing:
            raise DomainError(
                f"No hay ID publicado con estos números en {timeframe}: "
                f"{', '.join(str(number) for number in missing)}"
            )

        output.mkdir(parents=True, exist_ok=True)
        atr = analysis.table.set_index("id_num")["rango_atr"]
        for position, number in enumerate(ids, start=1):
            impulse = by_id[number]
            exit_note = (
                f"{impulse.exit_break_kind.value} el {impulse.ts_end:%Y-%m-%d}"
                if impulse.ts_end is not None and impulse.exit_break_kind is not None
                else "sigue vigente"
            )
            figure = id_card_figure(
                analysis,
                impulse,
                context=context,
                subtitle=(
                    f"ancla {impulse.anchor:,.4f} · extremo {impulse.extreme:,.4f} · "
                    f"rango {impulse.range_usd:,.2f} USD = "
                    f"{atr.get(number, float('nan')):.3f} ATR · "
                    f"{impulse.bars_alive(len(analysis.bars) - 1)} barras vigente · "
                    f"salida {exit_note} · "
                    f"ANCHOR_MODE = {run_config.rules.anchor_mode.value} · "
                    f"LEG_START_MODE = {run_config.rules.leg_start_mode.value} · "
                    f"hash {run.config_hash}"
                ),
            )
            destination = output / f"{prefix}_{position:02d}_id{number}.png"
            figure.write_image(str(destination), width=1600, height=900, scale=1)
            console.print(f"  [dim]{destination}[/dim]")


@structure_app.command("auditoria-sesion")
def session_audit_command(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option("--salida", "-o")] = Path("now"),
    captures: Annotated[bool, typer.Option("--capturas/--sin-capturas")] = True,
) -> None:
    """Censo de velas cortas, su impacto y los cortes de sesión alternativos (B).

    El mercado abre el domingo a las 23:00 UTC y `D_SESSION_START` decide si esa
    hora se queda sola en una vela diaria propia. Aquí se mide cuántas velas
    contienen menos mercado del que prometen, qué papel juegan en los impulsos y
    cómo cambia todo con cada corte. No se recomienda ninguno: la salida es una
    tabla de OHLC para cotejar contra TradingView.
    """
    with _handled():
        run_config = load_impulse_config(config)
        history = load_history(run_config.data, run_config.structure_side)
        detected = run_config.charts.detected

        console.print("[dim]Corrida base con el modo de ancla configurado...[/dim]")
        base_run = _run_only(run_config, history.frame, detected)
        minutes = {
            timeframe: minutes_covered(
                history.frame,
                timeframe,
                run_config.aggregation,
                pd.DatetimeIndex(base_run.analyses[timeframe].bars.index),
            )
            for timeframe in detected
        }
        audit = audit_session(base_run.analyses, minutes)

        console.print("[dim]Corrida de regresión con el otro modo de ancla...[/dim]")
        other = (
            AnchorMode.A2_FIRST_LEG_BAR
            if run_config.rules.anchor_mode is AnchorMode.A1_LAST_COUNTER_BODY
            else AnchorMode.A1_LAST_COUNTER_BODY
        )
        other_run = _run_only(
            replace(run_config, rules=replace(run_config.rules, anchor_mode=other)),
            history.frame,
            detected,
        )

        console.print("[dim]Cortes de sesión alternativos...[/dim]")
        daily_runs, daily_rows = _sweep(
            run_config,
            history.frame,
            DAILY,
            [
                (corte, replace(run_config.aggregation, d_session_start=corte))
                for corte in DAILY_SESSION_STARTS
            ],
        )
        _, h4_rows = _sweep(
            run_config,
            history.frame,
            H4,
            [
                # Los desplazamientos fijos se miden sobre la rejilla UTC, así que
                # el corte diario se fuerza a 00:00: si el YAML llevara un ancla de
                # sesión, las cuatro filas saldrían ancladas y no compararían nada.
                (
                    f"+{offset} h",
                    replace(
                        run_config.aggregation,
                        h4_offset_hours=offset,
                        d_session_start="00:00",
                    ),
                )
                for offset in H4_OFFSETS
            ]
            + [
                (corte, replace(run_config.aggregation, d_session_start=corte))
                for corte in SESSION_ANCHORS
            ],
        )

        contrast = []
        for corte, (run, aggregation) in daily_runs.items():
            bars = run.analyses[DAILY].bars
            contrast += contrast_rows(
                corte,
                bars,
                minutes_covered(
                    history.frame, DAILY, aggregation, pd.DatetimeIndex(bars.index)
                ),
                CONTRAST_DATES,
            )

        output.mkdir(parents=True, exist_ok=True)
        images = (
            _session_captures(daily_runs, output) if captures else ()
        )

        report = SessionAuditReport(
            subtitle=(
                f"{run_config.symbol} · {history.provenance} · lado {history.side} · "
                f"ANCHOR_MODE = {run_config.rules.anchor_mode.value} · "
                f"LEG_START_MODE = {run_config.rules.leg_start_mode.value} · "
                f"hash {base_run.config_hash}"
            ),
            colour_a1=pd.DataFrame(
                [colour_audit(base_run.analyses[tf]) for tf in detected]
            ),
            colour_a2=pd.DataFrame(
                [colour_audit(other_run.analyses[tf]) for tf in detected]
            ),
            audit=audit,
            cuts_daily=pd.DataFrame(daily_rows),
            cuts_h4=pd.DataFrame(h4_rows),
            contrast=pd.DataFrame(contrast),
            captures=images,
            notes=(
                f"Corrida de regresión con ANCHOR_MODE = {other.value}, "
                f"hash {other_run.config_hash}.",
                *_equivalence_notes(daily_runs),
                "Una vela es corta si contiene menos del 25 % de los minutos de M1 que su "
                "temporalidad promete (1.440 en el diario, 240 en H4, 60 en H1).",
                "Los cortes de B.3 reconstruyen las velas y vuelven a ejecutar el detector "
                "entero; no son un recuento sobre las mismas velas.",
            ),
        )

        page = output / "velas_cortas_y_cortes.html"
        page.write_text(render_session_audit(report), encoding="utf-8")
        _write_tables(report, output)
        console.print(f"\nPágina: [bold]{page}[/bold]")
        _print_session_audit(audit, daily_rows, h4_rows)


#: Anclas de sesión: el día no empieza a una hora fija de UTC sino cuando abre
#: Nueva York, de modo que el corte se mueve solo con el horario de verano
#: (22:00 UTC en invierno y 21:00 en verano con las 17:00; 23:00 y 22:00 con las
#: 18:00). Es la hipótesis del propietario sobre lo que hace su TradingView.
SESSION_ANCHORS = ("NY_17:00", "NY_18:00")

DAILY_SESSION_STARTS = ("00:00", "21:00", "22:00", "23:00", *SESSION_ANCHORS)
H4_OFFSETS = (0, 1, 2, 3)

#: Las velas diarias que el propietario cotejará contra su TradingView, y el
#: tramo de las capturas. Los elige él, no el motor.
#:
#: Las cinco primeras son los viernes y lunes que rodean a los domingos en
#: discusión: ahí el corte de sesión decide qué entra en cada vela. Las cinco
#: siguientes son miércoles, lejos de cualquier apertura o cierre semanal, y
#: sirven de control: si el OHLC de un miércoles tampoco cuadra con TradingView,
#: el problema no es el corte del domingo.
CONTRAST_DATES = (
    "2019-03-08",
    "2019-03-11",
    "2024-06-07",
    "2024-06-10",
    "2025-03-03",
    "2019-03-13",
    "2022-05-11",
    "2023-09-20",
    "2024-06-12",
    "2025-03-05",
)
CAPTURE_WINDOW = (
    pd.Timestamp("2019-03-01", tz="UTC"),
    pd.Timestamp("2019-03-20 23:59", tz="UTC"),
)


def _run_only(
    config: ImpulseConfig, frame: pd.DataFrame, timeframes: Sequence[str]
) -> ImpulseRun:
    """Agrega y detecta sólo las temporalidades pedidas, sin gráficos de contexto."""
    tuned = replace(config, charts=ChartsConfig(layout={tf: (tf,) for tf in timeframes}))
    series = {
        timeframe: aggregate(frame, timeframe, tuned.aggregation).frame
        for timeframe in timeframes
    }
    return DetectDominantImpulses(tuned).execute(series)


def _sweep(
    config: ImpulseConfig,
    frame: pd.DataFrame,
    timeframe: str,
    variants: Sequence[tuple[str, AggregationConfig]],
) -> tuple[dict[str, tuple[ImpulseRun, AggregationConfig]], list[dict[str, object]]]:
    """Una reconstrucción de velas y una detección completa por variante."""
    runs: dict[str, tuple[ImpulseRun, AggregationConfig]] = {}
    rows: list[dict[str, object]] = []
    for label, aggregation in variants:
        tuned = replace(config, aggregation=aggregation)
        run = _run_only(tuned, frame, (timeframe,))
        analysis = run.analyses[timeframe]
        counts = minutes_covered(
            frame, timeframe, aggregation, pd.DatetimeIndex(analysis.bars.index)
        )
        runs[label] = (run, aggregation)
        rows.append(cut_metrics(label, analysis, counts))
        console.print(f"  [dim]{timeframe} {label}: {rows[-1]}[/dim]")
    return runs, rows


def _equivalence_notes(
    runs: dict[str, tuple[ImpulseRun, AggregationConfig]],
) -> tuple[str, ...]:
    """Avisa de los cortes que no se pueden distinguir mirando el OHLC."""
    groups = equivalent_cuts(
        {label: run.analyses[DAILY].bars for label, (run, _) in runs.items()}
    )
    return tuple(
        f"Estos cortes producen exactamente las mismas velas diarias y sólo cambian la "
        f"marca de tiempo con que se etiquetan: {', '.join(group)}. Comparar su OHLC "
        f"contra TradingView no puede separarlos."
        for group in groups
    )


def _slug(label: str) -> str:
    """Nombre de fichero de un corte: `00:00` -> `00`, `NY_18:00` -> `NY_18`."""
    return label.replace(":00", "").replace(":", "_")


def _session_captures(
    runs: dict[str, tuple[ImpulseRun, AggregationConfig]], folder: Path
) -> tuple[Path, ...]:
    """El mismo tramo en cada corte, con la misma escala vertical."""
    windows: dict[str, tuple[int, int]] = {}
    low, high = float("inf"), float("-inf")
    for label, (run, _) in runs.items():
        bars = run.analyses[DAILY].bars
        index = pd.DatetimeIndex(bars.index)
        inside = np.flatnonzero((index >= CAPTURE_WINDOW[0]) & (index <= CAPTURE_WINDOW[1]))
        if inside.size == 0:
            continue
        first, last = int(inside[0]), int(inside[-1])
        windows[label] = (first, last)
        chunk = bars.iloc[first : last + 1]
        low = min(low, float(chunk["low"].min()))
        high = max(high, float(chunk["high"].max()))

    margin = 0.04 * (high - low)
    written: list[Path] = []
    for label, (first, last) in windows.items():
        run, _ = runs[label]
        analysis = run.analyses[DAILY]
        request = CaptureRequest(
            name=f"sesion_{_slug(label)}",
            timeframe=DAILY,
            first=first,
            last=last,
            title=(
                f"Diario · D_SESSION_START = {label} UTC · "
                f"{CAPTURE_WINDOW[0]:%Y-%m-%d} → {CAPTURE_WINDOW[1]:%Y-%m-%d}"
            ),
            context=0,
            subtitle=(
                f"{last - first + 1} velas en el tramo · "
                f"{len(analysis.table)} ID en todo el histórico · "
                f"hash {run.config_hash}"
            ),
        )
        figure = window_figure(analysis, request, y_range=(low - margin, high + margin))
        destination = folder / f"{request.name}.png"
        figure.write_image(str(destination), width=1600, height=900, scale=1)
        written.append(destination)
        console.print(f"  [dim]captura {destination}[/dim]")
    return tuple(written)


def _write_tables(report: SessionAuditReport, folder: Path) -> None:
    report.cuts_daily.to_csv(folder / "b3_cortes_diario.csv", index=False)
    report.cuts_h4.to_csv(folder / "b3_offsets_h4.csv", index=False)
    report.contrast.to_csv(folder / "b4_contraste.csv", index=False)
    for timeframe, item in report.audit.per_timeframe.items():
        item.census.to_csv(folder / f"b1_censo_{timeframe}.csv", index=False)
        item.by_weekday.to_csv(folder / f"b1_dias_{timeframe}.csv", index=False)
        item.roles.to_csv(folder / f"b2_papeles_{timeframe}.csv", index=False)


def _print_session_audit(
    audit: SessionAudit,
    daily: Sequence[dict[str, object]],
    h4: Sequence[dict[str, object]],
) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Temporalidad", style="dim")
    table.add_column("Velas", justify="right")
    table.add_column("Cortas", justify="right")
    table.add_column("Ancla", justify="right")
    table.add_column("Extremo", justify="right")
    table.add_column("Constituyen", justify="right")
    table.add_column("Rompen", justify="right")
    for timeframe, item in audit.per_timeframe.items():
        impact = item.impact
        table.add_row(
            timeframe,
            f"{item.bars:,}",
            f"{item.short_bars:,} ({item.short_share:.1%})",
            f"{int(impact['ancla_en_vela_corta']):,}",
            f"{int(impact['extremo_en_vela_corta']):,}",
            f"{int(impact['constitucion_en_vela_corta']):,}",
            f"{int(impact['rotura_en_vela_corta']):,}",
        )
    console.print("\n[bold]Velas cortas y su papel en los ID:[/bold]")
    console.print(table)
    console.print(f"[dim]{len(daily)} cortes de diario y {len(h4)} offsets de H4 comparados.[/dim]")


# --- Apoyo ------------------------------------------------------------------


def _leg_start_variants(
    config: ImpulseConfig, series: dict[str, pd.DataFrame]
) -> tuple[ModeVariant, ...]:
    """Una corrida completa por cada `LEG_START_MODE`, sobre las mismas velas.

    El modo no toca la agregación, así que las velas son idénticas en los tres y
    el explorador puede alternarlos sin cambiar de escala. Cada variante lleva su
    propia medición de contactos: mezclar los de un modo con los impulsos de otro
    enseñaría toques de límites que en ese modo no existen.
    """
    variants = []
    for mode in LegStartMode:
        tuned = replace(config, rules=replace(config.rules, leg_start_mode=mode))
        variant_run = DetectDominantImpulses(tuned).execute(series)
        variants.append(ModeVariant(run=variant_run, lateralization=measure(variant_run)))
    return tuple(variants)


def _print_leg_start_modes(variants: Sequence[ModeVariant]) -> None:
    if not variants:
        return
    console.print("\n[bold]Arranque de pierna · LEG_START_MODE (R-36):[/bold]")
    table = Table(box=None, pad_edge=False)
    table.add_column("Modo", style="dim")
    table.add_column("Hash")
    table.add_column("Impulsos", justify="right")
    table.add_column("Extremo de color contrario", justify="right")
    for variant in variants:
        analyses = variant.run.analyses.values()
        wrong = sum(
            1
            for analysis in analyses
            for impulse in analysis.impulses
            if impulse.extreme_on_counter_bar
        )
        table.add_row(
            variant.mode,
            variant.run.config_hash,
            f"{sum(len(analysis.impulses) for analysis in analyses):,}",
            f"{wrong:,}",
        )
    console.print(table)
    console.print("[dim]El explorador los lleva embebidos: se alternan sin recalcular.[/dim]")


def _aggregate_available(
    history: SidedHistory, config: ImpulseConfig
) -> tuple[dict[str, AggregatedSeries], list[str]]:
    """Agrega cada gráfico; los que el histórico no da para construir, se saltan.

    Un histórico H1 sirve para el diario, H4 y H1 pero no para M15 ni M5. Abortar
    la fase entera por eso sería peor que trabajar con lo que hay: lo que no puede
    pasar es fabricar velas M15 o M5 a partir de velas de una hora, y de eso ya se
    encarga `aggregate`. Si la temporalidad que falta lleva impulso, sí es fatal:
    su rotura la dibujan otros gráficos.
    """
    series: dict[str, AggregatedSeries] = {}
    skipped: list[str] = []
    for timeframe in config.charts.charts:
        try:
            series[timeframe] = aggregate(history.frame, timeframe, config.aggregation)
        except DomainError as error:
            if timeframe in config.charts.detected:
                raise
            skipped.append(f"{timeframe}: gráfico omitido — {error}")
            console.print(
                f"[yellow]Aviso:[/yellow] se omite el gráfico {timeframe}. {error}"
            )
    return series, skipped


def _audit_or_stop(
    config: ImpulseConfig, history: SidedHistory, skip: bool
) -> TimezoneAudit | None:
    if not config.timezone_audit.enabled:
        console.print(
            "[yellow]Verificación de zona horaria desactivada por configuración. "
            "La fase 1 la exige antes de calcular nada (§1.1).[/yellow]"
        )
        return None

    audit = audit_timezone(history.frame, config.timezone_audit)
    _print_audit(audit, history)
    if audit.ok:
        return audit

    if not skip:
        console.print(
            "\n[bold red]FASE 1 DETENIDA.[/bold red] La verificación de zona horaria "
            "ha fallado (§1.1).\nUn offset horario equivocado no produce un error visible: "
            "produce velas H4 desplazadas\ny por tanto impulsos distintos a los que ves en "
            "tu pantalla. Corrige el histórico\nantes de seguir."
        )
        raise typer.Exit(code=1)

    console.print(
        "\n[bold yellow]AVISO:[/bold yellow] se continúa con --skip-tz-audit pese al fallo. "
        "Los impulsos resultantes NO son auditables contra TradingView."
    )
    return audit


def _print_audit(audit: TimezoneAudit, history: SidedHistory) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column(justify="right")
    table.add_row("Origen", history.provenance)
    table.add_row("Lado del precio", history.side)
    table.add_row(
        f"Barras del histórico ({audit.resolution_minutes} min)", f"{audit.bars:,}"
    )
    table.add_row("Rango", f"{audit.first_bar} → {audit.last_bar}")
    table.add_row("Paradas > 12 h", f"{audit.gaps_found:,}")
    table.add_row(
        "Empiezan viernes / terminan domingo",
        f"{audit.gaps_starting_friday:,} / {audit.gaps_ending_sunday:,}",
    )
    table.add_row(
        "Pico de volatilidad",
        f"{audit.peak_minute_utc} UTC (esperado {audit.expected_peak_utc}, "
        f"desviación {audit.peak_offset_minutes} min)",
    )
    console.print()
    console.print(table)
    if audit.sample_gaps:
        gap = audit.sample_gaps[0]
        console.print(
            f"[dim]Ejemplo de parada: último {gap.last_before} → primero {gap.first_after}[/dim]"
        )
    verdict = "[green]OK[/green]" if audit.ok else "[bold red]FALLO[/bold red]"
    console.print(f"Verificación de zona horaria: {verdict}")
    for problem in audit.problems:
        console.print(f"  [red]·[/red] {problem}")


def _print_summary(run: ImpulseRun) -> None:
    table = Table(box=None, pad_edge=False)
    table.add_column("Gráfico", style="dim")
    table.add_column("Barras", justify="right")
    table.add_column("Impulsos que dibuja")
    table.add_column("Propios", justify="right")
    table.add_column("A favor", justify="right")
    table.add_column("En contra", justify="right")
    for chart in run.config.charts.charts:
        analysis = run.analyses.get(chart)
        diagnostics = analysis.diagnostics if analysis else {}
        table.add_row(
            chart,
            f"{len(run.chart_bars[chart]):,}" if chart in run.chart_bars else "n/d",
            " + ".join(run.config.charts.overlays(chart)),
            f"{len(analysis.published):,}" if analysis else "—",
            f"{diagnostics.get('roturas_a_favor', 0):,}" if analysis else "—",
            f"{diagnostics.get('roturas_en_contra', 0):,}" if analysis else "—",
        )
    console.print()
    console.print(table)
    console.print(f"[dim]Hash de configuración: {run.config_hash}[/dim]")
    console.print("\n[bold]Parámetros cerrados por el propietario:[/bold]")
    for decision in run.config.closed_decisions():
        console.print(f"  · {decision}")
    console.print("\n[bold]Parámetros que aún decide el propietario:[/bold]")
    for decision in run.config.open_decisions():
        console.print(f"  · {decision}")


def _baseline_comparison(
    config: ImpulseConfig, history: SidedHistory, run: ImpulseRun
) -> BaselineComparison:
    """Vuelve a ejecutar el módulo con la configuración provisional y compara.

    Las velas se reconstruyen desde el M1: el corte diario provisional no es el
    de esta corrida, así que no se pueden reutilizar las barras ya agregadas.
    """
    console.print("[dim]Corrida provisional para la tabla de antes y después...[/dim]")
    previous = _run_only(provisional_config(config), history.frame, config.charts.detected)
    return baseline_comparison.compare(run, previous)


def _print_baseline(baseline: BaselineComparison | None) -> None:
    if baseline is None:
        return
    console.print("\n[bold]Antes y después:[/bold]")
    console.print(f"  [dim]provisional {baseline.provisional_description}[/dim]")
    console.print(f"  [dim]definitivo  {baseline.definitive_description}[/dim]")
    if not baseline.provisional_matches_archive:
        console.print(
            "  [yellow]AVISO:[/yellow] la corrida provisional no reproduce el hash "
            "archivado; la comparación no es la publicada."
        )


def _print_anchor_verdicts(anchors: AnchorComparison | None) -> None:
    if anchors is None:
        return
    console.print("\n[bold]Ancla A1 vs A2 (§B):[/bold]")
    for item in anchors.per_timeframe.values():
        colour = "green" if item.is_cosmetic else "yellow"
        console.print(f"  [{colour}]·[/{colour}] {item.verdict}")


@contextmanager
def _handled() -> Iterator[None]:
    """Traduce los errores del dominio y de configuración en salida limpia."""
    try:
        yield
    except (ConfigError, DomainError) as error:
        console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error
