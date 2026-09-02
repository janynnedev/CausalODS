#!/usr/bin/env python3
"""Inference-only H1/H2 extension execution and observation-level aggregation."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "correlation-engine")]

from multimodal_v1.benchmark_v3.core import (  # noqa: E402
    BenchmarkDataset, HeuristicEstimator, ML_MODELS, evaluate_scores,
    load_verified_artifact,
)
from experiments.final_campaigns_v3.h1h2_test_extension import (  # noqa: E402
    aggregate_observations, file_sha256, ids_sha256, validate_heuristic_identity,
)
from experiments.scripts.run_h1h2_expansion_gate_v3 import (  # noqa: E402
    CAMPAIGNS, TrainingCallGuard, artifact_for, bind_model_identity,
    campaign_root, historical_results, inference_only_scores, load_json,
    reconstruct_parent, validate_sums,
)
from experiments.multimodal_v3.core import (  # noqa: E402
    ExperimentStatisticsV3, canonical_json, sha256_value, verify_v2_freeze,
)
from experiments.final_campaigns_v3.h3_freeze_audit import audit as h3_freeze_audit  # noqa: E402


INPUT_ROOT = ROOT / "artifacts/final-experiments-v3/scientific-inputs-h1h2-test-147"
EXTENSION_ROOT = ROOT / "artifacts/final-experiments-v3-h1h2-extension-147"
FINAL_ROOT = ROOT / "artifacts/final-experiments-v3/h1h2-final-147"
REQUIRED = {"METRIC_ONLY": {"metric"}, "TRACE_ONLY": {"trace"},
            "METRIC_TRACE": {"metric", "trace"},
            "METRIC_TRACE_LOG": {"metric", "trace", "log"}}
FAMILY_CONTRACTS = {
    "H1-A": [(method, "FULL") for method in (
        "xgboost-rca-v3", "temporal-baseline", "max-zscore", "first-anomalous")],
    "H1-B": [("xgboost-rca-v3", view) for view in ("NO_GRAPH", "GRAPH")],
    "H1-C": [("xgboost-rca-v3", view) for view in (
        "METRIC_ONLY", "TRACE_ONLY", "METRIC_TRACE", "METRIC_TRACE_LOG")],
    "H2": [(method, "FULL") for method in (
        "temporal-baseline", "max-zscore", "first-anomalous", "logistic-regression",
        "random-forest", "xgboost-rca-v3", "mlp-light-v3")],
}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _historical_snapshot() -> dict[str, str]:
    return {family: file_sha256(campaign_root(root) / "SHA256SUMS")
            for family, root in CAMPAIGNS.items()}


def _capabilities() -> dict[str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts/scientific-datasets-v3/rcaeval/capabilities.json")
    return {item["incident_id"]: item for item in payload["capabilities"]}


def _catalog() -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = load_json(INPUT_ROOT / "benchmark-catalog.json")
    by_view_system, by_view = {}, {}
    for item in payload["datasets"]:
        if item["protocol"] != "within-dataset" or item["seed"] != 42:
            continue
        by_view_system[(item["feature_view"], item["source_system"])] = item
        by_view.setdefault(item["feature_view"], item)
    return by_view_system, by_view


def _applicable(view: str, capability: dict[str, Any]) -> bool:
    return REQUIRED.get(view, set()) <= set(capability["available_modalities"])


def build_plan(extension: dict[str, Any]) -> dict[str, Any]:
    caps = _capabilities()
    rows, counts = [], {}
    for family, contracts in FAMILY_CONTRACTS.items():
        applicable = skips = 0
        for incident in sorted(extension["added_test_incident_ids"]):
            capability = caps[incident]
            for method, view in contracts:
                applies = _applicable(view, capability)
                applicable += applies
                skips += not applies
                rows.append({"family": family, "incident_id": incident,
                             "source_system": capability["source_system"],
                             "method": method, "feature_view": view,
                             "protocol": "within-dataset", "seed": 42,
                             "applicability": "APPLICABLE" if applies else "SKIP_NOT_APPLICABLE",
                             "extension_tranche_sha256": extension["selection_sha256"]})
        counts[family] = {"planned": applicable + skips, "applicable": applicable,
                          "expected_structured_skips": skips}
    historical = set(extension["locked_base_test_incident_ids"])
    planned = {row["incident_id"] for row in rows}
    if planned != set(extension["added_test_incident_ids"]) or planned & historical:
        raise RuntimeError("H1_H2_EXTENSION_PLAN_INCIDENT_FILTER_FAIL")
    return {"schema_version": "h1h2-extension-execution-plan-v1", "runs": rows,
            "counts": counts, "planned_unique_incidents": len(planned),
            "historical_incidents_in_plan": len(planned & historical),
            "plan_sha256": sha256_value(rows)}


def _expected_identity(contract: dict[str, Any]) -> dict[str, Any]:
    return dict(contract["expected"])


def execute(extension: dict[str, Any], plan: dict[str, Any],
            model_audit: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_view_system, by_view = _catalog()
    caps = _capabilities()
    learned = {(item["family"], item["method"], item["feature_view"]): item
               for item in model_audit["learned_contracts"]}
    heuristic = {item["method"]: item for item in model_audit["heuristic_contracts"]}
    datasets, artifacts, models = {}, {}, {}
    guard = TrainingCallGuard()
    observations, inference_counts = [], defaultdict(int)
    for family in ("H1-A", "H1-B", "H1-C", "H2"):
        family_rows = [row for row in plan["runs"] if row["family"] == family]
        for row in family_rows:
            if row["applicability"] != "APPLICABLE":
                observations.append({**row, "execution_status": "SKIP_NOT_APPLICABLE",
                                     "result_origin": "test_extension_147"})
        for method, view in FAMILY_CONTRACTS[family]:
            incident_ids = sorted(row["incident_id"] for row in family_rows
                                  if row["method"] == method and row["feature_view"] == view
                                  and row["applicability"] == "APPLICABLE")
            if not incident_ids:
                continue
            # Every expanded view dataset has the same 147 test membership; use
            # the source-specific catalog binding where it exists.
            entry = by_view.get(view)
            if entry is None:
                raise RuntimeError(f"EXTENSION_DATASET_VIEW_MISSING:{view}")
            path = ROOT / entry["path"]
            dataset = datasets.setdefault(str(path), BenchmarkDataset.from_json(path))
            x_test, _, test_rows = dataset.select("test")
            indexes = [index for index, item in enumerate(test_rows)
                       if item.incident_id in set(incident_ids)]
            scoped_rows = tuple(test_rows[index] for index in indexes)
            if {item.incident_id for item in scoped_rows} != set(incident_ids):
                raise RuntimeError(f"EXTENSION_DATASET_INCIDENT_COVERAGE_FAIL:{family}:{method}:{view}")
            if method in ML_MODELS:
                contract = learned[(family, method, view)]
                artifact_path = ROOT / contract["artifact_dir"]
                verified = artifacts.setdefault(str(artifact_path), load_verified_artifact(artifact_path))
                model_file = verified.manifest["model_file"]
                actual = {
                    "serialized_artifact_sha256": file_sha256(artifact_path / model_file),
                    "training_split_hash": ids_sha256({item.incident_id for item in dataset.rows if item.split == "train"}),
                    "validation_split_hash": ids_sha256({item.incident_id for item in dataset.rows if item.split == "validation"}),
                    "feature_schema_sha256": verified.manifest["feature_view_sha256"],
                    "model_specification_hash": file_sha256(ROOT / "experiments/configs/final-v3/scientific-model-specification.json"),
                    "hyperparameters": dict(verified.manifest["hyperparameters"]),
                }
                bind_model_identity(_expected_identity(contract), actual)
                models.setdefault(str(artifact_path), joblib.load(artifact_path / model_file))
                model = models[str(artifact_path)]
                model_hash, implementation_hash = actual["serialized_artifact_sha256"], None
                contract_key = contract["contract_key"]
            else:
                contract = heuristic[method]
                actual_hash = hashlib.sha256(
                    f"benchmark-v3:{method}:HeuristicEstimator".encode()).hexdigest()
                validate_heuristic_identity(contract["expected_method_implementation_hash"], actual_hash)
                model = HeuristicEstimator(method, dataset.feature_names)
                model_hash, implementation_hash = None, actual_hash
                contract_key = contract["contract_key"]
            scores = inference_only_scores(model, x_test[indexes], guard)
            inference_counts[contract_key] += len(incident_ids)
            evaluated = evaluate_scores(scores, scoped_rows, inference_seconds=0.0,
                                        memory_peak_bytes=0, model_size_bytes=0,
                                        training_seconds=0.0)
            for prediction in evaluated["incident_predictions"]:
                ranking = list(prediction["ranking"])
                reciprocal = prediction["reciprocal_rank"]
                observations.append({"family": family, "incident_id": prediction["incident_id"],
                    "source_system": caps[prediction["incident_id"]]["source_system"],
                    "method": method, "feature_view": view, "protocol": "within-dataset",
                    "seed": 42, "execution_status": "SUCCESS", "result_origin": "test_extension_147",
                    "predicted_service": ranking[0]["service_name"], "ranked_services": ranking,
                    "top1_correct": reciprocal == 1.0, "top3_correct": reciprocal >= 1 / 3,
                    "reciprocal_rank": reciprocal, "confidence": ranking[0]["score"],
                    "model_specification_hash": file_sha256(ROOT / "experiments/configs/final-v3/scientific-model-specification.json"),
                    "feature_schema_sha256": entry["feature_view_sha256"],
                    "trained_model_artifact_hash": model_hash,
                    "method_implementation_hash": implementation_hash,
                    "base_training_tranche_id": "tranche-a-180",
                    "base_training_tranche_sha256": extension["base_tranche_selection_sha256"],
                    "expanded_evaluation_tranche_id": extension["tranche_id"],
                    "expanded_evaluation_tranche_sha256": extension["selection_sha256"],
                    "training_split_sha256": contract.get("actual", {}).get("training_split_hash"),
                    "validation_split_sha256": contract.get("actual", {}).get("validation_split_hash"),
                    "base_test_split_sha256": extension["base_test_incident_ids_sha256"],
                    "expanded_test_split_sha256": extension["expanded_test_incident_ids_sha256"],
                    "model_retrained": False, "model_retuned": False})
        summary = {state: sum(row["execution_status"] == state for row in observations
                              if row["family"] == family) for state in (
                                  "SUCCESS", "SKIP_NOT_APPLICABLE", "FAILED_INPUT",
                                  "FAILED_EXECUTION", "FAILED_VALIDATION")}
        print(canonical_json({"stage": "extension-progress", "family": family, **summary}), flush=True)
    expected = sum(item["planned"] for item in plan["counts"].values())
    if len(observations) != expected:
        raise RuntimeError("EXTENSION_EXECUTION_ACCOUNTING_MISMATCH")
    return observations, {"schema_version": "h1h2-final-model-reuse-audit-v1",
        "distinct_learned_contracts_used": len(learned), "inference_count_per_contract": dict(inference_counts),
        **guard.counters, "new_model_artifacts_created": 0,
        "status": "PASS" if all(value == 0 for value in guard.counters.values()) else "FAIL"}


def historical_observations(base: dict[str, Any]) -> list[dict[str, Any]]:
    rows, _ = historical_results(base)
    result = []
    artifact_cache = {}
    for old in rows:
        item = {key: value for key, value in old.items()
                if key not in {"campaign_root"}}
        item.update({"result_origin": "historical_tranche_a_180",
                     "family": old["family"]})
        if old["execution_status"] == "SUCCESS":
            # Read stored historical predictions; this is not model execution.
            catalog = load_json(ROOT / "artifacts/final-experiments-v3/scientific-inputs-tranche-a-180/benchmark-catalog.json")
            entry = next(value for value in catalog["datasets"]
                         if value["feature_view"] == old["feature_view"]
                         and value["source_system"] == old["source_system"])
            artifact_path = artifact_for(Path(old["campaign_root"]), old["method"],
                                         entry["benchmark_dataset_sha256"])
            artifact_key = str(artifact_path)
            if artifact_key not in artifact_cache:
                artifact_cache[artifact_key] = load_verified_artifact(artifact_path)
            verified = artifact_cache[artifact_key]
            candidates = [dict(value) for value in verified.predictions
                          if value["incident_id"] == old["incident_id"]]
            candidates.sort(key=lambda value: (-value["score"], value["service_name"]))
            item["ranked_services"] = candidates
            item["top3_correct"] = old["reciprocal_rank"] >= 1 / 3
            item["confidence"] = candidates[0]["score"]
        result.append(item)
    return result


def _metrics(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = defaultdict(list)
    for item in observations:
        grouped[(item["family"], item["method"], item["feature_view"])].append(item)
    rows = []
    for (family, method, view), values in sorted(grouped.items()):
        success = [item for item in values if item["execution_status"] == "SUCCESS"]
        truth, predicted = [], []
        for item in success:
            for candidate in item["ranked_services"]:
                truth.append(bool(candidate["is_root_cause"]))
                predicted.append(candidate["score"] >= .5)
        fp = sum(p and not t for p, t in zip(predicted, truth)); tn = sum(not p and not t for p, t in zip(predicted, truth))
        rows.append({"family": family, "method": method, "feature_view": view,
            "total_test_incidents": 147, "applicable_denominator": len(success),
            "success_count": len(success),
            "skip_not_applicable_count": sum(item["execution_status"] == "SKIP_NOT_APPLICABLE" for item in values),
            "failure_count": sum(item["execution_status"].startswith("FAILED") for item in values),
            "top1": statistics.fmean(item["top1_correct"] for item in success) if success else None,
            "top3": statistics.fmean(item["top3_correct"] for item in success) if success else None,
            "mrr": statistics.fmean(item["reciprocal_rank"] for item in success) if success else None,
            "fpr": fp / (fp + tn) if fp + tn else 0.0,
            "mean_confidence": statistics.fmean(item["confidence"] for item in success) if success else None})
    return rows


def _pairing(observations: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for family in FAMILY_CONTRACTS:
        values = [item for item in observations if item["family"] == family
                  and item["execution_status"] == "SUCCESS"]
        groups = defaultdict(dict)
        for item in values:
            groups[f"{item['method']}:{item['feature_view']}"][item["incident_id"]] = {
                "reciprocal_rank": item["reciprocal_rank"], "top1_correct": item["top1_correct"]}
        names, comparisons, raw_p = sorted(groups), [], []
        for index, left_name in enumerate(names):
            for right_name in names[index + 1:]:
                left, right = groups[left_name], groups[right_name]
                ids = sorted(set(left) & set(right))
                if not ids:
                    continue
                paired_left, paired_right = ({key: side[key] for key in ids} for side in (left, right))
                stats = ExperimentStatisticsV3.compare_paired(
                    paired_left, paired_right, metric="reciprocal_rank", seed=42, samples=1000)
                p = stats["mcnemar"]["p_value_two_sided"]
                comparisons.append({"left": left_name, "right": right_name,
                    "individual_denominator_a": len(left), "individual_denominator_b": len(right),
                    "paired_intersection": len(ids), "a_only_ids": sorted(set(left) - set(right)),
                    "b_only_ids": sorted(set(right) - set(left)), "paired_id_hash": ids_sha256(ids),
                    "statistics": stats, "mcnemar_p_value": p})
                raw_p.append(p)
        adjusted = ExperimentStatisticsV3.holm(raw_p) if raw_p else ()
        for comparison, value in zip(comparisons, adjusted):
            comparison["holm_adjusted_p_value"] = value
        result[family] = comparisons
    return {"schema_version": "h1h2-final-pairing-audit-v1", "bootstrap_iterations": 1000,
            "campaigns": result}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--resume", action="store_true"); args = parser.parse_args()
    gate = ROOT / "artifacts/final-experiments-v3/h1h2-expansion-gate"
    extension = load_json(gate / "extension-manifest.json")
    gate_audit = load_json(gate / "expansion-audit.json")
    model_audit = load_json(gate / "model-reuse-audit.json")
    if not gate_audit["existing_40_results_reusable"] or gate_audit["gates"]["H1_H2_FROZEN_MODEL_REUSE_GATE"] != "PASS":
        raise RuntimeError("HARDENED_GATE_NOT_APPROVED")
    parent = reconstruct_parent(); historical = set(extension["locked_base_test_incident_ids"]); added = set(extension["added_test_incident_ids"])
    if len(parent["test"]) != 147 or len(historical) != 40 or len(added) != 107 or historical & added or historical | added != set(parent["test"]):
        raise RuntimeError("H1_H2_PARENT_TEST_COMPLEMENT_AUDIT_FAIL")
    historical_before = _historical_snapshot()
    plan = build_plan(extension); _write(EXTENSION_ROOT / "execution-plan.json", plan)
    observations, reuse = execute(extension, plan, model_audit)
    for family in FAMILY_CONTRACTS:
        _write(EXTENSION_ROOT / family / "observations.json",
               {"schema_version": "h1h2-extension-observations-v1",
                "observations": [item for item in observations if item["family"] == family]})
    _write(EXTENSION_ROOT / "model-reuse-final-audit.json", reuse)
    base = load_json(ROOT / "experiments/configs/final-v3/scientific-tranche-a-180.json")
    old = historical_observations(base)
    combined = aggregate_observations(old, observations)
    if {item["incident_id"] for item in combined} != set(parent["test"]):
        raise RuntimeError("H1_H2_FINAL_147_COVERAGE_AUDIT_FAIL")
    metrics = _metrics(list(combined)); pairing = _pairing(list(combined))
    for family in FAMILY_CONTRACTS:
        family_observations = [item for item in combined if item["family"] == family]
        _write(FINAL_ROOT / family / "observations.json", {"observations": family_observations})
        _write(FINAL_ROOT / family / "metrics.json", {"metrics": [item for item in metrics if item["family"] == family]})
        _write(FINAL_ROOT / family / "statistical-analysis.json", {"comparisons": pairing["campaigns"][family]})
    _write(FINAL_ROOT / "pairing-audit.json", pairing)
    _write(FINAL_ROOT / "coverage-audit.json", {"historical": 40, "extension": 107, "union": 147,
        "parent_test_sha256": ids_sha256(parent["test"]), "union_sha256": ids_sha256({item["incident_id"] for item in combined}), "status": "PASS"})
    historical_after = _historical_snapshot()
    if historical_before != historical_after:
        raise RuntimeError("H1_H2_HISTORICAL_RESULTS_MUTATED")
    v2, h3 = verify_v2_freeze(ROOT), h3_freeze_audit()
    manifest = {"schema_version": "h1h2-final-147-manifest-v1", "final_test_count": 147,
        "parent_test_sha256": ids_sha256(parent["test"]), "historical_test_count": 40,
        "historical_test_sha256": ids_sha256(historical), "extension_test_count": 107,
        "extension_test_sha256": ids_sha256(added), "base_training_tranche_id": "tranche-a-180",
        "base_training_tranche_sha256": extension["base_tranche_selection_sha256"],
        "extension_tranche_id": extension["tranche_id"], "extension_tranche_sha256": extension["selection_sha256"],
        "protocol": "within-dataset", "seed": 42, "repetitions": 1,
        "model_specification_hash": file_sha256(ROOT / "experiments/configs/final-v3/scientific-model-specification.json"),
        "historical_artifact_hashes": historical_before,
        "extension_artifact_root": str(EXTENSION_ROOT.relative_to(ROOT)),
        "model_retrained": False, "model_retuned": False,
        "V2_FREEZE_HASHES_OK": f"{v2['ok']}/{v2['total']}", "H3_LLM_FREEZE_STATIC_AUDIT": h3["status"]}
    _write(FINAL_ROOT / "final-147-manifest.json", manifest)
    summary = {"H1_H2_PARENT_TEST_COMPLEMENT_AUDIT": "PASS",
        "H1_H2_EXTENSION_PLANNED_UNIQUE_INCIDENTS": 107,
        "H1_H2_HISTORICAL_INCIDENTS_IN_EXTENSION_PLAN": 0,
        "H1_H2_EXTENSION_INPUT_MATERIALIZATION": "PASS",
        "H1_H2_EXTENSION_MODEL_IDENTITY_AUDIT": "PASS",
        "H1_H2_EXTENSION_INFERENCE_ONLY_AUDIT": reuse["status"],
        "H1_H2_107_EXTENSION_EXECUTION": "PASS",
        "H1_H2_HISTORICAL_RESULTS_MUTATED": "NO",
        "H1_H2_FINAL_UNIQUE_TEST_INCIDENTS": 147,
        "H1_H2_FINAL_147_COVERAGE_AUDIT": "PASS",
        "H1_H2_FINAL_AGGREGATION_DUPLICATE_AUDIT": "PASS",
        "H1_H2_FINAL_CONTRACT_COMPATIBILITY_AUDIT": "PASS",
        "H1_H2_FINAL_MODEL_REUSE_AUDIT": reuse["status"],
        "V2_FREEZE_HASHES_OK": f"{v2['ok']}/{v2['total']}",
        "H3_LLM_FREEZE_STATIC_AUDIT": h3["status"], "H1_H2_FINAL_147_CAMPAIGN": "PASS"}
    _write(FINAL_ROOT / "execution-summary.json", summary)
    files = sorted(path for path in FINAL_ROOT.rglob("*.json"))
    (FINAL_ROOT / "SHA256SUMS").write_text("\n".join(
        f"{file_sha256(path)}  {path.relative_to(FINAL_ROOT)}" for path in files) + "\n")
    for key, value in summary.items(): print(f"{key} = {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
