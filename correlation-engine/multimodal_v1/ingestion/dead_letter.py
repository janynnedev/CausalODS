from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from multimodal_v1.ingestion.context import NormalizationContext


_HEADER_SECRET = re.compile(
    r"(?im)\b(authorization|cookie|password|token)\s*:\s*[^\r\n;|]+"
)
_AUTH_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie)\s*=\s*[^&;\r\n,|]+"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|password|email)=)[^&#\s]*"
)
_VALUE_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|email)\s*=\s*[^\s,;&|]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;|]+")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def sanitize_error_message(message: str) -> str:
    sanitized = _HEADER_SECRET.sub(r"\1: [REDACTED]", message)
    sanitized = _AUTH_ASSIGNMENT.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _QUERY_SECRET.sub(r"\1[REDACTED]", sanitized)
    sanitized = _VALUE_ASSIGNMENT.sub(r"\1=[REDACTED]", sanitized)
    sanitized = _BEARER.sub("Bearer [REDACTED]", sanitized)
    sanitized = _EMAIL.sub("[REDACTED_EMAIL]", sanitized)
    return sanitized[:1024]


@dataclass(frozen=True)
class DeadLetterRecord:
    schema_version: str
    signal_type: str
    source_reference: str
    topic: str | None
    partition: int | None
    offset: int | None
    payload_sha256: str
    original_size_bytes: int
    error_type: str
    error_message: str
    rejected_at: str
    payload_base64: str | None = None
    payload_truncated: bool = False

    @classmethod
    def create(cls, signal_type: str, context: NormalizationContext,
               payload: bytes, error: Exception, *, include_payload: bool = False,
               max_payload_bytes: int = 4096) -> "DeadLetterRecord":
        if max_payload_bytes < 0:
            raise ValueError("max_payload_bytes não pode ser negativo.")
        included = payload[:max_payload_bytes] if include_payload else None
        return cls(
            schema_version="multimodal-dead-letter-v1",
            signal_type=signal_type, source_reference=context.source_reference,
            topic=context.topic, partition=context.partition, offset=context.offset,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            original_size_bytes=len(payload),
            error_type=type(error).__name__,
            error_message=sanitize_error_message(str(error)),
            rejected_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            payload_base64=(
                base64.b64encode(included).decode("ascii")
                if included is not None else None
            ),
            payload_truncated=include_payload and len(payload) > max_payload_bytes,
        )

    def to_json(self) -> bytes:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


class DeadLetterPublisher(Protocol):
    def publish(self, record: DeadLetterRecord) -> None: ...


class InMemoryDeadLetterPublisher:
    def __init__(self) -> None:
        self.records: list[DeadLetterRecord] = []

    def publish(self, record: DeadLetterRecord) -> None:
        self.records.append(record)


class KafkaDeadLetterPublisher:
    def __init__(self, producer, topic: str) -> None:
        if not topic.strip():
            raise ValueError("Dead-letter topic não pode ser vazio.")
        self._producer = producer
        self._topic = topic

    def publish(self, record: DeadLetterRecord) -> None:
        # Confirma publicação antes de permitir commit do offset de origem.
        self._producer.send(
            self._topic, value=record.to_json()
        ).get(timeout=10)
