from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENSEARCH_URL = "http://localhost:9200"
RCA_INDEX = "causalods-rca-results"
INCIDENT_INDEX = "causalods-incidents"
KAFKA_CONTAINER = "kafka"
KAFKA_TOPIC = "traces"
CONSUMER_GROUP = "correlation-engine"
CORRELATION_CONTAINER = "correlation-engine"
OPENSEARCH_CONTAINER = "causalods-opensearch"

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def timestamp_now() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def run_command(
    arguments: list[str],
    *,
    check: bool = True,
    timeout: float | None = None,
    cwd: Path = REPOSITORY_ROOT,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            "Comando falhou:\n"
            f"{' '.join(arguments)}\n\n"
            f"STDOUT:\n{completed.stdout}\n\n"
            f"STDERR:\n{completed.stderr}"
        )
    return completed


def http_json(
    method: str,
    url: str,
    *,
    payload: Any = None,
    timeout: float = 10.0,
    allow_http_error: bool = False,
) -> tuple[int, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url=url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw.strip() else None
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed: Any
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            parsed = raw
        if allow_http_error:
            return exc.code, parsed
        raise RuntimeError(f"HTTP {exc.code} em {url}: {raw}") from exc
    except (URLError, TimeoutError, ConnectionResetError, OSError) as exc:
        raise RuntimeError(
            f"Falha ao acessar {url}: {type(exc).__name__}: {exc}"
        ) from exc


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_manifest(root: Path, name: str = "SHA256SUMS") -> Path:
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name != name
    )
    manifest = root / name
    manifest.write_text(
        "\n".join(
            f"{sha256_file(path)}  {path.relative_to(root)}"
            for path in paths
        ) + "\n",
        encoding="utf-8",
    )
    return manifest


def first_value(document: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        current: Any = document
        for item in path:
            if not isinstance(current, dict) or item not in current:
                current = None
                break
            current = current[item]
        if current is not None:
            return current
    return None
