from __future__ import annotations

import random

import pytest

from experiments.final_campaigns_v3.h1h2_test_extension import (
    MODEL_IDENTITY_FIELDS, STRICT_REPRODUCIBILITY_TOLERANCE, aggregate_observations,
    build_extension_manifest, build_spot_selection, compare_spot_records,
    compare_spot_records_detailed, target_decision, validate_heuristic_identity,
    validate_model_identity,
)


def metadata(count=240):
    return [{"incident_id": f"i-{index:03d}", "source_system": f"S{index % 3}",
             "integrity_status": "CLEAN" if index % 4 else "PARTIAL",
             "modalities": ["metric", "trace"] if index % 2 else ["metric"],
             "fault_type": f"f{index % 5}",
             "event_count_by_modality": {"metric": index + 1}}
            for index in range(count)]


def base():
    return {"train_incident_ids": ["train-a"], "validation_incident_ids": ["validation-a"],
            "test_incident_ids": [f"i-{index:03d}" for index in range(40)],
            "selection_sha256": "a" * 64, "parent_base_key": "b" * 64,
            "parent_split_key": "c" * 64}


def test_target_feasibility_cases():
    assert target_decision(200)["added_test_count"] == 160
    assert target_decision(200)["target_200_feasibility"] == "PASS"
    assert target_decision(147)["final_proposed_test_count"] == 147
    assert target_decision(147)["added_test_count"] == 107
    assert target_decision(147)["target_200_feasibility"] == "NOT_PASS_PARENT_TEST_LIMIT"
    with pytest.raises(ValueError, match="CANNOT_CONTAIN_BASE"):
        target_decision(39)


def test_extension_is_nested_and_deterministic_under_input_shuffle():
    rows = metadata()
    parent = [f"i-{index:03d}" for index in range(220)]
    first = build_extension_manifest(base=base(), parent_test_ids=parent, metadata=rows)
    random.Random(91).shuffle(rows)
    second = build_extension_manifest(base=base(), parent_test_ids=reversed(parent), metadata=rows)
    assert first == second
    locked, added, final = map(set, (first["locked_base_test_incident_ids"],
                                     first["added_test_incident_ids"], first["test_incident_ids"]))
    assert len(final) == 200 and len(added) == 160
    assert locked < final and locked.isdisjoint(added) and final - locked == added
    assert first["train_incident_ids"] == base()["train_incident_ids"]
    assert first["validation_incident_ids"] == base()["validation_incident_ids"]


def test_selector_rejects_outcome_fields_and_spot_is_stable():
    rows = metadata(40)
    one = build_spot_selection(base_test_ids=base()["test_incident_ids"], metadata=rows)
    random.Random(5).shuffle(rows)
    assert one == build_spot_selection(base_test_ids=base()["test_incident_ids"], metadata=rows)
    rows[0]["predicted_service"] = "forbidden"
    with pytest.raises(ValueError, match="OUTCOME_FIELD"):
        build_spot_selection(base_test_ids=base()["test_incident_ids"], metadata=rows)


@pytest.mark.parametrize("field", MODEL_IDENTITY_FIELDS)
def test_each_model_identity_tamper_fails(field):
    expected = {name: ({"x": 1} if name == "hyperparameters" else "a" * 64)
                for name in MODEL_IDENTITY_FIELDS}
    actual = dict(expected)
    actual[field] = {"x": 2} if field == "hyperparameters" else "b" * 64
    with pytest.raises(ValueError, match=field):
        validate_model_identity(expected, actual)


def test_spot_comparison_gate_and_timing_exclusion():
    expected = {"incident_id": "i", "method": "m", "predicted_service": "s",
                "runtime_seconds": 1.0}
    same_science = {**expected, "runtime_seconds": 99.0}
    assert compare_spot_records(expected, same_science)[0]
    assert not compare_spot_records(expected, {**same_science, "predicted_service": "x"})[0]


def test_strict_numeric_tolerance_boundaries_and_default():
    assert STRICT_REPRODUCIBILITY_TOLERANCE == 1e-15
    expected = {"reciprocal_rank": 0.5}
    assert compare_spot_records(expected, {"reciprocal_rank": 0.5 + 5e-16})[0]
    assert compare_spot_records(expected, {"reciprocal_rank": 0.5 + 1e-15})[0]
    assert not compare_spot_records(expected, {"reciprocal_rank": 0.5 + 2e-15})[0]
    _, _, stats = compare_spot_records_detailed(
        expected, {"reciprocal_rank": 0.5 + 5e-16}, tolerance=1e-15)
    assert stats["numeric_comparisons_checked"] == 1
    assert stats["numeric_comparisons_failed"] == 0


def test_heuristic_identity_tamper_fails():
    validate_heuristic_identity("a" * 64, "a" * 64)
    with pytest.raises(ValueError, match="HEURISTIC_IDENTITY"):
        validate_heuristic_identity("a" * 64, "b" * 64)


def test_frozen_spot_selection_identity_tamper_fails():
    from experiments.scripts.run_h1h2_expansion_gate_v3 import (
        FROZEN_SPOT_IDS, FROZEN_SPOT_SHA256, validate_frozen_spot_selection,
    )
    valid = {"selected_incident_ids": list(FROZEN_SPOT_IDS),
             "selection_sha256": FROZEN_SPOT_SHA256}
    validate_frozen_spot_selection(valid)
    changed_id = {**valid, "selected_incident_ids": [*FROZEN_SPOT_IDS[:-1], "changed"]}
    with pytest.raises(RuntimeError, match="SELECTION_IDENTITY"):
        validate_frozen_spot_selection(changed_id)
    with pytest.raises(RuntimeError, match="SELECTION_IDENTITY"):
        validate_frozen_spot_selection({**valid, "selection_sha256": "0" * 64})


def test_runner_behaviorally_binds_separate_expected_and_actual_identities():
    from experiments.scripts.run_h1h2_expansion_gate_v3 import bind_model_identity
    expected = {name: ({"x": 1} if name == "hyperparameters" else "a" * 64)
                for name in MODEL_IDENTITY_FIELDS}
    actual = {**expected, "hyperparameters": {"x": 1}}
    calls = []

    def spy(left, right):
        calls.append((left, right))
        validate_model_identity(left, right)

    bind_model_identity(expected, actual, spy)
    assert len(calls) == 1 and calls[0][0] is expected and calls[0][1] is actual
    with pytest.raises(RuntimeError, match="SELF_COMPARISON"):
        bind_model_identity(expected, expected, spy)
    tampered_actual = {**actual, "serialized_artifact_sha256": "b" * 64}
    with pytest.raises(ValueError, match="serialized_artifact_sha256"):
        bind_model_identity(expected, tampered_actual, spy)


def test_runtime_training_guard_blocks_training_entrypoint():
    import numpy as np
    import multimodal_v1.benchmark_v3.core as benchmark_core
    from experiments.scripts.run_h1h2_expansion_gate_v3 import (
        TrainingCallGuard, inference_only_scores,
    )

    class MaliciousModel:
        def predict_proba(self, _matrix):
            benchmark_core.train_and_evaluate(None, None, None)

    guard = TrainingCallGuard()
    with pytest.raises(RuntimeError, match="FORBIDDEN_TRAINING_ENTRYPOINT"):
        inference_only_scores(MaliciousModel(), np.zeros((1, 1)), guard)
    assert guard.counters["training_calls"] == 1


def test_aggregation_rejects_duplicate_run_identity():
    row = {"family": "H1-A", "incident_id": "i", "method": "m", "feature_view": "FULL",
           "protocol": "within-dataset", "seed": 42}
    with pytest.raises(ValueError, match="DUPLICATE"):
        aggregate_observations([row], [row])


def test_aggregation_allows_same_contract_in_distinct_families():
    row = {"incident_id": "i", "method": "xgboost-rca-v3", "feature_view": "FULL",
           "protocol": "within-dataset", "seed": 42}
    combined = aggregate_observations(
        [{**row, "family": "H1-A"}], [{**row, "family": "H2"}],
    )
    assert len(combined) == 2


def test_gate_runner_has_no_training_entrypoint():
    source = open("experiments/scripts/run_h1h2_expansion_gate_v3.py", encoding="utf-8").read()
    assert "benchmark_core.train_and_evaluate(" not in source
    assert ".fit(" not in source
    assert ".partial_fit(" not in source
    assert "tolerance=STRICT_REPRODUCIBILITY_TOLERANCE" in source
    assert "bind_model_identity(expected_identity, actual_identity, identity_validator)" in source


def test_final_runner_preserves_the_complete_frozen_view_matrix():
    from experiments.scripts.run_h1h2_final_147_v3 import FAMILY_CONTRACTS, REQUIRED
    assert ("xgboost-rca-v3", "GRAPH_BUSINESS") in FAMILY_CONTRACTS["H1-B"]
    assert ("xgboost-rca-v3", "METRIC_TRACE_LOG_BUSINESS") in FAMILY_CONTRACTS["H1-C"]
    assert REQUIRED["GRAPH_BUSINESS"] == {"business_event"}
    assert REQUIRED["METRIC_TRACE_LOG_BUSINESS"] == {"metric", "trace", "log", "business_event"}


def test_shards_are_complete_disjoint_and_guard_invalid_membership():
    from experiments.scripts.materialize_h1h2_extension_shard_v3 import (
        shard_incident_ids, validate_shard_partition,
    )
    extension = {"added_test_incident_ids": [f"i-{index:03d}" for index in range(107)]}
    audit = validate_shard_partition(extension, 2)
    assert audit["status"] == "PASS" and audit["sizes"] == [54, 53]
    assert set(shard_incident_ids(extension, shard=0, shards=2)).isdisjoint(
        shard_incident_ids(extension, shard=1, shards=2))
    with pytest.raises(ValueError, match="INVALID_SHARD"):
        shard_incident_ids(extension, shard=2, shards=2)


def test_final_metrics_delegate_to_authoritative_benchmark_metric_code():
    from experiments.scripts.run_h1h2_final_147_v3 import _metrics
    observations = [{"family": "H1-A", "method": "x", "feature_view": "FULL",
                     "execution_status": "SUCCESS", "incident_id": "i",
                     "ranked_services": [{"service_name": "root", "score": .9, "is_root_cause": True},
                                         {"service_name": "other", "score": .1, "is_root_cause": False}],
                     "confidence": .9}]
    metric = _metrics(observations)[0]
    assert metric["top1"] == 1.0 and metric["top3"] == 1.0 and metric["mrr"] == 1.0
    assert metric["fpr"] == 0.0


def test_final_executor_has_no_view_only_catalog_fallback():
    source = open("experiments/scripts/run_h1h2_final_147_v3.py", encoding="utf-8").read()
    assert "by_view.get" not in source
    assert "by_view_system.get((view, source_system))" in source


def test_resume_checkpoint_reuses_valid_and_rejects_tampered_binding(tmp_path):
    from experiments.final_campaigns_v3.h1h2_test_extension import (
        load_resume_checkpoint, resume_key, write_resume_checkpoint,
    )
    base = {"family": "H1-A", "incident_id": "a", "source_system": "OB",
            "method": "x", "feature_view": "FULL", "protocol": "within-dataset", "seed": 42,
            "expanded_evaluation_tranche_sha256": "e" * 64, "feature_schema_sha256": "f" * 64,
            "trained_model_artifact_hash": "m" * 64, "method_implementation_hash": None,
            "model_specification_hash": "s" * 64, "training_split_sha256": "t" * 64,
            "validation_split_sha256": "v" * 64, "expanded_test_split_sha256": "z" * 64,
            "execution_status": "SUCCESS"}
    path = tmp_path / f"{resume_key(base)}.json"; write_resume_checkpoint(path, base)
    assert load_resume_checkpoint(path, base) is not None
    tampered = {**base, "feature_schema_sha256": "q" * 64}
    assert load_resume_checkpoint(path, tampered) is None
