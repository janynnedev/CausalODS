from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import shutil

import numpy as np
import pytest

from multimodal_v1.benchmark_v3.cli import main, parser
from multimodal_v1.benchmark_v3.core import (
    BENCHMARK_VERSION, ML_MODELS, MODEL_NAMES, BenchmarkDataset, BenchmarkRow,
    BenchmarkRuntimePolicy, canonical_json, compare_predictions, evaluate_artifact,
    load_verified_artifact, positive_scores, sha256, train_and_evaluate, tune_model,
    validate_incident_prediction,
)
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.event_graph import EventGraphBuilder
from multimodal_v1.features_v3 import (
    FeatureInputV3, IncidentDatasetGeneratorV3, RCAServiceTargetV3,
)


BASE = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
FAST_RUNTIME = BenchmarkRuntimePolicy(1, 1, 1)


def _event(event_id, signal, service, workflow, seconds):
    values = dict(event_id=event_id, event_name=event_id, event_type=signal,
                  signal_type=signal, event_timestamp=BASE + timedelta(seconds=seconds),
                  service_name=service, correlation_id=workflow,
                  source_reference=f"fixture:{event_id}")
    if signal == "trace":
        values.update(trace_id=hashlib.md5(workflow.encode()).hexdigest(),
                      span_id=hashlib.md5(event_id.encode()).hexdigest()[:16],
                      attributes={"duration_ms": 10 + seconds, "expected_span_count": 2})
    elif signal == "log":
        values.update(severity="ERROR" if service == "payment-service" else "INFO",
                      attributes={"body": "fixture message"})
    elif signal == "metric":
        values.update(metric_name="system.cpu.utilization",
                      metric_value=80 if service == "payment-service" else 20)
    else:
        values.update(business_operation="transition", business_outcome="approved",
                      business_entity_type="order", business_entity_id=workflow)
    return UnifiedEvent(**values)


def _feature_input(number):
    workflow = f"workflow-{number}"
    assembler = IncidentEvidenceAssembler()
    for index, service in enumerate(("order-service", "payment-service")):
        for offset, signal in enumerate(("trace", "log", "metric", "business_event")):
            assembler.ingest(_event(f"{signal}-{number}-{index}", signal, service,
                                    workflow, index * 10 + offset))
    bundle = assembler.active_bundles()[0]
    return FeatureInputV3(bundle, EventGraphBuilder().build(bundle))


@pytest.fixture(scope="module")
def official_dataset(tmp_path_factory):
    inputs = [_feature_input(number) for number in range(12)]
    labels = []
    for number, item in enumerate(inputs):
        root = "payment-service" if number % 2 else "order-service"
        for service in ("order-service", "payment-service"):
            labels.append(RCAServiceTargetV3(
                item.bundle.incident_id, service, service == root, "labeled", "fixture"))
    dataset = IncidentDatasetGeneratorV3(seed=73).generate(
        inputs, targets=labels, supervised=True,
        train_fraction=.5, validation_fraction=.25)
    path = tmp_path_factory.mktemp("benchmark-real") / "dataset.json"
    path.write_text(dataset.to_json(), encoding="utf-8")
    return dataset, path


def _load(official_dataset):
    return BenchmarkDataset.from_json(official_dataset[1])


def _rehash_payload(payload):
    manifest = payload["manifest"]
    manifest["rows_sha256"] = sha256(payload["rows"])
    for split in ("train", "validation", "test"):
        manifest["split_sha256"][split] = sha256(
            [row for row in payload["rows"] if row["split"] == split])
    payload["dataset_sha256"] = sha256({key: value for key, value in payload.items()
                                        if key != "dataset_sha256"})


@pytest.fixture
def verified_artifact(official_dataset, tmp_path):
    data = _load(official_dataset)
    result = train_and_evaluate("temporal-baseline", data, tmp_path, seed=31,
                                tuning_budget=1, bootstrap_iterations=5,
                                runtime_policy=FAST_RUNTIME)
    return data, pytest.importorskip("pathlib").Path(result["artifact_dir"]), result


def test_real_feature_dataset_round_trip_has_40_canonical_columns(official_dataset, tmp_path):
    source, _ = official_dataset
    data = _load(official_dataset)
    assert len(data.feature_names) == 40
    assert data.feature_names == source.feature_view.names
    baseline = train_and_evaluate("temporal-baseline", data, tmp_path, seed=1,
                                  tuning_budget=1, bootstrap_iterations=5,
                                  runtime_policy=FAST_RUNTIME)
    supervised = train_and_evaluate("logistic-regression", data, tmp_path, seed=1,
                                    tuning_budget=1, bootstrap_iterations=5,
                                    runtime_policy=FAST_RUNTIME)
    assert baseline["metrics"]["mrr"] >= 0
    assert supervised["manifest"]["selection_metric"] == "validation_mrr"


def test_json_mapping_order_does_not_change_canonical_feature_order(official_dataset, tmp_path):
    payload = json.loads(official_dataset[1].read_text())
    for row in payload["rows"]:
        row["vector"]["features"] = dict(sorted(row["vector"]["features"].items()))
    _rehash_payload(payload)
    path = tmp_path / "alphabetical.json"
    path.write_text(canonical_json(payload))
    assert BenchmarkDataset.from_json(path).feature_names == official_dataset[0].feature_view.names


@pytest.mark.parametrize("mutation,match", [
    ("dataset_hash", "dataset_sha256"), ("missing", "missing"),
    ("extra", "extra"), ("infinite", "range|finita"),
])
def test_dataset_integrity_rejects_tampering(official_dataset, tmp_path, mutation, match):
    payload = json.loads(official_dataset[1].read_text())
    if mutation == "dataset_hash":
        payload["dataset_sha256"] = "0" * 64
    elif mutation == "missing":
        del payload["rows"][0]["vector"]["features"][payload["manifest"]["feature_names"][0]]
        _rehash_payload(payload)
    elif mutation == "extra":
        payload["rows"][0]["vector"]["features"]["extra"] = 1
        _rehash_payload(payload)
    else:
        payload["rows"][0]["vector"]["features"][payload["manifest"]["feature_names"][0]] = float("inf")
    path = tmp_path / f"{mutation}.json"
    path.write_text(json.dumps(payload))
    with pytest.raises((ValueError, TypeError), match=match):
        BenchmarkDataset.from_json(path)


def test_dataset_contract_rejects_cross_split_and_incident_without_positive(official_dataset):
    data = _load(official_dataset)
    rows = list(data.rows)
    rows[0] = replace(rows[0], split="train" if rows[0].split != "train" else "test")
    with pytest.raises(ValueError, match="incidente|Leakage"):
        replace(data, rows=tuple(rows)).validate()
    incident = data.rows[0].incident_id
    no_root = tuple(replace(row, target=False) if row.incident_id == incident else row
                    for row in data.rows)
    with pytest.raises(ValueError, match="causa raiz"):
        replace(data, rows=no_root).validate()


@pytest.mark.parametrize("field", ["incident_id", "service_name"])
def test_dataset_rejects_target_from_another_vector_identity(official_dataset, tmp_path, field):
    payload = json.loads(official_dataset[1].read_text())
    payload["rows"][0]["target"][field] = "different-value"
    _rehash_payload(payload)
    path = tmp_path / f"target-{field}.json"
    path.write_text(canonical_json(payload))
    with pytest.raises(ValueError, match=f"target.{field}"):
        BenchmarkDataset.from_json(path)


def test_dataset_rejects_label_distribution_and_noncanonical_row_order(official_dataset, tmp_path):
    payload = json.loads(official_dataset[1].read_text())
    payload["manifest"]["label_distribution"]["positive"] += 1
    payload["dataset_sha256"] = sha256({key: value for key, value in payload.items()
                                        if key != "dataset_sha256"})
    path = tmp_path / "distribution.json"
    path.write_text(canonical_json(payload))
    with pytest.raises(ValueError, match="label_distribution"):
        BenchmarkDataset.from_json(path)
    payload = json.loads(official_dataset[1].read_text())
    payload["rows"][0], payload["rows"][1] = payload["rows"][1], payload["rows"][0]
    _rehash_payload(payload)
    path = tmp_path / "order.json"
    path.write_text(canonical_json(payload))
    with pytest.raises(ValueError, match="ordem canônica"):
        BenchmarkDataset.from_json(path)


def _valid_incident_prediction():
    return {"incident_id": "incident-1", "actual_services": ["root"],
            "ranking": [{"service_name": "root", "score": .8, "is_root_cause": True},
                        {"service_name": "worker", "score": .2, "is_root_cause": False}],
            "reciprocal_rank": 1.0}


def test_incident_prediction_is_reconstructed_and_rejects_serialized_divergence():
    item = _valid_incident_prediction()
    normalized = validate_incident_prediction(item)
    assert (normalized.first_root_rank, normalized.reciprocal_rank,
            normalized.top1_correct, normalized.top3_correct) == (1, 1.0, True, True)
    left, right = deepcopy(item), deepcopy(item)
    right["reciprocal_rank"] = .5
    manifest = {"model_name": "model", "dataset_sha256": "a" * 64,
                "splits": {"test": ["incident-1"]}}
    with pytest.raises(ValueError, match="reciprocal_rank"):
        compare_predictions({"manifest": manifest, "incident_predictions": [left]},
                            {"manifest": manifest, "incident_predictions": [right]},
                            iterations=5)


@pytest.mark.parametrize("mutation,match", [
    ("duplicate", "duplicado"), ("roots", "actual_services"),
    ("nan", "score"), ("infinite", "score"), ("range", "score"),
    ("order", "ordem canônica"), ("missing_root", "actual_services|ausente"),
])
def test_incident_prediction_contract_rejects_invalid_rankings(mutation, match):
    item = _valid_incident_prediction()
    if mutation == "duplicate":
        item["ranking"].append(dict(item["ranking"][0]))
    elif mutation == "roots":
        item["actual_services"] = ["worker"]
    elif mutation == "nan":
        item["ranking"][0]["score"] = math.nan
    elif mutation == "infinite":
        item["ranking"][0]["score"] = math.inf
    elif mutation == "range":
        item["ranking"][0]["score"] = 1.1
    elif mutation == "order":
        item["ranking"].reverse()
    else:
        item["actual_services"] = ["ghost"]
    with pytest.raises((ValueError, TypeError), match=match):
        validate_incident_prediction(item)


@pytest.mark.parametrize("name", sorted(ML_MODELS))
def test_empty_train_column_is_preserved_by_every_supervised_model(official_dataset, name):
    data = _load(official_dataset)
    index = 0
    rows = tuple(replace(row, features=tuple(
        None if position == index and row.split == "train" else value
        for position, value in enumerate(row.features))) for row in data.rows)
    altered = replace(data, rows=rows)
    model, _, _, tuning = tune_model(name, altered, seed=5, budget=1)
    x_validation, _, validation_rows = altered.select("validation")
    assert model.named_steps["imputer"].transform(x_validation).shape[1] == 40
    assert model.n_features_in_ == 40
    assert positive_scores(model, x_validation).shape == (len(validation_rows),)
    assert tuning["tuning_trials_executed"] == 1


def test_tuning_records_all_trials_and_never_uses_test(official_dataset):
    data = _load(official_dataset)
    poisoned = tuple(replace(row, features=tuple(999 if value is not None else None
                                                for value in row.features))
                     if row.split == "test" else row for row in data.rows)
    first = tune_model("logistic-regression", data, seed=9, budget=2)
    second = tune_model("logistic-regression", replace(data, rows=poisoned), seed=9, budget=2)
    assert first[1] == second[1]
    assert [(trial["parameters"], trial["validation_metrics"], trial["status"])
            for trial in first[3]["trials"]] == [
                (trial["parameters"], trial["validation_metrics"], trial["status"])
                for trial in second[3]["trials"]]
    assert all(trial["validation_metrics"] for trial in first[3]["trials"])


def test_artifact_integrity_overwrite_and_xgboost_filename(official_dataset, tmp_path):
    data = _load(official_dataset)
    result = train_and_evaluate("xgboost-rca-v3", data, tmp_path, seed=4,
                                tuning_budget=1, bootstrap_iterations=5,
                                runtime_policy=FAST_RUNTIME)
    artifact = result["artifact_dir"]
    assert (pytest.importorskip("pathlib").Path(artifact) / "xgboost-rca-v3.joblib").is_file()
    with pytest.raises(FileExistsError):
        train_and_evaluate("xgboost-rca-v3", data, tmp_path, seed=4,
                           tuning_budget=1, bootstrap_iterations=5,
                           runtime_policy=FAST_RUNTIME)
    evaluate_artifact(artifact, data, bootstrap_iterations=5, runtime_policy=FAST_RUNTIME)
    metrics = pytest.importorskip("pathlib").Path(artifact) / "metrics.json"
    metrics.write_text(metrics.read_text() + " ")
    with pytest.raises(ValueError, match="metrics.json"):
        evaluate_artifact(artifact, data, bootstrap_iterations=5,
                          runtime_policy=FAST_RUNTIME)
    rebuilt = train_and_evaluate("xgboost-rca-v3", data, tmp_path, seed=4,
                                 tuning_budget=1, bootstrap_iterations=5,
                                 runtime_policy=FAST_RUNTIME, overwrite=True)
    model_path = pytest.importorskip("pathlib").Path(rebuilt["artifact_dir"]) / "xgboost-rca-v3.joblib"
    model_path.write_bytes(model_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="xgboost-rca-v3.joblib"):
        evaluate_artifact(rebuilt["artifact_dir"], data, bootstrap_iterations=5,
                          runtime_policy=FAST_RUNTIME)


def _copy_artifact(artifact, tmp_path, name):
    target = tmp_path / name
    shutil.copytree(artifact, target)
    return target


def _reanchor_manifest(artifact):
    manifest_path = artifact / "manifest-core.json"
    hashes_path = artifact / "artifact-hashes.json"
    integrity_path = artifact / "run-integrity.json"
    manifest = json.loads(manifest_path.read_text())
    hashes = json.loads(hashes_path.read_text())
    integrity = json.loads(integrity_path.read_text())
    integrity["manifest_payload_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    contract = {"benchmark_version": manifest["benchmark_version"],
                "run_id": manifest["run_id"], "model_name": manifest["model_name"],
                "dataset_sha256": manifest["dataset_sha256"],
                "feature_view_sha256": manifest["feature_view_sha256"],
                "seed": manifest["seed"],
                "manifest_payload_sha256": integrity["manifest_payload_sha256"],
                "artifact_hashes": hashes,
                "artifact_hashes_sha256": integrity["artifact_hashes_sha256"]}
    integrity["run_fingerprint"] = sha256(contract)
    integrity_path.write_text(canonical_json(integrity) + "\n")


def test_run_integrity_rejects_isolated_and_coordinated_tampering(verified_artifact, tmp_path):
    _, artifact, _ = verified_artifact
    manifest_tampered = _copy_artifact(artifact, tmp_path, "manifest-tampered")
    with (manifest_tampered / "manifest-core.json").open("a") as target:
        target.write(" ")
    with pytest.raises(ValueError, match="manifest-core"):
        load_verified_artifact(manifest_tampered)
    hashes_tampered = _copy_artifact(artifact, tmp_path, "hashes-tampered")
    with (hashes_tampered / "artifact-hashes.json").open("a") as target:
        target.write(" ")
    with pytest.raises(ValueError, match="artifact-hashes"):
        load_verified_artifact(hashes_tampered)
    coordinated = _copy_artifact(artifact, tmp_path, "coordinated")
    metrics_path = coordinated / "metrics.json"
    metrics_path.write_text(metrics_path.read_text() + " ")
    hashes_path = coordinated / "artifact-hashes.json"
    hashes = json.loads(hashes_path.read_text())
    hashes["metrics.json"] = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    hashes_path.write_text(canonical_json(hashes) + "\n")
    with pytest.raises(ValueError, match="artifact-hashes"):
        load_verified_artifact(coordinated)


def test_run_integrity_rejects_path_traversal_before_joblib(verified_artifact, tmp_path):
    _, artifact, _ = verified_artifact
    target = _copy_artifact(artifact, tmp_path, "traversal")
    manifest_path = target / "manifest-core.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["model_file"] = "../secret.joblib"
    manifest_path.write_text(canonical_json(manifest) + "\n")
    _reanchor_manifest(target)
    with pytest.raises(ValueError, match="basename"):
        load_verified_artifact(target)


def test_compare_and_report_reject_tampered_directories(verified_artifact, tmp_path):
    _, artifact, _ = verified_artifact
    target = _copy_artifact(artifact, tmp_path, "cli-tampered")
    with (target / "metrics.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="metrics.json"):
        main(["compare", "--left", str(target), "--right", str(artifact),
              "--output", str(tmp_path / "comparison.json")])
    with pytest.raises(ValueError, match="metrics.json"):
        main(["export-report", "--results", str(target), str(artifact),
              "--output", str(tmp_path / "report.md")])


def test_train_and_evaluate_share_runtime_metric_schema(verified_artifact):
    data, artifact, trained = verified_artifact
    evaluated = evaluate_artifact(artifact, data, bootstrap_iterations=5,
                                  runtime_policy=FAST_RUNTIME)
    required = {"batch_throughput_rows_s", "single_row_p50_ms",
                "single_row_p95_ms", "single_row_p99_ms",
                "python_tracemalloc_peak_bytes", "tuning_seconds",
                "final_fit_seconds", "model_size_bytes"}
    assert required <= trained["metrics"].keys()
    assert required <= evaluated["metrics"].keys()


def _comparison_report(result):
    return {"manifest": result["manifest"],
            "incident_predictions": result["incident_predictions"]}


def test_paired_comparison_contract_and_known_mcnemar(official_dataset, tmp_path):
    data = _load(official_dataset)
    left = train_and_evaluate("temporal-baseline", data, tmp_path, seed=2,
                              tuning_budget=1, bootstrap_iterations=5,
                              runtime_policy=FAST_RUNTIME)
    right = train_and_evaluate("max-zscore", data, tmp_path, seed=2,
                               tuning_budget=1, bootstrap_iterations=5,
                               runtime_policy=FAST_RUNTIME)
    comparison = compare_predictions(_comparison_report(left), _comparison_report(right),
                                     seed=3, iterations=50)
    assert comparison["difference_direction"] == "right-left"
    assert 0 <= comparison["mcnemar_exact_p_value"] <= 1
    duplicate = _comparison_report(left)
    duplicate["incident_predictions"] = [*duplicate["incident_predictions"],
                                          duplicate["incident_predictions"][0]]
    with pytest.raises(ValueError, match="duplicado"):
        compare_predictions(duplicate, _comparison_report(right), iterations=5)
    with pytest.raises(ValueError, match="iterations"):
        compare_predictions(_comparison_report(left), _comparison_report(right), iterations=0)
    divergent_target = deepcopy(_comparison_report(right))
    divergent_target["incident_predictions"][0]["actual_services"] = ["ghost"]
    with pytest.raises(ValueError, match="actual_services|Ground truth"):
        compare_predictions(_comparison_report(left), divergent_target, iterations=5)
    divergent_candidates = deepcopy(_comparison_report(right))
    divergent_candidates["incident_predictions"][0]["ranking"] = [
        candidate for candidate in divergent_candidates["incident_predictions"][0]["ranking"]
        if candidate["is_root_cause"]]
    with pytest.raises(ValueError, match="candidatos|ranking"):
        compare_predictions(_comparison_report(left), divergent_candidates, iterations=5)


def test_known_mcnemar_exact_value_is_reproducible():
    manifest_left = {"model_name": "left", "dataset_sha256": "a" * 64,
                     "splits": {"test": ["i1", "i2"]}}
    manifest_right = {"model_name": "right", "dataset_sha256": "a" * 64,
                      "splits": {"test": ["i1", "i2"]}}
    def item(incident, reciprocal):
        ranking = ([{"service_name": "root", "score": .6, "is_root_cause": True},
                    {"service_name": "other", "score": .4, "is_root_cause": False}]
                   if reciprocal == 1 else
                   [{"service_name": "other", "score": .6, "is_root_cause": False},
                    {"service_name": "root", "score": .4, "is_root_cause": True}])
        return {"incident_id": incident, "actual_services": ["root"],
                "reciprocal_rank": reciprocal,
                "ranking": ranking}
    left = {"manifest": manifest_left,
            "incident_predictions": [item("i1", .5), item("i2", .5)]}
    right = {"manifest": manifest_right,
             "incident_predictions": [item("i1", 1.0), item("i2", 1.0)]}
    result = compare_predictions(left, right, seed=1, iterations=10)
    assert result["discordant_left_only"] == 0
    assert result["discordant_right_only"] == 2
    assert result["mcnemar_exact_p_value"] == pytest.approx(.5)


def test_cli_commands_end_to_end(official_dataset, tmp_path):
    dataset_path = str(official_dataset[1])
    output = tmp_path / "cli"
    assert main(["train", "--dataset", dataset_path, "--model", "temporal-baseline",
                 "--output", str(output), "--seed", "8", "--tuning-budget", "1",
                 "--bootstrap-iterations", "5"]) == 0
    artifact = output / "temporal-baseline" / official_dataset[0].dataset_sha256[:12] / "seed-8"
    evaluated = tmp_path / "evaluated.json"
    assert main(["evaluate", "--dataset", dataset_path, "--artifact", str(artifact),
                 "--output", str(evaluated), "--bootstrap-iterations", "5"]) == 0
    all_output = tmp_path / "all"
    assert main(["benchmark-all", "--dataset", dataset_path, "--output", str(all_output),
                 "--seeds", "3", "--tuning-budget", "1",
                 "--bootstrap-iterations", "5"]) == 0
    prefix = official_dataset[0].dataset_sha256[:12]
    temporal = all_output / "temporal-baseline" / prefix / "seed-3"
    zscore = all_output / "max-zscore" / prefix / "seed-3"
    comparison = tmp_path / "comparison.json"
    assert main(["compare", "--left", str(temporal), "--right", str(zscore),
                 "--output", str(comparison), "--iterations", "5"]) == 0
    report = tmp_path / "report.md"
    assert main(["export-report", "--results", str(temporal), str(zscore),
                 "--output", str(report)]) == 0
    assert official_dataset[0].dataset_sha256 in report.read_text()


def test_cli_rejects_empty_and_duplicate_seeds(official_dataset, tmp_path):
    base = ["benchmark-all", "--dataset", str(official_dataset[1]),
            "--output", str(tmp_path)]
    with pytest.raises(ValueError, match="seed"):
        main([*base, "--seeds", ","])
    with pytest.raises(ValueError, match="únicas"):
        main([*base, "--seeds", "1,1"])


def test_model_names_exclude_gnn_and_version_is_v3():
    assert not any("gnn" in name for name in MODEL_NAMES)
    assert BENCHMARK_VERSION == "rca-model-benchmark-v3"
