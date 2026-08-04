from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from multimodal_v1.identity import normalize_distributed_id


CONFLICT_FLAG = "conflicting_business_correlation_ids"


@dataclass(frozen=True)
class IdentityContext:
    """Agregação opt-in de identidade para a futura pipeline v3."""

    correlation_id: str | None
    correlation_ids: tuple[str, ...]
    data_quality_flags: tuple[str, ...] = ()


def aggregate_correlation_ids(values: Iterable[object]) -> IdentityContext:
    valid: set[str] = set()
    flags: set[str] = set()
    for value in values:
        normalized, value_flags = normalize_distributed_id(value)
        flags.update(value_flags)
        if normalized is not None:
            valid.add(normalized)

    correlation_ids = tuple(sorted(valid))
    if len(correlation_ids) > 1:
        flags.add(CONFLICT_FLAG)
        correlation_id = None
    else:
        correlation_id = correlation_ids[0] if correlation_ids else None

    return IdentityContext(
        correlation_id=correlation_id,
        correlation_ids=correlation_ids,
        data_quality_flags=tuple(sorted(flags)),
    )
