from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Iterable, Mapping

from platform_v3.constants import OPERATIONAL_GROUND_TRUTH_FIELDS, ROOT, SECRET_FIELD_NAMES


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


SOURCE_SNAPSHOT_EXCLUDED_FILES = frozenset(
    {
        "artifacts/platform-v3/platform-validation-report.json",
        "artifacts/platform-v3/platform-validation-report.md",
        "artifacts/platform-v3/release-manifest.json",
        "artifacts/platform-v3/release-manifest.json.sha256",
        "artifacts/platform-v3/test-results.json",
        "artifacts/platform-v3/fault-e2e-live.json",
        "artifacts/platform-v3/fault-e2e-live.json.sha256",
    }
)
SOURCE_SNAPSHOT_EXCLUDED_PREFIXES = ("artifacts/platform-v3/models/",)
SOURCE_SNAPSHOT_EXCLUDED_PARTS = frozenset({"__pycache__", ".pytest_cache"})


def _tracked_files(repository_root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def _included_in_source_snapshot(relative_path: str) -> bool:
    canonical = PurePosixPath(relative_path).as_posix()
    parts = PurePosixPath(canonical).parts
    return not (
        canonical in SOURCE_SNAPSHOT_EXCLUDED_FILES
        or canonical.endswith(".pyc")
        or any(part in SOURCE_SNAPSHOT_EXCLUDED_PARTS for part in parts)
        or any(canonical.startswith(prefix) for prefix in SOURCE_SNAPSHOT_EXCLUDED_PREFIXES)
    )


def source_snapshot_entries(
    repository_root: Path = ROOT,
    *,
    tracked_files: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Return canonical path/content hashes for the current tracked worktree."""
    repository_root = Path(repository_root)
    relative_paths = _tracked_files(repository_root) if tracked_files is None else tracked_files
    entries = []
    for relative_path in sorted({PurePosixPath(item).as_posix() for item in relative_paths}):
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise ValueError(f"caminho rastreado inválido: {relative_path}")
        if not _included_in_source_snapshot(relative_path):
            continue
        path = repository_root.joinpath(*pure_path.parts)
        if not path.is_file():
            raise ValueError(f"arquivo rastreado ausente no snapshot: {relative_path}")
        entries.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return entries


def source_snapshot_sha256(
    repository_root: Path = ROOT,
    *,
    tracked_files: Iterable[str] | None = None,
) -> str:
    """Fingerprint tracked source/configuration without self-referential artifacts."""
    return sha256_value(
        source_snapshot_entries(repository_root, tracked_files=tracked_files)
    )


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
