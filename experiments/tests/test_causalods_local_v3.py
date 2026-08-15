from __future__ import annotations

import gzip
import json
import sys
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "correlation-engine"
for path in (str(ROOT), str(ENGINE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from experiments.causalods_local_v3.core import (  # noqa: E402
    DATASET_ID, EXPECTED_MODALITIES, SCENARIOS, Campaign,
    ObservedFaultControllerState,
    atomic_json, audit_anti_leakage, audit_events, audit_run_isolation,
    audit_service_state_reset, audit_topic_offsets, derive_repeat_seed, file_sha256,
    ground_truth_from_observed_state, requested_fault_configuration,
    validate_fault_controller_binding,
)
from experiments.causalods_local_v3.audit import build_final_audit  # noqa: E402
from experiments.causalods_local_v3.live import (  # noqa: E402
    FINAL_OUTPUT, LiveExecutor, collect_real_metrics, validate_run_mode,
)
from experiments.multimodal_v3.core import DatasetCapabilityV3  # noqa: E402
from multimodal_v1.public_datasets_v3.contracts import IncidentManifest  # noqa: E402
from multimodal_v1.contracts import UnifiedEvent  # noqa: E402


def event(modality: str, correlation_id: str, service: str = "order-service"):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "event_id": f"{modality}-{service}-{correlation_id}", "event_name": f"observed.{modality}",
        "event_type": modality, "signal_type": modality, "event_timestamp": now,
        "correlation_id": None if modality == "metric" else correlation_id,
        "service_name": service, "trace_id": "a" * 32 if modality == "trace" else None,
        "span_id": "b" * 16 if modality == "trace" else None,
        "attributes": {"http.method": "POST"},
        "source_reference": ("kafka:metrics-v1:0" if modality == "metric"
                             else f"kafka:{modality}:0"),
    }


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.next_start = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def __call__(self, scenario, seed, run_id, correlation_id):
        self.calls.append((scenario.name, seed, run_id, correlation_id))
        start = self.next_start
        self.next_start += timedelta(seconds=2)
        finish = start + timedelta(seconds=1)
        rows = [event(item, correlation_id) for item in EXPECTED_MODALITIES]
        if scenario.name != "order_error":
            rows.extend(event(item, correlation_id, "payment-service")
                        for item in ("trace", "log", "business_event"))
        for row in rows:
            row["event_timestamp"] = (start + timedelta(milliseconds=500)).isoformat()
        requested = requested_fault_configuration(scenario, seed)
        service_state = {
            service: {**configuration,
                      "container_id": f"container-{service}-{seed}",
                      "container_started_at": start.isoformat()}
            for service, configuration in requested["services"].items()
        }
        observed = ObservedFaultControllerState.from_mapping(service_state)
        return {
            "events": rows, "experiment_started_at": start.isoformat(),
            "request_started_at": (start + timedelta(milliseconds=200)).isoformat(),
            "collection_finished_at": finish.isoformat(),
            "fault_started_at": start.isoformat(), "fault_stopped_at": finish.isoformat(),
            "http_status": 503 if scenario.fault_type == "error" else 200,
            "dlq_count": 0,
            "request_metadata": {"experiment_run_id": run_id,
                                 "request_id": f"request-{seed}",
                                 "business.correlation.id": correlation_id,
                                 "request_payload": {"product": "opaque", "quantity": 1}},
            "timing": {"duration_seconds": 1.0,
                       "experiment_started_at": start.isoformat(),
                       "request_started_at": (start + timedelta(milliseconds=200)).isoformat(),
                       "request_finished_at": (start + timedelta(milliseconds=500)).isoformat(),
                       "collection_finished_at": finish.isoformat()},
            "requested_fault_configuration": requested,
            "observed_fault_controller_state": observed.to_dict(),
            "fault_controller_binding_audit": validate_fault_controller_binding(
                scenario, seed, observed),
            "fault_controller_state": service_state,
            "service_state": service_state,
            "kafka_offsets_before": {topic: {"0": 10} for topic in (
                "traces-v3", "logs-v1", "metrics-v1", "business-events-v1",
                "multimodal-dead-letter-v1")},
            "kafka_offsets_after": {topic: {"0": 10 if topic ==
                "multimodal-dead-letter-v1" else 11} for topic in (
                "traces-v3", "logs-v1", "metrics-v1", "business-events-v1",
                "multimodal-dead-letter-v1")},
            "pipeline": {"completed": True, "rca_result_emitted": True,
                         "ranking_produced": True, "reliability_gate_executed": True},
        }


def test_exact_scenario_definitions_and_fault_semantics():
    assert tuple(SCENARIOS) == ("healthy", "order_error", "order_latency",
                               "payment_error", "payment_latency")
    assert SCENARIOS["healthy"].target_service is None
    assert not SCENARIOS["healthy"].fault_injected
    assert SCENARIOS["order_error"].target_service == "order-service"
    assert SCENARIOS["payment_latency"].delay_ms == 1500
    assert all(item.rate == 1 for name, item in SCENARIOS.items() if name != "healthy")


def test_seed_is_stable_and_scenario_specific():
    first = derive_repeat_seed(42, "healthy", 1)
    assert first == derive_repeat_seed(42, "healthy", 1)
    assert first != derive_repeat_seed(42, "healthy", 2)
    assert first != derive_repeat_seed(42, "order_error", 1)
    assert 0 <= first <= 0x7FFF_FFFF


def _observed(scenario_name: str, seed: int) -> dict:
    requested = requested_fault_configuration(SCENARIOS[scenario_name], seed)
    return {
        service: {**configuration,
                  "container_id": f"container-{service}-{seed}",
                  "container_started_at": "2026-08-14T00:00:00Z"}
        for service, configuration in requested["services"].items()
    }


def test_ground_truth_is_observed_controller_derived_and_healthy_is_null():
    seed = derive_repeat_seed(42, "healthy", 1)
    observed = ObservedFaultControllerState.from_mapping(_observed("healthy", seed))
    binding = validate_fault_controller_binding(SCENARIOS["healthy"], seed, observed)
    truth = ground_truth_from_observed_state(
        run_id=f"{DATASET_ID}/healthy/0001", scenario_name="healthy",
        repeat_index=1, base_seed=42, observed=observed, binding_audit=binding,
        fault_started_at="2026-08-14T00:00:00Z", fault_stopped_at=None,
        request_id="req-1", correlation_id="corr-1")
    assert truth.root_cause_service is None
    assert truth.root_cause_type == "healthy"
    assert truth.fault_parameters == {"mode": "none", "rate": 0.0,
                                      "delay_ms": 0, "seed": seed}


def test_requested_order_error_observed_order_none_fails_binding():
    seed = derive_repeat_seed(42, "order_error", 1)
    state = _observed("order_error", seed)
    state["order-service"].update(mode="none", rate=0.0)
    audit = validate_fault_controller_binding(SCENARIOS["order_error"], seed, state)
    assert audit["status"] == "FAIL"
    assert {item["field"] for item in audit["violations"]} >= {"active_services", "mode"}


def test_requested_payment_latency_observed_payment_error_fails_binding():
    seed = derive_repeat_seed(42, "payment_latency", 1)
    state = _observed("payment_latency", seed)
    state["payment-service"]["mode"] = "error"
    audit = validate_fault_controller_binding(SCENARIOS["payment_latency"], seed, state)
    assert audit["status"] == "FAIL"
    assert any(item.get("field") == "mode" for item in audit["violations"])


def test_observed_seed_mismatch_fails_binding():
    seed = derive_repeat_seed(42, "order_error", 1)
    state = _observed("order_error", seed)
    state["order-service"]["seed"] = seed + 1
    audit = validate_fault_controller_binding(SCENARIOS["order_error"], seed, state)
    assert audit["status"] == "FAIL"
    assert any(item.get("field") == "seed" for item in audit["violations"])


def test_observed_delay_mismatch_fails_binding():
    seed = derive_repeat_seed(42, "payment_latency", 1)
    state = _observed("payment_latency", seed)
    state["payment-service"]["delay_ms"] = 1499
    audit = validate_fault_controller_binding(SCENARIOS["payment_latency"], seed, state)
    assert audit["status"] == "FAIL"
    assert any(item.get("field") == "delay_ms" for item in audit["violations"])


def test_two_simultaneous_faults_fail_binding():
    seed = derive_repeat_seed(42, "order_error", 1)
    state = _observed("order_error", seed)
    state["payment-service"].update(mode="error", rate=1.0)
    audit = validate_fault_controller_binding(SCENARIOS["order_error"], seed, state)
    assert audit["status"] == "FAIL"
    assert audit["active_services"] == ["order-service", "payment-service"]


def test_healthy_with_any_observed_fault_fails_binding():
    seed = derive_repeat_seed(42, "healthy", 1)
    state = _observed("healthy", seed)
    state["order-service"].update(mode="error", rate=1.0)
    assert validate_fault_controller_binding(
        SCENARIOS["healthy"], seed, state)["status"] == "FAIL"


def test_ground_truth_derives_fault_service_type_and_parameters_from_observed_state():
    seed = derive_repeat_seed(42, "payment_latency", 1)
    observed = ObservedFaultControllerState.from_mapping(
        _observed("payment_latency", seed))
    binding = validate_fault_controller_binding(
        SCENARIOS["payment_latency"], seed, observed)
    truth = ground_truth_from_observed_state(
        run_id=f"{DATASET_ID}/payment_latency/0001",
        scenario_name="payment_latency", repeat_index=1, base_seed=42,
        observed=observed, binding_audit=binding,
        fault_started_at="2026-08-14T00:00:00Z", fault_stopped_at=None,
        request_id="req-1", correlation_id="corr-1")
    assert truth.root_cause_service == "payment-service"
    assert truth.root_cause_type == "latency"
    assert truth.fault_parameters == {"mode": "latency", "rate": 1.0,
                                      "delay_ms": 1500, "seed": seed}


def test_ground_truth_is_blocked_when_binding_fails():
    seed = derive_repeat_seed(42, "order_error", 1)
    observed = ObservedFaultControllerState.from_mapping(_observed("healthy", seed))
    binding = validate_fault_controller_binding(SCENARIOS["order_error"], seed, observed)
    with pytest.raises(ValueError, match="FAULT_CONTROLLER_BINDING=FAIL"):
        ground_truth_from_observed_state(
            run_id=f"{DATASET_ID}/order_error/0001", scenario_name="order_error",
            repeat_index=1, base_seed=42, observed=observed, binding_audit=binding,
            fault_started_at="2026-08-14T00:00:00Z", fault_stopped_at=None,
            request_id="req-1", correlation_id="corr-1")


def test_campaign_mode_guard_allows_only_exact_smoke_or_final_shape(tmp_path):
    assert validate_run_mode("smoke", "payment_latency", 1, tmp_path) == tmp_path
    assert validate_run_mode("final", "all", 100, None) == FINAL_OUTPUT
    for scenario, repeat in (("all", 50), ("all", 101), ("payment_error", 100)):
        with pytest.raises(ValueError, match="MODE=final"):
            validate_run_mode("final", scenario, repeat, tmp_path / "final")
    with pytest.raises(ValueError, match="MODE=smoke"):
        validate_run_mode("smoke", "all", 2, tmp_path)
    with pytest.raises(ValueError, match="output separado"):
        validate_run_mode("final", "all", 100, tmp_path / "smoke-v2")


def test_anti_leakage_is_structured_not_substring_based():
    safe = event("log", "corr")
    safe["attributes"]["fault_tolerance_enabled"] = True
    assert audit_anti_leakage([safe])["status"] == "PASS"
    unsafe = event("log", "corr")
    unsafe["attributes"]["fault_target_service"] = "order-service"
    audit = audit_anti_leakage([unsafe])
    assert audit["status"] == "FAIL"
    assert audit["forbidden_keys"] == ["$[0].attributes.fault_target_service"]


def test_modality_and_identity_require_real_nonempty_events():
    correlation = "corr-123"
    rows = [event(item, correlation) for item in EXPECTED_MODALITIES]
    audit = audit_events(rows, correlation)
    assert audit["modality_status"] == "PASS"
    assert audit["identity_status"] == "PASS"
    assert audit["counts"] == {item: 1 for item in EXPECTED_MODALITIES}
    assert audit_events(rows[:-1], correlation)["modality_status"] == "FAIL"
    rows[0]["correlation_id"] = "other"
    assert audit_events(rows, correlation)["identity_status"] == "FAIL"


def test_modality_audit_accepts_signal_enum_from_validated_unified_event():
    correlation = "corr-enum"
    rows = [event(item, correlation) for item in ("trace", "log", "business_event")]
    rows.append(UnifiedEvent(
        event_id="metric-enum", event_name="process_cpu_usage", event_type="metric",
        signal_type="metric", event_timestamp=datetime.now(timezone.utc),
        service_name="order-service", metric_name="process_cpu_usage", metric_value=0.1,
    ).to_dict())
    assert audit_events(rows, correlation)["counts"]["metric"] == 1


def test_atomic_json_leaves_complete_parseable_file(tmp_path):
    target = tmp_path / "checkpoint.json"
    atomic_json(target, {"status": "COMPLETE", "value": 1})
    assert json.loads(target.read_text()) == {"status": "COMPLETE", "value": 1}
    assert not list(tmp_path.glob(".checkpoint.json.*"))


def test_campaign_manifest_capability_hash_index_and_resume(tmp_path):
    executor = FakeExecutor()
    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=executor)
    result = campaign.run(SCENARIOS, repeat=1)
    assert result["completed"] == result["new_executions"] == 5
    assert len(executor.calls) == 5
    index = json.loads((tmp_path / "INDEX.json").read_text())
    assert index["count"] == 5
    assert len({item["incident_id"] for item in index["incidents"]}) == 5
    campaign_manifest = json.loads((tmp_path / "campaign-manifest.json").read_text())
    assert {key: campaign_manifest[key] for key in (
        "target_runs", "repeat_target_per_scenario", "scenario_count", "workers",
        "parallel", "smoke_runs_included",
    )} == {"target_runs": 500, "repeat_target_per_scenario": 100,
           "scenario_count": 5, "workers": 1, "parallel": False,
           "smoke_runs_included": False}
    for row in index["incidents"]:
        manifest = IncidentManifest.from_dict(json.loads(
            (tmp_path / row["manifest_path"]).read_text()))
        assert manifest.expected_modalities == tuple(sorted(EXPECTED_MODALITIES))
        assert manifest.usable_for_benchmark
        capability = DatasetCapabilityV3.from_incident_manifest(manifest)
        assert capability.available_modalities == tuple(sorted(EXPECTED_MODALITIES))
        assert capability.manifest_sha256 == manifest.fingerprint
        for relative, digest in row["artifact_hashes"].items():
            assert file_sha256(tmp_path / relative) == digest
        checkpoint = _checkpoint(tmp_path, row["run_id"])
        assert checkpoint["fault_controller_binding_audit"]["status"] == "PASS"
        safe = row["run_id"].replace("/", "__")
        ground = json.loads((tmp_path / "incidents" / safe / "ground-truth.json").read_text())
        observed = tmp_path / "incidents" / safe / "observed-fault-controller-state.json"
        assert ground["observed_fault_controller_state_sha256"] == file_sha256(observed)
    resumed = campaign.run(SCENARIOS, repeat=1, resume=True)
    assert resumed["completed"] == resumed["skipped"] == resumed["resumed"] == 5
    assert resumed["new_executions"] == 0
    assert len(executor.calls) == 5
    assert json.loads((tmp_path / "audits" / "run-isolation.json").read_text())["status"] == "PASS"


def test_inconsistent_checkpoint_is_reexecuted_only_for_that_case(tmp_path):
    executor = FakeExecutor()
    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=executor)
    campaign.run(("healthy", "order_error"))
    checkpoint = json.loads(campaign.checkpoint_path(
        f"{DATASET_ID}/order_error/0001").read_text())
    relative = next(path for path in checkpoint["artifact_hashes"] if path.endswith("log.json.gz"))
    (tmp_path / relative).write_bytes(b"corrupt")
    resumed = campaign.run(("healthy", "order_error"), resume=True)
    assert resumed["skipped"] == 1
    assert resumed["new_executions"] == 1


def test_binding_failed_checkpoint_is_not_accepted_by_resume(tmp_path):
    executor = FakeExecutor()
    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=executor)
    campaign.run(("healthy",))
    checkpoint_path = campaign.checkpoint_path(f"{DATASET_ID}/healthy/0001")
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["fault_controller_binding_audit"]["status"] = "FAIL"
    atomic_json(checkpoint_path, checkpoint)
    resumed = campaign.run(("healthy",), resume=True)
    assert resumed["skipped"] == 0
    assert resumed["new_executions"] == 1
    assert len(executor.calls) == 2


def test_observed_mismatch_persists_proof_but_never_ground_truth_or_checkpoint(tmp_path):
    class MismatchExecutor(FakeExecutor):
        def __call__(self, scenario, seed, run_id, correlation_id):
            result = dict(super().__call__(scenario, seed, run_id, correlation_id))
            observed = dict(result["observed_fault_controller_state"])
            observed["services"] = {
                key: dict(value) for key, value in observed["services"].items()}
            observed["services"]["order-service"].update(mode="none", rate=0.0)
            result["observed_fault_controller_state"] = observed
            return result

    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1,
                        executor=MismatchExecutor())
    with pytest.raises(RuntimeError, match="FAULT_CONTROLLER_BINDING=FAIL"):
        campaign.run(("order_error",))
    safe = f"{DATASET_ID}__order_error__0001"
    incident = tmp_path / "incidents" / safe
    assert (incident / "requested-fault-configuration.json").is_file()
    assert (incident / "observed-fault-controller-state.json").is_file()
    audit = json.loads((incident / "fault-controller-binding-audit.json").read_text())
    assert audit["status"] == "FAIL"
    assert not (incident / "ground-truth.json").exists()
    assert not campaign.checkpoint_path(f"{DATASET_ID}/order_error/0001").exists()


def test_fault_ground_truth_never_appears_in_normalized_files(tmp_path):
    executor = FakeExecutor()
    Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=executor).run(("order_error",))
    for path in (tmp_path / "incidents").rglob("*.json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as source:
            assert audit_anti_leakage(json.load(source))["status"] == "PASS"


def test_run_isolation_detects_reused_correlation_id(tmp_path):
    metadata_a = tmp_path / "incidents" / "a" / "request-metadata.json"
    metadata_b = tmp_path / "incidents" / "b" / "request-metadata.json"
    atomic_json(metadata_a, {"business.correlation.id": "same"})
    atomic_json(metadata_b, {"business.correlation.id": "same"})
    rows = [
        {"run_id": "a", "artifact_hashes": {str(metadata_a.relative_to(tmp_path)): "x"},
         "timing": {"experiment_started_at": "2026-08-14T00:00:00Z",
                    "collection_finished_at": "2026-08-14T00:00:01Z"}},
        {"run_id": "b", "artifact_hashes": {str(metadata_b.relative_to(tmp_path)): "x"},
         "timing": {"experiment_started_at": "2026-08-14T00:00:02Z",
                    "collection_finished_at": "2026-08-14T00:00:03Z"}},
    ]
    assert audit_run_isolation(rows, tmp_path)["status"] == "FAIL"


def _checkpoint(root: Path, run_id: str):
    safe = run_id.replace("/", "__")
    return json.loads((root / "checkpoints" / f"{safe}.json").read_text())


def _rewrite_events(root: Path, checkpoint, modality: str, change):
    relative = next(path for path in checkpoint["artifact_hashes"]
                    if path.endswith(f"/{modality}.json.gz"))
    target = root / relative
    with gzip.open(target, "rt", encoding="utf-8") as source:
        rows = json.load(source)
    change(rows)
    with gzip.open(target, "wt", encoding="utf-8") as stream:
        json.dump(rows, stream)


def test_scientific_metrics_exclude_actuator_diagnostic_source():
    rows = [event(item, "corr") for item in EXPECTED_MODALITIES]
    rows[-2 if EXPECTED_MODALITIES[-1] == "business_event" else -1]
    metric = next(item for item in rows if item["signal_type"] == "metric")
    metric["source_reference"] = "http://order-service:8081/actuator/prometheus"
    assert audit_events(rows, "corr")["metric_pipeline_provenance"]["status"] == "FAIL"
    assert "collect_real_metrics" not in inspect.getsource(LiveExecutor.__call__)
    assert "actuator/prometheus" in inspect.getsource(collect_real_metrics)
    overlay = (ROOT / "docker-compose.causalods-local-v3.yml").read_text()
    assert overlay.count('OTEL_METRIC_EXPORT_INTERVAL: "30000"') == 2
    assert 'PLATFORM_V3_INDEX_UNASSOCIATED_EVENTS: "true"' in overlay
    runtime = (ROOT / "correlation-engine" / "platform_v3" / "runtime.py").read_text()
    assert '"PLATFORM_V3_INDEX_UNASSOCIATED_EVENTS", "false"' in runtime
    collector = (ROOT / "collector" / "otel-collector-config.causalods-local-v3.yaml").read_text()
    assert "filter/scientific_metrics" in collector
    assert "http.server.request.duration" in collector


def test_telemetry_topic_offset_audit_requires_all_scientific_topics_and_zero_dlq():
    topics = ("traces-v3", "logs-v1", "metrics-v1", "business-events-v1",
              "multimodal-dead-letter-v1")
    before = {topic: {"0": 10} for topic in topics}
    after = {topic: {"0": 10 if topic.endswith("dead-letter-v1") else 11}
             for topic in topics}
    assert audit_topic_offsets(before, after)["status"] == "PASS"
    after["metrics-v1"]["0"] = 10
    assert audit_topic_offsets(before, after)["status"] == "FAIL"


def test_run_isolation_detects_foreign_correlation(tmp_path):
    Campaign(tmp_path, base_seed=42, disk_gate_bytes=1,
             executor=FakeExecutor()).run(("healthy",))
    checkpoint = _checkpoint(tmp_path, f"{DATASET_ID}/healthy/0001")
    _rewrite_events(tmp_path, checkpoint, "log",
                    lambda rows: rows[0].update(correlation_id="foreign"))
    report = audit_run_isolation([checkpoint], tmp_path)
    assert "foreign_correlation_id" in {item["reason"] for item in report["violations"]}


def test_run_isolation_detects_duplicate_event_id_across_runs(tmp_path):
    Campaign(tmp_path, base_seed=42, disk_gate_bytes=1,
             executor=FakeExecutor()).run(("healthy", "order_error"))
    first = _checkpoint(tmp_path, f"{DATASET_ID}/healthy/0001")
    second = _checkpoint(tmp_path, f"{DATASET_ID}/order_error/0001")
    first_path = next(path for path in first["artifact_hashes"] if path.endswith("/trace.json.gz"))
    with gzip.open(tmp_path / first_path, "rt", encoding="utf-8") as source:
        duplicate = json.load(source)[0]["event_id"]
    _rewrite_events(tmp_path, second, "trace",
                    lambda rows: rows[0].update(event_id=duplicate))
    report = audit_run_isolation([first, second], tmp_path)
    assert "duplicate_event_id_across_runs" in {item["reason"] for item in report["violations"]}


def test_run_isolation_detects_metric_outside_window_and_unknown_service(tmp_path):
    Campaign(tmp_path, base_seed=42, disk_gate_bytes=1,
             executor=FakeExecutor()).run(("healthy",))
    checkpoint = _checkpoint(tmp_path, f"{DATASET_ID}/healthy/0001")
    def corrupt(rows):
        rows[0]["event_timestamp"] = "2026-08-13T23:59:00Z"
        rows[0]["service_name"] = "unknown-service"
    _rewrite_events(tmp_path, checkpoint, "metric", corrupt)
    reasons = {item["reason"] for item in
               audit_run_isolation([checkpoint], tmp_path)["violations"]}
    assert {"metric_outside_run_window", "unknown_service"}.issubset(reasons)


def test_run_isolation_detects_ambiguous_and_overlapping_metric_windows(tmp_path):
    Campaign(tmp_path, base_seed=42, disk_gate_bytes=1,
             executor=FakeExecutor()).run(("healthy", "order_error"))
    first = _checkpoint(tmp_path, f"{DATASET_ID}/healthy/0001")
    second = _checkpoint(tmp_path, f"{DATASET_ID}/order_error/0001")
    second["timing"]["experiment_started_at"] = "2026-08-14T00:00:00.500000+00:00"
    _rewrite_events(tmp_path, second, "metric",
                    lambda rows: rows[0].update(event_timestamp="2026-08-14T00:00:00.750000Z"))
    reasons = {item["reason"] for item in
               audit_run_isolation([first, second], tmp_path)["violations"]}
    assert {"metric_ambiguous_between_runs", "overlapping_metric_windows"}.issubset(reasons)


def test_service_state_reset_audit_requires_both_recreations_before_request():
    state = {service: {"container_id": service,
                       "container_started_at": "2026-08-14T00:00:00Z"}
             for service in ("order-service", "payment-service")}
    assert audit_service_state_reset(state, "2026-08-14T00:00:01Z")["status"] == "PASS"
    state["payment-service"]["container_started_at"] = "2026-08-14T00:00:02Z"
    assert audit_service_state_reset(state, "2026-08-14T00:00:01Z")["status"] == "FAIL"


def test_resume_report_is_created_by_actual_resume_execution(tmp_path):
    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=FakeExecutor())
    campaign.run(("healthy",))
    result = campaign.run(("healthy",), resume=True)
    persisted = json.loads((tmp_path / "audits" / "resume-report.json").read_text())
    assert persisted == result
    assert persisted["attempted"] == persisted["completed"] == persisted["skipped"] == 1
    assert persisted["new_executions"] == 0
    assert isinstance(persisted["duration_seconds"], float)


def test_audit_has_no_hard_coded_resume_timing_and_disk_includes_infrastructure(tmp_path):
    assert "2.3951250970130786" not in inspect.getsource(build_final_audit)
    campaign = Campaign(tmp_path, base_seed=42, disk_gate_bytes=1, executor=FakeExecutor())
    campaign.run(("healthy",))
    campaign.run(("healthy",), resume=True)
    atomic_json(tmp_path / "audits" / "resource-before.json", {
        "filesystem_used_bytes": 1000, "dataset_output_bytes": 0,
        "opensearch": {"total_bytes": 100}, "kafka": {"total_bytes": 100},
        "docker_logs": {"total_bytes": 100}})
    atomic_json(tmp_path / "audits" / "resource-after.json", {
        "filesystem_used_bytes": 3000, "dataset_output_bytes": 500,
        "opensearch": {"total_bytes": 300}, "kafka": {"total_bytes": 400},
        "docker_logs": {"total_bytes": 150}})
    resource_report = build_final_audit(tmp_path)["resource_report"]
    assert resource_report["filesystem_used_delta"] == 2000
    assert resource_report["opensearch_delta"] == 200
    assert resource_report["kafka_delta"] == 300
    assert resource_report["required_projection_500"] >= 200000
