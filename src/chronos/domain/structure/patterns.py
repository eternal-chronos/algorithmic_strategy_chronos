"""El OB y el FVG que se marcan DENTRO del ID (K.1).

Regla del propietario, dictada el 2026-09-12 en su versión simple —los detalles
finos llegan mirando el dibujo—:

- **Sólo dentro del ID.** En el Diario se marcan los OB y FVG que caen dentro
  del ID del Diario; en H4, los que caen dentro del ID de H4. Nunca un patrón
  fuera del ID de su temporalidad.
- **H1 no tiene ID**: sus OB y FVG se marcan dentro del ID de H4. Ni pasados ni
  usados.
- **Los OB que arrancan donde arranca el ID son los importantes.** Muchos de los
  que se marcan nacen en la vela contraria previa a la pierna que construyó el
  ID —la vela de la que sale el ancla A1—, así que la ventana del ID empieza en
  esa vela, no en su constitución.

"Dentro del ID" se lee así, y sólo así:

- **dirección**: la del ID. En un ID alcista se buscan OB y FVG alcistas;
- **tiempo**: desde la vela previa al arranque de la pierna hasta la vela que
  mata al ID, ambas incluidas. El patrón tiene que haberse **sabido** ahí dentro;
- **precio**: la zona del patrón **solapa** el rango `[ancla, extremo]`. Solapa,
  no cabe entera: el OB del arranque asoma por debajo del ancla y es justo el
  que más importa.

Un patrón que cae en la ventana de dos ID consecutivos —la pierna del segundo
arranca mientras el primero vive— es del **último** que lo reclama: es el OB
"donde arranca el ID", y ése manda.

**Usado**: el precio ha vuelto a entrar en la zona después de que el patrón se
supiera. Se mira sólo mientras el ID vive; lo que pase después no es de este ID.
Un patrón usado sigue en la tabla con la vela que lo usó: quien dibuje decide
hasta dónde lo enseña.

Geometría de los dos patrones, sobre la vela ENTERA —mecha incluida—, que son
sitios a los que el precio vuelve y el precio vuelve a donde llegó:

- **OB** — la última vela contraria antes de que el precio se vaya. Alcista: una
  vela **bajista** y, dentro de las `displacement` velas siguientes, un cierre
  por encima de su máximo. La zona es esa vela, `[low, high]`, y se sabe en la
  vela que cierra más allá, no antes.
- **FVG** — el hueco de tres velas. Alcista: `low[i+1] > high[i-1]`. La zona es
  el hueco, `[high[i-1], low[i+1]]`, y se sabe al cerrar `i+1`.

Función pura sobre DataFrames: sin reloj, sin ficheros, sin red.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.impulse import DominantImpulse

#: Columnas del resultado, en su orden. Una fila por patrón.
PATTERN_COLUMNS = (
    "timeframe",
    "id_timeframe",
    "id_num",
    "direccion",
    "tipo",
    "ts_origen",
    "ts_conocido",
    "ts_usado",
    "ts_fin",
    "precio_bajo",
    "precio_alto",
    "usado",
    "vivo",
    "indice_origen",
    "indice_conocido",
    "indice_usado",
)


class PatternKind(StrEnum):
    """Los dos patrones. Los valores salen al CSV y al explorador."""

    ORDER_BLOCK = "OB"
    FAIR_VALUE_GAP = "FVG"


@dataclass(frozen=True, slots=True)
class PatternRule:
    """Lo único que la geometría no fija sola."""

    #: Velas que se le dan al precio para irse tras el OB. Con 1 el OB exige que
    #: la vela siguiente ya cierre al otro lado. El propietario no lo ha cerrado.
    displacement: int = 3

    def __post_init__(self) -> None:
        if self.displacement < 1:
            raise DomainError(f"El desplazamiento del OB no puede ser {self.displacement}")

    def describe(self) -> str:
        return (
            "dentro del ID: en su dirección, solapando su rango, desde la vela previa "
            "a la pierna hasta la que lo mata · OB: vela contraria y cierre más allá "
            f"de su extremo en {self.displacement} velas, zona = la vela entera · "
            "FVG: hueco de tres velas · usado: el precio vuelve a entrar en la zona"
        )


DEFAULT_RULE = PatternRule()


def patterns_inside(
    bars: pd.DataFrame,
    *,
    timeframe: str,
    impulses: Sequence[DominantImpulse],
    id_bars: pd.DataFrame,
    rule: PatternRule = DEFAULT_RULE,
) -> pd.DataFrame:
    """Los OB y FVG de `bars` (velas de `timeframe`) dentro de cada `impulse`.

    `id_bars` son las velas de la temporalidad del ID: sus índices (`index_leg_start`,
    `index_end`) se traducen ahí a tiempo y el tiempo es lo que acota `bars`, que
    pueden ser las mismas velas (Diario en Diario, H4 en H4) o más finas (H1
    dentro del ID de H4). Los impulsos se reclaman del último al primero.
    """
    _check_bars(bars, "los patrones")
    _check_bars(id_bars, "el ID")
    if bars.empty or id_bars.empty or not impulses:
        return _empty()

    stamps = _utc(bars.index)
    id_stamps = _utc(id_bars.index)
    # `datetime64` sin zona para buscar; los índices, para escribir fechas.
    ticks = stamps.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    id_ticks = id_stamps.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    open_ = bars["open"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)

    found = {
        direction: _candidates(open_, high, low, close, direction, rule.displacement)
        for direction in ImpulseDirection
    }
    claimed = {direction: np.zeros(len(found[direction].origin), dtype=bool) for direction in found}

    rows: list[dict[str, object]] = []
    for impulse in sorted(impulses, key=lambda item: item.index_constitution, reverse=True):
        first, last_exclusive = _window(impulse, id_ticks, ticks)
        if last_exclusive <= first:
            continue
        candidates = found[impulse.direction]
        lower, upper = sorted((impulse.anchor, impulse.extreme))
        inside = (
            (candidates.origin >= first)
            & (candidates.known < last_exclusive)
            & (candidates.low <= upper)
            & (candidates.high >= lower)
            & ~claimed[impulse.direction]
        )
        claimed[impulse.direction] |= inside
        for position in np.flatnonzero(inside):
            origin = int(candidates.origin[position])
            known = int(candidates.known[position])
            zone_low = float(candidates.low[position])
            zone_high = float(candidates.high[position])
            used = _first_use(
                high, low, impulse.direction, zone_low, zone_high, known + 1, last_exclusive
            )
            alive = impulse.index_end is None and used is None
            end: pd.Timestamp | None = None
            if used is not None:
                end = stamps[used]
            elif impulse.index_end is not None:
                end = id_stamps[impulse.index_end]
            rows.append(
                {
                    "timeframe": timeframe,
                    "id_timeframe": impulse.timeframe,
                    "id_num": impulse.id_num,
                    "direccion": impulse.direction.value,
                    "tipo": str(candidates.kind[position]),
                    "ts_origen": stamps[origin],
                    "ts_conocido": stamps[known],
                    "ts_usado": stamps[used] if used is not None else pd.NaT,
                    "ts_fin": end if end is not None else pd.NaT,
                    "precio_bajo": zone_low,
                    "precio_alto": zone_high,
                    "usado": used is not None,
                    "vivo": alive,
                    "indice_origen": origin,
                    "indice_conocido": known,
                    "indice_usado": used,
                }
            )

    if not rows:
        return _empty()
    frame = pd.DataFrame(rows, columns=list(PATTERN_COLUMNS))
    frame["indice_usado"] = frame["indice_usado"].astype("Int64")
    return frame.sort_values(["ts_conocido", "ts_origen", "tipo"]).reset_index(drop=True)


# --- Geometría ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidates:
    """Todos los OB y FVG de una serie en una dirección, como arrays paralelos."""

    kind: np.ndarray
    origin: np.ndarray
    known: np.ndarray
    low: np.ndarray
    high: np.ndarray


def _candidates(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: ImpulseDirection,
    displacement: int,
) -> _Candidates:
    blocks = _order_blocks(open_, high, low, close, direction, displacement)
    gaps = _fair_value_gaps(high, low, direction)
    return _Candidates(
        kind=np.concatenate(
            [
                np.full(len(blocks[0]), PatternKind.ORDER_BLOCK.value, dtype=object),
                np.full(len(gaps[0]), PatternKind.FAIR_VALUE_GAP.value, dtype=object),
            ]
        ),
        origin=np.concatenate([blocks[0], gaps[0]]),
        known=np.concatenate([blocks[1], gaps[1]]),
        low=np.concatenate([blocks[2], gaps[2]]),
        high=np.concatenate([blocks[3], gaps[3]]),
    )


def _order_blocks(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: ImpulseDirection,
    displacement: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origen, conocido, bajo, alto) de cada OB en `direction`.

    Alcista: vela con cuerpo **bajista** y, en las `displacement` velas
    siguientes, un cierre por encima de su máximo. El doji no cuenta como vela
    contraria: no tiene dirección, igual que en el módulo 1.
    """
    bullish = direction is ImpulseDirection.ALCISTA
    # La vela del OB va en contra del ID: roja en un ID alcista.
    candidates = close < open_ if bullish else close > open_
    total = len(close)
    # La primera de las `displacement` velas siguientes que cierra al otro lado,
    # buscada de una vez para todas las velas: un desplazamiento por pasada, no
    # una pasada por vela. `-1` es "no se fue".
    known = np.full(total, -1, dtype=np.int64)
    level = high if bullish else low
    for step in range(1, displacement + 1):
        if step >= total:
            break
        shifted = close[step:]
        gone = shifted > level[:-step] if bullish else shifted < level[:-step]
        pending = gone & (known[:-step] < 0) & candidates[:-step]
        known[:-step][pending] = np.flatnonzero(pending) + step
    origin = np.flatnonzero(known >= 0)
    return origin, known[origin], low[origin], high[origin]


def _fair_value_gaps(
    high: np.ndarray, low: np.ndarray, direction: ImpulseDirection
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origen, conocido, bajo, alto) de cada FVG en `direction`.

    Alcista: `low[i+1] > high[i-1]`, el hueco que dejó un tramo alcista. El
    hueco tiene que ser estricto: dos velas que se tocan exactamente en el mismo
    precio no dejan hueco, igual que "más allá" es estricto en la rotura.
    """
    empty = np.zeros(0, dtype=np.int64)
    if len(high) < 3:
        return empty, empty, empty.astype(float), empty.astype(float)
    if direction is ImpulseDirection.ALCISTA:
        gap_low, gap_high = high[:-2], low[2:]
    else:
        gap_low, gap_high = high[2:], low[:-2]
    offset = np.flatnonzero(gap_low < gap_high)
    return offset + 1, offset + 2, gap_low[offset], gap_high[offset]


def _first_use(
    high: np.ndarray,
    low: np.ndarray,
    direction: ImpulseDirection,
    zone_low: float,
    zone_high: float,
    start: int,
    stop: int,
) -> int | None:
    """Primera vela de `[start, stop)` que vuelve a entrar en la zona.

    En un patrón alcista el precio está por encima y vuelve bajando: entra
    cuando su mínimo toca el techo de la zona. En uno bajista, al revés.
    """
    if stop <= start:
        return None
    if direction is ImpulseDirection.ALCISTA:
        hits = np.flatnonzero(low[start:stop] <= zone_high)
    else:
        hits = np.flatnonzero(high[start:stop] >= zone_low)
    return start + int(hits[0]) if hits.size else None


# --- Ventana ------------------------------------------------------------------


def _window(
    impulse: DominantImpulse, id_ticks: np.ndarray, ticks: np.ndarray
) -> tuple[int, int]:
    """Posiciones `[first, last_exclusive)` de `stamps` que caen dentro del ID.

    Empieza en la vela previa al arranque de la pierna —la del ancla A1, de la
    que sale el OB "donde arranca el ID"— y termina con la vela que mata al ID:
    todo lo que va etiquetado antes de la vela SIGUIENTE a ésa está dentro. Con
    el ID vivo, hasta el final de las velas.
    """
    start = id_ticks[max(impulse.index_leg_start - 1, 0)]
    first = int(np.searchsorted(ticks, start, side="left"))
    if impulse.index_end is None:
        return first, len(ticks)
    following = impulse.index_end + 1
    if following >= len(id_ticks):
        return first, len(ticks)
    return first, int(np.searchsorted(ticks, id_ticks[following], side="left"))


def _utc(index: pd.Index) -> pd.DatetimeIndex:
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is None:
        raise DomainError("Los patrones exigen un índice tz-aware en UTC")
    return stamps.tz_convert("UTC")


def _check_bars(frame: pd.DataFrame, what: str) -> None:
    missing = {"open", "high", "low", "close"} - set(frame.columns)
    if missing:
        raise DomainError(f"A las velas de {what} les faltan columnas: {sorted(missing)}")


def _empty() -> pd.DataFrame:
    frame = pd.DataFrame(columns=list(PATTERN_COLUMNS))
    return frame.astype({"indice_usado": "Int64"})
