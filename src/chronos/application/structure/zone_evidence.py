"""Evidencia ejecutable de la fase 2.0 (§9.3).

"Los tests pasan" no es evidencia: evidencia es el valor esperado al lado del
obtenido, para cada caso, generado en la misma corrida que produce los informes.
Este módulo vuelve a ejecutar las comprobaciones de la fase 2.0 y devuelve las
dos columnas; quien las imprime es la CLI.

Las mismas comprobaciones viven además como tests de pytest. No es duplicación
gratuita: pytest las rompe en la máquina del que programa, y esto las enseña en
la carpeta del informe, que es donde las va a leer el propietario.

Reutiliza el `Check`/`CheckGroup`/`Evidence` de la fase 1 para que las dos
salidas se lean igual.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

import pandas as pd

from chronos.application.structure.config import ImpulseConfig, ZonesConfig
from chronos.application.structure.detect_impulses import DetectDominantImpulses
from chronos.application.structure.evidence import (
    BASELINE_HASH,
    PHASE1_BASELINE,
    Check,
    CheckGroup,
    Evidence,
    format_counts,
)
from chronos.application.structure.zones import detect_zones
from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.detector import DominantImpulseDetector
from chronos.domain.structure.enums import AnchorMode, BodyDirection, SeedMode
from chronos.domain.structure.errors import LookaheadError
from chronos.domain.structure.impulse import DominantImpulse
from chronos.domain.structure.synthetic_zones import (
    MIRROR_CENTRE,
    SYNTHETIC_DOJI_OB,
    SYNTHETIC_ZONES_DOWN,
    SYNTHETIC_ZONES_START,
    SYNTHETIC_ZONES_UP,
    Candle,
)
from chronos.domain.structure.zones import (
    CandleSeries,
    Zone,
    ZoneBook,
    ZoneKind,
    last_zone,
    order_block_zone,
)

STEP = pd.Timedelta(hours=4)


def collect(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> Evidence:
    """Ejecuta toda la evidencia de la fase 2.0 sobre las velas ya agregadas."""
    return Evidence(
        groups=(
            _synthetic_up(),
            _synthetic_down(),
            _synthetic_doji(),
            _lookahead(),
            _zones_off(config, series),
            _regression(config, series),
        )
    )


# --- El día sintético del §6 -------------------------------------------------


#: Un impulso con sus dos zonas. El OB puede no existir.
Zoned = tuple[DominantImpulse, Zone, Zone | None]


def _zoned(
    candles: Sequence[Candle], *, anchor_mode: AnchorMode
) -> tuple[CandleSeries, list[Zoned]]:
    series = _series(candles)
    detector = DominantImpulseDetector(
        timeframe="H4",
        anchor_mode=anchor_mode,
        seed_mode=SeedMode.S2_FIRST_COUNTER_BAR,
        warmup_bars=0,
    )
    detector.process_all(
        [
            BodyBar(
                timestamp=series.at(position),
                open=float(series.open[position]),
                close=float(series.close[position]),
            )
            for position in range(len(series))
        ]
    )
    items = [
        (
            impulse,
            last_zone(
                series,
                id_num=impulse.id_num,
                timeframe="H4",
                direction=impulse.direction,
                index_extreme=impulse.index_extreme,
                ts_constitution=impulse.ts_constitution,
            ),
            order_block_zone(
                series,
                id_num=impulse.id_num,
                timeframe="H4",
                direction=impulse.direction,
                index_anchor=impulse.index_anchor,
                ts_constitution=impulse.ts_constitution,
                index_end=impulse.index_end,
            ),
        )
        for impulse in detector.impulses
    ]
    return series, items


def _series(candles: Sequence[Candle]) -> CandleSeries:
    stamps = pd.date_range(SYNTHETIC_ZONES_START, periods=len(candles), freq=STEP, tz="UTC")
    return CandleSeries(
        timestamps=pd.DatetimeIndex(stamps),
        open=pd.Series([c[0] for c in candles], dtype=float).to_numpy(),
        high=pd.Series([c[1] for c in candles], dtype=float).to_numpy(),
        low=pd.Series([c[2] for c in candles], dtype=float).to_numpy(),
        close=pd.Series([c[3] for c in candles], dtype=float).to_numpy(),
    )


def _synthetic_up() -> CheckGroup:
    """Los casos construibles del §6, con el esperado escrito antes de correr."""
    _, items = _zoned(SYNTHETIC_ZONES_UP, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    uls = [zone for _, zone, _ in items]
    obs = [block for _, _, block in items]

    checks = [
        Check(
            "recuento",
            "5 impulsos alcista",
            f"{len(items)} impulsos {items[0][0].direction.value}",
        ),
        Check(
            "1. UL de mecha normal (ID#1)",
            "interior 2010.00, exterior 2012.00, altura 2.00",
            f"interior {uls[0].inner:.2f}, exterior {uls[0].outer:.2f}, "
            f"altura {uls[0].height:.2f}",
        ),
        Check(
            "2. UL que se extiende (ID#2)",
            "interior 2019.00, exterior 2023.00, extendida True",
            f"interior {uls[1].inner:.2f}, exterior {uls[1].outer:.2f}, "
            f"extendida {uls[1].extended}",
        ),
        Check(
            "3a. UL que no se extiende, mecha menor (ID#1)",
            "extendida False, exterior 2012.00",
            f"extendida {uls[0].extended}, exterior {uls[0].outer:.2f}",
        ),
        Check(
            "3b. UL que no se extiende, empate exacto (ID#3)",
            "extendida False, exterior 2026.00",
            f"extendida {uls[2].extended}, exterior {uls[2].outer:.2f}",
        ),
        Check(
            "4. UL de altura cero (ID#3)",
            "altura 0.00, plana True",
            f"altura {uls[2].height:.2f}, plana {uls[2].is_flat}",
        ),
        Check(
            "5. OB confirmado 2 barras tras constituir (ID#4)",
            "constituye en b13, confirma en b15, nace al confirmarse",
            f"constituye en b{items[3][0].index_constitution}, "
            f"confirma en b{obs[3].index_confirmation}, "  # type: ignore[union-attr]
            f"nace {'al confirmarse' if obs[3].ts_birth == obs[3].ts_confirmation else 'al constituirse'}",  # type: ignore[union-attr]
        ),
        Check(
            "6. OB que nunca se confirma (ID#5)",
            "sin OB, pero con UL de altura 1.00",
            f"{'sin OB' if obs[4] is None else 'con OB'}, "
            f"pero con UL de altura {uls[4].height:.2f}",
        ),
        Check(
            "7. la vela que constituye confirma el OB",
            "IMPOSIBLE: 0 casos (la constituyente es del color contrario)",
            f"IMPOSIBLE: {sum(1 for impulse, _, block in items if block is not None and block.index_confirmation == impulse.index_constitution)} casos "
            f"(la constituyente es del color contrario)",
        ),
        Check(
            "el OB cubre el cuerpo y el UL no (ID#4)",
            "OB [2023.00, 2045.00] contiene 2030.00; UL [2034.00, 2035.00] no",
            f"OB [{obs[3].low:.2f}, {obs[3].high:.2f}] "  # type: ignore[union-attr]
            f"{'contiene' if obs[3].contains(2030.00) else 'no contiene'} 2030.00; "  # type: ignore[union-attr]
            f"UL [{uls[3].low:.2f}, {uls[3].high:.2f}] "
            f"{'contiene' if uls[3].contains(2030.00) else 'no'}",
        ),
    ]
    return CheckGroup(
        title="Z.1 Día sintético del §6, lado alcista",
        note=(
            "Veinte velas OHLC calculadas a mano. El día sintético de la fase 1 son pares "
            "(open, close): las zonas viven en las mechas y necesitan una serie propia."
        ),
        checks=tuple(checks),
    )


def _synthetic_down() -> CheckGroup:
    """§6.9 — la simetría, comprobada contra el espejo exacto de la serie alcista."""
    _, arriba = _zoned(SYNTHETIC_ZONES_UP, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    _, abajo = _zoned(SYNTHETIC_ZONES_DOWN, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)

    def reflect(price: float) -> float:
        return 2 * MIRROR_CENTRE - price

    mismatches = []
    for (up_imp, up_ul, up_ob), (down_imp, down_ul, down_ob) in zip(arriba, abajo, strict=True):
        same = (
            down_imp.index_constitution == up_imp.index_constitution
            and abs(down_ul.inner - reflect(up_ul.inner)) < 1e-9
            and abs(down_ul.outer - reflect(up_ul.outer)) < 1e-9
            and down_ul.extended == up_ul.extended
            and down_ul.is_flat == up_ul.is_flat
            and (down_ob is None) == (up_ob is None)
        )
        if not same:
            mismatches.append(up_imp.id_num)

    return CheckGroup(
        title="Z.2 El lado bajista es el espejo exacto del alcista",
        note=(
            "La serie bajista no se escribe a mano: es la alcista reflejada. Todas las "
            "comparaciones del módulo son estrictas y la reflexión las invierte a la vez, "
            "así que cualquier asimetría sería un fallo del código y no de la serie."
        ),
        checks=(
            Check("impulsos reflejados", f"{len(arriba)} de {len(arriba)}", f"{len(arriba) - len(mismatches)} de {len(arriba)}"),
            Check("dirección", "bajista", abajo[0][0].direction.value),
            Check("zonas que no cuadran con el espejo", "ninguna", "ninguna" if not mismatches else str(mismatches)),
        ),
    )


def _synthetic_doji() -> CheckGroup:
    """§6.8 — el doji en posición de OB, en las dos lecturas del ancla."""
    series_a2, con_a2 = _zoned(SYNTHETIC_DOJI_OB, anchor_mode=AnchorMode.A2_FIRST_LEG_BAR)
    series_a1, con_a1 = _zoned(SYNTHETIC_DOJI_OB, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    a2_anchor = con_a2[1][0].index_anchor
    a1_anchor = con_a1[1][0].index_anchor

    return CheckGroup(
        title="Z.3 Doji en posición de OB (§6.8)",
        note=(
            "Sólo existe con ANCHOR_MODE = A2. Con el A1 del proyecto el ancla sale de la "
            "última vela CONTRARIA previa a la pierna, y un doji no es contrario a nada: "
            "por eso el recuento del histórico sale en cero. Las mismas ocho velas leídas "
            "de las dos formas."
        ),
        checks=(
            Check(
                "A2: la vela del ancla",
                "b4, doji",
                f"b{a2_anchor}, {series_a2.direction_of(a2_anchor).value}",
            ),
            Check(
                "A2: el OB del doji existe igual",
                "altura 1.00, confirma en b5",
                f"altura {con_a2[1][2].height:.2f}, "  # type: ignore[union-attr]
                f"confirma en b{con_a2[1][2].index_confirmation}",  # type: ignore[union-attr]
            ),
            Check(
                "A1: la vela del ancla sobre las MISMAS velas",
                "b3, bearish",
                f"b{a1_anchor}, {series_a1.direction_of(a1_anchor).value}",
            ),
            Check(
                "A1: el OB nunca es un doji",
                "True",
                str(series_a1.direction_of(a1_anchor) is not BodyDirection.DOJI),
            ),
        ),
    )


# --- La garantía anti-lookahead ---------------------------------------------


def _lookahead() -> CheckGroup:
    """§5 — las tres vías por las que la garantía tiene que saltar."""
    series, items = _zoned(SYNTHETIC_ZONES_UP, anchor_mode=AnchorMode.A1_LAST_COUNTER_BODY)
    ul_extendido = items[1][1]
    ob_tardio = items[3][2]
    assert ob_tardio is not None  # el ID#4 lo confirma en b15, por diseño

    libro = ZoneBook("H4", (items[4][1],))
    libro.record_missing_order_block(5)

    return CheckGroup(
        title="Z.4 La garantía anti-lookahead salta de verdad",
        note="Un guardarraíl que nunca se ha visto saltar no es un guardarraíl.",
        checks=(
            Check(
                "1. zona antes de su nacimiento (UL del ID#1 en b2)",
                "LookaheadError",
                _catch(lambda: items[0][1].borders_at(series.at(2))),
            ),
            Check(
                "2. OB antes de su confirmación (ID#4 en b14)",
                "LookaheadError",
                _catch(lambda: ob_tardio.borders_at(series.at(14))),
            ),
            Check(
                "3. extensión del UL antes de cerrar su vela (ID#2 en b5)",
                "LookaheadError",
                _catch(lambda: ul_extendido.outer_at(series.at(5))),
            ),
            Check(
                "4. OB de un ID que nunca lo tuvo (ID#5)",
                "LookaheadError",
                _catch(lambda: libro.of(5, ZoneKind.ORDER_BLOCK, at=series.at(19))),
            ),
            Check(
                "y en cuanto se puede, se lee",
                "2023.0000",
                f"{ul_extendido.outer_at(series.at(6)):.4f}",
            ),
        ),
    )


def _catch(action: Callable[[], object]) -> str:
    try:
        action()
    except LookaheadError:
        return "LookaheadError"
    return "no saltó"


# --- Apagado y regresión -----------------------------------------------------


def _zones_off(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """§4 — con las zonas apagadas el sistema sale exactamente como en la fase 1."""
    off = replace(config, zones=ZonesConfig(enabled=False))
    on = replace(config, zones=ZonesConfig(enabled=True))
    sin = DetectDominantImpulses(off).execute(dict(series))
    con = DetectDominantImpulses(on).execute(dict(series))
    zonas_off = detect_zones(sin)
    zonas_on = detect_zones(con)

    identical = all(
        analysis.impulses == sin.analyses[timeframe].impulses
        and analysis.events == sin.analyses[timeframe].events
        and analysis.states == sin.analyses[timeframe].states
        for timeframe, analysis in con.analyses.items()
    )
    return CheckGroup(
        title="Z.5 Con las zonas apagadas no se emite nada, y encenderlas no mueve nada",
        note=(
            "`zones.enabled: false`: ni una zona, ni una fila, ni un fichero. Y con `true`, "
            "los impulsos, los eventos y el estado por barra son los mismos objetos."
        ),
        checks=(
            Check("apagadas, no emiten nada", "True", str(zonas_off.emits_nothing)),
            Check("apagadas, filas en la tabla de zonas", "0", str(len(zonas_off.table()))),
            Check("encendidas, temporalidades con zonas", "3", str(len(zonas_on.per_timeframe))),
            Check(
                "impulsos con zonas y sin zonas",
                format_counts({tf: len(a.impulses) for tf, a in sin.analyses.items()}),
                format_counts({tf: len(a.impulses) for tf, a in con.analyses.items()}),
            ),
            Check("impulsos, eventos y estados idénticos", "True", str(identical)),
            Check("config_hash", sin.config_hash, con.config_hash),
        ),
    )


def _regression(config: ImpulseConfig, series: Mapping[str, pd.DataFrame]) -> CheckGroup:
    """La línea base de la fase 1, comprobada **con las zonas puestas**."""
    on = replace(config, zones=ZonesConfig(enabled=True))
    run = DetectDominantImpulses(on).execute(dict(series))
    obtained = {tf: len(a.impulses) for tf, a in run.analyses.items()}
    expected = {tf: PHASE1_BASELINE[tf] for tf in obtained if tf in PHASE1_BASELINE}
    zones = detect_zones(run)

    return CheckGroup(
        title="Z.6 Regresión de la línea base de la fase 1 con las zonas encendidas",
        note=(
            f"{format_counts(PHASE1_BASELINE)} y hash {BASELINE_HASH}. Las zonas no entran en el "
            "hash porque no mueven ni una vela ni un impulso, y esto es lo que lo comprueba."
        ),
        checks=(
            Check("impulsos detectados", format_counts(expected), format_counts(obtained)),
            Check("config_hash", BASELINE_HASH, run.config_hash),
            Check("y aun así hay zonas", "True", str(zones.enabled and bool(zones.per_timeframe))),
        ),
    )


__all__ = ["collect"]
