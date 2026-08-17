"""Configuración de la fase 3.0. Dataclasses puras; el YAML vive en infraestructura.

Dos cosas que este fichero decide y conviene leer antes que el código:

**El motor no elige parámetros.** Los que el enunciado deja abiertos se declaran
aquí con un valor por defecto que se imprime como *abierto* en el informe, junto
con la rejilla que el propietario tiene que recorrer mirando gráficos. Ninguno se
ha buscado optimizando: no se ha corrido ni una barrida de resultados.

**Los costes van marcados VERIFICAR.** Ninguno está calibrado contra Pepperstone
Razor. Los valores por defecto son los de la ficha del instrumento que ya vivía
en el proyecto, y el informe los imprime con la marca puesta para que nadie lea
un neto creyendo que es el neto de su cuenta.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from chronos.domain.entries.enums import ConfirmMode, ConfirmPriority, EntryMode
from chronos.domain.entries.rejection import REJECTION_PERCENTILES
from chronos.domain.errors import DomainError

#: Valor centinela de las ventanas: "mientras la observación siga viva", que es
#: la frontera que ya existe en el módulo y no un número inventado.
UNTIL_OBSERVATION_ENDS = 0


@dataclass(frozen=True, slots=True)
class EntryCosts:
    """⚠️ TODO MARCADO **VERIFICAR** hasta calibrarlo contra Pepperstone Razor.

    Nada de esto se ha comprobado contra una cuenta real. Se aplica siempre —un
    backtest sin horquilla, comisión y swap no dice nada sobre XAUUSD— y se
    imprime en portada con la marca puesta.
    """

    #: Horquilla en puntos (ticks) del símbolo. Sólo se usa cuando **no hay
    #: fichero de ask**: con los dos lados, la horquilla es un dato, no un modelo.
    spread_points: float = 20.0
    #: Deslizamiento en puntos. Siempre en contra, en entrada y en salida.
    slippage_points: float = 1.0
    #: Comisión por lote y por lado, en divisa de la cuenta.
    commission_per_lot_per_side: float = 3.0
    #: Swap en puntos por lote y noche. Negativo = coste.
    swap_long_points: float = -0.9
    swap_short_points: float = 0.2
    #: Día de la semana con swap triple (0 = lunes).
    triple_swap_weekday: int = 2

    def __post_init__(self) -> None:
        if self.spread_points < 0 or self.slippage_points < 0:
            raise DomainError("La horquilla y el deslizamiento no pueden ser negativos")
        if self.commission_per_lot_per_side < 0:
            raise DomainError("La comisión no puede ser negativa")
        if not 0 <= self.triple_swap_weekday <= 6:
            raise DomainError("triple_swap_weekday debe estar entre 0 y 6")

    def describe(self) -> tuple[str, ...]:
        return (
            f"VERIFICAR · horquilla {self.spread_points:g} puntos "
            "(sólo se aplica si no hay fichero de ask)",
            f"VERIFICAR · deslizamiento {self.slippage_points:g} puntos, siempre en contra",
            f"VERIFICAR · comisión {self.commission_per_lot_per_side:g} por lote y lado",
            f"VERIFICAR · swap {self.swap_long_points:g} / {self.swap_short_points:g} "
            f"puntos por noche (largo / corto), triple el día {self.triple_swap_weekday}",
        )


@dataclass(frozen=True, slots=True)
class EntriesConfig:
    """Fase 3.0. Con `enabled: False` no se emite ni una señal.

    El apagado es obligatorio y está testado: con las señales apagadas la corrida
    reproduce la línea base de la fase 2.1 —hash `801951b9cc26`, D 239 / H4 1.214
    / H1 4.148 detectados y 233 / 1.211 / 4.141 publicados— exacta.
    """

    enabled: bool = False

    # --- Fase 3.2: qué abre una operación en H4 ------------------------------

    #: Qué desenlace de la zona de H4 abre operación. `v32_rechazo` es el del
    #: proyecto; `v31_contacto` se conserva **sólo** para el test de regresión
    #: —1.508 confirmaciones y 3.298 operaciones exactas— y para poder poner la
    #: columna de la 3.1 al lado. No es una variante que haya que elegir.
    entry_mode: EntryMode = EntryMode.V32_RECHAZO

    # --- Fase 3.1: las vías de confirmación en H1 ----------------------------

    #: Qué juego de vías corre. `v31_dos_vias` es el del proyecto; el otro se
    #: conserva **sólo** para el test de regresión y para poder poner la columna
    #: de la 3.0 al lado. No es una variante que haya que elegir.
    confirm_mode: ConfirmMode = ConfirmMode.V31_DOS_VIAS
    #: Cuál manda si las dos vías caen en la misma vela. **Parámetro abierto**:
    #: hace falta un orden determinista y el propietario no lo ha fijado. El
    #: informe cuenta cuántas veces coinciden y si el orden cambia algo.
    confirm_priority: ConfirmPriority = ConfirmPriority.TURTLE_PRIMERO

    # --- Parámetros abiertos (§0: los decide el propietario, mirando gráficos) --

    #: `RECHAZO_PERCENTIL` del §2. **Se calculan los tres de la rejilla siempre**
    #: y el informe los desglosa; éste es sólo el que encabeza los resúmenes.
    #: No se ha elegido optimizando y no se recomienda ninguno.
    rejection_percentile: int = 75
    #: Rejilla completa que el §2 exige recorrer.
    rejection_grid: tuple[int, ...] = REJECTION_PERCENTILES
    #: Barras de H4 que se espera un retesteo tras romper el UL. `0` = hasta que
    #: se constituya el ID de H4 siguiente, que es la frontera que la máquina de
    #: estados ya tiene escrita y no un número nuevo.
    retest_window_h4: int = UNTIL_OBSERVATION_ENDS
    #: Rejilla que el propietario puede recorrer para esa espera.
    retest_grid: tuple[int, ...] = (6, 12, 24)
    #: Barras de M15 que se busca el OB suelto tras confirmar en H1. `0` =
    #: mientras la observación siga viva.
    m15_search_bars: int = UNTIL_OBSERVATION_ENDS

    # --- Ejecución (§4) ------------------------------------------------------

    costs: EntryCosts = field(default_factory=EntryCosts)
    #: Sizing de investigación **continuo**, sin lote mínimo, para que la
    #: población de señales no dependa del camino del equity (§4). El riesgo por
    #: operación no mueve ni una métrica en R —todos los costes escalan con el
    #: lotaje— y sólo fija la columna de lotes.
    risk_per_trade_usd: float = 1_000.0
    #: ⚠️ §4: no hay fichero de ask en el proyecto. Con `False` el comando se
    #: **detiene y avisa**. Con `True` se corre usando el bid para los dos lados,
    #: y esa asunción se declara en portada del informe, en el explorador y en la
    #: salida de la CLI. No se simula un ask inventado en ningún caso.
    allow_missing_ask: bool = False

    def __post_init__(self) -> None:
        if self.rejection_percentile not in self.rejection_grid:
            raise DomainError(
                f"RECHAZO_PERCENTIL = {self.rejection_percentile} no está en la "
                f"rejilla {self.rejection_grid}"
            )
        if self.retest_window_h4 < 0 or self.m15_search_bars < 0:
            raise DomainError("Las ventanas de búsqueda no pueden ser negativas")
        if self.risk_per_trade_usd <= 0:
            raise DomainError("El riesgo por operación debe ser positivo")

    def fingerprint(self) -> str:
        """Hash de lo que mueve una señal. Traza cada salida a sus parámetros.

        Deja fuera `enabled` a propósito: el hash identifica **qué cascada** se
        corrió, y con la fase apagada no se corre ninguna. La línea base de la
        fase 2.1 se comprueba con su propio hash, que este bloque no toca.
        """
        payload = {
            "entry_mode": self.entry_mode.value,
            "confirm_mode": self.confirm_mode.value,
            "confirm_priority": self.confirm_priority.value,
            "rejection_percentile": self.rejection_percentile,
            "rejection_grid": list(self.rejection_grid),
            "retest_window_h4": self.retest_window_h4,
            "m15_search_bars": self.m15_search_bars,
            "risk_per_trade_usd": self.risk_per_trade_usd,
            "costs": asdict(self.costs),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def open_decisions(self) -> tuple[str, ...]:
        """Parámetros abiertos. Se imprimen en el informe, sin recomendación."""
        retest = (
            "hasta que se constituya el ID de H4 siguiente"
            if self.retest_window_h4 == UNTIL_OBSERVATION_ENDS
            else f"{self.retest_window_h4} barras de H4"
        )
        search = (
            "mientras la observación siga viva"
            if self.m15_search_bars == UNTIL_OBSERVATION_ENDS
            else f"{self.m15_search_bars} barras de M15"
        )
        return (
            f"CONFIRM_PRIORITY = {self.confirm_priority.value} "
            "(cuál manda si las dos vías caen en la misma vela de H1; hace falta "
            "un orden determinista y no está decidido. El informe cuenta cuántas "
            "veces coinciden y en cuántas el orden cambia el resultado)",
            f"RECHAZO_PERCENTIL = {self.rejection_percentile} "
            f"(rejilla {{{', '.join(str(value) for value in self.rejection_grid)}}}; "
            "los tres se siguen calculando y persistiendo, pero ya NO deciden "
            "ninguna entrada: son columnas informativas)",
            f"VENTANA DE RETESTEO = {retest} "
            "(§1.2 no la acota; el valor por defecto es la frontera que la máquina "
            f"de estados ya tenía, no un número nuevo; rejilla {self.retest_grid})",
            f"VENTANA DE BÚSQUEDA EN M15 = {search} "
            "(§1.4 no la acota)",
            f"RIESGO POR OPERACIÓN = {self.risk_per_trade_usd:,.0f} USD "
            "(§4, sizing continuo sin lote mínimo; no mueve ninguna métrica en R)",
        )

    def closed_decisions(self) -> tuple[str, ...]:
        """Lo que el enunciado cierra y aquí no se discute."""
        return (
            "EL CONTACTO NO ES SEÑAL (fase 3.2): tocar una zona de H4 abre "
            "OBSERVACIÓN y nada más. Sólo hay operación con un RECHAZO en H4 o con "
            "una ROTURA CON RETESTEO. La rama «respeto operado a favor del ID por "
            "contacto» queda ELIMINADA.",
            "DIRECCIÓN POR RAMA (fase 3.2): UL rechazado -> EN CONTRA del ID de H4; "
            "UL roto y retesteado -> a favor de la rotura; OB rechazado -> a favor "
            "del ID; OB roto -> sin operación, la observación muere.",
            "RECHAZO EN H4 = dos formas y vale la que ocurra primero (fase 3.2, §2): "
            "A) una vela entra en la zona y cierra fuera de ella, del lado por el que "
            "entró; B) turtle soup de H4, dos velas consecutivas, la segunda llega a "
            "la mecha de la primera y cierra sin superarla, en la zona. SIN "
            "PARÁMETROS: ni umbrales, ni percentiles, ni proporciones.",
            "RETESTEO DEL OB = NO IMPLEMENTADO. El propietario lo ha aparcado a "
            "propósito. Es un pendiente conocido, no un olvido: hoy el OB roto mata "
            "la observación y no abre ninguna rama.",
            "CONFIRMACIÓN EN H1 = DOS VÍAS Y SÓLO DOS (fase 3.1): turtle soup, o "
            "ID de H1 con su OB formado y el precio llegando a ese OB. Un ID de H1 "
            "solo NO confirma. R1, R2 y R3 dejan de ser vías: se siguen calculando "
            "y persistiendo como columnas informativas y ninguna regla las lee.",
            "OBJETIVO = 1 : 3,3 R fijo, siempre (§3). Sin parciales, sin trailing, "
            "sin break-even. Equilibrio bruto en el 23,3 % de aciertos.",
            "STOP = versión 1, PRE-REGISTRADA antes de ver ningún resultado (§3): "
            "al otro lado de la zona de entrada, exactamente en su borde exterior, "
            "sin holgura añadida. Cualquier cambio posterior es la versión 2.",
            "EJECUCIÓN = open de la barra M1 siguiente a la decisión (§4).",
            "REGLA INTRA-BARRA = si el stop y el objetivo se tocan en la misma "
            "barra, gana el stop (§4). Conservadora por convención, no por dato.",
            "JERARQUÍA = si el Diario dice una cosa y H4 la contraria, manda H4 "
            "(§1.1). El conflicto se registra en la señal; no se descarta.",
        )


__all__ = ["UNTIL_OBSERVATION_ENDS", "EntriesConfig", "EntryCosts"]
