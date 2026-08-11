from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from platform_v3.constants import OPERATIONAL_GROUND_TRUTH_FIELDS, SECRET_FIELD_NAMES


def plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Documentos de visualização não aceitam NaN/Infinity.")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps devem ser timezone-aware.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [plain(item) for item in value]
    if hasattr(value, "to_dict"):
        return plain(value.to_dict())
    if is_dataclass(value):
        return {item.name: plain(getattr(value, item.name)) for item in fields(value)}
    raise TypeError(f"Valor não serializável: {type(value).__name__}.")


def canonical_json(value: Any) -> str:
    return json.dumps(
        plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize(value: Any, *, evaluation: bool = False) -> Any:
    """Remove secrets e, em projeções operacionais, qualquer ground truth."""
    value = plain(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = key.strip().lower()
            if normalized in SECRET_FIELD_NAMES:
                continue
            if not evaluation and normalized in OPERATIONAL_GROUND_TRUTH_FIELDS:
                continue
            result[key] = sanitize(item, evaluation=evaluation)
        return result
    if isinstance(value, list):
        return [sanitize(item, evaluation=evaluation) for item in value]
    return value


def arbitrary_json(value: Any, *, evaluation: bool = False) -> str:
    """Representação segura para dados livres; é armazenada sem indexação."""
    return canonical_json(sanitize(value, evaluation=evaluation))
