from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from types import MappingProxyType
from typing import Any, Mapping

from multimodal_v1.contracts import UnifiedEvent


def utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} deve ser um datetime timezone-aware.")
    return value.astimezone(timezone.utc)


def frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class AnalysisWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start, "analysis_window.start"))
        object.__setattr__(self, "end", utc(self.end, "analysis_window.end"))
        if self.end < self.start:
            raise ValueError("analysis_window.end não pode preceder start.")


@dataclass(frozen=True)
class ServiceIdentity:
    service_name: str
    service_instance_id: str | None = None


@dataclass(frozen=True)
class EventReference:
    event_id: str
    source_reference: str | None = None


@dataclass(frozen=True)
class TrustedIdentityClaim:
    identity_value: str
    source: str
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        for name in ("identity_value", "source", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"TrustedIdentityClaim.{name} deve ser não vazio.")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.identity_value):
            raise ValueError("TrustedIdentityClaim.identity_value contém caracteres de controle.")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not 0 < self.confidence <= 1:
            raise ValueError("TrustedIdentityClaim.confidence deve satisfazer 0 < confidence <= 1.")


@dataclass(frozen=True)
class ExternalIdentityEvidence:
    event_reference: EventReference
    identity_type: str
    identity_value: str
    source: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class IncidentIdentityEvidence:
    """Identidade armazenada no incidente sem perder origem ou confiança."""

    event_reference: EventReference
    identity_type: str
    identity_value: str
    origin: str
    confidence: float
    source: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.identity_type not in {"correlation_id", "trace_id"}:
            raise ValueError("identity_type deve ser correlation_id ou trace_id.")
        if self.origin not in {"observed", "external"}:
            raise ValueError("origin deve ser observed ou external.")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not 0 < self.confidence <= 1:
            raise ValueError("confidence deve satisfazer 0 < confidence <= 1.")
        if self.origin == "external" and (not self.source or not self.reason):
            raise ValueError("Identidade externa requer source e reason.")


_OTLP_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_MAX_CORRELATION_ID_LENGTH = 128


@dataclass(frozen=True)
class TrustedAssociationContext:
    """Claims independentes que complementam, mas nunca sobrescrevem o evento."""

    correlation: TrustedIdentityClaim | None = None
    trace: TrustedIdentityClaim | None = None

    def __post_init__(self) -> None:
        if self.correlation is None and self.trace is None:
            raise ValueError("TrustedAssociationContext requer correlation ou trace.")
        if self.correlation is not None:
            value = self.correlation.identity_value
            if len(value) > _MAX_CORRELATION_ID_LENGTH or any(character.isspace() for character in value):
                raise ValueError("correlation_id externo deve ter até 128 caracteres e não conter whitespace.")
        if self.trace is not None and not _OTLP_TRACE_ID.fullmatch(self.trace.identity_value):
            raise ValueError("trace_id externo deve ser um TraceId OTLP hexadecimal de 32 caracteres.")

    def identities_for(
        self, event: UnifiedEvent,
    ) -> tuple[str | None, str | None, tuple[ExternalIdentityEvidence, ...]]:
        correlation_id = self.correlation.identity_value if self.correlation else None
        trace_id = self.trace.identity_value if self.trace else None
        if correlation_id and event.correlation_id and correlation_id != event.correlation_id:
            raise ValueError("TrustedAssociationContext diverge do correlation_id observado.")
        if trace_id and event.trace_id and trace_id != event.trace_id:
            raise ValueError("TrustedAssociationContext diverge do trace_id observado.")
        reference = EventReference(event.event_id, event.source_reference)
        evidence = []
        for identity_type, claim in (("correlation_id", self.correlation), ("trace_id", self.trace)):
            if claim is not None:
                evidence.append(ExternalIdentityEvidence(
                    reference, identity_type, claim.identity_value, claim.source,
                    float(claim.confidence), claim.reason,
                ))
        return event.correlation_id or correlation_id, event.trace_id or trace_id, tuple(evidence)


@dataclass(frozen=True)
class MetricSummary:
    metric_name: str
    count: int
    minimum: float
    maximum: float
    total: float
    average: float
    last: float
    unit: str | None = None
    aggregation: str | None = None


@dataclass(frozen=True)
class ServiceEvidence:
    identity: ServiceIdentity
    event_counts: Mapping[str, int]
    modality_intervals: Mapping[str, AnalysisWindow]
    first_event_at: datetime
    last_event_at: datetime
    event_count: int
    error_count: int
    error_rate: float
    metrics: tuple[MetricSummary, ...] = ()
    business_states: tuple[Mapping[str, Any], ...] = ()
    logs_and_exceptions: tuple[Mapping[str, Any], ...] = ()
    missing_fields: tuple[str, ...] = ()
    data_quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_counts", frozen_mapping(self.event_counts))
        object.__setattr__(self, "modality_intervals", frozen_mapping(self.modality_intervals))
        object.__setattr__(self, "business_states", tuple(frozen_mapping(v) for v in self.business_states))
        object.__setattr__(self, "logs_and_exceptions", tuple(frozen_mapping(v) for v in self.logs_and_exceptions))


@dataclass(frozen=True)
class EventAssociation:
    event_reference: EventReference
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ProvenanceRecord:
    event_reference: EventReference
    source_dataset: str | None


@dataclass(frozen=True)
class IncidentEvidenceBundle:
    incident_id: str
    workflow_key: str
    analysis_window: AnalysisWindow
    events: tuple[UnifiedEvent, ...]
    services: tuple[ServiceEvidence, ...]
    traces: tuple[str, ...]
    business_entities: tuple[tuple[str | None, str], ...]
    missing_modalities: tuple[str, ...]
    late_events: tuple[EventReference, ...]
    association_reasons: tuple[EventAssociation, ...]
    association_scores: Mapping[EventReference, float]
    provenance: tuple[ProvenanceRecord, ...]
    external_identity_evidence: tuple[ExternalIdentityEvidence, ...] = ()
    identity_evidence: tuple[IncidentIdentityEvidence, ...] = ()
    lifecycle_generation: int = 0
    reopened_count: int = 0
    closed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "association_scores", frozen_mapping(self.association_scores))


@dataclass(frozen=True)
class AssociationDecision:
    event_reference: EventReference
    incident_id: str | None
    score: float
    reasons: tuple[str, ...]
    associated: bool
    duplicate: bool = False
    reopened: bool = False

    @property
    def event_id(self) -> str:
        return self.event_reference.event_id

    @property
    def source_reference(self) -> str | None:
        return self.event_reference.source_reference
