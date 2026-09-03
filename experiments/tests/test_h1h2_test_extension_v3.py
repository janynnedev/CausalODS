from __future__ import annotations

import ast
import hashlib
import json
import random
from types import SimpleNamespace

import numpy as np
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
            "execution_status": "SUCCESS", "predicted_service": "root",
            "ranked_services": [{"service_name": "root", "score": 1.0}],
            "top1_correct": True, "top3_correct": True, "reciprocal_rank": 1.0,
            "confidence": 1.0, "model_retrained": False, "model_retuned": False}
    path = tmp_path / f"{resume_key(base)}.json"; write_resume_checkpoint(path, base)
    assert load_resume_checkpoint(path, base) is not None
    tampered = {**base, "feature_schema_sha256": "q" * 64}
    assert load_resume_checkpoint(path, tampered) is None


def _executor_resume_fixture(tmp_path, monkeypatch):
    import experiments.scripts.run_h1h2_final_147_v3 as runner

    incidents = ("a", "b", "c")
    rows = [{"family": "H1-A", "incident_id": incident, "source_system": "OB",
             "method": "temporal-baseline", "feature_view": "FULL",
             "protocol": "within-dataset", "seed": 42, "applicability": "APPLICABLE",
             "extension_tranche_sha256": "e" * 64} for incident in incidents]
    plan = {"runs": rows, "counts": {"H1-A": {
        "planned": 3, "applicable": 3, "expected_structured_skips": 0}}}
    extension = {"selection_sha256": "e" * 64,
                 "expanded_test_incident_ids_sha256": "z" * 64,
                 "base_tranche_selection_sha256": "b" * 64,
                 "base_test_incident_ids_sha256": "o" * 64,
                 "tranche_id": "extension-147"}
    implementation_hash = hashlib.sha256(
        b"benchmark-v3:temporal-baseline:HeuristicEstimator").hexdigest()
    contract = {"method": "temporal-baseline", "contract_key": "heuristic-contract",
                "expected_method_implementation_hash": implementation_hash,
                "identity_validation": "PASS"}
    model_audit = {"learned_contracts": [], "heuristic_contracts": [contract]}
    entry = {"path": "synthetic.json", "source_system": "OB", "protocol": "within-dataset",
             "seed": 42, "held_out": None, "feature_view_sha256": "f" * 64,
             "train_incident_ids_sha256": "t" * 64,
             "validation_incident_ids_sha256": "v" * 64}

    class Dataset:
        feature_names = ("feature",)

        def select(self, split):
            assert split == "test"
            return (np.arange(3, dtype=float).reshape(3, 1), None,
                    tuple(SimpleNamespace(incident_id=item) for item in incidents))

    inference_batches = []

    def inference(_model, matrix, _guard):
        inference_batches.append(tuple(int(value) for value in matrix[:, 0]))
        return np.asarray(matrix[:, 0], dtype=float)

    def evaluate(_scores, scoped_rows, **_kwargs):
        return {"incident_predictions": [
            {"incident_id": item.incident_id, "reciprocal_rank": 1.0,
             "ranking": [{"service_name": "root", "score": 1.0,
                          "is_root_cause": True}]}
            for item in scoped_rows]}

    monkeypatch.setattr(runner, "FAMILY_CONTRACTS", {
        "H1-A": [("temporal-baseline", "FULL")]})
    monkeypatch.setattr(runner, "_catalog", lambda: {("FULL", "OB"): entry})
    monkeypatch.setattr(runner, "_capabilities", lambda: {
        item: {"source_system": "OB"} for item in incidents})
    monkeypatch.setattr(runner.BenchmarkDataset, "from_json", lambda _path: Dataset())
    monkeypatch.setattr(runner, "HeuristicEstimator", lambda *_args: object())
    monkeypatch.setattr(runner, "inference_only_scores", inference)
    monkeypatch.setattr(runner, "evaluate_scores", evaluate)
    checkpoint_root = tmp_path / "checkpoints"
    audit_path = tmp_path / "resume-audit.json"

    def expected(incident):
        row = next(item for item in rows if item["incident_id"] == incident)
        return runner._resume_expected_observation(row, extension, entry, contract)

    def checkpoint(incident, *, tamper=False):
        value = {**expected(incident), "execution_status": "SUCCESS",
                 "result_origin": "test_extension_147", "predicted_service": "root",
                 "ranked_services": [{"service_name": "root", "score": 1.0}],
                 "top1_correct": True, "top3_correct": True,
                 "reciprocal_rank": 1.0, "confidence": 1.0,
                 "model_retrained": False, "model_retuned": False}
        if tamper:
            value["feature_schema_sha256"] = "q" * 64
        path = checkpoint_root / f"{runner.resume_key(expected(incident))}.json"
        runner.write_resume_checkpoint(path, value)
        return path

    def execute():
        return runner.execute(extension, plan, model_audit, resume=True,
                              checkpoint_root=checkpoint_root,
                              resume_audit_path=audit_path)

    return SimpleNamespace(runner=runner, execute=execute, checkpoint=checkpoint,
                           expected=expected,
                           extension=extension, model_audit=model_audit,
                           checkpoint_root=checkpoint_root, audit_path=audit_path,
                           inference_batches=inference_batches)


def test_executor_selective_resume_infers_only_missing_observation(tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    fixture.checkpoint("a"); fixture.checkpoint("b")
    observations, reuse = fixture.execute()
    assert fixture.inference_batches == [(2,)]
    assert {item["incident_id"] for item in observations} == {"a", "b", "c"}
    assert reuse["selective_resume"]["valid_success_reused"] == 2
    assert reuse["selective_resume"]["missing_executed"] == 1


def test_executor_quarantines_tampered_binding_and_reexecutes_only_it(tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    invalid = fixture.checkpoint("a", tamper=True)
    fixture.checkpoint("b"); fixture.checkpoint("c")
    observations, reuse = fixture.execute()
    assert fixture.inference_batches == [(0,)]
    assert len(observations) == 3
    assert reuse["selective_resume"]["invalid_quarantined"] == 1
    assert fixture.runner.load_resume_checkpoint(invalid, fixture.expected("a")) is not None
    assert len(list((fixture.checkpoint_root / "quarantine").rglob("*.json"))) == 1


def test_executor_all_complete_resume_performs_zero_inference(tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    for incident in ("a", "b", "c"):
        fixture.checkpoint(incident)
    observations, reuse = fixture.execute()
    assert fixture.inference_batches == []
    assert len(observations) == 3
    assert reuse["selective_resume"]["status"] == "PASS"


def test_executor_interruption_preserves_written_success_for_next_resume(tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    original = fixture.runner.write_resume_checkpoint
    writes = 0

    def interrupt_after_first(path, observation):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("synthetic interruption")
        original(path, observation)

    monkeypatch.setattr(fixture.runner, "write_resume_checkpoint", interrupt_after_first)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        fixture.execute()
    assert fixture.inference_batches == [(0, 1, 2)]
    interrupted_audit = json.loads(fixture.audit_path.read_text())
    assert interrupted_audit["inference_observations_executed"] == 3
    assert interrupted_audit["observations_remaining"] == 2
    assert interrupted_audit["status"] == "FAIL"
    monkeypatch.setattr(fixture.runner, "write_resume_checkpoint", original)
    observations, reuse = fixture.execute()
    assert fixture.inference_batches == [(0, 1, 2), (1, 2)]
    assert len(observations) == 3
    assert reuse["selective_resume"]["valid_success_reused"] == 1
    assert reuse["selective_resume"]["status"] == "PASS"


def test_executor_reconstructs_structured_skip_without_catalog_or_inference(
        tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(fixture.runner, "FAMILY_CONTRACTS", {
        "H1-A": [("temporal-baseline", "GRAPH_BUSINESS")]})
    monkeypatch.setattr(fixture.runner, "_catalog", lambda: {})
    monkeypatch.setattr(fixture.runner, "_capabilities", lambda: {
        "a": {"source_system": "OB", "available_modalities": ["metric", "trace"]}})
    row = {"family": "H1-A", "incident_id": "a", "source_system": "OB",
           "method": "temporal-baseline", "feature_view": "GRAPH_BUSINESS",
           "protocol": "within-dataset", "seed": 42,
           "applicability": "SKIP_NOT_APPLICABLE", "extension_tranche_sha256": "e" * 64}
    plan = {"runs": [row], "counts": {"H1-A": {
        "planned": 1, "applicable": 0, "expected_structured_skips": 1}}}
    observations, reuse = fixture.runner.execute(
        fixture.extension, plan, fixture.model_audit, resume=True,
        checkpoint_root=fixture.checkpoint_root, resume_audit_path=fixture.audit_path)
    assert fixture.inference_batches == []
    assert observations[0]["execution_status"] == "SKIP_NOT_APPLICABLE"
    assert reuse["selective_resume"]["valid_skips_reused_or_reconstructed"] == 1
    assert fixture.runner._contract_compatibility_audit(
        observations, fixture.extension, fixture.model_audit)["status"] == "PASS"


def test_contract_audit_rejects_false_skip_for_applicable_capability(tmp_path, monkeypatch):
    fixture = _executor_resume_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(fixture.runner, "_catalog", lambda: {})
    monkeypatch.setattr(fixture.runner, "_capabilities", lambda: {
        "a": {"source_system": "OB", "available_modalities": ["business_event"]}})
    contract = fixture.model_audit["heuristic_contracts"][0]
    row = {"family": "H1-A", "incident_id": "a", "source_system": "OB",
           "method": "temporal-baseline", "feature_view": "GRAPH_BUSINESS",
           "protocol": "within-dataset", "seed": 42,
           "applicability": "SKIP_NOT_APPLICABLE", "extension_tranche_sha256": "e" * 64}
    observation = {**fixture.runner._expected_skip_observation(
        row, fixture.extension, contract), "execution_status": "SKIP_NOT_APPLICABLE"}
    audit = fixture.runner._contract_compatibility_audit(
        [observation], fixture.extension, fixture.model_audit)
    assert audit["status"] == "FAIL"
    assert audit["mismatches"][0]["reason"] == "false_structured_skip"


def test_final_campaign_not_pass_returns_nonzero_process_outcome():
    from experiments.scripts.run_h1h2_final_147_v3 import _final_campaign_exit_code
    assert _final_campaign_exit_code(True) == 0
    assert _final_campaign_exit_code(False) != 0


def test_real_learned_structured_skip_needs_no_contract_dataset_model_or_inference(
        tmp_path, monkeypatch):
    import experiments.scripts.run_h1h2_final_147_v3 as runner

    row = {"family": "H1-B", "incident_id": "incident-ob", "source_system": "OB",
           "method": "xgboost-rca-v3", "feature_view": "GRAPH_BUSINESS",
           "protocol": "within-dataset", "seed": 42,
           "applicability": "SKIP_NOT_APPLICABLE", "extension_tranche_sha256": "e" * 64}
    plan = {"runs": [row], "counts": {"H1-B": {
        "planned": 1, "applicable": 0, "expected_structured_skips": 1}}}
    extension = {"selection_sha256": "e" * 64,
                 "expanded_test_incident_ids_sha256": "z" * 64}
    model_audit = {"learned_contracts": [], "heuristic_contracts": []}
    calls = {"dataset": 0, "artifact": 0, "model": 0, "inference": 0}

    def forbidden(kind):
        def call(*_args, **_kwargs):
            calls[kind] += 1
            raise AssertionError(f"structured skip called {kind}")
        return call

    monkeypatch.setattr(runner, "FAMILY_CONTRACTS", {
        "H1-B": [("xgboost-rca-v3", "GRAPH_BUSINESS")]})
    monkeypatch.setattr(runner, "_catalog", lambda: {})
    monkeypatch.setattr(runner, "_capabilities", lambda: {"incident-ob": {
        "source_system": "OB", "available_modalities": ["metric", "trace", "log"]}})
    monkeypatch.setattr(runner.BenchmarkDataset, "from_json", forbidden("dataset"))
    monkeypatch.setattr(runner, "load_verified_artifact", forbidden("artifact"))
    monkeypatch.setattr(runner.joblib, "load", forbidden("model"))
    monkeypatch.setattr(runner, "inference_only_scores", forbidden("inference"))
    observations, reuse = runner.execute(
        extension, plan, model_audit, resume=True,
        checkpoint_root=tmp_path / "checkpoints",
        resume_audit_path=tmp_path / "resume-audit.json")
    assert calls == {"dataset": 0, "artifact": 0, "model": 0, "inference": 0}
    assert observations[0]["execution_status"] == "SKIP_NOT_APPLICABLE"
    for field in ("feature_schema_sha256", "trained_model_artifact_hash",
                  "method_implementation_hash", "training_split_sha256",
                  "validation_split_sha256"):
        assert observations[0][field] is None
    assert reuse["selective_resume"]["status"] == "PASS"
    assert runner._contract_compatibility_audit(
        observations, extension, model_audit)["status"] == "PASS"


def test_preflight_and_execute_fail_closed_for_missing_applicable_learned_contract(
        tmp_path, monkeypatch):
    import experiments.scripts.run_h1h2_final_147_v3 as runner

    row = {"family": "H1-B", "incident_id": "i", "source_system": "OB",
           "method": "xgboost-rca-v3", "feature_view": "GRAPH",
           "protocol": "within-dataset", "seed": 42, "applicability": "APPLICABLE",
           "extension_tranche_sha256": "e" * 64}
    plan = {"runs": [row], "counts": {"H1-B": {
        "planned": 1, "applicable": 1, "expected_structured_skips": 0}}}
    extension = {"added_test_incident_ids": ["i"], "locked_base_test_incident_ids": [],
                 "selection_sha256": "e" * 64,
                 "expanded_test_incident_ids_sha256": "z" * 64}
    model_audit = {"learned_contracts": [], "heuristic_contracts": []}
    entry = {"path": "unused.json", "source_system": "OB", "protocol": "within-dataset",
             "seed": 42, "held_out": None}
    monkeypatch.setattr(runner, "FAMILY_CONTRACTS", {
        "H1-B": [("xgboost-rca-v3", "GRAPH")]})
    monkeypatch.setattr(runner, "build_plan", lambda _extension: plan)
    monkeypatch.setattr(runner, "reconstruct_parent", lambda: {"test": ("i",)})
    monkeypatch.setattr(runner, "_catalog", lambda: {("GRAPH", "OB"): entry})
    monkeypatch.setattr(runner, "_capabilities", lambda: {
        "i": {"source_system": "OB", "available_modalities": ["metric", "trace"]}})
    monkeypatch.setattr(runner, "_historical_snapshot", lambda: {
        "H1-A": {"bound_files": {"result.json": "a" * 64}}})
    monkeypatch.setattr(runner.BenchmarkDataset, "from_json", lambda _path: SimpleNamespace(
        rows=[SimpleNamespace(incident_id="i", split="test")]))
    preflight = runner.preflight(
        extension, model_audit,
        v2={"ok": 12, "total": 12, "valid": True},
        h3={"status": "PASS"})
    assert preflight["model_identity"]["status"] == "FAIL"
    assert preflight["model_identity"]["missing_applicable_learned_contracts"] == [
        ["H1-B", "xgboost-rca-v3", "GRAPH"]]
    assert preflight["execution_readiness"] == "FAIL"
    with pytest.raises(RuntimeError, match="H1_H2_APPLICABLE_LEARNED_CONTRACT_MISSING"):
        runner.execute(extension, plan, model_audit, resume=True,
                       checkpoint_root=tmp_path / "checkpoints",
                       resume_audit_path=tmp_path / "resume-audit.json")


def test_resume_checkpoint_requires_every_identity_field(tmp_path):
    from experiments.final_campaigns_v3.h1h2_test_extension import write_resume_checkpoint
    with pytest.raises(ValueError, match="RESUME_IDENTITY_FIELDS_MISSING"):
        write_resume_checkpoint(tmp_path / "incomplete.json", {
            "family": "H1-A", "execution_status": "SUCCESS"})


def test_final_scientific_gates_are_not_unconditional_pass_literals():
    source = open("experiments/scripts/run_h1h2_final_147_v3.py", encoding="utf-8").read()
    gates = {
        "H1_H2_PARENT_TEST_COMPLEMENT_AUDIT",
        "H1_H2_EXTENSION_INPUT_MATERIALIZATION",
        "H1_H2_EXTENSION_MODEL_IDENTITY_AUDIT",
        "H1_H2_107_EXTENSION_EXECUTION",
        "H1_H2_FINAL_147_COVERAGE_AUDIT",
        "H1_H2_FINAL_AGGREGATION_DUPLICATE_AUDIT",
        "H1_H2_FINAL_CONTRACT_COMPATIBILITY_AUDIT",
        "H1_H2_FINAL_147_CAMPAIGN",
    }
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value in gates
                    and isinstance(value, ast.Constant)):
                assert value.value != "PASS"
