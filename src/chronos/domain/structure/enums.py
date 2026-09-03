"""Vocabulario del impulso dominante.

Los *valores* de estos enums son datos que salen a CSV, a JSON y al informe: se
escriben en el idioma de la especificación del propietario y no deben cambiarse
sin actualizar también las salidas.
"""

from __future__ import annotations

from enum import StrEnum


class ImpulseDirection(StrEnum):
    """Dirección de un impulso dominante."""

    ALCISTA = "alcista"
    BAJISTA = "bajista"

    def opposite(self) -> ImpulseDirection:
        return ImpulseDirection.BAJISTA if self is ImpulseDirection.ALCISTA else ImpulseDirection.ALCISTA


class BodyDirection(StrEnum):
    """Dirección del *cuerpo* de una vela. La mecha no interviene (§2.1)."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    DOJI = "doji"

    def as_impulse(self) -> ImpulseDirection:
        """Dirección de impulso equivalente. El doji no tiene ninguna."""
        if self is BodyDirection.DOJI:
            raise ValueError("Un doji no tiene dirección de impulso")
        return (
            ImpulseDirection.ALCISTA if self is BodyDirection.BULLISH else ImpulseDirection.BAJISTA
        )


class BreakKind(StrEnum):
    """Por cuál de los dos límites se rompió el impulso (§2.4)."""

    #: Cierre más allá del `extremo`: la tendencia continúa, el nuevo ID tendrá
    #: la misma dirección.
    A_FAVOR = "ROTURA_A_FAVOR"
    #: Cierre más allá del `ancla`: cambio de sesgo, el nuevo ID será opuesto.
    EN_CONTRA = "ROTURA_EN_CONTRA"


class ContactKind(StrEnum):
    """Cómo tocó el precio uno de los dos límites del ID (sección D).

    Sólo se **mide**: ninguna regla del módulo 1 lee estas categorías, y la
    detección de impulsos no cambia por su existencia.
    """

    #: Alcanza el nivel con la mecha pero la barra cierra dentro del rango.
    TOQUE_MECHA = "TOQUE_MECHA"
    #: Cierra fuera del límite y la barra siguiente vuelve a cerrar dentro.
    ROTURA_FALLIDA = "ROTURA_FALLIDA"
    #: Cierra fuera y no vuelve: es la rotura que mata al ID.
    ROTURA_REAL = "ROTURA_REAL"


class ContactSide(StrEnum):
    """Cuál de los dos límites del ID se tocó, leído como geometría.

    Parte alta y parte baja del rango, no ancla y extremo: en un ID alcista el
    extremo es el límite superior y en uno bajista es el inferior.
    """

    SUPERIOR = "superior"
    INFERIOR = "inferior"


class AnchorMode(StrEnum):
    """Dónde se ancla el nuevo impulso. **Parámetro abierto** (§2.5).

    Los dos modos difieren en pocos centavos; el propietario decide comparando
    con sus capturas. El informe imprime la diferencia entre ambos.
    """

    #: Extremo del cuerpo de la última vela contraria previa al arranque de la
    #: pierna (en una pierna bajista, su body high).
    A1_LAST_COUNTER_BODY = "A1_last_counter_body"
    #: Extremo del cuerpo de la primera vela de la propia pierna.
    A2_FIRST_LEG_BAR = "A2_first_leg_bar"


class SeedMode(StrEnum):
    """Cómo arranca la máquina al principio del histórico. **Parámetro abierto**.

    La especificación no dice qué pierna está en curso en la primera barra del
    dataset, porque en el gráfico del propietario siempre hay pasado a la
    izquierda. Aquí no lo hay, así que la decisión se expone en vez de elegirse.
    Afecta únicamente a los primeros impulsos del histórico.
    """

    #: La primera vela no-doji abre la pierna en curso, en su propia dirección.
    #: El ancla A1 del primer impulso puede no existir (no hay vela contraria
    #: anterior): ese impulso se marca como no publicable.
    S1_FIRST_NON_DOJI = "S1_first_non_doji"
    #: Se descarta el tramo inicial —cuyo arranque no está en los datos— y la
    #: pierna en curso la abre la primera vela contraria a ese tramo. Garantiza
    #: que el ancla existe en ambos modos.
    S2_FIRST_COUNTER_BAR = "S2_first_counter_bar"


class LegStartMode(StrEnum):
    """Qué papel juega la vela que rompe en la pierna que abre. **Parámetro abierto**.

    La regla del propietario dice que en un impulso alcista el extremo cae sobre
    una vela verde y en uno bajista sobre una roja. Dentro de la pierna eso se
    cumple solo: una vela contraria constituye el ID en el acto, así que no llega
    a fijar nada. La excepción es la **primera** vela, la de la rotura, que la
    máquina adopta sin mirar su color (R-36). Los tres modos son las tres
    lecturas posibles de esa excepción; ninguna la elige el motor.
    """

    #: Comportamiento anterior a R-36: la pierna arranca en la vela que rompe,
    #: sea cual sea su color, y su cuerpo puede fijar el extremo.
    L1_CURRENT = "L1_actual"
    #: La vela que rompe cuenta *sólo* como rotura: la pierna arranca en la
    #: siguiente y ni el ancla ni el extremo pueden salir de la que rompió.
    L2_NEXT_BAR = "L2_siguiente_barra"
    #: La pierna arranca en la vela que rompe —de ahí sale el ancla— pero esa
    #: vela no fija el extremo si su color es contrario al del impulso.
    L3_VALID_COLOUR_EXTREME = "L3_extremo_solo_color_valido"


class DojiBreakMode(StrEnum):
    """Si un doji puede romper un ID. **Parámetro abierto**.

    La especificación se contradice: §2.2 dice que el doji "no constituye
    impulsos ni rompe nada", pero la máquina de estados de §2.6 sólo excluye al
    doji en el estado LIMBO y evalúa la rotura por cierre sin mirar el cuerpo.
    En vez de elegir, se enumeran las dos lecturas y el informe cuenta cuántas
    barras del histórico distinguen una de otra.
    """

    #: Lectura literal de §2.2: un doji es neutro también para romper.
    D1_NEUTRAL = "D1_doji_no_rompe"
    #: Lectura literal de §2.6: la rotura es por cierre, el cuerpo da igual.
    D2_BREAKS = "D2_doji_rompe_por_cierre"


class BreakLevelSource(StrEnum):
    """De dónde salió el nivel que rompió el ID (fase 2.1).

    Con `BREAK_BY_ZONE = false` es siempre `linea` y la columna no dice nada
    nuevo. Con la regla nueva separa las formas de morir: atravesando una zona
    entera —UL a favor, PUL en contra—, o por línea porque en ese lado no había
    ninguna zona que la sustituyera, que es lo que le pasa al ancla de los
    primeros ID del histórico.

    El lado en contra es **siempre** la zona del extremo del ID inmediatamente
    anterior: su UL viejo pasa a ser el PUL de éste. Sólo se rompe por línea
    mientras no hay ningún ID detrás del que sacarla.
    """

    #: El nivel es la línea del ID: el extremo a favor, el ancla en contra.
    LINE = "linea"
    #: Borde exterior de la zona UL, pegada al extremo.
    LAST = "UL"
    #: Borde exterior de la zona PUL: la vela del extremo del ID anterior, por el
    #: tramo que mira a este ID —el cuerpo si aquél iba al revés, su mecha si iba
    #: en el mismo sentido—.
    PENULTIMATE = "PUL"


class OverlapPriority(StrEnum):
    """Qué lado se evalúa primero cuando una vela rompe por los dos (fase 2.1).

    Con las zonas UL y PUL solapadas en precio —impulsos cuyo rango cabe dentro de
    el cuerpo del extremo anterior— un mismo cierre puede quedar más allá de los dos bordes
    exteriores a la vez. El orden decide de qué muere el ID y, con él, la
    dirección de la pierna que abre el limbo: no es cosmético mientras no se
    demuestre con datos que lo es. **El motor no elige**: los dos órdenes se
    implementan y el informe cuenta cuántas veces se dan y qué cambia.

    `a_favor_primero` reproduce además el orden que la fase 1 ya tenía escrito en
    la máquina de estados, así que con `BREAK_BY_ZONE = false` no mueve nada.
    """

    A_FAVOR_FIRST = "a_favor_primero"
    EN_CONTRA_FIRST = "en_contra_primero"


class MachineState(StrEnum):
    """Estados de la máquina (§2.6)."""

    #: El ID anterior está muerto y el nuevo aún no existe. Estado legítimo.
    LIMBO = "LIMBO"
    ID_VIGENTE = "ID_VIGENTE"
