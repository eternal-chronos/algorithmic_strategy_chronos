"""Interfaz de línea de comandos. Es el punto de composición del proyecto:
aquí, y solo aquí, se juntan configuración, datos, estrategia y motor.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from chronos.application.backtest.config import BacktestConfig
from chronos.application.metrics.performance import PerformanceReport
from chronos.application.use_cases.run_backtest import RunBacktest
from chronos.domain.enums import Timeframe
from chronos.domain.errors import DomainError
from chronos.infrastructure.config.loader import ConfigError, load_run
from chronos.infrastructure.data.frames import normalize_frame, resample_frame, validate_frame
from chronos.infrastructure.data.repository import build_repository
from chronos.infrastructure.data.synthetic import generate_ohlcv
from chronos.infrastructure.reporting.report import ReportWriter
from chronos.strategies.registry import available_strategies, create_strategy

app = typer.Typer(
    help="Laboratorio de backtesting de XAUUSD (Pepperstone / cTrader).",
    no_args_is_help=True,
    add_completion=False,
)
data_app = typer.Typer(help="Gestión del histórico de precios.", no_args_is_help=True)
strategy_app = typer.Typer(help="Estrategias registradas.", no_args_is_help=True)
app.add_typer(data_app, name="data")
app.add_typer(strategy_app, name="strategy")

console = Console()

DEFAULT_CONFIG = Path("config/backtest.yaml")


# --- backtest ---------------------------------------------------------------


@app.command("backtest")
def backtest(
    config: Annotated[Path, typer.Option("--config", "-c", help="Fichero YAML de la corrida")] = DEFAULT_CONFIG,
    strategy_name: Annotated[str | None, typer.Option("--strategy", "-s", help="Sobrescribe la estrategia del YAML")] = None,
    start: Annotated[str | None, typer.Option("--start", help="Inicio del periodo simulado (YYYY-MM-DD)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="Fin del periodo simulado (YYYY-MM-DD)")] = None,
    report: Annotated[bool, typer.Option("--report/--no-report", help="Generar informe HTML y CSV")] = True,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Solo el resumen")] = False,
) -> None:
    """Ejecuta un backtest completo y escribe el informe."""
    try:
        run_config, spec = load_run(config)
        run_config = _apply_period(run_config, start, end)
        name = strategy_name or run_config.strategy_name
        strategy = create_strategy(name, dict(run_config.strategy_params))

        if not quiet:
            periodo = ""
            if run_config.data.start or run_config.data.end:
                desde = run_config.data.start.date() if run_config.data.start else "inicio"
                hasta = run_config.data.end.date() if run_config.data.end else "fin"
                periodo = f" · periodo {desde} → {hasta}"
            console.print(
                f"[bold]{spec.symbol}[/bold] · {run_config.data.strategy_timeframe.value} · "
                f"estrategia [bold]{name}[/bold] · fuente {run_config.data.source}{periodo}"
            )

        repository = build_repository(run_config.data, spec.symbol)
        use_case = RunBacktest(spec, run_config)
        run = use_case.execute(repository, strategy)

        _print_summary(run.performance, run.result.rejections, run.result.halted_reason)

        if report:
            prices = _prices_for_report(repository, run_config)
            folder = ReportWriter(run_config.reporting.output_dir).write(run, prices=prices)
            console.print(f"\nInforme: [bold]{folder / 'report.html'}[/bold]")
    except (ConfigError, DomainError) as error:
        console.print(f"[bold red]Error:[/bold red] {error}")
        raise typer.Exit(code=1) from error


def _apply_period(
    config: BacktestConfig, start: str | None, end: str | None
) -> BacktestConfig:
    """Acota el histórico simulado. A diferencia del filtro del panel, esto
    vuelve a ejecutar la estrategia: el calentamiento y el estado arrancan de
    cero dentro del periodo indicado."""
    if start is None and end is None:
        return config
    data = replace(
        config.data,
        start=_parse_date(start, "--start") if start else config.data.start,
        end=_parse_date(end, "--end") if end else config.data.end,
    )
    if data.start and data.end and data.start > data.end:
        raise ConfigError("--start es posterior a --end")
    return replace(config, data=data)


def _parse_date(value: str, flag: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(f"{flag} no es una fecha ISO válida: {value!r}") from error


def _prices_for_report(repository: object, run_config: object) -> pd.DataFrame | None:
    frame = getattr(repository, "frame", None)
    if frame is None:
        return None
    timeframe = run_config.data.strategy_timeframe  # type: ignore[attr-defined]
    base = run_config.data.timeframe  # type: ignore[attr-defined]
    return frame if timeframe is base else resample_frame(frame, timeframe)


def _print_summary(
    report: PerformanceReport, rejections: dict[str, int], halted: str | None
) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim")
    table.add_column(justify="right")

    def tone(value: float) -> str:
        return "green" if value > 0 else ("red" if value < 0 else "white")

    rows = [
        ("Beneficio neto", f"[{tone(report.net_profit)}]{report.net_profit:,.2f} USD[/]"),
        ("Retorno", f"[{tone(report.return_pct)}]{report.return_pct:.2%}[/]"),
        ("CAGR", f"{report.cagr:.2%}"),
        ("Drawdown máximo", f"[red]{report.max_drawdown_pct:.2%}[/] ({report.max_drawdown:,.2f} USD)"),
        ("Sharpe / Sortino / Calmar", f"{report.sharpe:.2f} / {report.sortino:.2f} / {report.calmar:.2f}"),
        ("Operaciones", f"{report.total_trades:,}"),
        ("Acierto", f"{report.win_rate:.2%}"),
        ("Profit factor", f"{report.profit_factor:.2f}"),
        ("Expectativa", f"{report.expectancy:,.2f} USD"),
        ("Expectativa en R", f"{report.expectancy_r:.3f}" if report.expectancy_r is not None else "n/d"),
        ("Costes (comisión + swap)", f"{report.total_commission + report.total_swap:,.2f} USD"),
        ("Exposición", f"{report.exposure_pct:.2%}"),
    ]
    for label, value in rows:
        table.add_row(label, value)

    console.print()
    console.print(table)
    if halted:
        console.print(f"\n[yellow]Corrida detenida:[/yellow] {halted}")
    if rejections:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(rejections.items()))
        console.print(f"[dim]Señales descartadas: {detail}[/dim]")


# --- data -------------------------------------------------------------------


@data_app.command("synth")
def data_synth(
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("data/synthetic/XAUUSD_M1.parquet"),
    periods: Annotated[int, typer.Option("--periods", "-n", help="Número de barras")] = 200_000,
    start: Annotated[str, typer.Option("--start")] = "2024-01-01",
    seed: Annotated[int, typer.Option("--seed")] = 42,
) -> None:
    """Genera un histórico sintético para probar el motor (no para evaluar señales)."""
    frame = generate_ohlcv(start=start, periods=periods, seed=seed, spread_points=20)
    validate_frame(frame)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out)
    console.print(
        f"[green]OK[/green] {len(frame):,} barras M1 → {out}\n"
        f"[dim]{frame.index[0]} → {frame.index[-1]}[/dim]\n"
        "[yellow]Datos sintéticos: sirven para validar el motor, no la estrategia.[/yellow]"
    )


@data_app.command("import")
def data_import(
    source: Annotated[Path, typer.Argument(help="CSV exportado del bróker")],
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("data/processed/XAUUSD_M1.parquet"),
    timezone: Annotated[str, typer.Option("--tz", help="Zona horaria del CSV si es naíf")] = "UTC",
) -> None:
    """Importa un CSV al formato canónico en parquet."""
    if not source.is_file():
        console.print(f"[bold red]Error:[/bold red] no existe {source}")
        raise typer.Exit(code=1)

    frame = normalize_frame(pd.read_csv(source), timezone=timezone)
    validate_frame(frame)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out)
    console.print(
        f"[green]OK[/green] {len(frame):,} barras → {out}\n"
        f"[dim]{frame.index[0]} → {frame.index[-1]} (UTC)[/dim]"
    )


@data_app.command("info")
def data_info(
    path: Annotated[Path, typer.Argument()] = Path("data/processed/XAUUSD_M1.parquet"),
    timeframe: Annotated[str, typer.Option("--tf", help="Resamplear antes de resumir")] = "",
) -> None:
    """Resume un histórico: rango, huecos y calidad."""
    if not path.is_file():
        console.print(f"[bold red]Error:[/bold red] no existe {path}")
        raise typer.Exit(code=1)

    frame = normalize_frame(pd.read_parquet(path))
    if timeframe:
        frame = resample_frame(frame, Timeframe.parse(timeframe))
    validate_frame(frame)

    deltas = frame.index.to_series().diff().dropna()
    step = deltas.mode().iloc[0] if not deltas.empty else pd.Timedelta(0)
    gaps = int((deltas > step * 3).sum())

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column(justify="right")
    table.add_row("Barras", f"{len(frame):,}")
    table.add_row("Desde", str(frame.index[0]))
    table.add_row("Hasta", str(frame.index[-1]))
    table.add_row("Paso más frecuente", str(step))
    table.add_row("Huecos (> 3 pasos)", f"{gaps:,}")
    table.add_row("Precio mín / máx", f"{frame['low'].min():,.2f} / {frame['high'].max():,.2f}")
    table.add_row("Columna spread", "sí" if "spread" in frame.columns else "no")
    console.print(table)


# --- strategy ---------------------------------------------------------------


@strategy_app.command("list")
def strategy_list() -> None:
    """Lista las estrategias registradas."""
    names = available_strategies()
    if not names:
        console.print("[yellow]No hay estrategias registradas.[/yellow]")
        return
    for name in names:
        console.print(f"· [bold]{name}[/bold]")


if __name__ == "__main__":  # pragma: no cover
    app()
