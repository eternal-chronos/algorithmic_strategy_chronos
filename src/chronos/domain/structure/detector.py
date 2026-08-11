"""Máquina de estados del impulso dominante (§2.3, §2.4, §2.6).

Un impulso dominante nace en **dos momentos**:

1. **Rotura** — una pierna cierra más allá de uno de los dos límites del ID
   vigente. Eso mata el ID anterior y *no* crea ninguno. Se entra en LIMBO.
2. **Constitución** — cierra la primera vela contraria a esa pierna. Ahí, y sólo
   ahí, nace el nuevo ID, con el extremo por cuerpo alcanzado hasta la vela
   anterior a la contraria.

De esa regla sale gratis la inmunidad al lookahead: el extremo no se conoce
hasta que cierra la vela contraria, así que no hay nada que "desplazar".

Decisiones de orden y de borde, todas cubiertas por tests:

- **Rotura antes que constitución.** El estado se evalúa al *principio* de la
  barra: una barra que rompe sólo rompe. La constitución puede ocurrir como muy
  pronto en la barra siguiente. Así, una barra que rompe y además es contraria a
  la pierna que queda en curso no hace las dos cosas a la vez.
- **Rotura estricta.** "Cerrar más allá" es desigualdad estricta; tocar el nivel
  exacto no rompe.
- **Arranque de la pierna.** Es el primer elemento de la racha contigua de velas
  en la dirección de la pierna (los dojis no la cortan) que termina en la barra
  de la rotura. Si la propia barra de rotura ya es contraria a la pierna nueva,
  la pierna arranca en ella.
- **Doji.** Cuerpo nulo: ni constituye ni corta rachas. Si además puede romper
  depende de `DojiBreakMode`, que es un parámetro abierto.

R-36: la vela de la rotura es la única de la pierna que se adopta sin comprobar
su color —dentro de la pierna una vela contraria constituiría el ID en el acto,
así que nunca llega a fijar el extremo— y por esa puerta de atrás entraban
extremos sobre velas del color contrario a su impulso. `LegStartMode` enumera las
tres lecturas de ese borde; la decisión es del propietario. Los tres modos se
describen uno a uno en `_open_leg`, `_extend_leg` y `_on_bar_in_limbo`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from chronos.domain.structure.body import BodyBar
from chronos.domain.structure.enums import (
    AnchorMode,
    BodyDirection,
    BreakKind,
    DojiBreakMode,
    ImpulseDirection,
    LegStartMode,
    MachineState,
    SeedMode,
)
from chronos.domain.structure.errors import LookaheadError, StructureError
from chronos.domain.structure.impulse import BarState, BreakEvent, DominantImpulse

DEFAULT_WARMUP_BARS = 50


@dataclass(slots=True)
class _Leg:
    """Pierna en curso durante el limbo. Estado interno, no sale del detector."""

    direction: ImpulseDirection
    start_index: int
    #: `None` mientras ninguna vela válida haya fijado el extremo. Sólo ocurre en
    #: `L3`, donde una vela de color contrario no puede fijarlo: hasta que llegue
    #: una del color correcto la pierna no encierra nada y no puede constituir.
    extreme: float | None
    #: Barra que fijó el extremo vigente. Con empate manda la primera.
    extreme_index: int | None
    #: Índice de la barra de rotura que abrió este limbo; `None` si el limbo es
    #: el inicial del histórico (no hubo rotura previa).
    break_index: int | None
    #: Primera barra que cuenta como limbo. Sirve para `n_barras_limbo`.
    limbo_start_index: int
    #: Desde dónde se busca hacia atrás el ancla A1. Es el arranque de la pierna
    #: salvo en `L2`, donde la vela de la rotura tampoco puede aportar el ancla.
    anchor_search_before: int


@dataclass(frozen=True, slots=True)
class _PendingLeg:
    """Pierna que en `L2` todavía no ha arrancado: falta la barra siguiente."""

    direction: ImpulseDirection
    break_index: int


class DominantImpulseDetector:
    """Detecta impulsos dominantes sobre una serie de cuerpos de una temporalidad.

    Se alimenta barra a barra y en orden cronológico. Un ID sólo se rompe con
    cierres de su propia temporalidad: nunca mezcles series aquí.
    """

    def __init__(
        self,
        *,
        timeframe: str,
        anchor_mode: AnchorMode = AnchorMode.A2_FIRST_LEG_BAR,
        seed_mode: SeedMode = SeedMode.S2_FIRST_COUNTER_BAR,
        doji_break_mode: DojiBreakMode = DojiBreakMode.D1_NEUTRAL,
        leg_start_mode: LegStartMode = LegStartMode.L1_CURRENT,
        warmup_bars: int = DEFAULT_WARMUP_BARS,
    ) -> None:
        if warmup_bars < 0:
            raise StructureError("warmup_bars no puede ser negativo")
        self._timeframe = timeframe
        self._anchor_mode = anchor_mode
        self._seed_mode = seed_mode
        self._doji_break_mode = doji_break_mode
        self._leg_start_mode = leg_start_mode
        self._warmup_bars = warmup_bars

        self._bars: list[BodyBar] = []
        self._state = MachineState.LIMBO
        self._current: DominantImpulse | None = None
        self._leg: _Leg | None = None
        self._pending: _PendingLeg | None = None
        self._seed_direction: ImpulseDirection | None = None
        self._next_id = 1

        self._impulses: list[DominantImpulse] = []
        self._events: list[BreakEvent] = []
        self._states: list[BarState] = []
        self._diagnostics: dict[str, int] = {
            "dojis": 0,
            "roturas_a_favor": 0,
            "roturas_en_contra": 0,
            "impulsos_en_calentamiento": 0,
            "impulsos_sin_ancla_a1": 0,
            "impulsos_rango_no_positivo": 0,
            "dojis_en_nivel_de_rotura": 0,
            #: R-36. Piernas cuya primera vela va en contra de la propia pierna.
            #: Es la puerta de atrás que abrió el defecto y se cuenta en los tres
            #: modos: en `L1` mide cuántos extremos pueden salir mal, en `L2` y
            #: `L3` mide cuántas veces el modo entra en juego.
            "piernas_arrancadas_en_vela_contraria": 0,
            #: `L3`: veces que una vela contraria no pudo fijar el extremo.
            "extremos_rechazados_por_color": 0,
            #: `L3`: velas contrarias que llegaron antes de que la pierna tuviera
            #: extremo válido y por tanto no constituyeron ningún ID.
            "constituciones_aplazadas_sin_extremo": 0,
        }

    # --- Alimentación -------------------------------------------------------

    def process(self, bar: BodyBar) -> None:
        """Consume una barra ya cerrada. El orden cronológico es obligatorio."""
        if self._bars and bar.timestamp <= self._bars[-1].timestamp:
            raise StructureError(
                f"Barra fuera de orden: {bar.timestamp} no es posterior a {self._bars[-1].timestamp}"
            )
        self._bars.append(bar)
        index = len(self._bars) - 1

        if bar.direction is BodyDirection.DOJI:
            self._diagnostics["dojis"] += 1

        if self._state is MachineState.ID_VIGENTE:
            self._on_bar_with_impulse(index, bar)
        elif self._pending is not None:
            # L2: la rotura dejó la pierna en el aire y ésta es su primera vela.
            self._start_pending_leg(index)
        elif self._leg is None:
            self._seed(index, bar)
        else:
            self._on_bar_in_limbo(index, bar)

        self._states.append(self._snapshot(index, bar))

    def process_all(self, bars: Iterable[BodyBar]) -> None:
        for bar in bars:
            self.process(bar)

    # --- Consulta -----------------------------------------------------------

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def state(self) -> MachineState:
        return self._state

    @property
    def bars_processed(self) -> int:
        return len(self._bars)

    @property
    def impulses(self) -> tuple[DominantImpulse, ...]:
        return tuple(self._impulses)

    @property
    def published_impulses(self) -> tuple[DominantImpulse, ...]:
        return tuple(impulse for impulse in self._impulses if impulse.publishable)

    @property
    def events(self) -> tuple[BreakEvent, ...]:
        return tuple(self._events)

    @property
    def states(self) -> tuple[BarState, ...]:
        return tuple(self._states)

    @property
    def diagnostics(self) -> dict[str, int]:
        return dict(self._diagnostics)

    @property
    def current_impulse(self) -> DominantImpulse | None:
        """El ID vigente, o `None` si el sistema está en limbo."""
        return self._current

    def current_impulse_extreme(self) -> float:
        """Extremo del ID vigente.

        En limbo **no existe** un extremo publicable: la pierna sigue avanzando y
        su extremo definitivo sólo se conoce cuando cierra la vela contraria.
        Preguntarlo antes es mirar al futuro.
        """
        if self._current is None:
            raise LookaheadError(
                f"[{self._timeframe}] No hay ID constituido (estado {self._state.value}): "
                "el extremo de la pierna en curso todavía no está fijado"
            )
        return self._current.extreme

    def state_at(self, timestamp: datetime) -> BarState:
        """Estado publicado en el cierre de barra vigente en `timestamp`."""
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise StructureError(f"La marca de tiempo {timestamp!r} no es tz-aware")
        if not self._states:
            raise StructureError("Todavía no se ha procesado ninguna barra")
        frontier = self._states[-1].timestamp
        if timestamp > frontier:
            raise LookaheadError(
                f"[{self._timeframe}] Se pidió el estado en {timestamp}, "
                f"posterior a la última barra procesada ({frontier})"
            )
        if timestamp < self._states[0].timestamp:
            raise StructureError(
                f"[{self._timeframe}] {timestamp} es anterior al inicio de la serie"
            )
        # Última barra cerrada en o antes de `timestamp`.
        low, high = 0, len(self._states) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if self._states[middle].timestamp <= timestamp:
                low = middle
            else:
                high = middle - 1
        return self._states[low]

    # --- Transiciones -------------------------------------------------------

    def _seed(self, index: int, bar: BodyBar) -> None:
        """Arranque del histórico: decide qué pierna está en curso (`SeedMode`)."""
        body = bar.direction
        if body is BodyDirection.DOJI:
            return

        direction = body.as_impulse()
        if self._seed_mode is SeedMode.S1_FIRST_NON_DOJI:
            self._open_leg(direction, start_index=index, break_index=None, limbo_start_index=0)
            return

        # S2: se ignora el tramo inicial, cuyo arranque no está en los datos, y
        # la pierna la abre la primera vela contraria a ese tramo.
        if self._seed_direction is None:
            self._seed_direction = direction
            return
        if direction is self._seed_direction.opposite():
            self._open_leg(direction, start_index=index, break_index=None, limbo_start_index=0)

    def _on_bar_with_impulse(self, index: int, bar: BodyBar) -> None:
        """Estado ID_VIGENTE: o rompe por uno de los dos límites, o es retroceso."""
        impulse = self._current
        assert impulse is not None  # invariante del estado ID_VIGENTE

        beyond_extreme = self._is_beyond(bar.close, impulse.extreme, impulse.direction)
        beyond_anchor = self._is_beyond(
            bar.close, impulse.anchor, impulse.direction.opposite()
        )
        if bar.direction is BodyDirection.DOJI and (beyond_extreme or beyond_anchor):
            self._diagnostics["dojis_en_nivel_de_rotura"] += 1
            if self._doji_break_mode is DojiBreakMode.D1_NEUTRAL:
                return  # §2.2: el doji no rompe nada

        if beyond_extreme:
            kind, level, new_direction = BreakKind.A_FAVOR, impulse.extreme, impulse.direction
        elif beyond_anchor:
            kind, level, new_direction = (
                BreakKind.EN_CONTRA,
                impulse.anchor,
                impulse.direction.opposite(),
            )
        else:
            return  # retroceso dentro del rango: no pasa nada

        impulse.close(timestamp=bar.timestamp, index=index, kind=kind)
        self._events.append(
            BreakEvent(
                kind=kind,
                timestamp=bar.timestamp,
                index=index,
                timeframe=self._timeframe,
                broken_id_num=impulse.id_num,
                broken_direction=impulse.direction,
                new_leg_direction=new_direction,
                close=bar.close,
                level=level,
            )
        )
        self._diagnostics[
            "roturas_a_favor" if kind is BreakKind.A_FAVOR else "roturas_en_contra"
        ] += 1
        self._current = None

        if self._leg_start_mode is LegStartMode.L2_NEXT_BAR:
            # L2: esta barra sólo rompe. La pierna no existe todavía porque su
            # primera vela es la siguiente, que aún no ha cerrado; hasta entonces
            # no hay ni ancla ni extremo que calcular.
            self._leg = None
            self._pending = _PendingLeg(direction=new_direction, break_index=index)
            self._state = MachineState.LIMBO
            return

        self._open_leg(
            new_direction,
            start_index=self._leg_start_index(index, new_direction),
            break_index=index,
            limbo_start_index=index + 1,
        )

    def _start_pending_leg(self, index: int) -> None:
        """L2: abre en `index` la pierna que dejó pendiente la barra de rotura.

        Esta vela es la primera de la pierna y, como la de la rotura en `L1`, se
        adopta sin mirar su color: no se evalúa como contraria y por tanto no
        constituye. Si viene en contra de la pierna, el extremo vuelve a salir de
        una vela del color equivocado —el defecto se ha movido una barra, no ha
        desaparecido— y eso se cuenta para poder decirlo con números.
        """
        pending = self._pending
        assert pending is not None  # invariante de la rama que llama
        self._pending = None
        self._open_leg(
            pending.direction,
            start_index=index,
            break_index=pending.break_index,
            limbo_start_index=pending.break_index + 1,
        )

    def _on_bar_in_limbo(self, index: int, bar: BodyBar) -> None:
        """Estado LIMBO: la primera vela contraria a la pierna constituye el ID."""
        leg = self._leg
        assert leg is not None  # invariante del estado LIMBO con pierna abierta

        if bar.is_counter_to(leg.direction):
            if leg.extreme is None:
                # L3 y sólo L3: la pierna aún no tiene extremo de color válido,
                # así que no encierra nada que se pueda constituir. Se deja pasar
                # la contraria como si fuera un doji y el limbo sigue. Constituir
                # aquí exigiría fijar el extremo con una vela del color prohibido,
                # que es justo lo que este modo impide.
                self._diagnostics["constituciones_aplazadas_sin_extremo"] += 1
                return
            # §2.3: el extremo se fija con lo alcanzado *hasta la vela anterior*,
            # así que la contraria no lo actualiza aunque su cuerpo lo supere.
            self._constitute(index, bar, leg, extreme=leg.extreme)
            return

        self._extend_leg(leg, index, bar)

    def _constitute(self, index: int, bar: BodyBar, leg: _Leg, *, extreme: float) -> None:
        anchor_a2 = self._bars[leg.start_index].anchor_towards(leg.direction)
        counter_index = self._last_counter_index_before(
            leg.anchor_search_before, leg.direction
        )
        anchor_a1 = (
            self._bars[counter_index].anchor_towards(leg.direction)
            if counter_index is not None
            else None
        )
        anchor = anchor_a1 if self._anchor_mode is AnchorMode.A1_LAST_COUNTER_BODY else anchor_a2

        publishable = index >= self._warmup_bars
        if index < self._warmup_bars:
            self._diagnostics["impulsos_en_calentamiento"] += 1
        if anchor_a1 is None:
            self._diagnostics["impulsos_sin_ancla_a1"] += 1
        if anchor is None:
            # Sólo ocurre con ANCHOR_MODE=A1 al principio del histórico: no hay
            # vela contraria anterior de la que sacar el ancla. No se inventa un
            # sustituto; el impulso existe pero no se publica.
            publishable = False
            anchor = anchor_a2

        break_bar = self._bars[leg.break_index] if leg.break_index is not None else None
        # Barra de la que sale el ancla *activa*: la del arranque de la pierna en
        # A2, la última contraria previa en A1. Es lo que el explorador necesita
        # para pegar cada línea a la vela que la define, y evita reconstruirlo a
        # posteriori buscando qué cuerpo coincide con el precio.
        anchor_index = (
            counter_index
            if self._anchor_mode is AnchorMode.A1_LAST_COUNTER_BODY and counter_index is not None
            else leg.start_index
        )
        extreme_index = leg.extreme_index
        assert extreme_index is not None  # el extremo y su barra se fijan juntos
        impulse = DominantImpulse(
            id_num=self._next_id,
            timeframe=self._timeframe,
            direction=leg.direction,
            ts_previous_break=break_bar.timestamp if break_bar is not None else None,
            ts_constitution=bar.timestamp,
            index_constitution=index,
            ts_leg_start=self._bars[leg.start_index].timestamp,
            index_leg_start=leg.start_index,
            anchor=anchor,
            extreme=extreme,
            anchor_a1=anchor_a1,
            anchor_a2=anchor_a2,
            index_anchor=anchor_index,
            ts_anchor=self._bars[anchor_index].timestamp,
            index_extreme=extreme_index,
            ts_extreme=self._bars[extreme_index].timestamp,
            extreme_bar_direction=self._bars[extreme_index].direction,
            limbo_bars=index - leg.limbo_start_index,
            constituting_body_size=bar.body_size,
            publishable=publishable,
        )
        if impulse.range_usd <= 0:
            self._diagnostics["impulsos_rango_no_positivo"] += 1

        self._next_id += 1
        self._impulses.append(impulse)
        self._current = impulse
        self._leg = None
        self._state = MachineState.ID_VIGENTE

    # --- Utilidades ---------------------------------------------------------

    def _open_leg(
        self,
        direction: ImpulseDirection,
        *,
        start_index: int,
        break_index: int | None,
        limbo_start_index: int,
    ) -> None:
        # En L2 la vela de la rotura queda fuera de la pierna a todos los efectos,
        # también como candidata a ancla A1: "ni el ancla ni el extremo pueden
        # salir de la vela que rompió".
        anchor_search_before = start_index
        if self._leg_start_mode is LegStartMode.L2_NEXT_BAR and break_index is not None:
            anchor_search_before = break_index
        if self._bars[start_index].is_counter_to(direction):
            self._diagnostics["piernas_arrancadas_en_vela_contraria"] += 1

        leg = _Leg(
            direction=direction,
            start_index=start_index,
            extreme=None,
            extreme_index=None,
            break_index=break_index,
            limbo_start_index=limbo_start_index,
            anchor_search_before=anchor_search_before,
        )
        # El arranque entra por la misma puerta que el resto: es la única forma de
        # que L3 pueda vetarlo por color sin duplicar la regla en dos sitios.
        for offset, bar in enumerate(self._bars[start_index:], start=start_index):
            self._extend_leg(leg, offset, bar)
        self._leg = leg
        self._state = MachineState.LIMBO

    def _extend_leg(self, leg: _Leg, index: int, bar: BodyBar) -> None:
        """Estira el extremo de la pierna con lo que alcanza esta vela.

        En `L3` una vela de color contrario a la pierna no puede fijarlo. Dentro
        de la pierna eso sólo puede pasarle a la vela del arranque —cualquier otra
        contraria habría constituido el ID—, que es exactamente el borde de R-36.
        El doji sí lo fija: §2.2 lo declara neutro en todo el módulo y convertirlo
        aquí en "contrario" sería inventar una regla que nadie ha pedido.
        """
        if self._leg_start_mode is LegStartMode.L3_VALID_COLOUR_EXTREME and bar.is_counter_to(
            leg.direction
        ):
            self._diagnostics["extremos_rechazados_por_color"] += 1
            return

        reached = bar.extreme_towards(leg.direction)
        # Estricto: con empate manda la primera vela que llegó al nivel.
        improves = leg.extreme is None or (
            reached > leg.extreme
            if leg.direction is ImpulseDirection.ALCISTA
            else reached < leg.extreme
        )
        if improves:
            leg.extreme = reached
            leg.extreme_index = index

    @staticmethod
    def _is_beyond(price: float, level: float, direction: ImpulseDirection) -> bool:
        """"Más allá" es estricto: cerrar justo en el nivel no rompe."""
        return price > level if direction is ImpulseDirection.ALCISTA else price < level

    def _leg_start_index(self, break_index: int, direction: ImpulseDirection) -> int:
        """Arranque de la racha contigua en `direction` que acaba en `break_index`.

        Los dojis no cortan la racha. Si la propia barra de rotura ya es
        contraria a la pierna nueva, la pierna arranca ahí mismo.
        """
        if self._bars[break_index].is_counter_to(direction):
            return break_index
        start = break_index
        while start > 0 and not self._bars[start - 1].is_counter_to(direction):
            start -= 1
        return start

    def _last_counter_index_before(
        self, start_index: int, direction: ImpulseDirection
    ) -> int | None:
        """Última vela contraria a `direction` anterior a `start_index` (salta dojis)."""
        for index in range(start_index - 1, -1, -1):
            if self._bars[index].is_counter_to(direction):
                return index
        return None

    def _snapshot(self, index: int, bar: BodyBar) -> BarState:
        # En L2, entre la rotura y la vela siguiente la pierna está pendiente pero
        # su dirección ya se conoce: es la que fija la rotura. Publicar `None` ahí
        # diría que el sistema no sabe hacia dónde va, y sí lo sabe.
        leg_direction = self._leg.direction if self._leg is not None else None
        if leg_direction is None and self._pending is not None:
            leg_direction = self._pending.direction
        return BarState(
            index=index,
            timestamp=bar.timestamp,
            timeframe=self._timeframe,
            state=self._state,
            impulse_id=self._current.id_num if self._current is not None else None,
            leg_direction=leg_direction,
        )
