from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models.span import Span
from multimodal_v1.contracts import SignalType, UnifiedEvent


class SpanToUnifiedEventAdapter:
    """Adapta o `models.span.Span` v2 sem modificar seu contrato."""

    SOURCE = "causalods-span-v2"

    def adapt(
        self,
        span: Span,
        *,
        observed_timestamp: datetime | None = None,
        ingested_timestamp: datetime | None = None,
        attributes: dict[str, Any] | None = None,
        resource_attributes: dict[str, Any] | None = None,
    ) -> UnifiedEvent:
        if not isinstance(span, Span):
            raise ValueError("span deve ser uma instância de models.span.Span.")
        observed = observed_timestamp or datetime.now(timezone.utc)
        ingested = ingested_timestamp or observed
        event_timestamp = datetime.fromtimestamp(
            span.start_time / 1_000_000_000, tz=timezone.utc
        )
        late_arrival_ms = max(
            0.0, (ingested - event_timestamp).total_seconds() * 1000
        )
        status = str(span.status_code) if span.status_code is not None else None
        return UnifiedEvent(
            event_id=f"trace:{span.trace_id}:{span.span_id}",
            event_name=span.operation_name or "unknown-operation",
            event_type="span",
            signal_type=SignalType.TRACE,
            event_timestamp=event_timestamp,
            observed_timestamp=observed,
            ingested_timestamp=ingested,
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            service_name=span.service_name,
            status_code=status,
            severity="ERROR" if status == "ERROR" else None,
            attributes={
                "duration_ns": span.duration_ns,
                "end_time_unix_nano": span.end_time,
                **(attributes or {}),
            },
            resource_attributes=resource_attributes or {},
            schema_version=UnifiedEvent.SCHEMA_VERSION,
            source_reference=self.SOURCE,
            late_arrival_ms=late_arrival_ms,
        )
