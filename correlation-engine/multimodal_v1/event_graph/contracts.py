from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.canonical import canonical_json_value, deep_freeze_json
from multimodal_v1.evidence.contracts import (
    EventAssociation, EventReference, ExternalIdentityEvidence,
    IncidentIdentityEvidence, ProvenanceRecord,
)


class RelationType(str, Enum):
    SPAN_PARENT = "SPAN_PARENT"
    SPAN_LINK = "SPAN_LINK"
    SERVICE_CALL = "SERVICE_CALL"
    TEMPORAL_PRECEDENCE = "TEMPORAL_PRECEDENCE"
    SAME_CORRELATION = "SAME_CORRELATION"
    SAME_BUSINESS_ENTITY = "SAME_BUSINESS_ENTITY"
    BUSINESS_TRANSITION = "BUSINESS_TRANSITION"
    CO_ANOMALY = "CO_ANOMALY"
    TOPOLOGY_DEPENDENCY = "TOPOLOGY_DEPENDENCY"


class EdgeClassification(str, Enum):
    STRUCTURAL = "structural"
    TEMPORAL = "temporal"
    SEMANTIC = "semantic"
    STATISTICAL = "statistical"
    CAUSAL_CANDIDATE = "causal_candidate"


class EdgeDirection(str, Enum):
    DIRECTED = "directed"
    UNDIRECTED = "undirected"


EDGE_TAXONOMY = MappingProxyType({
    RelationType.SPAN_PARENT: (EdgeClassification.STRUCTURAL, EdgeDirection.DIRECTED),
    RelationType.SPAN_LINK: (EdgeClassification.STRUCTURAL, EdgeDirection.DIRECTED),
    RelationType.SERVICE_CALL: (EdgeClassification.STRUCTURAL, EdgeDirection.DIRECTED),
    RelationType.TEMPORAL_PRECEDENCE: (EdgeClassification.TEMPORAL, EdgeDirection.DIRECTED),
    RelationType.SAME_CORRELATION: (EdgeClassification.SEMANTIC, EdgeDirection.DIRECTED),
    RelationType.SAME_BUSINESS_ENTITY: (EdgeClassification.SEMANTIC, EdgeDirection.DIRECTED),
    RelationType.BUSINESS_TRANSITION: (EdgeClassification.SEMANTIC, EdgeDirection.DIRECTED),
    RelationType.CO_ANOMALY: (EdgeClassification.STATISTICAL, EdgeDirection.UNDIRECTED),
    RelationType.TOPOLOGY_DEPENDENCY: (EdgeClassification.CAUSAL_CANDIDATE, EdgeDirection.DIRECTED),
})


def reference_key(reference: EventReference) -> tuple[str, str]:
    return reference.event_id, reference.source_reference or ""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("derived_at deve ser timezone-aware.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class EventGraphNode:
    reference: EventReference
    event: UnifiedEvent

    def __post_init__(self) -> None:
        expected = EventReference(self.event.event_id, self.event.source_reference)
        if self.reference != expected:
            raise ValueError("node.reference diverge do UnifiedEvent.")


@dataclass(frozen=True)
class ServiceGraphNode:
    service_name: str
    event_references: tuple[EventReference, ...]


@dataclass(frozen=True)
class EventGraphEdge:
    source: EventReference
    target: EventReference
    relation_type: RelationType | str
    weight: float
    confidence: float
    evidence_references: tuple[EventReference, ...]
    rule_version: str
    direction: EdgeDirection | str
    derived_at: datetime
    classification: EdgeClassification | str
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, EventReference) or not isinstance(self.target, EventReference):
            raise TypeError("source e target devem ser EventReference.")
        object.__setattr__(self, "relation_type", RelationType(self.relation_type))
        object.__setattr__(self, "direction", EdgeDirection(self.direction))
        object.__setattr__(self, "classification", EdgeClassification(self.classification))
        expected = EDGE_TAXONOMY[self.relation_type]
        if (self.classification, self.direction) != expected:
            raise ValueError(f"Taxonomia incompatível para {self.relation_type.value}.")
        for name in ("rule_version", "explanation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} deve ser texto não vazio.")
        for name in ("weight", "confidence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} deve estar entre 0 e 1.")
            object.__setattr__(self, name, float(value))
        if any(not isinstance(item, EventReference) for item in self.evidence_references):
            raise TypeError("evidence_references deve conter EventReference.")
        evidence = tuple(sorted(set(self.evidence_references), key=reference_key))
        object.__setattr__(self, "evidence_references", evidence)
        object.__setattr__(self, "derived_at", _utc(self.derived_at))

    @property
    def key(self):
        endpoints = (self.source, self.target)
        if self.direction is EdgeDirection.UNDIRECTED:
            endpoints = tuple(sorted(endpoints, key=reference_key))
        return (*endpoints, self.relation_type, self.rule_version)

    def to_dict(self) -> dict[str, Any]:
        encode = lambda ref: {"event_id": ref.event_id, "source_reference": ref.source_reference}
        return {
            "source": encode(self.source), "target": encode(self.target),
            "relation_type": self.relation_type.value, "weight": self.weight,
            "confidence": self.confidence,
            "evidence_references": [encode(ref) for ref in self.evidence_references],
            "rule_version": self.rule_version, "direction": self.direction.value,
            "derived_at": self.derived_at.isoformat().replace("+00:00", "Z"),
            "classification": self.classification.value, "explanation": self.explanation,
        }


@dataclass(frozen=True)
class IncidentGraphContext:
    incident_id: str
    workflow_key: str
    lifecycle_generation: int
    association_scores: Mapping[EventReference, float]
    association_reasons: tuple[EventAssociation, ...]
    identity_evidence: tuple[IncidentIdentityEvidence, ...]
    external_identity_evidence: tuple[ExternalIdentityEvidence, ...]
    provenance: tuple[ProvenanceRecord, ...]
    late_events: tuple[EventReference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "association_scores", MappingProxyType(dict(self.association_scores)))


@dataclass(frozen=True)
class EventEvidenceGraph:
    context: IncidentGraphContext
    nodes: tuple[EventGraphNode, ...]
    edges: tuple[EventGraphEdge, ...]
    service_nodes: tuple[ServiceGraphNode, ...] = ()
    rule_versions: Mapping[str, str] = field(default_factory=dict)
    policy_provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.context, IncidentGraphContext):
            raise TypeError("context deve ser IncidentGraphContext.")
        if any(not isinstance(node, EventGraphNode) for node in self.nodes):
            raise TypeError("nodes deve conter EventGraphNode.")
        if any(not isinstance(edge, EventGraphEdge) for edge in self.edges):
            raise TypeError("edges deve conter EventGraphEdge.")
        references = [node.reference for node in self.nodes]
        if len(references) != len(set(references)):
            raise ValueError("EventReference deve ser único por grafo.")
        known = set(references)
        context_references = set(self.context.association_scores)
        context_references.update(item.event_reference for item in self.context.association_reasons)
        context_references.update(item.event_reference for item in self.context.identity_evidence)
        context_references.update(item.event_reference for item in self.context.external_identity_evidence)
        context_references.update(item.event_reference for item in self.context.provenance)
        context_references.update(self.context.late_events)
        if not context_references <= known:
            raise ValueError("Contexto contém EventReference ausente dos nós.")
        edge_keys = set()
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("Aresta aponta para endpoint inexistente.")
            if any(reference not in known for reference in edge.evidence_references):
                raise ValueError("Aresta contém evidência que não pertence ao grafo.")
            if edge.key in edge_keys:
                raise ValueError("Chave de aresta duplicada na construção direta.")
            edge_keys.add(edge.key)
        for service_node in self.service_nodes:
            if not isinstance(service_node, ServiceGraphNode):
                raise TypeError("service_nodes deve conter ServiceGraphNode.")
            if any(reference not in known for reference in service_node.event_references):
                raise ValueError("ServiceGraphNode contém referência inexistente.")
            if any(next(node.event.service_name for node in self.nodes if node.reference == reference)
                   != service_node.service_name for reference in service_node.event_references):
                raise ValueError("ServiceGraphNode contém referência de outro serviço.")
        versions = dict(self.rule_versions)
        for edge in self.edges:
            declared = versions.get(edge.relation_type.value)
            allowed = {declared} if isinstance(declared, str) else set(declared or ())
            if edge.rule_version not in allowed:
                raise ValueError("rule_versions não cobre a versão de uma aresta.")
        object.__setattr__(self, "rule_versions", deep_freeze_json(self.rule_versions))
        object.__setattr__(self, "policy_provenance", deep_freeze_json(self.policy_provenance))

    def event(self, reference: EventReference) -> UnifiedEvent:
        if not isinstance(reference, EventReference):
            raise TypeError("lookup requer EventReference.")
        try:
            return next(node.event for node in self.nodes if node.reference == reference)
        except StopIteration as exception:
            raise KeyError(reference) from exception

    def event_by_id(self, event_id: str) -> UnifiedEvent:
        matches = [node.event for node in self.nodes if node.reference.event_id == event_id]
        if len(matches) > 1:
            raise ValueError(f"event_id ambíguo: {event_id!r}.")
        if not matches:
            raise KeyError(event_id)
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        encode = lambda ref: {"event_id": ref.event_id, "source_reference": ref.source_reference}
        return {
            "context": {
                "incident_id": self.context.incident_id,
                "workflow_key": self.context.workflow_key,
                "lifecycle_generation": self.context.lifecycle_generation,
                "association_scores": [
                    {"event": encode(ref), "score": score}
                    for ref, score in sorted(self.context.association_scores.items(), key=lambda item: reference_key(item[0]))
                ],
                "late_events": [encode(ref) for ref in sorted(self.context.late_events, key=reference_key)],
                "association_reasons": [
                    {"event": encode(item.event_reference), "score": item.score, "reasons": list(item.reasons)}
                    for item in self.context.association_reasons
                ],
                "identity_evidence": [
                    {"event": encode(item.event_reference), "identity_type": item.identity_type,
                     "identity_value": item.identity_value, "origin": item.origin,
                     "confidence": item.confidence, "source": item.source, "reason": item.reason}
                    for item in self.context.identity_evidence
                ],
                "external_identity_evidence": [
                    {"event": encode(item.event_reference), "identity_type": item.identity_type,
                     "identity_value": item.identity_value, "source": item.source,
                     "confidence": item.confidence, "reason": item.reason}
                    for item in self.context.external_identity_evidence
                ],
                "provenance": [
                    {"event": encode(item.event_reference), "source_dataset": item.source_dataset}
                    for item in self.context.provenance
                ],
            },
            "nodes": [
                {"reference": encode(node.reference), "service_name": node.event.service_name,
                 "signal_type": node.event.signal_type.value,
                 "event_timestamp": node.event.event_timestamp.isoformat().replace("+00:00", "Z"),
                 "event_fingerprint": node.event.contract_fingerprint}
                for node in self.nodes
            ],
            "service_nodes": [
                {"service_name": node.service_name,
                 "event_references": [encode(ref) for ref in node.event_references]}
                for node in self.service_nodes
            ],
            "edges": [edge.to_dict() for edge in self.edges],
            "rule_versions": {
                key: value if isinstance(value, str) else list(value)
                for key, value in sorted(self.rule_versions.items())
            },
            "policy_provenance": canonical_json_value(self.policy_provenance),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def graph_fingerprint(self) -> str:
        """SHA-256 contratual do contexto, nós, arestas, taxonomia e rule versions."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ServiceGraphFeatures:
    service_name: str
    depth: int
    directed_in_degree: int
    directed_out_degree: int
    undirected_degree: int
    centrality: float
    structural_centrality: float
    plausible_causal_centrality: float
    all_evidence_centrality: float
    affected_descendants: int
    anomalous_upstream: int
    anomalous_downstream: int
    shortest_path_to_first_failure: int
    causal_candidate_edge_weight: float
    event_chain_count: int
    business_transition_distance: int

    @property
    def in_degree(self):
        return self.directed_in_degree

    @property
    def out_degree(self):
        return self.directed_out_degree


@dataclass(frozen=True)
class PruningExclusion:
    service_name: str
    rule: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.service_name, self.rule)):
            raise ValueError("PruningExclusion requer serviço e regra não vazios.")


@dataclass(frozen=True)
class PruningResult:
    candidates: tuple[str, ...]
    before_count: int
    after_count: int
    exclusions: tuple[PruningExclusion, ...]
    enabled: bool
    incident_id: str
    workflow_key: str
    graph_fingerprint: str
    policy_version: str
    symptom_references: tuple[EventReference, ...]
    protected_ground_truth: tuple[str, ...] = ()
    mode: str = "production"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled deve ser booleano.")
        if any(not isinstance(item, str) or not item.strip() for item in self.candidates):
            raise ValueError("candidates deve conter strings não vazias.")
        if tuple(sorted(set(self.candidates))) != self.candidates:
            raise ValueError("candidates deve ser único e ordenado.")
        for name in ("before_count", "after_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if self.after_count != len(self.candidates):
            raise ValueError("after_count deve corresponder a len(candidates).")
        if self.mode == "production" and self.before_count < self.after_count:
            raise ValueError("before_count não pode ser menor que after_count em production.")
        if any(not isinstance(item, PruningExclusion) for item in self.exclusions):
            raise TypeError("exclusions deve conter PruningExclusion.")
        excluded_services = [item.service_name for item in self.exclusions]
        if len(excluded_services) != len(set(excluded_services)):
            raise ValueError("Serviços de exclusions devem ser únicos.")
        for name in ("incident_id", "workflow_key", "graph_fingerprint", "policy_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"PruningResult.{name} deve ser texto não vazio.")
        if any(not isinstance(item, EventReference) for item in self.symptom_references):
            raise TypeError("symptom_references deve conter EventReference.")
        if not self.symptom_references:
            raise ValueError("symptom_references não pode ser vazio.")
        if len(self.symptom_references) != len(set(self.symptom_references)):
            raise ValueError("symptom_references deve ser único.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.graph_fingerprint):
            raise ValueError("graph_fingerprint deve ser SHA-256 hexadecimal.")
        object.__setattr__(self, "symptom_references",
                           tuple(sorted(self.symptom_references, key=reference_key)))
        if self.mode not in {"production", "disabled_ablation", "evaluation_audit"}:
            raise ValueError("mode de pruning inválido.")
        if self.mode == "production" and not self.enabled:
            raise ValueError("Pruning production deve estar enabled.")
        if self.mode == "disabled_ablation" and self.enabled:
            raise ValueError("Pruning disabled_ablation não pode estar enabled.")
        if self.protected_ground_truth and self.mode != "evaluation_audit":
            raise ValueError("Ground truth protegido exige evaluation_audit.")
        if not set(self.protected_ground_truth) <= set(self.candidates):
            raise ValueError("protected_ground_truth deve ser subconjunto de candidates.")
        if any(not isinstance(item, str) or not item.strip() for item in self.protected_ground_truth):
            raise ValueError("protected_ground_truth deve conter strings não vazias.")
        if tuple(sorted(set(self.protected_ground_truth))) != self.protected_ground_truth:
            raise ValueError("protected_ground_truth deve ser único e ordenado.")
        excluded_candidates = {item.service_name for item in self.exclusions} & set(self.candidates)
        if excluded_candidates - set(self.protected_ground_truth):
            raise ValueError("Serviço excluído não pode permanecer em candidates.")
        if self.mode == "production" and self.before_count != self.after_count + len(excluded_services):
            raise ValueError("Counts production devem satisfazer before=after+exclusions únicas.")
        if self.mode == "disabled_ablation" and (
            self.before_count != self.after_count or self.exclusions
        ):
            raise ValueError("disabled_ablation requer counts iguais e exclusions vazias.")

    def to_dict(self) -> dict[str, Any]:
        encode = lambda ref: {"event_id": ref.event_id, "source_reference": ref.source_reference}
        return {
            "candidates": list(self.candidates), "before_count": self.before_count,
            "after_count": self.after_count,
            "exclusions": [{"service_name": item.service_name, "rule": item.rule}
                           for item in self.exclusions],
            "enabled": self.enabled, "incident_id": self.incident_id,
            "workflow_key": self.workflow_key, "graph_fingerprint": self.graph_fingerprint,
            "policy_version": self.policy_version,
            "symptom_references": [encode(ref) for ref in self.symptom_references],
            "protected_ground_truth": list(self.protected_ground_truth), "mode": self.mode,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def pruning_fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
