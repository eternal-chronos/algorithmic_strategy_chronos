"""Informe de la fase 3.1 en texto plano.

Se lee en un terminal, se pega en un correo y se archiva junto a las capturas, así
que va en ancho fijo y sin colores.

Cuatro cosas que este informe hace y que no son decorativas:

1. **La portada declara de dónde salió cada lado del precio.** El §4 lo exige
   porque no hay fichero de ask en el proyecto, y un neto leído sin saberlo es un
   neto mal leído.
2. **Los stops van ANTES que los resultados.** El propietario lo pidió así: si ve
   primero qué operaciones ganaron, su juicio sobre dónde va el stop queda
   contaminado. El orden de las secciones es esa nota convertida en índice.
3. **Ningún número agregado sin su desglose.** Todas las tablas traen la
   población delante y ninguna mezcla poblaciones sin decirlo.
4. **La columna de la 3.0 al lado.** La 3.1 cambia una sola cosa —qué confirma en
   H1— y sin la columna anterior no se puede saber qué movió ese cambio.

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se presentan
números; decide el propietario.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from chronos.application.entries import archetypes, comparison, metrics
from chronos.application.entries.cascade import CascadeRun
from chronos.application.entries.comparison import (
    LostConfirmation,
    PhaseRun,
    PriorityEffect,
)
from chronos.application.entries.config import EntriesConfig
from chronos.application.entries.execution import ExecutionRun
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.domain.entries.signal import STOP_VERSION, TARGET_R
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.ascii_table import render_table, section

_COUNT = ",d"
_PCT = ".1%"
_R = "+.3f"
_R_ABS = ".3f"

_METRICS = {
    "n": _COUNT,
    "abiertas": _COUNT,
    "aciertos": _COUNT,
    "win_rate": _PCT,
    "expectativa_bruta_r": _R,
    "expectativa_neta_r": _R,
    "ic95_bajo_r": _R,
    "ic95_alto_r": _R,
    "payoff_medio_r": ".2f",
    "coste_medio_r": _R_ABS,
    "coste_p90_r": _R_ABS,
    "racha_ganadora": _COUNT,
    "racha_perdedora": _COUNT,
}
_COMPARED = {
    # Con coma y sin decimales, no `,d`: una población que existe en una fase y
    # no en la otra deja la celda vacía, y una columna con un hueco es float.
    "n_31": ",.0f",
    "n_30": ",.0f",
    "win_rate_31": _PCT,
    "win_rate_30": _PCT,
    "expectativa_bruta_r_31": _R,
    "expectativa_bruta_r_30": _R,
    "expectativa_neta_r_31": _R,
    "expectativa_neta_r_30": _R,
    "coste_medio_r_31": _R_ABS,
    "coste_medio_r_30": _R_ABS,
}
_FUNNEL = {"n": _COUNT, "pct_sobre_el_anterior": _PCT, "pct_sobre_el_primero": _PCT}
_FUNNEL_PAIR = {
    "n_30": _COUNT,
    "n_31": _COUNT,
    "diferencia": "+,d",
    "pct_de_la_30": _PCT,
}
_RAILS = {"n": _COUNT, "pct": _PCT}
_VIAS = {"n": _COUNT, "pct": _PCT, "con_la_otra_via_disponible": _COUNT}
_LOST = {"n": _COUNT}
_COSTS = {"n": _COUNT, "coste_medio_r": _R_ABS, "coste_p90_r": _R_ABS, "coste_maximo_r": _R_ABS}
_RISK = {
    "n": _COUNT,
    "usd_minimo": ",.3f",
    "bajo_una_horquilla": _COUNT,
    **{f"usd_p{value}": ",.2f" for value in (10, 25, 50, 75, 90)},
    **{f"atr_p{value}": ".2f" for value in (10, 25, 50, 75, 90)},
    **{f"pct_precio_p{value}": ".4%" for value in (10, 25, 50, 75, 90)},
}
_FREQUENCY = {
    "semanas": _COUNT,
    "operaciones": _COUNT,
    "por_semana": ".2f",
    "semanas_vacias": _COUNT,
    "pct_vacias": _PCT,
}


def render_entry_report(
    run: ImpulseRun,
    cascade: CascadeRun,
    execution: ExecutionRun,
    trades: pd.DataFrame,
    *,
    provenance: str,
    regression_ok: bool,
    regression_note: str,
    previous: PhaseRun | None = None,
    priority: PriorityEffect | None = None,
    lost: Sequence[LostConfirmation] = (),
    v30_regression: str = "",
    generated_at: datetime | None = None,
) -> str:
    """El informe entero. El orden de las secciones es el que pidió el propietario."""
    generated_at = generated_at or SystemClock().now()
    config = cascade.config
    before = previous.trades if previous is not None else pd.DataFrame()
    parts = [
        _cover(run, cascade, execution, provenance, generated_at),
        _regression(regression_ok, regression_note, v30_regression),
        _scope(cascade),
        _decisions(config),
        _stop_first(trades, before),
        _funnel(cascade, execution, previous, lost),
        _vias(cascade, trades, priority),
        _results(trades, execution, before),
        _costs(trades, before),
        _direction_by_year(trades, before),
        _frequency(trades, before),
        _rejections(trades),
        _archetypes(cascade, execution),
        _edge_cases(),
    ]
    return "\n".join(parts) + "\n"


# --- Portada -----------------------------------------------------------------


def _cover(
    run: ImpulseRun,
    cascade: CascadeRun,
    execution: ExecutionRun,
    provenance: str,
    generated_at: datetime,
) -> str:
    rows = [
        ("Símbolo", run.config.symbol),
        ("Lado del precio (STRUCTURE_SIDE)", run.config.structure_side),
        ("Origen de los datos", provenance),
        ("Estructura", f"fase 2.1 · BREAK_BY_ZONE = true · hash {run.config_hash}"),
        (
            "Cascada",
            f"fase 3.1 · CONFIRM_MODE = {cascade.config.confirm_mode.value} · "
            f"CONFIRM_PRIORITY = {cascade.config.confirm_priority.value} · "
            f"hash {cascade.config_hash}",
        ),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    width = max(len(label) for label, _ in rows)
    header = "\n".join(f"{label.ljust(width)}   {value}" for label, value in rows)
    return (
        "FASE 3.1 · CORRECCIÓN DE LA CONFIRMACIÓN EN H1\n"
        "==============================================\n\n"
        f"{header}\n\n"
        "DECLARACIÓN OBLIGATORIA DE PORTADA (§4)\n"
        "---------------------------------------\n"
        f"{_wrap(execution.price_side_note)}\n\n"
        "COSTES · TODOS MARCADOS VERIFICAR\n"
        "---------------------------------\n"
        "Ninguno está calibrado contra Pepperstone Razor. Se aplican siempre —un\n"
        "backtest sin horquilla, comisión y swap no dice nada sobre XAUUSD— pero el\n"
        "neto que sale de aquí NO es el neto de una cuenta real hasta que estos\n"
        "cuatro números se comprueben.\n"
        + "\n".join(f"  · {note}" for note in execution.notes)
        + "\n"
    )


def _regression(ok: bool, note: str, v30: str) -> str:
    verdict = "PASA" if ok else "FALLA"
    body = [
        section("0. Regresión: dos líneas base que esta fase no puede mover"),
        "",
        "Todo apagable con test. Con `entries.enabled: false` la corrida tiene que",
        "reproducir la fase 2.1 exacta: la cascada lee la estructura y no la toca.",
        "",
        f"  0.1 · LÍNEA BASE DE LA FASE 2.1 — VEREDICTO: {verdict}",
        f"        {note}",
        "",
        "Y con `CONFIRM_MODE = v30_tres_vias` el motor tiene que reproducir la fase",
        "3.0 exacta. Si no saliera idéntico habría un bug en la refactorización y la",
        "comparación entre las dos fases no significaría nada.",
        "",
        f"  0.2 · LÍNEA BASE DE LA FASE 3.0\n        {v30}" if v30 else
        "  0.2 · LÍNEA BASE DE LA FASE 3.0 — no se corrió en esta ejecución.",
    ]
    return "\n".join(body) + "\n"


def _scope(cascade: CascadeRun) -> str:
    return (
        section("Alcance de esta fase")
        + "\n\n"
        "La 3.1 corrige la ESPECIFICACIÓN de la confirmación en H1, que en la 3.0\n"
        "trataba tres vías como equivalentes. No es un arreglo de código: el\n"
        "propietario nunca dijo que un ID de H1 confirmara una entrada, y las tres\n"
        "definiciones de rechazo se quedaron sin decidir y funcionando EN UNIÓN, que\n"
        "es el criterio más laxo posible.\n\n"
        "NO SE HA TOCADO nada más: ni el ID, ni las zonas, ni la regla de rotura por\n"
        "zona, ni la ejecución, ni el objetivo. No hay FVG. No se ha optimizado nada:\n"
        "no se ha corrido ni una barrida buscando umbrales que mejoren el resultado.\n\n"
        "H1 CONFIRMA POR DOS VÍAS Y SÓLO DOS:\n\n"
        "  Vía 1 · TURTLE SOUP\n"
        "          Dos velas de H1 CONSECUTIVAS. La primera deja una mecha en la\n"
        "          dirección del movimiento previo. La segunda llega a esa mecha y\n"
        "          cierra sin superarla: su recorrido alcanza el extremo de la mecha\n"
        "          anterior pero su cierre queda de este lado. No lo supera con\n"
        "          cuerpo: lo rechaza. Sin parámetros, sin umbrales, sin percentiles.\n\n"
        "  Vía 2 · OB DE H1 ALCANZADO\n"
        "          Hay ID de H1 en la dirección buscada, su OB está FORMADO y el\n"
        "          PRECIO LLEGA a ese OB. La entrada se coloca en ese OB. Un ID de H1\n"
        "          sin OB formado no confirma nada; que el OB exista tampoco basta.\n\n"
        "LO QUE SE ELIMINA:\n\n"
        "  · `id_h1` como vía independiente. Un ID solo no confirma.\n"
        "  · R1, R2 y R3 como vías. Se siguen calculando y persistiendo en el CSV\n"
        "    como columnas informativas y NINGUNA REGLA LAS LEE: sirven para\n"
        "    estudiarlas más adelante sin recalcular el histórico.\n\n"
        "El juego de la 3.0 sigue implementado bajo `CONFIRM_MODE = v30_tres_vias`,\n"
        "SÓLO para el test de regresión y para poner las dos columnas al lado. No es\n"
        "una variante del proyecto.\n\n"
        "TURTLE SOUP EN TODA LA SERIE DE H1 (informativo, no es una señal):\n"
        + "".join(
            f"  · {direction}: {count:,} patrones en el histórico completo\n"
            for direction, count in sorted(cascade.turtle_census.items())
        )
        + f"  · de ellos, {len(cascade.turtle_soups):,} caen dentro de una ventana de\n"
        "    observación viva y son los que la cascada llegó a mirar.\n"
    )


def _decisions(config: EntriesConfig) -> str:
    closed = "\n".join(f"  · {item}" for item in config.closed_decisions())
    open_ = "\n".join(f"  · {item}" for item in config.open_decisions())
    return (
        section("Parámetros")
        + "\n\n"
        "Cerrados por el enunciado:\n"
        f"{closed}\n\n"
        "ABIERTOS. Los decide el propietario MIRANDO GRÁFICOS, no expectativas. El\n"
        "valor que sale impreso es el que se ha usado para calcular, no una\n"
        "recomendación: no se ha buscado ninguno optimizando.\n"
        f"{open_}\n"
    )


# --- Los stops, ANTES de los resultados (§5.4) -------------------------------


def _stop_first(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("1. LOS STOPS Y EL 1R · míralos ANTES de bajar a los resultados"),
        "",
        "Nota para el propietario: si ves primero qué operaciones ganaron, tu juicio",
        "sobre dónde va el stop queda contaminado. Esta sección va delante por eso y",
        "no por orden alfabético.",
        "",
        f"STOP VERSIÓN {STOP_VERSION}, PRE-REGISTRADA antes de ver ningún resultado.",
        "Al otro lado de la zona de entrada, exactamente en su borde exterior, sin",
        "holgura añadida. Cualquier cambio posterior será la versión 2 y contará como",
        "configuración medida, no como corrección.",
        "",
        f"Objetivo 1 : {TARGET_R:g} R fijo, siempre. Sin parciales, sin trailing, sin",
        "break-even.",
        "",
        metrics.break_even_note(),
        "",
        section("1.1 · §5.4 · Distribución del 1R en USD, en ATR y en % del precio", 2),
        "",
        "El control de sanidad que dice si la fórmula del stop aterriza en una banda",
        "operable o produce stops de un dólar que la horquilla se come. Los dólares",
        "solos no comparan 2018 con 2025 —el oro pasó de ~1.200 a ~4.300— así que las",
        "tres unidades son obligatorias. `bajo_una_horquilla` cuenta los 1R menores",
        "que una horquilla entera (VERIFICAR): ahí el desenlace no es fiable. En la",
        "3.0 eran 21 operaciones, con un mínimo de 1 céntimo.",
        "",
        render_table(metrics.risk_distribution(trades), formats=_RISK),
        "",
        section("1.2 · El mismo 1R por variante de entrada y de stop", 2),
        "",
        render_table(
            metrics.risk_distribution(
                trades.assign(configuracion=_configuration(trades)), by="configuracion"
            )
            if not trades.empty
            else trades,
            formats=_RISK,
        ),
        "",
        section("1.3 · El mismo 1R de la fase 3.0, para comparar", 2),
        "",
        render_table(metrics.risk_distribution(before), formats=_RISK),
    ]
    return "\n".join(body) + "\n"


def _configuration(trades: pd.DataFrame) -> pd.Series:
    return trades["entrada_en"].astype(str) + " / stop " + trades["stop_en"].astype(str)


# --- §5.1 El embudo, antes y después -----------------------------------------


def _funnel(
    cascade: CascadeRun,
    execution: ExecutionRun,
    previous: PhaseRun | None,
    lost: Sequence[LostConfirmation],
) -> str:
    body = [
        section("2. §5.1 · Embudo completo, antes y después"),
        "",
        "De cuántos contactos de zona a cuántas operaciones, y dónde se cae cada una.",
        "",
        "DOS PASOS SUBEN EN VEZ DE BAJAR, y no es un error de recuento: una misma zona",
        "tocada produce hasta DOS observaciones —la de respeto y la de rotura y",
        "retesteo, que son momentos distintos— y una misma señal produce hasta DOS",
        "operaciones, una por variante de stop. El embudo es la cascada, no una",
        "partición, y se deja como sale.",
        "",
        render_table(metrics.funnel(cascade, execution), formats=_FUNNEL),
    ]
    if previous is not None:
        body += [
            "",
            section("2.1 · El mismo embudo, 3.0 contra 3.1", 2),
            "",
            "Los pasos de arriba —zonas y observaciones— son IDÉNTICOS por construcción:",
            "la 3.1 sólo cambia qué confirma en H1 y las observaciones se recogen antes",
            "de mirar H1. Si alguno de esos pasos se moviera, sería un bug.",
            "",
            render_table(
                comparison.funnel_comparison(
                    previous.cascade, cascade, previous.execution, execution
                ),
                formats=_FUNNEL_PAIR,
            ),
        ]
    body += [
        "",
        section("2.2 · Observaciones que confirmaban en la 3.0 y ahora mueren", 2),
        "",
        f"{len(lost):,} observaciones confirmaban en la 3.0 y con las dos vías de la",
        "3.1 ya no confirman. Se cuentan por la vía que las confirmaba antes y por el",
        "guardarraíl en el que mueren ahora. El detalle completo, una fila por",
        "observación, está en `confirmaciones_perdidas.csv`.",
        "",
        render_table(comparison.lost_table(lost), formats=_LOST),
        "",
    ]
    if previous is not None:
        gained = comparison.gained_confirmations(previous.cascade, cascade)
        body += [
            "Y AL REVÉS: hay observaciones que la 3.1 confirma y la 3.0 no. No es una",
            "contradicción y es lo que hace que la resta cuadre: la vía 2 confirma",
            "cuando el PRECIO LLEGA al OB, que puede ser muchas velas después de que",
            "el OB naciera, y en la 3.0 esa visita no confirmaba nada.",
            "",
            f"  {len(lost):,} confirmaciones perdidas · {len(gained):,} ganadas · "
            f"saldo {len(gained) - len(lost):+,}",
            "",
        ]
    body += [
        section("2.3 · En qué guardarraíl muere cada señal descartada", 2),
        "",
        "Cuenta MOTIVOS, no zonas: una zona puede aportar dos, uno por su observación",
        "de respeto y otro por su rama de rotura y retesteo.",
        "",
        render_table(metrics.guard_rails(cascade, execution), formats=_RAILS),
        "",
        "DOS DE ESTOS CEROS SON ESTRUCTURALES, no casualidad de la muestra, y se",
        "dejan en la tabla porque un guardarraíl que no puede dispararse nunca es",
        "un dato sobre las reglas:",
        "",
        "  · `zona_de_entrada_plana` no puede darse. La vela que define una zona de",
        "    entrada —el ancla del OB de H1, la contraria del OB suelto de M15— es",
        "    siempre de cuerpo no nulo, y entonces su `high` es mayor que su `low`",
        "    por fuerza. La zona siempre mide algo.",
        "  · `id_h4_muerto` sólo puede darse en el borde del histórico, cuando el",
        "    contacto de H4 cae después de la última vela de H1. Dentro de la",
        "    muestra no hay ninguno.",
    ]
    if cascade.notes:
        body += ["", "Notas de la corrida:"]
        body += [f"  · {note}" for note in cascade.notes]
    return "\n".join(body) + "\n"


# --- §5.2 Las dos vías --------------------------------------------------------


def _vias(
    cascade: CascadeRun, trades: pd.DataFrame, priority: PriorityEffect | None
) -> str:
    body = [
        section("3. §5.2 · Confirmaciones por vía"),
        "",
        "Cuántas observaciones confirmó cada vía y cuántas veces la otra estaba",
        "disponible en la misma vela. La segunda columna es la que dice si las dos",
        "vías se están pisando o si cada una encuentra cosas distintas.",
        "",
        render_table(comparison.confirmations_by_via(cascade), formats=_VIAS),
    ]
    if priority is not None:
        body += [
            "",
            section("3.1 · ¿Importa el orden? (CONFIRM_PRIORITY)", 2),
            "",
            "Con las dos vías disponibles en la misma vela hace falta un orden",
            "determinista. El propietario no lo ha fijado, así que se declara como",
            "parámetro abierto y se mide qué cambia al invertirlo: se corre la cascada",
            f"entera con `{priority.other.value}` y se comparan las decisiones una a una.",
            "",
            *(f"  {line}" for line in _wrap(priority.describe(), 76).splitlines()),
        ]
    body += [
        "",
        section("3.2 · Resultados de cada vía por separado", 2),
        "",
        "Expectativa neta y bruta, win rate e intervalo de confianza de cada vía. El",
        "intervalo es normal-asintótico y aproximado: con menos de ~30 operaciones no",
        "significa gran cosa, y está para que se vea cuándo la población es demasiado",
        "pequeña para decir nada.",
        "",
        render_table(metrics.by_via(trades), formats=_METRICS),
        "",
        section("3.3 · Turtle soup detectados dentro de una observación", 2),
        "",
        "El patrón se dibuja en el explorador CONFIRME O NO. Aquí está el recuento por",
        "motivo: `confirma` es la vía que se tomó, `gana_el_ob` es que las dos cayeron",
        "en la misma vela y el orden dio el OB, y `fuera_de_zona` es que el patrón",
        "apareció con la observación viva pero el precio ya no estaba dentro de la",
        "zona de H4, que es la puerta de todas las confirmaciones.",
        "",
        "SE CUENTA UNA VEZ POR VELA Y DIRECCIÓN, no una por observación: el patrón es",
        "el mismo aunque lo estén mirando dos zonas de H4 a la vez, y dibujarlo dos",
        "veces en el mismo minuto sólo emborrona el gráfico. Por eso `confirma` puede",
        "quedar por debajo de las confirmaciones por turtle soup de la tabla de",
        "arriba: dos observaciones distintas pueden confirmar con la misma vela.",
        "",
        render_table(_turtle_reasons(cascade), formats={"n": _COUNT}),
    ]
    return "\n".join(body) + "\n"


def _turtle_reasons(cascade: CascadeRun) -> pd.DataFrame:
    if not cascade.turtle_soups:
        return pd.DataFrame(columns=["motivo", "direccion", "n"])
    frame = pd.DataFrame(
        [
            {"motivo": item.reason.value, "direccion": item.direction.value}
            for item in cascade.turtle_soups
        ]
    )
    counted = frame.groupby(["motivo", "direccion"], sort=True).size().reset_index(name="n")
    total = pd.DataFrame(
        [{"motivo": "TOTAL", "direccion": "", "n": len(cascade.turtle_soups)}]
    )
    return pd.concat([counted, total], ignore_index=True)


# --- §5.3 Los resultados ------------------------------------------------------


def _results(
    trades: pd.DataFrame, execution: ExecutionRun, before: pd.DataFrame
) -> str:
    body = [
        section("4. §5.3 · Resultados · SIEMPRE en R, netos y brutos"),
        "",
        "La diferencia entre el bruto y el neto es lo que diagnostica si un efecto es",
        "real o aritmética de costes. Por eso van los dos y nunca uno solo.",
        "",
        "El intervalo de confianza es normal-asintótico y aproximado: con menos de",
        "~30 operaciones no significa gran cosa, y está justamente para que se vea",
        "cuándo la población es demasiado pequeña para decir nada.",
        "",
        "Cada desglose va dos veces: la tabla completa de la 3.1 y, debajo, las mismas",
        "poblaciones con la columna de la 3.0 al lado. Una población que existía en la",
        "3.0 y ya no existe sale con la columna de la 3.1 vacía: es un dato.",
        "",
        section("4.1 · Las configuraciones (entrada, stop) medidas por separado", 2),
        "",
        "La combinación *entrada en H1 con stop de M15* NO aparece y no es un olvido:",
        "la zona de M15 se forma DESPUÉS de decidir la entrada de H1, así que su stop",
        "no se puede leer en el instante de decidir. Se declara en vez de rellenarse.",
        "",
        render_table(metrics.configurations(execution), formats=_METRICS),
    ]
    # Los títulos ya vienen numerados con el desglose que pedía la fase 3.0
    # (5.1, 5.2, ...): se dejan tal cual para que quien lea el informe con el
    # enunciado al lado encuentre cada desglose donde lo pidió.
    for title, table in metrics.all_breakdowns(trades):
        body += ["", section(title, 2), "", render_table(table, formats=_METRICS)]
        if before.empty:
            continue
        twin = _same_breakdown(title, before)
        if twin is None:
            continue
        body += [
            "",
            "        3.1 contra 3.0:",
            "",
            render_table(metrics.side_by_side(table, twin), formats=_COMPARED),
        ]
    return "\n".join(body) + "\n"


def _same_breakdown(title: str, before: pd.DataFrame) -> pd.DataFrame | None:
    """El mismo desglose sobre las operaciones de la 3.0.

    Se busca por título para que las dos tablas no se puedan desparejar: si un
    desglose cambiara de columna, aquí saldría `None` en vez de comparar dos
    cosas distintas sin avisar.
    """
    for name, table in metrics.all_breakdowns(before):
        if name == title:
            return table
    return None


# --- §5.5 El coste por operación ---------------------------------------------


def _costs(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("5. §5.5 · Coste por operación en R, por vía y por stop"),
        "",
        "El coste no es un residuo: en la fase 3.0 el stop de M15 costaba 0,191 R por",
        "operación, y sobre una expectativa que se mide en centésimas de R eso decide",
        "el signo. Va con el percentil 90 y el máximo porque la media sola esconde las",
        "operaciones con el 1R minúsculo, que son las que se comen la horquilla.",
        "",
        "Todos los costes van marcados VERIFICAR: horquilla, deslizamiento, comisión y",
        "swap. Ninguno está calibrado contra Pepperstone Razor.",
        "",
        render_table(metrics.cost_by_via_and_stop(trades), formats=_COSTS),
        "",
        section("5.1 · El mismo coste en la fase 3.0", 2),
        "",
        render_table(metrics.cost_by_via_and_stop(before), formats=_COSTS),
    ]
    return "\n".join(body) + "\n"


# --- §5.6 Largos y cortos, por año -------------------------------------------


def _direction_by_year(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("6. §5.6 · ⚠️ LARGOS Y CORTOS POR AÑO, POR SEPARADO"),
        "",
        "ESTA TABLA ES IMPRESCINDIBLE. Sin ella no se puede distinguir si los cortos",
        "fallan por el setup o porque el oro subió de ~1.200 a ~4.300 USD durante todo",
        "el histórico. En la fase 3.0 los largos daban +0,010 R y los cortos -0,332 R.",
        "",
        "AQUÍ NO SE SACA NINGUNA CONCLUSIÓN Y NO SE PROPONE FILTRAR POR DIRECCIÓN.",
        "Se presenta y decide el propietario.",
        "",
        render_table(metrics.by_year_and_direction(trades), formats=_METRICS),
        "",
        section("6.1 · La misma tabla en la fase 3.0", 2),
        "",
        render_table(metrics.by_year_and_direction(before), formats=_METRICS),
    ]
    return "\n".join(body) + "\n"


# --- §5.7 Frecuencia ----------------------------------------------------------


def _frequency(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("7. §5.7 · Frecuencia: operaciones por semana y semanas sin señal"),
        "",
        "Las semanas se cuentan sobre el calendario completo del histórico y no sobre",
        "las semanas en que hubo operaciones: si no, el porcentaje de semanas vacías",
        "saldría siempre cero por construcción.",
        "",
    ]
    if trades.empty:
        body.append("Ninguna operación: no hay frecuencia que contar.")
        return "\n".join(body) + "\n"
    first = pd.Timestamp(trades["ts_entrada"].min())
    last = pd.Timestamp(trades["ts_entrada"].max())
    body += [render_table(metrics.frequency(trades, first, last), formats=_FREQUENCY)]
    if not before.empty:
        body += [
            "",
            section("7.1 · La misma frecuencia en la fase 3.0", 2),
            "",
            render_table(
                metrics.frequency(
                    before,
                    pd.Timestamp(before["ts_entrada"].min()),
                    pd.Timestamp(before["ts_entrada"].max()),
                ),
                formats=_FREQUENCY,
            ),
        ]
    return "\n".join(body) + "\n"


# --- R1, R2 y R3: ya no confirman, se siguen midiendo ------------------------


def _rejections(trades: pd.DataFrame) -> str:
    overlap = metrics.rejection_overlap(trades)
    body = [
        section("8. R1, R2 y R3 · YA NO CONFIRMAN NADA · columnas informativas"),
        "",
        "En la fase 3.0 las tres definiciones de rechazo funcionaban EN UNIÓN y el",
        "95,5 % de las confirmaciones llegó por ahí. En la 3.1 dejan de ser vías: se",
        "siguen calculando sobre la vela que confirma, se siguen persistiendo en",
        "`operaciones.csv` y NINGUNA REGLA LAS LEE. Están para poder estudiarlas más",
        "adelante sin recalcular ocho años de H1.",
        "",
        "  R1_mecha_en_zona_cierre_fuera  la mecha entra en la zona y el cuerpo cierra",
        "                                 fuera, a favor de la dirección buscada. Sin",
        "                                 parámetros.",
        "  R2_mecha_dominante             la mecha contra el movimiento supera el",
        "                                 percentil P de la proporción mecha/cuerpo de",
        "                                 H1, calculado SOLO sobre sesiones anteriores.",
        "  R3_cierre_en_extremo           el cierre queda en el tercio favorable del",
        "                                 rango de la vela.",
        "",
        "El doji no marca en ninguna de las tres: se declara neutro en todo el módulo",
        "y esa lectura no se rompe aquí.",
        "",
        section("8.1 · Cuántas marca cada una sobre las velas que confirmaron", 2),
        "",
        "La diagonal es cuántas marcó cada definición; fuera de la diagonal, cuántas",
        "marcaron las dos a la vez. Las poblaciones se solapan y NO suman al total.",
        "",
        render_table(overlap.reset_index(names="definicion") if not overlap.empty else overlap),
        "",
        section("8.2 · Resultados por definición · SIN VALOR DE DECISIÓN", 2),
        "",
        "Estas filas NO son estrategias: son la misma población de la 3.1 recortada",
        "por una etiqueta que no decidió nada. Leerlas como si fueran variantes sería",
        "exactamente el error que la 3.1 viene a corregir.",
        "",
        render_table(metrics.by_rejection(trades), formats=_METRICS),
    ]
    return "\n".join(body) + "\n"


# --- Las cinco auditorías por arquetipo --------------------------------------


def _archetypes(cascade: CascadeRun, execution: ExecutionRun) -> str:
    found = archetypes.audit(cascade, execution)
    body = [
        section("9. Las cinco auditorías por arquetipo"),
        "",
        "Un ejemplar de cada forma que la cascada puede tomar, elegido con un criterio",
        "escrito ANTES de mirar el resultado y el mismo para todos: el primero",
        "cronológicamente que cumple la forma. No es una selección de las mejores.",
        "",
        "SI UN ARQUETIPO NO EXISTE EN LA MUESTRA, SE DECLARA. La ausencia es un dato.",
        "",
        render_table(archetypes.table(found)),
    ]
    for item in found:
        body += [
            "",
            section(f"9.{item.number} · {item.title}", 2),
            "",
            f"Criterio: {item.criterion}",
            "",
            *item.narrative(),
            "",
            f"Captura: {item.capture}.png" if item.found else "Captura: no procede.",
        ]
    return "\n".join(body) + "\n"


# --- Los casos límite --------------------------------------------------------


def _edge_cases() -> str:
    return (
        section("10. Casos límite encontrados y cómo se resolvieron")
        + "\n\n"
        + "\n\n".join(_wrap(case) for case in EDGE_CASES)
        + "\n"
    )


#: Los casos límite que aparecieron al construir la fase y qué se decidió en cada
#: uno. Van en el informe y no sólo en los comentarios del código porque son
#: decisiones, y las decisiones las audita el propietario. Los de la 3.0 siguen
#: vigentes: la 3.1 no ha tocado nada de lo que los produjo.
EDGE_CASES: tuple[str, ...] = (
    "QUÉ MECHA MIRA EL TURTLE SOUP. El enunciado la nombra por el contexto —«mecha "
    "superior en un contexto alcista»— y el contexto es el MOVIMIENTO PREVIO, el que "
    "trajo el precio a la zona, que va en contra de lo que se busca. Para una entrada "
    "alcista la vela que la trae es la que hace suelo: la mecha que interesa es la "
    "INFERIOR y lo que confirma es que la segunda vela baje a ese mínimo y cierre por "
    "encima de él. Escrito al derecho en el código: la mecha se mide del lado contrario "
    "a la dirección buscada. Leerlo del otro modo daría un patrón que confirma compras "
    "rechazando máximos, que es lo contrario de un turtle soup.",
    "«LLEGA A LA MECHA» ES ALCANZAR EL EXTREMO, NO ROZAR LA MECHA. El enunciado dice "
    "las dos cosas en la misma frase —«llega a esa mecha», «su recorrido alcanza el "
    "extremo»— y sólo la segunda es comprobable sin inventar un umbral de cuánta mecha "
    "hay que tocar. Se ha implementado el extremo: el `low` de la segunda vela tiene "
    "que llegar al `low` de la primera (o el `high` al `high`, en bajista). Con el "
    "borde incluido, porque tocarlo es llegar.",
    "«SIN SUPERARLA CON EL CIERRE» ES ESTRICTO. Cerrar exactamente EN el extremo no lo "
    "supera, pero tampoco lo rechaza: la desigualdad se ha escrito estricta (`cierre > "
    "extremo` en alcista) para que el patrón exija que el precio se haya separado del "
    "nivel. Es la misma convención que el resto del módulo, donde todas las "
    "desigualdades son estrictas y el espejo las invierte a la vez.",
    "LA VÍA 2 NO ES LA VÍA `ob_h1` DE LA 3.0 CON OTRO NOMBRE. En la 3.0 el OB confirmaba "
    "EN LA VELA EN QUE NACÍA. En la 3.1 confirma cuando EL PRECIO LLEGA a él, que puede "
    "ser mucho después o no ocurrir nunca. Comparten etiqueta en el CSV porque los dos "
    "modos no se mezclan jamás en una corrida, y separarlas habría obligado a leer dos "
    "columnas para contestar a la misma pregunta.",
    "EL ORDEN ENTRE LAS DOS VÍAS HABÍA QUE FIJARLO Y NO ESTABA DECIDIDO. Se ha "
    "declarado `CONFIRM_PRIORITY` como parámetro abierto, con un valor por defecto que "
    "no se ha elegido optimizando, y se mide qué cambia al invertirlo corriendo la "
    "cascada entera del otro modo y comparando decisión a decisión. El informe lo dice "
    "en la sección 3.1; si no cambia ninguna operación, se declara cosmético.",
    "LOS TURTLE SOUP SE DIBUJAN AUNQUE NO CONFIRMEN, Y HABÍA QUE ACOTAR CUÁLES. El "
    "patrón aparece por toda la serie de H1 y dibujarlos todos taparía el gráfico con "
    "decenas de miles de marcas. Se dibujan los que la cascada MIRÓ —los que caen "
    "dentro de una ventana de observación viva—, cada uno con el motivo por el que "
    "acabó como acabó, y el informe da el censo total de la serie para que no parezca "
    "que el patrón sólo existe donde hay zona.",
    "EL UL SE MUEVE MIENTRAS LA ZONA ESTÁ EN OBSERVACIÓN. Con la rotura por zona de "
    "la fase 2.1 un ID vigente extiende su extremo, y cada extensión cambia su UL. La "
    "fase 2.0 guarda UNA zona por impulso: la que le quedó al morir. Usar ésa para "
    "observar sería mirar al futuro justo en las velas que más importan, porque las "
    "que extienden el extremo son las mismas que tocan la zona. Resuelto reconstruyendo "
    "la línea temporal del UL desde la traza de extensiones que el detector ya "
    "registraba, con `LookaheadError` al pedir una barra que aún no ha cerrado.",
    "LA ZONA QUE SE RETESTEA NO ES LA QUE SE VE DESPUÉS DE ROMPERSE. La vela que "
    "rompe el UL suele ser también la vela de margen que lo estiraría, así que "
    "redibujar la zona con ella la agranda hasta contener el cierre que la atravesó. "
    "Resuelto usando la zona vigente al cierre ANTERIOR al de la rotura, que es el "
    "borde contra el que la fase 2.1 juzgó esa vela.",
    "ENTRADA EN H1 CON STOP DE M15 ES IMPOSIBLE SIN LOOKAHEAD. Hay que medir las dos "
    "variantes de stop, pero la zona de M15 se forma DESPUÉS de decidir la entrada de "
    "H1. Se han medido las tres combinaciones que sí existen —H1/H1, M15/M15 y M15/H1— "
    "y la cuarta se declara imposible en vez de rellenarse.",
    "LA VENTANA DE RETESTEO NO ESTÁ ACOTADA EN EL ENUNCIADO. Tras romper el UL el "
    "precio 'vuelve a testearla' pero no se dice hasta cuándo se espera. En vez de "
    "inventar un número se ha usado la frontera que la máquina de estados ya tenía "
    "escrita —la constitución del ID de H4 siguiente— y se ha dejado el parámetro "
    "abierto con su rejilla, para que lo cierre el propietario.",
    "NO HAY FICHERO DE ASK. Se pide longs al ask y shorts al bid, y sólo está "
    "descargado el M1 del lado bid. El comando SE DETIENE por defecto; para correr hay "
    "que autorizarlo explícitamente, y entonces la asunción se declara en portada, en "
    "la salida de la CLI y en el explorador. No se ha fabricado ninguna serie de ask.",
    "EL DESLIZAMIENTO SE COBRA COMO COSTE Y NO DESPLAZANDO EL PRECIO DE EJECUCIÓN. Con "
    "el precio desplazado el bruto deja de valer exactamente -1 R o +3,3 R y la "
    "diferencia bruto/neto deja de leerse, que es justo el diagnóstico que hace falta.",
    "EL SIZING CONTINUO HACE QUE LAS MÉTRICAS EN R NO DEPENDAN DEL RIESGO POR "
    "OPERACIÓN. Todos los costes escalan con el lotaje y el lotaje escala con el "
    "riesgo, así que el coste en R es invariante. El riesgo por operación sólo fija la "
    "columna de lotes; cambiarlo no mueve ni una expectativa.",
    "HAY OPERACIONES QUE NACEN MUERTAS Y NO SE ARREGLAN. Cuando el open de la M1 "
    "siguiente a la decisión ya está al otro lado del stop, la operación no se puede "
    "abrir. Se descarta con motivo `stop_invalido` en vez de mover el stop hasta que "
    "quepa, que sería inventar una regla que el propietario no ha dado.",
    "DOS GUARDARRAÍLES SALEN A CERO POR CONSTRUCCIÓN, no por la muestra. Una zona de "
    "entrada no puede medir cero —la vela que la define tiene cuerpo, luego su `high` "
    "supera a su `low`— y el ID de H4 no puede morirse antes de que su observación "
    "tenga una sola vela de H1 salvo en el borde del histórico. Se dejan en la tabla: "
    "un guardarraíl que no puede dispararse dice algo sobre las reglas.",
)


def _wrap(text: str, width: int = 79) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width))


def render_archetype_index(
    cascade: CascadeRun,
    execution: ExecutionRun,
    captures: Sequence[str],
    lost: Sequence[LostConfirmation] = (),
) -> str:
    """El `LEEME.txt` de `now/fase31/`: qué es cada fichero de la carpeta."""
    found = archetypes.audit(cascade, execution)
    lines = [
        "FASE 3.1 · CORRECCIÓN DE LA CONFIRMACIÓN EN H1 · índice de la carpeta",
        "=====================================================================",
        "",
        "QUÉ CAMBIA EN ESTA FASE",
        "-----------------------",
        "H1 confirma por DOS vías y sólo dos:",
        "  1. TURTLE SOUP: dos velas de H1 consecutivas; la primera deja mecha y la",
        "     segunda llega a ese extremo y cierra sin superarlo. Sin parámetros.",
        "  2. OB DE H1 ALCANZADO: hay ID de H1 en la dirección, su OB está formado y",
        "     el PRECIO LLEGA a ese OB. La entrada se coloca ahí.",
        "",
        "Se elimina `id_h1` como vía (un ID solo no confirma) y se eliminan R1, R2 y",
        "R3 como vías. Las tres se siguen calculando y persistiendo en el CSV como",
        "columnas informativas: ninguna regla las lee.",
        "",
        "NOTA PARA EL PROPIETARIO: revisa los stops ANTES de mirar los resultados. Si",
        "ves primero qué operaciones ganaron, tu juicio sobre dónde va el stop queda",
        "contaminado. El informe está ordenado para eso: la sección 1 es la de los",
        "stops y va delante de la 4, que es la de los resultados.",
        "",
        "FICHEROS",
        "--------",
        "  reporte_entradas.txt   el informe entero: embudo antes y después, las dos",
        "                         vías por separado, métricas con todos los desgloses",
        "                         y la columna de la 3.0 al lado.",
        "  evidencia_entradas.txt el esperado al lado del obtenido, caso por caso:",
        "                         el día sintético de la 3.1, las excepciones",
        "                         anti-lookahead y el apagado.",
        "  operaciones.csv        una fila por operación, con la vía que confirmó, las",
        "                         vías disponibles y las columnas informativas de R1,",
        "                         R2 y R3.",
        "  operaciones_v30.csv    lo mismo con `CONFIRM_MODE = v30_tres_vias`, para",
        "                         poder comparar sin volver a correr nada.",
        "  descartadas.csv        una fila por señal descartada y el guardarraíl que",
        "                         la mató.",
        "  confirmaciones_perdidas.csv  una fila por observación que confirmaba en la",
        "                         3.0 y ahora muere, con la vía de antes y el",
        "                         guardarraíl de ahora.",
        "  explorador_entradas.html  cada operación navegable sobre las cuatro",
        "                         temporalidades, con los turtle soup y las señales",
        "                         que la 3.0 tomaba y la 3.1 descarta en capas propias.",
        "",
        "CAPTURAS",
        "--------",
        "  turtle_NN_*      veinte confirmaciones por turtle soup.",
        "  ob_NN_*          veinte confirmaciones por OB de H1 alcanzado.",
        "  perdida_NN_*     veinte señales que la 3.0 tomaba y la 3.1 descarta.",
        "  arquetipo_N_*    los cinco arquetipos, uno por forma.",
        "",
        "Las capturas de cada lote son las PRIMERAS cronológicamente de su clase, no",
        "una selección: elegir 'las mejores' habría convertido la carpeta en un",
        "argumento en vez de en una muestra.",
        "",
        f"{len(lost):,} observaciones confirmaban en la 3.0 y ahora no confirman.",
        "",
        "LOS CINCO ARQUETIPOS",
        "--------------------",
    ]
    for item in found:
        state = "encontrado" if item.found else "NO EXISTE EN LA MUESTRA"
        lines.append(f"  {item.capture}: {item.title} — {state}")
        if not item.found:
            lines.append(f"      {item.absence}")
    lines += [
        "",
        f"{len(captures)} capturas en esta carpeta.",
        "",
        "AVISOS",
        "------",
        _wrap(execution.price_side_note),
        "",
        "Todos los costes van marcados VERIFICAR: ninguno está calibrado contra",
        "Pepperstone Razor. El neto de este informe NO es el neto de una cuenta real",
        "hasta que esos cuatro números se comprueben.",
        "",
        "No se recomienda ningún parámetro y no se interpreta si la estrategia es",
        "buena o mala. Hay números e imágenes; decide el propietario.",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["EDGE_CASES", "render_archetype_index", "render_entry_report"]
