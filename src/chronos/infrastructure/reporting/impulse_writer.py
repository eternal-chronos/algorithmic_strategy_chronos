"""Artefactos de una corrida del módulo 1 (§5).

Cada corrida deja una carpeta autocontenida: la tabla de impulsos, los eventos
de rotura, el estado barra a barra, el informe en texto plano, el explorador
HTML y el volcado exacto de la configuración. Con el módulo apagado no se
escribe absolutamente nada, ni siquiera la carpeta.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from chronos.application.ports import Clock
from chronos.application.structure.anchor_comparison import AnchorComparison
from chronos.application.structure.baseline_comparison import BaselineComparison
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.application.structure.geometry import GEOMETRY_COLUMNS, atr_by_bar, geometry_of
from chronos.application.structure.lateralization import (
    CONTACT_COLUMNS,
    LateralizationStudy,
    measure,
)
from chronos.application.structure.statistics import ImpulseStatistics, summarize
from chronos.application.structure.zones import ZonesRun, detect_zones
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.impulse_captures import write_captures
from chronos.infrastructure.reporting.impulse_explorer import ModeVariant, render_explorer
from chronos.infrastructure.reporting.impulse_report import render_report
from chronos.infrastructure.reporting.zone_report import render_zone_report


class ImpulseReportWriter:
    """Escribe todo lo que produce el módulo de impulso dominante."""

    def __init__(self, output_dir: str | Path = "reports", clock: Clock | None = None) -> None:
        self._root = Path(output_dir)
        self._clock: Clock = clock or SystemClock()

    def write(
        self,
        run: ImpulseRun,
        *,
        statistics: ImpulseStatistics | None = None,
        anchors: AnchorComparison | None = None,
        lateralization: LateralizationStudy | None = None,
        #: Corrida con la configuración provisional, para la tabla de antes y después.
        baseline: BaselineComparison | None = None,
        #: R-36. Las otras corridas de `LEG_START_MODE` que el explorador embebe
        #: para poder alternarlas sobre las mismas velas. Vacío = explorador de un
        #: solo modo, como antes.
        variants: Sequence[ModeVariant] = (),
        #: Fase 2.0. Con las zonas apagadas viene vacío y no se escribe ni un
        #: fichero suyo: la carpeta sale exactamente como en la fase 1.
        zones: ZonesRun | None = None,
        run_id: str | None = None,
    ) -> Path | None:
        """Devuelve la carpeta escrita, o `None` si el módulo está apagado."""
        if not run.enabled:
            return None

        statistics = statistics or summarize(run)
        lateralization = lateralization or measure(run)
        zones = zones if zones is not None else detect_zones(run)
        generated_at = self._clock.now()
        stamp = run_id or generated_at.strftime("%Y%m%d_%H%M%S")
        folder = self._root / f"{stamp}_impulso_dominante"
        folder.mkdir(parents=True, exist_ok=True)

        run.table().to_csv(folder / "impulsos.csv", index=False)
        _events_frame(run).to_csv(folder / "eventos_rotura.csv", index=False)
        _states_frame(run).to_csv(folder / "estado_por_barra.csv", index=False)
        _contacts_frame(lateralization).to_csv(folder / "contactos.csv", index=False)

        if run.config.reporting.text_report:
            (folder / "reporte.txt").write_text(
                render_report(
                    run,
                    statistics,
                    generated_at,
                    anchors=anchors,
                    lateralization=lateralization,
                    baseline=baseline,
                ),
                encoding="utf-8",
            )
        if baseline is not None:
            _baseline_frame(baseline).to_csv(folder / "antes_y_despues.csv", index=False)
        if zones.enabled:
            zones.table().to_csv(folder / "zonas.csv", index=False)
            zones.survivals().to_csv(folder / "roturas_y_zonas.csv", index=False)
            (folder / "reporte_zonas.txt").write_text(
                render_zone_report(run, zones, generated_at), encoding="utf-8"
            )
        if run.config.reporting.captures:
            # Kaleido abre un navegador headless por imagen: son un par de
            # minutos, así que se puede apagar en las corridas de trabajo.
            write_captures(run, statistics, lateralization, folder / "capturas")
        if run.config.reporting.explorer_html:
            (folder / "explorador.html").write_text(
                render_explorer(
                    run,
                    run.config.reporting.max_explorer_bars,
                    generated_at,
                    lateralization,
                    variants,
                    zones,
                ),
                encoding="utf-8",
            )

        (folder / "run.json").write_text(
            json.dumps(
                {
                    "modulo": "impulso_dominante",
                    "fase": 1,
                    "config_hash": run.config_hash,
                    "config": asdict(run.config),
                    "origen_datos": run.provenance,
                    "agregacion": list(run.aggregation_notes),
                    "verificacion_zona_horaria": (
                        _audit_summary(run) if run.audit is not None else None
                    ),
                    "graficos": {
                        chart: {
                            "barras": len(run.chart_bars[chart]),
                            "impulsos_que_dibuja": list(run.config.charts.overlays(chart)),
                            "principal": run.config.charts.primary(chart),
                        }
                        for chart in run.config.charts.charts
                        if chart in run.chart_bars
                    },
                    "por_temporalidad": {
                        timeframe: {
                            "barras": len(analysis.bars),
                            "impulsos_publicados": len(analysis.published),
                            "impulsos_totales": len(analysis.impulses),
                            "eventos": len(analysis.events),
                            "diagnosticos": analysis.diagnostics,
                        }
                        for timeframe, analysis in run.analyses.items()
                    },
                    "zonas": _zones_summary(zones),
                    "decisiones_cerradas": list(run.config.closed_decisions()),
                    "decisiones_abiertas": list(run.config.open_decisions()),
                    "sustituye_a": (
                        {
                            "config_hash": baseline.provisional_hash,
                            "descripcion": baseline.provisional_description,
                            "coincide_con_lo_archivado": baseline.provisional_matches_archive,
                        }
                        if baseline is not None
                        else None
                    ),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        return folder


def _zones_summary(zones: ZonesRun) -> dict[str, object]:
    """Fase 2.0 en el `run.json`: apagada, o con el recuento por temporalidad.

    Se deja constancia también cuando están apagadas para que un informe
    archivado diga si la corrida las llevaba o no, en vez de tener que deducirlo
    de la ausencia de ficheros.
    """
    if not zones.enabled:
        return {"activas": False}
    return {
        "activas": True,
        "fase": "2.0 · sólo detección",
        "por_temporalidad": {
            timeframe: {
                "impulsos_con_zonas": len(item.items),
                "zonas": len(item.table),
                "con_ob_confirmado": len(item.with_order_block),
                "sin_ob_confirmado": len(item.without_order_block),
                "ul_altura_cero": sum(1 for zoned in item.items if zoned.last.is_flat),
                "ul_extendidos": sum(1 for zoned in item.items if zoned.last.extended),
            }
            for timeframe, item in zones.per_timeframe.items()
        },
    }


def _baseline_frame(baseline: BaselineComparison) -> pd.DataFrame:
    """La tabla de antes y después de todas las temporalidades en un solo CSV."""
    frames = [
        table.assign(temporalidad=timeframe)
        for timeframe, table in baseline.per_timeframe.items()
    ]
    frames.append(baseline.alignment.assign(temporalidad="las tres"))
    combined = pd.concat(frames, ignore_index=True)
    return combined[["temporalidad", *[c for c in combined.columns if c != "temporalidad"]]]


_EVENT_COLUMNS = (
    "timeframe", "tipo", "timestamp", "id_roto", "direccion_id_roto",
    "direccion_pierna_nueva", "cierre", "nivel_roto", *GEOMETRY_COLUMNS,
)


def _events_frame(run: ImpulseRun) -> pd.DataFrame:
    """Roturas de todas las temporalidades, con la geometría de la sección E.

    La geometría se calcula aquí, al escribir, y no dentro del detector: el
    dominio no la conoce y así no puede leerla ninguna regla.
    """
    frames = [_events_of(analysis) for analysis in run.analyses.values()]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=list(_EVENT_COLUMNS))
    return pd.concat(frames, ignore_index=True)[list(_EVENT_COLUMNS)]


def _events_of(analysis: TimeframeAnalysis) -> pd.DataFrame:
    events = analysis.events
    if not events:
        return pd.DataFrame(columns=list(_EVENT_COLUMNS))

    bars = analysis.bars
    frame = pd.DataFrame(
        {
            "timeframe": [event.timeframe for event in events],
            "tipo": [event.kind.value for event in events],
            "timestamp": [event.timestamp for event in events],
            "id_roto": [event.broken_id_num for event in events],
            "direccion_id_roto": [event.broken_direction.value for event in events],
            "direccion_pierna_nueva": [event.new_leg_direction.value for event in events],
            "cierre": [event.close for event in events],
            "nivel_roto": [event.level for event in events],
        }
    )
    indices = np.array([event.index for event in events], dtype=int)
    levels = frame["nivel_roto"].to_numpy(dtype=float)
    # El cierre rompió hacia arriba si quedó por encima del nivel superado.
    beyond_upper = frame["cierre"].to_numpy(dtype=float) > levels
    atr = atr_by_bar(analysis.table, pd.DatetimeIndex(bars.index))[indices]
    geometry = geometry_of(bars, indices, levels=levels, beyond_upper=beyond_upper, atr=atr)
    return pd.concat([frame, geometry], axis=1)


def _contacts_frame(study: LateralizationStudy) -> pd.DataFrame:
    frames = [
        measurement.contacts
        for measurement in study.per_timeframe.values()
        if not measurement.contacts.empty
    ]
    if not frames:
        return pd.DataFrame(columns=list(CONTACT_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def _states_frame(run: ImpulseRun) -> pd.DataFrame:
    rows = [
        {
            "timeframe": state.timeframe,
            "timestamp": state.timestamp,
            "estado": state.state.value,
            "id_vigente": state.impulse_id,
            "pierna_en_curso": state.leg_direction.value if state.leg_direction else None,
        }
        for analysis in run.analyses.values()
        for state in analysis.states
    ]
    columns = ["timeframe", "timestamp", "estado", "id_vigente", "pierna_en_curso"]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


def _audit_summary(run: ImpulseRun) -> dict[str, object]:
    audit = run.audit
    assert audit is not None
    return {
        "ok": audit.ok,
        "problemas": list(audit.problems),
        "barras_m1": audit.bars,
        "primera_barra": str(audit.first_bar),
        "ultima_barra": str(audit.last_bar),
        "paradas_detectadas": audit.gaps_found,
        "paradas_que_empiezan_viernes": audit.gaps_starting_friday,
        "paradas_que_terminan_domingo": audit.gaps_ending_sunday,
        "pico_volatilidad_utc": audit.peak_minute_utc,
        "pico_hora_utc": audit.peak_hour_utc,
        "pico_esperado_utc": audit.expected_peak_utc,
        "desviacion_minutos": audit.peak_offset_minutes,
        # A.3: el pico debe moverse una hora con el verano de EE. UU. Es
        # diagnóstico, así que se deja constancia aquí aunque no detenga nada.
        "desplazamiento_verano_min": audit.dst_shift_minutes,
        "desplazamiento_verano_ok": audit.dst_shift_ok,
    }
