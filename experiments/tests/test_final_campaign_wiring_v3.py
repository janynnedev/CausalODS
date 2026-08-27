from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import experiments.final_campaigns_v3.core as final
from experiments.multimodal_v3 import (
    CampaignStoreV3, CampaignValidatorV3, DatasetCapabilityV3,
)
from experiments.multimodal_v3.fixtures import build_smoke_orchestrator, smoke_config
from multimodal_v1.degradation_v3 import OPERATORS


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/configs/final-v3"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _campaign(family: str, **changes):
    payload = {
        "schema_version": "final-campaign-config-v3", "campaign_id": f"test-{family.lower()}",
        "family": family, "protocols": ["within-dataset"], "seeds": [42],
        "repetitions": 1, "execution_mode": "offline",
        "model_version": "xgboost-rca-v3-scientific", "model_hash": "a" * 64,
        "test_fraction": .2, "validation_fraction": .2, "criteria": {},
    }
    payload.update(changes)
    return payload


def _caps(count=40, modalities=("business_event", "log", "metric", "trace")):
    return tuple(DatasetCapabilityV3(
        "fixture", "system-a" if index % 2 else "system-b", f"incident-{index:03d}",
        modalities, hashlib.sha256(f"manifest-{index}".encode()).hexdigest(),
        fault_type="error", scenario=f"scenario-{index % 2}",
    ) for index in range(count))


def _fixture_registry(tmp_path: Path, *, usable=4, identity="PASS"):
    root = tmp_path / "dataset"
    root.mkdir()
    incidents = [{"incident_id": f"i-{index}", "usable_for_benchmark": index < usable,
                  "integrity_status": "CLEAN"} for index in range(4)]
    capabilities = [{
        "source_dataset": "fixture", "source_system": "system-a",
        "incident_id": f"i-{index}", "available_modalities": ["metric"],
        "manifest_sha256": hashlib.sha256(f"m-{index}".encode()).hexdigest(),
        "usable_for_benchmark": index < usable,
    } for index in range(4)]
    (root / "INDEX.json").write_text(json.dumps({"count": 4, "incidents": incidents}))
    (root / "capabilities.json").write_text(json.dumps({"count": 4, "capabilities": capabilities}))
    (root / "materialization-report.json").write_text(json.dumps({"valid": 4, "usable": usable,
                                                                    "identity": identity}))
    files = [root / "INDEX.json", root / "capabilities.json", root / "materialization-report.json"]
    (root / "SHA256SUMS").write_text("".join(
        f"{_sha(path)}  {path.name}\n" for path in files))
    dataset = final.FrozenDataset("fixture", "fixture", root, 4, 4,
                                  root / "SHA256SUMS", _sha(root / "SHA256SUMS"),
                                  root / "INDEX.json", root / "capabilities.json",
                                  root / "materialization-report.json")
    return final.FrozenDatasetRegistry(ROOT, (dataset,)), root


def _real_registry():
    return final.FrozenDatasetRegistry.load(CONFIG / "datasets.json", ROOT)


def test_01_registry_accepts_frozen_rcaeval():
    registry = _real_registry()
    assert registry.datasets["rcaeval"].expected_incidents == 735
    assert registry.datasets["rcaeval"].frozen


def test_02_registry_accepts_frozen_local_v3():
    registry = _real_registry()
    assert registry.datasets["causalods-local-v3"].expected_incidents == 500
    assert registry.datasets["causalods-local-v3"].frozen


def test_03_preflight_rejects_usable_mismatch(tmp_path):
    registry, _ = _fixture_registry(tmp_path, usable=3)
    report = registry.preflight()
    assert not report["valid"] and "usable_index_mismatch" in report["datasets"][0]["issues"]


def test_04_preflight_rejects_local_identity_fail(tmp_path):
    registry, root = _fixture_registry(tmp_path)
    (root / "campaign-manifest.json").write_text(json.dumps(
        {"target_runs": 500, "materialized_runs": 500}))
    (root / "audits").mkdir()
    (root / "audits/final-audit.json").write_text(json.dumps({
        "identity_audit": "FAIL", "ground_truth_audit": "PASS",
        "fault_controller_binding": "PASS", "full_modality_audit": "PASS",
        "metric_pipeline_provenance": "PASS", "telemetry_transport_audit": "PASS",
        "anti_leakage_audit": "PASS", "service_state_reset_audit": "PASS",
        "run_isolation_audit": {"status": "PASS"},
        "resume_audit": {"resume_validation": {"attempted": 500, "completed": 500,
                                                   "resumed": 500, "new_executions": 0}},
    }))
    dataset = replace(next(iter(registry.datasets.values())), kind="causalods-local-v3",
                      expected_incidents=500, expected_usable=500)
    report = final.FrozenDatasetRegistry(tmp_path, (dataset,)).preflight(verify_all_hashes=False)
    assert not report["valid"] and "local_gate_failed:identity" in report["datasets"][0]["issues"]


def test_05_preflight_rejects_checksum_mismatch(tmp_path):
    registry, root = _fixture_registry(tmp_path)
    (root / "INDEX.json").write_text("{}")
    report = registry.preflight()
    assert not report["valid"]
    assert any("checksum_mismatch" in issue for issue in report["datasets"][0]["issues"])


def test_06_h1a_exact_methods():
    _, runs = final.build_plan(_campaign("H1-A"), _caps())
    assert {run.method for run in runs} == set(final.H1A_METHODS)


def test_07_h1b_exact_views():
    _, runs = final.build_plan(_campaign("H1-B"), _caps())
    assert {run.feature_view for run in runs} == set(final.H1B_VIEWS)


def test_08_h1c_exact_views():
    _, runs = final.build_plan(_campaign("H1-C"), _caps())
    assert {run.feature_view for run in runs} == set(final.H1C_VIEWS)


def test_09_h2_exact_methods():
    _, runs = final.build_plan(_campaign("H2"), _caps())
    assert {run.method for run in runs} == set(final.H2_METHODS)


def test_10_h4_exact_severities_and_operators():
    _, runs = final.build_plan(_campaign("H4"), _caps())
    assert {run.degradation_severity for run in runs} == set(final.H4_SEVERITIES)
    assert {run.degradation_operator for run in runs} == set(OPERATORS)


def test_11_h4_model_hash_invariant():
    _, runs = final.build_plan(_campaign("H4"), _caps())
    assert final.assert_h4_no_retraining(runs)


def test_12_missing_modality_is_structured_skip():
    _, runs = final.build_plan(_campaign("H1-C"), _caps(modalities=("metric",)))
    trace = next(run for run in runs if run.feature_view == "TRACE_ONLY")
    assert not trace.eligibility and trace.skip_reason == "missing_modalities:trace"


def test_13_missing_modality_is_never_synthesized():
    matrix = final.build_capability_matrix(_caps(modalities=("metric",)))
    row = matrix["incidents"][0]
    assert not row["available"]["business_event"]
    assert not row["applicable_h1c_views"]["METRIC_TRACE_LOG_BUSINESS"]


def test_14_ground_truth_is_rejected_from_scientific_input():
    with pytest.raises(ValueError, match="leakage"):
        final.ScientificInput({"ground_truth_service": "payment"}, {}, {})
    final.ScientificInput({"latency_ms": 2}, {"metric": True}, {"dataset": "fixture"})


def test_15_deterministic_run_id_stable():
    first = final.build_plan(_campaign("H1-A"), _caps())[1]
    second = final.build_plan(_campaign("H1-A"), tuple(reversed(_caps())))[1]
    assert [run.run_id for run in first] == [run.run_id for run in second]


def test_16_material_config_change_changes_run_id():
    first = final.build_plan(_campaign("H1-A", seeds=[42]), _caps())[1]
    second = final.build_plan(_campaign("H1-A", seeds=[43]), _caps())[1]
    assert {run.run_id for run in first}.isdisjoint(run.run_id for run in second)


def test_17_resume_accepts_identical_hashes(tmp_path):
    path = tmp_path / "result.json"
    payload = {"execution_status": "SUCCESS", "run_id": "run", "configuration_hash": "c",
               "dataset_hash": "d", "method_hash": "m"}
    path.write_text(json.dumps(payload))
    assert final.reusable_result(path, run_id="run", configuration_hash="c",
                                 dataset_hash="d", model_hash="m", artifact_sha256=_sha(path))


def test_18_corrupt_result_is_not_resumed(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"execution_status": "SUCCESS", "run_id": "run",
                                "configuration_hash": "c", "dataset_hash": "d",
                                "method_hash": "m"}))
    expected = _sha(path)
    path.write_text(path.read_text() + " ")
    assert not final.reusable_result(path, run_id="run", configuration_hash="c",
                                     dataset_hash="d", model_hash="m", artifact_sha256=expected)


def test_18b_selective_resume_reexecutes_only_corrupt_run(tmp_path):
    first = build_smoke_orchestrator(tmp_path, ROOT, "selective-resume")
    initial = first.run()
    target = next(path for path in first.store.runs_path.glob("*.json")
                  if path.name != "index.json")
    target.write_text(target.read_text() + " ")
    audit = final.prepare_selective_resume(first.store, first.planner.plan())
    assert audit == {"reused": 25, "reexecute": 1, "quarantined": 1}
    resumed = build_smoke_orchestrator(tmp_path, ROOT, "selective-resume").run(resume=True)
    assert len(initial) == len(resumed) == 26
    assert len(list((first.store.path / "recovery").rglob("*.json"))) == 1


def test_19_dirty_git_blocks_final(monkeypatch):
    monkeypatch.setattr(final, "git_worktree_status",
                        lambda root: {"git_commit": "a" * 40, "dirty": True, "dirty_entries": 1})
    with pytest.raises(RuntimeError, match="GIT_WORKTREE_CLEAN_REQUIRED"):
        final.require_clean_final(ROOT, mode="final")


def test_20_dry_run_does_not_call_executor(tmp_path, monkeypatch):
    registry, _ = _fixture_registry(tmp_path)
    config = tmp_path / "h1a.json"
    config.write_text(json.dumps(_campaign("H1-A")))
    monkeypatch.setattr("experiments.causalods_campaign_bindings.build_executors",
                        lambda **kwargs: pytest.fail("executor called"))
    assert final.dry_run(registry, (config,), tmp_path / "out")["valid"]


def test_21_dry_run_does_not_call_llm(tmp_path, monkeypatch):
    registry, _ = _fixture_registry(tmp_path)
    config = tmp_path / "h1a.json"
    config.write_text(json.dumps(_campaign("H1-A")))
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.EnvironmentOpenAICompatibleClient.generate",
                        lambda *args, **kwargs: pytest.fail("LLM called"))
    final.dry_run(registry, (config,), tmp_path / "out")


def test_22_h3_three_modes_share_same_run_factors():
    _, runs = final.build_plan(_campaign("H3", sample_size=40), _caps(250))
    groups = {}
    for run in runs:
        groups.setdefault((run.incident_id, run.protocol, run.seed), []).append(run)
    assert len(groups) == 40
    assert all({item.explanation_mode for item in items} == set(final.H3_MODES)
               and len({item.model_hash for item in items}) == 1 for items in groups.values())


def test_23_blind_ids_do_not_reveal_mode():
    _, runs = final.build_plan(_campaign("H3", sample_size=40), _caps(250))
    blind = final.deterministic_blind_map(runs, seed=42)
    assert all(not any(mode in item["blind_id"] for mode in final.H3_MODES)
               for item in blind["private_map"])
    assert all(set(item) == {"blind_id"} for item in blind["blind_package_index"])


def test_24_h3_sample_is_deterministic():
    first = final.build_plan(_campaign("H3", sample_size=40), _caps(50))[1]
    second = final.build_plan(_campaign("H3", sample_size=40), tuple(reversed(_caps(50))))[1]
    assert {run.incident_id for run in first} == {run.incident_id for run in second}


def test_25_pairing_rejects_different_method_sets():
    runs = final.build_plan(_campaign("H1-A"), _caps())[1]
    reduced = tuple(run for run in runs if run.method != final.H1A_METHODS[-1])
    assert not final.audit_pairing({"H1-A": reduced})["valid"]


def test_26_pairing_accepts_valid_pair():
    runs = final.build_plan(_campaign("H1-A"), _caps())[1]
    assert final.audit_pairing({"H1-A": runs})["valid"]


def test_27_result_schema_supports_top1_and_mrr():
    value = final.ScientificEvaluation("payment", "payment", ("payment", "order"), True, 1.0)
    assert value.top1_correct and value.reciprocal_rank == 1.0


def test_28_failure_ledger_distinguishes_skip_from_failure(tmp_path):
    run = final.build_plan(_campaign("H1-A"), _caps())[1][0]
    ledger = final.FailureLedger(tmp_path / "failures.jsonl")
    ledger.append(run=run, state=final.ResultState.SKIP_NOT_APPLICABLE, stage="planning",
                  error_type="missing_modality", error_message="not applicable", retryable=False)
    ledger.append(run=run, state=final.ResultState.FAILED_EXECUTION, stage="model",
                  error_type="RuntimeError", error_message="failed", retryable=True)
    states = [json.loads(line)["state"] for line in ledger.path.read_text().splitlines()]
    assert states == ["SKIP_NOT_APPLICABLE", "FAILED_EXECUTION"]


def test_29_rcaeval_matrix_never_fabricates_business_event():
    caps = tuple(item for item in _real_registry().capabilities() if item.source_dataset == "rcaeval")
    matrix = final.build_capability_matrix(caps)
    assert all(not row["available"]["business_event"] for row in matrix["incidents"])


def test_30_local_v3_supports_full_view():
    caps = tuple(item for item in _real_registry().capabilities()
                 if item.source_dataset == "causalods-local-v3")
    matrix = final.build_capability_matrix(caps)
    assert len(caps) == 500
    assert all(row["applicable_h1c_views"]["METRIC_TRACE_LOG_BUSINESS"]
               for row in matrix["incidents"])


def test_31_v2_freeze_is_12_of_12():
    from experiments.multimodal_v3.core import verify_v2_freeze
    report = verify_v2_freeze(ROOT)
    assert report["valid"] and report["ok"] == report["total"] == 12


def test_32_v2_has_no_multimodal_imports():
    assert final.v2_to_v3_import_count(ROOT) == 0


def test_fixture_integration_plan_execute_persist_resume_and_pair(tmp_path):
    orchestrator = build_smoke_orchestrator(tmp_path, ROOT, "final-wiring-fixture")
    results = orchestrator.run()
    resumed = build_smoke_orchestrator(tmp_path, ROOT, "final-wiring-fixture").run(resume=True)
    validation = CampaignValidatorV3().validate(
        orchestrator.planner.plan(), results, store=orchestrator.store, repository_root=ROOT)
    assert validation["complete"] and len(results) == len(resumed)
    h1 = tuple(run for run in orchestrator.planner.plan() if run.experiment_family == "H1-A")
    assert final.audit_pairing({"H1-A": h1})["valid"]
