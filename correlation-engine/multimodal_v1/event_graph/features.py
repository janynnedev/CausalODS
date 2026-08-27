from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json

from multimodal_v1.anomaly import AnomalyPolicyV1, DEFAULT_ANOMALY_POLICY
from multimodal_v1.event_graph.contracts import (
    EdgeClassification, EdgeDirection, EventEvidenceGraph, RelationType,
    ServiceGraphFeatures, reference_key,
)


@dataclass(frozen=True)
class GraphFeaturePolicy:
    version: str = "event-graph-features-v1"
    structural_adjacency: tuple[EdgeClassification, ...] = (EdgeClassification.STRUCTURAL,)
    plausible_causal_adjacency: tuple[EdgeClassification, ...] = (
        EdgeClassification.STRUCTURAL,
        EdgeClassification.SEMANTIC,
        EdgeClassification.CAUSAL_CANDIDATE,
    )
    all_evidence_adjacency: None = None

    def to_dict(self):
        encode = lambda values: None if values is None else [item.value for item in values]
        return {"version": self.version,
                "structural_adjacency": encode(self.structural_adjacency),
                "plausible_causal_adjacency": encode(self.plausible_causal_adjacency),
                "all_evidence_adjacency": encode(self.all_evidence_adjacency)}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()


def _distance(adjacency, sources, targets):
    targets = set(targets)
    if not targets:
        return -1
    queue = deque((source, 0) for source in sorted(set(sources), key=reference_key))
    visited = {source for source, _ in queue}
    while queue:
        node, distance = queue.popleft()
        if node in targets:
            return distance
        for target in adjacency.get(node, ()):
            if target not in visited:
                visited.add(target)
                queue.append((target, distance + 1))
    return -1


def _reachable(adjacency, sources):
    if not adjacency or not any(source in adjacency for source in sources):
        return set()
    initial = set(sources)
    visited = set(initial)
    queue = deque(sorted(visited, key=reference_key))
    while queue:
        node = queue.popleft()
        for target in adjacency.get(node, ()):
            if target not in visited:
                visited.add(target)
                queue.append(target)
    return visited - initial


def _distances_from_sources(adjacency, sources):
    """One BFS for every node instead of one BFS per destination node."""
    ordered = tuple(sorted(set(sources), key=reference_key))
    distances = {source: 0 for source in ordered}
    queue = deque(ordered)
    while queue:
        node = queue.popleft()
        distance = distances[node] + 1
        for target in adjacency.get(node, ()):
            if target not in distances:
                distances[target] = distance
                queue.append(target)
    return distances


def _freeze_adjacency(values):
    return {source: tuple(sorted(targets, key=reference_key))
            for source, targets in values.items() if targets}


class GraphFeatureExtractor:
    """Features RCA v3 usam alcance plausível, nunca temporal/coanomalia isolados."""

    def __init__(self, policy: GraphFeaturePolicy | None = None,
                 anomaly_policy: AnomalyPolicyV1 | None = None):
        self.policy = policy or GraphFeaturePolicy()
        self.anomaly_policy = anomaly_policy or DEFAULT_ANOMALY_POLICY

    def extract(self, graph: EventEvidenceGraph) -> dict[str, ServiceGraphFeatures]:
        events = {node.reference: node.event for node in graph.nodes}
        by_service = {}
        for reference, event in events.items():
            if event.service_name:
                by_service.setdefault(event.service_name, []).append(reference)
        structural_raw, plausible_raw, plausible_reverse_raw = {}, {}, {}
        semantic_reverse_raw = {}
        directed_in, directed_out, undirected_degree = {}, {}, {}
        structural_degree, plausible_degree = {}, {}
        causal_weights = {}
        structural_targets = set()
        business_targets = set()

        def connect(mapping, source, target):
            mapping.setdefault(source, set()).add(target)

        def increment(mapping, service):
            if service:
                mapping[service] = mapping.get(service, 0) + 1

        for edge in graph.edges:
            source_service = events[edge.source].service_name
            target_service = events[edge.target].service_name
            if edge.direction is EdgeDirection.UNDIRECTED:
                increment(undirected_degree, source_service)
                increment(undirected_degree, target_service)
            else:
                increment(directed_out, source_service)
                increment(directed_in, target_service)
            if edge.classification is EdgeClassification.STRUCTURAL and edge.direction is EdgeDirection.DIRECTED:
                structural_targets.add(edge.target)
            if edge.classification in self.policy.structural_adjacency:
                connect(structural_raw, edge.source, edge.target)
                increment(structural_degree, source_service)
                increment(structural_degree, target_service)
                if edge.direction is EdgeDirection.UNDIRECTED:
                    connect(structural_raw, edge.target, edge.source)
            if edge.classification in self.policy.plausible_causal_adjacency:
                connect(plausible_raw, edge.source, edge.target)
                connect(plausible_reverse_raw, edge.target, edge.source)
                increment(plausible_degree, source_service)
                increment(plausible_degree, target_service)
                if edge.direction is EdgeDirection.UNDIRECTED:
                    connect(plausible_raw, edge.target, edge.source)
                    connect(plausible_reverse_raw, edge.source, edge.target)
            if edge.classification is EdgeClassification.SEMANTIC:
                connect(semantic_reverse_raw, edge.target, edge.source)
                if edge.direction is EdgeDirection.UNDIRECTED:
                    connect(semantic_reverse_raw, edge.source, edge.target)
            if edge.classification is EdgeClassification.CAUSAL_CANDIDATE:
                for service in (source_service, target_service):
                    if service:
                        causal_weights[service] = max(causal_weights.get(service, 0.0), edge.weight)
            if edge.relation_type is RelationType.BUSINESS_TRANSITION:
                business_targets.add(edge.target)

        structural = _freeze_adjacency(structural_raw)
        plausible = _freeze_adjacency(plausible_raw)
        plausible_reverse = _freeze_adjacency(plausible_reverse_raw)
        semantic_reverse = _freeze_adjacency(semantic_reverse_raw)
        roots = set(events) - structural_targets
        structural_distances = _distances_from_sources(structural, roots)
        anomalies = {reference for reference, event in events.items()
                     if self.anomaly_policy.is_anomalous(event)}
        first_failure = ()
        if anomalies:
            first_failure = (min(anomalies, key=lambda ref: (events[ref].event_timestamp, *reference_key(ref))),)
        failure_distances = _distances_from_sources(plausible_reverse, first_failure)
        business_distances = _distances_from_sources(semantic_reverse, business_targets)
        result = {}
        for service, service_events in sorted(by_service.items()):
            descendants = _reachable(plausible, service_events)
            upstream = _reachable(plausible_reverse, service_events)
            descendant_anomalies = anomalies & descendants
            upstream_anomalies = anomalies & upstream
            affected_services = {
                events[ref].service_name for ref in descendant_anomalies
                if events[ref].service_name and events[ref].service_name != service
            }
            in_degree = directed_in.get(service, 0)
            out_degree = directed_out.get(service, 0)
            symmetric_degree = undirected_degree.get(service, 0)
            centrality = min(
                1.0, (in_degree + out_degree + symmetric_degree) / max(1, 2 * (len(events) - 1))
            )
            structural_centrality = min(
                1.0, structural_degree.get(service, 0) / max(1, 2 * (len(events) - 1)))
            plausible_centrality = min(
                1.0, plausible_degree.get(service, 0) / max(1, 2 * (len(events) - 1)))
            valid_depths = [structural_distances[reference] for reference in service_events
                            if reference in structural_distances]
            failure_depths = [failure_distances[reference] for reference in service_events
                              if reference in failure_distances]
            business_depths = [business_distances[reference] for reference in service_events
                               if reference in business_distances]
            result[service] = ServiceGraphFeatures(
                service_name=service,
                depth=min(valid_depths) if valid_depths else -1,
                directed_in_degree=in_degree,
                directed_out_degree=out_degree,
                undirected_degree=symmetric_degree,
                centrality=centrality,
                structural_centrality=structural_centrality,
                plausible_causal_centrality=plausible_centrality,
                all_evidence_centrality=centrality,
                affected_descendants=len(affected_services),
                anomalous_upstream=len(upstream_anomalies),
                anomalous_downstream=len(descendant_anomalies),
                shortest_path_to_first_failure=min(failure_depths) if failure_depths else -1,
                causal_candidate_edge_weight=causal_weights.get(service, 0.0),
                event_chain_count=len(descendants),
                business_transition_distance=min(business_depths) if business_depths else -1,
            )
        return result
