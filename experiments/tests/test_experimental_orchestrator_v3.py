from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from experiments.multimodal_v3 import (
    CampaignStoreV3, CampaignValidatorV3, DatasetCapabilityV3,
    ExperimentOrchestratorV3, ExperimentPlannerV3, ExperimentStatisticsV3,
    ExperimentReportBuilderV3, H3ExperimentExecutor, H4ExperimentExecutor,
    RunStatusV3, derive_validation_split,
)
from experiments.multimodal_v3.core import ExperimentConfigV3, build_campaign_manifest
from experiments.multimodal_v3.executors import ExistingBenchmarkDelegateV3
from experiments.multimodal_v3.fixtures import (
    StubLLMClientV3, build_smoke_orchestrator, smoke_config, smoke_model_evaluator,
    smoke_observation, smoke_rca,
)
from explanations_v2 import ExplanationMode, ExplanationService
from experiments.multimodal_v3.cli import main as orchestrator_cli


def test_planner_is_deterministic_and_run_identity_changes_with_relevant_configuration():
    first = ExperimentPlannerV3(smoke_config()).plan()
    second = ExperimentPlannerV3(smoke_config()).plan()
    changed = ExperimentPlannerV3(replace(smoke_config(), seeds=(8,))).plan()
    assert [item.run_id for item in first] == [item.run_id for item in second]
    assert set(item.run_id for item in first).isdisjoint(item.run_id for item in changed)


def test_planner_builds_all_hypotheses_and_expected_run_count():
    planner = ExperimentPlannerV3(smoke_config())
    runs = planner.plan()
    assert len(runs) == 26
    assert {item.hypothesis for item in runs} == {"H1", "H2", "H3", "H4"}
    assert planner.summary(runs) == {
        "expected_executions": 26, "expected_skips": 0,
        "expected_H1": 12, "expected_H2": 7, "expected_H3": 3, "expected_H4": 4,
    }


def test_capability_matrix_generates_explicit_skips_without_fabricating_modalities():
    metric_only = tuple(DatasetCapabilityV3(
        "rcaeval", "RE1-OB", f"metric-{index}", ("metric",), "c" * 64)
        for index in range(3))
    config = replace(smoke_config(), datasets=metric_only)
    runs = ExperimentPlannerV3(config).plan()
    trace = next(item for item in runs if item.feature_view == "TRACE_ONLY")
    business = next(item for item in runs if item.experiment_family == "H1-B"
                    and item.graph_mode == "GRAPH_BUSINESS")
    assert not trace.eligibility and trace.skip_reason == "missing_modalities:trace"
    assert not business.eligibility and business.skip_reason == "missing_modalities:business_event"
    assert trace.available_modalities == ("metric",)


def test_protocol_and_validation_split_are_incident_grouped_and_leak_free():
    train, validation = derive_validation_split(("i1", "i2", "i3", "i4"), seed=7)
    assert set(train).isdisjoint(validation)
    assert (train, validation) == derive_validation_split(("i4", "i3", "i2", "i1"), seed=7)
    with pytest.raises(ValueError, match="insuficiente"):
        derive_validation_split(("only",), seed=7)


def test_planner_delegates_protocols_and_marks_non_applicable_without_leakage():
    planner = ExperimentPlannerV3(smoke_config())
    assignments, manifests = planner.protocol_assignments("within-dataset", 7)
    assert manifests[0]["version"] == "public-dataset-protocol-v2"
    assert len(assignments) == 1
    assignment = assignments[0]
    assert set(assignment.train_incident_ids).isdisjoint(assignment.test_incident_ids)
    assert set(assignment.validation_incident_ids).isdisjoint(assignment.test_incident_ids)
    singleton = DatasetCapabilityV3("rcaeval", "RE1", "only", ("metric",), "c" * 64)
    skipped = ExperimentPlannerV3(replace(smoke_config(), datasets=(singleton,))).plan()
    assert all(not run.eligibility for run in skipped)
    assert all(run.skip_reason.startswith("protocol_not_applicable:") for run in skipped)


@pytest.mark.parametrize("protocol", ("cross-system", "cross-source", "leave-one-system-out"))
def test_all_public_protocols_reuse_protocol_planner(protocol):
    capabilities = tuple(DatasetCapabilityV3(
        "source-a" if index < 2 else "source-b",
        "system-a" if index < 2 else "system-b",
        f"incident-{index}", ("metric",), f"{index + 1:x}" * 64)
        for index in range(4))
    planner = ExperimentPlannerV3(replace(
        smoke_config(), datasets=capabilities, protocols=(protocol,), hypotheses=("H2",)))
    assignments, manifests = planner.protocol_assignments(protocol, 7)
    assert manifests and all(item["version"] == "public-dataset-protocol-v2" for item in manifests)
    assert assignments and all(item.applicable for item in assignments)
    assert all(set(item.train_incident_ids).isdisjoint(item.test_incident_ids)
               and set(item.validation_incident_ids).isdisjoint(item.test_incident_ids)
               for item in assignments)


def test_h1_structural_ablations_keep_algorithm_and_control_graph_business_pruning():
    runs = [item for item in ExperimentPlannerV3(smoke_config()).plan()
            if item.experiment_family == "H1-B"]
    assert {item.method for item in runs} == {"xgboost-rca-v3"}
    by_mode = {item.graph_mode: item for item in runs}
    assert by_mode["NO_GRAPH"].pruning_mode == "disabled_ablation"
    assert by_mode["NO_GRAPH"].feature_view_hash != by_mode["GRAPH"].feature_view_hash
    assert by_mode["GRAPH"].required_modalities == ()
    assert by_mode["GRAPH_BUSINESS"].required_modalities == ("business_event",)
    assert len({item.train_incident_ids for item in runs}) == 1
    manifests = ExperimentPlannerV3.feature_view_manifests(runs)
    by_id = {item["id"]: item for item in manifests}
    assert "graph" in by_id["NO_GRAPH"]["inactive_groups"]
    assert "business" in by_id["GRAPH"]["inactive_groups"]


def test_h2_delegation_plan_uses_seven_existing_methods_and_same_full_view():
    runs = [item for item in ExperimentPlannerV3(smoke_config()).plan()
            if item.hypothesis == "H2"]
    assert len(runs) == 7
    assert {item.method for item in runs} == {
        "temporal-baseline", "max-zscore", "first-anomalous", "logistic-regression",
        "random-forest", "xgboost-rca-v3", "mlp-light-v3"}
    assert {item.feature_view for item in runs} == {"FULL"}
    assert len({item.feature_view_hash for item in runs}) == 1
    assert len({(item.train_incident_ids, item.validation_incident_ids,
                 item.test_incident_ids) for item in runs}) == 1


def test_smoke_executes_persists_validates_statistics_and_exports_paper(tmp_path, repo_root):
    orchestrator = build_smoke_orchestrator(tmp_path, repo_root, "smoke-test")
    results = orchestrator.run()
    runs = orchestrator.planner.plan()
    validation = CampaignValidatorV3().validate(
        runs, results, store=orchestrator.store, repository_root=repo_root)
    assert validation["complete"] and len(results) == 26
    assert (orchestrator.store.path / "campaign-manifest.json").is_file()
    assert (orchestrator.store.path / "experiment-plan.csv").is_file()
    assert (orchestrator.store.path / "SHA256SUMS").is_file()
    ExperimentReportBuilderV3().build(orchestrator.store, results, validation)
    assert (orchestrator.store.path / "paper/summary-metrics.json").is_file()
    blinded = (orchestrator.store.path / "h3/h3-human-evaluation-blinded.csv").read_text()
    assert all(mode not in blinded for mode in ("generic", "raw_telemetry", "evidence_grounded"))


@pytest.fixture
def repo_root():
    return __import__("pathlib").Path(__file__).resolve().parents[2]


def test_resume_does_not_duplicate_and_rejects_configuration_divergence(tmp_path, repo_root):
    first = build_smoke_orchestrator(tmp_path, repo_root, "resume-test")
    initial = first.run()
    resumed = build_smoke_orchestrator(tmp_path, repo_root, "resume-test").run(resume=True)
    assert len(initial) == len(resumed) == 26
    assert len(list(first.store.runs_path.glob("*.json"))) == 27  # 26 runs + index
    divergent = build_smoke_orchestrator(tmp_path, repo_root, "resume-test")
    divergent.config = replace(divergent.config, seeds=(99,))
    divergent.planner = ExperimentPlannerV3(divergent.config)
    with pytest.raises(ValueError, match="configuration_sha256"):
        divergent.run(resume=True)


def test_resume_detects_corrupted_output(tmp_path, repo_root):
    orchestrator = build_smoke_orchestrator(tmp_path, repo_root, "corrupt-test")
    results = orchestrator.run()
    target = next(path for path in orchestrator.store.runs_path.glob("*.json")
                  if path.name != "index.json")
    target.write_text(target.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="Checksum"):
        build_smoke_orchestrator(tmp_path, repo_root, "corrupt-test").run(resume=True)
    validation = CampaignValidatorV3().validate(
        orchestrator.planner.plan(), results, store=orchestrator.store)
    assert "checksum_mismatch" in validation["issues"]


def test_resume_verifies_complete_campaign_before_modifying_any_artifact(tmp_path, repo_root):
    orchestrator = build_smoke_orchestrator(tmp_path, repo_root, "campaign-corrupt")
    results = orchestrator.run()
    validation = CampaignValidatorV3().validate(
        orchestrator.planner.plan(), results, store=orchestrator.store,
        repository_root=repo_root)
    ExperimentReportBuilderV3().build(orchestrator.store, results, validation)
    checksum_path = orchestrator.store.path / "SHA256SUMS"
    original_checksums = checksum_path.read_bytes()
    target = orchestrator.store.path / "paper/summary-metrics.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum|SHA256SUMS"):
        build_smoke_orchestrator(
            tmp_path, repo_root, "campaign-corrupt").run(resume=True)

    assert checksum_path.read_bytes() == original_checksums


def test_h3_uses_same_rca_root_and_hash_for_three_distinct_safe_modes(tmp_path, repo_root):
    orchestrator = build_smoke_orchestrator(tmp_path, repo_root, "h3-test")
    results = [item for item in orchestrator.run() if item["identity"]["hypothesis"] == "H3"]
    assert len(results) == 3
    assert {item["result"]["root_cause"] for item in results} == {"payment-service"}
    assert len({item["provenance"]["rca_result_hash"] for item in results}) == 1
    assert len({item["provenance"]["prompt_hash"] for item in results}) == 3
    assert all("ground_truth" not in item["provenance"]["prompt_text"].lower() for item in results)
    by_mode = {item["configuration"]["explanation_mode"]: item for item in results}
    assert "evidence_catalog" not in by_mode["generic"]["provenance"]["prompt_text"]
    assert "selected_raw_telemetry" in by_mode["raw_telemetry"]["provenance"]["prompt_text"]
    assert "allowed_evidence_ids" in by_mode["evidence_grounded"]["provenance"]["prompt_text"]


def test_h3_fallback_is_recorded_and_live_provider_is_blocked():
    run = next(item for item in ExperimentPlannerV3(smoke_config()).plan()
               if item.hypothesis == "H3" and item.explanation_mode == "generic")
    fallback = H3ExperimentExecutor(ExplanationService(), {run.incident_id: smoke_rca()})
    result, _, metrics = fallback.execute(run)
    assert result["used_fallback"] and result["fallback_reason"] == "no_llm_client"
    assert metrics["fallback_rate"] == 1.0
    client = StubLLMClientV3()
    client.is_live_provider = True
    blocked = H3ExperimentExecutor(ExplanationService(llm_client=client),
                                   {run.incident_id: smoke_rca()})
    with pytest.raises(Exception, match="live desabilitado"):
        blocked.execute(run)


def test_h3_raw_removes_all_ground_truth_fields_and_fallback_obeys_citation_boundary():
    raw = ({"event_id": "raw-1", "signal_type": "trace",
            "actual_services": ["payment-service"],
            "nested": {"actual_services": ["order-service"],
                       "ground_truth": "payment-service",
                       "fault_service": "payment-service",
                       "fault_location": "POST /payments",
                       "is_root_cause": True}},)
    service = ExplanationService()
    raw_result = service.explain(
        smoke_rca(), mode=ExplanationMode.RAW_TELEMETRY, raw_telemetry=raw)
    forbidden = {"actual_services", "ground_truth", "fault_service",
                 "fault_location", "is_root_cause"}
    assert all(field not in raw_result.prompt.prompt for field in forbidden)
    assert raw_result.explanation.cited_evidence_ids == ()
    assert raw_result.explanation.evidence_statements == ()
    generic = service.explain(smoke_rca(), mode=ExplanationMode.GENERIC)
    assert generic.prompt.sanitized_context["allowed_evidence_ids"] == ()
    assert generic.explanation.cited_evidence_ids == ()
    grounded = service.explain(smoke_rca(), mode=ExplanationMode.EVIDENCE_GROUNDED)
    allowed = set(grounded.prompt.sanitized_context["allowed_evidence_ids"])
    assert grounded.explanation.evidence_statements
    assert set(grounded.explanation.cited_evidence_ids) <= allowed


def test_configured_tuning_budget_reaches_benchmark_and_matches_manifest(
        monkeypatch, tmp_path, repo_root):
    config = replace(smoke_config("budget-test"), hypotheses=("H2",), tuning_budget=4)
    run = ExperimentPlannerV3(config).plan()[0]
    dataset = SimpleNamespace(
        feature_schema={"feature_view_sha256": run.feature_view_hash},
        dataset_hash="d" * 64)
    captured = []

    def fake_train(method, dataset_arg, output, **kwargs):
        captured.append(kwargs)
        return {"metrics": {"macro_f1": 1.0},
                "incident_predictions": [{
                    "incident_id": run.incident_id, "ranking": [],
                    "actual_services": ["payment-service"], "reciprocal_rank": 1.0}],
                "manifest": {"splits": {
                    "train": list(run.train_incident_ids),
                    "validation": list(run.validation_incident_ids),
                    "test": list(run.test_incident_ids)}},
                "artifact_dir": str(tmp_path)}

    monkeypatch.setattr(
        "experiments.multimodal_v3.executors.train_and_evaluate", fake_train)
    delegate = ExistingBenchmarkDelegateV3(
        {(run.source_dataset, run.source_system, run.feature_view, run.protocol,
          run.held_out, run.seed): dataset},
        tmp_path, tuning_budget=config.tuning_budget)
    report = delegate(run)
    delegate(run)
    manifest = build_campaign_manifest(config, (run,), repo_root)
    assert captured == [{"seed": run.seed, "tuning_budget": 4,
                         "bootstrap_iterations": 100, "runtime_policy": None}]
    assert report["effective_tuning_budget"] == 4
    assert manifest["models"]["tuning_budget"] == captured[0]["tuning_budget"]


def test_benchmark_delegate_reuses_physical_dataset_across_logical_catalog_keys(
        monkeypatch, tmp_path):
    base = next(run for run in ExperimentPlannerV3(replace(
        smoke_config(), hypotheses=("H2",))).plan()
                if run.method == "xgboost-rca-v3")
    logical_reuse = replace(base, source_system="logical-system-b",
                            protocol="cross-system", run_id="")
    dataset = SimpleNamespace(
        feature_schema={"feature_view_sha256": base.feature_view_hash},
        dataset_hash="d" * 64)
    calls = []

    def fake_train(method, dataset_arg, output, **kwargs):
        calls.append((method, dataset_arg.dataset_hash, kwargs["seed"]))
        return {
            "metrics": {"macro_f1": 1.0},
            "incident_predictions": [{
                "incident_id": base.incident_id, "ranking": [],
                "actual_services": ["payment-service"], "reciprocal_rank": 1.0,
            }],
            "manifest": {"splits": {
                "train": list(base.train_incident_ids),
                "validation": list(base.validation_incident_ids),
                "test": list(base.test_incident_ids),
            }},
            "artifact_dir": str(tmp_path / method / dataset_arg.dataset_hash[:12]
                                / f"seed-{kwargs['seed']}"),
        }

    monkeypatch.setattr(
        "experiments.multimodal_v3.executors.train_and_evaluate", fake_train)
    catalog = {
        (base.source_dataset, base.source_system, base.feature_view, base.protocol,
         base.held_out, base.seed): dataset,
        (logical_reuse.source_dataset, logical_reuse.source_system,
         logical_reuse.feature_view, logical_reuse.protocol, logical_reuse.held_out,
         logical_reuse.seed): dataset,
    }
    delegate = ExistingBenchmarkDelegateV3(catalog, tmp_path, tuning_budget=6,
                                            bootstrap_iterations=1000)

    first = delegate(base)
    second = delegate(logical_reuse)

    assert calls == [(base.method, dataset.dataset_hash, base.seed)]
    assert first["artifact_dir"] == second["artifact_dir"]


def test_benchmark_delegate_requires_exact_logical_catalog_lookup(tmp_path):
    run = next(run for run in ExperimentPlannerV3(replace(
        smoke_config(), hypotheses=("H2",))).plan()
               if run.method == "xgboost-rca-v3")
    dataset = SimpleNamespace(
        feature_schema={"feature_view_sha256": run.feature_view_hash},
        dataset_hash="d" * 64)
    delegate = ExistingBenchmarkDelegateV3(
        {(run.source_dataset, run.source_system, run.feature_view): dataset}, tmp_path)

    with pytest.raises(Exception, match="BenchmarkDataset ausente") as raised:
        delegate(run)
    assert raised.value.stage == "dataset_lookup"


def test_tranche_a_catalog_reuses_physical_benchmark_datasets(repo_root):
    catalog = json.loads((
        repo_root / "artifacts/final-experiments-v3/scientific-inputs-tranche-a-180"
        / "benchmark-catalog.json").read_text(encoding="utf-8"))
    rows = catalog["datasets"]
    full = [row for row in rows if row["feature_view"] == "FULL"]

    assert len(rows) == 18  # H1A_LOGICAL_CATALOG_KEYS
    assert len({row["benchmark_dataset_sha256"] for row in rows}) == 7
    assert len(full) == 3  # FULL_VIEW_LOGICAL_KEYS
    assert len({row["benchmark_dataset_sha256"] for row in full}) == 1


def test_h4_is_paired_uses_same_base_noop_and_never_retrains(tmp_path, repo_root):
    results = [item for item in build_smoke_orchestrator(
        tmp_path, repo_root, "h4-test").run() if item["identity"]["hypothesis"] == "H4"]
    assert {item["configuration"]["degradation_severity"] for item in results} == {0.0, .1, .3, .5}
    assert len({item["provenance"]["base_observation_sha256"] for item in results}) == 1
    assert all(item["provenance"]["model_retrained"] is False for item in results)
    zero = next(item for item in results if item["configuration"]["degradation_severity"] == 0.0)
    assert zero["provenance"]["base_observation_sha256"] == zero["provenance"]["degraded_observation_sha256"]
    assert all(item["provenance"]["baseline_0pct_run_id"] == zero["identity"]["run_id"]
               for item in results)


def test_kafka_replay_is_optional_in_tests_and_failure_has_own_status():
    run = next(item for item in ExperimentPlannerV3(
        replace(smoke_config(), hypotheses=("H4",), execution_mode="kafka-replay")).plan()
        if item.degradation_severity == 0.0)
    executor = H4ExperimentExecutor(
        {run.incident_id: smoke_observation()}, smoke_model_evaluator)
    with pytest.raises(Exception) as raised:
        executor.execute(run)
    assert raised.value.status is RunStatusV3.FAILED_REPLAY


def test_h4_unavailable_operator_modality_is_an_explicit_skip():
    capabilities = tuple(DatasetCapabilityV3(
        "rcaeval", "RE1", f"metric-{index}", ("metric",), f"{index + 1:x}" * 64)
        for index in range(3))
    runs = ExperimentPlannerV3(replace(
        smoke_config(), datasets=capabilities, hypotheses=("H4",),
        degradation_operators=("random_span_drop",))).plan()
    assert runs and all(not run.eligibility for run in runs)
    assert all(run.skip_reason == "missing_modalities:trace" for run in runs)


def test_validator_detects_missing_duplicate_illegal_modality_version_nan_and_leakage(tmp_path, repo_root):
    orchestrator = build_smoke_orchestrator(tmp_path, repo_root, "validator-test")
    runs = orchestrator.planner.plan()
    results = list(orchestrator.run())
    missing = results[:-1]
    assert any(issue.startswith("missing_run") for issue in CampaignValidatorV3().validate(runs, missing)["issues"])
    duplicate = results + [results[0]]
    assert "duplicate_run" in CampaignValidatorV3().validate(runs, duplicate)["issues"]
    mutated = deepcopy(results)
    mutated[0]["provenance"]["observed_modalities"] = ["business_event", "illegal"]
    mutated[0]["provenance"]["orchestrator_version"] = "old"
    mutated[0]["provenance"]["train_ids"] = ["same"]
    mutated[0]["provenance"]["test_ids"] = ["same"]
    mutated[0]["evaluation"]["bad"] = float("nan")
    h3_item = next(item for item in mutated if item["identity"]["hypothesis"] == "H3")
    h3_item["provenance"]["prompt_text"] += ' "actual_services": ["secret"]'
    issues = CampaignValidatorV3().validate(runs, mutated)["issues"]
    assert any(value.startswith("illegal_modality") for value in issues)
    assert any(value.startswith("version_mismatch") for value in issues)
    assert any(value.startswith("split_leakage") for value in issues)
    assert any(value.startswith("nonfinite") for value in issues)
    assert any(value.startswith("ground_truth_in_prompt") for value in issues)


def test_statistics_require_exact_pairing_are_deterministic_and_apply_holm():
    left = {"a": {"score": .2, "top1_correct": False},
            "b": {"score": .4, "top1_correct": True}}
    right = {"a": {"score": .5, "top1_correct": True},
             "b": {"score": .6, "top1_correct": True}}
    first = ExperimentStatisticsV3.compare_paired(left, right, metric="score", samples=100)
    assert first == ExperimentStatisticsV3.compare_paired(left, right, metric="score", samples=100)
    assert first["pairs"] == 2 and first["mcnemar"]["discordant_pairs"] == 1
    adjusted = ExperimentStatisticsV3.holm((.01, .04, .03))
    assert len(adjusted) == 3 and all(0 <= value <= 1 for value in adjusted)
    with pytest.raises(ValueError, match="alinhamento"):
        ExperimentStatisticsV3.compare_paired(left, {"a": right["a"]}, metric="score")


def test_paired_report_bootstraps_incident_reciprocal_rank_not_repeated_macro_f1():
    def item(method, incident, reciprocal_rank, correct):
        predicted = "payment-service" if correct else "order-service"
        return {"identity": {"hypothesis": "H1", "experiment_family": "H1-A",
                             "protocol": "within-dataset", "repetition": 0},
                "input": {"dataset": "local", "system": "causalods",
                          "incident_id": incident, "seed": 7},
                "configuration": {"method": method},
                "result": {"predicted_ranking": [{"service_name": predicted}]},
                "evaluation": {"reciprocal_rank": reciprocal_rank,
                               "macro_f1": 0.99,
                               "ground_truth_services": ["payment-service"]},
                "status": {"state": "SUCCESS"}}

    values = []
    for incident, left, right in zip(
            ("i1", "i2", "i3"), (1.0, 0.5, 0.0), (1.0, 1.0, 0.5)):
        values.extend((item("temporal-baseline", incident, left, left == 1.0),
                       item("xgboost-rca-v3", incident, right, right == 1.0)))
    row = ExperimentReportBuilderV3._paired_rows(values, "H1")[0]
    assert row["metric"] == "mrr"
    assert row["bootstrap_unit"] == "incident_reciprocal_rank"
    assert row["mcnemar_endpoint"] == "top1_accuracy"
    assert row["value_a"] == pytest.approx(0.5)
    assert row["value_b"] == pytest.approx(5 / 6)


def test_plan_json_csv_and_sha256s_are_stable(tmp_path, repo_root):
    first = build_smoke_orchestrator(tmp_path, repo_root, "stable-test")
    first.prepare()
    plan_json = (first.store.path / "experiment-plan.json").read_bytes()
    plan_csv = (first.store.path / "experiment-plan.csv").read_bytes()
    second = build_smoke_orchestrator(tmp_path, repo_root, "stable-test")
    second.prepare()
    assert (second.store.path / "experiment-plan.json").read_bytes() == plan_json
    assert (second.store.path / "experiment-plan.csv").read_bytes() == plan_csv
    second.store.write_sha256s()
    sums = (second.store.path / "SHA256SUMS").read_text()
    second.store.write_sha256s()
    assert (second.store.path / "SHA256SUMS").read_text() == sums


def test_human_rating_import_requires_pairs_and_calculates_agreement(tmp_path):
    header = ("evaluation_item_id,reviewer_id,utility,clarity,perceived_confidence,"
              "traceability,uncertainty_adequacy\n")
    path = tmp_path / "ratings.csv"
    path.write_text(header + "i1,r1,5,4,4,5,4\n", encoding="utf-8")
    insufficient = ExperimentReportBuilderV3.import_human_ratings(path)
    assert insufficient["status"] == "INSUFFICIENT_DATA"
    path.write_text(header + "i1,r1,5,4,4,5,4\ni1,r2,5,3,4,5,4\n", encoding="utf-8")
    calculated = ExperimentReportBuilderV3.import_human_ratings(path)
    assert calculated["status"] == "CALCULATED"
    assert calculated["agreement"]["utility"] == 1.0


def test_cli_run_and_resume_use_explicit_executor_factory(tmp_path):
    config_path = tmp_path / "campaign.json"
    config_path.write_text(json.dumps(smoke_config("cli-run").to_dict()), encoding="utf-8")
    common = ["--config", str(config_path), "--output", str(tmp_path),
              "--executor-factory",
              "experiments.multimodal_v3.fixtures:smoke_executor_factory"]
    assert orchestrator_cli(["run", *common]) == 0
    assert orchestrator_cli(["resume", *common]) == 0
