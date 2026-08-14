"""Informe de la fase 2.0 en texto plano (§7): zonas UL y OB.

Se lee junto a las capturas del propietario, así que dice siempre lo mismo antes
de cualquier número: que esta fase **sólo detecta y dibuja**, que ninguna zona ha
tocado la detección de impulsos, y con qué configuración se calculó.

Todo va desglosado por año porque el oro pasó de ~1.200 a ~4.300 USD en el
histórico: ninguna altura en dólares es comparable entre extremos, y por eso
cada una se da además en ATR y en porcentaje del precio.

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se presentan
números; decide el propietario.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from chronos.application.structure import zones as tables
from chronos.application.structure.detect_impulses import ImpulseRun
from chronos.application.structure.zones import HEIGHT_PERCENTILES, ZonesRun
from chronos.domain.structure.enums import BodyDirection
from chronos.domain.structure.zones import ZoneKind
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.ascii_table import render_table, section

_COUNT = ",d"
_PCT = ".1%"

_COVERAGE = {
    "impulsos": _COUNT, "con_ul": _COUNT, "con_ob": _COUNT, "sin_ob": _COUNT,
    "pct_ul": _PCT, "pct_ob": _PCT, "pct_sin_ob": _PCT,
}
_DELAY = {
    "n": _COUNT, "mediana": ",.1f", "p10": ",.1f", "p90": ",.1f", "maximo": ",.0f",
    "mediana_desde_constitucion": ",.1f", "ya_confirmado_al_nacer": _COUNT,
    "pct_ya_confirmado": _PCT,
}
_HEIGHTS = {
    "n": _COUNT,
    **{f"usd_p{value}": ",.4f" for value in HEIGHT_PERCENTILES},
    **{f"atr_p{value}": ",.3f" for value in HEIGHT_PERCENTILES},
    **{f"pct_p{value}": ".4%" for value in HEIGHT_PERCENTILES},
    "altura_cero": _COUNT, "pct_altura_cero": _PCT,
    "extendidas": _COUNT, "pct_extendidas": _PCT,
}
_COMPARISON = {
    "pares": _COUNT, "ul_mediana_atr": ",.3f", "ob_mediana_atr": ",.3f",
    "ob_mayor": _COUNT, "pct_ob_mayor": _PCT, "empates": _COUNT,
    "ul_mayor": _COUNT, "pct_ul_mayor": _PCT,
}
_OVERLAP = {"pares": _COUNT, "se_solapan": _COUNT, "pct_solape": _PCT}
_PROFILE = {
    "pares": _COUNT, "ul_mediana_atr": ",.3f", "ob_mediana_atr": ",.3f",
    "rango_id_atr_mediano": ",.3f", "ul_extendidos": _COUNT, "pct_extendidos": _PCT,
}
_SURVIVAL = {
    "roturas": _COUNT, "con_zona": _COUNT, "sin_zona": _COUNT, "cerro_dentro": _COUNT,
    "pct_sobre_roturas": _PCT, "pct_sobre_las_que_tenian_zona": _PCT,
}
_SURVIVAL_YEAR = {
    "roturas": _COUNT, "favor_dentro": _COUNT, "favor_total": _COUNT,
    "contra_dentro": _COUNT, "contra_total": _COUNT, "contra_sin_ob": _COUNT,
    "sobrevivirian": _COUNT, "pct": _PCT,
}


def render_zone_report(
    run: ImpulseRun, zones: ZonesRun, generated_at: datetime | None = None
) -> str:
    """Devuelve el informe completo de la fase 2.0 en texto plano."""
    if not zones.enabled:
        return (
            "FASE 2.0 · ZONAS UL Y OB\n"
            "========================\n\n"
            "Las zonas están DESACTIVADAS por configuración (`zones.enabled: false`).\n"
            "No se ha calculado ninguna y el módulo 1 sale exactamente como en la fase 1.\n"
        )

    generated_at = generated_at or SystemClock().now()
    blocks = [
        _header(run, zones, generated_at),
        _scope_block(),
        _coverage_block(zones),
        _delay_block(zones),
        _height_block(zones, ZoneKind.LAST),
        _height_block(zones, ZoneKind.ORDER_BLOCK),
        _comparison_block(zones),
        _overlap_block(zones),
        _survival_block(zones),
        _edge_cases_block(zones),
    ]
    return "\n".join(blocks).strip() + "\n"


# --- Cabecera ---------------------------------------------------------------


def _header(run: ImpulseRun, zones: ZonesRun, generated_at: datetime) -> str:
    config = run.config
    rows = [
        ("Símbolo", config.symbol),
        ("Lado del precio (STRUCTURE_SIDE)", config.structure_side),
        ("Origen de los datos", run.provenance or "n/d"),
        ("Temporalidades con zonas", ", ".join(zones.per_timeframe)),
        ("Hash de configuración", zones.config_hash),
        ("Generado", generated_at.strftime("%Y-%m-%d %H:%M:%S")),
    ]
    width = max(len(label) for label, _ in rows) + 2
    body = "\n".join(f"{label:<{width}}{value}" for label, value in rows)
    counts = " · ".join(
        f"{timeframe} {len(item.items):,} ID / {len(item.table):,} zonas"
        for timeframe, item in zones.per_timeframe.items()
    )
    return (
        "FASE 2.0 · ZONAS UL Y OB · SÓLO DETECCIÓN\n"
        "=========================================\n\n"
        f"{body}\n\n"
        f"{counts}\n\n"
        "El `config_hash` es el mismo de la fase 1 y eso es deliberado: las zonas no\n"
        "mueven ni una vela ni un impulso, así que no entran en él. La línea base\n"
        "8e51cd9140c8 — D 401 / H4 1.914 / H1 7.231 detectados — se conserva con las\n"
        "zonas puestas, y hay un test de regresión que lo comprueba en los dos casos.\n"
    )


def _scope_block() -> str:
    return (
        f"{section('ALCANCE · qué hace y qué no hace esta fase')}\n\n"
        "HACE: calcula las dos zonas de cada impulso publicado y las dibuja.\n"
        "NO HACE: no cambia la detección del impulso dominante, no cambia la regla de\n"
        "rotura —que sigue siendo por línea—, no implementa FVG, y no emite señales,\n"
        "entradas, stops ni targets.\n\n"
        "Las dos zonas, tal como las definió el propietario:\n\n"
        "  UL  el extremo del ID, sobre la vela que fija `precio_extremo`. Va del borde\n"
        "      del CUERPO a la punta de la mecha: ocupa sólo el tramo de mecha y nunca\n"
        "      cubre el cuerpo. Su borde interior es exactamente la línea del extremo.\n"
        "      Si la vela inmediatamente posterior llega más lejos en la misma\n"
        "      dirección, la zona se estira hasta ella: UNA vela de margen, no más.\n\n"
        "  OB  la vela donde arranca la pierna, la que fija `precio_ancla`. La vela\n"
        "      ENTERA, de `low` a `high`: a diferencia del UL, sí cubre el cuerpo. No\n"
        "      existe hasta que una vela posterior DEL COLOR DEL IMPULSO la supera\n"
        "      incluyendo mecha.\n\n"
        "`borde_interior` es el que un precio que sale del rango encuentra primero y\n"
        "`borde_exterior` el que tiene que cruzar para dejar la zona atrás. El UL se\n"
        "recorre a favor del impulso y el OB en contra, que son los dos límites por los\n"
        "que el módulo 1 ya rompe.\n"
    )


# --- 7.1 --------------------------------------------------------------------


def _coverage_block(zones: ZonesRun) -> str:
    lines = [
        section("7.1 · ID CON UL Y CON OB CONFIRMADO  ***LA CIFRA QUE MANDA***"),
        "",
        "El UL existe siempre: lo fija la misma vela que fija el extremo, y todo ID",
        "tiene extremo. Se cuenta igualmente porque cualquier cosa distinta del 100 %",
        "sería un fallo del motor y hay que poder verlo.",
        "",
        ">>> `pct_sin_ob` ES LA CIFRA MÁS IMPORTANTE DE ESTA FASE. En la fase 2.1 esos",
        ">>> impulsos se romperán POR LÍNEA, porque no tienen zona con la que romper.",
        "",
    ]
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.coverage_by_year(item), formats=_COVERAGE),
            "",
        ]
    lines += [
        "",
        "Un ID se queda sin OB cuando ninguna vela del color del impulso llega a superar",
        "la mecha de la vela del ancla mientras el ID está vigente. Ocurre cuando esa",
        "vela tiene una mecha muy larga en la dirección del impulso: el ancla sale de su",
        "cuerpo, pero la confirmación exige superar su mecha.",
    ]
    return "\n".join(lines) + "\n"


# --- 7.2 --------------------------------------------------------------------


def _delay_block(zones: ZonesRun) -> str:
    lines = [
        section("7.2 · BARRAS QUE TARDA EL OB EN CONFIRMARSE"),
        "",
        "`mediana`, `p10`, `p90` y `maximo` se cuentan desde la VELA DEL OB, que es",
        "cuando empieza la espera, y por eso nunca bajan de 1.",
        "",
        "`mediana_desde_constitucion` cuenta desde el nacimiento del ID y puede salir",
        "NEGATIVA: significa que el OB ya estaba confirmado cuando el ID nació, porque",
        "la vela que lo confirma suele ser la que arranca la pierna. `ya_confirmado_al",
        "_nacer` cuenta cuántos están en ese caso.",
        "",
    ]
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.confirmation_delay(item), formats=_DELAY),
            "",
        ]
    return "\n".join(lines) + "\n"


# --- 7.3 y 7.4 --------------------------------------------------------------


def _height_block(zones: ZonesRun, kind: ZoneKind) -> str:
    number, name = ("7.3", "UL") if kind is ZoneKind.LAST else ("7.4", "OB")
    lines = [
        section(f"{number} · ALTURA DE LA ZONA {name} EN USD, ATR Y % DEL PRECIO"),
        "",
        "Los percentiles van en las tres unidades. Las columnas `usd_*` no son",
        "comparables entre años; las `atr_*` y `pct_*` sí, y para eso están.",
    ]
    if kind is ZoneKind.LAST:
        lines += [
            "",
            "`altura_cero` cuenta los UL cuya vela del extremo cerró en su propio máximo",
            "(o mínimo, en bajista) y no dejó mecha. Se conservan como zona degenerada —los",
            "dos bordes en el mismo precio— en vez de descartarlos: el ID sí tiene UL, lo",
            "que no tiene es mecha. Se cuentan sobre la zona YA EXTENDIDA, así que un UL sin",
            "mecha al que la vela de margen le dio altura no aparece aquí.",
            "",
            "`extendidas` cuenta los que se estiraron a la vela siguiente.",
        ]
    else:
        lines += [
            "",
            "El OB no tiene columna de altura cero ni de extensión: cubre la vela entera,",
            "así que sólo sería plano si la vela no tuviera recorrido, y no se extiende",
            "nunca.",
        ]
    lines.append("")
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.heights_by_year(item, kind), formats=_HEIGHTS),
            "",
        ]
    return "\n".join(lines) + "\n"


# --- 7.5 --------------------------------------------------------------------


def _comparison_block(zones: ZonesRun) -> str:
    lines = [
        section("7.5 · ALTURA DEL UL FRENTE A LA DEL OB"),
        "",
        "Sólo entran los ID que tienen las dos zonas. Se espera que el OB salga",
        "sistemáticamente mayor, porque incluye el cuerpo de su vela mientras que el UL",
        "ocupa sólo el tramo de mecha. `ul_mayor` cuenta las excepciones.",
        "",
    ]
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.height_comparison(item), formats=_COMPARISON),
            "",
            "De dónde viene la diferencia en las excepciones:",
            "",
            render_table(tables.exception_profile(item), formats=_PROFILE),
            "",
        ]
    lines += [
        "",
        "QUÉ SON LAS EXCEPCIONES. Aritméticamente, `UL > OB` es que la mecha de la vela",
        "del extremo —más el margen, si la zona se extendió— sea más larga que TODA la",
        "vela del ancla. La tabla de arriba separa las dos poblaciones para que se vea de",
        "qué lado viene la diferencia. La extensión del UL apenas la explica: su",
        "porcentaje se mueve poco entre una población y otra. Lo que cambia de verdad es",
        "que en las excepciones el UL es mucho más alto de lo normal Y el OB, más bajo:",
        "una vela de extremo con mechazo contra una vela de ancla pequeña.",
    ]
    return "\n".join(lines) + "\n"


# --- 7.6 --------------------------------------------------------------------


def _overlap_block(zones: ZonesRun) -> str:
    lines = [
        section("7.6 · SOLAPE ENTRE LA ZONA UL Y LA ZONA OB DEL MISMO ID"),
        "",
        "Se cuenta como solape que los dos intervalos de precio se toquen.",
        "",
    ]
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.overlaps(item), formats=_OVERLAP),
            "",
            "En qué se diferencian los que se solapan:",
            "",
            render_table(tables.overlap_profile(item), formats=_PROFILE),
            "",
        ]
    lines += [
        "",
        "POR QUÉ NO SALE CERO. El UL vive pegado al extremo y el OB a la vela del ancla,",
        "que son los dos límites opuestos del rango: en un ID con recorrido no pueden",
        "tocarse. La tabla de perfiles enseña de dónde salen los que sí se tocan sin que",
        "haya que creerse nada — su rango de ID es unas tres veces menor que el de los",
        "demás, mientras que las dos alturas son las de siempre. Son ID tan cortos que su",
        "rango cabe dentro de la vela del ancla, así que la mecha del OB alcanza el",
        "extremo. Es la misma población enana que la fase 1 ya contaba en C.5, mirada",
        "desde otro sitio.",
    ]
    return "\n".join(lines) + "\n"


# --- 7.7 --------------------------------------------------------------------


def _survival_block(zones: ZonesRun) -> str:
    lines = [
        section("7.7 · ANTICIPO DE LA FASE 2.1  ***SÓLO MEDICIÓN***"),
        "",
        "PREGUNTA: de las roturas que ya ocurrieron en este histórico, ¿cuántas cerraron",
        "DENTRO de la zona que les correspondía sin atravesarla entera? Esos ID habrían",
        "sobrevivido en el instante de su rotura con la regla de la fase 2.1.",
        "",
        "NO ES UNA RE-EJECUCIÓN. Cambiar la regla cambiaría toda la historia posterior a",
        "la primera rotura salvada —otros extremos, otras anclas, otra numeración— y eso",
        "no se simula aquí. Es una estimación descriptiva sobre los datos existentes, tal",
        "como se pidió.",
        "",
        "Qué zona le toca a cada rotura no es una elección: la rotura A FAVOR cruza el",
        "extremo, y la zona pegada al extremo es el UL; la rotura EN CONTRA cruza el",
        "ancla, y la zona de la vela del ancla es el OB.",
        "",
        ">>> `sin_zona` son roturas en contra de un ID SIN OB CONFIRMADO. Ésas no",
        ">>> sobreviven: sin zona, la fase 2.1 las rompe por línea. No es lo mismo que",
        ">>> cerrar fuera de la zona, y por eso van en su propia columna.",
        "",
    ]
    for timeframe, item in zones.per_timeframe.items():
        lines += [
            "",
            f"--- {timeframe} ---",
            "",
            render_table(tables.survival_estimate(item), formats=_SURVIVAL),
            "",
            "Por año:",
            "",
            render_table(tables.survival_by_year(item), formats=_SURVIVAL_YEAR),
            "",
        ]
    return "\n".join(lines) + "\n"


# --- Casos límite -----------------------------------------------------------


def _edge_cases_block(zones: ZonesRun) -> str:
    """Los bordes que el enunciado mandaba documentar y contar, con su recuento."""
    lines = [
        section("CASOS LÍMITE · lo que el enunciado mandaba documentar y contar"),
        "",
        render_table(_edge_counts(zones), formats={
            "id": _COUNT, "ul_altura_cero": _COUNT, "ul_extendidos": _COUNT,
            "sin_ob": _COUNT, "ob_doji": _COUNT, "ob_confirma_al_constituir": _COUNT,
            "ob_ya_confirmado_al_nacer": _COUNT,
        }),
        "",
        "",
        "UL DE ALTURA CERO (`ul_altura_cero`). La vela del extremo cerró en su propio",
        "máximo y no dejó mecha. DECISIÓN: se conserva como zona degenerada con los dos",
        "bordes en el mismo precio, que es exactamente la línea del ID. Descartarla diría",
        "que el ID no tiene UL, y sí lo tiene: lo que no tiene es mecha. Con la regla de",
        "la fase 2.1 esa zona se comportaría igual que la línea de la fase 1.",
        "",
        "ID QUE MUERE SIN OB CONFIRMADO (`sin_ob`). Estado legítimo y registrado: el",
        "libro de zonas distingue «no existe» de «no lo he calculado», y preguntarlo",
        "lanza `LookaheadError` en vez de devolver nada. Es la cifra de 7.1.",
        "",
        "LA VELA QUE CONFIRMA ES LA QUE CONSTITUYE (`ob_confirma_al_constituir`).",
        "IMPOSIBLE POR CONSTRUCCIÓN, y por eso la columna sale en cero. La vela que",
        "constituye un ID es la primera CONTRARIA a la pierna, y la dirección del ID es",
        "la de la pierna: en un ID alcista constituye una vela roja. La confirmación del",
        "OB exige una vela DEL COLOR DEL IMPULSO, verde en un ID alcista. Las dos",
        "condiciones se excluyen. No se ha inventado una lectura alternativa: se ha",
        "implementado la regla literal y se cuenta el resultado. Hay un test que fija la",
        "imposibilidad para que salte si alguien cambia la regla.",
        "",
        "VARIAS VELAS PODRÍAN CONFIRMAR. Se toma la primera cronológicamente, como pide",
        "el enunciado.",
        "",
        "DOJI EN POSICIÓN DE OB (`ob_doji`). DECISIÓN: el doji es un OB perfectamente",
        "válido —la zona es la vela entera y la regla sólo mira el color de QUIEN la",
        "supera, no el suyo— pero con el `ANCHOR_MODE = A1` del proyecto no puede",
        "ocurrir, y por eso la columna sale en cero: A1 busca la última vela CONTRARIA",
        "previa a la pierna, y `is_counter_to` no considera contrario a un doji. Con A2",
        "sí ocurre, porque el ancla es la primera vela de la pierna y los dojis no cortan",
        "la racha. El día sintético lo construye en las dos lecturas.",
        "",
        "EL OB YA CONFIRMADO AL NACER EL ID (`ob_ya_confirmado_al_nacer`). No es un caso",
        "límite sino el caso corriente, y conviene tenerlo a la vista al leer 7.2: la",
        "vela que confirma suele caer dentro de la pierna, antes de la constitución. La",
        "zona no nace ahí de todos modos: durante el limbo no existe ninguna zona, así",
        "que el OB nace con el ID.",
    ]
    return "\n".join(lines) + "\n"


def _edge_counts(zones: ZonesRun) -> pd.DataFrame:
    columns = [
        "temporalidad", "id", "ul_altura_cero", "ul_extendidos", "sin_ob", "ob_doji",
        "ob_confirma_al_constituir", "ob_ya_confirmado_al_nacer",
    ]
    rows = [
        {
            "temporalidad": timeframe,
            "id": len(item.items),
            "ul_altura_cero": sum(1 for zoned in item.items if zoned.last.is_flat),
            "ul_extendidos": sum(1 for zoned in item.items if zoned.last.extended),
            "sin_ob": len(item.without_order_block),
            "ob_doji": sum(
                1
                for zoned in item.items
                if zoned.order_block is not None
                and zoned.order_block.defining_body is BodyDirection.DOJI
            ),
            "ob_confirma_al_constituir": sum(
                1
                for zoned in item.items
                if zoned.order_block is not None
                and zoned.order_block.index_confirmation == zoned.index_constitution
            ),
            "ob_ya_confirmado_al_nacer": sum(
                1
                for zoned in item.items
                if zoned.bars_to_confirmation is not None and zoned.bars_to_confirmation <= 0
            ),
        }
        for timeframe, item in zones.per_timeframe.items()
    ]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


__all__ = ["render_zone_report"]
