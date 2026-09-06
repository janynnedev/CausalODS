from __future__ import annotations

import copy
import inspect
import json
import hashlib
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.final_campaigns_v3 import h4_final_40 as h4
from experiments.final_campaigns_v3.providers import (
    FrozenH4ObservationMapping,
    ScientificH4Evaluator,
    _resolve_scientific_baseline,
    evaluate_h4_ranking,
    evaluate_h4_reliability,
    load_h4_inputs,
)
from experiments.final_campaigns_v3.preprocessing import IncidentSource
from experiments.causalods_campaign_bindings import _H4ProvenanceExecutor
from experiments.multimodal_v3 import ExperimentPlannerV3
from experiments.multimodal_v3.executors import H4ExperimentExecutor
from experiments.multimodal_v3.core import verify_v2_freeze
from experiments.multimodal_v3.fixtures import smoke_config, smoke_observation
from multimodal_v1.degradation_v3 import (
    OPERATORS, ObservationDataset, ObservationDegradationHarness,
)
from multimodal_v1.features_v3.dataset import baseline_binding_sha256


ROOT = Path(__file__).resolve().parents[2]
TRANCHE = json.loads((ROOT / "experiments/configs/final-v3/"
                      "scientific-tranche-a-180.json").read_text())
CONFIG = json.loads((ROOT / "experiments/configs/final-v3/h4.json").read_text())
HISTORICAL_HASH = json.loads((ROOT / "artifacts/final-experiments-v3/h1h2-final-147/"
                              "final-147-manifest.json").read_text())["historical_test_sha256"]


class Vector:
    def __init__(self, name, **values):
        self.service_name, self.values = name, values


def test_exact_sample_is_tranche_test_40():
    assert h4.validate_sample_binding(TRANCHE["test_incident_ids"], TRANCHE,
                                      HISTORICAL_HASH) == HISTORICAL_HASH


def test_147_incident_sample_fails():
    values = TRANCHE["test_incident_ids"] + [f"extra-{i}" for i in range(107)]
    with pytest.raises(ValueError, match="SAMPLE_BINDING"):
        h4.validate_sample_binding(values, TRANCHE, HISTORICAL_HASH)


def test_alternate_40_sample_fails():
    values = list(TRANCHE["test_incident_ids"]); values[-1] = "alternate"
    with pytest.raises(ValueError, match="SAMPLE_BINDING"):
        h4.validate_sample_binding(values, TRANCHE, HISTORICAL_HASH)


@pytest.mark.parametrize(("field", "value"), [
    ("protocols", ["cross-system"]), ("seeds", [7]), ("repetitions", 2),
])
def test_protocol_seed_and_repetitions_fail_closed(field, value):
    config = copy.deepcopy(CONFIG); config[field] = value
    with pytest.raises(ValueError, match="CONTRACT_MISMATCH"):
        h4.validate_final_contract(config, TRANCHE["test_incident_ids"])


@pytest.mark.parametrize(("field", "value"), [
    ("h4_method", "random-forest"), ("h4_feature_view", "TRACE_ONLY"),
])
def test_method_and_feature_view_fail_closed(field, value):
    config = copy.deepcopy(CONFIG); config["criteria"][field] = value
    with pytest.raises(ValueError, match="CONTRACT_MISMATCH"):
        h4.validate_final_contract(config, TRANCHE["test_incident_ids"])


def test_operator_and_severity_freeze():
    assert len(OPERATORS) == 14
    assert h4.SEVERITIES == (0.0, 0.1, 0.3, 0.5)
    assert h4.STRUCTURAL_PLANNED == 2240


def test_unavailable_modality_is_structured_skip_without_inference():
    assert not h4.operator_applicable("business_event_drop", ("metric",))
    assert not h4.operator_applicable("random_span_drop", ("metric",))
    assert h4.operator_applicable("metric_delay", ("metric",))


def test_zero_severity_is_exact_noop_for_every_operator():
    observation, harness = smoke_observation(), ObservationDegradationHarness()
    for operator in OPERATORS:
        result = harness.apply(observation, operator, 0.0, seed=42)
        assert result.dataset.observation_fingerprint == observation.observation_fingerprint
        assert result.dataset.ground_truth_fingerprint == observation.ground_truth_fingerprint
        accounting = result.manifest.accounting
        assert accounting.affected_units == accounting.removed_event_count == 0
        assert accounting.added_event_count == accounting.modified_event_count == 0
        assert accounting.removed_topology_edge_count == accounting.added_topology_edge_count == 0


def test_degradation_deterministic_ground_truth_and_input_immutable():
    observation, harness = smoke_observation(), ObservationDegradationHarness()
    before = observation.fingerprint
    for operator in OPERATORS:
        left = harness.apply(observation, operator, 0.3, seed=42)
        right = harness.apply(observation, operator, 0.3, seed=42)
        assert left.manifest.to_json() == right.manifest.to_json()
        assert left.dataset.ground_truth_fingerprint == observation.ground_truth_fingerprint
    assert observation.fingerprint == before


def test_h4_fpr_delegates_to_candidate_level_benchmark_semantics():
    ranked = [(0.8, Vector("wrong", x=1.0)), (0.7, Vector("root", x=2.0)),
              (0.1, Vector("other", x=0.0))]
    metrics = evaluate_h4_ranking("synthetic", ranked, {"root"})
    assert metrics["top_1"] == 0.0
    assert metrics["fpr"] == 0.5
    assert metrics["fpr"] != 1.0  # not Top-1 error relabelled as FPR


def test_reliability_uses_actual_score_and_is_not_constant_zero():
    reliable = evaluate_h4_reliability(
        [(0.9, Vector("root", x=1.0)), (0.1, Vector("other", x=0.0))], ("x",))
    abstained = evaluate_h4_reliability(
        [(0.4, Vector("root", x=1.0)), (0.39, Vector("other", x=0.0))], ("x",))
    assert reliable.is_reliable and reliable.confidence == 0.9
    assert not abstained.is_reliable and abstained.confidence == 0.4


def test_evaluator_has_real_confidence_calibration_and_latency_wiring():
    source = inspect.getsource(ScientificH4Evaluator.__call__)
    assert "time.perf_counter()" in source
    assert '"abstentions": int(not reliability.is_reliable)' in source
    assert '"confidence_values": [float(ranked[0][0])]' in source
    assert '"outcomes": [bool(canonical["top_1"])]' in source
    assert 'processing_latency_ms": 0.0' not in source
    assert 'fpr": float(first != 1)' not in source


def test_pairing_rejects_missing_applicable_severity():
    rows = []
    for severity in h4.SEVERITIES:
        rows.append({"execution_status": "PLANNED_APPLICABLE", "incident_id": "i",
                     "operator": "metric_delay", "model_artifact_sha256": "a" * 64,
                     "seed": 42, "repetition": 0, "severity": severity})
    plan = {"runs": rows}
    assert h4._pairing(plan)["status"] == "PASS"
    assert h4._pairing({"runs": rows[:-1]})["status"] == "FAIL"


def test_preflight_has_zero_runtime_mutation_counters():
    payload = json.loads((ROOT / "artifacts/final-experiments-v3-h4-final-40/"
                          "preflight-audit.json").read_text())
    assert payload["execution_readiness"] == "PASS"
    assert [payload[key] for key in ("inference_calls", "training_calls", "tuning_calls",
                                     "fit_calls", "partial_fit_calls")] == [0, 0, 0, 0, 0]
    assert payload["h3_freeze"]["provider_calls"] == 0


def test_provider_is_explicitly_filtered_to_tranche_test_40():
    source = inspect.getsource(load_h4_inputs)
    assert 'selected_incidents = frozenset(tranche.get("test_incident_ids", ()))' in source
    assert "if capability.incident_id not in selected_incidents" in source
    assert "set(sources) != selected_incidents" in source


class _CountingLoader:
    def __init__(self, events):
        self._events = events
        self.calls = []

    def events(self, source):
        self.calls.append(source.incident_id)
        return self._events


def _lazy_observations(*incident_ids):
    observation = smoke_observation()
    loader = _CountingLoader(observation.events)
    sources = {incident_id: IncidentSource(
        incident_id, "fixture", "system-a", Path(f"{incident_id}.json"),
        hashlib.sha256(incident_id.encode()).hexdigest(), "payment-service",
        ("trace", "log", "metric", "business_event", "topology"),
    ) for incident_id in incident_ids}
    return FrozenH4ObservationMapping(loader, sources), loader


def test_frozen_h4_mapping_is_lazy_read_only_and_cache_bound_one():
    observations, loader = _lazy_observations("incident-smoke", "incident-second")
    assert loader.calls == []
    assert len(observations) == 2
    assert tuple(observations) == ("incident-smoke", "incident-second")
    assert "incident-smoke" in observations and "missing" not in observations
    assert loader.calls == []
    first = observations["incident-smoke"]
    assert loader.calls == ["incident-smoke"]
    assert observations["incident-smoke"] is first
    assert loader.calls == ["incident-smoke"]
    second = observations["incident-second"]
    assert loader.calls == ["incident-smoke", "incident-second"]
    assert second.ground_truth == {"incident-second": ("payment-service",)}
    assert observations.current_cached_incidents == ("incident-second",)
    assert observations.maximum_cached_incidents_seen == 1
    with pytest.raises(KeyError):
        observations["missing"]


class _CountingMapping(Mapping):
    def __init__(self, values):
        self.values, self.getitem_calls = values, 0

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        return iter(self.values)

    def __getitem__(self, key):
        self.getitem_calls += 1
        return self.values[key]


def test_h4_executor_constructor_preserves_lazy_mapping_and_dict_compatibility():
    counting = _CountingMapping({"incident-smoke": smoke_observation()})
    executor = H4ExperimentExecutor(counting, lambda run, observation: {})
    assert executor.observations is counting
    assert counting.getitem_calls == 0
    ordinary = {"incident-smoke": smoke_observation()}
    assert H4ExperimentExecutor(ordinary, lambda run, observation: {}).observations is ordinary


def test_lazy_observation_matches_eager_and_provenance_reuses_cached_base():
    run = next(run for run in ExperimentPlannerV3(smoke_config()).plan()
               if run.hypothesis == "H4" and run.degradation_severity == 0.0)
    observations, loader = _lazy_observations(run.incident_id)
    eager = ObservationDataset(
        smoke_observation().events, {}, {run.incident_id: ("payment-service",)})
    lazy = observations[run.incident_id]
    assert lazy.events == eager.events
    assert lazy.ground_truth == eager.ground_truth
    assert lazy.observation_fingerprint == eager.observation_fingerprint
    assert loader.calls == [run.incident_id]

    def evaluator(current_run, dataset):
        return {"top_1": 1.0, "top_3": 1.0, "mrr": 1.0, "fpr": 0.0,
                "confidence_values": (1.0,), "outcomes": (True,),
                "abstentions": 0, "reliability": True,
                "processing_latency_ms": 1.0,
                "predicted_ranking": ({"service_name": "payment-service", "score": 1.0},),
                "predicted_service": "payment-service",
                "ground_truth_service": "payment-service",
                "feature_hash": dataset.observation_fingerprint}

    lazy_result = _H4ProvenanceExecutor(
        observations, evaluator,
        feature_hasher=lambda current_run, dataset: dataset.observation_fingerprint,
    ).execute(run)
    eager_result = _H4ProvenanceExecutor(
        {run.incident_id: eager}, evaluator,
        feature_hasher=lambda current_run, dataset: dataset.observation_fingerprint,
    ).execute(run)
    assert loader.calls == [run.incident_id]
    assert lazy_result == eager_result


def _minimal_baseline_payload():
    return {"statistics": {}, "log_templates": {}, "training_incident_ids": [],
            "metric_selections": {}, "anomaly_policy_version": "test-v1",
            "schema_version": "rca-feature-baselines-v3"}


def _frozen_parent_fixture(tmp_path):
    payload = _minimal_baseline_payload()
    digest = h4.sha256_value(payload)
    reference = {
        "schema_version": "frozen-parent-baseline-reference-v1",
        "path": "snapshot.json", "baseline_sha256": digest,
        "parent_base_key": "base", "parent_split_sha256": "split",
        "parent_training_incident_ids_sha256": h4.sha256_value([]),
        "parent_training_incident_count": 0,
        "preprocessing_version": "scientific-input-preprocessing-v3",
    }
    provenance = {
        "official_parent_baseline_sha256": digest,
        "parent_base_key": "base", "parent_split_sha256": "split",
        "scientific_tranche_id": "tranche", "scientific_tranche_sha256": "t" * 64,
        "parent_training_incident_ids_sha256": h4.sha256_value([]),
        "parent_training_incident_count": 0, "baseline_binding_sha256": "",
    }
    splits = {"train": [], "validation": [], "test": []}
    provenance["baseline_binding_sha256"] = baseline_binding_sha256(provenance, splits)
    snapshot = {
        "status": "COMPLETED", "preprocessing_version":
        "scientific-input-preprocessing-v3", "baseline_sha256": digest,
        "base_key": "base", "split_sha256": "split", "training_incident_ids": [],
        "baseline_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
    }
    (tmp_path / "snapshot.json").write_text(json.dumps(snapshot))
    return {"baseline_binding_policy": "frozen-parent-train", "baseline_json": None,
            "baseline_sha256": digest, "baseline_reference": reference,
            "baseline_binding_provenance": provenance,
            "baseline_parent_training_incident_ids": [], "split_incident_ids": splits}


def test_split_train_exact_baseline_resolution_and_unknown_policy_fail_closed(tmp_path):
    payload = _minimal_baseline_payload()
    manifest = {"baseline_binding_policy": "split-train-exact",
                "baseline_json": json.dumps(payload),
                "baseline_sha256": h4.sha256_value(payload)}
    assert _resolve_scientific_baseline(manifest, tmp_path).training_incident_ids == ()
    manifest["baseline_binding_policy"] = "unknown"
    with pytest.raises(ValueError, match="policy"):
        _resolve_scientific_baseline(manifest, tmp_path)
    manifest["baseline_binding_policy"] = "split-train-exact"
    manifest["baseline_json"] = None
    with pytest.raises(ValueError, match="baseline_json"):
        _resolve_scientific_baseline(manifest, tmp_path)


@pytest.mark.parametrize("mutation", [
    "embedded", "missing_reference", "missing_snapshot", "reference_sha",
    "dataset_sha", "parent_base", "parent_split", "training_identity",
    "malformed_baseline",
])
def test_frozen_parent_baseline_resolution_fails_closed(tmp_path, mutation):
    manifest = _frozen_parent_fixture(tmp_path)
    if mutation == "embedded":
        manifest["baseline_json"] = "{}"
    elif mutation == "missing_reference":
        del manifest["baseline_reference"]
    elif mutation == "missing_snapshot":
        manifest["baseline_reference"]["path"] = "missing.json"
    elif mutation == "reference_sha":
        manifest["baseline_reference"]["baseline_sha256"] = "0" * 64
    elif mutation == "dataset_sha":
        manifest["baseline_sha256"] = "0" * 64
    elif mutation == "parent_base":
        manifest["baseline_reference"]["parent_base_key"] = "wrong"
    elif mutation == "parent_split":
        manifest["baseline_reference"]["parent_split_sha256"] = "wrong"
    elif mutation == "training_identity":
        manifest["baseline_reference"]["parent_training_incident_count"] = 1
    elif mutation == "malformed_baseline":
        snapshot = json.loads((tmp_path / "snapshot.json").read_text())
        snapshot["baseline_json"] = "not-json"
        (tmp_path / "snapshot.json").write_text(json.dumps(snapshot))
    with pytest.raises(ValueError):
        _resolve_scientific_baseline(manifest, tmp_path)


def test_actual_h4_ob_ss_tt_frozen_parent_baseline_and_feature_binding():
    catalog_payload = json.loads((ROOT / CONFIG["criteria"][
        "scientific_benchmark_catalog"]).read_text())
    artifact_payload = json.loads((ROOT / CONFIG["criteria"][
        "scientific_trained_artifact_index"]).read_text())
    catalogs, entries = {}, {}
    for item in catalog_payload["datasets"]:
        key = tuple(item[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed"))
        catalogs[key] = item
    for item in artifact_payload["artifacts"]:
        key = tuple(item[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed",
            "method"))
        entries[key] = item
    evaluator = ScientificH4Evaluator(entries, catalogs, ROOT)
    policies = {}
    for system in ("OB", "SS", "TT"):
        run = SimpleNamespace(source_dataset="rcaeval", source_system=system,
                              feature_view="FULL", protocol="within-dataset",
                              held_out=None, seed=42, method="xgboost-rca-v3")
        catalog = evaluator._catalog(run)
        manifest = json.loads((ROOT / catalog["path"]).read_text())["manifest"]
        policies[system] = manifest["baseline_binding_policy"]
        baseline, names, baseline_sha, feature_sha = evaluator._metadata_for(run)
        assert baseline.training_incident_ids
        assert baseline_sha == manifest["baseline_sha256"]
        assert feature_sha == manifest["feature_view_sha256"]
        assert names
    assert policies == {system: "frozen-parent-train" for system in ("OB", "SS", "TT")}
    assert len(evaluator._metadata) == evaluator.maximum_metadata_cache_entries_seen == 1


def test_h4_technical_canary_is_three_system_noncanonical_and_nonleaking():
    root = ROOT / "artifacts/final-experiments-v3-h4-final-40-canary"
    report = json.loads((root / "canary-report.json").read_text())
    assert report["status"] == "PASS"
    assert report["purpose"] == "TECHNICAL_EXECUTION_VALIDATION_ONLY"
    assert not report["used_for_model_selection"]
    assert not report["used_for_threshold_selection"]
    assert not report["used_for_hypothesis_interpretation"]
    assert report["operator"] == "metric_delay" and report["severity"] == 0.0
    assert {item["source_system"] for item in report["systems"]} == {"OB", "SS", "TT"}
    assert all(item["execution"] == "SUCCESS" for item in report["systems"])
    assert all(item["result_schema_validation"] == "PASS" for item in report["systems"])
    assert all(item["model_sha256"] ==
               "a10ede7c8bfd922e14765caaf8dc6bfbf7e18627a3a49b722895bb2240e9f889"
               for item in report["systems"])
    serialized = root.joinpath("canary-report.json").read_text()
    assert all(field not in serialized for field in (
        "predicted_service", "ground_truth_service", "top_1", "top_3", "mrr", "confidence"))
    canonical = ROOT / "artifacts/final-experiments-v3-h4-final-40/H4/final-h4-v3"
    assert not any(canonical.iterdir())


def test_model_identity_and_same_hash_across_severities():
    payload = json.loads((ROOT / "artifacts/final-experiments-v3-h4-final-40/"
                          "model-binding.json").read_text())
    assert payload["status"] == "PASS"
    assert len(payload["bindings"]) == 3
    assert len(payload["distinct_serialized_artifacts"]) == 1
    assert payload["same_artifact_across_severities"]


def test_missing_model_binding_fails_closed():
    caps = {"i": {"incident_id": "i", "source_dataset": "rcaeval",
                  "source_system": "missing", "available_modalities": ["metric"],
                  "manifest_sha256": "a" * 64}}
    with pytest.raises(ValueError, match="MODEL_BINDING_MISSING"):
        h4.build_execution_plan(["i"], caps, {})


def test_v2_freeze_is_12_of_12():
    result = verify_v2_freeze(ROOT)
    assert result["valid"] and (result["ok"], result["total"]) == (12, 12)
