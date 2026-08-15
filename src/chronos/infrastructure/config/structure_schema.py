"""Esquema Pydantic del YAML del módulo de impulso dominante.

Toda la tolerancia al mundo exterior vive aquí. Los tres parámetros abiertos se
declaran como literales cerrados: escribir un modo que no existe es un error de
configuración, no un valor por defecto silencioso.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chronos.application.entries.config import EntriesConfig, EntryCosts
from chronos.application.structure.config import (
    AggregationConfig,
    ChartsConfig,
    ImpulseConfig,
    ImpulseRulesConfig,
    StructureDataConfig,
    StructureReportingConfig,
    TimezoneAuditConfig,
    ZonesConfig,
)
from chronos.domain.structure.enums import (
    AnchorMode,
    DojiBreakMode,
    LegStartMode,
    OverlapPriority,
    SeedMode,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructureDataSchema(_Strict):
    path: str = ""
    bid_path: str = ""
    ask_path: str = ""
    timezone: str = "UTC"
    start: str | None = None
    end: str | None = None

    def to_domain(self) -> StructureDataConfig:
        return StructureDataConfig(**self.model_dump())


class AggregationSchema(_Strict):
    h4_offset_hours: int = Field(default=0, ge=0, le=23)
    #: Decidido por el propietario. Ancla también el origen de H4: con un ancla de
    #: sesión, `h4_offset_hours` no pinta nada.
    d_session_start: str = "NY_18:00"

    def to_domain(self) -> AggregationConfig:
        return AggregationConfig(**self.model_dump())


class ImpulseRulesSchema(_Strict):
    anchor_mode: Literal["A1_last_counter_body", "A2_first_leg_bar"] = "A1_last_counter_body"
    seed_mode: Literal["S1_first_non_doji", "S2_first_counter_bar"] = "S2_first_counter_bar"
    doji_break_mode: Literal["D1_doji_no_rompe", "D2_doji_rompe_por_cierre"] = "D1_doji_no_rompe"
    leg_start_mode: Literal[
        "L1_actual", "L2_siguiente_barra", "L3_extremo_solo_color_valido"
    ] = "L1_actual"
    #: Fase 2.1. Apagado por defecto: el fichero tal cual reproduce la línea base.
    break_by_zone: bool = False
    overlap_priority: Literal["a_favor_primero", "en_contra_primero"] = "a_favor_primero"
    warmup_bars: int = Field(default=50, ge=0)
    atr_period: int = Field(default=14, ge=1)

    def to_domain(self) -> ImpulseRulesConfig:
        return ImpulseRulesConfig(
            anchor_mode=AnchorMode(self.anchor_mode),
            seed_mode=SeedMode(self.seed_mode),
            doji_break_mode=DojiBreakMode(self.doji_break_mode),
            leg_start_mode=LegStartMode(self.leg_start_mode),
            break_by_zone=self.break_by_zone,
            overlap_priority=OverlapPriority(self.overlap_priority),
            warmup_bars=self.warmup_bars,
            atr_period=self.atr_period,
        )


class EntryCostsSchema(_Strict):
    """⚠️ Todo marcado VERIFICAR: nada calibrado contra Pepperstone Razor."""

    spread_points: float = Field(default=20.0, ge=0)
    slippage_points: float = Field(default=1.0, ge=0)
    commission_per_lot_per_side: float = Field(default=3.0, ge=0)
    swap_long_points: float = -0.9
    swap_short_points: float = 0.2
    triple_swap_weekday: int = Field(default=2, ge=0, le=6)

    def to_domain(self) -> EntryCosts:
        return EntryCosts(**self.model_dump())


class EntriesSchema(_Strict):
    """Fase 3.0. Apagada por defecto: el fichero tal cual reproduce la 2.1."""

    enabled: bool = False
    #: Parámetro abierto del §2. Tiene que estar en la rejilla.
    rejection_percentile: int = Field(default=75, gt=0, lt=100)
    rejection_grid: list[int] = Field(default_factory=lambda: [60, 75, 90])
    #: `0` = hasta que se constituya el ID de H4 siguiente.
    retest_window_h4: int = Field(default=0, ge=0)
    retest_grid: list[int] = Field(default_factory=lambda: [6, 12, 24])
    #: `0` = mientras la observación siga viva.
    m15_search_bars: int = Field(default=0, ge=0)
    costs: EntryCostsSchema = Field(default_factory=EntryCostsSchema)
    risk_per_trade_usd: float = Field(default=1_000.0, gt=0)
    #: ⚠️ §4: sin fichero de ask, con `false` el comando se detiene y avisa.
    allow_missing_ask: bool = False

    def to_domain(self) -> EntriesConfig:
        return EntriesConfig(
            enabled=self.enabled,
            rejection_percentile=self.rejection_percentile,
            rejection_grid=tuple(self.rejection_grid),
            retest_window_h4=self.retest_window_h4,
            retest_grid=tuple(self.retest_grid),
            m15_search_bars=self.m15_search_bars,
            costs=self.costs.to_domain(),
            risk_per_trade_usd=self.risk_per_trade_usd,
            allow_missing_ask=self.allow_missing_ask,
        )


class ZonesSchema(_Strict):
    #: Fase 2.0. Apagadas por defecto: la línea base de la fase 1 se reproduce
    #: con este fichero tal cual, sin tocar nada.
    enabled: bool = False

    def to_domain(self) -> ZonesConfig:
        return ZonesConfig(**self.model_dump())


class TimezoneAuditSchema(_Strict):
    enabled: bool = True
    expected_peak_utc: str = "13:30"
    tolerance_minutes: int = Field(default=30, ge=0)
    weekend_gap_hours: float = Field(default=12.0, gt=0)
    min_conformity: float = Field(default=0.9, gt=0, le=1)

    def to_domain(self) -> TimezoneAuditConfig:
        return TimezoneAuditConfig(**self.model_dump())


class StructureReportingSchema(_Strict):
    output_dir: str = "reports"
    session_timezone: str = "Europe/Athens"
    text_report: bool = True
    explorer_html: bool = True
    captures: bool = True
    max_explorer_bars: int = Field(default=60_000, ge=0)

    def to_domain(self) -> StructureReportingConfig:
        return StructureReportingConfig(**self.model_dump())


class ImpulseSchema(_Strict):
    enabled: bool = True
    symbol: str = "XAUUSD"
    structure_side: Literal["bid", "ask", "mid"] = "bid"
    data: StructureDataSchema = Field(default_factory=StructureDataSchema)
    aggregation: AggregationSchema = Field(default_factory=AggregationSchema)
    #: Gráfico -> impulsos que dibuja, el primero el principal. `None` deja el
    #: reparto por defecto, que es el que usa el propietario.
    charts: dict[str, list[str]] | None = None
    rules: ImpulseRulesSchema = Field(default_factory=ImpulseRulesSchema)
    zones: ZonesSchema = Field(default_factory=ZonesSchema)
    #: Fase 3.0. Apagada por defecto, igual que las zonas de la 2.0.
    entries: EntriesSchema = Field(default_factory=EntriesSchema)
    timezone_audit: TimezoneAuditSchema = Field(default_factory=TimezoneAuditSchema)
    reporting: StructureReportingSchema = Field(default_factory=StructureReportingSchema)

    def to_domain(self) -> ImpulseConfig:
        return ImpulseConfig(
            enabled=self.enabled,
            symbol=self.symbol,
            structure_side=self.structure_side,
            data=self.data.to_domain(),
            aggregation=self.aggregation.to_domain(),
            charts=(
                ChartsConfig()
                if self.charts is None
                else ChartsConfig(
                    {chart: tuple(overlays) for chart, overlays in self.charts.items()}
                )
            ),
            rules=self.rules.to_domain(),
            zones=self.zones.to_domain(),
            entries=self.entries.to_domain(),
            timezone_audit=self.timezone_audit.to_domain(),
            reporting=self.reporting.to_domain(),
        )
