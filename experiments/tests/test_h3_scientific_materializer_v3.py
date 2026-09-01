from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from experiments.final_campaigns_v3.h3_materializer import (
    BUNDLE_VERSION, SCHEMA_VERSION, _forbidden_paths, _payload_bytes,
    _resolve_rca_context_id, _scientific_fingerprint, validate_bundle,
)
import experiments.final_campaigns_v3.providers as providers
from experiments.final_campaigns_v3.providers import FrozenRawTelemetryMapping, load_h3_inputs
from experiments.multimodal_v3 import DatasetCapabilityV3
from experiments.multimodal_v3.core import ExperimentPlannerV3, file_sha256, sha256_value
from experiments.multimodal_v3.executors import H3ExperimentExecutor
from experiments.multimodal_v3.fixtures import smoke_config, smoke_rca
from explanations_v2 import ExplanationService
from experiments.causalods_campaign_bindings import build_executors
from models.rca_context import RCAContext
from pipeline.rca_result_factory import RCAResultFactory
from types import SimpleNamespace


def _config():
    capabilities = tuple(DatasetCapabilityV3(
        "frozen", "system", f"h3-{index:02d}",
        ("trace", "log", "metric", "business_event"), f"{index + 1:064x}",
        "fault", scenario=f"scenario-{index % 5}") for index in range(200))
    return replace(smoke_config("h3-materializer-test"), datasets=capabilities,
                   hypotheses=("H3",), seeds=(42,), sample_size=40)


def _planned_incidents(config):
    return sorted({run.incident_id for run in ExperimentPlannerV3(config).plan()
                   if run.hypothesis == "H3"
                   and run.experiment_family == "H3-EXPLANATIONS"})


def _sanitized(config):
    return replace(config, datasets=tuple(replace(
        item, fault_type=None, scenario=None, rca_correct=None, reliable=None)
        for item in config.datasets))


def _bundle(root: Path, *, corrupt_raw=False, omit_last=False):
    config = _config()
    incidents = []
    rca = smoke_rca().to_dict()
    expected = _planned_incidents(config)
    for incident_id in expected:
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
        "h3_sample_selection_sha256": sha256_value(expected),
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
    expected = _planned_incidents(config)
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
        validate_bundle(path, expected_incidents=_planned_incidents(config),
                        repository_root=tmp_path)


def test_h3_bundle_missing_incident_is_rejected(tmp_path):
    config, path, _ = _bundle(tmp_path, omit_last=True)
    with pytest.raises(ValueError, match="exatamente 40"):
        validate_bundle(path, expected_incidents=_planned_incidents(config),
                        repository_root=tmp_path)


def test_provider_uses_protocol_plan_not_direct_dataset_sample(tmp_path):
    config, path, _ = _bundle(tmp_path)
    planner = ExperimentPlannerV3(config)
    planned = set(_planned_incidents(config))
    direct = set(planner._h3_sample(tuple(config.datasets), seed=42))
    assert len(planned) == len(direct) == 40
    assert planned != direct
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(tmp_path))})
    assert len(load_h3_inputs(config=configured, repository_root=tmp_path)["rca_results"]) == 40


def test_provider_rejects_missing_or_different_planned_incident(tmp_path):
    config, path, index = _bundle(tmp_path)
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(tmp_path))})
    index["incidents"].pop()
    path.write_bytes(_payload_bytes(index))
    with pytest.raises(ValueError, match="amostra congelada"):
        load_h3_inputs(config=configured, repository_root=tmp_path)

    _, path, index = _bundle(tmp_path / "different")
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(tmp_path / "different"))})
    index["incidents"][0]["incident_id"] = "different-incident"
    path.write_bytes(_payload_bytes(index))
    with pytest.raises(ValueError, match="amostra congelada"):
        load_h3_inputs(config=configured, repository_root=tmp_path / "different")


def test_provider_rejects_sample_selection_hash_mismatch(tmp_path):
    config, path, index = _bundle(tmp_path)
    index["h3_sample_selection_sha256"] = "0" * 64
    path.write_bytes(_payload_bytes(index))
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(tmp_path))})
    with pytest.raises(ValueError, match="sample selection hash"):
        load_h3_inputs(config=configured, repository_root=tmp_path)


def test_all_h3_modes_bind_to_one_rca_contract_per_incident(tmp_path):
    config, path, index = _bundle(tmp_path)
    configured = replace(config, criteria={"scientific_rca_results":
                                           str(path.relative_to(tmp_path))})
    loaded = load_h3_inputs(config=configured, repository_root=tmp_path)
    contracts = {item["incident_id"]: item["rca_result_contract_sha256"]
                 for item in index["incidents"]}
    runs = ExperimentPlannerV3(config).plan()
    grouped = {}
    for run in runs:
        grouped.setdefault(run.incident_id, []).append(run)
    assert len(runs) == 120 and len(grouped) == 40
    assert all({run.explanation_mode for run in values} == {
        "generic", "raw_telemetry", "evidence_grounded"} for values in grouped.values())
    assert all(sha256_value(loaded["rca_results"][incident_id].to_dict()) == contracts[incident_id]
               for incident_id, values in grouped.items() for _ in values)


def test_frozen_raw_mapping_is_lazy_bounded_and_equivalent(tmp_path, monkeypatch):
    _, path, index = _bundle(tmp_path)
    calls = []
    real_read = providers._read

    def counted_read(value):
        calls.append(value)
        return real_read(value)

    monkeypatch.setattr(providers, "_read", counted_read)
    mapping = FrozenRawTelemetryMapping(tmp_path, index["incidents"])
    assert len(mapping) == 40
    assert list(mapping) == [item["incident_id"] for item in index["incidents"]]
    assert calls == []
    first, second = index["incidents"][:2]
    eager = tuple(real_read(tmp_path / first["raw_telemetry_path"]))
    assert mapping[first["incident_id"]] == eager
    assert len(calls) == 1
    mapping[second["incident_id"]]
    assert len(calls) == 2
    with pytest.raises(KeyError):
        mapping["unknown"]


def test_frozen_raw_mapping_fails_closed(tmp_path):
    _, _, index = _bundle(tmp_path)
    first = dict(index["incidents"][0])
    raw_path = tmp_path / first["raw_telemetry_path"]
    raw_path.write_text(raw_path.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        FrozenRawTelemetryMapping(tmp_path, (first,))[first["incident_id"]]

    traversal = {**first, "raw_telemetry_path": "../escape.json"}
    with pytest.raises(ValueError, match="escapa"):
        FrozenRawTelemetryMapping(tmp_path, (traversal,))[first["incident_id"]]

    raw_path.write_text("{}")
    malformed = {**first, "raw_telemetry_sha256": file_sha256(raw_path)}
    with pytest.raises(ValueError, match="lista JSON de objetos"):
        FrozenRawTelemetryMapping(tmp_path, (malformed,))[first["incident_id"]]


def test_h3_executor_preserves_lazy_mapping_and_loads_only_raw_mode():
    config = _config()
    runs = ExperimentPlannerV3(config).plan()
    incident_id = runs[0].incident_id

    class CountingMapping(dict):
        def __init__(self):
            super().__init__({incident_id: ({"event_id": "raw-1", "service_name": "service"},)})
            self.loads = 0

        def get(self, key, default=None):
            self.loads += 1
            return super().get(key, default)

    raw = CountingMapping()
    executor = H3ExperimentExecutor(ExplanationService(), {incident_id: smoke_rca()},
                                    raw_telemetry=raw)
    assert executor.raw_telemetry is raw and raw.loads == 0
    by_mode = {run.explanation_mode: run for run in runs if run.incident_id == incident_id}
    generic = executor.execute(by_mode["generic"])
    grounded = executor.execute(by_mode["evidence_grounded"])
    assert raw.loads == 0
    raw_result = executor.execute(by_mode["raw_telemetry"])
    assert raw.loads == 1
    selected = raw_result[0]["supporting_context"]["selected_raw_telemetry"][0]
    assert selected["selection_id"] == "raw-1"
    assert selected["payload"]["event_id"] == "raw-1"
    assert len({value[1]["rca_result_hash"] for value in (generic, grounded, raw_result)}) == 1


def test_provider_accepts_authoritative_binding_for_sanitized_config(tmp_path):
    config, path, index = _bundle(tmp_path)
    expected = tuple(_planned_incidents(config))
    configured = replace(_sanitized(config), criteria={
        "scientific_rca_results": str(path.relative_to(tmp_path))})
    with pytest.raises(ValueError, match="AUTHORITATIVE_SAMPLE_BINDING_REQUIRED"):
        load_h3_inputs(config=configured, repository_root=tmp_path)
    loaded = load_h3_inputs(
        config=configured, repository_root=tmp_path,
        expected_incident_ids=expected,
        expected_sample_sha256=sha256_value(list(expected)),
    )
    assert len(loaded["rca_results"]) == len(loaded["raw_telemetry"]) == 40
    assert tuple(item["incident_id"] for item in index["incidents"]) == expected


def test_provider_rejects_wrong_authoritative_binding(tmp_path):
    config, path, _ = _bundle(tmp_path)
    expected = tuple(_planned_incidents(config))
    configured = replace(_sanitized(config), criteria={
        "scientific_rca_results": str(path.relative_to(tmp_path))})
    wrong_ids = tuple(sorted((*expected[1:], "wrong-incident")))
    with pytest.raises(ValueError, match="diverge da amostra"):
        load_h3_inputs(
            config=configured, repository_root=tmp_path,
            expected_incident_ids=wrong_ids,
            expected_sample_sha256=sha256_value(list(wrong_ids)),
        )
    with pytest.raises(ValueError, match="authoritative sample hash"):
        load_h3_inputs(
            config=configured, repository_root=tmp_path,
            expected_incident_ids=expected, expected_sample_sha256="0" * 64,
        )


def test_build_executors_binds_original_sample_before_sanitization(
        tmp_path, monkeypatch):
    config, path, _ = _bundle(tmp_path)
    configured = replace(config, criteria={
        "scientific_rca_results": str(path.relative_to(tmp_path)),
        "h3_input_provider": "experiments.final_campaigns_v3.providers:load_h3_inputs",
    })
    monkeypatch.setenv("CAUSALODS_LLM_MODEL", "preflight-only")
    monkeypatch.setenv("CAUSALODS_LLM_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("CAUSALODS_LLM_API_KEY", "preflight-only")
    raw_decodes = []
    real_read = providers._read

    def audited_read(value):
        if value.name == "raw-telemetry.json":
            raw_decodes.append(value)
        return real_read(value)

    monkeypatch.setattr(providers, "_read", audited_read)
    executors = build_executors(
        config=configured, output=tmp_path / "output", repository_root=tmp_path)
    delegate = executors["H3"].delegate
    assert len(delegate.rca_results) == len(delegate.raw_telemetry) == 40
    assert isinstance(delegate.raw_telemetry, FrozenRawTelemetryMapping)
    assert raw_decodes == []
    assert all(not _forbidden_paths(rca.to_dict()) for rca in delegate.rca_results.values())


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
