from __future__ import annotations

import csv
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

DURATION_POLICY_VERSION = "public-dataset-duration-policy-v1"
TIMESTAMP_POLICY_VERSION = "magnitude-or-iso8601-to-utc-v1"


def duration_attributes(value: Any, *, column: str, unit: str | None,
                        factor_to_ms: float | None) -> tuple[dict[str, Any], tuple[str, ...]]:
    attributes = {"duration_raw": value, "duration_raw_unit": unit or "unknown",
                  "duration_source_column": column,
                  "duration_policy_version": DURATION_POLICY_VERSION}
    try:
        number = finite_float(value)
    except (TypeError, ValueError):
        return attributes, ("invalid_trace_duration", "missing_normalized_duration")
    if number < 0:
        return attributes, ("negative_trace_duration", "missing_normalized_duration")
    if factor_to_ms is None:
        return attributes, ("unknown_trace_duration_unit", "missing_normalized_duration")
    normalized = number * factor_to_ms
    if not math.isfinite(normalized):
        return attributes, ("invalid_trace_duration", "missing_normalized_duration")
    attributes["duration_ms"] = normalized
    return attributes, ()


def source_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def event_id(dataset: str, relative_path: str, row: int, discriminator: str = "") -> str:
    raw = f"{dataset}\0{relative_path}\0{row}\0{discriminator}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Timestamp ingênuo não é aceito.")
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        raise ValueError("Timestamp vazio.")
    try:
        numeric = float(text)
    except ValueError:
        candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    magnitude = abs(numeric)
    divisor = 1_000_000_000 if magnitude >= 1e18 else (
        1_000_000 if magnitude >= 1e15 else (1_000 if magnitude >= 1e12 else 1))
    return datetime.fromtimestamp(numeric / divisor, tz=timezone.utc)


def finite_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Valor métrico não finito.")
    return number


def read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"CSV sem cabeçalho: {path}")
        return tuple(reader.fieldnames), list(reader)


def first(row: Mapping[str, Any], names: Iterable[str]) -> tuple[str | None, str | None]:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip(), name
    return None, None


def valid_hex(value: str | None, lengths: tuple[int, ...]) -> str | None:
    if value and len(value) in lengths and all(char in "0123456789abcdefABCDEF" for char in value):
        return value.lower()
    return None
