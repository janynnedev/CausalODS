from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.dead_letter import sanitize_error_message
from .contracts import (AnalysisWindow, IncidentDescriptor, IncidentManifest, ReplayResult,
                        ValidationReport, canonical_json, sha256_value)

NORMALIZED_CACHE_VERSION = "normalized-public-dataset-cache-v2"


class KafkaReplayPublisher(Protocol):
    def send(self, topic: str, value: bytes, *, key: bytes,
             headers: Sequence[tuple[str, bytes]]) -> Any: ...


class DatasetAdapter(ABC):
    @abstractmethod
    def list_incidents(self) -> tuple[IncidentDescriptor, ...]: ...
    @abstractmethod
    def load_incident(self, incident_id: str) -> IncidentManifest: ...
    @abstractmethod
    def normalize_events(self, incident_id: str) -> tuple[UnifiedEvent, ...]: ...
    @abstractmethod
    def get_ground_truth(self, incident_id: str) -> Mapping[str, Any]: ...
    @abstractmethod
    def get_analysis_window(self, incident_id: str) -> AnalysisWindow: ...
    @abstractmethod
    def validate(self) -> ValidationReport: ...

    def replay_to_kafka(self, incident_id: str, publisher: KafkaReplayPublisher,
                        topics: Mapping[str, str] | None = None, *,
                        failure_policy: str = "fail-fast") -> ReplayResult:
        if failure_policy not in {"fail-fast", "continue"}:
            raise ValueError("failure_policy deve ser fail-fast ou continue.")
        configured = {"trace": "causalods.public.trace", "log": "causalods.public.log",
                      "metric": "causalods.public.metric",
                      "business_event": "causalods.public.business"} | dict(topics or {})
        for signal, topic in configured.items():
            if signal not in {"trace", "log", "metric", "business_event"}:
                raise ValueError(f"Signal topic desconhecido: {signal}")
            if not isinstance(topic, str) or not topic.strip() or any(ord(c) < 33 for c in topic):
                raise ValueError(f"Tópico inválido para {signal}.")
        attempted = confirmed = failed = 0
        errors = []
        for event in self.normalize_events(incident_id):
            attempted += 1
            try:
                acknowledgement = publisher.send(
                    configured[event.signal_type.value], event.to_json().encode("utf-8"),
                    key=event.event_id.encode("utf-8"),
                    headers=(("content-type", b"application/json"),
                             ("schema-version", b"unified-event-v1"),
                             ("source-dataset", event.source_dataset.encode("utf-8"))))
                if hasattr(acknowledgement, "get"):
                    acknowledgement.get()
                elif hasattr(acknowledgement, "result"):
                    acknowledgement.result()
                confirmed += 1
            except Exception as exc:  # publisher boundary: result records the external failure
                failed += 1
                errors.append(sanitize_error_message(
                    f"{event.event_id}:{type(exc).__name__}:{exc}"))
                if failure_policy == "fail-fast":
                    raise RuntimeError("Replay Kafka falhou em fail-fast.") from exc
        return ReplayResult(attempted, confirmed, failed, tuple(errors))


class NormalizedEventCache:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser().resolve() if path else None

    def load(self, key: str, *, adapter: str, adapter_version: str,
             configuration_sha256: str, source_files_sha256: Mapping[str, str]) -> tuple[UnifiedEvent, ...] | None:
        self._validate_metadata(key, adapter, adapter_version, configuration_sha256,
                                source_files_sha256)
        target = self.target(key)
        if target is None or not target.exists(): return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Cache normalizado ilegível.") from exc
        if not isinstance(payload, dict): raise RuntimeError("Cache envelope deve ser objeto.")
        expected = {"cache_version": NORMALIZED_CACHE_VERSION, "cache_key": key,
                    "adapter": adapter, "adapter_version": adapter_version,
                    "configuration_sha256": configuration_sha256,
                    "source_files_sha256": dict(sorted(source_files_sha256.items()))}
        for name, value in expected.items():
            if payload.get(name) != value:
                raise RuntimeError(f"Cache incompatível: {name}.")
        raw_events = payload.get("events")
        if not isinstance(raw_events, list) or payload.get("event_count") != len(raw_events):
            raise RuntimeError("Cache adulterado: event_count.")
        if payload.get("events_sha256") != sha256_value(raw_events):
            raise RuntimeError("Cache adulterado: events_sha256.")
        try:
            return tuple(UnifiedEvent.from_dict(item) for item in raw_events)
        except (TypeError, ValueError, KeyError) as exc:
            raise RuntimeError("Cache contém UnifiedEvent inválido.") from exc

    def store(self, key: str, events: tuple[UnifiedEvent, ...], *, adapter: str,
              adapter_version: str, configuration_sha256: str,
              source_files_sha256: Mapping[str, str]) -> None:
        self._validate_metadata(key, adapter, adapter_version, configuration_sha256,
                                source_files_sha256)
        if not isinstance(events, tuple) or any(not isinstance(item, UnifiedEvent) for item in events):
            raise TypeError("Cache aceita tuple de UnifiedEvent.")
        target = self.target(key)
        if target is None: return
        target.parent.mkdir(parents=True, exist_ok=True)
        raw_events = [event.to_dict() for event in events]
        envelope = {"cache_version": NORMALIZED_CACHE_VERSION, "cache_key": key,
                    "adapter": adapter, "adapter_version": adapter_version,
                    "configuration_sha256": configuration_sha256,
                    "source_files_sha256": dict(sorted(source_files_sha256.items())),
                    "event_count": len(raw_events), "events_sha256": sha256_value(raw_events),
                    "events": raw_events}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                             prefix=f".{target.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical_json(envelope))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary and temporary.exists(): temporary.unlink()

    def target(self, key: str) -> Path | None:
        if not self._is_sha(key): raise ValueError("cache key deve ser SHA-256 hexadecimal.")
        if self.path is None: return None
        candidate = (self.path / f"{key}.json").resolve()
        try: candidate.relative_to(self.path)
        except ValueError as exc: raise ValueError("Cache target escapou da raiz.") from exc
        return candidate

    @staticmethod
    def _is_sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    @classmethod
    def _validate_metadata(cls, key, adapter, adapter_version, configuration_sha256,
                           source_files_sha256):
        if not cls._is_sha(key) or not cls._is_sha(configuration_sha256):
            raise ValueError("Cache key/configuration_sha256 inválido.")
        if not isinstance(adapter, str) or not adapter.strip() or not isinstance(adapter_version, str) or not adapter_version.strip():
            raise ValueError("Cache requer adapter e adapter_version textuais.")
        if not isinstance(source_files_sha256, Mapping) or any(
                not isinstance(path, str) or not path or not cls._is_sha(value)
                for path, value in source_files_sha256.items()):
            raise ValueError("source_files_sha256 inválido.")
