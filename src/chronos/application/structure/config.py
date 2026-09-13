"""Configuración del módulo de impulso dominante.

Dataclasses puras; la tolerancia al YAML vive en infraestructura. El hash de la
configuración se calcula aquí para que cada salida sea trazable a los parámetros
exactos con los que se generó (§5.1).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import time

from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import (
    AnchorMode,
    DojiBreakMode,
    LegStartMode,
    OverlapPriority,
    SeedMode,
)

#: Temporalidades del módulo. Un ID sólo se rompe con cierres de su propia
#: temporalidad, así que la que lleve detector lo lleva propio (§1.2, §2.4).
#: **En el reparto por defecto el ID vive en el Diario y en H4**; el fichero del
#: proyecto añade H1 declarándolo principal de su gráfico en `charts`. M15 y M5
#: siguen siendo sólo lectura; M5 es la más fina, donde el propietario afina la
#: entrada y el stop a mano.
M5 = "M5"
M15 = "M15"
H1 = "H1"
H4 = "H4"
DAILY = "D"

SUPPORTED_TIMEFRAMES = (M5, M15, H1, H4, DAILY)

TIMEFRAME_MINUTES: dict[str, int] = {M5: 5, M15: 15, H1: 60, H4: 240, DAILY: 1440}

#: Qué se dibuja en cada gráfico. La primera de la lista es la **principal** —de
#: ella salen el sombreado del limbo y los marcadores— y las siguientes son
#: contexto de temporalidad superior.
#:
#: El reparto lo fija el propietario: es cómo lee él el mercado, no una decisión
#: del motor. **Aquí sólo el Diario y H4 tienen ID propio**, que es lo que fija la
#: línea base de la fase 1: H1, M15 y M5 llevan dibujado el ID de H4 como
#: contexto.
#: El fichero del proyecto corre con otro reparto —H1 con ID propio—, que entra
#: en el hash de configuración por su cuenta.
#:
#: **El Diario se dibuja sólo en su gráfico.** En H4 no se ve nada suyo —ni el ID,
#: ni el marco, ni las zonas—: a esa escala la caja diaria tapa el precio y lo que
#: se está auditando es el ID de H4. Quitarlo de ahí no cambia el hash: el detector
#: del Diario sigue encendido porque su propio gráfico lo pide.
DEFAULT_CHARTS: dict[str, tuple[str, ...]] = {
    DAILY: (DAILY,),
    H4: (H4,),
    H1: (H4,),
    M15: (H4,),
    M5: (H4,),
}


def by_size(timeframes: Iterable[str], *, descending: bool = True) -> tuple[str, ...]:
    """Ordena temporalidades por duración, de mayor a menor por defecto."""
    return tuple(sorted(set(timeframes), key=lambda name: TIMEFRAME_MINUTES[name], reverse=descending))


@dataclass(frozen=True, slots=True)
class ChartsConfig:
    """Reparto de gráficos y superposiciones."""

    layout: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_CHARTS)
    )

    def __post_init__(self) -> None:
        if not self.layout:
            raise DomainError("Hay que declarar al menos un gráfico")
        for chart, overlays in self.layout.items():
            self._check_supported(chart)
            if not overlays:
                raise DomainError(f"El gráfico {chart} no declara ningún impulso que dibujar")
            if len(set(overlays)) != len(overlays):
                raise DomainError(f"El gráfico {chart} repite alguna temporalidad: {overlays}")
            for overlay in overlays:
                self._check_supported(overlay)
                if TIMEFRAME_MINUTES[overlay] < TIMEFRAME_MINUTES[chart]:
                    raise DomainError(
                        f"El gráfico {chart} no puede llevar el impulso de {overlay}: "
                        "la superposición sólo tiene sentido de temporalidad superior"
                    )

    @staticmethod
    def _check_supported(timeframe: str) -> None:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise DomainError(
                f"Temporalidad no soportada por el módulo: {timeframe}. "
                f"Disponibles: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )

    @property
    def charts(self) -> tuple[str, ...]:
        """Gráficos disponibles, de mayor a menor temporalidad."""
        return by_size(self.layout)

    @property
    def detected(self) -> tuple[str, ...]:
        """Temporalidades sobre las que hay que detectar impulsos.

        Es la unión de todo lo que se dibuja: una temporalidad que sólo aparece
        como gráfico —M15 o M5 en el reparto por defecto— no necesita detector.
        """
        return by_size({tf for overlays in self.layout.values() for tf in overlays})

    def overlays(self, chart: str) -> tuple[str, ...]:
        return self.layout[chart]

    def primary(self, chart: str) -> str:
        """Temporalidad que manda en ese gráfico: su limbo y sus marcadores."""
        return self.layout[chart][0]


@dataclass(frozen=True, slots=True)
class StructureDataConfig:
    """De dónde salen las barras M1 de bid y de ask.

    Tres formas admitidas, en este orden de preferencia:
      - `bid_path` (+ `ask_path` opcional): un fichero por lado;
      - `path`: un único fichero con columnas prefijadas `bid_*` / `ask_*`;
      - `path` con OHLC simple: se toma como el lado declarado en `structure_side`
        y el informe lo dice explícitamente.
    """

    path: str = ""
    bid_path: str = ""
    ask_path: str = ""
    timezone: str = "UTC"
    start: str | None = None
    end: str | None = None

    @property
    def is_declared(self) -> bool:
        """`False` mientras no haya ninguna ruta: el módulo apagado no necesita datos."""
        return bool(self.path or self.bid_path)


#: Plazas cuya sesión puede anclar el día. La clave es el prefijo que se escribe
#: en `d_session_start`; el valor, la zona IANA con la que se resuelve el horario
#: de verano de verdad, año por año.
SESSION_TIMEZONES: dict[str, str] = {"NY": "America/New_York"}

_SESSION_START = re.compile(r"^(?P<place>[A-Z]{2,4})_(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


@dataclass(frozen=True, slots=True)
class SessionAnchor:
    """Arranque del día pegado a la hora local de una plaza, con DST real.

    Un desplazamiento fijo en UTC no puede reproducir un gráfico anclado a la
    sesión: Nueva York abre a la misma hora local todo el año, así que en UTC el
    corte se mueve una hora dos veces al año. Con este ancla el corte se calcula
    convirtiendo a la zona de la plaza, no sumando horas.
    """

    #: Prefijo escrito en la configuración (`NY`).
    place: str
    #: Zona IANA con la que se resuelve el horario de verano.
    timezone: str
    #: Hora local de apertura de la sesión.
    at: time

    @property
    def label(self) -> str:
        return f"{self.place}_{self.at.hour:02d}:{self.at.minute:02d}"

    def describe(self) -> str:
        return f"{self.at.hour:02d}:{self.at.minute:02d} de {self.timezone} (DST real)"


@dataclass(frozen=True, slots=True)
class AggregationConfig:
    """Agregación del histórico a las temporalidades del módulo (§1.2).

    El resultado depende íntegramente de estos dos valores: cambiarlos cambia
    las velas y por tanto los impulsos. Los fija el propietario comparando con
    su TradingView.

    Sólo H4 y el diario admiten desplazamiento, que es donde las plataformas
    discrepan. La rejilla de M5, M15 y H1 no es ambigua —cinco minutos, cuartos
    de hora y horas en punto— así que no lleva parámetro que ajustar.
    """

    #: Desplazamiento del inicio de las velas H4 respecto a 00:00 UTC. **Se ignora
    #: cuando `d_session_start` ancla el día a una sesión**: ahí H4 arranca con la
    #: sesión, que es lo que hace la plataforma del propietario.
    h4_offset_hours: int = 0
    #: Arranque de la vela diaria. Dos formas:
    #:   `HH:MM`      -> hora fija en UTC (`"00:00"`, `"22:00"`);
    #:   `PLAZA_HH:MM`-> hora local de una plaza con horario de verano real
    #:                   (`"NY_17:00"`, `"NY_18:00"`).
    #: Decidido: `NY_17:00`, que es además el origen de H4. Es la rejilla de
    #: cTrader/Pepperstone, la plataforma con la que se opera en vivo.
    d_session_start: str = "NY_17:00"

    def __post_init__(self) -> None:
        if not 0 <= self.h4_offset_hours <= 23:
            raise DomainError("h4_offset_hours debe estar en [0, 23]")
        self.session_start_time()  # valida el formato al construir

    @property
    def h4_effective_offset_hours(self) -> int:
        """Desplazamiento efectivo dentro del ciclo de 4 horas."""
        return self.h4_offset_hours % 4

    @property
    def session_anchor(self) -> SessionAnchor | None:
        """Ancla de sesión, o `None` si el corte es una hora fija en UTC."""
        match = _SESSION_START.match(self.d_session_start)
        if match is None:
            return None
        place = match.group("place")
        timezone = SESSION_TIMEZONES.get(place)
        if timezone is None:
            raise DomainError(
                f"Plaza desconocida en d_session_start: {place!r}. "
                f"Disponibles: {', '.join(sorted(SESSION_TIMEZONES))}"
            )
        return SessionAnchor(
            place=place,
            timezone=timezone,
            at=_parse_time(match.group("hour"), match.group("minute")),
        )

    def session_start_time(self) -> time:
        """Hora de arranque del día, local a su plaza si hay ancla de sesión."""
        anchor = self.session_anchor
        if anchor is not None:
            return anchor.at
        parts = self.d_session_start.split(":")
        if len(parts) != 2:
            raise DomainError(
                f"d_session_start debe ser HH:MM o PLAZA_HH:MM, no {self.d_session_start!r}"
            )
        return _parse_time(*parts)

    def describe_daily_start(self) -> str:
        """Cómo se imprime el corte diario en los informes."""
        anchor = self.session_anchor
        return anchor.describe() if anchor is not None else f"{self.d_session_start} UTC"


def _parse_time(hour: str, minute: str) -> time:
    try:
        return time(hour=int(hour), minute=int(minute))
    except ValueError as error:
        raise DomainError(f"Hora inválida en d_session_start: {hour}:{minute}") from error


@dataclass(frozen=True, slots=True)
class ImpulseRulesConfig:
    """Las reglas del ID. Cuatro parámetros abiertos, ninguno elegido por el motor."""

    #: R-02, decidido por el propietario: el ancla sale de la última vela
    #: contraria previa al arranque de la pierna. A2 queda para regresión.
    anchor_mode: AnchorMode = AnchorMode.A1_LAST_COUNTER_BODY
    seed_mode: SeedMode = SeedMode.S2_FIRST_COUNTER_BAR
    doji_break_mode: DojiBreakMode = DojiBreakMode.D1_NEUTRAL
    #: R-36. Por defecto queda el comportamiento anterior para que la línea base
    #: siga siendo reproducible mientras el propietario compara los tres modos.
    leg_start_mode: LegStartMode = LegStartMode.L1_CURRENT
    #: FASE 2.1. `False` = la rotura es por línea, que es la línea base de la
    #: fase 1. `True` = manda la zona: el UL en el lado a favor y el PUL en el
    #: lado en contra, y romper es atravesar la zona entera. Es el primer cambio
    #: de comportamiento del proyecto, así que va apagado por defecto y su
    #: apagado reproduce el hash y los recuentos archivados.
    break_by_zone: bool = False
    #: FASE 3.0. Sólo significa algo con `break_by_zone: true`. `True` = las dos
    #: zonas mandan, que es la fase 2.1 tal cual. `False` = **el UL manda el lado
    #: a favor y el ancla el lado en contra**, que es la regla del propietario
    #: desde la fase 3.0: el ID no cambia mientras no se atraviese entero el UL,
    #: y en contra sigue mandando la línea de donde arranca el ID. La zona en
    #: contra se sigue clasificando y dibujando —de ella cuelgan el toque y la
    #: toque—, pero no mata al ID.
    break_against_by_zone: bool = True
    #: FASE 2.1, **parámetro abierto**. Qué lado se evalúa primero cuando una
    #: misma vela cumple las dos condiciones de rotura, que sólo puede pasar con
    #: las dos zonas solapadas en precio. Con `break_by_zone: false` no cambia
    #: nada: reproduce el orden que la máquina ya tenía escrito.
    overlap_priority: OverlapPriority = OverlapPriority.A_FAVOR_FIRST
    warmup_bars: int = 50
    atr_period: int = 14

    def __post_init__(self) -> None:
        if self.warmup_bars < 0:
            raise DomainError("warmup_bars no puede ser negativo")
        if self.atr_period < 1:
            raise DomainError("atr_period debe ser >= 1")


@dataclass(frozen=True, slots=True)
class ZonesConfig:
    """Zonas UL y PUL de la fase 2.0. **Sólo detección y dibujo.**

    Con `enabled: False` el sistema no emite ni una zona y todo lo demás sale
    byte a byte como en la fase 1. Con `True` se calculan y se publican, pero
    ninguna interviene en la detección de impulsos: la regla de rotura sigue
    siendo por línea hasta la fase 2.1.

    No hay ningún otro parámetro y es a propósito. Las dos zonas se derivan de
    velas que el detector ya registró —`ts_extreme` y `ts_anchor`— sin ninguna
    holgura, umbral ni ventana que ajustar, así que no existe una configuración
    que produzca zonas *distintas*: sólo zonas o ninguna zona. Por eso este
    bloque queda fuera de `fingerprint()` y el `config_hash` sigue significando
    exactamente lo que significaba: los parámetros que mueven un impulso.
    """

    enabled: bool = False


@dataclass(frozen=True, slots=True)
class TimezoneAuditConfig:
    """Verificación empírica de zona horaria (§1.1). Obligatoria antes de calcular."""

    enabled: bool = True
    #: Hora UTC en la que debe verse el pico de volatilidad (datos macro de EE. UU.).
    expected_peak_utc: str = "13:30"
    tolerance_minutes: int = 30
    #: Horas de hueco a partir de las cuales se considera parada de fin de semana.
    weekend_gap_hours: float = 12.0
    #: Fracción mínima de huecos que deben empezar en viernes y acabar en domingo.
    #: No es 1.0 porque los festivos también generan paradas largas.
    min_conformity: float = 0.9

    def __post_init__(self) -> None:
        if not 0 < self.min_conformity <= 1:
            raise DomainError("min_conformity debe estar en (0, 1]")
        if self.tolerance_minutes < 0:
            raise DomainError("tolerance_minutes no puede ser negativo")


@dataclass(frozen=True, slots=True)
class StructureReportingConfig:
    output_dir: str = "reports"
    #: Zona horaria de la sesión del propietario; el explorador imprime ambas.
    session_timezone: str = "Etc/GMT+4"
    text_report: bool = True
    explorer_html: bool = True
    #: Capturas PNG de F.4. Cuestan un par de minutos: se apagan para iterar.
    captures: bool = True
    #: Por temporalidad; se conservan las más recientes. 0 = todas.
    max_explorer_bars: int = 60_000


@dataclass(frozen=True, slots=True)
class ImpulseConfig:
    """Configuración completa del módulo 1."""

    #: Interruptor del módulo. Con `False` el sistema no emite absolutamente nada.
    enabled: bool = True
    symbol: str = "XAUUSD"
    #: Lado del precio sobre el que se calcula toda la estructura del proyecto.
    structure_side: str = "bid"  # bid | ask | mid
    data: StructureDataConfig = field(default_factory=StructureDataConfig)
    aggregation: AggregationConfig = field(default_factory=AggregationConfig)
    charts: ChartsConfig = field(default_factory=ChartsConfig)
    rules: ImpulseRulesConfig = field(default_factory=ImpulseRulesConfig)
    #: Fase 2.0. Apagadas por defecto: encenderlas no puede mover ni un impulso.
    zones: ZonesConfig = field(default_factory=ZonesConfig)
    timezone_audit: TimezoneAuditConfig = field(default_factory=TimezoneAuditConfig)
    reporting: StructureReportingConfig = field(default_factory=StructureReportingConfig)

    def __post_init__(self) -> None:
        if self.structure_side not in ("bid", "ask", "mid"):
            raise DomainError(f"structure_side desconocido: {self.structure_side}")

    def fingerprint(self) -> str:
        """Hash corto y estable de los parámetros que afectan al resultado.

        Deja fuera lo que no cambia ni una vela ni un impulso (rutas, carpeta de
        salida, opciones de informe): dos corridas con el mismo hash producen la
        misma tabla de impulsos.

        Las zonas de la fase 2.0 tampoco entran, por la misma razón y con la
        misma consecuencia buscada: encenderlas no mueve un solo impulso, así
        que la línea base `e27d20d0fa4e` se conserva con las zonas puestas y las
        corridas de la fase 1 siguen siendo comparables con las de ahora.
        """
        rules = asdict(self.rules)
        # R-36. `L1_actual` reproduce barra por barra lo que hacía el módulo antes
        # de que existiera este parámetro, así que se omite del hash: las corridas
        # ya archivadas siguen siendo comparables con las nuevas. `L2` y `L3` sí
        # cambian el resultado, entran en el hash y producen uno distinto.
        if self.rules.leg_start_mode is LegStartMode.L1_CURRENT:
            del rules["leg_start_mode"]
        # Fase 2.1, mismo criterio y por la misma razón. `break_by_zone: false`
        # reproduce barra por barra lo que hacía el módulo antes de que el
        # parámetro existiera, así que se omite del hash y la línea base
        # `e27d20d0fa4e` se conserva. Con `true` el resultado cambia, entra en el
        # hash —y con él el orden de solape, que sólo decide algo ahí— y produce
        # uno distinto: ninguna salida de la regla nueva puede confundirse con la
        # de la vieja.
        if not self.rules.break_by_zone:
            del rules["break_by_zone"]
            del rules["overlap_priority"]
        # Lo mismo con el lado en contra: `true` es lo que hacía la fase 2.1
        # antes de que el parámetro existiera, así que se omite del hash y los
        # hashes archivados de esa fase se conservan. La regla de la fase 3.0
        # —el ancla manda en contra— sí cambia el resultado y entra en el hash.
        if self.rules.break_against_by_zone:
            rules.pop("break_against_by_zone", None)
        payload = {
            "symbol": self.symbol,
            "structure_side": self.structure_side,
            "aggregation": asdict(self.aggregation),
            "detected": list(self.charts.detected),
            "rules": rules,
            "period": {"start": self.data.start, "end": self.data.end},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def closed_decisions(self) -> tuple[str, ...]:
        """Parámetros que el propietario ya ha cerrado, con el valor vigente.

        Se imprimen igual que los abiertos: quien lea el informe tiene que poder
        ver de un vistazo con qué se calculó, sin abrir el YAML.
        """
        return (
            f"ANCHOR_MODE = {self.rules.anchor_mode.value} "
            "(§2.5, R-02: el ancla va en la última vela contraria previa a la "
            "pierna; A2 queda para regresión)",
            f"LEG_START_MODE = {self.rules.leg_start_mode.value} "
            "(R-36: L2 multiplicaba el defecto y L3 daba rango negativo)",
            f"D_SESSION_START = {self.aggregation.d_session_start} y H4 con el mismo "
            f"origen ({self.aggregation.describe_daily_start()}) "
            "(§1.2: única rejilla que reproduce las velas del propietario)",
            f"STRUCTURE_SIDE = {self.structure_side} "
            "(§1.3: hoy sólo hay M1 del lado bid descargado)",
        )

    def open_decisions(self) -> tuple[str, ...]:
        """Parámetros que aún debe cerrar el propietario. Se imprimen en el informe."""
        decisions = [
            f"SEED_MODE = {self.rules.seed_mode.value} "
            "(arranque del histórico: la especificación no lo cubre)",
            f"DOJI_BREAK_MODE = {self.rules.doji_break_mode.value} "
            "(§2.2 y §2.6 se contradicen sobre si un doji puede romper)",
            f"BREAK_BY_ZONE = {str(self.rules.break_by_zone).lower()} "
            "(fase 2.1: false = rotura por línea, la línea base; true = manda la zona)",
        ]
        if self.rules.break_by_zone:
            decisions.append(
                f"BREAK_AGAINST_BY_ZONE = {str(self.rules.break_against_by_zone).lower()} "
                "(fase 3.0: false = el UL manda a favor y el ANCLA en contra)"
            )
            decisions.append(
                f"OVERLAP_PRIORITY = {self.rules.overlap_priority.value} "
                "(fase 2.1 §2: qué lado se evalúa primero con las dos zonas solapadas)"
            )
        return tuple(decisions)
