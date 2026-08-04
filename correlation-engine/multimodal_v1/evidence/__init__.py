"""Agregação multimodal v3 isolada do runtime trace-only v2."""

from multimodal_v1.evidence.aggregation import EvidenceAggregator
from multimodal_v1.evidence.assembler import IncidentEvidenceAssembler
from multimodal_v1.evidence.association import EventAssociationScorer
from multimodal_v1.evidence.contracts import (
    AnalysisWindow, AssociationDecision, EventAssociation, EventReference,
    ExternalIdentityEvidence, IncidentEvidenceBundle, IncidentIdentityEvidence,
    MetricSummary, ProvenanceRecord, ServiceEvidence, ServiceIdentity,
    TrustedAssociationContext, TrustedIdentityClaim,
)
from multimodal_v1.evidence.policy import IncidentWindowPolicy, KnownTopology, ServiceIdentityResolver
from multimodal_v1.evidence.telemetry import AssemblerTelemetry

__all__ = [
    "AnalysisWindow", "AssemblerTelemetry", "AssociationDecision", "EventAssociation", "EventReference",
    "ExternalIdentityEvidence",
    "IncidentIdentityEvidence",
    "EventAssociationScorer", "EvidenceAggregator", "IncidentEvidenceAssembler",
    "IncidentEvidenceBundle", "IncidentWindowPolicy", "KnownTopology", "MetricSummary",
    "ProvenanceRecord", "ServiceEvidence", "ServiceIdentity", "ServiceIdentityResolver",
    "TrustedAssociationContext", "TrustedIdentityClaim",
]
