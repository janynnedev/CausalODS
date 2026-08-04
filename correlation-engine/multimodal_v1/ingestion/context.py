from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib


@dataclass(frozen=True)
class NormalizationContext:
    source_reference: str
    ingested_timestamp: datetime
    source_dataset: str | None = None
    late_threshold_ms: float = 60_000.0
    future_tolerance_ms: float = 30_000.0
    topic: str | None = None
    partition: int | None = None
    offset: int | None = None
    message_timestamp: datetime | None = None
    key_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.source_reference.strip():
            raise ValueError("source_reference confiável não pode ser vazio.")
        for name in ("ingested_timestamp", "message_timestamp"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} deve possuir timezone.")
        if self.late_threshold_ms < 0 or self.future_tolerance_ms < 0:
            raise ValueError("Tolerâncias temporais não podem ser negativas.")

    @classmethod
    def now(cls, source_reference: str, **kwargs) -> "NormalizationContext":
        return cls(
            source_reference=source_reference,
            ingested_timestamp=datetime.now(timezone.utc),
            **kwargs,
        )

    @classmethod
    def from_kafka_message(cls, message, **kwargs) -> "NormalizationContext":
        key_hash = (
            hashlib.sha256(message.key).hexdigest()
            if isinstance(message.key, bytes) else None
        )
        message_time = (
            datetime.fromtimestamp(message.timestamp / 1000, tz=timezone.utc)
            if getattr(message, "timestamp", None) is not None else None
        )
        return cls.now(
            source_reference=f"kafka:{message.topic}:{message.partition}",
            topic=message.topic, partition=message.partition,
            offset=message.offset, message_timestamp=message_time,
            key_sha256=key_hash, **kwargs,
        )
