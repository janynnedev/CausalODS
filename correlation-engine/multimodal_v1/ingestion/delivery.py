from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.deduplication import DeduplicationKey

StreamKey = tuple[str, ...]


@dataclass(frozen=True)
class WatermarkUpdate:
    stream_key: StreamKey
    timestamp: datetime


@dataclass(frozen=True)
class MessageMetadata:
    topic: str | None
    partition: int | None
    offset: int | None
    message_timestamp: datetime | None
    key_sha256: str | None
    source_reference: str


@dataclass(frozen=True)
class PreparedBatch:
    events: tuple[UnifiedEvent, ...]
    deduplication_keys: tuple[DeduplicationKey, ...]
    watermark_updates: tuple[WatermarkUpdate, ...]
    message: MessageMetadata
    rejected_to_dead_letter: bool = False


class BatchSink(Protocol):
    """Sink idempotente por identidade do evento; falhas devem levantar exceção."""

    def deliver(self, events: tuple[UnifiedEvent, ...]) -> None: ...
