from __future__ import annotations

from datetime import datetime

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)

from multimodal_v1.contracts import SignalType
from multimodal_v1.identity import normalize_distributed_id
from multimodal_v1.ingestion.base import EventNormalizer
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.otlp import (
    identity, key_values, scope_dict, stable_event_id, timestamp_from_nanos,
    timing_quality,
)
from multimodal_v1.ingestion.validation import unified_event_from_input


def _optional(point, field):
    descriptor = point.DESCRIPTOR.fields_by_name[field]
    if descriptor.has_presence:
        return getattr(point, field) if point.HasField(field) else None
    return getattr(point, field)


def _number_value(value_holder):
    selected = value_holder.WhichOneof("value")
    return getattr(value_holder, selected) if selected else None


def _buckets(value):
    return {
        "offset": int(value.offset),
        "bucket_counts": [int(item) for item in value.bucket_counts],
    }


class MetricEventNormalizer(EventNormalizer):
    def decode(self, payload: bytes) -> ExportMetricsServiceRequest:
        request = ExportMetricsServiceRequest()
        request.ParseFromString(payload)
        return request

    @staticmethod
    def _points(metric):
        kind = metric.WhichOneof("data")
        data = getattr(metric, kind) if kind else None
        return kind, data, tuple(getattr(data, "data_points", ()))

    @staticmethod
    def _details(kind, data, point):
        if kind in ("gauge", "sum"):
            details = {"value": _number_value(point)}
            if kind == "sum":
                details.update(
                    aggregation_temporality=int(data.aggregation_temporality),
                    is_monotonic=bool(data.is_monotonic),
                )
            return details
        if kind == "histogram":
            return {
                "count": int(point.count), "sum": _optional(point, "sum"),
                "min": _optional(point, "min"), "max": _optional(point, "max"),
                "explicit_bounds": list(point.explicit_bounds),
                "bucket_counts": [int(item) for item in point.bucket_counts],
                "aggregation_temporality": int(data.aggregation_temporality),
            }
        if kind == "exponential_histogram":
            return {
                "count": int(point.count), "sum": _optional(point, "sum"),
                "min": _optional(point, "min"), "max": _optional(point, "max"),
                "scale": int(point.scale), "zero_count": int(point.zero_count),
                "zero_threshold": float(point.zero_threshold),
                "positive": _buckets(point.positive),
                "negative": _buckets(point.negative),
                "aggregation_temporality": int(data.aggregation_temporality),
            }
        if kind == "summary":
            return {
                "count": int(point.count), "sum": float(point.sum),
                "quantile_values": [
                    {"quantile": float(item.quantile), "value": float(item.value)}
                    for item in point.quantile_values
                ],
            }
        return {}

    @staticmethod
    def _scalar(kind, details):
        if kind in ("gauge", "sum"):
            return details.get("value")
        return details.get("sum") if details.get("sum") is not None else details.get("count")

    @staticmethod
    def _exemplars(point, fallback: datetime):
        result = []
        for exemplar in getattr(point, "exemplars", ()):
            result.append({
                "timestamp": timestamp_from_nanos(
                    exemplar.time_unix_nano, fallback,
                ).isoformat().replace("+00:00", "Z"),
                "value": _number_value(exemplar),
                "trace_id": exemplar.trace_id.hex() or None,
                "span_id": exemplar.span_id.hex() or None,
                "filtered_attributes": key_values(exemplar.filtered_attributes),
            })
        return tuple(result)

    def normalize(self, decoded, context: NormalizationContext):
        events = []
        for resource_metrics in decoded.resource_metrics:
            resource = key_values(resource_metrics.resource.attributes)
            for scope_metrics in resource_metrics.scope_metrics:
                scope = scope_dict(scope_metrics.scope)
                for metric in scope_metrics.metrics:
                    aggregation, data, points = self._points(metric)
                    for point in points:
                        series_attributes = key_values(point.attributes)
                        correlation, entity_type, entity_id = identity(series_attributes)
                        correlation, identity_flags = normalize_distributed_id(correlation)
                        timestamp = timestamp_from_nanos(
                            point.time_unix_nano or point.start_time_unix_nano,
                            context.ingested_timestamp,
                        )
                        details = self._details(aggregation, data, point)
                        details["start_time_unix_nano"] = (
                            int(point.start_time_unix_nano)
                            if point.start_time_unix_nano else None
                        )
                        details["time_unix_nano"] = (
                            int(point.time_unix_nano)
                            if point.time_unix_nano else None
                        )
                        value = self._scalar(aggregation, details)
                        exemplars = self._exemplars(point, context.ingested_timestamp)
                        attributes = {
                            "series_attributes": series_attributes,
                            "aggregation_details": details,
                            "exemplars": exemplars,
                        }
                        fingerprint = {
                            "signal_type": "metric",
                            "service.name": resource.get("service.name"),
                            "service.instance.id": resource.get("service.instance.id"),
                            "resource_attributes": resource,
                            "instrumentation_scope": scope,
                            "timestamp": timestamp.isoformat(),
                            "series_attributes": series_attributes,
                            "value": value, "metric_name": metric.name,
                            "aggregation": aggregation,
                            "aggregation_details": details,
                        }
                        event_id = series_attributes.get("event.id") or stable_event_id(
                            "metric", fingerprint
                        )
                        late, timing_flags = timing_quality(
                            timestamp, context.ingested_timestamp,
                            late_threshold_ms=context.late_threshold_ms,
                            future_tolerance_ms=context.future_tolerance_ms,
                        )
                        flags = [*identity_flags, *timing_flags]
                        if exemplars:
                            flags.append("metric_exemplar_descriptor")
                        events.append(unified_event_from_input(
                            event_id=event_id, event_name=metric.name or "unknown.metric",
                            event_type="metric_point", signal_type=SignalType.METRIC,
                            event_timestamp=timestamp,
                            observed_timestamp=context.ingested_timestamp,
                            ingested_timestamp=context.ingested_timestamp,
                            correlation_id=correlation,
                            business_entity_type=entity_type,
                            business_entity_id=entity_id,
                            service_name=resource.get("service.name"),
                            service_instance_id=resource.get("service.instance.id"),
                            service_version=resource.get("service.version"),
                            deployment_environment=resource.get("deployment.environment"),
                            metric_name=metric.name or None, metric_unit=metric.unit or None,
                            metric_value=value, metric_aggregation=aggregation,
                            attributes=attributes, resource_attributes=resource,
                            instrumentation_scope=scope,
                            source_reference=context.source_reference,
                            source_dataset=context.source_dataset,
                            late_arrival_ms=late, data_quality_flags=tuple(flags),
                        ))
        return events
