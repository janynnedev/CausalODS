from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import json

import pytest

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.degradation_v3 import (
    HARNESS_VERSION, MODALITIES, OPERATORS, ObservationDataset,
    ObservationDegradationHarness, degradation_metrics,
    degradation_selection_identity, derive_run_id, KafkaReplayResult,
    OperatorPolicyV3, operator_policy, run_identity,
)


BASE = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def event(signal, index, service):
    values = dict(
        event_id=f"{signal}-{index}", event_name=f"{signal}.fixture",
        event_type=signal, signal_type=signal,
        event_timestamp=BASE + timedelta(seconds=index), service_name=service,
        correlation_id=f"workflow-{index // 4}", source_reference=f"fixture:{signal}:{index}",
    )
    if signal == "trace":
        values.update(trace_id=f"{index:032x}", span_id=f"{index:016x}",
                      status_code="ERROR" if index % 5 == 0 else "OK",
                      attributes={"critical_path": index % 4 == 0, "duration_ms": index + 1})
    elif signal == "log":
        values.update(severity="ERROR" if index % 3 == 0 else "INFO",
                      attributes={"body": f"message {index}"})
    elif signal == "metric":
        values.update(metric_name=f"metric-{index % 4}", metric_value=float(index))
    else:
        values.update(business_entity_type="order", business_entity_id=f"order-{index}",
                      business_operation="transition", business_outcome="approved")
    return UnifiedEvent(**values)


@pytest.fixture
def observations():
    services = ("order-service", "payment-service", "inventory-service", "shipping-service")
    events = tuple(event(signal, index, services[index % len(services)])
                   for signal in ("trace", "log", "metric", "business_event")
                   for index in range(20))
    return ObservationDataset(
        events,
        {"order-service": ("payment-service", "inventory-service"),
         "payment-service": ("shipping-service",),
         "inventory-service": ("shipping-service",)},
        {"incident-1": ("payment-service",)},
    )


@pytest.mark.parametrize("operator", OPERATORS)
def test_every_operator_is_deterministic_preserves_input_and_ground_truth(observations, operator):
    harness = ObservationDegradationHarness()
    before = observations.fingerprint
    first = harness.apply(observations, operator, .5, seed=73,
                          parameters={"delay_ms": 5000})
    second = harness.apply(observations, operator, .5, seed=73,
                           parameters={"delay_ms": 5000})
    assert first.dataset.fingerprint == second.dataset.fingerprint
    assert first.manifest.to_dict() == second.manifest.to_dict()
    assert observations.fingerprint == before
    assert first.dataset is not observations
    assert first.dataset.ground_truth == observations.ground_truth
    assert first.manifest.input_sha256 == observations.fingerprint
    assert first.manifest.output_sha256 == first.dataset.fingerprint
    assert first.manifest.operator == operator


@pytest.mark.parametrize("severity", (0.0, .1, .3, .5))
def test_supported_severities_have_replayable_manifest(observations, severity):
    run = ObservationDegradationHarness().apply(
        observations, "random_span_drop", severity, seed=11)
    assert run.manifest.severity == severity
    assert json.loads(json.dumps(run.manifest.to_dict()))["seed"] == 11
    if severity == 0:
        assert run.dataset.fingerprint == observations.fingerprint
        assert run.manifest.accounting.removed_event_count == 0


@pytest.mark.parametrize("operator", OPERATORS)
def test_zero_severity_is_an_exact_noop_for_every_operator(observations, operator):
    run = ObservationDegradationHarness().apply(
        observations, operator, 0.0, seed=41, parameters={"delay_ms": 5000})
    accounting = run.manifest.accounting

    assert run.dataset.events == observations.events
    assert run.dataset.topology == observations.topology
    assert run.dataset.ground_truth == observations.ground_truth
    assert run.manifest.output_observation_sha256 == \
        run.manifest.input_observation_sha256 == observations.observation_fingerprint
    assert run.manifest.output_dataset_sha256 == \
        run.manifest.input_dataset_sha256 == observations.dataset_fingerprint
    assert accounting.requested_units == accounting.affected_units == 0
    assert accounting.removed_event_count == accounting.added_event_count == 0
    assert accounting.modified_event_count == 0
    assert accounting.removed_topology_edge_count == accounting.added_topology_edge_count == 0
    assert run.manifest.affected_references == ()


def test_drop_operators_measure_real_loss_by_modality(observations):
    harness = ObservationDegradationHarness()
    cases = {
        "random_span_drop": "trace", "critical_path_span_drop": "trace",
        "full_trace_sampling": "trace", "random_log_drop": "log",
        "error_log_drop": "log", "metric_downsampling": "metric",
        "metric_series_drop": "metric", "business_event_drop": "business_event",
    }
    for operator, modality in cases.items():
        run = harness.apply(observations, operator, .5, seed=5)
        assert run.manifest.accounting.removed_event_count > 0
        assert run.manifest.removed_by_modality[modality] == run.manifest.accounting.removed_event_count
        assert run.manifest.accounting.input_event_count - run.manifest.accounting.output_event_count == \
            run.manifest.accounting.removed_event_count


def test_delay_duplicate_reorder_and_uninstrumented_semantics(observations):
    harness = ObservationDegradationHarness()
    delayed = harness.apply(observations, "metric_delay", .5, seed=1,
                            parameters={"delay_ms": 1234})
    changed = [item for item in delayed.dataset.events
               if item.signal_type.value == "metric" and item.late_arrival_ms == 1234]
    assert len(changed) == delayed.manifest.accounting.modified_event_count
    duplicated = harness.apply(observations, "kafka_message_duplicate", .5, seed=1)
    assert duplicated.manifest.accounting.added_event_count == len(duplicated.dataset.events) - len(observations.events)
    reordered = harness.apply(observations, "kafka_message_reorder", .5, seed=1)
    assert sorted(item.event_id for item in reordered.dataset.events) == \
        sorted(item.event_id for item in observations.events)
    uninstrumented = harness.apply(observations, "uninstrumented_service", .5, seed=1)
    selected = {item["service_name"] for item in uninstrumented.manifest.affected_references}
    assert selected
    assert not any(item.service_name in selected for item in uninstrumented.dataset.events)


def test_topology_operators_change_only_separate_topology(observations):
    harness = ObservationDegradationHarness()
    dropped = harness.apply(observations, "topology_edge_drop", .5, seed=9)
    added = harness.apply(observations, "topology_edge_add", .5, seed=9)
    edge_count = lambda dataset: sum(len(targets) for targets in dataset.topology.values())
    assert edge_count(dropped.dataset) < edge_count(observations)
    assert edge_count(added.dataset) > edge_count(observations)
    assert dropped.dataset.events == observations.events == added.dataset.events


def test_topology_edge_add_zero_preserves_empty_topology_and_fingerprints():
    events = (event("trace", 1, "a"), event("trace", 2, "b"))
    dataset = ObservationDataset(events, {}, {"incident": ("a",)})
    run = ObservationDegradationHarness().apply(
        dataset, "topology_edge_add", 0.0, seed=7)

    assert run.dataset.topology == {}
    assert run.dataset.observation_fingerprint == dataset.observation_fingerprint
    assert run.dataset.dataset_fingerprint == dataset.dataset_fingerprint


def test_topology_edge_add_zero_preserves_partial_topology_representation():
    events = tuple(event("trace", index, service) for index, service in enumerate(
        ("a", "b", "c"), start=1))
    dataset = ObservationDataset(events, {"a": ("b",)}, {"incident": ("a",)})
    run = ObservationDegradationHarness().apply(
        dataset, "topology_edge_add", 0.0, seed=8)

    assert run.dataset.topology == dataset.topology
    assert tuple(run.dataset.topology) == tuple(dataset.topology)
    assert all(targets for targets in run.dataset.topology.values())
    assert run.dataset.to_dict() == dataset.to_dict()


def test_topology_edge_add_positive_accounts_exact_edge_difference():
    events = tuple(event("trace", index, service) for index, service in enumerate(
        ("a", "b", "c", "d"), start=1))
    dataset = ObservationDataset(events, {"a": ("b",)}, {"incident": ("a",)})
    run = ObservationDegradationHarness().apply(
        dataset, "topology_edge_add", 0.3, seed=9)
    before = {(source, target) for source, targets in dataset.topology.items()
              for target in targets}
    after = {(source, target) for source, targets in run.dataset.topology.items()
             for target in targets}
    added = after - before
    recorded = {(item["source"], item["target"])
                for item in run.manifest.affected_references}

    assert added == recorded
    assert run.manifest.accounting.added_topology_edge_count == len(added)
    assert set(run.dataset.topology) - set(dataset.topology) == \
        {source for source, _ in added if source not in dataset.topology}
    assert all(targets for targets in run.dataset.topology.values())


def test_missing_modalities_are_registered():
    trace = event("trace", 1, "only-service")
    dataset = ObservationDataset((trace,), {}, {"incident": ("only-service",)})
    run = ObservationDegradationHarness().apply(dataset, "random_span_drop", .5, seed=1)
    assert set(("log", "metric", "business_event", "topology")) <= \
        set(run.manifest.missing_modalities)


class Publisher:
    def __init__(self):
        self.messages = []

    def send(self, topic, *, key, value):
        self.messages.append((topic, key, value))


def test_offline_and_kafka_replay_smoke_with_metrics(observations):
    harness = ObservationDegradationHarness()
    run = harness.apply(observations, "random_log_drop", .3, seed=42)
    offline = harness.replay_offline(run)
    assert len(offline) == len(run.dataset.events)
    publisher = Publisher()
    replay = harness.replay_kafka(run, publisher, "causalods.replay")
    assert replay.attempted == replay.confirmed == len(offline)
    assert replay.failed == 0
    assert all(topic == "causalods.replay" for topic, _, _ in publisher.messages)
    metrics = degradation_metrics(
        {"top_1": .9, "top_3": 1.0, "mrr": .95, "fpr": .1},
        {"top_1": .7, "top_3": .9, "mrr": .8, "fpr": .2}, run.manifest,
        confidence_values=(.8, .6), outcomes=(True, False), abstentions=1,
        recovery_time_ms=1200, processing_latency_ms=15)
    assert metrics["delta_from_0pct"]["top_1"] == pytest.approx(-.2)
    assert metrics["calibration"] == pytest.approx(.4)
    assert metrics["abstention"] == pytest.approx(1 / 3)
    assert set(MODALITIES) == {"trace", "log", "metric", "business_event", "topology"}


def metric_point(event_id, seconds, *, unit="ms", instance="a", aggregation="gauge",
                 dimensions=None):
    return UnifiedEvent(
        event_id=event_id, event_name="metric.fixture", event_type="metric",
        signal_type="metric", event_timestamp=BASE + timedelta(seconds=seconds),
        service_name="metric-service", service_instance_id=instance,
        metric_name="latency", metric_unit=unit, metric_value=float(seconds),
        metric_aggregation=aggregation, source_reference=f"fixture:{event_id}",
        resource_attributes={"metric.dimensions": dimensions or {"route": "/orders"}},
    )


def test_metric_downsampling_is_temporal_preserves_endpoints_and_series_identity():
    events = tuple(metric_point(f"a-{index}", index, unit="ms") for index in (4, 0, 3, 1, 2)) + \
        tuple(metric_point(f"b-{index}", index, unit="s") for index in range(5))
    dataset = ObservationDataset(events, {}, {"incident": ("metric-service",)})
    run = ObservationDegradationHarness().apply(
        dataset, "metric_downsampling", .5, seed=4)
    remaining = {item.event_id for item in run.dataset.events}
    assert {"a-0", "a-4", "b-0", "b-4"} <= remaining
    series = run.manifest.operator_metadata["series"]
    assert len(series) == 2
    assert {item["series"][3] for item in series} == {"ms", "s"}
    assert operator_policy("metric_downsampling").configuration["selection"] == \
        "evenly-spaced-interior"
    assert operator_policy("metric_downsampling").version != \
        operator_policy("metric_series_drop").version


def test_critical_path_never_removes_noneligible_span(observations):
    run = ObservationDegradationHarness().apply(
        observations, "critical_path_span_drop", .5, seed=8)
    removed = {item["event_id"] for item in run.manifest.affected_references}
    eligible = {item.event_id for item in observations.events
                if item.signal_type.value == "trace"
                and item.attributes.get("critical_path") is True}
    assert removed and removed <= eligible
    assert run.manifest.operator_version == "critical-path-annotated-v1"


def test_error_log_versioned_policy_covers_critical_status_error_type_and_attributes():
    variants = []
    for index, override in enumerate((
        {"severity": "CRITICAL"}, {"status_code": "ERROR"},
        {"error_type": "Failure"}, {"attributes": {"exception.type": "Boom"}},
    )):
        payload = event("log", 100 + index, "svc").to_dict()
        payload.update(override)
        variants.append(UnifiedEvent.from_dict(payload))
    dataset = ObservationDataset(tuple(variants), {}, {"incident": ("svc",)})
    run = ObservationDegradationHarness().apply(dataset, "error_log_drop", .5, seed=2)
    assert run.manifest.accounting.eligible_units == 4
    assert run.manifest.operator_version == "error-log-policy-v1"


def test_minimum_one_unit_and_empty_eligibility_are_auditable():
    few_services = ObservationDataset(
        (event("trace", 1, "a"), event("trace", 2, "b")),
        {"a": ("b",)}, {"incident": ("a",)})
    harness = ObservationDegradationHarness()
    service_run = harness.apply(few_services, "uninstrumented_service", .1, seed=1)
    edge_run = harness.apply(few_services, "topology_edge_drop", .1, seed=1)
    assert service_run.manifest.accounting.affected_units == 1
    assert edge_run.manifest.accounting.affected_units == 1
    no_logs = harness.apply(few_services, "error_log_drop", .1, seed=1)
    assert no_logs.manifest.accounting.affected_units == 0
    assert no_logs.manifest.saturation_reason == "no-eligible-error-log"


def test_accounting_uses_operator_specific_denominators(observations):
    harness = ObservationDegradationHarness()
    edge = harness.apply(observations, "topology_edge_drop", .5, seed=3).manifest.accounting
    delay = harness.apply(observations, "metric_delay", .5, seed=3).manifest.accounting
    duplicate = harness.apply(observations, "kafka_message_duplicate", .5, seed=3).manifest.accounting
    reorder = harness.apply(observations, "kafka_message_reorder", .1, seed=3).manifest.accounting
    service = harness.apply(observations, "uninstrumented_service", .1, seed=3).manifest
    assert edge.topology_edge_loss_ratio == edge.removed_topology_edge_count / edge.input_topology_edge_count
    assert edge.event_loss_ratio == 0
    assert delay.event_modification_ratio > 0 and delay.event_loss_ratio == 0
    assert duplicate.event_addition_ratio > 0 and duplicate.event_loss_ratio == 0
    assert reorder.modified_event_count == reorder.affected_units
    assert service.accounting.unit_type == "service"
    assert service.accounting.removed_event_count > service.accounting.affected_units
    assert service.realized_severity == service.accounting.affected_unit_ratio


def test_reorder_is_materialized_and_duplicate_records_references(observations):
    harness = ObservationDegradationHarness()
    reordered = harness.apply(observations, "kafka_message_reorder", .1, seed=12)
    assert [item.event_id for item in reordered.dataset.events] != \
        [item.event_id for item in observations.events]
    assert all(item["original_position"] != item["final_position"]
               for item in reordered.manifest.affected_references)
    duplicated = harness.apply(observations, "kafka_message_duplicate", .1, seed=12)
    assert sum(item["copies"] for item in duplicated.manifest.affected_references) == \
        duplicated.manifest.accounting.added_event_count
    with pytest.raises(ValueError, match="duplicada"):
        ObservationDataset((observations.events[0], observations.events[0]), {},
                           observations.ground_truth)


def test_run_identity_manifest_and_nested_inputs_are_immutable(observations):
    harness = ObservationDegradationHarness()
    parameters = {"delay_ms": 100, "nested": {"labels": ["a", "b"]}}
    first = harness.apply(observations, "metric_delay", .3, seed=7, parameters=parameters)
    replay = harness.apply(observations, "metric_delay", .3, seed=7, parameters=parameters)
    assert first.manifest.run_id == replay.manifest.run_id
    assert first.manifest.manifest_fingerprint == replay.manifest.manifest_fingerprint
    assert first.manifest.to_json() == replay.manifest.to_json()
    parameters["nested"]["labels"].append("mutated")
    assert "mutated" not in first.manifest.to_json()
    changed_parameter = harness.apply(
        observations, "metric_delay", .3, seed=7, parameters={"delay_ms": 101})
    assert changed_parameter.manifest.run_id != first.manifest.run_id
    changed_input = ObservationDataset(observations.events[:-1], observations.topology,
                                       observations.ground_truth)
    assert harness.apply(changed_input, "metric_delay", .3, seed=7,
                         parameters={"delay_ms": 100}).manifest.run_id != first.manifest.run_id
    assert HARNESS_VERSION == "observation-degradation-harness-v3"


def test_source_structures_are_copied_and_ground_truth_is_unchanged():
    topology = {"a": ["b"]}
    truth = {"incident": ["a"]}
    dataset = ObservationDataset((event("trace", 1, "a"),), topology, truth)
    fingerprint = dataset.fingerprint
    topology["a"].append("c")
    truth["incident"].append("b")
    assert dataset.fingerprint == fingerprint
    assert dataset.topology["a"] == ("b",)
    assert dataset.ground_truth["incident"] == ("a",)


@pytest.mark.parametrize("bad", (-.1, 1.1, float("inf")))
def test_metrics_reject_probability_outside_contract(observations, bad):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        degradation_metrics({"top_1": bad, "top_3": 1., "mrr": .5, "fpr": .1},
                            {"top_1": .5, "top_3": 1., "mrr": .5, "fpr": .1}, manifest)


def test_metrics_validate_outcomes_abstention_times_delta_and_percent_drop(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    base = {"top_1": .8, "top_3": 1., "mrr": .5, "fpr": 0.}
    current = {"top_1": .6, "top_3": .9, "mrr": .4, "fpr": .1}
    with pytest.raises(TypeError, match="outcomes"):
        degradation_metrics(base, current, manifest,
                            confidence_values=(.5,), outcomes=(1,))
    with pytest.raises(ValueError, match="abstentions"):
        degradation_metrics(base, current, manifest,
                            confidence_values=(.5,), outcomes=(True,), abstentions=-1)
    with pytest.raises(ValueError, match="recovery_time"):
        degradation_metrics(base, current, manifest, recovery_time_ms=-1)
    result = degradation_metrics(base, current, manifest)
    assert result["delta_from_0pct"]["top_1"] == pytest.approx(-.2)
    assert result["percent_change_from_0pct"]["top_1"] == pytest.approx(-25.)
    assert result["percent_change_from_0pct"]["fpr"] is None


class PartialPublisher(Publisher):
    def send(self, topic, *, key, value):
        if len(self.messages) == 1:
            self.messages.append((topic, key, value))
            raise RuntimeError("broker failure")
        self.messages.append((topic, key, value))


def test_kafka_replay_reports_partial_failure_and_continues(observations):
    run = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .5, seed=1)
    result = ObservationDegradationHarness.replay_kafka(run, PartialPublisher(), "topic")
    assert result.attempted == len(run.dataset.events)
    assert result.confirmed == result.attempted - 1
    assert result.failed == 1
    assert result.policy_version == "synchronous-confirm-continue-v1"


def test_global_downsampling_uses_largest_remainder_across_100_series():
    events = []
    for series in range(100):
        for point in range(3):
            events.append(UnifiedEvent(
                event_id=f"series-{series}-point-{point}", event_name="metric",
                event_type="metric", signal_type="metric",
                event_timestamp=BASE + timedelta(seconds=point), service_name="svc",
                service_instance_id=f"instance-{series}", metric_name="latency",
                metric_unit="ms", metric_value=float(point), metric_aggregation="gauge",
                source_reference=f"fixture:{series}:{point}"))
    dataset = ObservationDataset(tuple(events), {}, {"incident": ("svc",)})
    first = ObservationDegradationHarness().apply(
        dataset, "metric_downsampling", .1, seed=19)
    replay = ObservationDegradationHarness().apply(
        dataset, "metric_downsampling", .1, seed=19)
    accounting = first.manifest.accounting
    series = first.manifest.operator_metadata["series"]
    assert accounting.eligible_units == 100
    assert accounting.requested_units == 10
    assert accounting.affected_units == 10
    assert sum(item["allocation"] for item in series) == accounting.affected_units
    assert [item["allocation"] for item in series] == [
        item["allocation"] for item in replay.manifest.operator_metadata["series"]]
    assert sum(item["allocation"] == 1 for item in series) == 10


def test_downsampling_zero_capacity_series_gets_zero_and_endpoints_survive():
    events = (metric_point("short-first", 0), metric_point("short-last", 1),
              metric_point("long-first", 0, instance="long"),
              metric_point("long-middle", 1, instance="long"),
              metric_point("long-last", 2, instance="long"))
    dataset = ObservationDataset(events, {}, {"incident": ("metric-service",)})
    run = ObservationDegradationHarness().apply(dataset, "metric_downsampling", .5, seed=2)
    metadata = run.manifest.operator_metadata["series"]
    assert sorted(item["capacity"] for item in metadata) == [0, 1]
    assert next(item for item in metadata if item["capacity"] == 0)["allocation"] == 0
    remaining = {item.event_id for item in run.dataset.events}
    assert {"short-first", "short-last", "long-first", "long-last"} <= remaining


def test_topology_addition_from_zero_edges_has_defined_nonzero_ratio():
    events = (event("trace", 1, "a"), event("trace", 2, "b"), event("trace", 3, "c"))
    dataset = ObservationDataset(events, {}, {"incident": ("a",)})
    run = ObservationDegradationHarness().apply(dataset, "topology_edge_add", .1, seed=4)
    accounting = run.manifest.accounting
    assert accounting.input_topology_edge_count == 0
    assert accounting.added_topology_edge_count == 1
    assert accounting.topology_edge_addition_ratio == pytest.approx(
        accounting.added_topology_edge_count / accounting.eligible_units)
    assert accounting.topology_edge_addition_ratio > 0
    assert accounting.realized_severity == pytest.approx(
        accounting.affected_units / accounting.eligible_units)


def test_accounting_and_manifest_reject_impossible_or_forged_contracts(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=4).manifest
    with pytest.raises(ValueError, match="Contagens de eventos"):
        replace(manifest.accounting, output_event_count=999)
    with pytest.raises(ValueError, match="excedem elegibilidade"):
        replace(manifest.accounting,
                requested_units=manifest.accounting.eligible_units + 1)
    with pytest.raises(ValueError, match="parameters_sha256"):
        replace(manifest, parameters_sha256="0" * 64)
    with pytest.raises(ValueError, match="operator_fingerprint"):
        replace(manifest, operator_fingerprint="0" * 64)
    with pytest.raises(ValueError, match="operator_version"):
        replace(manifest, operator_version=operator_policy("random_span_drop").version)
    with pytest.raises(ValueError, match="severidade"):
        replace(manifest, severity=.2)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(manifest, input_dataset_sha256="bad-hash")
    with pytest.raises(ValueError, match="run_id"):
        replace(manifest, run_id="degradation-tampered")


def test_operator_fingerprint_participates_in_run_identity(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=4).manifest
    selection = degradation_selection_identity(
        observation_sha256=manifest.input_observation_sha256, operator=manifest.operator,
        operator_version=manifest.operator_version,
        operator_fingerprint=manifest.operator_fingerprint,
        severity=manifest.severity, seed=manifest.seed,
        parameters_sha256=manifest.parameters_sha256)
    base = run_identity(input_dataset_sha256=manifest.input_dataset_sha256,
                        ground_truth_sha256=manifest.ground_truth_sha256,
                        selection_identity=selection)
    changed_selection = dict(selection, operator_fingerprint="f" * 64)
    changed = run_identity(input_dataset_sha256=manifest.input_dataset_sha256,
                           ground_truth_sha256=manifest.ground_truth_sha256,
                           selection_identity=changed_selection)
    assert derive_run_id(base) == manifest.run_id
    assert derive_run_id(changed) != manifest.run_id


def test_metric_direction_and_percent_change_are_unambiguous(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    result = degradation_metrics(
        {"top_1": .8, "top_3": 1., "mrr": .8, "fpr": .1},
        {"top_1": .6, "top_3": .9, "mrr": .6, "fpr": .2}, manifest)
    assert result["performance_degradation_percent"]["top_1"] == pytest.approx(25.)
    assert result["performance_degradation_percent"]["fpr"] == pytest.approx(100.)
    assert result["percent_change_from_0pct"]["top_1"] == pytest.approx(-25.)
    assert result["percent_change_from_0pct"]["fpr"] == pytest.approx(100.)
    assert result["metric_directions"]["fpr"] == "lower_is_better"
    zero = degradation_metrics(
        {"top_1": 0., "top_3": 0., "mrr": 0., "fpr": 0.},
        {"top_1": .1, "top_3": .1, "mrr": .1, "fpr": .1}, manifest)
    assert all(value is None for value in zero["performance_degradation_percent"].values())
    assert set(zero["performance_degradation_undefined_reasons"].values()) == {"baseline_zero"}


def test_metrics_require_manifest_and_exact_metric_set(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    valid = {"top_1": .5, "top_3": .8, "mrr": .6, "fpr": .1}
    with pytest.raises(TypeError, match="manifest"):
        degradation_metrics(valid, valid, object())
    with pytest.raises(ValueError, match="exatamente"):
        degradation_metrics({**valid, "extra": 1.}, valid, manifest)


@pytest.mark.parametrize("operator", (
    "random_span_drop", "random_log_drop", "full_trace_sampling",
    "metric_series_drop", "metric_delay", "kafka_message_duplicate",
    "uninstrumented_service", "topology_edge_drop", "topology_edge_add",
))
def test_operator_selection_has_no_ground_truth_leakage(observations, operator):
    left = observations
    right = ObservationDataset(observations.events, observations.topology,
                               {"different-incident": ("inventory-service",)})
    parameters = {"delay_ms": 3210}
    harness = ObservationDegradationHarness()
    first = harness.apply(left, operator, .3, seed=99, parameters=parameters)
    second = harness.apply(right, operator, .3, seed=99, parameters=parameters)
    assert first.manifest.affected_references == second.manifest.affected_references
    assert first.dataset.events == second.dataset.events
    assert first.dataset.topology == second.dataset.topology
    assert first.manifest.output_observation_sha256 == second.manifest.output_observation_sha256
    assert first.manifest.selection_fingerprint == second.manifest.selection_fingerprint
    assert first.manifest.ground_truth_sha256 != second.manifest.ground_truth_sha256
    assert first.dataset.dataset_fingerprint != second.dataset.dataset_fingerprint
    assert left.observation_fingerprint == right.observation_fingerprint


def test_observation_ground_truth_and_dataset_fingerprints_are_separate(observations):
    payload = observations.observation_payload()
    assert "ground_truth" not in payload
    assert observations.fingerprint == observations.dataset_fingerprint
    assert observations.observation_fingerprint != observations.ground_truth_fingerprint
    assert len(observations.dataset_fingerprint) == 64


def test_abstention_uses_responded_decisions_denominator(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    values = {"top_1": .5, "top_3": .8, "mrr": .6, "fpr": .1}
    two_answers = degradation_metrics(
        values, values, manifest, confidence_values=(.7, .8),
        outcomes=(True, False), abstentions=8)
    no_answers = degradation_metrics(values, values, manifest, abstentions=10)
    empty = degradation_metrics(values, values, manifest)
    assert two_answers["abstention"] == pytest.approx(.8)
    assert no_answers["abstention"] == 1.0
    assert no_answers["confidence"] is None and no_answers["calibration"] is None
    assert empty["abstention"] == 0.0
    with pytest.raises(ValueError, match="abstentions"):
        degradation_metrics(values, values, manifest, abstentions=-1)
    with pytest.raises(ValueError, match="abstentions"):
        degradation_metrics(values, values, manifest, abstentions=True)


def test_manifest_revalidates_requested_severity_and_saturation(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=4).manifest
    def with_severity(accounting, saturation):
        selection = degradation_selection_identity(
            observation_sha256=manifest.input_observation_sha256,
            operator=manifest.operator, operator_version=manifest.operator_version,
            operator_fingerprint=manifest.operator_fingerprint, severity=.5,
            seed=manifest.seed, parameters_sha256=manifest.parameters_sha256)
        identity = run_identity(
            input_dataset_sha256=manifest.input_dataset_sha256,
            ground_truth_sha256=manifest.ground_truth_sha256,
            selection_identity=selection)
        return replace(manifest, severity=.5, accounting=accounting,
                       selection_fingerprint=hashlib.sha256(
                           json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                       run_identity=identity, run_id=derive_run_id(identity),
                       saturation_reason=saturation)
    wrong = replace(manifest.accounting, eligible_units=100, requested_units=1,
                    affected_units=1)
    with pytest.raises(ValueError, match="requested_units"):
        with_severity(wrong, None)
    saturated = replace(manifest.accounting, eligible_units=100, requested_units=50,
                        affected_units=1)
    valid = with_severity(saturated, "test-capacity-saturation")
    assert valid.accounting.requested_units == 50
    with pytest.raises(ValueError, match="Saturação"):
        replace(manifest, accounting=replace(
            manifest.accounting, affected_units=0), saturation_reason=None)
    tiny = ObservationDataset((event("trace", 1, "a"), event("trace", 2, "b")),
                              {}, {"incident": ("a",)})
    expanded = ObservationDegradationHarness().apply(
        tiny, "kafka_message_reorder", .1, seed=2).manifest
    assert expanded.accounting.originally_requested_units == 1
    assert expanded.accounting.requested_units == 2
    assert expanded.accounting.requested_expansion_reason
    with pytest.raises(ValueError, match="Expansão"):
        replace(manifest.accounting, originally_requested_units=0,
                requested_expansion_reason="forged")


def test_removed_by_modality_is_strictly_reconciled(observations):
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .3, seed=2).manifest
    base = dict(manifest.removed_by_modality)
    for value in (-1, True):
        forged = dict(base, log=value)
        with pytest.raises(ValueError, match="removed_by_modality"):
            replace(manifest, removed_by_modality=forged)
    with pytest.raises(ValueError, match="Soma"):
        replace(manifest, removed_by_modality=dict(base, log=base["log"] + 1))
    with pytest.raises(ValueError, match="topology"):
        replace(manifest, removed_by_modality=dict(base, topology=1))


def test_degradation_run_binds_manifest_to_exact_dataset(observations):
    harness = ObservationDegradationHarness()
    first = harness.apply(observations, "random_log_drop", .1, seed=1)
    second = harness.apply(observations, "random_span_drop", .1, seed=1)
    with pytest.raises(ValueError, match="output_dataset"):
        type(first)(second.dataset, first.manifest)
    with pytest.raises(ValueError, match="output_dataset"):
        replace(first.manifest, output_dataset_sha256="0" * 64)
    with pytest.raises(ValueError, match="input_dataset|output_dataset|run_identity"):
        replace(first.manifest, ground_truth_sha256="0" * 64)
    duplicate_mode = ObservationDataset(first.dataset.events, first.dataset.topology,
                                        first.dataset.ground_truth,
                                        allow_duplicate_references=True)
    duplicate_manifest = replace(
        first.manifest, output_dataset_sha256=duplicate_mode.dataset_fingerprint,
        output_observation_sha256=duplicate_mode.observation_fingerprint)
    with pytest.raises(ValueError, match="duplicatas"):
        type(first)(duplicate_mode, duplicate_manifest)


@pytest.mark.parametrize("severity", (False, True))
def test_boolean_severity_is_rejected(observations, severity):
    with pytest.raises(TypeError, match="severity"):
        ObservationDegradationHarness().apply(
            observations, "random_log_drop", severity, seed=1)
    manifest = ObservationDegradationHarness().apply(
        observations, "random_log_drop", .1, seed=1).manifest
    with pytest.raises(TypeError, match="severity"):
        replace(manifest, severity=severity)


def test_observation_dataset_rejects_ambiguous_collections_and_invalid_mappings(observations):
    with pytest.raises(TypeError, match="topology"):
        ObservationDataset(observations.events, [], observations.ground_truth)
    with pytest.raises(TypeError, match="ground_truth"):
        ObservationDataset(observations.events, observations.topology, [])
    with pytest.raises(TypeError, match="iterável"):
        ObservationDataset(observations.events, {"a": "service-b"},
                           observations.ground_truth)
    with pytest.raises(TypeError, match="iterável"):
        ObservationDataset(observations.events, observations.topology,
                           {"incident": "service-a"})


def test_operator_policy_defensively_copies_modalities_and_configuration():
    modalities = ["trace"]
    configuration = {"nested": {"values": ["a"]}}
    policy = OperatorPolicyV3("random_span_drop", "custom-v1", modalities,
                              "event", configuration=configuration)
    fingerprint = policy.fingerprint
    modalities.append("log")
    configuration["nested"]["values"].append("changed")
    assert policy.modalities == ("trace",)
    assert policy.fingerprint == fingerprint
    assert "changed" not in json.dumps(policy.to_dict())


def test_kafka_replay_result_contract_is_strict():
    assert KafkaReplayResult(2, 1, 1, ("failure",)).attempted == 2
    with pytest.raises(ValueError, match="attempted"):
        KafkaReplayResult(2, 2, 1, ("failure",))
    with pytest.raises(ValueError, match="failed"):
        KafkaReplayResult(2, 1, 1, ())
    with pytest.raises(TypeError, match="tuple"):
        KafkaReplayResult(1, 0, 1, ["failure"])
    with pytest.raises(ValueError, match="inteiro"):
        KafkaReplayResult(True, 1, 0, ())
