"""La vela vista *por cuerpo*: la única lectura que usa el módulo 1 (§2.1).

El impulso dominante se traza sobre `max(open, close)` y `min(open, close)`. Las
mechas existen en el dataset y las usará el módulo 2, pero aquí no se miran.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chronos.domain.structure.enums import BodyDirection, ImpulseDirection
from chronos.domain.structure.errors import StructureError


@dataclass(frozen=True, slots=True)
class BodyBar:
    """Cuerpo de una vela agregada, con su marca de tiempo en UTC."""

    timestamp: datetime
    open: float
    close: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise StructureError(f"La marca de tiempo {self.timestamp!r} no es tz-aware")

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def direction(self) -> BodyDirection:
        """Dirección del cuerpo. La igualdad es exacta: no hay tolerancia inventada."""
        if self.close > self.open:
            return BodyDirection.BULLISH
        if self.close < self.open:
            return BodyDirection.BEARISH
        return BodyDirection.DOJI

    def is_counter_to(self, direction: ImpulseDirection) -> bool:
        """`True` si el cuerpo va en dirección opuesta a `direction`.

        Sin umbral de tamaño: cualquier cuerpo contrario cuenta, por pequeño que
        sea (decisión explícita del propietario, §2.2).
        """
        body = self.direction
        if body is BodyDirection.DOJI:
            return False
        return body.as_impulse() is direction.opposite()

    def extreme_towards(self, direction: ImpulseDirection) -> float:
        """Punto del cuerpo más avanzado en `direction`."""
        return self.body_high if direction is ImpulseDirection.ALCISTA else self.body_low

    def anchor_towards(self, direction: ImpulseDirection) -> float:
        """Punto del cuerpo del que arrancaría una pierna en `direction`."""
        return self.body_low if direction is ImpulseDirection.ALCISTA else self.body_high
