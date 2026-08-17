"""Informe de la fase 3.2 en texto plano.

Se lee en un terminal, se pega en un correo y se archiva junto a las capturas, así
que va en ancho fijo y sin colores.

Cinco cosas que este informe hace y que no son decorativas:

1. **La portada declara de dónde salió cada lado del precio.** El §4 lo exige
   porque no hay fichero de ask en el proyecto, y un neto leído sin saberlo es un
   neto mal leído.
2. **Los stops van ANTES que los resultados.** El propietario lo pidió así: si ve
   primero qué operaciones ganaron, su juicio sobre dónde va el stop queda
   contaminado. El orden de las secciones es esa nota convertida en índice.
3. **Ningún número agregado sin su desglose.** Todas las tablas traen la
   población delante y ninguna mezcla poblaciones sin decirlo.
4. **La columna de la 3.1 al lado.** La 3.2 cambia qué ABRE una operación, y sin
   la columna anterior no se puede saber qué movió ese cambio.
5. **Las tres ramas por separado** (§6.2) y **las operaciones contra el ID de H4
   aisladas** (§6.4). Son poblaciones distintas —una de ellas, nueva del
   proyecto— y promediarlas mezclaría tres estrategias en un número.

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
    "n_32": ",.0f",
    "n_31": ",.0f",
    "win_rate_32": _PCT,
    "win_rate_31": _PCT,
    "expectativa_bruta_r_32": _R,
    "expectativa_bruta_r_31": _R,
    "expectativa_neta_r_32": _R,
    "expectativa_neta_r_31": _R,
    "coste_medio_r_32": _R_ABS,
    "coste_medio_r_31": _R_ABS,
}
_FUNNEL = {"n": _COUNT, "pct_sobre_el_anterior": _PCT, "pct_sobre_el_primero": _PCT}
_FUNNEL_PAIR = {
    "n_31": _COUNT,
    "n_32": _COUNT,
    "diferencia": "+,d",
    "pct_de_la_31": _PCT,
}
_FORMS = {"disponible": _COUNT, "disparo": _COUNT, "pct_de_los_rechazos": _PCT}
_GAP = {
    "n_con_hueco": _COUNT,
    "neto_con_hueco_r": _R,
    "n_sin": _COUNT,
    "neto_sin_r": _R,
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
        _branches(trades, before),
        _rejection_forms(trades),
        _against(trades, before),
        _vias(cascade, trades, priority),
        _results(trades, execution, before),
        _costs(trades, before),
        _weekend(trades, before),
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
            f"fase 3.2 · ENTRY_MODE = {cascade.config.entry_mode.value} · "
            f"CONFIRM_MODE = {cascade.config.confirm_mode.value} · "
            f"CONFIRM_PRIORITY = {cascade.config.confirm_priority.value} · "
            f"hash {cascade.config_hash}",
        ),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    width = max(len(label) for label, _ in rows)
    header = "\n".join(f"{label.ljust(width)}   {value}" for label, value in rows)
    return (
        "FASE 3.2 · EL CONTACTO NO ES SEÑAL\n"
        "==================================\n\n"
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
        "Y con `ENTRY_MODE = v31_contacto` el motor tiene que reproducir la fase 3.1",
        "exacta: 1.508 confirmaciones y 3.298 operaciones. Si no saliera idéntico",
        "habría un bug en la refactorización y la comparación entre las dos fases no",
        "significaría nada.",
        "",
        f"  0.2 · LÍNEA BASE DE LA FASE 3.1\n        {v30}" if v30 else
        "  0.2 · LÍNEA BASE DE LA FASE 3.1 — no se corrió en esta ejecución.",
    ]
    return "\n".join(body) + "\n"


def _scope(cascade: CascadeRun) -> str:
    return (
        section("Alcance de esta fase")
        + "\n\n"
        "EL CONTACTO NO ES SEÑAL. En la 3.0 y la 3.1 tocar una zona de H4 bastaba\n"
        "para operar a favor del ID. Fue un error de la ESPECIFICACIÓN, no del\n"
        "código: se escribió que el contacto abría observación y que en el respeto se\n"
        "operaba a favor del ID. La consecuencia medida es que 2.234 de las 3.702\n"
        "operaciones de la 3.0 fueron «UL + respeto», es decir COMPRAR cuando el\n"
        "precio sube al techo del impulso: más de la mitad del backtest eran\n"
        "operaciones que no existen en la estrategia del propietario.\n\n"
        "LAS CUATRO RAMAS, Y NINGUNA MÁS:\n\n"
        "  UL rechazado ............. operación EN CONTRA del ID\n"
        "  UL roto y retesteado ..... a favor de la rotura (ya estaba)\n"
        "  OB rechazado ............. a favor del ID\n"
        "  OB roto .................. SIN operación, la observación muere\n\n"
        "Se elimina por completo la rama «respeto operado a favor del ID por\n"
        "contacto». Ninguna operación puede nacer de un contacto sin rechazo ni\n"
        "rotura con retesteo.\n\n"
        "EL RECHAZO SE ESPERA EN H4, no en H1. La vela de H4 rechaza la zona, eso fija\n"
        "la DIRECCIÓN de la operación, y sólo después H1 confirma la entrada con las\n"
        "dos vías de la 3.1, sin cambios. Dos formas válidas, y VALE LA QUE OCURRA\n"
        "PRIMERO:\n\n"
        "  Forma A · RECHAZO SIMPLE\n"
        "          Una vela de H4 entra en la zona y CIERRA FUERA DE ELLA, sin\n"
        "          sostenerla. Una sola vela. «Fuera» es el lado por el que entró:\n"
        "          cerrar más allá del borde exterior ya tiene nombre en el proyecto\n"
        "          —es la ROTURA de la fase 2.1— y una rotura es lo contrario de un\n"
        "          rechazo.\n\n"
        "  Forma B · TURTLE SOUP EN H4\n"
        "          Dos velas de H4 consecutivas: la segunda llega a la mecha de la\n"
        "          primera y cierra sin superarla, y eso ocurre EN LA ZONA. Misma\n"
        "          definición que el turtle soup de H1, aplicada a H4: se llama al\n"
        "          mismo detector, no se reimplementa.\n\n"
        "  SIN PARÁMETROS. Ninguna de las dos lleva umbrales, percentiles ni\n"
        "  proporciones.\n\n"
        "⚠️ ES LA PRIMERA VEZ QUE UNA OPERACIÓN VA EN CONTRA DEL ID DE H4. En un ID\n"
        "alcista el rechazo del UL produce una VENTA. La dirección deja de ser una\n"
        "propiedad del impulso y viaja en la observación hasta el stop y el objetivo;\n"
        "la sección 5 aísla esa población entera.\n\n"
        "EL RETESTEO DEL OB NO SE IMPLEMENTA. El propietario lo ha aparcado a\n"
        "propósito. Queda como PENDIENTE CONOCIDO: hoy el OB roto mata la observación\n"
        "y no abre ninguna rama.\n\n"
        "NO SE HA TOCADO nada más: ni el ID, ni las zonas UL/OB, ni la rotura por\n"
        "zona, ni la ejecución, ni el objetivo. No hay FVG. No se ha optimizado nada:\n"
        "no se ha corrido ni una barrida buscando umbrales que mejoren el resultado.\n\n"
        "H1 SIGUE CONFIRMANDO POR DOS VÍAS Y SÓLO DOS (fase 3.1, sin cambios):\n\n"
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
        "El juego de la 3.1 sigue implementado bajo `ENTRY_MODE = v31_contacto`,\n"
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
        section("1.1 · §6.5 · ⚠️ DISTRIBUCIÓN DEL 1R en USD, en ATR y en % del precio", 2),
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
        section("1.3 · El mismo 1R de la fase 3.1, para comparar", 2),
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
        section("2. §6.1 · Embudo completo, antes y después"),
        "",
        "De cuántos contactos de zona a cuántas operaciones, y dónde se cae cada una.",
        "",
        "DOS PASOS SUBEN EN VEZ DE BAJAR, y no es un error de recuento: una misma zona",
        "tocada produce hasta DOS observaciones —la de rechazo y la de rotura y",
        "retesteo, que son momentos distintos— y una misma señal produce hasta DOS",
        "operaciones, una por variante de stop. El embudo es la cascada, no una",
        "partición, y se deja como sale.",
        "",
        "`rechazos_en_h4` es el paso NUEVO de esta fase y es donde muere la rama que",
        "la 3.1 operaba por contacto. Con `ENTRY_MODE = v31_contacto` vale 0 porque en",
        "ese modo no se busca ningún rechazo; poner ahí el número de contactos daría a",
        "entender que sí.",
        "",
        render_table(metrics.funnel(cascade, execution), formats=_FUNNEL),
    ]
    if previous is not None:
        body += [
            "",
            section("2.1 · El mismo embudo, 3.1 contra 3.2", 2),
            "",
            "Los dos primeros pasos —zonas de H4 y zonas tocadas— son IDÉNTICOS por",
            "construcción: la 3.2 no toca ni el ID, ni las zonas, ni la rotura por zona.",
            "Si alguno se moviera sería un bug, y el comando para antes de llegar aquí.",
            "A partir de `observaciones` la caída es el efecto de la fase.",
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
        section("2.2 · Observaciones que confirmaban en la 3.1 y ahora mueren", 2),
        "",
        f"{len(lost):,} observaciones confirmaban en la 3.1 y en la 3.2 ya no. Se",
        "cuentan por la vía que las confirmaba antes y por el guardarraíl en el que",
        "mueren ahora. El detalle completo, una fila por observación, está en",
        "`confirmaciones_perdidas.csv`.",
        "",
        render_table(comparison.lost_table(lost), formats=_LOST),
        "",
        "        Y las mismas, por RAMA de la 3.1:",
        "",
        render_table(comparison.lost_by_branch(lost), formats=_LOST),
        "",
    ]
    if previous is not None:
        gained = comparison.gained_confirmations(previous.cascade, cascade)
        body += [
            "Y AL REVÉS: puede haber observaciones que la 3.2 confirme y la 3.1 no. No",
            "es una contradicción: la ventana de búsqueda en H1 de una rama de rechazo",
            "arranca en el cierre de la vela que rechazó, no en el del contacto, y la",
            "dirección que se busca es otra. Lo que sale de una ventana distinta en una",
            "dirección distinta no tiene por qué ser un subconjunto.",
            "",
            f"  {len(lost):,} confirmaciones perdidas · {len(gained):,} ganadas · "
            f"saldo {len(gained) - len(lost):+,}",
            "",
        ]
    body += [
        section("2.3 · En qué guardarraíl muere cada señal descartada", 2),
        "",
        "Cuenta MOTIVOS, no zonas: una zona puede aportar dos, uno por su observación",
        "de rechazo y otro por su rama de rotura y retesteo.",
        "",
        "`contacto_sin_desenlace` es el guardarraíl NUEVO: el precio tocó la zona y la",
        "observación se apagó sin rechazo ni rotura con retesteo. Es donde va a parar",
        "la rama que la 3.1 operaba a ciegas.",
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


# --- §6.2 Las tres ramas ------------------------------------------------------


def _branches(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    """⚠️ La tabla más importante de la fase: cada rama medida por separado."""
    body = [
        section("3. §6.2 · ⚠️ OPERACIONES POR RAMA, CADA UNA POR SEPARADO"),
        "",
        "Las tres ramas que la 3.2 deja vivas NO son la misma estrategia con tres",
        "etiquetas, y por eso no se promedian:",
        "",
        "  UL rechazo ............... el precio sube al techo del impulso, lo rechaza",
        "                             y se opera EN CONTRA del ID de H4. Población",
        "                             NUEVA del proyecto.",
        "  UL rotura_y_retesteo ..... el precio rompe el techo y vuelve a testearlo;",
        "                             se opera a favor de la rotura. Ya existía.",
        "  OB rechazo ............... el precio baja al origen de la pierna, lo",
        "                             rechaza y se opera a favor del ID.",
        "",
        "`UL respeto` y `OB respeto` sólo pueden aparecer con `ENTRY_MODE =",
        "v31_contacto`: son la rama que esta fase elimina. Si salieran aquí con el modo",
        "de la 3.2, sería un bug.",
        "",
        "El intervalo de confianza es normal-asintótico y aproximado: con menos de ~30",
        "operaciones no significa gran cosa, y está para que se vea cuándo una rama es",
        "demasiado pequeña para decir nada de ella.",
        "",
        render_table(metrics.by_branch(trades), formats=_METRICS),
    ]
    if not before.empty:
        body += [
            "",
            section("3.1 · Las mismas ramas en la fase 3.1", 2),
            "",
            "Ahí es donde se ve el tamaño de lo que se elimina: la rama `UL respeto` de",
            "la 3.1 era más de la mitad del backtest.",
            "",
            render_table(metrics.by_branch(before), formats=_METRICS),
            "",
            "        3.2 contra 3.1:",
            "",
            render_table(
                metrics.side_by_side(metrics.by_branch(trades), metrics.by_branch(before)),
                formats=_COMPARED,
            ),
        ]
    return "\n".join(body) + "\n"


# --- §6.3 Las dos formas del rechazo ------------------------------------------


def _rejection_forms(trades: pd.DataFrame) -> str:
    body = [
        section("4. §6.3 · Rechazos en H4 por forma (A y B)"),
        "",
        "  Forma A · la vela entra en la zona y CIERRA FUERA, por el lado por el que",
        "            entró. Una sola vela.",
        "  Forma B · turtle soup de H4: dos velas consecutivas, la segunda llega a la",
        "            mecha de la primera y cierra sin superarla, EN la zona.",
        "",
        "VALE LA QUE OCURRA PRIMERO. Se evalúan las dos SIEMPRE, aunque la primera ya",
        "rechace: en la 3.0 la evaluación se cortaba al primer acierto y esa",
        "información hubo que reconstruirla a mano después.",
        "",
        "Cuando las dos caen en la MISMA vela la decisión es idéntica —misma vela,",
        "misma dirección, mismo instante— así que cuál se apunte como disparo es",
        "COSMÉTICO. La fila `las dos a la vez` es la que lo mide.",
        "",
        "`disponible` y `disparo` no suman al total: una vela puede estar en las dos",
        "filas de arriba a la vez, y ésa es la pregunta que el §6.3 hace.",
        "",
        render_table(metrics.rejection_form_counts(trades), formats=_FORMS),
        "",
        section("4.1 · Expectativa de cada forma", 2),
        "",
        "Las poblaciones SE SOLAPAN y no reparten el total. Se presentan y no se",
        "interpretan: aquí no se recomienda una forma sobre la otra.",
        "",
        render_table(metrics.by_rejection_form(trades), formats=_METRICS),
    ]
    return "\n".join(body) + "\n"


# --- §6.4 Las operaciones EN CONTRA del ID ------------------------------------


def _against(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("5. §6.4 · ⚠️ OPERACIONES EN CONTRA DEL ID DE H4"),
        "",
        "Es una POBLACIÓN NUEVA del proyecto. Hasta la fase 3.1 ninguna operación iba",
        "contra el sesgo de H4: en un ID alcista se compraba siempre. Con el UL",
        "rechazado se VENDE en un ID alcista, y hay que verla aislada antes de mezclarla",
        "con nada.",
        "",
        "La dirección la fija el rechazo y no el impulso: el UL se atraviesa a favor",
        "del ID, así que rechazarlo es irse al otro lado. Es la misma regla que ya",
        "decidía por dónde se rompe cada zona, leída del revés.",
        "",
        render_table(metrics.against_the_id(trades), formats=_METRICS),
    ]
    if not before.empty:
        body += [
            "",
            section("5.1 · La misma tabla en la fase 3.1", 2),
            "",
            "En la 3.1 la fila de «en contra» tiene que salir vacía o no salir: esa",
            "población no existía. Es la comprobación de que la columna dice lo que",
            "promete.",
            "",
            render_table(metrics.against_the_id(before), formats=_METRICS),
        ]
    return "\n".join(body) + "\n"


# --- §6.7 El hueco de fin de semana -------------------------------------------


def _weekend(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("9. §6.7 · ⚠️ HUECO DE FIN DE SEMANA entre decisión y ejecución"),
        "",
        "En el análisis de la 3.1 salieron 49 confirmaciones DECIDIDAS CON VELAS DEL",
        "VIERNES Y EJECUTADAS EN LA REAPERTURA DEL DOMINGO, tras un hueco de unas 50",
        "horas. Daban -0,399 R (turtle) y -0,761 R (OB) frente a -0,079 R y -0,185 R",
        "del resto.",
        "",
        "ESTO NO SE CORRIGE. Es una decisión del propietario, no una limpieza técnica.",
        "Lo único que hace esta fase es MARCAR cada operación y REPORTAR el grupo por",
        "separado, aquí y en el desglose 5.10 de la sección 7.",
        "",
        "La marca NO lleva umbral de horas: no pregunta cuánto tardó, pregunta si",
        "queda un SÁBADO entre el cierre que decide y el open de M1 que ejecuta. El",
        "sábado es el único día en que el mercado no cotiza en ningún momento, así que",
        "su presencia en el intervalo es exactamente «el mercado cerró y volvió a",
        "abrir», sin elegir ninguna cifra.",
        "",
        render_table(metrics.weekend_gap(trades), formats=_METRICS),
        "",
        section("9.1 · El mismo corte por vía de confirmación y por rama", 2),
        "",
        render_table(metrics.weekend_gap_by_via(trades), formats=_GAP),
    ]
    if not before.empty:
        body += [
            "",
            section("9.2 · El mismo hueco en la fase 3.1", 2),
            "",
            render_table(metrics.weekend_gap(before), formats=_METRICS),
        ]
    return "\n".join(body) + "\n"


# --- §5.2 Las dos vías --------------------------------------------------------


def _vias(
    cascade: CascadeRun, trades: pd.DataFrame, priority: PriorityEffect | None
) -> str:
    body = [
        section("6. §5.2 de la 3.1 · Confirmaciones por vía"),
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
            section("6.1 · ¿Importa el orden? (CONFIRM_PRIORITY)", 2),
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
        section("6.2 · Resultados de cada vía por separado", 2),
        "",
        "Expectativa neta y bruta, win rate e intervalo de confianza de cada vía. El",
        "intervalo es normal-asintótico y aproximado: con menos de ~30 operaciones no",
        "significa gran cosa, y está para que se vea cuándo la población es demasiado",
        "pequeña para decir nada.",
        "",
        render_table(metrics.by_via(trades), formats=_METRICS),
        "",
        section("6.3 · Turtle soup de H1 detectados dentro de una observación", 2),
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
        section("7. §6 · Resultados · SIEMPRE en R, netos y brutos, con TODOS los desgloses"),
        "",
        "La diferencia entre el bruto y el neto es lo que diagnostica si un efecto es",
        "real o aritmética de costes. Por eso van los dos y nunca uno solo.",
        "",
        "El intervalo de confianza es normal-asintótico y aproximado: con menos de",
        "~30 operaciones no significa gran cosa, y está justamente para que se vea",
        "cuándo la población es demasiado pequeña para decir nada.",
        "",
        "Cada desglose va dos veces: la tabla completa de la 3.2 y, debajo, las mismas",
        "poblaciones con la columna de la 3.1 al lado. Una población que existía en la",
        "3.1 y ya no existe sale con la columna de la 3.2 vacía: es un dato, y en esta",
        "fase es EL dato: `UL respeto` desaparece entera.",
        "",
        section("7.1 · Las configuraciones (entrada, stop) medidas por separado", 2),
        "",
        "La combinación *entrada en H1 con stop de M15* NO aparece y no es un olvido:",
        "la zona de M15 se forma DESPUÉS de decidir la entrada de H1, así que su stop",
        "no se puede leer en el instante de decidir. Se declara en vez de rellenarse.",
        "",
        render_table(metrics.configurations(execution), formats=_METRICS),
    ]
    # Los títulos ya vienen numerados con el desglose que pedía la fase 3.0
    # (5.1, 5.2, ...): se dejan tal cual para que quien lea el informe con el
    # enunciado al lado encuentre cada desglose donde lo pidió. Los dos de la
    # fase 3.2 —dirección frente al ID y hueco de fin de semana— se numeran a
    # continuación en vez de abrir una serie nueva, por lo mismo.
    for title, table in metrics.all_breakdowns(trades):
        body += ["", section(title, 2), "", render_table(table, formats=_METRICS)]
        if before.empty:
            continue
        twin = _same_breakdown(title, before)
        if twin is None:
            continue
        body += [
            "",
            "        3.2 contra 3.1:",
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
        section("8. §6.6 · Coste por operación en R, por rama, por vía y por stop"),
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
        section("8.1 · El mismo coste en la fase 3.1", 2),
        "",
        render_table(metrics.cost_by_via_and_stop(before), formats=_COSTS),
    ]
    return "\n".join(body) + "\n"


# --- §5.6 Largos y cortos, por año -------------------------------------------


def _direction_by_year(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("10. §6.8 · ⚠️ LARGOS Y CORTOS POR AÑO, POR SEPARADO"),
        "",
        "ESTA TABLA ES IMPRESCINDIBLE. Sin ella no se puede distinguir si los cortos",
        "fallan por el setup o porque el oro subió de ~1.200 a ~4.300 USD durante todo",
        "el histórico. En la fase 3.0 los largos daban +0,010 R y los cortos -0,332 R.",
        "",
        "⚠️ En la 3.2 la dirección deja de ser una propiedad del ID de H4: la rama de",
        "UL rechazado opera EN CONTRA. Un corte por dirección ya no es un corte por",
        "sesgo, y por eso la sección 5 va aparte.",
        "",
        "AQUÍ NO SE SACA NINGUNA CONCLUSIÓN Y NO SE PROPONE FILTRAR POR DIRECCIÓN.",
        "Se presenta y decide el propietario.",
        "",
        render_table(metrics.by_year_and_direction(trades), formats=_METRICS),
        "",
        section("10.1 · La misma tabla en la fase 3.1", 2),
        "",
        render_table(metrics.by_year_and_direction(before), formats=_METRICS),
    ]
    return "\n".join(body) + "\n"


# --- §5.7 Frecuencia ----------------------------------------------------------


def _frequency(trades: pd.DataFrame, before: pd.DataFrame) -> str:
    body = [
        section("11. §6.9 · Frecuencia: operaciones por semana y semanas sin señal"),
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
            section("11.1 · La misma frecuencia en la fase 3.1", 2),
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
        section("12. R1, R2 y R3 · YA NO CONFIRMAN NADA · columnas informativas"),
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
        section("12.1 · Cuántas marca cada una sobre las velas que confirmaron", 2),
        "",
        "La diagonal es cuántas marcó cada definición; fuera de la diagonal, cuántas",
        "marcaron las dos a la vez. Las poblaciones se solapan y NO suman al total.",
        "",
        render_table(overlap.reset_index(names="definicion") if not overlap.empty else overlap),
        "",
        section("12.2 · Resultados por definición · SIN VALOR DE DECISIÓN", 2),
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
        section("13. Las auditorías por arquetipo"),
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
            section(f"13.{item.number} · {item.title}", 2),
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
        section("14. Casos límite encontrados y cómo se resolvieron")
        + "\n\n"
        + "\n\n".join(_wrap(case) for case in EDGE_CASES)
        + "\n"
    )


#: Los casos límite que aparecieron al construir la fase y qué se decidió en cada
#: uno. Van en el informe y no sólo en los comentarios del código porque son
#: decisiones, y las decisiones las audita el propietario. Los de la 3.0 y la 3.1
#: siguen vigentes: la 3.2 no ha tocado nada de lo que los produjo. Los de esta
#: fase van primero.
EDGE_CASES: tuple[str, ...] = (
    "«CIERRA FUERA DE LA ZONA» TIENE DOS LADOS, Y SÓLO UNO PODÍA SER EL RECHAZO. Una "
    "vela que entra en la zona puede cerrar fuera por el lado de dentro o por el de "
    "fuera, y el enunciado dice «fuera» sin más. Cerrar más allá del borde EXTERIOR "
    "ya tiene nombre en el proyecto desde la fase 2.1: es la ROTURA, y una rotura es "
    "lo contrario de un rechazo. Así que sólo queda el otro lado: el borde por el que "
    "el precio entró. No es una elección entre dos lecturas, es la única que no choca "
    "con una regla ya escrita; si marcara el otro lado, el mismo cierre sería a la vez "
    "rotura a favor del ID y rechazo en contra, y las dos ramas darían señales "
    "opuestas sobre la misma vela.",
    "LA DIRECCIÓN DE LA OPERACIÓN DEJÓ DE SER LA DEL ID. Con el UL rechazado se opera "
    "EN CONTRA de H4, y eso obligaba a decidir dónde vive esa dirección. Se ha puesto "
    "en la OBSERVACIÓN (`trade_direction`) y se deriva de la zona, no del impulso: es "
    "`break_direction` del revés, la misma regla que ya decidía por dónde se atraviesa "
    "cada zona. Desde ahí la lee todo lo que hay debajo —el turtle soup de H1, el OB "
    "de H1, el OB suelto de M15, el stop, el objetivo y el ejecutor— en un solo punto. "
    "Escribirla dos veces habría dejado un lado sin invertir, y una venta abierta como "
    "compra no la delata ninguna tabla.",
    "«Y ESO OCURRE EN LA ZONA» HABÍA QUE TRADUCIRLO SIN INVENTAR UN CRITERIO NUEVO. La "
    "forma B es un turtle soup de H4 «en la zona», y el proyecto ya tiene una lectura "
    "de estar en una zona: `touches`, el mismo con el que se abre la observación y el "
    "mismo que gobierna la puerta de la confirmación en H1. Se ha reutilizado sobre la "
    "vela que rechaza —la segunda—. Cualquier otra lectura habría sido un criterio que "
    "sólo existiría aquí.",
    "LA VELA QUE ROMPE LA ZONA NO PUEDE ADEMÁS RECHAZARLA. La ventana en la que se "
    "espera el rechazo termina donde termina el ID, pero si el ID murió atravesando "
    "ESA MISMA zona, su última vela queda fuera: en su cierre la zona ya está rota, y "
    "aceptarla sería dar por rechazo el mismo cierre que la fase 2.1 declara rotura.",
    "EL RECHAZO NO EXCLUYE AL DOJI Y LAS TRES DEFINICIONES DEL §2 SÍ. No es una "
    "incoherencia: R1, R2 y R3 excluyen el doji para poder compararse sobre la misma "
    "población, y ahí la exclusión tiene una función. Aquí no hay nada que comparar y "
    "el enunciado no lo menciona, así que un doji que entra en la zona y cierra fuera "
    "rechaza como cualquier otra vela. Añadir la excepción habría sido una regla que "
    "nadie ha dado.",
    "LA FORMA A Y LA FORMA B PUEDEN CAER EN LA MISMA VELA, Y HABÍA QUE ELEGIR UNA "
    "ETIQUETA. La decisión es idéntica por las dos —misma vela, misma dirección, mismo "
    "instante—, así que la elección es COSMÉTICA y no mueve ni una operación. Se apunta "
    "la A, que es la que el enunciado nombra primero, y las dos quedan registradas en "
    "`formas_rechazo_disponibles`. El §6.3 cuenta cuántas veces pasa.",
    "TRAS UN RECHAZO EL PRECIO ESTÁ, POR CONSTRUCCIÓN, FUERA DE LA ZONA. Y la puerta "
    "de la confirmación en H1 sigue pidiendo el precio DENTRO de la zona de H4. No se "
    "ha tocado: el enunciado dice que H1 confirma «con las dos vías ya existentes, sin "
    "cambios», y esa puerta es parte de ellas. La consecuencia es que la confirmación "
    "exige que el precio vuelva a la zona, y se ve en el embudo como una caída grande "
    "en `confirman_en_h1`. Se presenta medida en vez de relajarse.",
    "UNA CONFIRMACIÓN DE H1 EN LA DIRECCIÓN CONTRARIA A LA DEL RECHAZO NO HACE NADA. "
    "No hace falta una regla nueva: las dos vías se buscan A FAVOR de la dirección "
    "esperada, que desde la 3.2 es la del rechazo. Una confirmación del otro lado "
    "simplemente no se busca, así que no aparece; la observación sigue esperando la "
    "suya hasta que se cierra su ventana y muere en `sin_confirmacion_h1`. En la serie "
    "sintética del retesteo se ve las dos cosas a la vez: la rama alcista confirma por "
    "OB de H1 y la rama bajista del rechazo, mirando las mismas velas, no.",
    "EL CONTEXTO DIARIO SE COMPARA CONTRA LA OPERACIÓN, NO CONTRA EL ID DE H4. Hasta "
    "la 3.1 daba igual porque eran lo mismo. Con una operación en contra del ID, "
    "«a favor» tenía que significar a favor de lo que se opera, o la etiqueta estaría "
    "describiendo una operación que no es la que se hizo. El contacto diario que "
    "produjo el contexto viaja igualmente en la señal, con su propia dirección, así "
    "que el corte contrario se puede rehacer desde el CSV sin volver a correr nada.",
    "EL HUECO DE FIN DE SEMANA SE MARCA SIN UMBRAL DE HORAS. Un «más de N horas» sería "
    "un parámetro que nadie ha decidido. Lo que se pregunta es un hecho del calendario "
    "del mercado: si entre el cierre que decide y el open de M1 que ejecuta queda un "
    "SÁBADO, el único día en que el mercado no cotiza en ningún momento. NO SE "
    "CORRIGE: es una decisión del propietario y aquí sólo se marca y se reporta.",
    "EL RETESTEO DEL OB NO ESTÁ IMPLEMENTADO Y ES UN PENDIENTE CONOCIDO. El "
    "propietario lo ha aparcado a propósito. Hoy el OB roto mata la observación y no "
    "abre ninguna rama, exactamente como en la 3.0 y la 3.1. Se deja escrito aquí para "
    "que la ausencia no se lea como un olvido.",
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
    """El `LEEME.txt` de `now/fase32/`: qué es cada fichero de la carpeta."""
    found = archetypes.audit(cascade, execution)
    lines = [
        "FASE 3.2 · EL CONTACTO NO ES SEÑAL · índice de la carpeta",
        "=========================================================",
        "",
        "QUÉ CAMBIA EN ESTA FASE",
        "-----------------------",
        "Tocar una zona de H4 abre OBSERVACIÓN y nada más. Sólo hay operación si",
        "ocurre alguno de estos desenlaces:",
        "",
        "  UL rechazado ............. operación EN CONTRA del ID de H4",
        "  UL roto y retesteado ..... a favor de la rotura (ya estaba)",
        "  OB rechazado ............. a favor del ID",
        "  OB roto .................. SIN operación, la observación muere",
        "",
        "Se elimina la rama «respeto operado a favor del ID por contacto». En la 3.0",
        "eran 2.234 de 3.702 operaciones: comprar cuando el precio sube al techo del",
        "impulso.",
        "",
        "EL RECHAZO SE ESPERA EN H4, no en H1, y fija la DIRECCIÓN de la operación.",
        "Dos formas, sin un solo parámetro, y vale la que ocurra primero:",
        "  A. la vela entra en la zona y CIERRA FUERA, por el lado por el que entró.",
        "  B. turtle soup de H4: dos velas consecutivas, la segunda llega a la mecha",
        "     de la primera y cierra sin superarla, EN la zona.",
        "",
        "⚠️ ES LA PRIMERA VEZ QUE UNA OPERACIÓN VA EN CONTRA DEL ID DE H4. En un ID",
        "alcista, rechazar el UL produce una VENTA. Esa población va aislada en la",
        "sección 5 del informe y con color propio en el explorador.",
        "",
        "EL RETESTEO DEL OB NO SE IMPLEMENTA: el propietario lo ha aparcado a",
        "propósito. Pendiente conocido, no olvido.",
        "",
        "NOTA PARA EL PROPIETARIO: revisa los stops ANTES de mirar los resultados. Si",
        "ves primero qué operaciones ganaron, tu juicio sobre dónde va el stop queda",
        "contaminado. El informe está ordenado para eso: la sección 1 es la de los",
        "stops y va delante de la 7, que es la de los resultados.",
        "",
        "FICHEROS",
        "--------",
        "  reporte_entradas.txt   el informe entero: embudo antes y después, las tres",
        "                         ramas por separado, las operaciones en contra del ID",
        "                         aisladas, el hueco de fin de semana y todos los",
        "                         desgloses con la columna de la 3.1 al lado.",
        "  evidencia_entradas.txt el esperado al lado del obtenido, caso por caso:",
        "                         el día sintético de la 3.2, las excepciones",
        "                         anti-lookahead y las dos regresiones.",
        "  operaciones.csv        una fila por operación, con la rama, la forma del",
        "                         rechazo, las formas disponibles, si va en contra del",
        "                         ID, si hay hueco de fin de semana y las columnas",
        "                         informativas de R1, R2 y R3.",
        "  operaciones_v31.csv    lo mismo con `ENTRY_MODE = v31_contacto`, para poder",
        "                         comparar sin volver a correr nada.",
        "  descartadas.csv        una fila por señal descartada y el guardarraíl que",
        "                         la mató, `contacto_sin_desenlace` incluido.",
        "  confirmaciones_perdidas.csv  una fila por observación que confirmaba en la",
        "                         3.1 y ahora muere, con la vía y la rama de antes y el",
        "                         guardarraíl de ahora.",
        "  explorador_entradas.html  cada operación navegable sobre las cuatro",
        "                         temporalidades, con los rechazos de H4 por forma, las",
        "                         operaciones en contra del ID en color propio y las",
        "                         señales que la 3.1 tomaba y la 3.2 descarta.",
        "",
        "CAPTURAS",
        "--------",
        "  rama_ul_rechazo_NN_*      veinte de la rama de UL rechazado (en contra).",
        "  rama_ul_retesteo_NN_*     veinte de la rama de rotura y retesteo.",
        "  rama_ob_rechazo_NN_*      veinte de la rama de OB rechazado.",
        "  perdida_NN_*              veinte que la 3.1 tomaba y la 3.2 descarta, con",
        "                            el motivo por el que ya no existen.",
        "  finde_NN_*                diez con hueco de fin de semana entre la decisión",
        "                            y la ejecución.",
        "  arquetipo_N_*             los arquetipos, uno por forma.",
        "",
        "Las capturas de cada lote son las PRIMERAS cronológicamente de su clase, no",
        "una selección: elegir 'las mejores' habría convertido la carpeta en un",
        "argumento en vez de en una muestra.",
        "",
        f"{len(lost):,} observaciones confirmaban en la 3.1 y ahora no confirman.",
        "",
        "LOS ARQUETIPOS",
        "--------------",
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
