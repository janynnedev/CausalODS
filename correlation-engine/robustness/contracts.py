from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ScenarioLayer(str, Enum):
    OFFLINE_TRACE = "offline_trace"
    LIVE_INFRASTRUCTURE = "live_infrastructure"


class RobustnessScenario(str, Enum):
    CONTROL = "control"
    DUPLICATE_SPAN = "duplicate_span"
    OUT_OF_ORDER_SPANS = "out_of_order_spans"
    INCOMPLETE_TRACE = "incomplete_trace"
    DELAYED_SPAN_BEFORE_TIMEOUT = "delayed_span_before_timeout"
    DELAYED_SPAN_AFTER_TIMEOUT = "delayed_span_after_timeout"
    KAFKA_BACKLOG = "kafka_backlog"
    OPENSEARCH_UNAVAILABLE = "opensearch_unavailable"
    CORRELATION_ENGINE_RESTART = "correlation_engine_restart"


@dataclass(frozen=True)
class SpanFixture:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service_name: str
    operation: str
    start_ns: int
    end_ns: int
    status_code: str = "UNSET"
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trace_id:
            raise ValueError("trace_id não pode ser vazio")
        if not self.span_id:
            raise ValueError("span_id não pode ser vazio")
        if not self.service_name:
            raise ValueError("service_name não pode ser vazio")
        if self.end_ns < self.start_ns:
            raise ValueError("end_ns deve ser maior ou igual a start_ns")

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "service_name": self.service_name,
            "operation": self.operation,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "status_code": self.status_code,
            "attributes": self.attributes,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpanEnvelope:
    span: SpanFixture
    arrival_offset_ms: int
    sequence: int

    def __post_init__(self) -> None:
        if self.arrival_offset_ms < 0:
            raise ValueError("arrival_offset_ms deve ser não negativo")
        if self.sequence < 0:
            raise ValueError("sequence deve ser não negativo")

    def to_dict(self) -> dict[str, Any]:
        return {
            "span": self.span.to_dict(),
            "arrival_offset_ms": self.arrival_offset_ms,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class TraceFixture:
    trace_id: str
    spans: tuple[SpanFixture, ...]
    expected_span_ids: frozenset[str]
    expected_root_cause: str
    expected_reliable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.spans:
            raise ValueError("TraceFixture deve conter pelo menos um span")
        if any(span.trace_id != self.trace_id for span in self.spans):
            raise ValueError("Todos os spans devem possuir o trace_id da fixture")

        span_ids = [span.span_id for span in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("A fixture canônica não pode conter span_id duplicado")

        if set(span_ids) != set(self.expected_span_ids):
            raise ValueError(
                "expected_span_ids deve corresponder aos spans canônicos"
            )

    def span_by_id(self, span_id: str) -> SpanFixture:
        for span in self.spans:
            if span.span_id == span_id:
                return span
        raise KeyError(span_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": [span.to_dict() for span in self.spans],
            "expected_span_ids": sorted(self.expected_span_ids),
            "expected_root_cause": self.expected_root_cause,
            "expected_reliable": self.expected_reliable,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class RobustnessScenarioSpec:
    scenario: RobustnessScenario
    layer: ScenarioLayer
    description: str
    expected_trace_completed: bool | None
    expected_trace_timed_out: bool | None
    expected_rca_result_min: int
    expected_rca_result_max: int
    expected_incident_max: int | None
    reliable_decision_allowed: bool
    expected_top_service: str | None
    invariants: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenario"] = self.scenario.value
        payload["layer"] = self.layer.value
        payload["invariants"] = list(self.invariants)
        return payload


@dataclass(frozen=True)
class MutationResult:
    scenario: RobustnessScenario
    envelopes: tuple[SpanEnvelope, ...]
    input_span_count: int
    output_envelope_count: int
    introduced_duplicates: int = 0
    dropped_span_ids: tuple[str, ...] = ()
    delayed_span_ids: tuple[str, ...] = ()
    reordered: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.value,
            "envelopes": [item.to_dict() for item in self.envelopes],
            "input_span_count": self.input_span_count,
            "output_envelope_count": self.output_envelope_count,
            "introduced_duplicates": self.introduced_duplicates,
            "dropped_span_ids": list(self.dropped_span_ids),
            "delayed_span_ids": list(self.delayed_span_ids),
            "reordered": self.reordered,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class LifecycleObservation:
    scenario: RobustnessScenario
    trace_id: str
    received_envelopes: int
    accepted_unique_spans: int
    duplicate_spans_detected: int
    late_spans_rejected: int
    trace_finalization_count: int
    trace_completed: bool
    trace_timed_out: bool
    decision_allowed: bool
    expected_span_count: int
    missing_span_ids: tuple[str, ...]
    semantic_fingerprint: str
    finalized_at_ms: int | None
    processing_latency_ms: int | None
    events: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenario"] = self.scenario.value
        payload["missing_span_ids"] = list(self.missing_span_ids)
        payload["events"] = list(self.events)
        return payload


@dataclass(frozen=True)
class LiveObservation:
    scenario: RobustnessScenario
    trace_id: str | None
    rca_result_count: int
    incident_count: int
    top_service: str | None
    is_reliable: bool | None
    data_loss_count: int
    restart_count_before: int | None = None
    restart_count_after: int | None = None
    recovery_time_ms: float | None = None
    processing_latency_ms: float | None = None
    provenance_valid: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenario"] = self.scenario.value
        return payload


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    expected: Any = None
    actual: Any = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    scenario: RobustnessScenario
    valid: bool
    issues: tuple[ValidationIssue, ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.value,
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": self.metrics,
        }
