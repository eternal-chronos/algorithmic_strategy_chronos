"""Caso de uso: calcular las zonas UL y OB de los impulsos ya detectados (fase 2.0).

**Esto mide y dibuja; no decide nada.** La tabla de impulsos, los eventos de
rotura y el estado barra a barra salen exactamente iguales con este módulo dentro
que fuera, y hay un test que lo fija. En la fase 2.1 las zonas pasarán a decidir
la vida y la muerte de los impulsos; hasta que el propietario audite lo que se
dibuja aquí, la rotura sigue siendo por línea.

Las zonas no necesitan volver a recorrer la historia: el detector ya registró
qué vela fija cada nivel (`index_extreme` e `index_anchor`), así que se derivan
de esas dos velas y del OHLC de la temporalidad. Por eso este módulo no toca el
detector ni lo vuelve a ejecutar.

Sobre las tres unidades de altura: el oro pasó de ~1.200 a ~4.300 USD en el
histórico, así que una altura en dólares no es comparable entre 2018 y 2025. El
ATR y el porcentaje del precio son obligatorios y no decorativos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from chronos.application.structure.config import ImpulseConfig
from chronos.application.structure.detect_impulses import ImpulseRun, TimeframeAnalysis
from chronos.domain.structure.enums import BreakKind, ImpulseDirection
from chronos.domain.structure.impulse import BreakEvent, DominantImpulse
from chronos.domain.structure.zones import (
    CandleSeries,
    Zone,
    ZoneBook,
    ZoneKind,
    last_zone,
    order_block_zone,
)

#: Columnas que pide el §4, en su orden.
ZONE_COLUMNS = (
    "id_num",
    "timeframe",
    "tipo",
    "direccion_id",
    "ts_vela_definitoria",
    "ts_confirmacion",
    "ts_nacimiento_zona",
    "borde_interior",
    "borde_exterior",
    "altura_usd",
    "altura_atr",
    "altura_pct_precio",
    "extendida_a_vela_siguiente",
    "config_hash",
)

#: Material de auditoría: posiciones en la serie y contexto del ID. Lo que se
#: mide en barras se cuenta con índices y no con marcas de tiempo, igual que en
#: la fase 1: entre el viernes y el domingo hay dos días de reloj y cero velas.
ZONE_AUDIT_COLUMNS = (
    "color_vela_definitoria",
    "indice_vela_definitoria",
    "indice_confirmacion",
    "barras_hasta_confirmacion",
    "altura_cero",
    "ts_constitucion_id",
    "ts_fin_id",
    "tipo_rotura_salida",
    "atr_previo",
)

#: Un registro por rotura del histórico con la zona que le correspondía (§7.7).
SURVIVAL_COLUMNS = (
    "timeframe",
    "anio",
    "id_num",
    "tipo_rotura",
    "ts_rotura",
    "cierre",
    "zona",
    "borde_interior",
    "borde_exterior",
    "cerro_dentro",
    "sin_zona",
)


@dataclass(frozen=True, slots=True)
class ImpulseZones:
    """Las dos zonas de un impulso. El UL siempre existe; el OB puede no existir."""

    id_num: int
    timeframe: str
    direction: ImpulseDirection
    year: int
    last: Zone
    #: `None` cuando ninguna vela del color del impulso superó la vela del ancla
    #: mientras el ID estuvo vigente. Es un estado legítimo, no un fallo: en la
    #: fase 2.1 esos impulsos se romperán por línea.
    order_block: Zone | None
    #: ATR previo a la constitución del ID, el mismo con el que la fase 1
    #: normaliza su rango. Las dos zonas del ID comparten denominador para que
    #: la comparación de alturas de 7.5 sea entre ellas y no entre dos ATR.
    atr: float
    #: Los dos niveles del ID. Se copian para poder medir el rango del impulso al
    #: lado de sus zonas sin volver a la tabla de la fase 1.
    anchor: float
    extreme: float
    index_constitution: int
    index_end: int | None
    ts_constitution: datetime
    ts_end: datetime | None
    exit_break: BreakKind | None

    @property
    def has_order_block(self) -> bool:
        return self.order_block is not None

    @property
    def bars_to_confirmation(self) -> int | None:
        """Barras entre la constitución del ID y la confirmación de su OB.

        Sale **negativo** cuando el OB se confirmó dentro de la pierna, antes de
        que el ID naciera, que es el caso corriente: la vela que confirma suele
        ser la que arranca la pierna. No se recorta a cero porque el signo es
        justo lo que distingue un OB ya hecho al nacer el ID de uno que el
        propietario tuvo que esperar.
        """
        if self.order_block is None or self.order_block.index_confirmation is None:
            return None
        return self.order_block.index_confirmation - self.index_constitution

    @property
    def bars_from_ob_candle(self) -> int | None:
        """Barras entre la vela del OB y la que lo confirma. Siempre >= 1.

        Es la lectura literal de "cuánto tarda el OB en confirmarse" (§7.2): se
        cuenta desde la vela que lo define, que es cuando empieza la espera.
        `bars_to_confirmation` mide otra cosa —si al nacer el ID el OB ya estaba
        hecho— y las dos se reportan porque responden a preguntas distintas.
        """
        if self.order_block is None or self.order_block.index_confirmation is None:
            return None
        return self.order_block.index_confirmation - self.order_block.index_defining

    @property
    def zones_overlap(self) -> bool:
        """§7.6 — el UL y el OB del mismo ID se pisan en precio.

        Se espera que no ocurra casi nunca: el UL vive pegado al extremo y el OB
        a la vela del ancla, que son los dos límites opuestos del rango. Cuando
        ocurre, el ID es tan corto que sus dos velas definitorias se solapan.
        """
        if self.order_block is None:
            return False
        return self.last.low <= self.order_block.high and self.order_block.low <= self.last.high

    def zones(self) -> tuple[Zone, ...]:
        return (self.last,) if self.order_block is None else (self.last, self.order_block)


@dataclass(frozen=True, slots=True)
class TimeframeZones:
    """Zonas de una temporalidad, con la tabla que se persiste."""

    timeframe: str
    items: tuple[ImpulseZones, ...]
    book: ZoneBook
    table: pd.DataFrame
    #: Un registro por rotura con la zona que le tocaba (§7.7).
    survivals: pd.DataFrame

    @property
    def with_order_block(self) -> tuple[ImpulseZones, ...]:
        return tuple(item for item in self.items if item.has_order_block)

    @property
    def without_order_block(self) -> tuple[ImpulseZones, ...]:
        return tuple(item for item in self.items if not item.has_order_block)


@dataclass(frozen=True, slots=True)
class ZonesRun:
    """Resultado de la fase 2.0 sobre una corrida del módulo 1."""

    enabled: bool
    config_hash: str
    per_timeframe: dict[str, TimeframeZones]

    @property
    def emits_nothing(self) -> bool:
        """`True` con las zonas apagadas: ni una zona, ni una fila, ni un fichero."""
        return not self.enabled and not self.per_timeframe

    def table(self) -> pd.DataFrame:
        """Todas las zonas de todas las temporalidades, ya ordenadas."""
        frames = [item.table for item in self.per_timeframe.values() if not item.table.empty]
        if not frames:
            return pd.DataFrame(columns=[*ZONE_COLUMNS, *ZONE_AUDIT_COLUMNS])
        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values(
            ["timeframe", "ts_nacimiento_zona", "id_num", "tipo"]
        ).reset_index(drop=True)

    def survivals(self) -> pd.DataFrame:
        frames = [
            item.survivals for item in self.per_timeframe.values() if not item.survivals.empty
        ]
        if not frames:
            return pd.DataFrame(columns=list(SURVIVAL_COLUMNS))
        return pd.concat(frames, ignore_index=True)


def detect_zones(run: ImpulseRun, config: ImpulseConfig | None = None) -> ZonesRun:
    """Calcula las zonas de todos los impulsos publicados de la corrida.

    Con `zones.enabled = False` —o con el módulo 1 apagado— no se emite nada, ni
    siquiera un diccionario vacío con las temporalidades dentro.
    """
    config = config or run.config
    if not run.enabled or not config.zones.enabled:
        return ZonesRun(enabled=False, config_hash=run.config_hash, per_timeframe={})

    return ZonesRun(
        enabled=True,
        config_hash=run.config_hash,
        per_timeframe={
            timeframe: _zones_of(analysis, run.config_hash)
            for timeframe, analysis in run.analyses.items()
        },
    )


# --- Cálculo ----------------------------------------------------------------


def _zones_of(analysis: TimeframeAnalysis, config_hash: str) -> TimeframeZones:
    series = CandleSeries.of(analysis.bars)
    atr_by_id = _atr_by_id(analysis)

    items: list[ImpulseZones] = []
    for impulse in analysis.impulses:
        # Sólo los publicables: un ID de calentamiento no sale en ninguna tabla
        # de la fase 1 y sus zonas no tendrían dónde compararse.
        if not impulse.publishable:
            continue
        items.append(_zones_of_impulse(series, impulse, atr_by_id))

    zones = tuple(zone for item in items for zone in item.zones())
    book = ZoneBook(analysis.timeframe, zones)
    for item in items:
        if not item.has_order_block:
            book.record_missing_order_block(item.id_num)

    return TimeframeZones(
        timeframe=analysis.timeframe,
        items=tuple(items),
        book=book,
        table=_build_table(items, config_hash),
        survivals=_survivals(analysis, items),
    )


def _zones_of_impulse(
    series: CandleSeries, impulse: DominantImpulse, atr_by_id: dict[int, float]
) -> ImpulseZones:
    return ImpulseZones(
        id_num=impulse.id_num,
        timeframe=impulse.timeframe,
        direction=impulse.direction,
        year=impulse.ts_constitution.year,
        # El UL no se remarca: lo fija la vela del extremo con la que el ID se
        # constituyó y ahí se queda, aunque el extremo se estire después.
        last=last_zone(
            series,
            id_num=impulse.id_num,
            timeframe=impulse.timeframe,
            direction=impulse.direction,
            index_extreme=impulse.index_extreme_at_constitution,
            ts_constitution=impulse.ts_constitution,
        ),
        order_block=order_block_zone(
            series,
            id_num=impulse.id_num,
            timeframe=impulse.timeframe,
            direction=impulse.direction,
            index_anchor=impulse.index_anchor,
            ts_constitution=impulse.ts_constitution,
            index_end=impulse.index_end,
        ),
        atr=atr_by_id.get(impulse.id_num, float("nan")),
        anchor=impulse.anchor,
        extreme=impulse.extreme,
        index_constitution=impulse.index_constitution,
        index_end=impulse.index_end,
        ts_constitution=impulse.ts_constitution,
        ts_end=impulse.ts_end,
        exit_break=impulse.exit_break_kind,
    )


def _atr_by_id(analysis: TimeframeAnalysis) -> dict[int, float]:
    """ATR previo a la constitución, tal como lo escribió la fase 1."""
    table = analysis.table
    if table.empty:
        return {}
    return dict(
        zip(
            table["id_num"].astype(int),
            pd.to_numeric(table["atr_previo"], errors="coerce"),
            strict=True,
        )
    )


# --- Tablas -----------------------------------------------------------------


def _build_table(items: Sequence[ImpulseZones], config_hash: str) -> pd.DataFrame:
    columns = [*ZONE_COLUMNS, *ZONE_AUDIT_COLUMNS]
    rows = [
        _zone_row(item, zone, config_hash) for item in items for zone in item.zones()
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns]


def _zone_row(item: ImpulseZones, zone: Zone, config_hash: str) -> dict[str, object]:
    height = zone.height
    return {
        "id_num": item.id_num,
        "timeframe": item.timeframe,
        "tipo": zone.kind.value,
        "direccion_id": item.direction.value,
        "ts_vela_definitoria": zone.ts_defining,
        "ts_confirmacion": zone.ts_confirmation,
        "ts_nacimiento_zona": zone.ts_birth,
        "borde_interior": zone.inner,
        "borde_exterior": zone.outer,
        "altura_usd": height,
        "altura_atr": _safe_ratio(height, item.atr),
        # El borde interior es el nivel del ID en el UL y el techo de la vela del
        # ancla en el OB: en los dos es un precio de la propia zona, así que la
        # fracción se lee igual en 2018 y en 2025.
        "altura_pct_precio": _safe_ratio(height, abs(zone.inner)),
        # Sólo el UL puede extenderse. En el OB la columna va a `None` en vez de
        # a `False`: decir "no se extendió" sugeriría que podía hacerlo.
        "extendida_a_vela_siguiente": zone.extended if zone.kind is ZoneKind.LAST else None,
        "config_hash": config_hash,
        "color_vela_definitoria": zone.defining_body.value,
        "indice_vela_definitoria": zone.index_defining,
        "indice_confirmacion": zone.index_confirmation,
        "barras_hasta_confirmacion": (
            item.bars_to_confirmation if zone.kind is ZoneKind.ORDER_BLOCK else None
        ),
        "altura_cero": zone.is_flat,
        "ts_constitucion_id": item.ts_constitution,
        "ts_fin_id": item.ts_end,
        "tipo_rotura_salida": item.exit_break.value if item.exit_break else None,
        "atr_previo": item.atr,
    }


def _survivals(
    analysis: TimeframeAnalysis, items: Sequence[ImpulseZones]
) -> pd.DataFrame:
    """§7.7 — qué rotura del histórico cerró **dentro** de la zona que le tocaba.

    Es una medición descriptiva sobre los datos que ya existen, no una
    re-ejecución del módulo con otra regla: aquí no se resucita ningún impulso ni
    se recalcula lo que habría pasado después. Sólo se mira, rotura a rotura, si
    el cierre que mató al ID se quedó dentro de la zona correspondiente.

    Qué zona le toca a cada rotura no es una elección: la rotura a favor cruza el
    extremo, y la zona que hay pegada al extremo es el UL; la rotura en contra
    cruza el ancla, y la zona de la vela del ancla es el OB.
    """
    by_id = {item.id_num: item for item in items}
    rows: list[dict[str, object]] = []
    for event in analysis.events:
        item = by_id.get(event.broken_id_num)
        if item is None:
            continue  # ID de calentamiento: no se publicó y no tiene zonas
        zone = _zone_for(item, event)
        rows.append(
            {
                "timeframe": analysis.timeframe,
                "anio": event.timestamp.year,
                "id_num": event.broken_id_num,
                "tipo_rotura": event.kind.value,
                "ts_rotura": event.timestamp,
                "cierre": event.close,
                "zona": (
                    ZoneKind.LAST.value
                    if event.kind is BreakKind.A_FAVOR
                    else ZoneKind.ORDER_BLOCK.value
                ),
                "borde_interior": None if zone is None else zone.inner,
                "borde_exterior": None if zone is None else zone.outer,
                # Sin OB confirmado no hay zona que consultar y el ID se rompe
                # por línea: no sobrevive, y eso no es lo mismo que "cerró fuera".
                "cerro_dentro": False if zone is None else zone.contains(event.close),
                "sin_zona": zone is None,
            }
        )
    return pd.DataFrame(rows, columns=list(SURVIVAL_COLUMNS)) if rows else pd.DataFrame(
        columns=list(SURVIVAL_COLUMNS)
    )


def _zone_for(item: ImpulseZones, event: BreakEvent) -> Zone | None:
    if event.kind is BreakKind.A_FAVOR:
        return item.last
    return item.order_block


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator


# --- Tablas del informe (§7) -------------------------------------------------

TOTAL_ROW = "TOTAL"

#: Percentiles de altura que pide el §7.3, y que el §7.4 repite para el OB.
HEIGHT_PERCENTILES = (10, 25, 50, 75, 90)

#: Las tres unidades en que se da toda altura. Los dólares solos no comparan
#: 2018 con 2025; el ATR y el % del precio sí.
HEIGHT_UNITS = (
    ("usd", "altura_usd"),
    ("atr", "altura_atr"),
    ("pct", "altura_pct_precio"),
)


def coverage_by_year(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.1 — cuántos ID tienen UL y cuántos llegan a tener OB confirmado.

    **La cifra que manda de la fase 2.0 es `pct_sin_ob`**: en la fase 2.1 esos
    impulsos se romperán por línea porque no tienen zona con la que romper.
    """
    columns = ["anio", "impulsos", "con_ul", "pct_ul", "con_ob", "pct_ob", "sin_ob", "pct_sin_ob"]
    if not measurement.items:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        total = len(group)
        with_ob = sum(1 for item in group if item.has_order_block)
        return {
            "anio": label,
            "impulsos": total,
            # El UL existe siempre: lo fija la misma vela que fija el extremo, y
            # todo ID tiene extremo. Se cuenta igualmente porque un 99 % aquí
            # sería un fallo del motor y hay que poder verlo.
            "con_ul": total,
            "pct_ul": 1.0 if total else float("nan"),
            "con_ob": with_ob,
            "pct_ob": with_ob / total if total else float("nan"),
            "sin_ob": total - with_ob,
            "pct_sin_ob": (total - with_ob) / total if total else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(measurement.items)]
    rows.append(row(TOTAL_ROW, measurement.items))
    return pd.DataFrame(rows, columns=columns)


def confirmation_delay(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.2 — barras que tarda el OB en confirmarse, por año.

    `desde_la_vela_ob` es la espera literal y nunca baja de 1.
    `desde_la_constitucion` puede salir **negativa**: significa que el OB ya
    estaba confirmado cuando el ID nació, porque la vela que lo confirma suele
    ser la misma que arranca la pierna.
    """
    columns = [
        "anio", "n", "mediana", "p10", "p90", "maximo",
        "mediana_desde_constitucion", "ya_confirmado_al_nacer", "pct_ya_confirmado",
    ]
    confirmed = [item for item in measurement.items if item.has_order_block]
    if not confirmed:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        waits = _finite([item.bars_from_ob_candle for item in group])
        since = _finite([item.bars_to_confirmation for item in group])
        ready = sum(1 for item in group if (item.bars_to_confirmation or 0) <= 0)
        return {
            "anio": label,
            "n": int(waits.size),
            "mediana": _percentile(waits, 50),
            "p10": _percentile(waits, 10),
            "p90": _percentile(waits, 90),
            "maximo": float(waits.max()) if waits.size else float("nan"),
            "mediana_desde_constitucion": _percentile(since, 50),
            "ya_confirmado_al_nacer": ready,
            "pct_ya_confirmado": ready / len(group) if group else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(confirmed)]
    rows.append(row(TOTAL_ROW, confirmed))
    return pd.DataFrame(rows, columns=columns)


def heights_by_year(measurement: TimeframeZones, kind: ZoneKind) -> pd.DataFrame:
    """§7.3 y §7.4 — altura de una zona en las tres unidades, por año.

    El UL añade dos columnas que el OB no puede tener: las zonas de altura cero
    —la vela del extremo no tenía mecha— y las estiradas a la vela siguiente.
    """
    units = [f"{name}_p{value}" for name, _ in HEIGHT_UNITS for value in HEIGHT_PERCENTILES]
    columns = ["anio", "n", *units]
    if kind is ZoneKind.LAST:
        columns += ["altura_cero", "pct_altura_cero", "extendidas", "pct_extendidas"]

    table = measurement.table
    if table.empty:
        return pd.DataFrame(columns=columns)
    subset = table[table["tipo"] == kind.value]
    if subset.empty:
        return pd.DataFrame(columns=columns)
    subset = subset.assign(anio=pd.DatetimeIndex(subset["ts_nacimiento_zona"]).year)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        item: dict[str, object] = {"anio": label, "n": len(group)}
        for name, column in HEIGHT_UNITS:
            values = _clean(group[column])
            for value in HEIGHT_PERCENTILES:
                item[f"{name}_p{value}"] = _percentile(values, value)
        if kind is ZoneKind.LAST:
            flat = int(group["altura_cero"].sum())
            extended = int(group["extendida_a_vela_siguiente"].fillna(False).sum())
            item["altura_cero"] = flat
            item["pct_altura_cero"] = flat / len(group) if len(group) else float("nan")
            item["extendidas"] = extended
            item["pct_extendidas"] = extended / len(group) if len(group) else float("nan")
        return item

    rows = [row(str(year), group) for year, group in subset.groupby("anio", sort=True)]
    rows.append(row(TOTAL_ROW, subset))
    return pd.DataFrame(rows, columns=columns)


def height_comparison(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.5 — UL frente a OB sobre los ID que tienen las dos zonas.

    Se espera que el OB salga sistemáticamente mayor porque incluye el cuerpo de
    su vela, mientras que el UL ocupa sólo el tramo de mecha. Las excepciones se
    cuentan; no se explican aquí.
    """
    columns = [
        "anio", "pares", "ul_mediana_atr", "ob_mediana_atr", "ob_mayor", "pct_ob_mayor",
        "empates", "ul_mayor", "pct_ul_mayor",
    ]
    pairs = [item for item in measurement.items if item.has_order_block]
    if not pairs:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        # `group` sólo trae ID con las dos zonas, pero el tipo de `order_block`
        # sigue siendo opcional: se desempaqueta una vez en vez de repetir el
        # mismo `assert` en cada comprensión.
        pairs = [
            (item, item.order_block) for item in group if item.order_block is not None
        ]
        bigger = sum(1 for item, block in pairs if block.height > item.last.height)
        equal = sum(1 for item, block in pairs if block.height == item.last.height)
        smaller = len(pairs) - bigger - equal
        return {
            "anio": label,
            "pares": len(group),
            "ul_mediana_atr": _percentile(
                _finite([_safe_ratio(item.last.height, item.atr) for item in group]), 50
            ),
            "ob_mediana_atr": _percentile(
                _finite([_safe_ratio(block.height, item.atr) for item, block in pairs]), 50
            ),
            "ob_mayor": bigger,
            "pct_ob_mayor": bigger / len(group) if group else float("nan"),
            "empates": equal,
            "ul_mayor": smaller,
            "pct_ul_mayor": smaller / len(group) if group else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(pairs)]
    rows.append(row(TOTAL_ROW, pairs))
    return pd.DataFrame(rows, columns=columns)


def exception_profile(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.5 — en qué se diferencian los ID donde el UL sale mayor que el OB.

    La tabla no afirma un mecanismo: pone las dos poblaciones una al lado de la
    otra y deja ver de qué lado viene la diferencia —de un UL inusualmente alto,
    de un OB inusualmente bajo, o de la extensión del UL—.
    """
    columns = [
        "poblacion", "pares", "ul_mediana_atr", "ob_mediana_atr",
        "ul_extendidos", "pct_extendidos",
    ]
    pairs = [
        (item, item.order_block) for item in measurement.items if item.order_block is not None
    ]
    if not pairs:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[tuple[ImpulseZones, Zone]]) -> dict[str, object]:
        extended = sum(1 for item, _ in group if item.last.extended)
        return {
            "poblacion": label,
            "pares": len(group),
            "ul_mediana_atr": _percentile(
                _finite([_safe_ratio(item.last.height, item.atr) for item, _ in group]), 50
            ),
            "ob_mediana_atr": _percentile(
                _finite([_safe_ratio(block.height, item.atr) for item, block in group]), 50
            ),
            "ul_extendidos": extended,
            "pct_extendidos": extended / len(group) if group else float("nan"),
        }

    exceptions = [pair for pair in pairs if pair[0].last.height > pair[1].height]
    rest = [pair for pair in pairs if pair[0].last.height <= pair[1].height]
    return pd.DataFrame(
        [
            row("excepciones (UL > OB)", exceptions),
            row("el resto (OB >= UL)", rest),
            row(TOTAL_ROW, pairs),
        ],
        columns=columns,
    )


def overlap_profile(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.6 — en qué se diferencian los ID cuyas dos zonas se pisan.

    El rango del ID va en ATR porque es la única forma de comparar 2018 con
    2025. Si los que se solapan son los de rango minúsculo, la tabla lo enseña
    sin que nadie tenga que afirmarlo.
    """
    columns = ["poblacion", "pares", "rango_id_atr_mediano", "ul_mediana_atr", "ob_mediana_atr"]
    pairs = [
        (item, item.order_block) for item in measurement.items if item.order_block is not None
    ]
    if not pairs:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[tuple[ImpulseZones, Zone]]) -> dict[str, object]:
        return {
            "poblacion": label,
            "pares": len(group),
            "rango_id_atr_mediano": _percentile(
                _finite(
                    [
                        _safe_ratio(abs(item.extreme - item.anchor), item.atr)
                        for item, _ in group
                    ]
                ),
                50,
            ),
            "ul_mediana_atr": _percentile(
                _finite([_safe_ratio(item.last.height, item.atr) for item, _ in group]), 50
            ),
            "ob_mediana_atr": _percentile(
                _finite([_safe_ratio(block.height, item.atr) for item, block in group]), 50
            ),
        }

    touching = [pair for pair in pairs if pair[0].zones_overlap]
    apart = [pair for pair in pairs if not pair[0].zones_overlap]
    return pd.DataFrame(
        [row("se solapan", touching), row("no se solapan", apart), row(TOTAL_ROW, pairs)],
        columns=columns,
    )


def overlaps(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.6 — ID cuyas dos zonas se pisan en precio."""
    columns = ["anio", "pares", "se_solapan", "pct_solape"]
    pairs = [item for item in measurement.items if item.has_order_block]
    if not pairs:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        touching = sum(1 for item in group if item.zones_overlap)
        return {
            "anio": label,
            "pares": len(group),
            "se_solapan": touching,
            "pct_solape": touching / len(group) if group else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(pairs)]
    rows.append(row(TOTAL_ROW, pairs))
    return pd.DataFrame(rows, columns=columns)


def survival_estimate(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.7 — anticipo de la fase 2.1, **sólo medición**.

    Cuenta cuántas roturas del histórico actual cerraron dentro de la zona que
    les correspondía sin atravesarla entera. Esos ID habrían sobrevivido con la
    regla de la fase 2.1 en el instante de su rotura; lo que hubieran hecho
    después no se simula, porque cambiar la regla cambia toda la historia
    posterior y eso es una re-ejecución, no una estimación.
    """
    columns = [
        "tipo_rotura", "roturas", "con_zona", "sin_zona", "cerro_dentro",
        "pct_sobre_roturas", "pct_sobre_las_que_tenian_zona",
    ]
    frame = measurement.survivals
    if frame.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for label, subset in (
        (BreakKind.A_FAVOR.value, frame[frame["tipo_rotura"] == BreakKind.A_FAVOR.value]),
        (BreakKind.EN_CONTRA.value, frame[frame["tipo_rotura"] == BreakKind.EN_CONTRA.value]),
        (TOTAL_ROW, frame),
    ):
        total = len(subset)
        missing = int(subset["sin_zona"].sum())
        inside = int(subset["cerro_dentro"].sum())
        rows.append(
            {
                "tipo_rotura": label,
                "roturas": total,
                "con_zona": total - missing,
                "sin_zona": missing,
                "cerro_dentro": inside,
                "pct_sobre_roturas": inside / total if total else float("nan"),
                "pct_sobre_las_que_tenian_zona": (
                    inside / (total - missing) if total - missing else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def survival_by_year(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.7 desglosado por año, con las dos clases de rotura en columnas."""
    columns = [
        "anio", "roturas", "favor_dentro", "favor_total",
        "contra_dentro", "contra_total", "contra_sin_ob", "sobrevivirian", "pct",
    ]
    frame = measurement.survivals
    if frame.empty:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: pd.DataFrame) -> dict[str, object]:
        favor = group[group["tipo_rotura"] == BreakKind.A_FAVOR.value]
        against = group[group["tipo_rotura"] == BreakKind.EN_CONTRA.value]
        surviving = int(group["cerro_dentro"].sum())
        return {
            "anio": label,
            "roturas": len(group),
            "favor_dentro": int(favor["cerro_dentro"].sum()),
            "favor_total": len(favor),
            "contra_dentro": int(against["cerro_dentro"].sum()),
            "contra_total": len(against),
            "contra_sin_ob": int(against["sin_zona"].sum()),
            "sobrevivirian": surviving,
            "pct": surviving / len(group) if len(group) else float("nan"),
        }

    rows = [row(str(year), group) for year, group in frame.groupby("anio", sort=True)]
    rows.append(row(TOTAL_ROW, frame))
    return pd.DataFrame(rows, columns=columns)


# --- Apoyo ------------------------------------------------------------------


def _by_year(
    items: Sequence[ImpulseZones],
) -> list[tuple[int, tuple[ImpulseZones, ...]]]:
    years = sorted({item.year for item in items})
    return [(year, tuple(item for item in items if item.year == year)) for year in years]


def _finite(values: Sequence[float | int | None]) -> np.ndarray:
    numeric = np.asarray(
        [float(value) for value in values if value is not None], dtype=float
    )
    return numeric[np.isfinite(numeric)] if numeric.size else numeric


def _clean(values: pd.Series) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return numeric[np.isfinite(numeric)]


def _percentile(values: np.ndarray, percentile: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, percentile))


__all__ = [
    "HEIGHT_PERCENTILES",
    "SURVIVAL_COLUMNS",
    "ZONE_AUDIT_COLUMNS",
    "ZONE_COLUMNS",
    "ImpulseZones",
    "TimeframeZones",
    "ZonesRun",
    "confirmation_delay",
    "coverage_by_year",
    "detect_zones",
    "exception_profile",
    "height_comparison",
    "heights_by_year",
    "overlap_profile",
    "overlaps",
    "survival_by_year",
    "survival_estimate",
]
