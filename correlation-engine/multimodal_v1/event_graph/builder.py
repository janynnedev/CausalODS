from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.anomaly import AnomalyPolicyV1, DEFAULT_ANOMALY_POLICY
from multimodal_v1.evidence.contracts import EventReference, IncidentEvidenceBundle
from multimodal_v1.event_graph.contracts import (
    EventEvidenceGraph, EventGraphEdge, EventGraphNode, IncidentGraphContext,
    ServiceGraphNode, reference_key,
)
from multimodal_v1.event_graph.rules import (
    BusinessTransitionPolicy, EdgeRuleRegistry, RuleContext, TopologyDependencyPolicy,
)


class EventGraphBuilder:
    def __init__(self, registry: EdgeRuleRegistry | None = None, *,
                 temporal_window: timedelta = timedelta(minutes=5),
                 include_service_nodes: bool = True,
                 business_policy: BusinessTransitionPolicy | None = None,
                 topology_policy: TopologyDependencyPolicy | None = None,
                 anomaly_policy: AnomalyPolicyV1 | None = None):
        if temporal_window < timedelta(0):
            raise ValueError("temporal_window não pode ser negativa.")
        self.registry = registry or EdgeRuleRegistry.defaults()
        self.temporal_window = temporal_window
        self.include_service_nodes = include_service_nodes
        self.business_policy = business_policy or BusinessTransitionPolicy()
        self.topology_policy = topology_policy or TopologyDependencyPolicy()
        self.anomaly_policy = anomaly_policy or DEFAULT_ANOMALY_POLICY

    def build(self, bundle: IncidentEvidenceBundle, *,
              topology: Mapping[str, Iterable[str]] | None = None,
              derived_at: datetime | None = None) -> EventEvidenceGraph:
        if not isinstance(bundle, IncidentEvidenceBundle):
            raise TypeError("EventGraphBuilder.build requer IncidentEvidenceBundle.")
        if any(not isinstance(event, UnifiedEvent) for event in bundle.events):
            raise TypeError("IncidentEvidenceBundle contém evento inválido.")
        ordered = tuple(sorted(bundle.events, key=lambda event: (
            event.event_timestamp, *reference_key(EventReference(event.event_id, event.source_reference))
        )))
        derived = derived_at or max(
            ((event.ingested_timestamp or event.observed_timestamp or event.event_timestamp) for event in ordered),
            default=bundle.analysis_window.end,
        )
        if not isinstance(derived, datetime) or derived.tzinfo is None or derived.utcoffset() is None:
            raise ValueError("derived_at deve ser timezone-aware.")
        normalized_topology = self._topology(topology)
        references = {
            EventReference(event.event_id, event.source_reference): event for event in ordered
        }
        context = RuleContext(
            ordered, references, derived.astimezone(timezone.utc), self.temporal_window,
            normalized_topology, bundle.identity_evidence, self.business_policy, self.topology_policy,
            self.anomaly_policy,
        )
        edges: dict[tuple, EventGraphEdge] = {}
        versions = {}
        for relation_type, version, rule in self.registry.rules():
            versions[relation_type.value] = version
            for edge in rule(context):
                if edge.source == edge.target:
                    continue
                self._add_edge(edges, replace(edge, rule_version=version))
        nodes = tuple(EventGraphNode(reference, event) for reference, event in sorted(
            references.items(), key=lambda item: (
                item[1].event_timestamp, *reference_key(item[0])
            )
        ))
        service_nodes = self._service_nodes(ordered) if self.include_service_nodes else ()
        graph_context = IncidentGraphContext(
            bundle.incident_id, bundle.workflow_key, bundle.lifecycle_generation,
            bundle.association_scores, bundle.association_reasons, bundle.identity_evidence,
            bundle.external_identity_evidence, bundle.provenance, bundle.late_events,
        )
        return EventEvidenceGraph(
            graph_context, nodes, tuple(edges[key] for key in sorted(edges, key=self._edge_key)),
            service_nodes, versions, {
                "business_transition_policy": self.business_policy.to_dict(),
                "business_transition_policy_fingerprint": self.business_policy.fingerprint,
                "topology_dependency_policy": self.topology_policy.to_dict(),
                "topology_dependency_policy_fingerprint": self.topology_policy.fingerprint,
                "anomaly_policy": self.anomaly_policy.to_dict(),
                "anomaly_policy_fingerprint": self.anomaly_policy.fingerprint,
                "edge_rule_registry": self.registry.to_dict(),
                "edge_rule_registry_fingerprint": self.registry.fingerprint,
            },
        )

    @staticmethod
    def _add_edge(edges: dict[tuple, EventGraphEdge], edge: EventGraphEdge) -> None:
        existing = edges.get(edge.key)
        if existing is None:
            edges[edge.key] = edge
            return
        comparable_existing = replace(existing, evidence_references=())
        comparable_new = replace(edge, evidence_references=())
        if comparable_existing != comparable_new:
            raise ValueError(f"Arestas conflitantes para a chave {edge.key!r}.")
        evidence = tuple(sorted(
            set(existing.evidence_references) | set(edge.evidence_references), key=reference_key
        ))
        edges[edge.key] = replace(existing, evidence_references=evidence)

    @staticmethod
    def _edge_key(key):
        source, target, relation, version = key
        return (*reference_key(source), *reference_key(target), relation.value, version)

    @staticmethod
    def _topology(topology):
        if topology is None:
            return {}
        if not isinstance(topology, Mapping):
            raise TypeError("topology deve ser mapping de serviço para coleção de serviços.")
        normalized = {}
        for source, targets in topology.items():
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Chaves de topology devem ser strings não vazias.")
            if isinstance(targets, (str, bytes)) or not isinstance(targets, Iterable):
                raise TypeError("Targets de topology devem ser coleção de strings, não string.")
            clean = set()
            for target in targets:
                if not isinstance(target, str) or not target.strip():
                    raise ValueError("Targets de topology devem ser strings não vazias.")
                if target != source:  # self-loop é ignorado explicitamente
                    clean.add(target)
            normalized[source] = tuple(sorted(clean))
        return normalized

    @staticmethod
    def _service_nodes(events):
        grouped = defaultdict(list)
        for event in events:
            if event.service_name:
                grouped[event.service_name].append(EventReference(event.event_id, event.source_reference))
        return tuple(
            ServiceGraphNode(service, tuple(sorted(refs, key=reference_key)))
            for service, refs in sorted(grouped.items())
        )
