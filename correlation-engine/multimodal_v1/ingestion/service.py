from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from google.protobuf.message import DecodeError
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.dead_letter import DeadLetterPublisher, DeadLetterRecord
from multimodal_v1.ingestion.deduplication import DeduplicationKey, EventDeduplicator
from multimodal_v1.ingestion.delivery import (
    BatchSink, MessageMetadata, PreparedBatch, StreamKey, WatermarkUpdate,
)
from multimodal_v1.ingestion.errors import IngestionError
from multimodal_v1.ingestion.metrics import IngestionMetrics
from multimodal_v1.ingestion.registry import NormalizerRegistry


def _canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def stream_key(event: UnifiedEvent) -> StreamKey:
    origin = event.source_reference or ""
    serialized = event.to_dict()
    if event.signal_type is SignalType.TRACE:
        return ("trace", origin, event.trace_id or event.event_id)
    if event.signal_type is SignalType.LOG:
        return (
            "log", origin, event.service_name or "",
            _canonical(serialized["instrumentation_scope"]),
        )
    if event.signal_type is SignalType.METRIC:
        return (
            "metric", origin, event.service_name or "", event.metric_name or "",
            _canonical(
                serialized["attributes"].get(
                    "series_attributes", serialized["attributes"]
                )
            ),
        )
    business_identity = event.correlation_id or (
        f"{event.business_entity_type or ''}:{event.business_entity_id or ''}"
    )
    return ("business_event", origin, business_identity)


class MultimodalIngestionService:
    def __init__(self, registry: NormalizerRegistry,
                 dead_letter: DeadLetterPublisher,
                 *, deduplicator: EventDeduplicator | None = None,
                 metrics: IngestionMetrics | None = None,
                 dlq_include_payload: bool = False,
                 dlq_max_payload_bytes: int = 4096) -> None:
        self.registry = registry
        self.dead_letter = dead_letter
        self.deduplicator = deduplicator or EventDeduplicator()
        self.metrics = metrics or IngestionMetrics()
        self.dlq_include_payload = dlq_include_payload
        self.dlq_max_payload_bytes = dlq_max_payload_bytes
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "unified-event-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self._high_watermarks: dict[StreamKey, datetime] = {}
        self._state_lock = Lock()

    def prepare(self, signal_type: SignalType | str, payload: bytes,
                context: NormalizationContext) -> PreparedBatch:
        signal = signal_type.value if isinstance(signal_type, SignalType) else str(signal_type)
        self.metrics.increment("received", signal)
        metadata = MessageMetadata(
            topic=context.topic, partition=context.partition, offset=context.offset,
            message_timestamp=context.message_timestamp,
            key_sha256=context.key_sha256,
            source_reference=context.source_reference,
        )
        try:
            normalizer = self.registry.get(signal_type)
            candidates = normalizer.decode_and_normalize(payload, context)
            return self._prepare_candidates(signal, candidates, metadata)
        except (IngestionError, DecodeError, ValidationError) as exception:
            self.metrics.increment("rejected", signal)
            record = DeadLetterRecord.create(
                signal, context, payload, exception,
                include_payload=self.dlq_include_payload,
                max_payload_bytes=self.dlq_max_payload_bytes,
            )
            # Falha de publicação propaga e impede commit.
            self.dead_letter.publish(record)
            return PreparedBatch((), (), (), metadata, rejected_to_dead_letter=True)
        except Exception:
            self.metrics.increment("internal_error", signal)
            raise

    def _prepare_candidates(self, signal: str, candidates,
                            metadata: MessageMetadata) -> PreparedBatch:
        accepted: list[UnifiedEvent] = []
        keys: list[DeduplicationKey] = []
        proposed: dict[StreamKey, datetime] = {}
        local_keys: set[DeduplicationKey] = set()
        with self._state_lock:
            committed_watermarks = dict(self._high_watermarks)

        for candidate in candidates:
            self._validator.validate(candidate.to_dict())
            dedup_key = (candidate.event_id, candidate.source_reference or "")
            if dedup_key in local_keys or self.deduplicator.contains(dedup_key):
                self.metrics.increment("duplicates", signal)
                continue

            key = stream_key(candidate)
            previous = proposed.get(key, committed_watermarks.get(key))
            event = self._with_out_of_order(candidate, previous)
            accepted.append(event)
            keys.append(dedup_key)
            local_keys.add(dedup_key)
            proposed[key] = max(event.event_timestamp, previous or event.event_timestamp)
            if "late_arrival" in event.data_quality_flags:
                self.metrics.increment("late", signal)

        self.metrics.increment("normalized", signal, len(accepted))
        updates = tuple(
            WatermarkUpdate(key, timestamp)
            for key, timestamp in sorted(proposed.items())
        )
        return PreparedBatch(tuple(accepted), tuple(keys), updates, metadata)

    @staticmethod
    def _with_out_of_order(event: UnifiedEvent,
                           previous: datetime | None) -> UnifiedEvent:
        if previous is None or event.event_timestamp >= previous:
            return event
        payload = event.to_dict()
        payload["data_quality_flags"] = tuple(dict.fromkeys(
            (*event.data_quality_flags, "out_of_order")
        ))
        return UnifiedEvent.from_dict(payload)

    def deliver(self, batch: PreparedBatch, sink: BatchSink) -> None:
        if batch.events:
            sink.deliver(batch.events)
            self.metrics.increment(
                "delivered", batch.events[0].signal_type.value, len(batch.events)
            )

    def confirm(self, batch: PreparedBatch) -> None:
        """Confirma somente após entrega e commit Kafka bem-sucedidos."""
        if batch.rejected_to_dead_letter:
            return
        with self._state_lock:
            self.deduplicator.confirm(batch.deduplication_keys)
            for update in batch.watermark_updates:
                current = self._high_watermarks.get(update.stream_key)
                self._high_watermarks[update.stream_key] = max(
                    update.timestamp, current or update.timestamp
                )

    def watermark_snapshot(self) -> dict[StreamKey, datetime]:
        with self._state_lock:
            return dict(self._high_watermarks)
