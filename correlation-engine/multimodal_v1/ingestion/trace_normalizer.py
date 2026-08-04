from __future__ import annotations

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.base import EventNormalizer
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.otlp import key_values, scope_dict, timing_quality
from multimodal_v1.span_adapter import SpanToUnifiedEventAdapter
from normalizer.span_normalizer import SpanNormalizer


class TraceEventNormalizer(EventNormalizer):
    """Reutiliza o SpanNormalizer congelado e adapta sua saída para v3."""

    def __init__(self) -> None:
        self._span_normalizer = SpanNormalizer()
        self._adapter = SpanToUnifiedEventAdapter()

    def decode(self, payload: bytes) -> ExportTraceServiceRequest:
        request = ExportTraceServiceRequest()
        request.ParseFromString(payload)
        return request

    def normalize(self, decoded, context: NormalizationContext):
        spans_v2 = self._span_normalizer.normalize(decoded)
        raw = []
        for resource_spans in decoded.resource_spans:
            resource = key_values(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                scope = scope_dict(scope_spans.scope)
                for span in scope_spans.spans:
                    raw.append((span, resource, scope))
        if len(raw) != len(spans_v2):
            raise ValueError("Quantidade de spans OTLP diverge da normalização v2.")

        events = []
        for span_v2, (span, resource, scope) in zip(spans_v2, raw):
            attributes = key_values(span.attributes)
            event = self._adapter.adapt(
                span_v2,
                observed_timestamp=context.ingested_timestamp,
                ingested_timestamp=context.ingested_timestamp,
                attributes=attributes,
                resource_attributes=resource,
            )
            payload = event.to_dict()
            payload.update(
                source_reference=context.source_reference,
                source_dataset=context.source_dataset,
                trace_flags=int(span.flags & 0xFF),
                trace_state=span.trace_state or None,
                instrumentation_scope=scope,
                span_links=tuple(
                    {
                        "trace_id": link.trace_id.hex(),
                        "span_id": link.span_id.hex(),
                        "trace_state": link.trace_state or None,
                        "attributes": key_values(link.attributes),
                    }
                    for link in span.links
                ),
            )
            late, timing_flags = timing_quality(
                event.event_timestamp, context.ingested_timestamp,
                late_threshold_ms=context.late_threshold_ms,
                future_tolerance_ms=context.future_tolerance_ms,
            )
            payload["late_arrival_ms"] = late
            flags = [*payload["data_quality_flags"], *timing_flags]
            payload["data_quality_flags"] = tuple(dict.fromkeys(flags))
            events.append(UnifiedEvent.from_dict(payload))
        return events
