from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import time

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
        return self._build(bundle, topology=topology, derived_at=derived_at, profile=None)

    def build_profiled(self, bundle: IncidentEvidenceBundle, *,
                       topology: Mapping[str, Iterable[str]] | None = None,
                       derived_at: datetime | None = None,
                       operation_counts: dict[str, int] | None = None,
                       ) -> tuple[EventEvidenceGraph, dict[str, object]]:
        """Build the identical graph while exposing wall-clock stages.

        The returned diagnostics are deliberately outside EventEvidenceGraph so
        profiling never participates in fingerprints or scientific features.
        """
        profile: dict[str, object] = {"edge_rules_seconds": {}, "edge_rules_edges": {}}
        graph = self._build(
            bundle, topology=topology, derived_at=derived_at, profile=profile,
            operation_counts=operation_counts,
        )
        return graph, profile

    def _build(self, bundle: IncidentEvidenceBundle, *,
               topology: Mapping[str, Iterable[str]] | None,
               derived_at: datetime | None,
               profile: dict[str, object] | None,
               operation_counts: dict[str, int] | None = None) -> EventEvidenceGraph:
        total_started = time.perf_counter()
        if not isinstance(bundle, IncidentEvidenceBundle):
            raise TypeError("EventGraphBuilder.build requer IncidentEvidenceBundle.")
        if any(not isinstance(event, UnifiedEvent) for event in bundle.events):
            raise TypeError("IncidentEvidenceBundle contém evento inválido.")
        stage_started = time.perf_counter()
        ordered = tuple(sorted(bundle.events, key=lambda event: (
            event.event_timestamp, *reference_key(EventReference(event.event_id, event.source_reference))
        )))
        if profile is not None:
            profile["sorting_seconds"] = time.perf_counter() - stage_started
        derived = derived_at or max(
            ((event.ingested_timestamp or event.observed_timestamp or event.event_timestamp) for event in ordered),
            default=bundle.analysis_window.end,
        )
        if not isinstance(derived, datetime) or derived.tzinfo is None or derived.utcoffset() is None:
            raise ValueError("derived_at deve ser timezone-aware.")
        normalized_topology = self._topology(topology)
        stage_started = time.perf_counter()
        reference_pool = {
            reference: reference for reference in bundle.association_scores
        }
        references = {}
        event_references = {}
        spans = defaultdict(list)
        correlation_identities = defaultdict(list)
        by_service = defaultdict(list)
        for event in ordered:
            candidate = EventReference(event.event_id, event.source_reference)
            reference = reference_pool.get(candidate, candidate)
            references[reference] = event
            event_references[id(event)] = reference
            if event.trace_id and event.span_id:
                spans[(event.trace_id, event.span_id)].append(event)
            if event.service_name:
                by_service[event.service_name].append(event)
        for identity in bundle.identity_evidence:
            if (identity.identity_type == "correlation_id"
                    and identity.event_reference in references):
                correlation_identities[identity.event_reference].append(identity)
        span_observations = {}
        for key, values in spans.items():
            span_observations[key] = tuple(sorted(
                values, key=lambda event: (
                    event.event_timestamp, *reference_key(event_references[id(event)]),
                ),
            ))
        events_by_service = {key: tuple(values) for key, values in by_service.items()}
        service_timestamps = {
            key: tuple(event.event_timestamp for event in values)
            for key, values in events_by_service.items()
        }
        anomalous_events_by_service = {
            key: tuple(event for event in values if self.anomaly_policy.is_anomalous(event))
            for key, values in events_by_service.items()
        }
        correlation_references = defaultdict(set)
        for reference, identities in correlation_identities.items():
            event = references[reference]
            if event.service_name:
                for identity in identities:
                    correlation_references[(
                        event.service_name, identity.identity_value,
                    )].add(reference)
        correlation_events = {
            key: tuple(references[reference] for reference in sorted(values, key=lambda ref: (
                references[ref].event_timestamp, *reference_key(ref))))
            for key, values in correlation_references.items()
        }
        entity_references = defaultdict(set)
        for reference, event in references.items():
            if (event.service_name and event.business_entity_type
                    and event.business_entity_id):
                entity_references[(
                    event.service_name, event.business_entity_type,
                    event.business_entity_id,
                )].add(reference)
        entity_events = {
            key: tuple(references[reference] for reference in sorted(values, key=lambda ref: (
                references[ref].event_timestamp, *reference_key(ref))))
            for key, values in entity_references.items()
        }
        structural_references = defaultdict(set)
        for child in ordered:
            if not child.trace_id or not child.parent_span_id:
                continue
            child_ref = event_references[id(child)]
            for parent in span_observations.get((child.trace_id, child.parent_span_id), ()):
                parent_ref = event_references[id(parent)]
                structural_references[child_ref].add(parent_ref)
                structural_references[parent_ref].add(child_ref)
        structural_neighbors = {
            reference: tuple(references[target] for target in sorted(values, key=lambda ref: (
                references[ref].event_timestamp, *reference_key(ref))))
            for reference, values in structural_references.items()
        }
        if profile is not None:
            profile["indexes_seconds"] = time.perf_counter() - stage_started
        context = RuleContext(
            events=ordered, references=references,
            derived_at=derived.astimezone(timezone.utc),
            temporal_window=self.temporal_window, topology=normalized_topology,
            identity_evidence=bundle.identity_evidence,
            business_policy=self.business_policy, topology_policy=self.topology_policy,
            anomaly_policy=self.anomaly_policy, event_references=event_references,
            span_observations=span_observations,
            correlation_identities_by_reference={
                key: tuple(values) for key, values in correlation_identities.items()
            },
            events_by_service=events_by_service, service_timestamps=service_timestamps,
            anomalous_events_by_service=anomalous_events_by_service,
            correlation_events_by_service_value=correlation_events,
            entity_events_by_service_value=entity_events,
            structural_neighbors_by_reference=structural_neighbors,
            operation_counts=operation_counts,
        )
        edges: dict[tuple, EventGraphEdge] = {}
        versions = {}
        for relation_type, version, rule in self.registry.rules():
            versions[relation_type.value] = version
            rule_started = time.perf_counter()
            before_count = len(edges)
            for edge in rule(context):
                if edge.source == edge.target:
                    continue
                self._add_edge(edges, replace(edge, rule_version=version))
            if profile is not None:
                profile["edge_rules_seconds"][rule.__name__] = time.perf_counter() - rule_started
                profile["edge_rules_edges"][rule.__name__] = len(edges) - before_count
        stage_started = time.perf_counter()
        nodes = tuple(EventGraphNode(reference, event) for reference, event in sorted(
            references.items(), key=lambda item: (
                item[1].event_timestamp, *reference_key(item[0])
            )
        ))
        if profile is not None:
            profile["event_graph_nodes_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        service_nodes = self._service_nodes(ordered) if self.include_service_nodes else ()
        if profile is not None:
            profile["service_graph_nodes_seconds"] = time.perf_counter() - stage_started
        compact_scores = bool(getattr(
            bundle.association_scores,
            "_causalods_immutable_frozen_boundary", False,
        ))
        graph_context = IncidentGraphContext(
            bundle.incident_id, bundle.workflow_key, bundle.lifecycle_generation,
            {} if compact_scores else bundle.association_scores,
            bundle.association_reasons, bundle.identity_evidence,
            bundle.external_identity_evidence, bundle.provenance, bundle.late_events,
        )
        if compact_scores:
            # The experimental frozen boundary is already immutable.  Avoid a
            # defensive multi-million-entry dict that would duplicate the
            # reference_pool at the graph's peak allocation phase.
            object.__setattr__(graph_context, "association_scores",
                               bundle.association_scores)
        # Materialize the final immutable edge sequence before contract
        # validation, then release the mutable rule indexes.  Keeping both the
        # edge dictionary and every RuleContext index alive through
        # EventEvidenceGraph.__post_init__ doubled the transient footprint for
        # multi-million-event incidents without contributing to the graph.
        ordered_edges = tuple(edges[key] for key in sorted(edges, key=self._edge_key))
        del edges
        context = None
        del reference_pool, references, event_references
        del spans, correlation_identities, by_service, span_observations
        del events_by_service, service_timestamps, anomalous_events_by_service
        del correlation_references, correlation_events, entity_references, entity_events
        del structural_references, structural_neighbors
        stage_started = time.perf_counter()
        graph = EventEvidenceGraph(
            graph_context, nodes, ordered_edges,
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
        if profile is not None:
            profile["event_evidence_graph_post_init_seconds"] = time.perf_counter() - stage_started
            profile["total_seconds"] = time.perf_counter() - total_started
        return graph

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
