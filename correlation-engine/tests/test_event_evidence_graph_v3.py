from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.anomaly import is_anomalous
from multimodal_v1.evidence import (
    EventReference, IncidentEvidenceAssembler, IncidentWindowPolicy,
    TrustedAssociationContext, TrustedIdentityClaim,
)
from multimodal_v1.event_graph import (
    CandidatePruner, EdgeClassification, EdgeDirection, EdgeRuleRegistry,
    EventEvidenceGraph, EventGraphBuilder, EventGraphEdge, EventGraphNode,
    GraphFeatureExtractor, GraphPathExtractor, RelationType, ServiceGraphNode,
)
from multimodal_v1.event_graph.rules import temporal_rule


BASE = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def make_event(event_id, *, seconds=0, service="order-service", signal="trace", name=None, **values):
    payload = dict(
        event_id=event_id, event_name=name or "operation", event_type=signal, signal_type=signal,
        event_timestamp=BASE + timedelta(seconds=seconds), service_name=service,
        source_reference=f"topic:{event_id}",
    )
    if signal == "trace":
        payload.update(trace_id="a" * 32, span_id=(event_id[-1] * 16))
    elif signal == "metric":
        payload.update(metric_name="latency", metric_value=10)
    elif signal == "business_event":
        payload.update(business_operation="transition")
    payload.update(values)
    return UnifiedEvent(**payload)


def make_bundle(events, contexts=None, policy=None):
    assembler = IncidentEvidenceAssembler(policy=policy)
    contexts = contexts or [None] * len(events)
    incident_id = None
    for event, context in zip(events, contexts):
        decision = assembler.ingest(event, context=context)
        incident_id = incident_id or decision.incident_id
    bundles = {bundle.incident_id: bundle for bundle in assembler.active_bundles()}
    return bundles[incident_id]


def ref(event):
    return EventReference(event.event_id, event.source_reference)


def relation_edges(graph, relation):
    return [edge for edge in graph.edges if edge.relation_type is relation]


def order_payment_bundle(*, downstream_error=False):
    root = make_event("root-1", span_id="1" * 16, correlation_id="workflow")
    payment = make_event(
        "pay-2", seconds=1, service="payment-service", span_id="2" * 16,
        parent_span_id="1" * 16, correlation_id="workflow",
        severity="ERROR" if downstream_error else None,
    )
    return make_bundle([root, payment]), root, payment


def test_builder_consumes_bundle_and_preserves_incident_context():
    bundle, root, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    assert graph.context.incident_id == bundle.incident_id
    assert graph.context.workflow_key == bundle.workflow_key
    assert graph.context.lifecycle_generation == bundle.lifecycle_generation
    assert graph.context.association_scores == bundle.association_scores
    assert graph.context.association_reasons == bundle.association_reasons
    assert graph.context.identity_evidence == bundle.identity_evidence
    assert graph.context.external_identity_evidence == bundle.external_identity_evidence
    assert graph.context.provenance == bundle.provenance
    assert graph.context.late_events == bundle.late_events
    assert relation_edges(graph, RelationType.SPAN_PARENT)[0].source == ref(root)
    assert relation_edges(graph, RelationType.SERVICE_CALL)[0].target == ref(payment)


def test_duplicate_event_ids_from_different_sources_coexist_and_lookup_is_composite():
    first = make_event("same", signal="log", correlation_id="workflow", source_reference=None)
    second = make_event("same", seconds=1, signal="log", correlation_id="workflow", source_reference="topic:2")
    graph = EventGraphBuilder().build(make_bundle([first, second]))
    assert {node.reference for node in graph.nodes} == {ref(first), ref(second)}
    edge = relation_edges(graph, RelationType.SAME_CORRELATION)[0]
    assert {edge.source, edge.target} == {ref(first), ref(second)}
    assert set(edge.evidence_references) == {ref(first), ref(second)}
    assert graph.event(ref(first)) is first
    with pytest.raises(ValueError, match="ambíguo"):
        graph.event_by_id("same")
    serialized = graph.to_json()
    assert '"source_reference":null' in serialized and '"source_reference":"topic:2"' in serialized


def test_external_and_observed_correlation_confidence_come_from_bundle_identity_evidence():
    external = make_event("external-1", signal="log")
    observed = make_event("observed-2", seconds=1, signal="log", correlation_id="workflow")
    context = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", "gateway", 0.5, "signed header")
    )
    graph = EventGraphBuilder().build(make_bundle([external, observed], [context, None]))
    edge = relation_edges(graph, RelationType.SAME_CORRELATION)[0]
    assert edge.confidence == edge.weight == 0.5
    assert "external(gateway)" in edge.explanation

    observed2 = make_event("observed-3", seconds=2, signal="log", correlation_id="workflow")
    graph = EventGraphBuilder().build(make_bundle([observed, observed2]))
    assert relation_edges(graph, RelationType.SAME_CORRELATION)[0].confidence == 1.0


def test_graph_contract_rejects_missing_endpoint_and_divergent_node_reference():
    bundle, root, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    with pytest.raises(ValueError, match="diverge"):
        EventGraphNode(EventReference("wrong", None), root)
    missing = replace(graph.edges[0], target=EventReference("missing", "source"))
    with pytest.raises(ValueError, match="endpoint inexistente"):
        EventEvidenceGraph(graph.context, graph.nodes, (missing,))


def test_edge_taxonomy_is_enforced():
    bundle, root, payment = order_payment_bundle()
    with pytest.raises(ValueError, match="Taxonomia"):
        EventGraphEdge(
            ref(root), ref(payment), RelationType.TEMPORAL_PRECEDENCE, 1, 1,
            (ref(root), ref(payment)), "v1", EdgeDirection.UNDIRECTED, BASE,
            EdgeClassification.TEMPORAL, "inválida",
        )


def test_conflicting_duplicate_edges_are_not_silently_overwritten():
    bundle, root, payment = order_payment_bundle()

    def conflict_rule(context):
        yield EventGraphEdge(
            ref(root), ref(payment), RelationType.TEMPORAL_PRECEDENCE, 0.2, 0.2,
            (ref(root),), "v1", EdgeDirection.DIRECTED, context.derived_at,
            EdgeClassification.TEMPORAL, "primeira",
        )
        yield EventGraphEdge(
            ref(root), ref(payment), RelationType.TEMPORAL_PRECEDENCE, 0.8, 0.8,
            (ref(payment),), "v1", EdgeDirection.DIRECTED, context.derived_at,
            EdgeClassification.TEMPORAL, "conflitante",
        )

    registry = EdgeRuleRegistry()
    registry.register(RelationType.TEMPORAL_PRECEDENCE, conflict_rule)
    with pytest.raises(ValueError, match="conflitantes"):
        EventGraphBuilder(registry).build(bundle)


def test_builder_and_topology_validation_fail_with_controlled_errors():
    with pytest.raises(TypeError, match="IncidentEvidenceBundle"):
        EventGraphBuilder().build([])
    bundle, _, _ = order_payment_bundle()
    with pytest.raises(TypeError, match="não string"):
        EventGraphBuilder().build(bundle, topology={"order-service": "payment-service"})
    with pytest.raises(ValueError, match="Chaves"):
        EventGraphBuilder().build(bundle, topology={"": ["payment-service"]})
    graph = EventGraphBuilder().build(bundle, topology={"order-service": ["order-service"]})
    assert not relation_edges(graph, RelationType.TOPOLOGY_DEPENDENCY)


def test_path_extractor_uses_event_reference_empty_classifications_and_validates_depth():
    bundle, root, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    extractor = GraphPathExtractor(graph)
    assert extractor.shortest_path(ref(root), ref(payment)) == (ref(root), ref(payment))
    assert extractor.shortest_path(ref(root), ref(payment), classifications=()) == ()
    with pytest.raises(ValueError, match="max_depth"):
        extractor.paths(ref(root), ref(payment), max_depth=-1)
    with pytest.raises(KeyError):
        extractor.shortest_path(EventReference("missing", None), ref(payment))


def test_pruning_rejects_temporal_and_statistical_only_but_keeps_structural_path():
    first = make_event("first-1", signal="log", service="source")
    symptom = make_event("symptom-2", seconds=1, signal="log", service="symptom")
    temporal_graph = EventGraphBuilder().build(make_bundle([first, symptom]))
    result = CandidatePruner().prune(temporal_graph, ("source", "symptom"), (ref(symptom),))
    assert result.candidates == ("symptom",)

    anomaly1 = make_event("anomaly-1", signal="log", service="source", severity="ERROR")
    anomaly2 = make_event("anomaly-2", seconds=1, signal="log", service="symptom", severity="ERROR")
    statistical_graph = EventGraphBuilder().build(make_bundle([anomaly1, anomaly2]))
    assert relation_edges(statistical_graph, RelationType.CO_ANOMALY)
    result = CandidatePruner().prune(statistical_graph, ("source", "symptom"), (ref(anomaly2),))
    assert result.candidates == ("symptom",)

    structural, root, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(structural)
    result = CandidatePruner().prune(graph, ("order-service", "payment-service"), (ref(payment),))
    assert result.candidates == ("order-service", "payment-service")


def test_ground_truth_is_evaluation_only_and_does_not_change_production_result():
    bundle, _, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    pruner = CandidatePruner()
    production = pruner.prune(graph, ("payment-service", "unrelated"), (ref(payment),))
    with pytest.raises(ValueError, match="evaluation_mode"):
        pruner.audit_pruning_with_ground_truth(
            graph, ("payment-service", "unrelated"), (ref(payment),),
            ground_truth_service="unrelated",
        )
    audited = pruner.audit_pruning_with_ground_truth(
        graph, ("payment-service", "unrelated"), (ref(payment),),
        ground_truth_service="unrelated", evaluation_mode=True,
    )
    assert production.candidates == ("payment-service",)
    assert audited.protected_ground_truth == ("unrelated",)
    assert "unrelated" in audited.candidates
    assert pruner.prune(graph, ("payment-service", "unrelated"), (ref(payment),)) == production


def test_undirected_edge_degree_is_symmetric_and_does_not_create_directional_anomalies():
    first = make_event("anomaly-1", signal="log", service="one", severity="ERROR")
    second = make_event("anomaly-2", seconds=1, signal="log", service="two", severity="ERROR")
    graph = EventGraphBuilder().build(make_bundle([first, second]))
    features = GraphFeatureExtractor().extract(graph)
    assert features["one"].undirected_degree == features["two"].undirected_degree == 1
    assert features["one"].anomalous_upstream == features["one"].anomalous_downstream == 0
    assert features["two"].anomalous_upstream == features["two"].anomalous_downstream == 0


def test_causal_reach_features_ignore_irrelevant_temporal_edge():
    bundle, root, payment = order_payment_bundle(downstream_error=True)
    graph = EventGraphBuilder().build(bundle)
    baseline = GraphFeatureExtractor().extract(graph)["order-service"]
    temporal = EventGraphEdge(
        ref(payment), ref(root), RelationType.TEMPORAL_PRECEDENCE, 0.1, 0.1,
        (ref(payment), ref(root)), "manual-v1", EdgeDirection.DIRECTED, BASE,
        EdgeClassification.TEMPORAL, "Aresta temporal irrelevante para alcance causal.",
    )
    versions = dict(graph.rule_versions)
    versions[RelationType.TEMPORAL_PRECEDENCE.value] = ("v1", "manual-v1")
    augmented = EventEvidenceGraph(graph.context, graph.nodes, (*graph.edges, temporal), graph.service_nodes, versions)
    changed = GraphFeatureExtractor().extract(augmented)["order-service"]
    assert (
        baseline.depth, baseline.affected_descendants, baseline.anomalous_upstream,
        baseline.anomalous_downstream, baseline.shortest_path_to_first_failure,
        baseline.event_chain_count,
    ) == (
        changed.depth, changed.affected_descendants, changed.anomalous_upstream,
        changed.anomalous_downstream, changed.shortest_path_to_first_failure,
        changed.event_chain_count,
    )


def test_topology_requires_support_and_records_version_direction_and_evidence():
    healthy1 = make_event("healthy-1", signal="log", service="order-service")
    healthy2 = make_event("healthy-2", seconds=1, signal="metric", service="payment-service")
    graph = EventGraphBuilder().build(
        make_bundle([healthy1, healthy2]), topology={"order-service": ["payment-service"]}
    )
    assert not relation_edges(graph, RelationType.TOPOLOGY_DEPENDENCY)

    anomalous = make_event("bad-2", seconds=1, signal="log", service="payment-service", severity="ERROR")
    graph = EventGraphBuilder().build(
        make_bundle([healthy1, anomalous]), topology={"order-service": ["payment-service"]}
    )
    edge = relation_edges(graph, RelationType.TOPOLOGY_DEPENDENCY)[0]
    assert edge.rule_version == "v1"  # versão da regra no registry
    assert "topology-v1" in edge.explanation and "order-service->payment-service" in edge.explanation
    assert set(edge.evidence_references) == {ref(healthy1), ref(anomalous)}


def test_only_valid_business_transition_is_emitted():
    created = make_event(
        "created-1", signal="business_event", name="order.created",
        business_entity_type="order", business_entity_id="42",
    )
    arbitrary = make_event(
        "arbitrary-2", seconds=1, signal="business_event", name="order.packed",
        business_entity_type="order", business_entity_id="42",
    )
    graph = EventGraphBuilder().build(make_bundle([created, arbitrary]))
    assert not relation_edges(graph, RelationType.BUSINESS_TRANSITION)
    approved = make_event(
        "approved-3", seconds=1, signal="business_event", name="order.approved",
        business_entity_type="order", business_entity_id="42",
    )
    graph = EventGraphBuilder().build(make_bundle([created, approved]))
    assert relation_edges(graph, RelationType.BUSINESS_TRANSITION)[0].rule_version == "v1"
    assert "business-transition-v1" in relation_edges(graph, RelationType.BUSINESS_TRANSITION)[0].explanation


def test_cycle_is_safe_and_replay_json_is_deterministic():
    first = make_event("first-1", span_id="1" * 16,
                       span_links=({"trace_id": "a" * 32, "span_id": "2" * 16},))
    second = make_event("second-2", seconds=1, span_id="2" * 16,
                        span_links=({"trace_id": "a" * 32, "span_id": "1" * 16},))
    bundle = make_bundle([first, second])
    graph = EventGraphBuilder().build(bundle)
    paths = GraphPathExtractor(graph).paths(ref(first), ref(second))
    assert paths and all(len(path) == len(set(path)) for path in paths)
    assert graph.to_json() == EventGraphBuilder().build(bundle).to_json()


def test_healthy_earlier_target_does_not_hide_later_supported_anomaly():
    source = make_event("source-1", signal="log", service="order-service")
    healthy = make_event("healthy-2", seconds=1, signal="log", service="payment-service")
    anomalous = make_event("anomalous-3", seconds=2, signal="log", service="payment-service", status_code=500)
    graph = EventGraphBuilder().build(
        make_bundle([source, healthy, anomalous]),
        topology={"order-service": ["payment-service"]},
    )
    edges = relation_edges(graph, RelationType.TOPOLOGY_DEPENDENCY)
    assert edges and edges[0].target == ref(anomalous)
    assert all(edge.target != ref(healthy) for edge in edges)
    assert "target_anomalous" in edges[0].explanation


def test_weak_external_semantic_support_is_not_elevated_by_topology():
    source = make_event("source-1", signal="log", service="order-service")
    target = make_event("target-2", seconds=1, signal="log", service="payment-service")
    context = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", "gateway", 0.10, "weak claim")
    )
    bundle = make_bundle(
        [source, target], [context, context],
        IncidentWindowPolicy(association_threshold=0.05, reopen_threshold=0.70),
    )
    graph = EventGraphBuilder().build(bundle, topology={"order-service": ["payment-service"]})
    edge = relation_edges(graph, RelationType.TOPOLOGY_DEPENDENCY)[0]
    assert edge.confidence == edge.weight == 0.10
    assert "semantic_identity[0.100]" in edge.explanation
    assert "confidence=min(0.550,0.100)=0.100" in edge.explanation


@pytest.mark.parametrize("values", [
    {"status_code": 500},
    {"status_code": "FAILURE"},
    {"business_outcome": "failure"},
])
def test_unified_anomaly_policy_recognizes_status_and_business_failure(values):
    assert is_anomalous(make_event("failure-1", signal="log", **values))


def test_first_failure_features_use_the_same_anomaly_policy():
    failed = make_event("failed-1", signal="trace", span_id="1" * 16, status_code=500)
    child = make_event("child-2", seconds=1, service="payment-service", span_id="2" * 16,
                       parent_span_id="1" * 16)
    graph = EventGraphBuilder().build(make_bundle([failed, child]))
    features = GraphFeatureExtractor().extract(graph)
    assert is_anomalous(failed)
    assert features["order-service"].shortest_path_to_first_failure == 0
    assert features["payment-service"].anomalous_upstream == 1


def test_duplicate_span_observations_across_sources_are_all_preserved_deterministically():
    parent_a = make_event(
        "parent", span_id="1" * 16, source_reference="collector-a", source_dataset="dataset-a"
    )
    parent_b = make_event(
        "parent", span_id="1" * 16, source_reference="collector-b", source_dataset="dataset-b"
    )
    child = make_event(
        "child", seconds=1, service="payment-service", span_id="2" * 16,
        parent_span_id="1" * 16, source_reference="collector-c",
    )
    bundle = make_bundle([parent_b, child, parent_a])
    first = EventGraphBuilder().build(bundle)
    second = EventGraphBuilder().build(bundle)
    parents = relation_edges(first, RelationType.SPAN_PARENT)
    calls = relation_edges(first, RelationType.SERVICE_CALL)
    assert {edge.source for edge in parents} == {ref(parent_a), ref(parent_b)}
    assert {edge.source for edge in calls} == {ref(parent_a), ref(parent_b)}
    assert all("observações_candidatas=2" in edge.explanation for edge in parents)
    assert "collector-a" in first.to_json() and "collector-b" in first.to_json()
    assert first.to_json() == second.to_json()


def test_pruner_rejects_empty_symptoms_and_invalid_candidates_even_when_disabled():
    bundle, _, payment = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    with pytest.raises(ValueError, match="não pode ser vazio"):
        CandidatePruner().prune(graph, ("payment-service",), ())
    with pytest.raises(ValueError, match="candidato"):
        CandidatePruner().prune(graph, ("",), (ref(payment),))
    with pytest.raises(ValueError, match="não pode ser vazio"):
        CandidatePruner(enabled=False).prune(graph, ("payment-service",), ())


def test_centralities_are_explicit_per_evidence_view():
    bundle, _, _ = order_payment_bundle()
    features = GraphFeatureExtractor().extract(EventGraphBuilder().build(bundle))["order-service"]
    assert features.centrality == features.all_evidence_centrality
    assert 0 <= features.structural_centrality <= features.plausible_causal_centrality <= features.all_evidence_centrality <= 1


def test_direct_construction_rejects_invalid_context_service_reference_duplicates_and_versions():
    bundle, root, _ = order_payment_bundle()
    graph = EventGraphBuilder().build(bundle)
    with pytest.raises(TypeError, match="IncidentGraphContext"):
        EventEvidenceGraph(object(), graph.nodes, graph.edges, graph.service_nodes, graph.rule_versions)
    missing = EventReference("missing", "source")
    invalid_context = replace(graph.context, association_scores={**graph.context.association_scores, missing: 0.5})
    with pytest.raises(ValueError, match="Contexto"):
        EventEvidenceGraph(invalid_context, graph.nodes, graph.edges, graph.service_nodes, graph.rule_versions)
    invalid_service = ServiceGraphNode("order-service", (missing,))
    with pytest.raises(ValueError, match="ServiceGraphNode"):
        EventEvidenceGraph(graph.context, graph.nodes, graph.edges, (invalid_service,), graph.rule_versions)
    with pytest.raises(ValueError, match="duplicada"):
        EventEvidenceGraph(graph.context, graph.nodes, (graph.edges[0], graph.edges[0]), (), graph.rule_versions)
    wrong_versions = dict(graph.rule_versions)
    wrong_versions[graph.edges[0].relation_type.value] = "other"
    with pytest.raises(ValueError, match="rule_versions"):
        EventEvidenceGraph(graph.context, graph.nodes, graph.edges, graph.service_nodes, wrong_versions)
