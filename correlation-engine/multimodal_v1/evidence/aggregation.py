from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence.contracts import AnalysisWindow, MetricSummary, ServiceEvidence
from multimodal_v1.evidence.policy import ServiceIdentityResolver


def _is_error(event: UnifiedEvent) -> bool:
    severity = (event.severity or "").upper()
    outcome = (event.business_outcome or "").lower()
    status = event.status_code
    return bool(
        event.error_type
        or severity in {"ERROR", "FATAL", "CRITICAL"}
        or (isinstance(status, int) and status >= 400)
        or (isinstance(status, str) and status.upper() in {"ERROR", "FAILED", "FAILURE"})
        or outcome in {"failed", "failure", "rejected", "error"}
    )


class EvidenceAggregator:
    def __init__(self, resolver: ServiceIdentityResolver | None = None, max_log_samples: int = 100):
        if isinstance(max_log_samples, bool) or not isinstance(max_log_samples, int) or max_log_samples < 1:
            raise ValueError("max_log_samples deve ser positivo.")
        self.resolver = resolver or ServiceIdentityResolver()
        self.max_log_samples = max_log_samples

    def aggregate(self, events: Iterable[UnifiedEvent]) -> tuple[ServiceEvidence, ...]:
        groups: dict[tuple[str, str | None], list[UnifiedEvent]] = defaultdict(list)
        for event in events:
            identity = self.resolver.resolve(event)
            groups[(identity.service_name, identity.service_instance_id)].append(event)
        ordered = sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or ""))
        return tuple(self._service(items) for _, items in ordered)

    def _service(self, events: list[UnifiedEvent]) -> ServiceEvidence:
        events.sort(key=lambda item: (item.event_timestamp, item.event_id, item.source_reference or ""))
        identity = self.resolver.resolve(events[0])
        counts: dict[str, int] = defaultdict(int)
        modality_times: dict[str, list] = defaultdict(list)
        metric_values: dict[tuple[str, str | None, str | None], list[float]] = defaultdict(list)
        business: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        missing: set[str] = set()
        flags: set[str] = set()
        errors = 0
        for event in events:
            signal = event.signal_type.value
            counts[signal] += 1
            modality_times[signal].append(event.event_timestamp)
            errors += int(_is_error(event))
            missing.update(event.missing_fields)
            flags.update(event.data_quality_flags)
            if event.signal_type is SignalType.METRIC and event.metric_name and event.metric_value is not None:
                metric_values[(event.metric_name, event.metric_unit, event.metric_aggregation)].append(float(event.metric_value))
            if event.signal_type is SignalType.BUSINESS_EVENT:
                business.append({"event_id": event.event_id, "event_name": event.event_name,
                                 "operation": event.business_operation, "outcome": event.business_outcome,
                                 "entity_id": event.business_entity_id, "timestamp": event.event_timestamp})
            if event.signal_type is SignalType.LOG or event.error_type:
                message = event.attributes.get("body", event.attributes.get("message"))
                logs.append({"event_id": event.event_id, "timestamp": event.event_timestamp,
                             "severity": event.severity, "error_type": event.error_type, "message": message})
        intervals = {name: AnalysisWindow(min(values), max(values)) for name, values in modality_times.items()}
        metrics = tuple(
            MetricSummary(name, len(values), min(values), max(values), sum(values),
                          sum(values) / len(values), values[-1], unit, aggregation)
            for (name, unit, aggregation), values in sorted(metric_values.items())
        )
        return ServiceEvidence(identity, dict(counts), intervals, events[0].event_timestamp,
                               events[-1].event_timestamp, len(events), errors, errors / len(events),
                               metrics, tuple(business), tuple(logs[:self.max_log_samples]),
                               tuple(sorted(missing)), tuple(sorted(flags)))
