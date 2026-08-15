"""⚠️ Las TRES definiciones candidatas de rechazo (§2). **Ninguna adoptada.**

El propietario ha delegado la formalización y **no ha elegido**. Este módulo
implementa las tres, las marca todas sobre cada vela y no toma ninguna por
defecto: quien las combine tiene que decir cuál usa. Se decidirá mirando
capturas, no mirando expectativas.

    R1_mecha_en_zona_cierre_fuera  la mecha entra en la zona y el cuerpo cierra
                                   fuera. Sin parámetros. Es la lógica de la
                                   rotura por zona en pequeño.
    R2_mecha_dominante             la mecha contra el movimiento supera el
                                   percentil P de la proporción mecha/cuerpo de
                                   esa temporalidad, sobre sesiones anteriores.
    R3_cierre_en_extremo           el cierre queda en el tercio favorable del
                                   rango de la vela.

**El doji no rechaza en ninguna de las tres.** §2.2 lo declara neutro en todo el
módulo y esa lectura no se rompe aquí: un cuerpo de tamaño cero no tiene "mecha
contra el cuerpo" que medir (R2), y en R3 su cierre coincide con su apertura sin
decir nada sobre quién ganó la vela. R1 sí podría evaluarse sobre un doji —sólo
mira mecha y cierre— y aun así se excluye, para que las tres definiciones se
comparen sobre la misma población y la comparación del §2 signifique algo.

**Causalidad.** R1 y R3 se leen de la propia vela ya cerrada. R2 necesita una
distribución, y esa distribución sólo puede mirar **sesiones anteriores**: el
umbral vigente en la sesión de hoy se calcula con las velas de ayer hacia atrás
y nunca con las de hoy. Pedirlo más allá de la frontera declarada lanza
`LookaheadError` en vez de devolver un número.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chronos.domain.entries.enums import RejectionKind
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.zones import CandleSeries, Zone

#: Rejilla de `RECHAZO_PERCENTIL` que exige el §2. El motor no elige ninguno.
REJECTION_PERCENTILES: tuple[int, ...] = (60, 75, 90)

#: R3 parte el rango de la vela en tres. No es un parámetro: la definición del
#: propietario dice "tercio" y se escribe una vez, aquí.
_THIRD = 1.0 / 3.0


def wick_against(series: CandleSeries, index: int, direction: ImpulseDirection) -> float:
    """Mecha contraria al movimiento buscado.

    En un rechazo alcista es la mecha **inferior** —lo que el precio perdió y
    recuperó dentro de la misma vela— y en uno bajista, la superior.
    """
    body_low = float(min(series.open[index], series.close[index]))
    body_high = float(max(series.open[index], series.close[index]))
    if direction is ImpulseDirection.ALCISTA:
        return body_low - float(series.low[index])
    return float(series.high[index]) - body_high


def body_size(series: CandleSeries, index: int) -> float:
    return abs(float(series.close[index]) - float(series.open[index]))


def wick_body_ratios(series: CandleSeries, direction: ImpulseDirection) -> np.ndarray:
    """Proporción mecha-contraria / cuerpo de toda la serie, vela a vela.

    `NaN` en los doji: sin cuerpo no hay proporción, y forzar un cuerpo mínimo
    inventado metería en la distribución un número que nadie ha decidido.
    """
    body = np.abs(series.close - series.open)
    body_low = np.minimum(series.open, series.close)
    body_high = np.maximum(series.open, series.close)
    wick = (
        body_low - series.low
        if direction is ImpulseDirection.ALCISTA
        else series.high - body_high
    )
    ratios = np.full(len(series), np.nan, dtype=float)
    alive = body > 0.0
    ratios[alive] = wick[alive] / body[alive]
    return ratios


class RollingWickPercentile:
    """Umbral de `R2` vigente en cada vela, calculado sobre sesiones anteriores.

    Una sesión entera entra en la distribución **cuando ha terminado**. Dentro de
    una sesión el umbral no se mueve: si se recalculara vela a vela, la vela que
    se está juzgando acabaría entrando en su propio umbral por la puerta de atrás
    en cuanto la ventana avanzara un paso.

    Mientras no haya ninguna sesión anterior con velas medibles el umbral es
    `NaN` y `R2` no marca nada. Es lo correcto: no hay distribución con la que
    comparar, y rellenarla con la sesión en curso sería exactamente el lookahead
    que esta clase existe para impedir.
    """

    def __init__(
        self,
        series: CandleSeries,
        session_id: np.ndarray,
        *,
        direction: ImpulseDirection,
        percentiles: tuple[int, ...] = REJECTION_PERCENTILES,
        label: str = "",
    ) -> None:
        if len(session_id) != len(series):
            raise StructureError(
                f"Las sesiones no cubren la serie: {len(session_id)} frente a {len(series)}"
            )
        if np.any(np.diff(session_id) < 0):
            raise StructureError("Las sesiones tienen que ir en orden cronológico")
        for percentile in percentiles:
            if not 0 < percentile < 100:
                raise StructureError(f"Percentil fuera de (0, 100): {percentile}")

        self._percentiles = tuple(percentiles)
        self._label = label or f"R2 {direction.value}"
        self._frontier = -1
        self._thresholds = _expanding_percentiles(
            wick_body_ratios(series, direction), session_id, self._percentiles
        )

    def __len__(self) -> int:
        return int(self._thresholds.shape[0])

    @property
    def percentiles(self) -> tuple[int, ...]:
        return self._percentiles

    @property
    def frontier(self) -> int:
        return int(self._frontier)

    def advance(self, index: int) -> None:
        """Declara que la vela `index` ya ha cerrado."""
        if index < self._frontier:
            raise StructureError("La frontera de una serie causal no retrocede")
        if index >= len(self):
            raise StructureError(f"Índice {index} fuera de la serie ({len(self)} velas)")
        self._frontier = index

    def at(self, index: int, percentile: int) -> float:
        """Umbral vigente en `index`; `NaN` mientras no haya sesiones anteriores."""
        if index < 0 or index >= len(self):
            raise StructureError(f"Índice {index} fuera de la serie ({len(self)} velas)")
        if index > self._frontier:
            raise LookaheadError(
                f"{self._label}: se pidió el umbral de la vela {index} con la serie "
                f"procesada hasta {self._frontier}"
            )
        try:
            column = self._percentiles.index(percentile)
        except ValueError:
            raise StructureError(
                f"Percentil {percentile} no calculado. Disponibles: {self._percentiles}"
            ) from None
        return float(self._thresholds[index, column])


def _expanding_percentiles(
    ratios: np.ndarray, session_id: np.ndarray, percentiles: tuple[int, ...]
) -> np.ndarray:
    """Percentiles de `ratios` acumulados hasta la sesión **anterior** a cada vela."""
    thresholds = np.full((ratios.size, len(percentiles)), np.nan, dtype=float)
    if ratios.size == 0:
        return thresholds

    starts = np.flatnonzero(np.diff(session_id, prepend=session_id[0] - 1) != 0)
    edges = [*starts.tolist(), ratios.size]
    accumulated: list[np.ndarray] = []
    pool = np.empty(0, dtype=float)
    for position in range(len(edges) - 1):
        first, last = edges[position], edges[position + 1]
        if pool.size:
            thresholds[first:last] = np.percentile(pool, percentiles)
        session = ratios[first:last]
        finite = session[np.isfinite(session)]
        if finite.size:
            accumulated.append(finite)
            pool = np.concatenate(accumulated)
    return thresholds


# --- Las tres definiciones ---------------------------------------------------


def rejects_r1(
    series: CandleSeries, index: int, direction: ImpulseDirection, zone: Zone
) -> bool:
    """R1 — la mecha entra en la zona y el cuerpo cierra fuera.

    "Fuera" se lee **a favor de la dirección buscada**, que es lo que exige el
    §1.3: un rechazo que cierra fuera por el lado contrario no confirma nada. En
    un rechazo alcista la vela mete la mecha en la zona y cierra por encima de su
    borde superior; en uno bajista, al revés.

    Sobre una zona que está **por encima** del precio buscado —el UL de un ID
    alcista— la definición es casi imposible de cumplir: cerrar por encima de esa
    zona es exactamente romperla. No se corrige aquí ni se hace una excepción; se
    cuenta, y el §2 del informe enseña cuántas veces marca cada definición.
    """
    if body_size(series, index) == 0.0:
        return False
    close = float(series.close[index])
    if direction is ImpulseDirection.ALCISTA:
        touches = float(series.low[index]) <= zone.high and float(series.high[index]) >= zone.low
        return touches and close > zone.high
    touches = float(series.high[index]) >= zone.low and float(series.low[index]) <= zone.high
    return touches and close < zone.low


def rejects_r2(
    series: CandleSeries,
    index: int,
    direction: ImpulseDirection,
    threshold: float,
) -> bool:
    """R2 — la mecha contra el movimiento supera el umbral rodante.

    `threshold` lo da `RollingWickPercentile`, que sólo mira sesiones anteriores.
    Con `NaN` —todavía no hay distribución— no marca: no se rellena el hueco.
    """
    body = body_size(series, index)
    if body == 0.0 or not np.isfinite(threshold):
        return False
    return wick_against(series, index, direction) / body > threshold


def rejects_r3(series: CandleSeries, index: int, direction: ImpulseDirection) -> bool:
    """R3 — el cierre queda en el tercio favorable del rango de la vela.

    El rango se mide con mechas, de `low` a `high`. Una vela sin rango —los
    cuatro precios iguales— no rechaza: no hay tercios que repartir.
    """
    if body_size(series, index) == 0.0:
        return False
    low = float(series.low[index])
    high = float(series.high[index])
    span = high - low
    if span <= 0.0:
        return False
    close = float(series.close[index])
    if direction is ImpulseDirection.ALCISTA:
        return close >= high - _THIRD * span
    return close <= low + _THIRD * span


@dataclass(frozen=True, slots=True)
class RejectionMarks:
    """Qué definiciones marcaron esta vela. Las tres, siempre; ninguna elegida.

    `r2` es un diccionario por percentil y no un booleano porque la rejilla
    {60, 75, 90} está abierta: colapsarla a un valor obligaría a elegir uno.
    """

    index: int
    direction: ImpulseDirection
    r1: bool
    r2: dict[int, bool]
    r3: bool

    @property
    def any_r2(self) -> bool:
        return any(self.r2.values())

    @property
    def any_kind(self) -> bool:
        """`True` si alguna de las tres marcó. **No es una definición adoptada**:
        es el filtro más laxo posible, y existe para que el embudo pueda contar
        una población de la que las tres son subconjuntos."""
        return self.r1 or self.any_r2 or self.r3

    def marked(self, kind: RejectionKind, percentile: int) -> bool:
        if kind is RejectionKind.R1_MECHA_EN_ZONA:
            return self.r1
        if kind is RejectionKind.R3_CIERRE_EN_EXTREMO:
            return self.r3
        return self.r2.get(percentile, False)

    def kinds(self, percentile: int) -> tuple[RejectionKind, ...]:
        marked = []
        if self.r1:
            marked.append(RejectionKind.R1_MECHA_EN_ZONA)
        if self.r2.get(percentile, False):
            marked.append(RejectionKind.R2_MECHA_DOMINANTE)
        if self.r3:
            marked.append(RejectionKind.R3_CIERRE_EN_EXTREMO)
        return tuple(marked)


def mark_rejections(
    series: CandleSeries,
    index: int,
    direction: ImpulseDirection,
    zone: Zone,
    thresholds: dict[int, float],
) -> RejectionMarks:
    """Evalúa las tres definiciones sobre la misma vela. Ninguna gana."""
    return RejectionMarks(
        index=index,
        direction=direction,
        r1=rejects_r1(series, index, direction, zone),
        r2={
            percentile: rejects_r2(series, index, direction, threshold)
            for percentile, threshold in thresholds.items()
        },
        r3=rejects_r3(series, index, direction),
    )


__all__ = [
    "REJECTION_PERCENTILES",
    "RejectionMarks",
    "RollingWickPercentile",
    "body_size",
    "mark_rejections",
    "rejects_r1",
    "rejects_r2",
    "rejects_r3",
    "wick_against",
    "wick_body_ratios",
]
