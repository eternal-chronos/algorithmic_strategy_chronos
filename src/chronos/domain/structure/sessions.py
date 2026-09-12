"""El alto y el bajo de Asia y de Londres, marcados cada día a una hora fija.

Regla del propietario: cada día, a las 7:58, se marcan cuatro niveles —el alto
y el bajo de Asia (de 20:00 a 01:00) y el alto y el bajo de Londres (de 03:00 a
08:00)— y se dejan puestos hasta el cierre de la sesión (17:00). Las horas son
las del RELOJ CON EL QUE EL PROPIETARIO MIRA EL GRÁFICO —`timezone` de la
regla—, que por defecto es el de su plataforma, el UTC-4 fijo de cTrader
(`Etc/GMT+4`): las 7:58 de su pantalla son las 11:58 UTC todo el año. Con una
zona de plaza (`America/New_York`) el corte seguiría el horario de verano y en
invierno caería una hora más tarde en su pantalla, que no es lo que él ve.

Sin look-ahead: a las 7:58 sólo se conoce lo que ha cerrado a las 7:58. Como
Londres termina a las 8:00, su rango se mide hasta la marca —de 03:00 a 07:58—
y no hasta las 08:00: dibujar a las 7:58 un alto que se hizo a las 7:59 sería
enseñar el futuro. Asia cierra a la 01:00, mucho antes, y no le afecta.

Función pura sobre el DataFrame de barras: sin reloj, sin ficheros, sin red.
Está pensada para el histórico M1 —es el que ve el minuto 7:57 cerrar a las
7:58—; con barras más gruesas la marca sigue siendo honesta pero más pobre, que
sólo entra lo cerrado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from chronos.domain.errors import DomainError

#: Columnas del resultado, en su orden. Una fila por marca.
SESSION_COLUMNS = (
    "day",
    "marked_at",
    "until",
    "asia_high",
    "asia_low",
    "asia_high_at",
    "asia_low_at",
    "london_high",
    "london_low",
    "london_high_at",
    "london_low_at",
)

ASIA = "asia"
LONDON = "london"


@dataclass(frozen=True, slots=True)
class SessionLevelsRule:
    """Las horas de la regla, todas en el reloj de `timezone`.

    Una ventana cuyo `start` va después de su `end` cruza la medianoche —Asia,
    de 20:00 a 01:00— y pertenece al día en que TERMINA: el Asia que empieza el
    domingo a las 20:00 se marca el lunes a las 7:58.
    """

    #: El reloj de la pantalla del propietario: UTC-4 fijo (nombre POSIX, con el
    #: signo al revés). Se pasa el de `reporting.session_timezone` para que las
    #: marcas caigan donde él las lee.
    timezone: str = "Etc/GMT+4"
    asia_start: time = time(20, 0)
    asia_end: time = time(1, 0)
    london_start: time = time(3, 0)
    london_end: time = time(8, 0)
    #: Cuándo se marcan los cuatro niveles.
    mark_at: time = time(7, 58)
    #: Hasta cuándo se dejan puestos: el cierre de la sesión de NY.
    until: time = time(17, 0)

    def __post_init__(self) -> None:
        if not self.mark_at < self.until:
            raise DomainError("La marca de sesión tiene que ir antes de que se retire")
        if not self.london_start < self.london_end:
            raise DomainError("Londres no cruza la medianoche: start < end")

    def describe(self, clock: str | None = None) -> str:
        """Cómo se imprime la regla: las horas que de verdad se miden. `clock` es
        el nombre con el que se escribe el reloj; por defecto, la zona tal cual."""
        london_end = min(self.london_end, self.mark_at)
        return (
            f"Asia {_hm(self.asia_start)} → {_hm(self.asia_end)} · "
            f"Londres {_hm(self.london_start)} → {_hm(london_end)} · "
            f"marca a las {_hm(self.mark_at)} · hasta las {_hm(self.until)} · "
            f"reloj {clock or self.timezone}"
        )


#: La regla del propietario tal cual la dictó. Es la que se aplica si nadie
#: pasa otra.
OWNER_RULE = SessionLevelsRule()


def session_levels(bars: pd.DataFrame, rule: SessionLevelsRule = OWNER_RULE) -> pd.DataFrame:
    """Una fila por día marcado, con los cuatro niveles y la vela que fijó cada uno.

    `marked_at` y `until` van en UTC; `day` es la fecha de plaza de la marca. Un
    nivel cuya sesión no tuvo barras —festivo, hueco— queda en NaN y su vela en
    NaT: la marca existe igual, con lo que se sabía. Un día sin barras en
    ninguna de las dos sesiones no se marca. Tampoco se marca el día en que el
    histórico termina antes de la hora de la marca: a esa hora aún no había
    llegado.
    """
    if bars.empty:
        return _empty()
    index = pd.DatetimeIndex(bars.index)
    if index.tz is None:
        raise DomainError("Los niveles de sesión exigen un índice tz-aware en UTC")

    utc = index.tz_convert("UTC")
    local = utc.tz_convert(rule.timezone).tz_localize(None)
    day = local.normalize()
    minute = (local.hour * 60 + local.minute).to_numpy()
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)

    asia = _extremes(
        utc, highs, lows, *_window(day, minute, rule.asia_start, rule.asia_end)
    )
    # Londres se mide sólo hasta la marca: lo que cierra después no se conoce
    # a las 7:58.
    london = _extremes(
        utc,
        highs,
        lows,
        *_window(day, minute, rule.london_start, min(rule.london_end, rule.mark_at)),
    )

    days = pd.DatetimeIndex(sorted(set(asia.index) | set(london.index)))
    if days.empty:
        return _empty()
    marked_at = _at(days, rule.mark_at, rule.timezone)
    # La última barra cubre su propio paso: con M1, un minuto más.
    covered_until = pd.Timestamp(utc[-1]) + _step(utc)
    happened = marked_at <= covered_until
    days, marked_at = days[happened], marked_at[happened]
    if days.empty:
        return _empty()

    asia = asia.reindex(days)
    london = london.reindex(days)
    return pd.DataFrame(
        {
            "day": days,
            "marked_at": marked_at,
            "until": _at(days, rule.until, rule.timezone),
            "asia_high": asia["high"].to_numpy(),
            "asia_low": asia["low"].to_numpy(),
            "asia_high_at": asia["high_at"].to_numpy(),
            "asia_low_at": asia["low_at"].to_numpy(),
            "london_high": london["high"].to_numpy(),
            "london_low": london["low"].to_numpy(),
            "london_high_at": london["high_at"].to_numpy(),
            "london_low_at": london["low_at"].to_numpy(),
        },
        columns=list(SESSION_COLUMNS),
    ).reset_index(drop=True)


# --- Interno ------------------------------------------------------------------


def _window(
    day: pd.DatetimeIndex, minute: np.ndarray, start: time, end: time
) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Qué barras caen en la ventana y a qué día de marca pertenece cada una.

    `[start, end)` en minutos de reloj; la barra etiquetada en `t` cubre `t` y
    entra si `t < end`, que es cuando ya ha cerrado a las `end`.
    """
    lo = start.hour * 60 + start.minute
    hi = end.hour * 60 + end.minute
    if lo < hi:
        return (minute >= lo) & (minute < hi), day
    # Cruza la medianoche: el tramo de la tarde se marca al día siguiente.
    evening = minute >= lo
    mask = evening | (minute < hi)
    mark_day = pd.DatetimeIndex(day + pd.to_timedelta(evening.astype("int8"), unit="D"))
    return mask, mark_day


def _extremes(
    utc: pd.DatetimeIndex,
    highs: np.ndarray,
    lows: np.ndarray,
    mask: np.ndarray,
    mark_day: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Alto y bajo por día de marca, con la barra que fijó cada uno."""
    if not mask.any():
        return pd.DataFrame(
            {
                "high": pd.Series(dtype=float),
                "low": pd.Series(dtype=float),
                "high_at": pd.DatetimeIndex([], tz="UTC"),
                "low_at": pd.DatetimeIndex([], tz="UTC"),
            },
            index=pd.DatetimeIndex([]),
        )
    inside = pd.DataFrame(
        {"day": mark_day[mask], "high": highs[mask], "low": lows[mask], "at": utc[mask]}
    )
    grouped = inside.groupby("day", sort=True)
    # `idxmax`/`idxmin` devuelven la posición dentro de `inside`, que es la
    # primera barra que alcanzó el extremo si hay empate.
    high_at = inside["at"].to_numpy()[grouped["high"].idxmax().to_numpy()]
    low_at = inside["at"].to_numpy()[grouped["low"].idxmin().to_numpy()]
    return pd.DataFrame(
        {
            "high": grouped["high"].max().to_numpy(),
            "low": grouped["low"].min().to_numpy(),
            "high_at": pd.DatetimeIndex(high_at, tz="UTC"),
            "low_at": pd.DatetimeIndex(low_at, tz="UTC"),
        },
        index=pd.DatetimeIndex(grouped["high"].max().index),
    )


def _at(days: pd.DatetimeIndex, at: time, timezone: str) -> pd.DatetimeIndex:
    """La hora de pared `at` de cada día, en UTC. Sin `ambiguous`: si alguien
    configurase una hora dentro del salto del reloj tiene que fallar y verse."""
    naive = days + pd.Timedelta(hours=at.hour, minutes=at.minute)
    return pd.DatetimeIndex(naive).tz_localize(timezone).tz_convert("UTC")


def _step(utc: pd.DatetimeIndex) -> pd.Timedelta:
    """Paso del histórico: la moda de los saltos, que ni los huecos ni los
    minutos sin negociar la mueven."""
    if len(utc) < 2:
        return pd.Timedelta(minutes=1)
    deltas = utc.to_series().diff().dropna()
    return pd.Timedelta(deltas.mode().iloc[0]) if not deltas.empty else pd.Timedelta(minutes=1)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(SESSION_COLUMNS))


def _hm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"
