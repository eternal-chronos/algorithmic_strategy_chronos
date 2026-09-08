"""Informe de la fase 2.0 en texto plano (§7): zonas UL, PUL y APUL.

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
    "impulsos": _COUNT, "con_ul": _COUNT, "con_pul": _COUNT, "con_apul": _COUNT,
    "sin_zona": _COUNT,
    "pct_ul": _PCT, "pct_pul": _PCT, "pct_apul": _PCT, "pct_sin_zona": _PCT,
}
_HEIGHTS = {
    "n": _COUNT,
    **{f"usd_p{value}": ",.4f" for value in HEIGHT_PERCENTILES},
    **{f"atr_p{value}": ",.3f" for value in HEIGHT_PERCENTILES},
    **{f"pct_p{value}": ".4%" for value in HEIGHT_PERCENTILES},
    "altura_cero": _COUNT, "pct_altura_cero": _PCT,
    "extendidas": _COUNT, "pct_extendidas": _PCT,
}
#: Los nombres de cada bloque de altura: número de sección y etiqueta.
_HEIGHT_SECTIONS = {
    ZoneKind.LAST: ("7.3", "UL"),
    ZoneKind.PENULTIMATE: ("7.4", "PUL"),
    ZoneKind.ANTE_PENULTIMATE: ("7.4b", "APUL"),
}
_COMPARISON = {
    "pares": _COUNT, "ul_mediana_atr": ",.3f", "pul_mediana_atr": ",.3f",
    "pul_mayor": _COUNT, "pct_pul_mayor": _PCT, "empates": _COUNT,
    "ul_mayor": _COUNT, "pct_ul_mayor": _PCT,
}
_OVERLAP = {"pares": _COUNT, "se_solapan": _COUNT, "pct_solape": _PCT}
_PROFILE = {
    "pares": _COUNT, "ul_mediana_atr": ",.3f", "pul_mediana_atr": ",.3f",
    "rango_id_atr_mediano": ",.3f", "ul_extendidos": _COUNT, "pct_extendidos": _PCT,
}
_SURVIVAL = {
    "roturas": _COUNT, "con_zona": _COUNT, "sin_zona": _COUNT, "cerro_dentro": _COUNT,
    "pct_sobre_roturas": _PCT, "pct_sobre_las_que_tenian_zona": _PCT,
}
_SURVIVAL_YEAR = {
    "roturas": _COUNT, "favor_dentro": _COUNT, "favor_total": _COUNT,
    "contra_dentro": _COUNT, "contra_total": _COUNT, "contra_sin_pul": _COUNT,
    "sobrevivirian": _COUNT, "pct": _PCT,
}


def render_zone_report(
    run: ImpulseRun, zones: ZonesRun, generated_at: datetime | None = None
) -> str:
    """Devuelve el informe completo de la fase 2.0 en texto plano."""
    if not zones.enabled:
        return (
            "FASE 2.0 · ZONAS UL, PUL Y APUL\n"
            "===============================\n\n"
            "Las zonas están DESACTIVADAS por configuración (`zones.enabled: false`).\n"
            "No se ha calculado ninguna y el módulo 1 sale exactamente como en la fase 1.\n"
        )

    generated_at = generated_at or SystemClock().now()
    blocks = [
        _header(run, zones, generated_at),
        _scope_block(),
        _coverage_block(zones),
        _height_block(zones, ZoneKind.LAST),
        _height_block(zones, ZoneKind.PENULTIMATE),
        _height_block(zones, ZoneKind.ANTE_PENULTIMATE),
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
        "FASE 2.0 · ZONAS UL, PUL Y APUL · SÓLO DETECCIÓN\n"
        "===============================================\n\n"
        f"{body}\n\n"
        f"{counts}\n\n"
        "El `config_hash` es el mismo de la fase 1 y eso es deliberado: las zonas no\n"
        "mueven ni una vela ni un impulso, así que no entran en él. La línea base\n"
        "e27d20d0fa4e — D 401 / H4 2.027 detectados — se conserva con las\n"
        "zonas puestas, y hay un test de regresión que lo comprueba en los dos casos.\n"
    )


def _scope_block() -> str:
    return (
        f"{section('ALCANCE · qué hace y qué no hace esta fase')}\n\n"
        "HACE: calcula las dos zonas de cada impulso publicado y las dibuja.\n"
        "NO HACE: no cambia la detección del impulso dominante, no cambia la regla de\n"
        "rotura —que sigue siendo por línea—, no implementa FVG, y no emite señales,\n"
        "entradas, stops ni targets.\n\n"
        "Las zonas, tal como las definió el propietario. Un ID lleva SIEMPRE UL y, en el\n"
        "lado en contra, UNA sola de las otras dos:\n\n"
        "  UL  el extremo del ID, sobre la vela que fija `precio_extremo`. Va del borde\n"
        "      del CUERPO a la punta de la mecha: ocupa sólo el tramo de mecha y nunca\n"
        "      cubre el cuerpo. Su borde interior es exactamente la línea del extremo.\n"
        "      Si la vela inmediatamente posterior llega más lejos en la misma\n"
        "      dirección, la zona se estira hasta ella: UNA vela de margen, no más.\n\n"
        "  PUL el extremo del ID ANTERIOR, sobre la misma vela que llevaba su UL:\n"
        "      cuando un ID muere y nace el siguiente, el UL viejo pasa a ser el PUL del\n"
        "      nuevo. SIEMPRE, vaya como vaya aquel ID; lo único que cambia es QUÉ TRAMO\n"
        "      de esa vela es la zona, porque la zona es el tramo que MIRA al ID nuevo:\n"
        "        - aquel ID iba AL REVÉS: su mecha apunta al otro lado, así que el PUL\n"
        "          es el CUERPO de la vela, de un borde al otro y sin cubrir mecha;\n"
        "        - aquel ID iba EN EL MISMO SENTIDO —murió por rotura a favor y éste\n"
        "          nació más allá—: su mecha apunta hacia este ID, así que el PUL es esa\n"
        "          MECHA, el UL viejo tal cual.\n"
        "      No se estira a ninguna vela de margen: esa regla es la del extremo recién\n"
        "      fijado. Sólo se quedan sin PUL los primeros ID de cada temporalidad.\n\n"
        " APUL el lado en contra del ID nacido tras una CONSTITUCIÓN ABORTADA. Ahí el ID\n"
        "      anterior va en el mismo sentido que éste, pero no por continuación: en\n"
        "      medio había un ID contrario que iba a nacer y una vela lo mató antes. El\n"
        "      nivel se va a buscar donde estaba: se corre la máquina de ID DENTRO del\n"
        "      retroceso del ID anterior —de su extremo a la vela que lo rompió— y se\n"
        "      toma el extremo del último ID interior contrario. La zona es su MECHA, de\n"
        "      la base del cuerpo a la punta, hacia el lado de la rotura en contra.\n"
        "      SUSTITUYE al PUL: un ID lleva una zona en contra y sólo una.\n\n"
        "`borde_interior` es el que un precio que sale del rango encuentra primero y\n"
        "`borde_exterior` el que tiene que cruzar para dejar la zona atrás. El UL se\n"
        "recorre a favor del impulso y el PUL en contra, que son los dos límites por los\n"
        "que el módulo 1 ya rompe.\n"
    )


# --- 7.1 --------------------------------------------------------------------


def _coverage_block(zones: ZonesRun) -> str:
    lines = [
        section("7.1 · ID CON UL Y CON PUL  ***LA CIFRA QUE MANDA***"),
        "",
        "El UL existe siempre: lo fija la misma vela que fija el extremo, y todo ID",
        "tiene extremo. Se cuenta igualmente porque cualquier cosa distinta del 100 %",
        "sería un fallo del motor y hay que poder verlo.",
        "",
        "El lado en contra tiene TRES estados: `con_pul`, `con_apul` y `sin_zona`. Las",
        "tres columnas suman `impulsos`.",
        "",
        ">>> `pct_sin_zona` ES LA CIFRA MÁS IMPORTANTE DE ESTA FASE. En la fase 2.1 esos",
        ">>> impulsos —y sólo ésos— se romperán POR LÍNEA en el lado en contra, porque",
        ">>> no tienen ninguna zona con la que romper.",
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
        "Un ID se queda SIN ZONA en contra sólo mientras no ha habido ningún ID en el",
        "sentido contrario: el arranque de cada temporalidad y nada más. Una cifra alta",
        "aquí sería un fallo del motor.",
        "",
        "`con_apul` cuenta los ID nacidos tras una CONSTITUCIÓN ABORTADA: el ID contrario",
        "que tenía que dar el nivel iba a nacer y una vela lo mató antes, así que el nivel",
        "se va a buscar dentro del retroceso del ID anterior —el extremo del último ID",
        "interior contrario— en vez de al PUL, que ahí apuntaría al lado a favor. No es",
        "un defecto ni una carencia: es la estructura que el gráfico enseña y el motor no",
        "llegó a apuntar.",
    ]
    return "\n".join(lines) + "\n"


# --- 7.3 y 7.4 --------------------------------------------------------------


def _height_block(zones: ZonesRun, kind: ZoneKind) -> str:
    number, name = _HEIGHT_SECTIONS[kind]
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
    elif kind is ZoneKind.PENULTIMATE:
        lines += [
            "",
            "El PUL no tiene columna de altura cero ni de extensión: cubre la vela entera,",
            "así que sólo sería plano si la vela no tuviera recorrido, y no se extiende",
            "nunca.",
        ]
    else:
        lines += [
            "",
            "El APUL es la MECHA de la vela del extremo del último ID interior del retroceso",
            "anterior, así que puede salir plano —esa vela no dejó mecha por ese lado— y",
            "tampoco se extiende. Sólo lo llevan los ID nacidos tras una constitución",
            "abortada, así que esta tabla tiene pocas filas: si sale vacía es que en el",
            "histórico no cayó ninguna, no que falte el cálculo.",
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
        section("7.5 · ALTURA DEL UL FRENTE A LA DEL PUL"),
        "",
        "Sólo entran los ID que tienen las dos zonas. Se espera que el PUL salga",
        "sistemáticamente mayor, porque es el cuerpo entero de su vela mientras que el",
        "UL ocupa sólo el tramo de mecha. `ul_mayor` cuenta las excepciones.",
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
        "QUÉ SON LAS EXCEPCIONES. Aritméticamente, `UL > PUL` es que la mecha de la vela",
        "del extremo —más el margen, si la zona se extendió— sea más larga que el CUERPO",
        "de la vela del extremo anterior. La tabla de arriba separa las dos poblaciones",
        "para que se vea de qué lado viene la diferencia.",
    ]
    return "\n".join(lines) + "\n"


# --- 7.6 --------------------------------------------------------------------


def _overlap_block(zones: ZonesRun) -> str:
    lines = [
        section("7.6 · SOLAPE ENTRE LA ZONA UL Y LA ZONA PUL DEL MISMO ID"),
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
        "POR QUÉ NO SALE CERO. El UL vive pegado al extremo del ID y el PUL en el",
        "extremo anterior, que son los dos límites opuestos del rango: en un ID con",
        "recorrido no pueden tocarse. La tabla de perfiles enseña de dónde salen los que",
        "sí se tocan sin que haya que creerse nada: son ID tan cortos que su rango cabe",
        "dentro del cuerpo de la vela del extremo anterior. Es la misma población enana",
        "que la fase 1 ya contaba en C.5, mirada desde otro sitio.",
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
        "ancla, y la zona que hay detrás en ese lado es el PUL.",
        "",
        ">>> `sin_zona` son roturas en contra de un ID SIN PUL. Ésas no",
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
            "sin_zona": _COUNT, "apul_heredado": _COUNT,
            "apul_extremo_contrario": _COUNT, "apul_retroceso": _COUNT,
            "contra_doji": _COUNT, "contra_plana": _COUNT,
        }),
        "",
        "",
        "UL DE ALTURA CERO (`ul_altura_cero`). La vela del extremo cerró en su propio",
        "máximo y no dejó mecha. DECISIÓN: se conserva como zona degenerada con los dos",
        "bordes en el mismo precio, que es exactamente la línea del ID. Descartarla diría",
        "que el ID no tiene UL, y sí lo tiene: lo que no tiene es mecha. Con la regla de",
        "la fase 2.1 esa zona se comportaría igual que la línea de la fase 1.",
        "",
        "ID SIN ZONA EN CONTRA (`sin_zona`). No hay ID anterior del que sacar la vela:",
        "el arranque de cada temporalidad. Estado legítimo y registrado: el libro de",
        "zonas distingue «no existe» de «no lo he calculado», y preguntarlo lanza",
        "`LookaheadError` en vez de devolver nada. Es la cifra de 7.1.",
        "",
        "APUL HEREDADO (`apul_heredado`). El ID anterior iba AL REVÉS que éste y su",
        "extremo no queda por detrás sino delante: es el ancla de éste y no un nivel al",
        "que volver. DECISIÓN del propietario: ahí no hay PUL, y el nivel en contra es",
        "el que aquel ID llevaba —los mismos dos precios, leídos desde este lado—. La",
        "cadena puede venir de varios ID atrás; si llega a uno que tampoco tenía zona,",
        "éste se rompe por línea.",
        "",
        "APUL DEL EXTREMO CONTRARIO (`apul_extremo_contrario`). El ID anterior iba al",
        "revés pero su extremo SÍ quedó por detrás del ancla de éste: no murió de un",
        "giro sino por rotura a favor, y el giro lo trajo después una constitución",
        "abortada, así que el ancla se fijó más allá de aquel extremo. DECISIÓN del",
        "propietario: ahí el nivel es el UL de aquel ID —el último extremo que quedó",
        "detrás—, y no la zona que él llevaba, que está mucho más lejos.",
        "",
        "APUL DEL RETROCESO (`apul_retroceso`). El ID anterior iba en el MISMO sentido",
        "pero en medio se abortó una constitución: faltaba el ID contrario que tenía que",
        "dar el nivel, y se fue a buscarlo corriendo la máquina dentro de aquel retroceso.",
        "OJO: ese APUL cae DENTRO del rango del ID nuevo, así que la rotura en contra por",
        "zona se ADELANTA a la de la línea en vez de evitarla. Con la regla del",
        "propietario desde la fase 3.0 ese lado no rompe por zona, así que sólo se dibuja.",
        "",
        "DOJI EN LA ZONA EN CONTRA (`contra_doji`). La vela de esa zona es la que fijó el",
        "extremo de un ID, y el módulo 1 declara el doji neutro: no mueve ningún extremo,",
        "así que la columna sale en cero. Se cuenta igualmente porque cualquier otra",
        "cifra sería un fallo del motor.",
        "",
        "ZONA EN CONTRA DE ALTURA CERO (`contra_plana`). Aquella vela cerró en su propio",
        "extremo y no dejó mecha por ese lado, ni siquiera en el resto de la vida de su",
        "ID. DECISIÓN: la misma que en el UL —se conserva con los dos bordes en el mismo",
        "precio— y entonces el lado en contra se comporta igual que la línea.",
    ]
    return "\n".join(lines) + "\n"


def _edge_counts(zones: ZonesRun) -> pd.DataFrame:
    columns = [
        "temporalidad", "id", "ul_altura_cero", "ul_extendidos", "sin_zona",
        "apul_heredado", "apul_extremo_contrario", "apul_retroceso", "contra_doji",
        "contra_plana",
    ]
    rows = [
        {
            "temporalidad": timeframe,
            "id": len(item.items),
            "ul_altura_cero": sum(1 for zoned in item.items if zoned.last.is_flat),
            "ul_extendidos": sum(1 for zoned in item.items if zoned.last.extended),
            "sin_zona": len(item.without_against_zone),
            "apul_heredado": len(item.with_inherited_ante_penultimate),
            "apul_extremo_contrario": len(item.with_counter_extreme_ante_penultimate),
            "apul_retroceso": len(item.with_ante_penultimate)
            - len(item.with_inherited_ante_penultimate)
            - len(item.with_counter_extreme_ante_penultimate),
            "contra_doji": sum(
                1
                for zoned in item.items
                if zoned.against is not None
                and zoned.against.defining_body is BodyDirection.DOJI
            ),
            "contra_plana": sum(
                1
                for zoned in item.items
                if zoned.against is not None and zoned.against.is_flat
            ),
        }
        for timeframe, item in zones.per_timeframe.items()
    ]
    return pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)


__all__ = ["render_zone_report"]
