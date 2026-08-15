from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "correlation-engine"
for path in (str(ROOT), str(ENGINE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from experiments.causalods_local_v3.core import (  # noqa: E402
    DATASET_ID, EXPECTED_MODALITIES, SCENARIOS, Campaign, GroundTruth,
    atomic_json, audit_anti_leakage, audit_events, audit_run_isolation,
    derive_repeat_seed, file_sha256,
)
from experiments.multimodal_v3.core import DatasetCapabilityV3  # noqa: E402
from multimodal_v1.public_datasets_v3.contracts import IncidentManifest  # noqa: E402
from multimodal_v1.contracts import UnifiedEvent  # noqa: E402


def event(modality: str, correlation_id: str, service: str = "order-service"):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "event_id": f"{modality}-{service}", "event_name": f"observed.{modality}",
        "event_type": modality, "signal_type": modality, "event_timestamp": now,
        "correlation_id": None if modality == "metric" else correlation_id,
        "service_name": service, "trace_id": "a" * 32 if modality == "trace" else None,
        "span_id": "b" * 16 if modality == "trace" else None,
        "attributes": {"http.method": "POST"},
    }


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, scenario, seed, run_id, correlation_id):
        self.calls.append((scenario.name, seed, run_id, correlation_id))
        start = datetime.now(timezone.utc)
        finish = start + timedelta(seconds=1)
        rows = [event(item, correlation_id) for item in EXPECTED_MODALITIES]
        if scenario.name != "order_error":
            rows.extend(event(item, correlation_id, "payment-service")
                        for item in ("trace", "log", "business_event"))
        return {
            "events": rows, "experiment_started_at": start.isoformat(),
            "collection_finished_at": finish.isoformat(),
            "fault_started_at": start.isoformat(), "fault_stopped_at": finish.isoformat(),
            "http_status": 503 if scenario.fault_type == "error" else 200,
            "dlq_count": 0,
            "request_metadata": {"experiment_run_id": run_id,
                                 "request_id": f"request-{seed}",
                                 "business.correlation.id": correlation_id,
                                 "request_payload": {"product": "opaque", "quantity": 1}},
            "timing": {"duration_seconds": 1.0},
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


def test_ground_truth_is_controller_derived_and_healthy_is_null():
    seed = derive_repeat_seed(42, "healthy", 1)
    truth = GroundTruth(
        f"{DATASET_ID}/healthy/0001", "healthy", 1, False, None, None, "healthy",
        SCENARIOS["healthy"].parameters(seed), 42,
        derive_repeat_seed(42, "healthy", 1), seed,
        "2026-08-14T00:00:00Z", None, "req-1", "corr-1")
    assert truth.root_cause_service is None
    with pytest.raises(ValueError, match="diverge"):
        GroundTruth(f"{DATASET_ID}/healthy/0001", "healthy", 1, True,
                    "order-service", "order-service", "error", {}, 42, seed, seed,
                    "2026-08-14T00:00:00Z", None, "req-1", "corr-1")


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
        {"run_id": "a", "artifact_hashes": {str(metadata_a.relative_to(tmp_path)): "x"}},
        {"run_id": "b", "artifact_hashes": {str(metadata_b.relative_to(tmp_path)): "x"}},
    ]
    assert audit_run_isolation(rows, tmp_path)["status"] == "FAIL"
