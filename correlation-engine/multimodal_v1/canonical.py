from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence, Set
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any


def canonical_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Payload canônico não aceita NaN ou infinito.")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime canônico deve ser timezone-aware.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Chaves do payload canônico devem ser strings.")
        return {key: canonical_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, Set):
        items = [canonical_json_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonical_json_value(item) for item in value]
    raise ValueError(f"Tipo não suportado no payload canônico: {type(value).__name__}.")


def deep_freeze_json(value: Any) -> Any:
    canonical = canonical_json_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType({key: deep_freeze_json(item) for key, item in canonical.items()})
    if isinstance(canonical, list):
        return tuple(deep_freeze_json(item) for item in canonical)
    return canonical

