"""Informe de la fase 3.0 en texto plano (§5, §6, §9 y §11).

Se lee en un terminal, se pega en un correo y se archiva junto a las capturas, así
que va en ancho fijo y sin colores.

Tres cosas que este informe hace y que no son decorativas:

1. **La portada declara de dónde salió cada lado del precio.** El §4 lo exige
   porque no hay fichero de ask en el proyecto, y un neto leído sin saberlo es un
   neto mal leído.
2. **Los stops van ANTES que los resultados.** El propietario lo pidió así en el
   §10: si ve primero qué operaciones ganaron, su juicio sobre dónde va el stop
   queda contaminado. El orden de las secciones es esa nota convertida en índice.
3. **Ningún número agregado sin su desglose** (§5). Todas las tablas traen la
   población delante y ninguna mezcla poblaciones sin decirlo.

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se presentan
números; decide el propietario.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from chronos.application.entries import archetypes, metrics
from chronos.application.entries.cascade import CascadeRun
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
_FUNNEL = {"n": _COUNT, "pct_sobre_el_anterior": _PCT, "pct_sobre_el_primero": _PCT}
_RAILS = {"n": _COUNT, "pct": _PCT}
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
    generated_at: datetime | None = None,
) -> str:
    """El informe entero. El orden de las secciones es el que pide el §10."""
    generated_at = generated_at or SystemClock().now()
    config = cascade.config
    parts = [
        _cover(run, cascade, execution, provenance, generated_at),
        _regression(regression_ok, regression_note),
        _scope(),
        _decisions(config),
        _stop_first(trades),
        _funnel(cascade, execution),
        _results(trades, execution),
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
        ("Cascada", f"fase 3.0 · hash {cascade.config_hash}"),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    width = max(len(label) for label, _ in rows)
    header = "\n".join(f"{label.ljust(width)}   {value}" for label, value in rows)
    return (
        "FASE 3.0 · ENTRADAS\n"
        "===================\n\n"
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


def _regression(ok: bool, note: str) -> str:
    verdict = "PASA" if ok else "FALLA"
    return (
        section("0. Regresión: con las señales apagadas, la línea base de la fase 2.1")
        + "\n\n"
        "Todo apagable con test. Con `entries.enabled: false` la corrida tiene que\n"
        "reproducir la fase 2.1 exacta: la fase 3 lee la estructura y no la toca.\n"
        "Si estos números se movieran sería un bug de esta fase y no un resultado de\n"
        "la anterior.\n\n"
        f"  VEREDICTO: {verdict}\n"
        f"  {note}\n"
    )


def _scope() -> str:
    return (
        section("Alcance de esta fase")
        + "\n\n"
        "Construye la cascada de entrada y produce la PRIMERA medición de resultados\n"
        "del proyecto. Todo lo anterior era estructura.\n\n"
        "No se ha tocado la detección del ID, ni las zonas UL y OB, ni la regla de\n"
        "rotura por zona. No hay FVG (fase 2.2, aparcada). No se ha optimizado nada:\n"
        "no se ha corrido ni una barrida buscando umbrales que mejoren el resultado,\n"
        "y los parámetros abiertos se exponen abajo sin recomendación.\n\n"
        "La cascada, literal:\n\n"
        "  Diario  contexto opcional. Confirma y permite alargar, NUNCA dispara.\n"
        "          En conflicto con H4, manda H4 y el conflicto se registra.\n"
        "  H4      el motor. El precio toca una zona (OB o UL) del ID vigente y la\n"
        "          zona queda en observación. Dos desenlaces: RESPETO, o ROTURA Y\n"
        "          RETESTEO (sólo el UL; en el OB la rotura invalida siempre).\n"
        "  H1      confirma: ID de H1 en la dirección, OB de H1 en la dirección, o\n"
        "          rechazo. Las tres definiciones de rechazo van sin adoptar.\n"
        "  M15     afina: OB suelto, sin exigir ID de M15.\n"
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


# --- §10: los stops, ANTES de los resultados ---------------------------------


def _stop_first(trades: pd.DataFrame) -> str:
    """§3 y la nota del §10: revisar los stops **antes** de mirar los resultados."""
    body = [
        section("1. LOS STOPS · míralos ANTES de bajar a los resultados"),
        "",
        "Nota del §10 para el propietario: si ves primero qué operaciones ganaron, tu",
        "juicio sobre dónde va el stop queda contaminado. Esta sección va delante por",
        "eso y no por orden alfabético.",
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
        section("1.1 · Distribución del 1R en USD, en ATR y en % del precio, por año", 2),
        "",
        "El control de sanidad que dice si la fórmula del stop aterriza en una banda",
        "operable o produce stops de un dólar que la horquilla se come. Los dólares",
        "solos no comparan 2018 con 2025 —el oro pasó de ~1.200 a ~4.300— así que las",
        "tres unidades son obligatorias. `bajo_una_horquilla` cuenta los 1R menores",
        "que una horquilla entera (VERIFICAR): ahí el desenlace no es fiable.",
        "",
        render_table(metrics.risk_distribution(trades), formats=_RISK),
        "",
        section("1.2 · El mismo 1R por variante de entrada y de stop", 2),
        "",
        render_table(
            metrics.risk_distribution(trades.assign(configuracion=_configuration(trades)), by="configuracion")
            if not trades.empty
            else trades,
            formats=_RISK,
        ),
    ]
    return "\n".join(body) + "\n"


def _configuration(trades: pd.DataFrame) -> pd.Series:
    return trades["entrada_en"].astype(str) + " / stop " + trades["stop_en"].astype(str)


# --- §6: el embudo -----------------------------------------------------------


def _funnel(cascade: CascadeRun, execution: ExecutionRun) -> str:
    body = [
        section("2. Embudo de señales"),
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
        "",
        section("2.1 · En qué guardarraíl muere cada señal descartada", 2),
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


# --- §5 y §6: los resultados -------------------------------------------------


def _results(trades: pd.DataFrame, execution: ExecutionRun) -> str:
    body = [
        section("3. Resultados · SIEMPRE en R, netos y brutos"),
        "",
        "La diferencia entre el bruto y el neto es lo que diagnostica si un efecto es",
        "real o aritmética de costes. Por eso van los dos y nunca uno solo.",
        "",
        "El intervalo de confianza es normal-asintótico y aproximado: con menos de",
        "~30 operaciones no significa gran cosa, y está justamente para que se vea",
        "cuándo la población es demasiado pequeña para decir nada.",
        "",
        section("3.1 · Las configuraciones (entrada, stop) medidas por separado", 2),
        "",
        "La combinación *entrada en H1 con stop de M15* NO aparece y no es un olvido:",
        "la zona de M15 se forma DESPUÉS de decidir la entrada de H1, así que su stop",
        "no se puede leer en el instante de decidir. Es un caso límite del §5.5 y se",
        "declara en vez de rellenarse.",
        "",
        render_table(metrics.configurations(execution), formats=_METRICS),
    ]
    # Los títulos ya vienen numerados con el § del enunciado (5.1, 5.2, ...): se
    # dejan tal cual para que quien lea el informe con el enunciado al lado
    # encuentre cada desglose donde lo pidió.
    for title, table in metrics.all_breakdowns(trades):
        body += ["", section(title, 2), "", render_table(table, formats=_METRICS)]
    if not trades.empty:
        first = pd.Timestamp(trades["ts_entrada"].min())
        last = pd.Timestamp(trades["ts_entrada"].max())
        body += [
            "",
            section("3.9 · Frecuencia: operaciones por semana y semanas sin señal", 2),
            "",
            render_table(metrics.frequency(trades, first, last), formats=_FREQUENCY),
        ]
    return "\n".join(body) + "\n"


# --- §2: las tres definiciones de rechazo ------------------------------------


def _rejections(trades: pd.DataFrame) -> str:
    overlap = metrics.rejection_overlap(trades)
    body = [
        section("4. Las TRES definiciones de rechazo · NINGUNA ADOPTADA"),
        "",
        "El propietario ha delegado la formalización y no ha elegido. Las tres se",
        "implementan, las tres se marcan en el histórico y el motor no toma ninguna",
        "por defecto. La confirmación en H1 usa la UNIÓN de las tres, que es el filtro",
        "más laxo y del que las tres son subconjuntos: así el desglose puede recortar",
        "hacia cada una sin volver a recorrer H1.",
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
        "El doji no rechaza en ninguna de las tres: §2.2 lo declara neutro en todo el",
        "módulo y esa lectura no se rompe aquí.",
        "",
        section("4.1 · Cuántos marca cada una y cuánto se solapan", 2),
        "",
        "La diagonal es cuántas marcó cada definición; fuera de la diagonal, cuántas",
        "marcaron las dos a la vez. Las poblaciones se solapan y NO suman al total.",
        "",
        render_table(overlap.reset_index(names="definicion") if not overlap.empty else overlap),
        "",
        section("4.2 · Resultados por definición (el desglose del §5.8)", 2),
        "",
        render_table(metrics.by_rejection(trades), formats=_METRICS),
    ]
    return "\n".join(body) + "\n"


# --- §9: las cinco auditorías por arquetipo ----------------------------------


def _archetypes(cascade: CascadeRun, execution: ExecutionRun) -> str:
    found = archetypes.audit(cascade, execution)
    body = [
        section("5. Las cinco auditorías por arquetipo"),
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
            section(f"5.{item.number} · {item.title}", 2),
            "",
            f"Criterio: {item.criterion}",
            "",
            *item.narrative(),
            "",
            f"Captura: {item.capture}.png" if item.found else "Captura: no procede.",
        ]
    return "\n".join(body) + "\n"


# --- §11.9: los casos límite -------------------------------------------------


def _edge_cases() -> str:
    return (
        section("6. Casos límite encontrados y cómo se resolvieron")
        + "\n\n"
        + "\n\n".join(_wrap(case) for case in EDGE_CASES)
        + "\n"
    )


#: §11.9 — los casos límite que aparecieron al construir la fase y qué se decidió
#: en cada uno. Van en el informe y no sólo en los comentarios del código porque
#: son decisiones, y las decisiones las audita el propietario.
EDGE_CASES: tuple[str, ...] = (
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
    "ENTRADA EN H1 CON STOP DE M15 ES IMPOSIBLE SIN LOOKAHEAD. El §5.5 pide medir las "
    "dos variantes de stop, pero la zona de M15 se forma DESPUÉS de decidir la entrada "
    "de H1. Se han medido las tres combinaciones que sí existen —H1/H1, M15/M15 y "
    "M15/H1— y la cuarta se declara imposible en vez de rellenarse.",
    "LA VENTANA DE RETESTEO NO ESTÁ ACOTADA EN EL ENUNCIADO. El §1.2 dice que tras "
    "romper el UL el precio 'vuelve a testearla' pero no dice hasta cuándo se espera. "
    "En vez de inventar un número se ha usado la frontera que la máquina de estados ya "
    "tenía escrita —la constitución del ID de H4 siguiente— y se ha dejado el "
    "parámetro abierto con su rejilla, para que lo cierre el propietario.",
    "R1 SOBRE EL UL ES CASI IMPOSIBLE POR GEOMETRÍA. 'La mecha entra en la zona y el "
    "cuerpo cierra fuera' leído a favor de la dirección buscada exige, sobre una zona "
    "que está por encima del precio, cerrar por encima de ella: exactamente romperla. "
    "No se ha corregido ni se ha hecho una excepción; se cuenta, y la tabla 4.1 enseña "
    "cuántas veces marca cada definición.",
    "LA UNIÓN DE LAS TRES DEFINICIONES NO ES UNA DEFINICIÓN ADOPTADA. El §1.3 exige "
    "que un rechazo confirme y el §2 prohíbe elegir cuál. Se usa la unión porque es el "
    "filtro más laxo y las tres son subconjuntos suyos, de modo que el desglose del "
    "§5.8 puede recortar hacia cualquiera de ellas sin volver a recorrer H1. Elegir "
    "una habría decidido lo que el enunciado delega en el propietario.",
    "EL DOJI NO RECHAZA EN NINGUNA DE LAS TRES DEFINICIONES. R2 no tiene proporción "
    "que medir sin cuerpo y R3 no dice nada con el cierre en la apertura. R1 sí podría "
    "evaluarse sobre un doji, y aun así se excluye: si no, las tres definiciones se "
    "compararían sobre poblaciones distintas y la tabla 4.1 no significaría nada.",
    "NO HAY FICHERO DE ASK. El §4 pide longs al ask y shorts al bid, y sólo está "
    "descargado el M1 del lado bid. El comando SE DETIENE por defecto; para correr hay "
    "que autorizarlo explícitamente, y entonces la asunción se declara en portada, en "
    "la salida de la CLI y en el explorador. No se ha fabricado ninguna serie de ask.",
    "EL DESLIZAMIENTO SE COBRA COMO COSTE Y NO DESPLAZANDO EL PRECIO DE EJECUCIÓN. Con "
    "el precio desplazado el bruto deja de valer exactamente -1 R o +3,3 R y la "
    "diferencia bruto/neto deja de leerse, que es justo el diagnóstico que pide el §6.",
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
    cascade: CascadeRun, execution: ExecutionRun, captures: Sequence[str]
) -> str:
    """El `LEEME.txt` de `now/fase30/`: qué es cada fichero de la carpeta."""
    found = archetypes.audit(cascade, execution)
    lines = [
        "FASE 3.0 · ENTRADAS · índice de la carpeta",
        "==========================================",
        "",
        "NOTA PARA EL PROPIETARIO (§10): revisa los stops ANTES de mirar los",
        "resultados. Si ves primero qué operaciones ganaron, tu juicio sobre dónde va",
        "el stop queda contaminado. El informe está ordenado para eso: la sección 1 es",
        "la de los stops y va delante de la 3, que es la de los resultados.",
        "",
        "FICHEROS",
        "--------",
        "  reporte_entradas.txt   el informe entero: embudo, métricas con todos los",
        "                         desgloses, las tres definiciones de rechazo y las",
        "                         cinco auditorías por arquetipo.",
        "  evidencia_entradas.txt el esperado al lado del obtenido, caso por caso:",
        "                         el día sintético del §8 y las cinco excepciones",
        "                         anti-lookahead del §7.",
        "  operaciones.csv        una fila por operación, con todas las columnas que",
        "                         el §5 desglosa.",
        "  descartadas.csv        una fila por señal descartada y el guardarraíl que",
        "                         la mató.",
        "  explorador_entradas.html  cada operación navegable sobre las cuatro",
        "                         temporalidades, con las señales descartadas y los",
        "                         rechazos de las tres definiciones distinguibles.",
        "",
        "CAPTURAS",
        "--------",
        "  ganadora_NN_*    veinte operaciones que alcanzaron el objetivo.",
        "  perdedora_NN_*   veinte operaciones en las que saltó el stop.",
        "  descartada_*     diez señales muertas en cada guardarraíl.",
        "  arquetipo_N_*    los cinco arquetipos del §9, uno por forma.",
        "",
        "Las ganadoras y las perdedoras son las PRIMERAS cronológicamente de cada",
        "clase, no una selección: elegir 'las mejores' habría convertido la carpeta en",
        "un argumento en vez de en una muestra.",
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
