from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models.span import Span
from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.identity import (
    CORRELATION_ATTRIBUTE,
    ENTITY_ID_ATTRIBUTE,
    ENTITY_TYPE_ATTRIBUTE,
    normalize_distributed_id,
)


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
        stored_attributes = getattr(span, "attributes", None) or {}
        if not isinstance(stored_attributes, dict):
            raise ValueError("span.attributes deve ser um objeto quando presente.")
        # Política de colisão: atributos adicionais substituem atributos OTLP
        # armazenados; campos técnicos calculados pelo adaptador são reservados.
        merged_attributes = {**stored_attributes, **(attributes or {})}
        merged_attributes["duration_ns"] = span.duration_ns
        merged_attributes["end_time_unix_nano"] = span.end_time

        raw_correlation_id = getattr(span, "correlation_id", None)
        if raw_correlation_id is None:
            raw_correlation_id = merged_attributes.get(CORRELATION_ATTRIBUTE)
        correlation_id, identity_flags = normalize_distributed_id(
            raw_correlation_id
        )
        entity_type = getattr(span, "business_entity_type", None)
        if entity_type is None:
            entity_type = merged_attributes.get(ENTITY_TYPE_ATTRIBUTE)
        entity_id = getattr(span, "business_entity_id", None)
        if entity_id is None:
            entity_id = merged_attributes.get(ENTITY_ID_ATTRIBUTE)
        quality_flags = tuple(dict.fromkeys(
            (*getattr(span, "data_quality_flags", ()), *identity_flags)
        ))
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
            correlation_id=correlation_id,
            business_entity_type=entity_type,
            business_entity_id=entity_id,
            service_name=span.service_name,
            status_code=status,
            severity="ERROR" if status == "ERROR" else None,
            attributes=merged_attributes,
            resource_attributes=resource_attributes or {},
            schema_version=UnifiedEvent.SCHEMA_VERSION,
            source_reference=self.SOURCE,
            late_arrival_ms=late_arrival_ms,
            data_quality_flags=quality_flags,
        )
