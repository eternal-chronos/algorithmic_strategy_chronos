"""Caso de uso: calcular las zonas de los impulsos ya detectados (fase 2.0).

**Esto mide y dibuja; no decide nada.** La tabla de impulsos, los eventos de
rotura y el estado barra a barra salen exactamente iguales con este módulo dentro
que fuera, y hay un test que lo fija. En la fase 2.1 las zonas pasarán a decidir
la vida y la muerte de los impulsos; hasta que el propietario audite lo que se
dibuja aquí, la rotura sigue siendo por línea.

Cada ID lleva **dos** zonas: el UL en el lado a favor y, en el lado en contra,
una sola de las otras dos. Es el PUL cuando el ID anterior iba en el **mismo
sentido** que éste: su extremo quedó por detrás, así que su UL es el nivel al que
el precio vuelve. Si aquel ID iba al revés hay que mirar dónde quedó su extremo:
cuando es el ancla de éste no sirve de nivel y se **hereda la zona en contra de
aquel ID**, y cuando quedó por detrás del ancla —el giro no lo trajo él sino una
constitución abortada posterior— el nivel es su propio UL. Las dos se llaman
APUL. Y hay un tercer APUL: el del ID nacido tras una **constitución abortada**
con el anterior en su mismo sentido, cuyo nivel sale del último ID interior del
retroceso anterior, que el detector fue a buscar corriendo la máquina ahí dentro.
El PUL y el APUL nunca conviven. Sólo se quedan sin ninguna los primeros ID de
cada temporalidad, que no tienen ID anterior.

Las zonas no necesitan volver a recorrer la historia: el detector ya registró qué
velas fijan cada nivel —`index_extreme` para el UL, y para la de en contra la
vela del cuerpo, el sentido de la mecha y la ventana en la que buscar su punta—,
así que se derivan de esas velas y del OHLC de la temporalidad. Por eso este
módulo no toca el detector ni lo vuelve a ejecutar.

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
from chronos.domain.structure.enums import (
    AntePenultimateOrigin,
    BreakKind,
    ImpulseDirection,
)
from chronos.domain.structure.impulse import BreakEvent, DominantImpulse
from chronos.domain.structure.zones import (
    CandleSeries,
    Zone,
    ZoneBook,
    ZoneKind,
    against_zone,
    last_zone,
)

#: Columnas que pide el §4, en su orden.
ZONE_COLUMNS = (
    "id_num",
    "timeframe",
    "tipo",
    "direccion_id",
    "ts_vela_definitoria",
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
    """Las dos zonas de un impulso: el UL a favor y, en contra, el PUL o el APUL.

    El UL existe siempre. En el lado en contra hay una zona y sólo una: el PUL
    cuando el ID anterior iba en el mismo sentido y dejó su extremo detrás, y si
    no el APUL, con los tres orígenes de `AntePenultimateOrigin`. `against`
    devuelve la que gobierna sin que quien pregunte tenga que mirar cuál.
    """

    id_num: int
    timeframe: str
    direction: ImpulseDirection
    year: int
    last: Zone
    #: El UL del ID anterior cuando aquél iba en el mismo sentido que éste.
    #: `None` cuando no hay ID anterior del que sacarlo —el principio del
    #: histórico— y cuando manda el APUL. Lo primero es un estado legítimo, no un
    #: fallo: ese ID se rompe por línea en el lado en contra.
    penultimate: Zone | None
    #: El lado en contra cuando no lo lleva el PUL: la zona heredada del ID
    #: anterior, el UL de aquel ID cuando iba al revés y su extremo quedó por
    #: detrás del ancla, o la del último ID interior del retroceso tras una
    #: constitución abortada. Sustituye al PUL, no lo acompaña.
    ante_penultimate: Zone | None
    #: Hacia dónde iba el ID anterior. Es lo que dice si este ID lleva PUL —sólo
    #: si iba en el mismo sentido—. Se copia del impulso para poder contarlo en
    #: el informe sin volver a la lista de impulsos.
    penultimate_direction: ImpulseDirection | None
    #: Cuál de los tres APUL lleva, o `None` si no lleva APUL. Lo decidió el
    #: detector al constituir: por los bordes de la zona no se distinguen.
    ante_penultimate_origin: AntePenultimateOrigin | None
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
    def has_penultimate(self) -> bool:
        return self.penultimate is not None

    @property
    def has_ante_penultimate(self) -> bool:
        return self.ante_penultimate is not None

    @property
    def against(self) -> Zone | None:
        """La zona del lado en contra. `None` si ese lado se rompe por línea."""
        return self.penultimate if self.penultimate is not None else self.ante_penultimate

    @property
    def against_is_inherited(self) -> bool:
        """`True` si el APUL es el que le **prestó** el ID anterior.

        Pasa cuando aquel ID iba al revés que éste y su extremo es el ancla de
        éste, así que no queda ningún nivel detrás y el que vale es el que aquél
        llevaba. Los otros dos APUL salen de un extremo: el del propio ID
        anterior contrario cuando sí quedó por detrás del ancla, y el del ID
        interior de un retroceso. Quien dibuja no puede distinguirlos por los
        bordes, y son tres historias distintas.
        """
        return self.ante_penultimate_origin is AntePenultimateOrigin.INHERITED

    @property
    def against_is_counter_extreme(self) -> bool:
        """`True` si el APUL es el UL del ID anterior, que iba al revés.

        El caso del giro que no trajo aquel ID sino una constitución abortada
        posterior: su extremo quedó por detrás del ancla de éste, así que sigue
        siendo el nivel al que volver.
        """
        return self.ante_penultimate_origin is AntePenultimateOrigin.COUNTER_EXTREME

    @property
    def zones_overlap(self) -> bool:
        """§7.6 — las dos zonas del mismo ID se pisan en precio.

        Se espera que no ocurra casi nunca: el UL vive pegado al extremo del ID
        y la zona en contra en un extremo anterior, que son los dos límites
        opuestos del rango. Cuando ocurre, el ID es tan corto que sus dos velas
        definitorias se solapan.
        """
        against = self.against
        if against is None:
            return False
        return self.last.low <= against.high and against.low <= self.last.high

    def zones(self) -> tuple[Zone, ...]:
        against = self.against
        return (self.last,) if against is None else (self.last, against)


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
    def with_penultimate(self) -> tuple[ImpulseZones, ...]:
        return tuple(item for item in self.items if item.has_penultimate)

    @property
    def with_ante_penultimate(self) -> tuple[ImpulseZones, ...]:
        """ID que llevan APUL en vez de PUL, por cualquiera de los dos motivos."""
        return tuple(item for item in self.items if item.has_ante_penultimate)

    @property
    def with_inherited_ante_penultimate(self) -> tuple[ImpulseZones, ...]:
        """ID cuyo APUL es el que les prestó un ID anterior que iba al revés."""
        return tuple(item for item in self.items if item.against_is_inherited)

    @property
    def with_counter_extreme_ante_penultimate(self) -> tuple[ImpulseZones, ...]:
        """ID cuyo APUL es el UL del ID anterior, que iba al revés y quedó detrás."""
        return tuple(item for item in self.items if item.against_is_counter_extreme)

    @property
    def without_against_zone(self) -> tuple[ImpulseZones, ...]:
        """ID sin zona en contra, así que rompen por línea en ese lado. Son los
        primeros de cada temporalidad, que todavía no tienen ID anterior.
        """
        return tuple(item for item in self.items if item.against is None)


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
        if item.against is None:
            book.record_missing_penultimate(item.id_num)

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
        # Y la zona en contra, que es una sola: el PUL cuando el ID anterior iba
        # en el mismo sentido, y si no el APUL —el que le prestó aquel ID, su
        # propio UL si quedó por detrás del ancla, o el del último ID interior de
        # su retroceso—. Cuál es y sobre qué velas ya lo decidió el detector al
        # constituir; aquí no se re-deriva nada.
        penultimate=_against_zone_of(series, impulse, ZoneKind.PENULTIMATE),
        ante_penultimate=_against_zone_of(series, impulse, ZoneKind.ANTE_PENULTIMATE),
        penultimate_direction=impulse.penultimate_direction,
        ante_penultimate_origin=impulse.ante_penultimate_origin,
        atr=atr_by_id.get(impulse.id_num, float("nan")),
        anchor=impulse.anchor,
        extreme=impulse.extreme,
        index_constitution=impulse.index_constitution,
        index_end=impulse.index_end,
        ts_constitution=impulse.ts_constitution,
        ts_end=impulse.ts_end,
        exit_break=impulse.exit_break_kind,
    )


def _against_zone_of(
    series: CandleSeries, impulse: DominantImpulse, kind: ZoneKind
) -> Zone | None:
    """La zona en contra del impulso si es de ese tipo, y si no `None`.

    Un ID lleva una sola zona en contra, así que de las dos llamadas —PUL y
    APUL— una devuelve siempre `None`. Las dos velas, el sentido de la mecha y
    la ventana en la que buscar la punta vienen del impulso: el detector las
    apuntó al constituir y no se pueden re-derivar después.
    """
    if _against_kind(impulse) is not kind:
        return None
    return against_zone(
        series,
        kind=kind,
        id_num=impulse.id_num,
        timeframe=impulse.timeframe,
        direction=impulse.direction,
        index_body=impulse.index_against,
        tip_window=impulse.against_tip_window,
        zone_direction=impulse.against_direction,
        ts_constitution=impulse.ts_constitution,
    )


def _against_kind(impulse: DominantImpulse) -> ZoneKind | None:
    if impulse.has_penultimate:
        return ZoneKind.PENULTIMATE
    if impulse.has_ante_penultimate:
        return ZoneKind.ANTE_PENULTIMATE
    return None


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
        "ts_nacimiento_zona": zone.ts_birth,
        "borde_interior": zone.inner,
        "borde_exterior": zone.outer,
        "altura_usd": height,
        "altura_atr": _safe_ratio(height, item.atr),
        # El borde interior es un precio de la propia zona en las dos —el nivel
        # del ID en el UL, el borde que mira al ID en el PUL—, así que la
        # fracción se lee igual en 2018 y en 2025.
        "altura_pct_precio": _safe_ratio(height, abs(zone.inner)),
        # Sólo el UL puede extenderse. En el PUL la columna va a `None` en vez de
        # a `False`: decir "no se extendió" sugeriría que podía hacerlo.
        "extendida_a_vela_siguiente": zone.extended if zone.kind is ZoneKind.LAST else None,
        "config_hash": config_hash,
        "color_vela_definitoria": zone.defining_body.value,
        "indice_vela_definitoria": zone.index_defining,
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
    cruza el ancla, y la zona que hay detrás en ese lado es la que gobierne ese
    lado en ese ID, el PUL o el APUL.
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
                # El nombre sale de la zona que había. Sin ninguna en el lado en
                # contra se escribe PUL: es la zona que FALTABA, y la columna
                # `sin_zona` de al lado ya dice que no había ninguna.
                "zona": (
                    ZoneKind.LAST.value
                    if event.kind is BreakKind.A_FAVOR
                    else (zone.kind.value if zone is not None else ZoneKind.PENULTIMATE.value)
                ),
                "borde_interior": None if zone is None else zone.inner,
                "borde_exterior": None if zone is None else zone.outer,
                # Sin PUL no hay zona que consultar y el ID se rompe por línea:
                # no sobrevive, y eso no es lo mismo que "cerró fuera".
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
    return item.against


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    return numerator / denominator


# --- Tablas del informe (§7) -------------------------------------------------

TOTAL_ROW = "TOTAL"

#: Percentiles de altura que pide el §7.3, y que el §7.4 repite para el PUL.
HEIGHT_PERCENTILES = (10, 25, 50, 75, 90)

#: Las tres unidades en que se da toda altura. Los dólares solos no comparan
#: 2018 con 2025; el ATR y el % del precio sí.
HEIGHT_UNITS = (
    ("usd", "altura_usd"),
    ("atr", "altura_atr"),
    ("pct", "altura_pct_precio"),
)


def coverage_by_year(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.1 — qué lleva cada ID en cada uno de sus dos lados.

    El lado en contra tiene tres estados: PUL, APUL —el ID nació tras una
    constitución abortada—, o nada porque no hay ID anterior del que sacarlo.
    Los tres suman `impulsos`.

    **La cifra que manda de la fase 2.0 es `pct_sin_zona`**: en la fase 2.1 esos
    impulsos —y sólo ésos— se romperán por línea en el lado en contra.
    """
    columns = [
        "anio", "impulsos", "con_ul", "pct_ul", "con_pul", "pct_pul",
        "con_apul", "pct_apul", "sin_zona", "pct_sin_zona",
    ]
    if not measurement.items:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        total = len(group)
        with_pul = sum(1 for item in group if item.has_penultimate)
        with_apul = sum(1 for item in group if item.has_ante_penultimate)
        without = total - with_pul - with_apul
        return {
            "anio": label,
            "impulsos": total,
            # El UL existe siempre: lo fija la misma vela que fija el extremo, y
            # todo ID tiene extremo. Se cuenta igualmente porque un 99 % aquí
            # sería un fallo del motor y hay que poder verlo.
            "con_ul": total,
            "pct_ul": 1.0 if total else float("nan"),
            "con_pul": with_pul,
            "pct_pul": with_pul / total if total else float("nan"),
            "con_apul": with_apul,
            "pct_apul": with_apul / total if total else float("nan"),
            "sin_zona": without,
            "pct_sin_zona": without / total if total else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(measurement.items)]
    rows.append(row(TOTAL_ROW, measurement.items))
    return pd.DataFrame(rows, columns=columns)


def heights_by_year(measurement: TimeframeZones, kind: ZoneKind) -> pd.DataFrame:
    """§7.3, §7.4 y §7.4b — altura de una zona en las tres unidades, por año.

    Las dos pueden salir de altura cero: el UL cuando la vela del extremo no dejó
    mecha, y el PUL cuando es esa misma mecha —el ID anterior iba en el mismo
    sentido— o cuando la vela era un doji y no tiene cuerpo. Sólo el UL puede
    estirarse a la vela siguiente, así que esa columna es suya.
    """
    units = [f"{name}_p{value}" for name, _ in HEIGHT_UNITS for value in HEIGHT_PERCENTILES]
    columns = ["anio", "n", *units]
    columns += ["altura_cero", "pct_altura_cero"]
    if kind is ZoneKind.LAST:
        columns += ["extendidas", "pct_extendidas"]

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
        flat = int(group["altura_cero"].sum())
        item["altura_cero"] = flat
        item["pct_altura_cero"] = flat / len(group) if len(group) else float("nan")
        if kind is ZoneKind.LAST:
            extended = int(group["extendida_a_vela_siguiente"].fillna(False).sum())
            item["extendidas"] = extended
            item["pct_extendidas"] = extended / len(group) if len(group) else float("nan")
        return item

    rows = [row(str(year), group) for year, group in subset.groupby("anio", sort=True)]
    rows.append(row(TOTAL_ROW, subset))
    return pd.DataFrame(rows, columns=columns)


def height_comparison(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.5 — UL frente a PUL sobre los ID que tienen las dos zonas.

    El PUL es el UL de otro ID, así que las dos alturas son comparables. Se
    espera que el PUL salga algo mayor: comparte el borde del cuerpo con aquel UL
    pero su punta llega a la mecha más lejana de toda la vida de aquel ID, no
    sólo a la de su vela del extremo. Las excepciones se cuentan; no se explican
    aquí.
    """
    columns = [
        "anio", "pares", "ul_mediana_atr", "pul_mediana_atr", "pul_mayor", "pct_pul_mayor",
        "empates", "ul_mayor", "pct_ul_mayor",
    ]
    pairs = [item for item in measurement.items if item.has_penultimate]
    if not pairs:
        return pd.DataFrame(columns=columns)

    def row(label: str, group: Sequence[ImpulseZones]) -> dict[str, object]:
        # `group` sólo trae ID con las dos zonas, pero el tipo de `penultimate`
        # sigue siendo opcional: se desempaqueta una vez en vez de repetir el
        # mismo `assert` en cada comprensión.
        pairs = [
            (item, item.penultimate) for item in group if item.penultimate is not None
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
            "pul_mediana_atr": _percentile(
                _finite([_safe_ratio(block.height, item.atr) for item, block in pairs]), 50
            ),
            "pul_mayor": bigger,
            "pct_pul_mayor": bigger / len(group) if group else float("nan"),
            "empates": equal,
            "ul_mayor": smaller,
            "pct_ul_mayor": smaller / len(group) if group else float("nan"),
        }

    rows = [row(str(year), group) for year, group in _by_year(pairs)]
    rows.append(row(TOTAL_ROW, pairs))
    return pd.DataFrame(rows, columns=columns)


def exception_profile(measurement: TimeframeZones) -> pd.DataFrame:
    """§7.5 — en qué se diferencian los ID donde el UL sale mayor que el PUL.

    La tabla no afirma un mecanismo: pone las dos poblaciones una al lado de la
    otra y deja ver de qué lado viene la diferencia —de un UL inusualmente alto,
    de un PUL inusualmente bajo, o de la extensión del UL—.
    """
    columns = [
        "poblacion", "pares", "ul_mediana_atr", "pul_mediana_atr",
        "ul_extendidos", "pct_extendidos",
    ]
    pairs = [
        (item, item.penultimate) for item in measurement.items if item.penultimate is not None
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
            "pul_mediana_atr": _percentile(
                _finite([_safe_ratio(block.height, item.atr) for item, block in group]), 50
            ),
            "ul_extendidos": extended,
            "pct_extendidos": extended / len(group) if group else float("nan"),
        }

    exceptions = [pair for pair in pairs if pair[0].last.height > pair[1].height]
    rest = [pair for pair in pairs if pair[0].last.height <= pair[1].height]
    return pd.DataFrame(
        [
            row("excepciones (UL > PUL)", exceptions),
            row("el resto (PUL >= UL)", rest),
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
    columns = ["poblacion", "pares", "rango_id_atr_mediano", "ul_mediana_atr", "pul_mediana_atr"]
    pairs = [
        (item, zone)
        for item in measurement.items
        if (zone := item.against) is not None
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
            "pul_mediana_atr": _percentile(
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
    pairs = [item for item in measurement.items if item.against is not None]
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
        "contra_dentro", "contra_total", "contra_sin_pul", "sobrevivirian", "pct",
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
            "contra_sin_pul": int(against["sin_zona"].sum()),
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
