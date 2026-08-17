"""Fase 3.2 — el **rechazo en H4**. Dos formas, sin un solo parámetro.

    El contacto con una zona de H4 abre observación, y nada más. Hace falta un
    rechazo o una rotura con retesteo.

Hasta la fase 3.1 tocar una zona bastaba para operar a favor del ID. Eso fue un
error de la especificación: 2.234 de las 3.702 operaciones de la 3.0 fueron
"UL + respeto", es decir comprar cuando el precio sube al techo del impulso.
Aquí se escribe lo que sí abre una operación.

**El rechazo se espera en H4**, no en H1. La vela de H4 rechaza la zona, eso fija
la dirección de la operación, y sólo después H1 confirma la entrada con las dos
vías de la 3.1, sin cambios.

Dos formas válidas, y **vale la que ocurra primero**:

    Forma A · rechazo simple  una vela entra en la zona y CIERRA FUERA de ella,
                              sin sostenerla. Una sola vela.
    Forma B · turtle soup     dos velas consecutivas: la segunda llega a la mecha
                              de la primera y cierra sin superarla, en la zona.

**Sin parámetros.** Ninguna de las dos lleva umbrales, percentiles ni
proporciones. Si aquí hiciera falta un número que el enunciado no da, sería un
hueco de la especificación y no una decisión del motor.

---

**Hacia dónde rechaza, y por qué eso decide el lado de la operación.** Una zona
se atraviesa en un sentido —`break_direction`, la regla de la fase 2.1— y se
rechaza en el contrario: el precio entra, no la sostiene y se va por donde vino.

    UL de un ID alcista   se atraviesa hacia ARRIBA -> rechazarla es irse hacia
                          abajo -> la operación es una VENTA, EN CONTRA del ID.
    OB de un ID alcista   se atraviesa hacia ABAJO  -> rechazarla es irse hacia
                          arriba -> la operación es una COMPRA, a favor del ID.

Es la primera vez en el proyecto que una operación va en contra del ID de H4, y
no es un caso especial del código: sale de la misma regla que ya decidía por
dónde se rompe cada zona. La dirección se calcula aquí una sola vez y desde ahí
viaja por toda la cascada —confirmación en H1, entrada, stop y objetivo—, que es
lo que impide que un lado se quede sin invertir.

**"Cerrar fuera" es cerrar del lado de dentro.** Cerrar más allá del borde
exterior ya tiene nombre en el proyecto: es la ROTURA (`breaks`, fase 2.1), y una
rotura es lo contrario de un rechazo. Así que "fuera de la zona" sólo puede
significar el otro lado: el borde que el precio cruzó al entrar. No es una
elección entre dos lecturas posibles, es la única que no choca con una regla ya
escrita.

**Esto no es R1.** `rejects_r1` mira la zona desde la dirección BUSCADA y exige
cerrar más allá de ella, que sobre un UL es justamente romperlo. La forma A es la
contraria: se cierra de vuelta. Comparten la palabra "rechazo" y no la
definición, y por eso viven en ficheros distintos.

**Causalidad.** Las dos formas se leen de velas ya cerradas: la forma A de la
propia vela, la forma B de esa vela y la inmediatamente anterior. No hay nada que
pedir por adelantado, así que no hay frontera que declarar. La zona se recibe
barra a barra —el UL se mueve— y quien llama es responsable de dar la vigente en
cada cierre.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from chronos.domain.entries.enums import RejectionForm
from chronos.domain.entries.turtle_soup import TurtleSoup, find_turtle_soup
from chronos.domain.entries.zone_timeline import break_direction, touches
from chronos.domain.structure.enums import ImpulseDirection
from chronos.domain.structure.errors import StructureError
from chronos.domain.structure.zones import CandleSeries, Zone


@dataclass(frozen=True, slots=True)
class ZoneRejection:
    """Un rechazo de zona en H4, con **las dos formas registradas**.

    `form` es la que disparó y `forms` son todas las que se cumplían en esa vela.
    Las dos viajan porque el §2 lo pide con todas las letras: en la 3.0 se cortó
    la evaluación al primer acierto y esa información hubo que reconstruirla a
    mano después.

    Cuando las dos formas caen en la misma vela la decisión es **idéntica**
    —misma vela, misma dirección, mismo instante— así que cuál se apunte como
    disparo es cosmético. Se apunta la A, que es la que el enunciado nombra
    primero, y `forms` deja constancia de que la otra también estaba.
    """

    #: Vela de H4 en la que el rechazo queda decidido. En la forma B es la
    #: **segunda** vela: es cuando se sabe, no cuando empezó.
    index: int
    #: La forma que disparó.
    form: RejectionForm
    #: Todas las que se cumplían en esa vela, en el orden del enunciado.
    forms: tuple[RejectionForm, ...]
    #: Dirección de la OPERACIÓN, no del ID.
    direction: ImpulseDirection
    close: float
    #: Extremo de la mecha rechazada. Sólo en la forma B.
    extreme: float | None = None

    @property
    def both_forms(self) -> bool:
        """Las dos formas se cumplían en la misma vela."""
        return len(self.forms) == 2

    @property
    def index_first(self) -> int | None:
        """Primera vela del patrón, en la forma B."""
        return self.index - 1 if self.form is RejectionForm.B_TURTLE_SOUP else None


def rejection_direction(zone: Zone) -> ImpulseDirection:
    """Hacia dónde se va el precio al rechazar la zona: **la operación**.

    Es el reverso exacto de `break_direction`, la regla que la fase 2.1 ya usaba
    para decidir por dónde se atraviesa cada zona. Escribirla derivada y no a
    mano es lo que garantiza que el UL y el OB no se puedan desincronizar.
    """
    return break_direction(zone).opposite()


def rejects_by_close(zone: Zone, high: float, low: float, close: float) -> bool:
    """Forma A — la vela entra en la zona y **cierra fuera**, del lado de dentro.

    Dos condiciones y nada más: el recorrido alcanza la zona —el mismo `touches`
    que usa todo el módulo, con los bordes incluidos— y el cierre queda al otro
    lado del borde **interior**, es decir de vuelta por donde el precio entró.

    Cerrar dentro no rechaza: la vela sostuvo la zona. Cerrar más allá del borde
    exterior tampoco: eso es romperla, que es lo contrario.

    Sin excepción para el doji. Las tres definiciones candidatas del §2 lo
    excluyen para poder compararse sobre la misma población; aquí no hay nada que
    comparar y el enunciado no lo menciona, así que un doji que entra en la zona
    y cierra fuera rechaza como cualquier otra vela.
    """
    if not touches(zone, high, low):
        return False
    if break_direction(zone) is ImpulseDirection.ALCISTA:
        # La zona se atraviesa hacia arriba: su borde interior es el de abajo.
        return close < zone.low
    return close > zone.high


def rejects_by_turtle_soup(
    series: CandleSeries, index: int, zone: Zone
) -> TurtleSoup | None:
    """Forma B — **turtle soup de H4**, con la misma definición que en H1.

    No se reimplementa el patrón: se llama al de `turtle_soup`, que es el que
    corre en H1 desde la fase 3.1, con la dirección del rechazo. Una definición
    escrita dos veces es una definición que se contradice a sí misma en la
    siguiente refactorización.

    Lo único que añade esta capa es el "**y eso ocurre en la zona**" del §2, y se
    comprueba con el mismo `touches` que abre la observación y que gobierna la
    puerta de la confirmación en H1: la vela que rechaza —la segunda— tiene que
    alcanzar la zona. Reutilizar esa lectura es lo que evita inventar un criterio
    nuevo de "estar en la zona" que sólo existiría aquí.
    """
    if not 0 <= index < len(series):
        raise StructureError(f"Índice {index} fuera de la serie ({len(series)} velas)")
    if index == 0:
        return None
    if not touches(zone, float(series.high[index]), float(series.low[index])):
        return None
    return find_turtle_soup(series, index, rejection_direction(zone))


def evaluate_rejection(
    series: CandleSeries, index: int, zone: Zone
) -> ZoneRejection | None:
    """Las **dos** formas sobre la misma vela. Ninguna corta a la otra.

    Se evalúan siempre las dos aunque la primera ya rechace: la señal tiene que
    registrar qué formas estaban disponibles y no sólo la que disparó.
    """
    high = float(series.high[index])
    low = float(series.low[index])
    close = float(series.close[index])

    forms: list[RejectionForm] = []
    if rejects_by_close(zone, high, low, close):
        forms.append(RejectionForm.A_CIERRE_FUERA)
    soup = rejects_by_turtle_soup(series, index, zone)
    if soup is not None:
        forms.append(RejectionForm.B_TURTLE_SOUP)
    if not forms:
        return None

    return ZoneRejection(
        index=index,
        form=forms[0],
        forms=tuple(forms),
        direction=rejection_direction(zone),
        close=close,
        extreme=None if soup is None else soup.extreme,
    )


def first_rejection(
    series: CandleSeries,
    zone_by_bar: Mapping[int, Zone],
    *,
    first: int,
    through: int,
) -> ZoneRejection | None:
    """El **primer** rechazo de la ventana, con la zona vigente en cada barra.

    `first` es la vela del contacto, incluida: una vela puede tocar la zona y
    rechazarla en el mismo cierre, y descartarlo exigiría una espera que el
    enunciado no pide.

    La zona llega barra a barra porque **el UL se mueve**: con la rotura por zona
    un ID vigente extiende su extremo y cada extensión cambia su UL. Preguntar
    por el último sería mirar al futuro justo en las velas que más importan.

    Se toma el primero porque es el primero que el propietario podría haber
    operado; elegir otro exigiría conocer la ventana entera.
    """
    limit = min(through, len(series) - 1)
    for index in range(max(first, 0), limit + 1):
        zone = zone_by_bar.get(index)
        if zone is None:
            continue
        found = evaluate_rejection(series, index, zone)
        if found is not None:
            return found
    return None


__all__ = [
    "ZoneRejection",
    "evaluate_rejection",
    "first_rejection",
    "rejection_direction",
    "rejects_by_close",
    "rejects_by_turtle_soup",
]
