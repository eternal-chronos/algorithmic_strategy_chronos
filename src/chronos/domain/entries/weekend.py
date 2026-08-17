"""⚠️ El hueco de fin de semana entre la decisión y la ejecución (fase 3.2, §4).

En el análisis de la 3.1 salieron **49 confirmaciones decididas con velas del
viernes y ejecutadas en la reapertura del domingo**, tras un hueco de unas 50
horas. Sus operaciones daban -0,399 R (turtle) y -0,761 R (OB) frente a -0,079 R
y -0,185 R del resto.

**Esto no se corrige aquí.** Es una decisión del propietario, no una limpieza
técnica: lo único que hace este módulo es **marcar** cada operación y el informe
las **reporta por separado** en todos los desgloses.

**Sin umbral de horas.** Un "hueco de más de N horas" sería un parámetro que
nadie ha decidido. Lo que se pregunta es un hecho del calendario del mercado: si
entre el cierre que decide y el open de M1 que ejecuta **queda un sábado por
medio**. El sábado es el único día en que el mercado no cotiza en ningún momento,
así que su presencia en el intervalo es exactamente "el mercado cerró y volvió a
abrir", sin elegir ninguna cifra.

Se lee en UTC, que es la zona en la que viven todas las marcas de tiempo del
proyecto. El cierre del viernes y la apertura del domingo caen cerca de las 22:00
UTC y se mueven con el horario de verano; usar el sábado entero en vez de una
hora concreta hace que la marca no dependa de ese desplazamiento.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: Día de la semana en que el mercado no cotiza en ningún momento (0 = lunes).
_SATURDAY = 5


def crosses_weekend(decision: datetime, execution: datetime) -> bool:
    """`True` si entre los dos instantes queda un sábado, aunque sea un minuto.

    El intervalo es medio abierto `(decision, execution]`: decidir un sábado es
    imposible —no hay vela— y ejecutar justo en el primer minuto de la reapertura
    del domingo sí cuenta, que es el caso que el §4 quiere retratar.
    """
    if execution <= decision:
        return False
    day = decision.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= execution:
        if day.weekday() == _SATURDAY:
            start = day
            end = day + timedelta(days=1)
            if end > decision and start < execution:
                return True
        day += timedelta(days=1)
    return False


__all__ = ["crosses_weekend"]
