"""Informe estadístico de las señales de la cascada (fase 3.0). **Sin entradas.**

Dice siempre lo mismo antes de cualquier número: aquí no se abre nada, no hay
stop ni target ni tamaño, y por tanto ninguna cifra de esta hoja es una
rentabilidad. Lo que hay es el recorrido del precio después de cada marca y con
qué frecuencia llegó a recorrerlo antes de que la marca quedara desmentida.

**Aquí no se recomienda nada ni se interpreta ningún resultado.** Se presentan
números; decide el propietario.
"""

from __future__ import annotations

import pandas as pd

from chronos.application.entries.signal_stats import GroupStats, SignalStudy
from chronos.infrastructure.clock import SystemClock
from chronos.infrastructure.reporting.ascii_table import render_table, section

_FORMATS = {
    "n": ",d",
    "muerta": ".1%",
    "MFE (R)": ",.2f",
    "MAE (R)": ",.2f",
    "MFE (ATR)": ",.2f",
    "R (USD)": ",.2f",
    "velas H1 a 1R": ",.0f",
}


def render_signal_stats(study: SignalStudy) -> str:
    """El informe entero en texto plano."""
    if not study.enabled:
        return (
            "ESTADÍSTICA DE SEÑALES — la cascada no se ha podido calcular: "
            "hacen falta las zonas y los dos ID, el de H4 y el de H1.\n"
        )

    lines = [
        "ESTADÍSTICA DE LAS SEÑALES DE LA CASCADA H4 → H1 (fase 3.0)",
        "=" * 60,
        "",
        "ESTO NO SON ENTRADAS. No hay orden, ni stop, ni target, ni tamaño, ni",
        "comisiones, ni curva de capital: ninguna cifra de aquí es una",
        "rentabilidad. Lo que se mide es el recorrido del precio después de cada",
        "marca de la fase 3.0.",
        "",
        f"Generado: {SystemClock().now():%Y-%m-%d %H:%M} UTC",
    ]
    if study.span is not None:
        lines.append(f"Histórico: {study.span[0]:%Y-%m-%d} → {study.span[1]:%Y-%m-%d}")
    lines += [
        f"Tope de observación: {study.cap_bars} velas de H1 desde la señal.",
        "",
        "CÓMO SE MIDE",
        "-" * 12,
        "R  = distancia del precio de la señal al borde EXTERIOR de su zona. Es",
        "     donde el precio ha atravesado la zona entera y la señal deja de",
        "     significar nada. No es un stop: no hay stop.",
        "≥kR= fracción de señales cuya mecha llegó a k veces esa distancia a favor",
        "     ANTES de quedar desmentida.",
        "muerta = fracción desmentida (cierre de H1 más allá del borde exterior)",
        "     sin haber llegado ni a 1R.",
        "MFE/MAE = recorrido máximo a favor / en contra, en R, mediana del grupo.",
        "     El MAE se mide con mechas y puede pasar de 1R sin desmentir nada.",
        "MFE (ATR) = ese mismo recorrido a favor en ATR(14) de H1, que es la vara",
        "     común cuando dos filas miden en R distinto.",
        "La observación empieza en la vela SIGUIENTE a la que dio la señal.",
    ]

    if study.funnel is not None:
        funnel = study.funnel
        lines.append(section("El embudo"))
        rows = pd.DataFrame(
            [
                {"paso": "toques del OB de H4", "marcas": funnel.touches, "de las anteriores": ""},
                {
                    "paso": "vetados por el Diario",
                    "marcas": funnel.vetoed,
                    "de las anteriores": _pct(funnel.vetoed, funnel.touches),
                },
                {
                    "paso": "abren búsqueda en H1",
                    "marcas": funnel.searched,
                    "de las anteriores": _pct(funnel.searched, funnel.touches),
                },
                {
                    "paso": "con OB de H1 confirmado",
                    "marcas": funnel.confirmed,
                    "de las anteriores": _pct(funnel.confirmed, funnel.searched),
                },
                {
                    "paso": "SEÑAL: toque de ese OB de H1",
                    "marcas": funnel.signalled,
                    "de las anteriores": _pct(funnel.signalled, funnel.confirmed),
                },
            ]
        )
        lines.append(render_table(rows, formats={"marcas": ",d"}))
        lines.append(
            f"\nMediana de velas de H1 desde el toque de H4: "
            f"{funnel.bars_to_confirmation:,.0f} hasta la confirmación, "
            f"{funnel.bars_to_signal:,.0f} hasta la señal."
        )

    for block in study.sections:
        lines.append(section(block.title))
        lines.append(block.question)
        lines.append("")
        lines.append(render_table(_frame(block.groups, study.targets), formats=_FORMATS))

    if study.discarded:
        lines.append(section("Descartadas"))
        lines.append(
            "Marcas que no se pudieron medir. Se dicen en vez de esconderse:"
        )
        for reason, count in sorted(study.discarded.items()):
            lines.append(f"  {reason}: {count:,}")

    return "\n".join(lines) + "\n"


def _frame(groups: tuple[GroupStats, ...], targets: tuple[float, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "grupo": group.name,
                "n": group.n,
                **{
                    f"≥{target:g}R": f"{value:.1%}"
                    for target, value in zip(targets, group.hit, strict=True)
                },
                "muerta": group.dead,
                "MFE (R)": group.favor_median,
                "MAE (R)": group.against_median,
                "MFE (ATR)": group.favor_atr_median,
                "R (USD)": group.risk_median,
                "velas H1 a 1R": group.bars_to_first_median,
            }
            for group in groups
        ]
    )


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "n/d"


__all__ = ["render_signal_stats"]
