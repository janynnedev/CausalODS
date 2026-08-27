from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Mapping

from multimodal_v1.anomaly import AnomalyPolicyV1, DEFAULT_ANOMALY_POLICY
from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence.contracts import IncidentEvidenceBundle
from multimodal_v1.evidence.contracts import EventReference
from multimodal_v1.event_graph import GraphFeatureExtractor
from multimodal_v1.event_graph.features import GraphFeaturePolicy
from multimodal_v1.event_graph.contracts import (
    EdgeDirection, EventEvidenceGraph, PruningResult, RelationType, reference_key,
)
from multimodal_v1.features_v3.baselines import (
    BaselineSnapshotV3, event_latency_ms, log_template, metric_kind,
)
from multimodal_v1.features_v3.contracts import RCAFeatureVectorV3
from multimodal_v1.features_v3.registry import FeatureRegistryV3


FAILURES = frozenset({"failed", "failure", "rejected", "error", "declined"})
SUCCESSES = frozenset({"approved", "success", "succeeded", "completed", "accepted", "created"})


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * .95
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _ratio(expected: int | None, observed: int) -> float | None:
    if expected is None or expected <= 0:
        return None
    return max(0.0, min(1.0, (expected - observed) / expected))


def _expected(events: list[UnifiedEvent], key: str) -> int | None:
    candidates = []
    for event in events:
        for mapping in (event.attributes, event.resource_attributes):
            value = mapping.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                candidates.append(value)
    return max(candidates, default=None)


class RCAFeatureExtractorV3:
    """Extrai features observacionais; labels/ground truth não pertencem a esta API."""

    def __init__(self, registry: FeatureRegistryV3 | None = None, *,
                 graph_feature_policy: GraphFeaturePolicy | None = None,
                 anomaly_policy: AnomalyPolicyV1 | None = None):
        self.registry = registry or FeatureRegistryV3()
        self.graph_feature_policy = graph_feature_policy or GraphFeaturePolicy()
        self.anomaly_policy = anomaly_policy or DEFAULT_ANOMALY_POLICY

    def extract(self, bundle: IncidentEvidenceBundle, graph: EventEvidenceGraph, *,
                baselines: BaselineSnapshotV3 | None = None,
                pruning: PruningResult | None = None,
                topology: Mapping[str, Iterable[str]] | None = None) -> tuple[RCAFeatureVectorV3, ...]:
        if not isinstance(bundle, IncidentEvidenceBundle):
            raise TypeError("bundle deve ser IncidentEvidenceBundle.")
        if not isinstance(graph, EventEvidenceGraph):
            raise TypeError("graph deve ser EventEvidenceGraph.")
        if graph.context.incident_id != bundle.incident_id:
            raise ValueError("Grafo e bundle pertencem a incidentes diferentes.")
        if graph.context.workflow_key != bundle.workflow_key:
            raise ValueError("workflow_key diverge entre grafo e bundle.")
        if graph.context.lifecycle_generation != bundle.lifecycle_generation:
            raise ValueError("lifecycle_generation diverge entre grafo e bundle.")
        bundle_events = {
            EventReference(event.event_id, event.source_reference): event for event in bundle.events
        }
        graph_events = {node.reference: node.event for node in graph.nodes}
        graph_refs = set(graph_events)
        if set(bundle_events) != graph_refs:
            raise ValueError("Conjunto de EventReference diverge entre grafo e bundle.")
        # EventGraphBuilder retains the exact UnifiedEvent objects.  Only copied
        # or externally constructed graphs need the expensive canonical hash.
        if any(
            graph_events[reference] is not event
            and graph_events[reference].contract_fingerprint != event.contract_fingerprint
            for reference, event in bundle_events.items()
        ):
            raise ValueError("Payload canônico dos eventos diverge entre grafo e bundle.")
        if (graph.context.association_scores != bundle.association_scores
                or graph.context.association_reasons != bundle.association_reasons
                or graph.context.identity_evidence != bundle.identity_evidence
                or graph.context.external_identity_evidence != bundle.external_identity_evidence
                or graph.context.provenance != bundle.provenance
                or graph.context.late_events != bundle.late_events):
            raise ValueError("Contexto auditável diverge entre grafo e bundle.")
        if pruning is not None:
            if pruning.incident_id != bundle.incident_id:
                raise ValueError("PruningResult pertence a outro incident_id.")
            if pruning.workflow_key != bundle.workflow_key:
                raise ValueError("PruningResult pertence a outro workflow_key.")
            if pruning.graph_fingerprint != graph.graph_fingerprint:
                raise ValueError("PruningResult pertence a outro EventEvidenceGraph.")
            if not pruning.policy_version.strip():
                raise ValueError("PruningResult requer policy_version.")
            if pruning.mode not in {"production", "disabled_ablation"}:
                raise ValueError("Pruning evaluation_audit não pode alimentar features.")
            if pruning.protected_ground_truth:
                raise ValueError("Ground truth protegido não pode alimentar pruning_survival.")
            if not set(pruning.symptom_references) <= graph_refs:
                raise ValueError("PruningResult contém sintoma ausente do grafo.")
            # Força a serialização canônica integral; contratos inválidos não chegam aqui.
            if len(pruning.pruning_fingerprint) != 64:
                raise ValueError("Fingerprint canônico do pruning inválido.")
        normalized_topology = self._topology(topology)
        graph_features = GraphFeatureExtractor(
            self.graph_feature_policy, self.anomaly_policy
        ).extract(graph)
        by_service: dict[str, list[UnifiedEvent]] = defaultdict(list)
        references_by_service: dict[str, list[EventReference]] = defaultdict(list)
        for event in bundle.events:
            if event.service_name:
                by_service[event.service_name].append(event)
        for reference, event in graph_events.items():
            if event.service_name:
                references_by_service[event.service_name].append(reference)
        for evidence in bundle.services:
            by_service.setdefault(evidence.identity.service_name, [])
        for node in graph.service_nodes:
            by_service.setdefault(node.service_name, [])
        expected_pairs = {
            (edge.source, edge.target) for edge in graph.edges
            if edge.relation_type is RelationType.BUSINESS_TRANSITION
        }
        plausible_raw = defaultdict(set)
        for edge in graph.edges:
            if edge.classification not in self.graph_feature_policy.plausible_causal_adjacency:
                continue
            plausible_raw[edge.source].add(edge.target)
            if edge.direction is EdgeDirection.UNDIRECTED:
                plausible_raw[edge.target].add(edge.source)
        plausible = {
            source: tuple(sorted(targets, key=reference_key))
            for source, targets in plausible_raw.items() if targets
        }
        instrumented = {event.service_name for event in bundle.events if event.service_name}
        return tuple(
            self._service(bundle, service, sorted(events, key=lambda e: (e.event_timestamp, e.event_id, e.source_reference or "")),
                          graph, graph_features.get(service), baselines, pruning,
                          normalized_topology, expected_pairs, graph_refs, plausible,
                          graph_events, instrumented,
                          set(references_by_service.get(service, ())))
            for service, events in sorted(by_service.items())
        )

    @staticmethod
    def _topology(topology: Mapping[str, Iterable[str]] | None) -> dict[str, frozenset[str]] | None:
        if topology is None:
            return None
        if not isinstance(topology, Mapping):
            raise TypeError("topology deve ser mapping.")
        result: dict[str, set[str]] = defaultdict(set)
        for source, targets in topology.items():
            if not isinstance(source, str) or not source.strip() or isinstance(targets, (str, bytes)):
                raise ValueError("Topologia contém serviço ou adjacência inválida.")
            for target in targets:
                if not isinstance(target, str) or not target.strip():
                    raise ValueError("Topologia contém vizinho inválido.")
                result[source].add(target)
                result[target].add(source)
        return {key: frozenset(value) for key, value in result.items()}

    def _service(self, bundle, service, events, graph, gf, baselines, pruning, topology,
                 expected_pairs, known_references, plausible, graph_events,
                 instrumented, service_refs):
        traces = [event for event in events if event.signal_type is SignalType.TRACE]
        logs = [event for event in events if event.signal_type is SignalType.LOG]
        metrics = [event for event in events if event.signal_type is SignalType.METRIC]
        business = [event for event in events if event.signal_type is SignalType.BUSINESS_EVENT]
        latencies = [value for event in traces if (value := event_latency_ms(event)) is not None]
        latency_mean = sum(latencies) / len(latencies) if latencies else None
        latency_stat = baselines.statistic(service, "latency") if baselines and latency_mean is not None else None
        metric_values: dict[str, dict[tuple[str, str | None, str | None], list[tuple[UnifiedEvent, float]]]] = defaultdict(lambda: defaultdict(list))
        for event in metrics:
            kind = metric_kind(event.metric_name)
            if kind and event.metric_value is not None:
                identity = (event.metric_name, event.metric_unit, event.metric_aggregation)
                metric_values[kind][identity].append((event, float(event.metric_value)))
        def selected(kind):
            series = metric_values.get(kind, {})
            if not series:
                return []
            baseline_selection = baselines.metric_selection(service, kind) if baselines else None
            if baseline_selection:
                identity = baseline_selection.identity
                return series.get((identity.metric_name, identity.metric_unit, identity.metric_aggregation), [])
            key = min(series, key=lambda item: (-len(series[item]), repr(item)))
            return series[key]
        def last(kind):
            samples = selected(kind)
            return samples[-1][1] if samples else None
        def stat(kind):
            return baselines.statistic(service, kind) if baselines else None
        def zscore(kind):
            value, baseline = last(kind), stat(kind)
            return baseline.zscore(value) if value is not None and baseline else None
        def delta(kind):
            value, baseline = last(kind), stat(kind)
            return value - baseline.mean if value is not None and baseline else None

        templates = {template for event in logs if (template := log_template(event))}
        known = baselines.known_templates(service) if baselines else None
        outcomes = [(event.business_outcome or "").lower() for event in business]
        success_times = [event.event_timestamp for event in business if (event.business_outcome or "").lower() in SUCCESSES]
        entities: dict[tuple[str, str], list[UnifiedEvent]] = defaultdict(list)
        for event in business:
            if event.business_entity_type and event.business_entity_id:
                entities[(event.business_entity_type, event.business_entity_id)].append(event)
        entity_pairs = []
        outcome_change = False
        for entity_events in entities.values():
            entity_events.sort(key=lambda event: (event.event_timestamp, event.event_id, event.source_reference or ""))
            for left, right in zip(entity_events, entity_events[1:]):
                left_ref = EventReference(left.event_id, left.source_reference)
                right_ref = EventReference(right.event_id, right.source_reference)
                if left_ref in known_references and right_ref in known_references:
                    entity_pairs.append((left, right, left_ref, right_ref))
                outcome_change |= (left.business_outcome or "").lower() != (right.business_outcome or "").lower()
        unexpected = sum((left_ref, right_ref) not in expected_pairs
                         for _, _, left_ref, right_ref in entity_pairs)
        anomalous_metrics = 0
        for kind in metric_values:
            samples = selected(kind)
            baseline = stat(kind)
            anomalous_metrics += sum(
                self.anomaly_policy.is_anomalous(event)
                or (baseline is not None and abs(baseline.zscore(value)) >= 3)
                for event, value in samples
            )
        neighbors = topology.get(service, frozenset()) if topology is not None else None
        absent_neighbors = neighbors - instrumented if neighbors is not None else None
        reachable, pending = set(), list(service_refs)
        while pending:
            source = pending.pop()
            for target in plausible.get(source, ()):
                if target not in reachable and target not in service_refs:
                    reachable.add(target)
                    pending.append(target)
        failed_trace_descendants = sum(
            graph_events[reference].signal_type is SignalType.TRACE
            and self.anomaly_policy.is_anomalous(graph_events[reference])
            for reference in reachable
        )
        values = {
            "latency_mean": latency_mean,
            "latency_p95": _p95(latencies),
            "latency_zscore": latency_stat.zscore(latency_mean) if latency_stat and latency_mean is not None else None,
            "error_span_ratio": sum(self.anomaly_policy.is_anomalous(event) for event in traces) / len(traces) if traces else None,
            "distance_to_first_failure": gf.shortest_path_to_first_failure if gf else None,
            "failed_descendant_count": failed_trace_descendants,
            "log_error_count": sum(self.anomaly_policy.is_anomalous(event) for event in logs),
            "log_warn_count": sum((event.severity or "").upper() in {"WARN", "WARNING"} for event in logs),
            "exception_present": any(event.error_type or "exception" in (event.event_name or "").lower() for event in logs),
            "distinct_error_type_count": len({event.error_type for event in logs if event.error_type}),
            "novel_log_template_count": len(templates - known) if known is not None else None,
            "cpu_zscore": zscore("cpu"), "memory_zscore": zscore("memory"),
            "request_rate_delta": delta("request_rate"), "error_rate_delta": delta("error_rate"),
            "saturation_score": max((value for _, value in selected("saturation")), default=None),
            "anomalous_metric_count": anomalous_metrics,
            "business_failure_count": sum(outcome in FAILURES for outcome in outcomes),
            "rejected_transition_count": sum(
                (left_ref, right_ref) in expected_pairs
                and (right.business_outcome or "").lower() in {"rejected", "declined"}
                for _, right, left_ref, right_ref in entity_pairs
            ),
            "unexpected_transition_count": unexpected,
            "time_since_last_success": (bundle.analysis_window.end - max(success_times)).total_seconds() * 1000 if success_times else None,
            "business_outcome_change": outcome_change,
            "service_depth": gf.depth if gf else None,
            "in_degree": gf.directed_in_degree if gf else None,
            "out_degree": gf.directed_out_degree if gf else None,
            "centrality": gf.centrality if gf else None,
            "affected_descendant_count": gf.affected_descendants if gf else None,
            "upstream_anomaly_count": gf.anomalous_upstream if gf else None,
            "downstream_anomaly_count": gf.anomalous_downstream if gf else None,
            "event_chain_count": gf.event_chain_count if gf else None,
            "pruning_survival": (service in pruning.candidates
                                  if pruning is not None and pruning.mode == "production" else None),
            "missing_trace": not traces, "missing_logs": not logs, "missing_metrics": not metrics,
            "missing_business_events": not business,
            "span_loss_ratio": _ratio(_expected(events, "expected_span_count"), len(traces)),
            "log_loss_ratio": _ratio(_expected(events, "expected_log_count"), len(logs)),
            "metric_delay_ms": max((event.late_arrival_ms for event in metrics
                                     if event.late_arrival_ms is not None), default=None),
            "uninstrumented_neighbor_count": len(absent_neighbors) if absent_neighbors is not None else None,
            "topology_staleness_score": len(absent_neighbors) / len(neighbors) if neighbors else (0.0 if neighbors is not None else None),
        }
        if set(values) != set(self.registry.names):
            raise RuntimeError("Extrator e Feature Registry v3 divergiram.")
        return RCAFeatureVectorV3(bundle.incident_id, service, {name: values[name] for name in self.registry.names})
