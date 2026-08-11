"""Errores del módulo de estructura."""

from __future__ import annotations

from chronos.domain.errors import DomainError


class StructureError(DomainError):
    """La estructura de mercado se consultó o se alimentó de forma inválida."""


class LookaheadError(StructureError):
    """Se intentó leer información que aún no está disponible en el instante pedido.

    No es un error "defensivo" decorativo: es la única forma de que un fallo de
    causalidad se manifieste como una excepción y no como un backtest optimista.
    Devolver `None` o un valor por defecto escondería el problema.
    """
