"""⚠️ El hueco de fin de semana entre la decisión y la ejecución (fase 3.2, §4).

Lo que se fija aquí es que la marca **no lleva umbral de horas**: no pregunta
cuánto tardó, pregunta si el mercado cerró por medio. Un lunes con dos días
enteros de diferencia no está marcado y un viernes con dos minutos de diferencia
tampoco; lo que marca es que quede un sábado en el intervalo.

Esto NO corrige nada: la decisión de qué hacer con esas operaciones es del
propietario. Aquí sólo se comprueba que la marca dice lo que promete.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chronos.domain.entries.weekend import crosses_weekend


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    """Marzo de 2024: el 8 es viernes, el 9 sábado y el 10 domingo."""
    return datetime(2024, 3, day, hour, minute, tzinfo=UTC)


def test_el_viernes_por_la_noche_y_el_domingo_por_la_noche_estan_separados() -> None:
    """El caso que el §4 retrata: se decide el viernes y se ejecuta el domingo."""
    assert crosses_weekend(at(8, 21, 0), at(10, 22, 1))


def test_dentro_del_mismo_dia_no_hay_hueco() -> None:
    assert not crosses_weekend(at(8, 12, 0), at(8, 12, 1))


def test_de_un_dia_al_siguiente_entre_semana_tampoco() -> None:
    """Dos días enteros de diferencia y sin sábado por medio: no está marcado.

    Es lo que distingue esta marca de un umbral de horas.
    """
    assert not crosses_weekend(at(5, 23, 0), at(7, 23, 0))


def test_basta_con_que_el_sabado_asome_un_minuto() -> None:
    assert crosses_weekend(at(8, 23, 59), at(9, 0, 1))


def test_el_domingo_solo_no_marca() -> None:
    """El domingo el mercado ya está abierto: el que cierra el hueco es el sábado."""
    assert not crosses_weekend(at(10, 22, 30), at(11, 0, 5))


def test_una_ejecucion_anterior_a_la_decision_no_marca_nada() -> None:
    """No puede pasar, y si pasara sería un bug de otro sitio: aquí se dice `False`
    en vez de devolver un `True` que taparía el problema."""
    assert not crosses_weekend(at(10, 12, 0), at(8, 12, 0))
