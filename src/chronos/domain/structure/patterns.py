"""El OB y el FVG que se marcan DENTRO del ID de H4 (K.2).

Regla del propietario, dictada el 2026-09-15 sobre sus capturas de H4. Se marca
sólo en H4 y sólo lo que cae dentro del ID; cuando él vea que se marca bien se
pasa a lo siguiente.

**Cuándo se marca.** No en cada vela: en tres cortes al día. El día de la
plataforma arranca a las 17:00 de Nueva York y sus velas de H4 son la 1.ª (17),
la 2.ª (21), la 3.ª (01), la 4.ª (05), la 5.ª (09) y la 6.ª (13). Se marca al
CERRAR la 2.ª, la 3.ª y la 4.ª —«usamos dos velas de H4 para empezar a marcar»—,
que en el reloj de cTrader (UTC-4 fijo) son las 02:00, 06:00 y 10:00 de la
mañana en invierno y las 01:00, 05:00 y 09:00 en verano. Después de la 4.ª no
se busca más: a las 11:30 se deja de buscar operaciones. Un patrón que se forma
entre dos cortes se marca en el siguiente; uno que se forma y se rompe entre
dos cortes no se marca nunca. Quién es la 2.ª vela del día no lo sabe este
módulo: llega en `marking`, una máscara sobre las velas que dice en cuáles se
marca, calculada fuera con el ancla de sesión de la configuración.

**Qué se marca.** En cada corte, con el ID vigente en ese cierre:

- lo que ya se sabe —el OB confirmado, el hueco cerrado— y no está marcado;
- formado desde la vela del ancla del ID (la contraria previa a la pierna, de
  la que sale el OB «donde arranca el ID»);
- que CABE ENTERO entre el ancla y el extremo, en cualquiera de las dos
  direcciones: dentro de un ID alcista se marca el OB alcista del arranque y
  también el bajista que deja la vela del extremo;
- y, si es un FVG, que deja recorrido: del borde por el que el precio entra en
  el hueco al extremo del ID tiene que quedar al menos `min_fvg_room` del rango.
  El hueco «muy grande y muy encima del precio» no se marca: no lo va a
  respetar del todo, no permite recorrido.

Un patrón ya marcado sigue marcado en los cortes siguientes; si muere puede
volver a marcarse bajo otro ID en el que quepa.

**Geometría.** Con el CUERPO, no con las mechas, salvo el hueco del FVG:

- **OB** — la última vela contraria antes de que el precio se vaya. Alcista: una
  vela BAJISTA y, después, un cierre por encima de su máximo (la mecha, no el
  cuerpo): «hasta que no rompa la mecha no lo consideramos OB». Que la mecha de
  otra vela pase por debajo no cuenta; tiene que ser el cierre. Si antes de
  ese cierre llega otra vela bajista, es ésa la última contraria y la anterior
  deja de ser candidata. La zona es el cuerpo, `[min(open, close), max(open,
  close)]`, y se sabe en la vela que cierra más allá.
- **FVG** — el hueco de tres velas. Alcista: `low[i+1] > high[i-1]`, estricto.
  La zona es el hueco, y se sabe al cerrar `i+1`.

**Cuándo se desmarca.** «En el momento que se rompe»: la primera vela de H4
que CIERRA con el cuerpo más allá del borde lejano de la zona —por debajo de un
OB alcista, por encima de uno bajista— lo mata, en cualquier vela, no sólo en
los cortes. Y con su ID: cuando el ID muere, lo marcado dentro de él muere con
él. Ninguna de las dos cosas la dictó el propietario para el FVG ni para la
muerte del ID; se aplican en espejo y quedan como supuesto hasta que lo vea.

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

#: Columnas del resultado, en su orden. Una fila por marcado: el mismo OB
#: marcado bajo dos ID distintos son dos filas.
PATTERN_COLUMNS = (
    "timeframe",
    "id_timeframe",
    "id_num",
    "direccion",
    "tipo",
    "ts_origen",
    "ts_conocido",
    "ts_marcado",
    "ts_fin",
    "motivo_fin",
    "precio_bajo",
    "precio_alto",
    "vivo",
    "indice_origen",
    "indice_conocido",
    "indice_marcado",
    "indice_fin",
)


class PatternKind(StrEnum):
    """Los dos patrones. Los valores salen al CSV y al explorador."""

    ORDER_BLOCK = "OB"
    FAIR_VALUE_GAP = "FVG"


class PatternEnd(StrEnum):
    """Por qué se desmarcó un patrón."""

    #: Un cierre de H4 atravesó la zona por el lado contrario al de entrada.
    ROTURA = "ROTURA"
    #: El ID dentro del que se marcó murió, y lo marcado dentro de él con él.
    MUERTE_DEL_ID = "MUERTE_DEL_ID"


@dataclass(frozen=True, slots=True)
class PatternRule:
    """Lo único que la geometría no fija sola."""

    #: Posiciones, dentro del día de sesión, de las velas de H4 en cuyo CIERRE
    #: se marca: la 1.ª es la 0. Con `(1, 2, 3)` se marca al cerrar la 2.ª, la
    #: 3.ª y la 4.ª vela del día (02:00, 06:00 y 10:00 de cTrader en invierno;
    #: 01:00, 05:00 y 09:00 en verano). Lo dictó el propietario.
    marking_slots: tuple[int, ...] = (1, 2, 3)
    #: Recorrido mínimo que tiene que dejar un FVG, como fracción del rango del
    #: ID: del borde por el que el precio entra en el hueco al extremo del ID.
    #: Un FVG que deja menos no se marca. Con 0 se marcan todos. Parámetro
    #: abierto: «muy grande» lo afina el propietario mirando el dibujo.
    min_fvg_room: float = 0.5

    def __post_init__(self) -> None:
        if not self.marking_slots:
            raise DomainError("Hace falta al menos un corte del día en el que marcar")
        if any(slot < 0 for slot in self.marking_slots):
            raise DomainError(f"Las posiciones del día no pueden ser negativas: {self.marking_slots}")
        if not 0.0 <= self.min_fvg_room <= 1.0:
            raise DomainError(
                f"El recorrido mínimo del FVG es una fracción del ID: {self.min_fvg_room}"
            )

    def marks_at(self, slots: np.ndarray) -> np.ndarray:
        """Máscara de las velas en cuyo cierre se marca, dada la posición de cada
        una dentro de su día de sesión."""
        return np.isin(np.asarray(slots), self.marking_slots)

    def describe(self) -> str:
        ordinal = ", ".join(f"{slot + 1}.ª" for slot in self.marking_slots)
        return (
            f"se marca al cerrar la {ordinal} vela de H4 del día de sesión · dentro del ID: "
            "cabe entero entre ancla y extremo, en cualquiera de las dos direcciones, "
            "desde la vela del ancla · OB: cuerpo de la última vela contraria antes de "
            "que un cierre rompa su mecha · FVG: hueco de tres velas, sólo si deja al "
            f"menos el {self.min_fvg_room:.0%} del ID de recorrido hasta el extremo · "
            "se desmarca cuando un cierre lo atraviesa o cuando muere su ID"
        )


DEFAULT_RULE = PatternRule()


def patterns_inside(
    bars: pd.DataFrame,
    *,
    timeframe: str,
    impulses: Sequence[DominantImpulse],
    marking: np.ndarray,
    rule: PatternRule = DEFAULT_RULE,
) -> pd.DataFrame:
    """Los OB y FVG marcados dentro de cada ID de `impulses`, sobre `bars`.

    `bars` son las velas de la temporalidad del ID —hoy H4 dentro de H4— y los
    índices de los impulsos apuntan a ellas. `marking` es la máscara, del largo
    de `bars`, de las velas en cuyo cierre se marca. Los impulsos no publicables
    se ignoran.
    """
    _check_bars(bars, timeframe)
    total = len(bars)
    marking = np.asarray(marking, dtype=bool)
    if marking.shape != (total,):
        raise DomainError(
            f"La máscara de cortes tiene {marking.shape} elementos y las velas de "
            f"{timeframe} son {total}"
        )
    stamps = _utc(bars.index)
    published = [impulse for impulse in impulses if impulse.publishable]
    if total < 2 or not published:
        return _empty()

    open_ = bars["open"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)

    found = _candidates(open_, high, low, close)
    if not len(found.origin):
        return _empty()
    broken = _first_breaks(close, found, total)

    # El ID vigente en cada corte: constituido en o antes del corte y todavía
    # no roto. Los impulsos van en orden y no se solapan.
    constitution = np.array([impulse.index_constitution for impulse in published])
    ends = np.array(
        [total if impulse.index_end is None else impulse.index_end for impulse in published]
    )
    is_gap = found.kind == PatternKind.FAIR_VALUE_GAP.value
    is_bullish = found.direction == ImpulseDirection.ALCISTA.value
    #: Hasta qué vela (exclusiva) sigue marcado cada candidato; `-1` = sin marcar.
    marked_until = np.full(len(found.origin), -1, dtype=np.int64)

    rows: list[dict[str, object]] = []
    for cut in np.flatnonzero(marking):
        position = int(np.searchsorted(constitution, cut, side="right")) - 1
        if position < 0 or cut >= ends[position]:
            continue
        impulse = published[position]
        lower, upper = sorted((impulse.anchor, impulse.extreme))
        span = upper - lower
        if span <= 0:
            # Ancla y extremo cruzados: se cuenta en los diagnósticos del
            # detector; aquí no cabe nada.
            continue
        room = np.where(is_bullish, upper - found.high, found.low - lower)
        eligible = (
            (found.known <= cut)
            & (found.origin >= impulse.index_anchor)
            & (broken > cut)
            & (marked_until <= cut)
            & (found.low >= lower)
            & (found.high <= upper)
            & (~is_gap | (room >= rule.min_fvg_room * span))
        )
        for candidate in np.flatnonzero(eligible).tolist():
            origin = int(found.origin[candidate])
            known = int(found.known[candidate])
            end = int(min(broken[candidate], ends[position]))
            marked_until[candidate] = end
            alive = end >= total
            reason = (
                None
                if alive
                else PatternEnd.ROTURA
                if broken[candidate] <= ends[position]
                else PatternEnd.MUERTE_DEL_ID
            )
            rows.append(
                {
                    "timeframe": timeframe,
                    "id_timeframe": impulse.timeframe,
                    "id_num": impulse.id_num,
                    "direccion": str(found.direction[candidate]),
                    "tipo": str(found.kind[candidate]),
                    "ts_origen": stamps[origin],
                    "ts_conocido": stamps[known],
                    "ts_marcado": stamps[int(cut)],
                    "ts_fin": None if alive else stamps[end],
                    "motivo_fin": None if reason is None else reason.value,
                    "precio_bajo": float(found.low[candidate]),
                    "precio_alto": float(found.high[candidate]),
                    "vivo": alive,
                    "indice_origen": origin,
                    "indice_conocido": known,
                    "indice_marcado": int(cut),
                    "indice_fin": None if alive else end,
                }
            )

    if not rows:
        return _empty()
    frame = pd.DataFrame(rows, columns=list(PATTERN_COLUMNS))
    frame = frame.sort_values(["indice_marcado", "indice_origen", "tipo"]).reset_index(drop=True)
    return frame.astype({"indice_fin": "Int64"})


# --- Geometría --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Candidates:
    """Todos los OB y FVG de la serie, sepan o no de algún ID."""

    kind: np.ndarray
    direction: np.ndarray
    origin: np.ndarray
    known: np.ndarray
    low: np.ndarray
    high: np.ndarray


def _candidates(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> _Candidates:
    kinds: list[np.ndarray] = []
    directions: list[np.ndarray] = []
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    for direction in ImpulseDirection:
        for kind, found in (
            (PatternKind.ORDER_BLOCK, _order_blocks(open_, high, low, close, direction)),
            (PatternKind.FAIR_VALUE_GAP, _fair_value_gaps(high, low, direction)),
        ):
            count = len(found[0])
            kinds.append(np.full(count, kind.value, dtype=object))
            directions.append(np.full(count, direction.value, dtype=object))
            parts.append(found)
    return _Candidates(
        kind=np.concatenate(kinds),
        direction=np.concatenate(directions),
        origin=np.concatenate([part[0] for part in parts]).astype(np.int64),
        known=np.concatenate([part[1] for part in parts]).astype(np.int64),
        low=np.concatenate([part[2] for part in parts]).astype(float),
        high=np.concatenate([part[3] for part in parts]).astype(float),
    )


def _order_blocks(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: ImpulseDirection,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origen, conocido, bajo, alto) de cada OB en `direction`.

    Alcista: una vela bajista y, más tarde, un CIERRE por encima de su máximo
    sin que en medio haya cerrado otra vela bajista —si la hay, la última
    contraria es ésa—. La zona es el cuerpo. Se busca un desplazamiento por
    pasada: en la pasada `step` se mira si la vela `i + step` cierra más allá,
    y se descartan las candidatas a las que ya les cayó una contraria entre
    medias. El doji no es contrario ni candidato.
    """
    bullish = direction is ImpulseDirection.ALCISTA
    total = len(close)
    # La vela del OB va en contra del patrón: bajista en un OB alcista.
    counter = close < open_ if bullish else close > open_
    level = high if bullish else low
    known = np.full(total, -1, dtype=np.int64)
    # `clear[i]`: entre `i` y la vela que se mira no ha cerrado ninguna contraria.
    clear = np.ones(total, dtype=bool)
    for step in range(1, total):
        head = total - step
        if step > 1:
            clear[:head] &= ~counter[step - 1 : step - 1 + head]
        pending = counter[:head] & (known[:head] < 0) & clear[:head]
        if not pending.any():
            break
        gone = close[step:] > level[:head] if bullish else close[step:] < level[:head]
        hit = pending & gone
        known[:head][hit] = np.flatnonzero(hit) + step
    origin = np.flatnonzero(known >= 0)
    body_low = np.minimum(open_[origin], close[origin])
    body_high = np.maximum(open_[origin], close[origin])
    return origin, known[origin], body_low, body_high


def _fair_value_gaps(
    high: np.ndarray, low: np.ndarray, direction: ImpulseDirection
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(origen, conocido, bajo, alto) de cada FVG en `direction`.

    Alcista: `low[i+1] > high[i-1]`, el hueco que dejó un tramo alcista. El
    hueco es estricto: dos velas que se tocan en el mismo precio no dejan hueco.
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


def _first_breaks(close: np.ndarray, found: _Candidates, total: int) -> np.ndarray:
    """Primera vela, después de saberse, que CIERRA más allá del borde lejano
    de cada zona: por debajo del bajo en un patrón alcista, por encima del alto
    en uno bajista. `total` si ninguna lo hace.

    Es geometría del histórico entero, como `ts_fin` en los impulsos: quien
    marque en un corte sólo mira si esa rotura ya ha ocurrido.
    """
    broken = np.full(len(found.origin), total, dtype=np.int64)
    for position in range(len(found.origin)):
        start = int(found.known[position]) + 1
        if start >= total:
            continue
        tail = close[start:]
        beyond = (
            tail < found.low[position]
            if found.direction[position] == ImpulseDirection.ALCISTA.value
            else tail > found.high[position]
        )
        hits = np.flatnonzero(beyond)
        if hits.size:
            broken[position] = start + int(hits[0])
    return broken


# --- Utilidades ---------------------------------------------------------------


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
    return frame.astype({"indice_fin": "Int64"})
