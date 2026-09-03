from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

from experiments.final_campaigns_v3 import h4_final_40 as h4
from experiments.final_campaigns_v3.providers import (
    ScientificH4Evaluator,
    evaluate_h4_ranking,
    evaluate_h4_reliability,
    load_h4_inputs,
)
from experiments.multimodal_v3.core import verify_v2_freeze
from experiments.multimodal_v3.fixtures import smoke_observation
from multimodal_v1.degradation_v3 import OPERATORS, ObservationDegradationHarness


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
    assert "set(observations) != selected_incidents" in source


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
