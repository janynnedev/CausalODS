from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.errors import SemanticInputError


def unified_event_from_input(**values: Any) -> UnifiedEvent:
    """Converte somente violações do contrato de entrada em erro esperado."""
    try:
        return UnifiedEvent(**values)
    except ValueError as exception:
        raise SemanticInputError(
            f"Evento de entrada viola UnifiedEvent v1: {exception}"
        ) from exception


def unified_event_dict_from_input(payload: Mapping[str, Any]) -> UnifiedEvent:
    try:
        return UnifiedEvent.from_dict(payload)
    except ValueError as exception:
        raise SemanticInputError(
            f"Evento normalizado viola UnifiedEvent v1: {exception}"
        ) from exception
