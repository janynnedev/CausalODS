from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from multimodal_v1.anomaly import is_anomalous
from multimodal_v1.event_graph.contracts import (
    EdgeClassification, EdgeDirection, EventEvidenceGraph, RelationType,
    ServiceGraphFeatures, reference_key,
)
from multimodal_v1.event_graph.paths import GraphPathExtractor


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


class GraphFeatureExtractor:
    """Features RCA v3 usam alcance plausível, nunca temporal/coanomalia isolados."""

    def __init__(self, policy: GraphFeaturePolicy | None = None):
        self.policy = policy or GraphFeaturePolicy()

    def extract(self, graph: EventEvidenceGraph) -> dict[str, ServiceGraphFeatures]:
        events = {node.reference: node.event for node in graph.nodes}
        by_service = {}
        for reference, event in events.items():
            if event.service_name:
                by_service.setdefault(event.service_name, []).append(reference)
        extractor = GraphPathExtractor(graph)
        structural = extractor.adjacency(classifications=self.policy.structural_adjacency)
        plausible = extractor.adjacency(classifications=self.policy.plausible_causal_adjacency)
        plausible_reverse = extractor.adjacency(
            classifications=self.policy.plausible_causal_adjacency, reverse=True
        )
        directed_in = {reference: 0 for reference in events}
        directed_out = {reference: 0 for reference in events}
        undirected_degree = {reference: 0 for reference in events}
        for edge in graph.edges:
            if edge.direction is EdgeDirection.UNDIRECTED:
                undirected_degree[edge.source] += 1
                undirected_degree[edge.target] += 1
            else:
                directed_out[edge.source] += 1
                directed_in[edge.target] += 1
        structural_in = {reference: 0 for reference in events}
        for edge in graph.edges:
            if edge.classification is EdgeClassification.STRUCTURAL and edge.direction is EdgeDirection.DIRECTED:
                structural_in[edge.target] += 1
        roots = {reference for reference, count in structural_in.items() if count == 0}
        anomalies = {reference for reference, event in events.items() if is_anomalous(event)}
        first_failure = ()
        if anomalies:
            first_failure = (min(anomalies, key=lambda ref: (events[ref].event_timestamp, *reference_key(ref))),)
        business_targets = {
            edge.target for edge in graph.edges if edge.relation_type is RelationType.BUSINESS_TRANSITION
        }
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
            in_degree = sum(directed_in[ref] for ref in service_events)
            out_degree = sum(directed_out[ref] for ref in service_events)
            symmetric_degree = sum(undirected_degree[ref] for ref in service_events)
            centrality = min(
                1.0, (in_degree + out_degree + symmetric_degree) / max(1, 2 * (len(events) - 1))
            )
            def view_centrality(classifications):
                degree = sum(
                    1
                    for edge in graph.edges
                    if edge.classification in classifications
                    for endpoint in (edge.source, edge.target)
                    if endpoint in service_events
                )
                return min(1.0, degree / max(1, 2 * (len(events) - 1)))

            structural_centrality = view_centrality(set(self.policy.structural_adjacency))
            plausible_centrality = view_centrality(set(self.policy.plausible_causal_adjacency))
            causal_weights = [
                edge.weight for edge in graph.edges
                if edge.classification is EdgeClassification.CAUSAL_CANDIDATE
                and (edge.source in service_events or edge.target in service_events)
            ]
            depths = [_distance(structural, roots, (reference,)) for reference in service_events]
            valid_depths = [value for value in depths if value >= 0]
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
                shortest_path_to_first_failure=_distance(plausible, service_events, first_failure),
                causal_candidate_edge_weight=max(causal_weights, default=0.0),
                event_chain_count=len(descendants),
                business_transition_distance=_distance(
                    extractor.adjacency(classifications=(EdgeClassification.SEMANTIC,)),
                    service_events, business_targets,
                ),
            )
        return result
