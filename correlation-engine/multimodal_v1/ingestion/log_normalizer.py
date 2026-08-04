from __future__ import annotations

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)

from multimodal_v1.contracts import SignalType
from multimodal_v1.identity import normalize_distributed_id
from multimodal_v1.ingestion.base import EventNormalizer
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.otlp import (
    any_value, identity, key_values, scope_dict, stable_event_id,
    timestamp_from_nanos, timing_quality,
)
from multimodal_v1.ingestion.validation import unified_event_from_input


class LogEventNormalizer(EventNormalizer):
    def decode(self, payload: bytes) -> ExportLogsServiceRequest:
        request = ExportLogsServiceRequest()
        request.ParseFromString(payload)
        return request

    def normalize(self, decoded, context: NormalizationContext):
        events = []
        for resource_logs in decoded.resource_logs:
            resource = key_values(resource_logs.resource.attributes)
            for scope_logs in resource_logs.scope_logs:
                scope = scope_dict(scope_logs.scope)
                for record in scope_logs.log_records:
                    attributes = key_values(record.attributes)
                    correlation, entity_type, entity_id = identity(attributes)
                    correlation, identity_flags = normalize_distributed_id(correlation)
                    timestamp = timestamp_from_nanos(
                        record.time_unix_nano or record.observed_time_unix_nano,
                        context.ingested_timestamp,
                    )
                    observed = timestamp_from_nanos(
                        record.observed_time_unix_nano, context.ingested_timestamp
                    )
                    body = any_value(record.body)
                    fingerprint = {
                        "signal_type": "log",
                        "service.name": resource.get("service.name"),
                        "service.instance.id": resource.get("service.instance.id"),
                        "resource_attributes": resource,
                        "instrumentation_scope": scope,
                        "timestamp": timestamp.isoformat(), "body": body,
                        "attributes": attributes, "trace_id": record.trace_id.hex(),
                        "span_id": record.span_id.hex(),
                    }
                    event_id = attributes.get("event.id") or stable_event_id("log", fingerprint)
                    late, timing_flags = timing_quality(
                        timestamp, context.ingested_timestamp,
                        late_threshold_ms=context.late_threshold_ms,
                        future_tolerance_ms=context.future_tolerance_ms,
                    )
                    flags = [*identity_flags, *timing_flags]
                    events.append(unified_event_from_input(
                        event_id=event_id,
                        event_name=attributes.get("event.name") or "log.record",
                        event_type="log_record", signal_type=SignalType.LOG,
                        event_timestamp=timestamp, observed_timestamp=observed,
                        ingested_timestamp=context.ingested_timestamp,
                        trace_id=record.trace_id.hex() or None,
                        span_id=record.span_id.hex() or None,
                        trace_flags=int(record.flags & 0xFF),
                        correlation_id=correlation,
                        business_entity_type=entity_type,
                        business_entity_id=entity_id,
                        service_name=resource.get("service.name"),
                        service_instance_id=resource.get("service.instance.id"),
                        service_version=resource.get("service.version"),
                        deployment_environment=resource.get("deployment.environment"),
                        severity=record.severity_text or str(record.severity_number),
                        attributes={**attributes, "body": body},
                        resource_attributes=resource,
                        instrumentation_scope=scope,
                        source_reference=context.source_reference,
                        source_dataset=context.source_dataset,
                        late_arrival_ms=late,
                        data_quality_flags=tuple(flags),
                    ))
        return events
