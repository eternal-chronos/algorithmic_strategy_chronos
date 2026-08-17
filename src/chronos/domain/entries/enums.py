"""Vocabulario de la fase 3.

Los *valores* salen a CSV, a JSON y al informe: están en el idioma del
propietario y no se cambian sin actualizar también las salidas.
"""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    """Los desenlaces de una zona en observación (§1.2 y fase 3.2, §1).

    **El contacto no es un desenlace.** Tocar una zona abre observación y nada
    más: hasta la fase 3.1 el `respeto` se operaba por contacto, y eso fue un
    error de la especificación. En la 3.2 sólo hay operación con `rechazo` o con
    `rotura_y_retesteo`; `respeto` se conserva porque es el desenlace que produce
    `ENTRY_MODE = v31_contacto`, que existe **sólo** para la regresión.
    """

    #: ⚠️ Fase 3.1 y anteriores. El precio toca la zona y se opera a favor del ID
    #: sin exigir rechazo ni rotura. **Eliminado en la 3.2**: sólo lo produce el
    #: modo de regresión.
    RESPETO = "respeto"
    #: Fase 3.2. Una vela de H4 rechaza la zona. La dirección de la operación la
    #: fija el rechazo: en el UL va EN CONTRA del ID, en el OB a favor.
    RECHAZO = "rechazo"
    #: El precio rompe la zona y después vuelve a testearla. Sólo el UL.
    ROTURA_Y_RETESTEO = "rotura_y_retesteo"


class EntryMode(StrEnum):
    """Qué abre una operación en H4 (fase 3.2, §3).

    `v31_contacto` **no es una variante del proyecto**: se conserva para el test
    de regresión —1.508 confirmaciones y 3.298 operaciones exactas— y para poder
    poner las dos columnas una al lado de la otra.
    """

    #: 3.1. El contacto con la zona basta: se opera a favor del ID.
    V31_CONTACTO = "v31_contacto"
    #: 3.2. El contacto sólo abre observación. Hace falta un rechazo en H4 o una
    #: rotura con retesteo.
    V32_RECHAZO = "v32_rechazo"


class RejectionForm(StrEnum):
    """Las dos formas válidas de rechazo en H4 (fase 3.2, §2). **Sin parámetros.**

    Vale la que ocurra primero. Cuando las dos caen en la misma vela la decisión
    es idéntica —misma vela, misma dirección— así que la elección de etiqueta es
    cosmética y se registran las dos.
    """

    #: Forma A. Una vela de H4 entra en la zona y **cierra fuera de ella**, del
    #: lado por el que entró. Una sola vela.
    A_CIERRE_FUERA = "A_cierre_fuera"
    #: Forma B. Turtle soup de H4: dos velas consecutivas, la segunda llega a la
    #: mecha de la primera y cierra sin superarla, y eso ocurre en la zona.
    B_TURTLE_SOUP = "B_turtle_soup"


class RejectionKind(StrEnum):
    """Las tres definiciones candidatas de rechazo (§2). **Ninguna adoptada.**

    El propietario ha delegado la formalización pero no ha elegido. Las tres se
    calculan siempre, las tres se marcan en el histórico y el motor no toma
    ninguna por defecto: se decide mirando capturas.
    """

    #: La mecha entra en la zona y el cuerpo cierra fuera. Sin parámetros.
    R1_MECHA_EN_ZONA = "R1_mecha_en_zona_cierre_fuera"
    #: La mecha contra el movimiento supera el percentil P de la proporción
    #: mecha/cuerpo de la temporalidad, sobre sesiones anteriores únicamente.
    R2_MECHA_DOMINANTE = "R2_mecha_dominante"
    #: El cierre queda en el tercio favorable del rango de la vela.
    R3_CIERRE_EN_EXTREMO = "R3_cierre_en_extremo"


class ConfirmationKind(StrEnum):
    """Qué confirmó en H1.

    La fase 3.1 deja **dos** vías vivas —`TURTLE_SOUP` y `OB_H1`— y conserva las
    otras dos sólo para poder reproducir la 3.0 en regresión. Cuál de los dos
    juegos se usa lo decide `ConfirmMode`, y no se mezclan nunca en una corrida.
    """

    #: 3.1, vía 1. Dos velas de H1 consecutivas: la primera deja mecha y la
    #: segunda llega a ella y cierra sin superarla.
    TURTLE_SOUP = "turtle_soup"
    #: 3.1, vía 2. Hay ID de H1 en la dirección, su OB está formado y **el precio
    #: llega a ese OB**. En la 3.0 bastaba con que el OB naciera.
    OB_H1 = "ob_h1"
    #: 3.0 solamente. Se constituye un ID de H1 en la dirección buscada. Un ID por
    #: sí solo no confirma nada en la 3.1.
    ID_H1 = "id_h1"
    #: 3.0 solamente. Un rechazo en cualquiera de sus tres definiciones, en unión.
    #: R1, R2 y R3 se siguen calculando y persistiendo, pero ya no deciden nada.
    RECHAZO = "rechazo"


class ConfirmMode(StrEnum):
    """Qué juego de vías de confirmación corre (fase 3.1).

    `v30_tres_vias` **no es una variante del proyecto**: se conserva para el test
    de regresión y para poder poner las dos columnas una al lado de la otra.
    """

    #: Las tres de la 3.0: ID de H1, OB de H1 al nacer, y rechazo en unión.
    V30_TRES_VIAS = "v30_tres_vias"
    #: Las dos de la 3.1: turtle soup y OB de H1 alcanzado por el precio.
    V31_DOS_VIAS = "v31_dos_vias"


class ConfirmPriority(StrEnum):
    """Cuál manda si las dos vías caen en la misma vela de H1.

    Hace falta un orden determinista y el propietario no lo ha fijado, así que se
    declara como parámetro abierto y el informe cuenta cuántas veces coinciden y
    en cuántas el orden cambia el resultado.
    """

    TURTLE_PRIMERO = "turtle_primero"
    OB_PRIMERO = "ob_primero"

    @property
    def order(self) -> tuple[ConfirmationKind, ConfirmationKind]:
        if self is ConfirmPriority.TURTLE_PRIMERO:
            return (ConfirmationKind.TURTLE_SOUP, ConfirmationKind.OB_H1)
        return (ConfirmationKind.OB_H1, ConfirmationKind.TURTLE_SOUP)


class EntryTimeframe(StrEnum):
    """Dónde se coloca la entrada (§1.4). Las dos se miden por separado."""

    #: La zona de H1: su OB.
    H1 = "H1"
    #: El OB suelto de M15, que no necesita ID.
    M15 = "M15"


class StopZone(StrEnum):
    """Bajo qué zona va el stop (§3). Las dos se reportan por separado."""

    H1 = "h1"
    M15 = "m15"


class GuardRail(StrEnum):
    """Dónde muere una señal que no llega a operación (§6, embudo; §10, visual).

    El embudo no es decorativo: una estrategia que pierde el 99 % de sus
    contactos en un guardarraíl concreto no se juzga igual que una que los
    reparte. Cada señal descartada guarda **uno** de estos motivos, el primero
    que la mató.
    """

    #: Fase 3.2. El precio tocó la zona y la observación se apagó sin desenlace
    #: operable: ni rechazo en H4 ni rotura con retesteo. Es el guardarraíl que
    #: recoge la rama que la 3.1 operaba por contacto.
    CONTACTO_SIN_DESENLACE = "contacto_sin_desenlace"
    #: La zona se rompió y no hubo retesteo dentro de la ventana (§1.2).
    ROTURA_SIN_RETESTEO = "rotura_sin_retesteo"
    #: El OB se rompió: ahí no hay variante de retesteo, la observación muere.
    OB_ROTO = "ob_roto"
    #: El ID de H4 murió por el otro lado antes de que la observación diera nada.
    ID_H4_MUERTO = "id_h4_muerto"
    #: Nunca apareció ninguna de las vías de confirmación de H1 del modo activo.
    SIN_CONFIRMACION_H1 = "sin_confirmacion_h1"
    #: Confirmó en H1 pero el ID de H1 no tenía OB con el que colocar la entrada.
    SIN_OB_H1 = "sin_ob_h1"
    #: Confirmó en H1 pero no llegó a formarse ningún OB suelto de M15.
    SIN_OB_M15 = "sin_ob_m15"
    #: La zona de entrada medía cero: no hay stop ni, por tanto, 1R.
    ZONA_DE_ENTRADA_PLANA = "zona_de_entrada_plana"
    #: El precio ya estaba al otro lado del stop cuando tocaba ejecutar.
    STOP_INVALIDO = "stop_invalido"
    #: No quedaban velas M1 para ejecutar (final del histórico).
    SIN_M1_PARA_EJECUTAR = "sin_m1_para_ejecutar"


class TradeOutcome(StrEnum):
    """Cómo terminó una operación ejecutada."""

    OBJETIVO = "objetivo"
    STOP = "stop"
    #: Stop y objetivo tocados en la misma barra: gana el stop (§4). Se marca
    #: aparte porque es una convención conservadora, no un dato del mercado.
    STOP_MISMA_BARRA = "stop_misma_barra"
    #: El histórico se acabó con la operación abierta. No cuenta en la
    #: expectativa: se declara y se cuenta aparte.
    ABIERTA = "abierta"

    @property
    def is_resolved(self) -> bool:
        return self is not TradeOutcome.ABIERTA

    @property
    def is_win(self) -> bool:
        return self is TradeOutcome.OBJETIVO


class DailyContext(StrEnum):
    """Relación entre el contexto diario y la señal de H4 (§1.1 y §5.1)."""

    #: No hay contexto diario activo. La señal vale igual: el contexto es
    #: opcional y no obligatorio.
    SIN_CONTEXTO = "sin_contexto"
    #: El Diario tocó zona en la misma dirección que la señal de H4.
    A_FAVOR = "a_favor"
    #: El Diario dice una cosa y H4 la contraria. **Manda H4** y se registra el
    #: conflicto: no se descarta la señal.
    CONFLICTO = "conflicto"

    @property
    def is_active(self) -> bool:
        """`True` cuando había contexto diario, esté a favor o en conflicto."""
        return self is not DailyContext.SIN_CONTEXTO
