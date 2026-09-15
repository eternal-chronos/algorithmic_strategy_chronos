"""Caso de uso: detectar los impulsos dominantes de un histórico.

Recibe las barras ya agregadas —quién las lee del disco y cómo se agregan es
detalle de infraestructura, y lo compone la CLI— y devuelve, por temporalidad,
los impulsos, los eventos de rotura y el estado barra a barra.

Fase 1: aquí no hay señales, ni entradas, ni stops, ni targets, ni medición de
rentabilidad. Sólo estructura.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from chronos.application.structure.causal import PriorBarAtr
from chronos.application.structure.config import (
    H4,
    PATTERN_TIMEFRAMES,
    TIMEFRAME_MINUTES,
    AggregationConfig,
    ImpulseConfig,
)
from chronos.application.structure.timezone_audit import TimezoneAudit
from chronos.domain.errors import DomainError
from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.impulse import BarState, BreakEvent, DominantImpulse
from chronos.domain.structure.patterns import (
    DEFAULT_RULE,
    PATTERN_COLUMNS,
    PatternRule,
    patterns_inside,
)

#: Columnas exigidas por §5.1, en su orden. Las que van detrás son material de
#: auditoría (comparativa de anclas, arranque de la pierna) y no sustituyen a
#: ninguna de éstas.
TABLE_COLUMNS = (
    "id_num",
    "timeframe",
    "direccion",
    "ts_rotura_anterior",
    "ts_constitucion",
    "ts_fin",
    "n_barras_limbo",
    "n_barras_id",
    "precio_ancla",
    "precio_extremo",
    "rango_usd",
    "rango_atr",
    "rango_pct_precio",
    "tipo_rotura_salida",
    "estado",
    "config_hash",
)

AUDIT_COLUMNS = (
    "precio_ancla_a1",
    "precio_ancla_a2",
    "diferencia_anclas_usd",
    "ts_arranque_pierna",
    #: R-36. La vela que fija el extremo y su color: la regla del propietario dice
    #: que en un ID alcista es verde y en uno bajista, roja. Sale del detector, no
    #: se re-deriva buscando qué cuerpo coincide con el precio (con empate no hay
    #: respuesta única).
    "ts_vela_extremo",
    "color_vela_extremo",
    "ts_vela_ancla",
    "cuerpo_vela_constituyente",
    "atr_previo",
    #: Posiciones en la serie de la temporalidad. Lo que se mide en barras —el
    #: latigazo de C.8, las ventanas de las capturas— se cuenta con esto y no
    #: con las marcas de tiempo: entre el viernes y el domingo hay dos días de
    #: reloj y cero velas.
    "indice_constitucion",
    "indice_fin",
)


@dataclass(frozen=True, slots=True)
class TimeframeAnalysis:
    """Todo lo que el módulo sabe de una temporalidad."""

    timeframe: str
    bars: pd.DataFrame
    impulses: tuple[DominantImpulse, ...]
    events: tuple[BreakEvent, ...]
    states: tuple[BarState, ...]
    table: pd.DataFrame
    diagnostics: dict[str, int]

    @property
    def published(self) -> tuple[DominantImpulse, ...]:
        return tuple(impulse for impulse in self.impulses if impulse.publishable)


@dataclass(frozen=True, slots=True)
class ImpulseRun:
    """Resultado completo de una corrida del módulo 1."""

    enabled: bool
    config: ImpulseConfig
    config_hash: str
    #: Una entrada por temporalidad con detector propio.
    analyses: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    #: Velas de cada gráfico, incluidas las que no llevan detector (M15 en el
    #: reparto por defecto sólo muestra el impulso de H1).
    chart_bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    audit: TimezoneAudit | None = None
    provenance: str = ""
    aggregation_notes: tuple[str, ...] = ()
    #: El OB y el FVG dentro del ID (K.2), una tabla por temporalidad de
    #: `PATTERN_TIMEFRAMES`, cada una dentro de su propio ID: hoy sólo H4. Una
    #: temporalidad que no está aquí no marca ninguno.
    patterns: dict[str, pd.DataFrame] = field(default_factory=dict)
    pattern_rule: PatternRule = DEFAULT_RULE

    @property
    def emits_nothing(self) -> bool:
        """`True` cuando el módulo está apagado: sin impulsos, sin eventos, sin nada."""
        return not self.enabled and not self.analyses

    def table(self) -> pd.DataFrame:
        """Tabla de impulsos de todas las temporalidades, ya ordenada."""
        frames = [analysis.table for analysis in self.analyses.values()]
        if not frames:
            return pd.DataFrame(columns=[*TABLE_COLUMNS, *AUDIT_COLUMNS])
        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values(["timeframe", "ts_constitucion"]).reset_index(drop=True)

    def patterns_table(self) -> pd.DataFrame:
        """Los OB y FVG de todos los gráficos en una tabla, ya ordenada."""
        frames = [frame for frame in self.patterns.values() if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=list(PATTERN_COLUMNS))
        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values(["timeframe", "ts_marcado", "ts_origen"]).reset_index(
            drop=True
        )


class DetectDominantImpulses:
    """Orquesta un detector por temporalidad sobre las barras ya agregadas."""

    def __init__(self, config: ImpulseConfig) -> None:
        self._config = config

    def execute(
        self,
        series: Mapping[str, pd.DataFrame],
        *,
        audit: TimezoneAudit | None = None,
        provenance: str = "",
        aggregation_notes: Sequence[str] = (),
        pattern_rule: PatternRule = DEFAULT_RULE,
    ) -> ImpulseRun:
        """`series` trae las velas de cada gráfico; el detector sólo corre en las
        temporalidades que el reparto declara como impulso."""
        config_hash = self._config.fingerprint()
        if not self._config.enabled:
            # Módulo apagado: no se procesa ni una barra y no se emite nada.
            return ImpulseRun(enabled=False, config=self._config, config_hash=config_hash)

        detected = self._config.charts.detected
        missing = [timeframe for timeframe in detected if timeframe not in series]
        if missing:
            raise DomainError(
                f"Faltan las velas de {', '.join(missing)}, que el reparto de gráficos "
                "necesita para dibujar su impulso"
            )

        analyses = {
            timeframe: self._analyse(timeframe, series[timeframe], config_hash)
            for timeframe in detected
        }
        # K.2: el OB y el FVG de cada temporalidad dentro de su propio ID, y
        # sólo en los cortes del día que dictó el propietario. Sólo donde hay
        # detector.
        patterns = {
            timeframe: patterns_inside(
                analyses[timeframe].bars,
                timeframe=timeframe,
                impulses=analyses[timeframe].published,
                marking=pattern_rule.marks_at(
                    session_slots(
                        pd.DatetimeIndex(analyses[timeframe].bars.index),
                        self._config.aggregation,
                    )
                ),
                rule=pattern_rule,
            )
            for timeframe in PATTERN_TIMEFRAMES
            if timeframe in analyses
        }
        return ImpulseRun(
            enabled=True,
            config=self._config,
            config_hash=config_hash,
            analyses=analyses,
            chart_bars={
                chart: series[chart] for chart in self._config.charts.charts if chart in series
            },
            audit=audit,
            provenance=provenance,
            aggregation_notes=tuple(aggregation_notes),
            patterns=patterns,
            pattern_rule=pattern_rule,
        )

    # --- Interno ------------------------------------------------------------

    def _analyse(self, timeframe: str, frame: pd.DataFrame, config_hash: str) -> TimeframeAnalysis:
        if frame.empty:
            raise DomainError(f"No hay barras agregadas en {timeframe}")

        rules = self._config.rules
        detector = DominantImpulseDetector(
            timeframe=timeframe,
            anchor_mode=rules.anchor_mode,
            seed_mode=rules.seed_mode,
            doji_break_mode=rules.doji_break_mode,
            leg_start_mode=rules.leg_start_mode,
            warmup_bars=rules.warmup_bars,
        )
        atr = PriorBarAtr(
            frame["high"].to_numpy(dtype=float),
            frame["low"].to_numpy(dtype=float),
            frame["close"].to_numpy(dtype=float),
            period=rules.atr_period,
            label=f"ATR({rules.atr_period}) {timeframe}",
        )

        index = pd.DatetimeIndex(frame.index)
        opens = frame["open"].to_numpy(dtype=float)
        closes = frame["close"].to_numpy(dtype=float)

        atr_at_constitution: dict[int, float] = {}
        seen = 0
        for position in range(len(frame)):
            # La frontera avanza *antes* de procesar: el ATR legible en esta
            # barra es el que cerró en la anterior, nunca el de la propia barra.
            atr.advance(position)
            detector.process(
                BodyBar(
                    timestamp=index[position].to_pydatetime(),
                    open=float(opens[position]),
                    close=float(closes[position]),
                )
            )
            if len(detector.impulses) > seen:
                seen = len(detector.impulses)
                atr_at_constitution[detector.impulses[-1].id_num] = atr.at(position)

        table = _build_table(
            detector.published_impulses,
            last_index=len(frame) - 1,
            atr_at_constitution=atr_at_constitution,
            config_hash=config_hash,
        )
        return TimeframeAnalysis(
            timeframe=timeframe,
            bars=frame,
            impulses=detector.impulses,
            events=detector.events,
            states=detector.states,
            table=table,
            diagnostics=detector.diagnostics,
        )


def session_slots(index: pd.DatetimeIndex, aggregation: AggregationConfig) -> np.ndarray:
    """Posición de cada vela de H4 dentro de su día de sesión: 0 la que abre la
    sesión, 1 la siguiente, hasta 5.

    Es lo que necesita la regla de los OB y FVG (K.2) para saber en qué cierres
    se marca: la 2.ª, la 3.ª y la 4.ª vela del día. Se lee con el reloj de la
    plaza del ancla de sesión —igual que se agregan las velas— para que la
    posición no se mueva con el horario de verano: la 2.ª vela es la de las
    21:00 de Nueva York todo el año, aunque en UTC salte una hora dos veces al
    año. Sin ancla de sesión el día empieza a la hora fija en UTC.
    """
    anchor = aggregation.session_anchor
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is None:
        raise DomainError("La posición en el día de sesión exige un índice tz-aware")
    local = stamps.tz_convert(anchor.timezone if anchor is not None else "UTC")
    opening = aggregation.session_start_time()
    minute_of_day = local.hour.to_numpy() * 60 + local.minute.to_numpy()
    since_open = (minute_of_day - (opening.hour * 60 + opening.minute)) % 1440
    return np.asarray(since_open // TIMEFRAME_MINUTES[H4], dtype=np.int64)


def _build_table(
    impulses: Sequence[DominantImpulse],
    *,
    last_index: int,
    atr_at_constitution: Mapping[int, float],
    config_hash: str,
) -> pd.DataFrame:
    rows = []
    for impulse in impulses:
        atr_value = atr_at_constitution.get(impulse.id_num, float("nan"))
        range_usd = impulse.range_usd
        rows.append(
            {
                "id_num": impulse.id_num,
                "timeframe": impulse.timeframe,
                "direccion": impulse.direction.value,
                "ts_rotura_anterior": impulse.ts_previous_break,
                "ts_constitucion": impulse.ts_constitution,
                "ts_fin": impulse.ts_end,
                "n_barras_limbo": impulse.limbo_bars,
                "n_barras_id": impulse.bars_alive(last_index),
                "precio_ancla": impulse.anchor,
                "precio_extremo": impulse.extreme,
                "rango_usd": range_usd,
                "rango_atr": _safe_ratio(range_usd, atr_value),
                "rango_pct_precio": impulse.range_pct_price,
                "tipo_rotura_salida": (
                    impulse.exit_break_kind.value if impulse.exit_break_kind else None
                ),
                "estado": impulse.state,
                "config_hash": config_hash,
                "precio_ancla_a1": impulse.anchor_a1,
                "precio_ancla_a2": impulse.anchor_a2,
                "diferencia_anclas_usd": (
                    abs(impulse.anchor_a1 - impulse.anchor_a2)
                    if impulse.anchor_a1 is not None
                    else float("nan")
                ),
                "ts_arranque_pierna": impulse.ts_leg_start,
                "ts_vela_extremo": impulse.ts_extreme,
                "color_vela_extremo": impulse.extreme_bar_direction.value,
                "ts_vela_ancla": impulse.ts_anchor,
                "cuerpo_vela_constituyente": impulse.constituting_body_size,
                "atr_previo": atr_value,
                "indice_constitucion": impulse.index_constitution,
                "indice_fin": impulse.index_end,
            }
        )

    columns = [*TABLE_COLUMNS, *AUDIT_COLUMNS]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns]


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator
