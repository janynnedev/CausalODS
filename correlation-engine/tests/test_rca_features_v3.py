from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
import math

import pytest

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import EventReference, IncidentEvidenceAssembler
from multimodal_v1.event_graph import (
    BusinessTransitionPolicy, CandidatePruner, EventGraphBuilder,
    TopologyDependencyPolicy,
)
from multimodal_v1.event_graph.features import GraphFeaturePolicy
from multimodal_v1.features_v3 import (
    FEATURES, FeatureGroup, FeatureInputV3, FeatureRegistryV3,
    IncidentDatasetGeneratorV3, RCAFeatureExtractorV3, RCAFeatureVectorV3,
    RCAFeatureVectorViewV3, RCAServiceTargetV3, TrainingBaselineEstimatorV3,
)


BASE = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def event(event_id, signal, *, service="order-service", seconds=0, workflow="workflow-1", **values):
    payload = dict(
        event_id=event_id, event_name=values.pop("event_name", event_id), event_type=signal,
        signal_type=signal, event_timestamp=BASE + timedelta(seconds=seconds),
        service_name=service, correlation_id=workflow, source_reference=f"fixture:{event_id}",
    )
    if signal == "trace":
        payload.update(trace_id=("a" if workflow == "workflow-1" else "b") * 32,
                       span_id=(event_id[-1] * 16), attributes={"duration_ms": 10.0})
    elif signal == "metric":
        payload.update(metric_name="system.cpu.utilization", metric_value=20.0)
    elif signal == "business_event":
        payload.update(business_operation="transition", business_outcome="approved",
                       business_entity_type="order", business_entity_id=workflow)
    payload.update(values)
    return UnifiedEvent(**payload)


def sample(workflow="workflow-1", *, latency=10.0, cpu=20.0, include_all=True):
    suffix = workflow[-1]
    events = [event(f"trace-{suffix}", "trace", workflow=workflow,
                    attributes={"duration_ms": latency, "expected_span_count": 1})]
    if include_all:
        events.extend([
            event(f"log-{suffix}", "log", seconds=1, workflow=workflow,
                  severity="INFO", attributes={"body": "order 123 approved"}),
            event(f"metric-{suffix}", "metric", seconds=2, workflow=workflow,
                  metric_value=cpu, late_arrival_ms=5),
            event(f"business-{suffix}", "business_event", seconds=3, workflow=workflow),
        ])
    assembler = IncidentEvidenceAssembler()
    incident_id = None
    for item in events:
        decision = assembler.ingest(item)
        incident_id = decision.incident_id
    bundle = next(bundle for bundle in assembler.active_bundles() if bundle.incident_id == incident_id)
    return bundle, EventGraphBuilder().build(bundle)


def test_registry_has_stable_order_manifest_types_and_semantics():
    registry = FeatureRegistryV3()
    assert len(registry.names) == 40
    assert registry.names[:3] == ("latency_mean", "latency_p95", "latency_zscore")
    assert registry.names[-3:] == (
        "metric_delay_ms", "uninstrumented_neighbor_count", "topology_staleness_score",
    )
    manifest = registry.manifest()
    assert manifest["schema_version"] == "rca-features-v3"
    assert all(item["type"] and item["semantics"] for item in manifest["features"])


def test_same_input_produces_identical_features_and_deterministic_json():
    bundle, graph = sample()
    baseline = TrainingBaselineEstimatorV3().fit([bundle], training_incident_ids=[bundle.incident_id])
    extractor = RCAFeatureExtractorV3()
    first = extractor.extract(bundle, graph, baselines=baseline)
    second = extractor.extract(bundle, graph, baselines=baseline)
    assert first == second
    assert first[0].to_json() == second[0].to_json()
    assert json.loads(first[0].to_json())["features"]["latency_zscore"] == 0.0
    assert baseline.to_json() == baseline.to_json()


def test_missing_modalities_are_explicit_and_model_missing_values_are_nan():
    bundle, graph = sample(include_all=False)
    vector = RCAFeatureExtractorV3().extract(bundle, graph)[0]
    assert vector.values["missing_trace"] is False
    assert vector.values["missing_logs"] is True
    assert vector.values["missing_metrics"] is True
    assert vector.values["missing_business_events"] is True
    assert vector.values["cpu_zscore"] is None
    index = FeatureRegistryV3().names.index("cpu_zscore")
    assert math.isnan(vector.model_row(FeatureRegistryV3().names)[index])
    assert json.loads(vector.to_json())["features"]["cpu_zscore"] is None


def test_baseline_is_fit_only_on_training_incidents_without_heldout_leakage():
    train, _ = sample("workflow-1", latency=10, cpu=20)
    heldout, _ = sample("workflow-2", latency=10_000, cpu=9_000)
    snapshot = TrainingBaselineEstimatorV3().fit(
        [train, heldout], training_incident_ids=[train.incident_id]
    )
    assert snapshot.training_incident_ids == (train.incident_id,)
    assert snapshot.statistic("order-service", "latency").mean == 10
    assert snapshot.statistic("order-service", "cpu").mean == 20
    assert heldout.incident_id not in snapshot.to_json()


def test_reproducible_incident_split_has_no_cross_split_rows_and_labels_do_not_change_features():
    inputs = []
    for number in range(1, 6):
        bundle, graph = sample(f"workflow-{number}", latency=number * 10)
        inputs.append(FeatureInputV3(bundle, graph))
    generator = IncidentDatasetGeneratorV3(seed=73)
    plain = generator.generate(inputs, train_fraction=.6, validation_fraction=.2)
    labels = [RCAServiceTargetV3(item.bundle.incident_id, "order-service", True,
                                "labeled", "fixture") for item in inputs]
    labelled = generator.generate(inputs, targets=labels, supervised=True,
                                  train_fraction=.6, validation_fraction=.2)
    assert plain.split_incident_ids == labelled.split_incident_ids
    assert [row.vector for row in plain.rows] == [row.vector for row in labelled.rows]
    sets = [set(value) for value in plain.split_incident_ids.values()]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
    assert generator.split([item.bundle.incident_id for item in inputs], train_fraction=.6,
                           validation_fraction=.2) == plain.split_incident_ids


def test_group_ablation_preserves_canonical_relative_order():
    items = [FeatureInputV3(*sample(f"workflow-{number}")) for number in range(1, 4)]
    dataset = IncidentDatasetGeneratorV3(seed=1).generate(
        items, train_fraction=1, validation_fraction=0
    )
    ablated = dataset.ablate(FeatureGroup.LOG, FeatureGroup.BUSINESS)
    assert len(ablated.registry.names) == 30
    assert all(name not in ablated.registry.names for name in (
        "log_error_count", "business_failure_count",
    ))
    assert ablated.registry.names == tuple(
        name for name in dataset.registry.names if name in ablated.registry.names
    )
    assert len(ablated.matrix("train")[0]) == 30


def test_graph_pruning_and_quality_topology_features_are_auditable():
    bundle, graph = sample()
    vector = RCAFeatureExtractorV3().extract(
        bundle, graph, topology={"order-service": ("payment-service",)}
    )[0]
    assert vector.values["pruning_survival"] is None
    assert vector.values["uninstrumented_neighbor_count"] == 1
    assert vector.values["topology_staleness_score"] == 1.0


def test_matrix_is_compatible_with_sklearn_and_xgboost_if_installed():
    np = pytest.importorskip("numpy")
    sklearn = pytest.importorskip("sklearn.impute")
    xgb = pytest.importorskip("xgboost")
    items = [FeatureInputV3(*sample(f"workflow-{number}")) for number in range(1, 4)]
    dataset = IncidentDatasetGeneratorV3(seed=1).generate(
        items, train_fraction=1, validation_fraction=0
    )
    matrix = np.asarray(dataset.matrix("train"), dtype=float)
    transformed = sklearn.SimpleImputer(
        strategy="constant", fill_value=0, keep_empty_features=True
    ).fit_transform(matrix)
    assert transformed.shape == (3, 40)
    assert xgb.DMatrix(matrix, feature_names=list(dataset.registry.names)).num_col() == 40


def test_invalid_split_and_duplicate_incident_input_are_rejected():
    bundle, graph = sample()
    generator = IncidentDatasetGeneratorV3()
    with pytest.raises(ValueError, match="Frações"):
        generator.split([bundle.incident_id], train_fraction=.9, validation_fraction=.2)
    with pytest.raises(ValueError, match="único"):
        generator.generate([FeatureInputV3(bundle, graph), FeatureInputV3(bundle, graph)])


def test_pruning_modes_block_ground_truth_audit_and_disabled_is_null():
    bundle, graph = sample()
    symptom = graph.nodes[0].reference
    production = CandidatePruner().prune(graph, ("order-service",), (symptom,))
    assert RCAFeatureExtractorV3().extract(bundle, graph, pruning=production)[0].values["pruning_survival"] is True
    disabled = CandidatePruner(enabled=False).prune(graph, ("order-service",), (symptom,))
    assert disabled.mode == "disabled_ablation"
    assert RCAFeatureExtractorV3().extract(bundle, graph, pruning=disabled)[0].values["pruning_survival"] is None
    audited = CandidatePruner().audit_pruning_with_ground_truth(
        graph, ("order-service",), (symptom,), ground_truth_service="order-service",
        evaluation_mode=True,
    )
    with pytest.raises(ValueError, match="evaluation_audit"):
        RCAFeatureExtractorV3().extract(bundle, graph, pruning=audited)


def test_zero_variance_is_saturated_and_metric_identity_does_not_mix_units():
    train, _ = sample()
    estimator = TrainingBaselineEstimatorV3()
    snapshot = estimator.fit([train], training_incident_ids=[train.incident_id])
    assert snapshot.statistic("order-service", "latency").zscore(10) == 0
    assert snapshot.statistic("order-service", "latency").zscore(100) == 12
    metrics = (
        event("cpu-ratio", "metric", metric_name="cpu.utilization", metric_unit="1", metric_value=.5),
        event("cpu-percent", "metric", seconds=1, metric_name="cpu.utilization",
              metric_unit="percent", metric_value=50),
    )
    assembler = IncidentEvidenceAssembler()
    for item in metrics:
        incident = assembler.ingest(item).incident_id
    bundle = next(item for item in assembler.active_bundles() if item.incident_id == incident)
    metric_snapshot = estimator.fit([bundle], training_incident_ids=[bundle.incident_id])
    selection = metric_snapshot.metric_selection("order-service", "cpu")
    assert selection.sample_count == metric_snapshot.statistic("order-service", "cpu").count == 1
    with pytest.raises(ValueError, match="duplicados"):
        estimator.fit([bundle, bundle], training_incident_ids=[bundle.incident_id])


def test_supervised_labels_require_explicit_negatives_and_preserve_multiple_roots():
    inputs = [FeatureInputV3(*sample(f"workflow-{number}")) for number in range(1, 4)]
    keys = [(item.bundle.incident_id, "order-service") for item in inputs]
    labels = [RCAServiceTargetV3(incident, service, index < 2, "labeled", "fixture")
              for index, (incident, service) in enumerate(keys)]
    dataset = IncidentDatasetGeneratorV3(seed=2).generate(
        inputs, targets=labels, supervised=True, train_fraction=1, validation_fraction=0)
    assert sum(target is True for target in dataset.targets("train")) == 2
    assert False in dataset.targets("train")
    with pytest.raises(ValueError, match="todas"):
        IncidentDatasetGeneratorV3().generate(
            inputs, targets=labels[:-1], supervised=True, train_fraction=1, validation_fraction=0)
    extra = RCAServiceTargetV3(keys[0][0], "ghost", False, "labeled", "fixture")
    with pytest.raises(ValueError, match="sem linha"):
        IncidentDatasetGeneratorV3().generate(
            inputs, targets=[*labels, extra], supervised=True, train_fraction=1, validation_fraction=0)
    with pytest.raises(ValueError, match="duplicado"):
        IncidentDatasetGeneratorV3().generate(
            inputs, targets=[*labels, labels[0]], supervised=True, train_fraction=1, validation_fraction=0)


def test_manifest_hashes_replay_and_ablation_are_deterministic_and_distinct():
    inputs = [FeatureInputV3(*sample(f"workflow-{number}")) for number in range(1, 6)]
    generator = IncidentDatasetGeneratorV3(seed=91)
    first = generator.generate(inputs, train_fraction=.6, validation_fraction=.2)
    replay = generator.generate(reversed(inputs), train_fraction=.6, validation_fraction=.2)
    assert first.to_json() == replay.to_json()
    assert first.dataset_sha256 == replay.dataset_sha256
    ablated = first.ablate(FeatureGroup.LOG)
    assert ablated.feature_view.ablation_id != first.feature_view.ablation_id
    assert ablated.feature_view.fingerprint != first.feature_view.fingerprint
    assert "log_error_count" not in ablated.rows[0].vector.values
    assert len(ablated.rows[0].vector.values) == 35
    assert ablated.dataset_sha256 != first.dataset_sha256
    row = first.rows[0]
    values = dict(row.vector.values)
    values["latency_mean"] = (values["latency_mean"] or 0) + 1
    vector = RCAFeatureVectorViewV3(row.vector.incident_id, row.vector.service_name,
                                    values, row.vector.feature_names,
                                    row.vector.feature_fingerprint, row.vector.ablation_id)
    changed = replace(first, rows=(replace(row, vector=vector), *first.rows[1:]))
    assert changed.dataset_sha256 != first.dataset_sha256
    assert IncidentDatasetGeneratorV3(seed=92).generate(
        inputs, train_fraction=.6, validation_fraction=.2).dataset_sha256 != first.dataset_sha256


def _bundle(events):
    assembler = IncidentEvidenceAssembler()
    for item in events:
        incident = assembler.ingest(item).incident_id
    return next(item for item in assembler.active_bundles() if item.incident_id == incident)


def test_business_transitions_are_scoped_by_typed_entity():
    bundle = _bundle((
        event("a-1", "business_event", business_entity_type="order", business_entity_id="A", business_outcome="approved"),
        event("b-1", "business_event", seconds=1, business_entity_type="order", business_entity_id="B", business_outcome="failed"),
        event("a-2", "business_event", seconds=2, business_entity_type="order", business_entity_id="A", business_outcome="approved"),
        event("b-2", "business_event", seconds=3, business_entity_type="order", business_entity_id="B", business_outcome="failed"),
    ))
    vector = RCAFeatureExtractorV3().extract(bundle, EventGraphBuilder().build(bundle))[0]
    assert vector.values["business_outcome_change"] is False
    assert vector.values["rejected_transition_count"] == 0


def test_metric_delay_distinguishes_absent_from_observed_zero_and_graph_mismatch_fails():
    def extract_one(item):
        bundle = _bundle((item,))
        graph = EventGraphBuilder().build(bundle)
        return bundle, graph, RCAFeatureExtractorV3().extract(bundle, graph)[0]
    _, _, absent = extract_one(event("metric-none", "metric", late_arrival_ms=None))
    bundle, graph, observed = extract_one(event("metric-zero", "metric", late_arrival_ms=0))
    assert absent.values["metric_delay_ms"] is None
    assert observed.values["metric_delay_ms"] == 0.0
    divergent = replace(graph, context=replace(graph.context, workflow_key="different"))
    with pytest.raises(ValueError, match="workflow_key"):
        RCAFeatureExtractorV3().extract(bundle, divergent)


def test_failed_trace_descendants_are_distinct_from_all_downstream_anomalies():
    root = event("root-x", "trace", span_id="1" * 16)
    child = event("child-x", "trace", seconds=1, service="payment-service",
                  span_id="2" * 16, parent_span_id="1" * 16)
    error_log = event("log-x", "log", seconds=2, service="payment-service", severity="ERROR")
    bundle = _bundle((root, child, error_log))
    vectors = {item.service_name: item for item in RCAFeatureExtractorV3().extract(
        bundle, EventGraphBuilder().build(bundle))}
    order = vectors["order-service"].values
    assert order["failed_descendant_count"] == 0
    assert order["downstream_anomaly_count"] >= 1


def test_vector_types_and_registry_order_are_strict():
    bundle, graph = sample()
    values = dict(RCAFeatureExtractorV3().extract(bundle, graph)[0].values)
    for name, invalid, error in (
        ("centrality", float("inf"), ValueError),
        ("failed_descendant_count", True, TypeError),
        ("failed_descendant_count", 1.5, TypeError),
        ("missing_trace", None, ValueError),
    ):
        candidate = dict(values)
        candidate[name] = invalid
        with pytest.raises(error):
            RCAFeatureVectorV3(bundle.incident_id, "order-service", candidate)
    with pytest.raises(ValueError, match="canônicas"):
        FeatureRegistryV3(tuple(reversed(FEATURES)))


def test_pruning_is_bound_to_incident_workflow_and_graph_fingerprint():
    bundle_a, graph_a = sample("workflow-1")
    bundle_b, graph_b = sample("workflow-2")
    pruning_a = CandidatePruner().prune(
        graph_a, ("order-service",), (graph_a.nodes[0].reference,))
    assert pruning_a.graph_fingerprint == graph_a.graph_fingerprint
    assert pruning_a.policy_version == "candidate-pruning-v1"
    with pytest.raises(ValueError, match="incident_id"):
        RCAFeatureExtractorV3().extract(bundle_b, graph_b, pruning=pruning_a)


def test_rejected_transition_requires_valid_business_transition_edge():
    valid = _bundle((
        event("valid-created", "business_event", event_name="order.created",
              business_entity_id="valid", business_outcome="created"),
        event("valid-rejected", "business_event", seconds=1, event_name="order.rejected",
              business_entity_id="valid", business_outcome="rejected"),
    ))
    valid_values = RCAFeatureExtractorV3().extract(valid, EventGraphBuilder().build(valid))[0].values
    assert valid_values["rejected_transition_count"] == 1
    assert valid_values["unexpected_transition_count"] == 0

    invalid = _bundle((
        event("invalid-approved", "business_event", event_name="order.approved",
              business_entity_id="invalid", business_outcome="approved"),
        event("invalid-rejected", "business_event", seconds=1, event_name="order.rejected",
              business_entity_id="invalid", business_outcome="rejected"),
    ))
    invalid_values = RCAFeatureExtractorV3().extract(invalid, EventGraphBuilder().build(invalid))[0].values
    assert invalid_values["rejected_transition_count"] == 0
    assert invalid_values["unexpected_transition_count"] == 1


def test_direct_feature_view_validates_values_identity_and_serializes_names():
    dataset = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(*sample())], train_fraction=1, validation_fraction=0)
    vector = dataset.rows[0].vector
    def build(values=None, names=None, fingerprint=None, ablation=None):
        return RCAFeatureVectorViewV3(
            vector.incident_id, vector.service_name, values or dict(vector.values),
            names or vector.feature_names, fingerprint or vector.feature_fingerprint,
            ablation or vector.ablation_id,
        )
    for name, invalid, error in (
        ("centrality", float("inf"), ValueError),
        ("failed_descendant_count", True, TypeError),
        ("missing_trace", None, ValueError),
    ):
        values = dict(vector.values)
        values[name] = invalid
        with pytest.raises(error):
            build(values=values)
    with pytest.raises(ValueError, match="fingerprint"):
        build(fingerprint="0" * 64)
    with pytest.raises(ValueError, match="feature_names|ordem"):
        build(names=tuple(reversed(vector.feature_names)))
    assert vector.to_dict()["feature_names"] == list(vector.feature_names)


def test_ablation_manifest_describes_exact_view_and_chains_cumulatively():
    dataset = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(*sample())], train_fraction=1, validation_fraction=0)
    first = dataset.ablate(FeatureGroup.LOG)
    second = first.ablate(FeatureGroup.BUSINESS)
    assert len(first.feature_view.names) == 35
    assert len(second.feature_view.names) == 30
    assert "log_error_count" not in second.feature_view.names
    assert "business_failure_count" not in second.feature_view.names
    manifest = second.manifest
    assert manifest.feature_names == second.feature_view.names
    assert manifest.column_count == 30
    assert json.loads(manifest.feature_view_json)["features"] == [
        item.to_dict() for item in second.feature_view.definitions]


def test_policy_topology_and_pruning_provenance_change_dataset_hash():
    bundle, graph = sample()
    base_input = FeatureInputV3(bundle, graph)
    base = IncidentDatasetGeneratorV3(extractor=RCAFeatureExtractorV3(
        graph_feature_policy=GraphFeaturePolicy(version="graph-policy-A"))).generate(
        [base_input], train_fraction=1, validation_fraction=0)
    graph_changed = IncidentDatasetGeneratorV3(extractor=RCAFeatureExtractorV3(
        graph_feature_policy=GraphFeaturePolicy(version="graph-policy-B"))).generate(
        [base_input], train_fraction=1, validation_fraction=0)
    assert graph_changed.dataset_sha256 != base.dataset_sha256

    topology = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph, topology={"order-service": ("payment-service",)})],
        train_fraction=1, validation_fraction=0)
    assert topology.dataset_sha256 != IncidentDatasetGeneratorV3().generate(
        [base_input], train_fraction=1, validation_fraction=0).dataset_sha256

    symptom = graph.nodes[0].reference
    disabled_a = CandidatePruner(enabled=False).prune(graph, ("order-service",), (symptom,))
    disabled_b = CandidatePruner(enabled=False).prune(graph, ("ghost", "order-service"), (symptom,))
    pruning_a = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph, disabled_a)], train_fraction=1, validation_fraction=0)
    pruning_b = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph, disabled_b)], train_fraction=1, validation_fraction=0)
    assert pruning_a.rows[0].vector.values == pruning_b.rows[0].vector.values
    assert pruning_a.dataset_sha256 != pruning_b.dataset_sha256

    provenance = base.policy_provenance
    incident_id = bundle.incident_id
    versions_a = {"A": ("v1",), "B": ("v2",)}
    versions_b = {"A": ("v2",), "B": ("v1",)}
    swapped_a = replace(provenance, relation_rule_versions=versions_a,
                        relation_rule_versions_by_incident={incident_id: versions_a})
    swapped_b = replace(provenance, relation_rule_versions=versions_b,
                        relation_rule_versions_by_incident={incident_id: versions_b})
    assert replace(base, policy_provenance=swapped_a).dataset_sha256 != replace(
        base, policy_provenance=swapped_b).dataset_sha256


def test_target_rank_only_exists_for_positive_labeled_root():
    positive = RCAServiceTargetV3("incident", "service", True, "labeled", "fixture", root_cause_rank=1)
    assert positive.root_cause_rank == 1
    for status, root in (("labeled", False), ("unknown", None), ("excluded", None)):
        with pytest.raises(ValueError, match="rank"):
            RCAServiceTargetV3("incident", "service", root, status, "fixture", root_cause_rank=1)


def test_zero_variance_uses_versioned_tolerant_equality():
    from multimodal_v1.features_v3 import BaselineStatistic
    statistic = BaselineStatistic(10.0, 0.0, 2)
    assert statistic.zscore(10.0 + 1e-13) == 0.0
    assert statistic.zscore(10.01) == 12.0


@pytest.mark.parametrize(
    ("signal", "initial", "changed"),
    (
        ("log", {"severity": "INFO"}, {"severity": "ERROR"}),
        ("metric", {"metric_value": 20}, {"metric_value": 21}),
        ("business_event", {"business_outcome": "approved"}, {"business_outcome": "failed"}),
        ("trace", {"attributes": {"duration_ms": 10}}, {"attributes": {"duration_ms": 11}}),
    ),
)
def test_event_payload_changes_graph_fingerprint_and_bundle_graph_match(signal, initial, changed):
    original_event = event(f"payload-{signal}", signal, **initial)
    bundle = _bundle((original_event,))
    graph = EventGraphBuilder().build(bundle)
    changed_event = replace(original_event, **changed)
    changed_node = replace(graph.nodes[0], event=changed_event)
    changed_graph = replace(graph, nodes=(changed_node,))
    assert changed_event.contract_fingerprint != original_event.contract_fingerprint
    assert changed_graph.graph_fingerprint != graph.graph_fingerprint
    with pytest.raises(ValueError, match="Payload canônico"):
        RCAFeatureExtractorV3().extract(bundle, changed_graph)
    pruning = CandidatePruner().prune(graph, ("order-service",), (graph.nodes[0].reference,))
    with pytest.raises(ValueError, match="outro EventEvidenceGraph"):
        RCAFeatureExtractorV3().extract(
            replace(bundle, events=(changed_event,)), changed_graph, pruning=pruning)


def test_event_fingerprint_is_canonical_for_mapping_order_and_utc():
    left = event("canonical", "log", attributes={"b": 2, "a": {"y": 2, "x": 1}})
    right = replace(left, attributes={"a": {"x": 1, "y": 2}, "b": 2})
    assert left.to_json() == right.to_json()
    assert left.contract_fingerprint == right.contract_fingerprint


def test_pruning_fingerprint_covers_exclusions_counts_and_dataset_hash():
    bundle, graph = sample()
    symptom = graph.nodes[0].reference
    pruning = CandidatePruner().prune(
        graph, ("ghost", "order-service"), (symptom,))
    changed_rule = replace(
        pruning, exclusions=(replace(pruning.exclusions[0], rule="changed-rule"),))
    assert pruning.pruning_fingerprint != changed_rule.pruning_fingerprint
    datasets = [IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph, item)], train_fraction=1,
        validation_fraction=0) for item in (pruning, changed_rule)]
    assert len({item.dataset_sha256 for item in datasets}) == 2


def test_malformed_pruning_and_missing_disabled_symptom_are_rejected():
    bundle, graph = sample()
    valid = CandidatePruner().prune(
        graph, ("order-service",), (graph.nodes[0].reference,))
    with pytest.raises(ValueError, match="after_count"):
        replace(valid, after_count=2)
    with pytest.raises(ValueError, match="before=after"):
        replace(valid, before_count=valid.before_count + 1)
    with pytest.raises(ValueError, match="único e ordenado"):
        replace(valid, candidates=("order-service", "order-service"), after_count=2)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(valid, graph_fingerprint="invalid")
    with pytest.raises(KeyError, match="Sintoma"):
        CandidatePruner(enabled=False).prune(
            graph, ("order-service",), (EventReference("missing", None),))


def test_custom_graph_feature_policy_is_executed_and_provenance_is_derived_from_it():
    root = event("policy-root", "trace", span_id="1" * 16)
    child = event("policy-child", "trace", seconds=1, service="payment-service",
                  span_id="2" * 16, parent_span_id="1" * 16, severity="ERROR")
    bundle = _bundle((root, child))
    graph = EventGraphBuilder().build(bundle)
    default = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph)], train_fraction=1, validation_fraction=0)
    policy = GraphFeaturePolicy(
        version="event-graph-features-no-plausible-v1",
        plausible_causal_adjacency=(),
    )
    custom = IncidentDatasetGeneratorV3(
        extractor=RCAFeatureExtractorV3(graph_feature_policy=policy)
    ).generate([FeatureInputV3(bundle, graph)], train_fraction=1, validation_fraction=0)
    default_order = next(row for row in default.rows if row.vector.service_name == "order-service")
    custom_order = next(row for row in custom.rows if row.vector.service_name == "order-service")
    assert default_order.vector.values["downstream_anomaly_count"] > 0
    assert custom_order.vector.values["downstream_anomaly_count"] == 0
    assert default_order.vector.values["failed_descendant_count"] > 0
    assert custom_order.vector.values["failed_descendant_count"] == 0
    assert default_order.vector.values["event_chain_count"] > 0
    assert custom_order.vector.values["event_chain_count"] == 0
    assert custom.policy_provenance.graph_feature_policy_fingerprint == policy.fingerprint
    assert custom.dataset_sha256 != default.dataset_sha256


def test_graph_builder_policies_are_structural_provenance_even_without_numeric_change():
    bundle, _ = sample()
    default_graph = EventGraphBuilder().build(bundle)
    custom_graph = EventGraphBuilder(
        business_policy=BusinessTransitionPolicy(version="business-policy-custom", transitions={}),
        topology_policy=TopologyDependencyPolicy(
            topology_version="topology-custom", configured_topology_weight=.4,
        ),
    ).build(bundle)
    assert custom_graph.policy_provenance != default_graph.policy_provenance
    assert custom_graph.graph_fingerprint != default_graph.graph_fingerprint
    default = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, default_graph)], train_fraction=1, validation_fraction=0)
    custom = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, custom_graph)], train_fraction=1, validation_fraction=0)
    assert [row.vector.values for row in default.rows] == [row.vector.values for row in custom.rows]
    assert custom.dataset_sha256 != default.dataset_sha256


def test_deep_freeze_prevents_source_or_nested_mutation_from_changing_hashes():
    bundle, graph = sample()
    transitions = graph.policy_provenance["business_transition_policy"]["transitions"]
    with pytest.raises(TypeError):
        transitions["new"] = ("transition",)
    first_targets = next(iter(transitions.values()))
    with pytest.raises(AttributeError):
        first_targets.append("changed")

    source = {"nested": {"items": ["a", "b"]}}
    copied_graph = replace(graph, policy_provenance=source)
    graph_hash = copied_graph.graph_fingerprint
    source["nested"]["items"].append("mutated")
    assert copied_graph.graph_fingerprint == graph_hash

    dataset = IncidentDatasetGeneratorV3().generate(
        [FeatureInputV3(bundle, graph)], train_fraction=1, validation_fraction=0)
    config_source = dataset.policy_provenance.graph_feature_policy_config.copy()
    config_source["plausible_causal_adjacency"] = list(
        config_source["plausible_causal_adjacency"])
    provenance = replace(dataset.policy_provenance,
                         graph_feature_policy_config=config_source)
    copied_dataset = replace(dataset, policy_provenance=provenance)
    dataset_hash = copied_dataset.dataset_sha256
    config_source["plausible_causal_adjacency"].append("mutated")
    assert copied_dataset.dataset_sha256 == dataset_hash
    assert copied_dataset.dataset_sha256 == copied_dataset.dataset_sha256


def test_pruning_mode_count_and_exclusion_invariants_are_strict():
    bundle, graph = sample()
    symptom = graph.nodes[0].reference
    production = CandidatePruner().prune(
        graph, ("ghost", "order-service"), (symptom,))
    with pytest.raises(ValueError, match="before=after"):
        replace(production, before_count=production.before_count + 1)
    with pytest.raises(ValueError, match="únicos"):
        replace(production, exclusions=(production.exclusions[0], production.exclusions[0]))
    disabled = CandidatePruner(enabled=False).prune(
        graph, ("order-service",), (symptom,))
    with pytest.raises(ValueError, match="disabled_ablation"):
        replace(disabled, exclusions=(production.exclusions[0],))
    with pytest.raises(TypeError, match="booleano"):
        replace(production, enabled=1)
