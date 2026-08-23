"""Evidencia ejecutable de la fase 1 (sección G).

"Los tests pasan" no es evidencia: evidencia es el valor esperado al lado del
obtenido, para cada caso, generado en la misma corrida que produce los informes.
Este módulo vuelve a ejecutar las comprobaciones que el propietario pidió y
devuelve las dos columnas; quien las imprime es la CLI.

Las mismas comprobaciones viven además como tests de pytest. No es duplicación
gratuita: pytest las rompe en la máquina del que programa, y esto las enseña en
la carpeta del informe, que es donde las va a leer el propietario.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

import pandas as pd

from chronos.application.structure.causal import PriorBarAtr
from chronos.application.structure.config import ChartsConfig, ImpulseConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import BreakKind, MachineState, SeedMode
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.synthetic_day import (
    SYNTHETIC_DAY,
    SYNTHETIC_DAY_GAP,
    SYNTHETIC_DAY_GAP_AFTER,
    SYNTHETIC_DAY_START,
)

#: Línea base DEFINITIVA de la fase 1: ancla A1, arranque de pierna L1 y sesión
#: anclada a las 17:00 de Nueva York —la rejilla de cTrader/Pepperstone, que es
#: donde se ejecuta en vivo—. Cualquier cambio que la mueva es una regresión.
#: Son impulsos **detectados**, publicados o no.
#:
#: Venía de `NY_18:00` (rejilla de TradingView) con D 401 / H4 1.914: el diario
#: no se mueve —el corte cae en la parada diaria del oro con las dos anclas—
#: pero H4 sí, porque las velas son otras.
#:
#: **Sólo el Diario y H4.** H1 dejó de llevar detector: los 7.231 impulsos de H1
#: que traía esta línea base quedan archivados y ya no se comprueban. Los dos
#: recuentos que quedan no se movieron ni un impulso al quitarlo, que es justo lo
#: que garantiza la independencia entre temporalidades de G.5.
PHASE1_BASELINE: dict[str, int] = {"D": 401, "H4": 2027}

#: Hash de la configuración que produce esa línea base. Va en el mismo sitio que
#: los recuentos: un recuento correcto con otra configuración no es la línea base.
#: Era `8e51cd9140c8` cuando H1 llevaba detector y `f2f2a87f8efe` con la sesión
#: anclada a las 18:00 de Nueva York.
BASELINE_HASH = "e27d20d0fa4e"

#: Línea base anterior, con el ancla A2 y el corte diario en 00:00 UTC. Queda
#: escrita para que quien lea un informe archivado sepa a qué corrida pertenece;
#: no se comprueba contra nada. (Traía además H1 7.416.)
PROVISIONAL_BASELINE: dict[str, int] = {"D": 477, "H4": 2068}

#: Pasos de las dos temporalidades con las que se corre el día sintético. El
#: detector es agnóstico a la temporalidad: si el resultado dependiera del paso,
#: sería un fallo.
SYNTHETIC_STEPS: dict[str, pd.Timedelta] = {
    "D": pd.Timedelta(days=1),
    "H4": pd.Timedelta(hours=4),
}


@dataclass(frozen=True, slots=True)
class Check:
    """Una comprobación con su esperado y su obtenido, uno al lado del otro."""

    name: str
    expected: str
    obtained: str

    @property
    def ok(self) -> bool:
        return self.expected == self.obtained


@dataclass(frozen=True, slots=True)
class CheckGroup:
    title: str
    note: str
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass(frozen=True, slots=True)
class Evidence:
    groups: tuple[CheckGroup, ...]

    @property
    def ok(self) -> bool:
        return all(group.ok for group in self.groups)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(
            check for group in self.groups for check in group.checks if not check.ok
        )


def collect(
    config: ImpulseConfig, series: Mapping[str, pd.DataFrame]
) -> Evidence:
    """Ejecuta toda la sección G sobre las velas ya agregadas."""
    return Evidence(
        groups=(
            *(_synthetic_day(timeframe) for timeframe in SYNTHETIC_STEPS),
            _lookahead(),
            _module_off(config),
            _determinism(config, series),
            _independence(config, series),
            _regression(config, series),
        )
    )


# --- G.1 El día sintético en las dos temporalidades -------------------------


def _synthetic_day(timeframe: str) -> CheckGroup:
    """Los nueve casos del §4, con el esperado escrito antes de correr el motor."""
    detector = _run_synthetic(timeframe)
    impulses = detector.impulses
    states = detector.states
    events = detector.events

    checks = [
        Check(
            "1. pierna bajista limpia + contraria -> ID bajista",
            "bajista, ancla 2000.0, extremo 1970.0, constituye en b3",
            f"{impulses[0].direction.value}, ancla {impulses[0].anchor:.1f}, "
            f"extremo {impulses[0].extreme:.1f}, constituye en b{impulses[0].index_constitution}",
        ),
        Check(
            "2. limbo de 3 barras sin contraria",
            "b6-b9 en LIMBO, n_barras_limbo = 3",
            f"b6-b9 en {_states_between(states, 6, 9)}, "
            f"n_barras_limbo = {impulses[1].limbo_bars}",
        ),
        Check(
            "3. contraria de 1 céntimo constituye igual",
            "cuerpo 0.01 constituye ID#2 en b10",
            f"cuerpo {impulses[1].constituting_body_size:.2f} constituye "
            f"ID#{impulses[1].id_num} en b{impulses[1].index_constitution}",
        ),
        Check(
            "4. doji en posición de constituir",
            "b12 sigue en LIMBO, el ID nace en b14",
            f"b12 sigue en {states[12].state.value}, "
            f"el ID nace en b{impulses[2].index_constitution}",
        ),
        Check(
            "5. ROTURA_A_FAVOR",
            "b6 rompe ID#1 a favor, pierna nueva bajista",
            f"b{events[0].index} rompe ID#{events[0].broken_id_num} "
            f"{'a favor' if events[0].kind is BreakKind.A_FAVOR else 'en contra'}, "
            f"pierna nueva {events[0].new_leg_direction.value}",
        ),
        Check(
            "6. ROTURA_EN_CONTRA",
            "b15 rompe ID#3 en contra, pierna nueva alcista",
            f"b{events[2].index} rompe ID#{events[2].broken_id_num} "
            f"{'a favor' if events[2].kind is BreakKind.A_FAVOR else 'en contra'}, "
            f"pierna nueva {events[2].new_leg_direction.value}",
        ),
        Check(
            "7. retroceso profundo dentro del rango",
            "b4 y b5 con ID#1 en ID_VIGENTE, sin eventos",
            f"b4 y b5 con ID#{states[4].impulse_id} en {_states_between(states, 4, 5)}, "
            f"{'sin eventos' if not [e for e in events if e.index in (4, 5)] else 'con eventos'}",
        ),
        Check(
            "8. barra que rompe y es contraria a la vez",
            "b17 sólo rompe; el ID nace en b19 con ancla 1955.0",
            f"b17 sólo {'rompe' if states[17].state is MachineState.LIMBO else 'constituye'}; "
            f"el ID nace en b{impulses[4].index_constitution} "
            f"con ancla {impulses[4].anchor:.1f}",
        ),
        Check(
            "9. hueco de mercado entre b5 y b6",
            "los mismos 5 impulsos que sin hueco; ID#1 se rompe en b6",
            f"{_same_as_continuous(timeframe)}; "
            f"ID#{impulses[0].id_num} se rompe en b{impulses[0].index_end}",
        ),
        Check(
            "recuento total",
            "5 impulsos, 4 eventos, 1 doji",
            f"{len(impulses)} impulsos, {len(events)} eventos, "
            f"{detector.diagnostics['dojis']} doji",
        ),
    ]
    return CheckGroup(
        title=f"G.1 Día sintético del §4 en {timeframe}",
        note=(
            "La misma serie de cuerpos con paso de "
            f"{SYNTHETIC_STEPS[timeframe]}. El detector no mira la temporalidad: si el "
            "resultado cambiara con el paso, sería un fallo."
        ),
        checks=tuple(checks),
    )


def _run_synthetic(timeframe: str, *, with_gap: bool = True) -> DominantImpulseDetector:
    step = SYNTHETIC_STEPS[timeframe]
    detector = DominantImpulseDetector(
        timeframe=timeframe, seed_mode=SeedMode.S1_FIRST_NON_DOJI, warmup_bars=0
    )
    stamp = pd.Timestamp(SYNTHETIC_DAY_START)
    for position, (open_, close) in enumerate(SYNTHETIC_DAY):
        if with_gap and position == SYNTHETIC_DAY_GAP_AFTER + 1:
            stamp += pd.Timedelta(SYNTHETIC_DAY_GAP)
        detector.process(
            BodyBar(timestamp=stamp.to_pydatetime(), open=open_, close=close)
        )
        stamp += step
    return detector


def _same_as_continuous(timeframe: str) -> str:
    """El hueco no puede cambiar ni un impulso: las velas son las mismas.

    Con paso diario el salto no cae en fin de semana —la serie sintética son
    veinte velas, no veinte días de calendario—, así que lo que se fija aquí es
    la invariancia frente al hueco, no el día de la semana.
    """
    with_gap = _run_synthetic(timeframe, with_gap=True).impulses
    without = _run_synthetic(timeframe, with_gap=False).impulses
    same = [
        (impulse.direction, impulse.anchor, impulse.extreme, impulse.index_constitution)
        for impulse in with_gap
    ] == [
        (impulse.direction, impulse.anchor, impulse.extreme, impulse.index_constitution)
        for impulse in without
    ]
    if not same:
        return f"el hueco CAMBIA los impulsos ({len(with_gap)} vs {len(without)})"
    return f"los mismos {len(with_gap)} impulsos que sin hueco"


def _states_between(states: Sequence[object], first: int, last: int) -> str:
    values = {states[index].state.value for index in range(first, last + 1)}  # type: ignore[attr-defined]
    return " y ".join(sorted(values))


# --- G.2 LookaheadError -----------------------------------------------------


def _lookahead() -> CheckGroup:
    """Las tres vías por las que la garantía anti-lookahead tiene que saltar."""
    checks = [
        Check("1. extremo de un ID en LIMBO", "LookaheadError", _raises_extreme()),
        Check("2. estado posterior a la última barra", "LookaheadError", _raises_state()),
        Check("3. ATR más allá de la frontera", "LookaheadError", _raises_atr()),
    ]
    return CheckGroup(
        title="G.2 La garantía anti-lookahead salta de verdad",
        note="Un guardarraíl que nunca se ha visto saltar no es un guardarraíl.",
        checks=tuple(checks),
    )


def _raises_extreme() -> str:
    detector = DominantImpulseDetector(
        timeframe="H4", seed_mode=SeedMode.S1_FIRST_NON_DOJI, warmup_bars=0
    )
    stamp = pd.Timestamp(SYNTHETIC_DAY_START)
    for open_, close in SYNTHETIC_DAY[:3]:  # pierna sin vela contraria
        detector.process(BodyBar(timestamp=stamp.to_pydatetime(), open=open_, close=close))
        stamp += pd.Timedelta(hours=4)
    return _catch(detector.current_impulse_extreme)


def _raises_state() -> str:
    detector = DominantImpulseDetector(
        timeframe="H4", seed_mode=SeedMode.S1_FIRST_NON_DOJI, warmup_bars=0
    )
    stamp = pd.Timestamp(SYNTHETIC_DAY_START)
    for open_, close in SYNTHETIC_DAY[:4]:
        detector.process(BodyBar(timestamp=stamp.to_pydatetime(), open=open_, close=close))
        stamp += pd.Timedelta(hours=4)
    future = (stamp + pd.Timedelta(days=1)).to_pydatetime()
    return _catch(lambda: detector.state_at(future))


def _raises_atr() -> str:
    highs = [2000.0 + value for value in range(40)]
    lows = [1990.0 + value for value in range(40)]
    closes = [1995.0 + value for value in range(40)]
    atr = PriorBarAtr(
        pd.Series(highs).to_numpy(),
        pd.Series(lows).to_numpy(),
        pd.Series(closes).to_numpy(),
        period=14,
    )
    atr.advance(20)
    return _catch(lambda: atr.at(21))


def _catch(action: Callable[[], object]) -> str:
    try:
        action()
    except LookaheadError:
        return "LookaheadError"
    return "no saltó"


# --- G.3 a G.6 --------------------------------------------------------------


def _module_off(config: ImpulseConfig) -> CheckGroup:
    off = replace(config, enabled=False)
    run = DetectDominantImpulses(off).execute({})
    return CheckGroup(
        title="G.3 Con el módulo apagado el sistema no emite nada",
        note="`enabled: false`: ni impulsos, ni eventos, ni estado, ni ficheros.",
        checks=(
            Check("no emite nada", "True", str(run.emits_nothing)),
            Check("temporalidades analizadas", "0", str(len(run.analyses))),
            Check("filas en la tabla de impulsos", "0", str(len(run.table()))),
        ),
    )


def _determinism(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    first = DetectDominantImpulses(config).execute(dict(series))
    second = DetectDominantImpulses(config).execute(dict(series))
    counts = {
        timeframe: len(analysis.impulses) for timeframe, analysis in first.analyses.items()
    }
    return CheckGroup(
        title="G.4 Determinismo: dos ejecuciones seguidas",
        note="Mismo hash de configuración y mismo número de impulsos.",
        checks=(
            Check("config_hash", first.config_hash, second.config_hash),
            Check(
                "impulsos por temporalidad",
                format_counts(counts),
                format_counts(
                    {
                        timeframe: len(analysis.impulses)
                        for timeframe, analysis in second.analyses.items()
                    }
                ),
            ),
        ),
    )


def _independence(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """Apagar el ID de H4 no puede mover ni un ID del diario."""
    if not {"D", "H4"} <= set(series):
        return CheckGroup(
            title="G.5 Independencia entre temporalidades",
            note="No se puede medir: esta corrida no lleva las dos temporalidades.",
            checks=(),
        )
    with_h4 = DetectDominantImpulses(config).execute(dict(series))
    without = replace(config, charts=ChartsConfig({"D": ("D",)}))
    reduced = DetectDominantImpulses(without).execute(
        {timeframe: frame for timeframe, frame in series.items() if timeframe != "H4"}
    )
    checks = [
        Check(
            "temporalidades detectadas",
            "D",
            ", ".join(reduced.analyses),
        ),
        Check(
            "impulsos de D",
            _fingerprint(with_h4.analyses["D"].impulses),
            _fingerprint(reduced.analyses["D"].impulses),
        ),
    ]
    return CheckGroup(
        title="G.5 Independencia entre temporalidades",
        note=(
            "Se compara la huella de cada impulso —instante, ancla y extremo—, no sólo el "
            "recuento: un cambio compensado no se vería contando."
        ),
        checks=tuple(checks),
    )


def _regression(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    run = DetectDominantImpulses(config).execute(dict(series))
    obtained = {
        timeframe: len(analysis.impulses) for timeframe, analysis in run.analyses.items()
    }
    expected = {
        timeframe: PHASE1_BASELINE[timeframe]
        for timeframe in obtained
        if timeframe in PHASE1_BASELINE
    }
    return CheckGroup(
        title="G.6 Regresión contra la línea base de la fase 1",
        note=(
            f"{format_counts(PHASE1_BASELINE)} con la configuración definitiva. Cualquier cosa "
            f"que la mueva es una regresión. La base provisional —{format_counts(PROVISIONAL_BASELINE)}, "
            "ancla A2 y corte diario en 00:00 UTC— queda archivada y ya no se comprueba."
        ),
        checks=(
            Check("impulsos detectados", format_counts(expected), format_counts(obtained)),
            Check("config_hash", BASELINE_HASH, config.fingerprint()),
        ),
    )


def format_counts(counts: Mapping[str, int]) -> str:
    """Recuentos por temporalidad en una línea. Pública: la fase 2.0 los imprime igual."""
    return " / ".join(f"{timeframe} {value:,}" for timeframe, value in counts.items())


def _fingerprint(impulses: Sequence[object]) -> str:
    """Huella corta de una lista de impulsos: instante, ancla y extremo."""
    parts = [
        f"{impulse.ts_constitution:%Y%m%d%H%M}:{impulse.anchor:.4f}:{impulse.extreme:.4f}"  # type: ignore[attr-defined]
        for impulse in impulses
    ]
    digest = pd.util.hash_array(pd.Series(parts).to_numpy()).sum()
    return f"{len(parts)} impulsos · huella {digest % 10**12:012d}"


__all__ = [
    "BASELINE_HASH",
    "PHASE1_BASELINE",
    "PROVISIONAL_BASELINE",
    "Check",
    "CheckGroup",
    "Evidence",
    "collect",
    "format_counts",
]
