from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from experiments.final_campaigns_v3.h3_materializer import (
    BUNDLE_VERSION, SCHEMA_VERSION, _forbidden_paths, _payload_bytes,
    _resolve_rca_context_id, _scientific_fingerprint, validate_bundle,
)
from experiments.final_campaigns_v3.providers import load_h3_inputs
from experiments.multimodal_v3 import DatasetCapabilityV3
from experiments.multimodal_v3.core import file_sha256, sha256_value
from experiments.multimodal_v3.fixtures import smoke_config, smoke_rca
from models.rca_context import RCAContext
from pipeline.rca_result_factory import RCAResultFactory
from types import SimpleNamespace


def _config():
    capabilities = tuple(DatasetCapabilityV3(
        "frozen", "system", f"h3-{index:02d}",
        ("trace", "log", "metric", "business_event"), f"{index + 1:064x}",
        "fault") for index in range(40))
    return replace(smoke_config("h3-materializer-test"), datasets=capabilities,
                   hypotheses=("H3",), sample_size=40)


def _bundle(root: Path, *, corrupt_raw=False, omit_last=False):
    config = _config()
    incidents = []
    rca = smoke_rca().to_dict()
    for incident_id in sorted(item.incident_id for item in config.datasets):
        directory = root / "bundle" / "incidents" / incident_id
        directory.mkdir(parents=True)
        rca_payload = {**rca, "rca_result_id": f"rca-{incident_id}",
                       "metadata": {**rca["metadata"], "incident_id": incident_id}}
        raw = [{"event_id": f"event-{incident_id}", "service_name": "service"}]
        provenance = {"incident_id": incident_id, "model_retrained": False,
                      "model_retuned": False, "ground_truth_accessed": False}
        for name, payload in (("rca-result.json", rca_payload),
                              ("raw-telemetry.json", raw),
                              ("provenance.json", provenance)):
            (directory / name).write_bytes(_payload_bytes(payload))
        incidents.append({
            "incident_id": incident_id,
            "rca_result_path": str((directory / "rca-result.json").relative_to(root)),
            "rca_result_sha256": file_sha256(directory / "rca-result.json"),
            "rca_result_contract_sha256": sha256_value(rca_payload),
            "raw_telemetry_path": str((directory / "raw-telemetry.json").relative_to(root)),
            "raw_telemetry_sha256": file_sha256(directory / "raw-telemetry.json"),
            "provenance_path": str((directory / "provenance.json").relative_to(root)),
            "provenance_sha256": file_sha256(directory / "provenance.json"),
            "model_artifact_sha256": "a" * 64,
        })
    if omit_last:
        incidents.pop()
    index = {
        "schema_version": SCHEMA_VERSION, "bundle_version": BUNDLE_VERSION,
        "generated_at": "deterministic-from-frozen-inputs",
        "incident_count": len(incidents), "incidents": incidents,
        "h3_sample_selection_sha256": sha256_value(sorted(
            item.incident_id for item in config.datasets)),
        "source_scientific_configuration_sha256": config.configuration_sha256,
        "source_commit": None,
        "model": {"method": "xgboost-rca-v3", "artifact_sha256": "a" * 64,
                  "benchmark_run_fingerprint": "b" * 64},
        "feature_schema": {"sha256": "c" * 64, "registry_sha256": "d" * 64},
        "dataset_split": {"training_dataset_sha256": "e" * 64},
    }
    index["scientific_bundle_fingerprint"] = _scientific_fingerprint(index)
    path = root / "bundle" / "INDEX.json"
    path.write_bytes(_payload_bytes(index))
    if corrupt_raw:
        target = root / incidents[0]["raw_telemetry_path"]
        target.write_text(target.read_text() + " ")
    return config, path, index


def test_valid_exact_h3_bundle_loads_provider_and_is_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    config, path, index = _bundle(first)
    _, other_path, other = _bundle(second)
    expected = sorted(item.incident_id for item in config.datasets)
    assert validate_bundle(path, expected_incidents=expected, repository_root=first)
    assert index["scientific_bundle_fingerprint"] == other["scientific_bundle_fingerprint"]
    assert [item["rca_result_sha256"] for item in index["incidents"]] == [
        item["rca_result_sha256"] for item in other["incidents"]]
    assert [item["raw_telemetry_sha256"] for item in index["incidents"]] == [
        item["raw_telemetry_sha256"] for item in other["incidents"]]
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(first))})
    loaded = load_h3_inputs(config=configured, repository_root=first)
    assert len(loaded["rca_results"]) == len(loaded["raw_telemetry"]) == 40
    assert other_path.is_file()


def test_h3_bundle_checksum_mismatch_is_rejected(tmp_path):
    config, path, _ = _bundle(tmp_path, corrupt_raw=True)
    with pytest.raises(ValueError, match="checksum"):
        validate_bundle(path, expected_incidents=[item.incident_id for item in config.datasets],
                        repository_root=tmp_path)


def test_h3_bundle_missing_incident_is_rejected(tmp_path):
    config, path, _ = _bundle(tmp_path, omit_last=True)
    with pytest.raises(ValueError, match="exatamente 40"):
        validate_bundle(path, expected_incidents=[item.incident_id for item in config.datasets],
                        repository_root=tmp_path)


@pytest.mark.parametrize("key", sorted(("ground_truth", "actual_services", "fault_service",
                                         "fault_location", "is_root_cause", "label_status",
                                         "root_cause_rank")))
def test_h3_leakage_scanner_rejects_target_fields(key):
    assert _forbidden_paths({"nested": [{key: "forbidden"}]}) == (f"$.nested[0].{key}",)


def test_h3_context_identity_uses_observed_trace_without_relabeling():
    bundle = SimpleNamespace(traces=("observed-trace-123",), workflow_key="workflow-a")
    assert _resolve_rca_context_id(bundle, "incident-a") == (
        "observed-trace-123", "observed_trace_id", "observed-trace-123")


def test_h3_identity_free_context_is_deterministic_distinct_and_factory_compatible():
    bundle = SimpleNamespace(traces=(), workflow_key="frozen:workflow-a")
    first = _resolve_rca_context_id(bundle, "incident-a")
    repeated = _resolve_rca_context_id(bundle, "incident-a")
    different = _resolve_rca_context_id(bundle, "incident-b")
    assert first == repeated
    assert first != different
    assert first[0].startswith("scientific-context-")
    assert first[1:] == ("synthetic_scientific_context", None)

    result = RCAResultFactory().build(RCAContext(
        trace_id=first[0], services=("service-a",),
        predictions={"service-a": {"probability": .8}},
        temporal_results={"service-a": {"score": .7}},
        graph_results={"service-a": {"score": .6}},
        anomaly_results={"service-a": {"score": .9}},
        shap_results={"service-a": {"support_score": .5, "contributions": ()}},
        model_version="xgboost-rca-v3", pipeline_version="h3-materializer-test",
    ))
    assert result.trace_id == first[0]
    assert not _forbidden_paths(result.to_dict())
