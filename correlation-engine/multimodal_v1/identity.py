from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CORRELATION_ATTRIBUTE = "business.correlation.id"
ENTITY_TYPE_ATTRIBUTE = "business.entity.type"
ENTITY_ID_ATTRIBUTE = "business.entity.id"
MAX_DISTRIBUTED_ID_LENGTH = 128
_DISTRIBUTED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE_MARKER = re.compile(
    r"(bearer|token|secret|password|passwd|email|phone|cpf|ssn)", re.IGNORECASE
)


def normalize_distributed_id(value: Any) -> tuple[str | None, tuple[str, ...]]:
    """Valida IDs interoperáveis sem interromper ingestão de telemetria."""
    if value is None:
        return None, ()
    if not isinstance(value, str):
        return None, ("invalid_business_correlation_id",)
    candidate = value.strip()
    if (
        not candidate
        or not _DISTRIBUTED_ID.fullmatch(candidate)
        or _SENSITIVE_MARKER.search(candidate)
    ):
        return None, ("invalid_business_correlation_id",)
    return candidate, ()


def selected_identity_attributes(attributes: dict[str, Any]) -> dict[str, str]:
    """Copia somente atributos de identidade explicitamente permitidos."""
    selected: dict[str, str] = {}
    for key in (
        CORRELATION_ATTRIBUTE,
        ENTITY_TYPE_ATTRIBUTE,
        ENTITY_ID_ATTRIBUTE,
    ):
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            selected[key] = value.strip()
    return selected


@dataclass(frozen=True)
class PropagationContext:
    traceparent: str | None = None
    tracestate: str | None = None
    baggage: str | None = None
    correlation_id: str | None = None
    quality_flags: tuple[str, ...] = ()
