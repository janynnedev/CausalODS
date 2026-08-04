from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


def timestamp_from_nanos(value: int, fallback: datetime) -> datetime:
    if not value:
        return fallback
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc)


def any_value(value) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [any_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return key_values(value.kvlist_value.values)
    if kind == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, kind)


def key_values(values: Iterable) -> dict[str, Any]:
    return {item.key: any_value(item.value) for item in values}


def scope_dict(scope) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if getattr(scope, "name", ""):
        result["name"] = scope.name
    if getattr(scope, "version", ""):
        result["version"] = scope.version
    attrs = key_values(getattr(scope, "attributes", ()))
    if attrs:
        result["attributes"] = attrs
    return result


def stable_event_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def identity(attributes: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return (
        attributes.get("business.correlation.id"),
        attributes.get("business.entity.type"),
        attributes.get("business.entity.id"),
    )


def late_arrival_ms(event_timestamp: datetime, ingested_timestamp: datetime) -> float:
    return max(0.0, (ingested_timestamp - event_timestamp).total_seconds() * 1000)


def timing_quality(event_timestamp: datetime, ingested_timestamp: datetime,
                   *, late_threshold_ms: float,
                   future_tolerance_ms: float) -> tuple[float, tuple[str, ...]]:
    delta_ms = (ingested_timestamp - event_timestamp).total_seconds() * 1000
    flags = []
    if delta_ms > late_threshold_ms:
        flags.append("late_arrival")
    if delta_ms < -future_tolerance_ms:
        flags.append("clock_skew_future_event")
    return max(0.0, delta_ms), tuple(flags)
