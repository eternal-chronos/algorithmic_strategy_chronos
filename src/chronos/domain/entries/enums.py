"""Vocabulario de la fase 3.

Los *valores* salen a CSV, a JSON y al informe: están en el idioma del
propietario y no se cambian sin actualizar también las salidas.
"""

from __future__ import annotations

from enum import StrEnum


class Outcome(StrEnum):
    """Los dos desenlaces de una zona en observación (§1.2)."""

    #: El precio reacciona en la zona sin romperla. Se opera a favor del ID de H4.
    RESPETO = "respeto"
    #: El precio rompe la zona y después vuelve a testearla. Sólo el UL.
    ROTURA_Y_RETESTEO = "rotura_y_retesteo"


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
    """Qué confirmó en H1 (§1.3). Las tres valen por igual; no se ordenan."""

    #: Se constituye un ID de H1 en la dirección buscada.
    ID_H1 = "id_h1"
    #: Se forma un OB de H1 en la dirección buscada.
    OB_H1 = "ob_h1"
    #: Aparece un rechazo, en cualquiera de sus tres definiciones.
    RECHAZO = "rechazo"


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

    #: La zona se rompió y no hubo retesteo dentro de la ventana (§1.2).
    ROTURA_SIN_RETESTEO = "rotura_sin_retesteo"
    #: El OB se rompió: ahí no hay variante de retesteo, la observación muere.
    OB_ROTO = "ob_roto"
    #: El ID de H4 murió por el otro lado antes de que la observación diera nada.
    ID_H4_MUERTO = "id_h4_muerto"
    #: Nunca apareció ninguna de las tres confirmaciones de H1.
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
