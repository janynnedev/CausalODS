#!/usr/bin/env python3
"""Inference-only H1/H2 extension execution and observation-level aggregation."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "correlation-engine")]

from multimodal_v1.benchmark_v3.core import (  # noqa: E402
    BenchmarkDataset, HeuristicEstimator, ML_MODELS, evaluate_observation_rankings,
    evaluate_scores,
    load_verified_artifact,
)
from experiments.final_campaigns_v3.h1h2_test_extension import (  # noqa: E402
    aggregate_observations, file_sha256, ids_sha256, validate_heuristic_identity,
    load_resume_checkpoint, resume_binding, resume_key, write_resume_checkpoint,
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
REQUIRED = {"GRAPH_BUSINESS": {"business_event"},
            "METRIC_ONLY": {"metric"}, "TRACE_ONLY": {"trace"},
            "METRIC_TRACE": {"metric", "trace"},
            "METRIC_TRACE_LOG": {"metric", "trace", "log"},
            "METRIC_TRACE_LOG_BUSINESS": {"metric", "trace", "log", "business_event"}}
FAMILY_CONTRACTS = {
    "H1-A": [(method, "FULL") for method in (
        "xgboost-rca-v3", "temporal-baseline", "max-zscore", "first-anomalous")],
    "H1-B": [("xgboost-rca-v3", view) for view in ("NO_GRAPH", "GRAPH", "GRAPH_BUSINESS")],
    "H1-C": [("xgboost-rca-v3", view) for view in (
        "METRIC_ONLY", "TRACE_ONLY", "METRIC_TRACE", "METRIC_TRACE_LOG", "METRIC_TRACE_LOG_BUSINESS")],
    "H2": [(method, "FULL") for method in (
        "temporal-baseline", "max-zscore", "first-anomalous", "logistic-regression",
        "random-forest", "xgboost-rca-v3", "mlp-light-v3")],
}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _historical_snapshot() -> dict[str, Any]:
    snapshot = {}
    for family, base in CAMPAIGNS.items():
        root = campaign_root(base)
        validate_sums(root)
        bound_files = {}
        for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            bound_files[relative] = expected
        snapshot[family] = {
            "sha256s_file_sha256": file_sha256(root / "SHA256SUMS"),
            "bound_files": bound_files,
        }
    return snapshot


def _capabilities() -> dict[str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts/scientific-datasets-v3/rcaeval/capabilities.json")
    return {item["incident_id"]: item for item in payload["capabilities"]}


def _catalog() -> dict[tuple[str, str], dict[str, Any]]:
    payload = load_json(INPUT_ROOT / "benchmark-catalog.json")
    by_view_system = {}
    for item in payload["datasets"]:
        if item["protocol"] != "within-dataset" or item["seed"] != 42:
            continue
        key = (item["feature_view"], item["source_system"])
        if key in by_view_system:
            raise RuntimeError("H1_H2_EXTENSION_SOURCE_SYSTEM_BINDING_AMBIGUOUS")
        if item.get("held_out") is not None:
            raise RuntimeError("H1_H2_EXTENSION_SPLIT_BINDING_INVALID")
        by_view_system[key] = item
    return by_view_system


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


def _resume_expected_observation(
    row: dict[str, Any], extension: dict[str, Any], entry: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Bind one planned observation to its complete frozen scientific identity."""
    learned = row["method"] in ML_MODELS
    model = contract.get("expected", {}) if learned and contract is not None else {}
    expected = {
        **row,
        "expanded_evaluation_tranche_sha256": extension["selection_sha256"],
        "feature_schema_sha256": entry["feature_view_sha256"],
        "trained_model_artifact_hash": model.get("serialized_artifact_sha256"),
        "method_implementation_hash": (
            None if learned else contract["expected_method_implementation_hash"]),
        "model_specification_hash": model.get(
            "model_specification_hash",
            file_sha256(ROOT / "experiments/configs/final-v3/scientific-model-specification.json"),
        ),
        "training_split_sha256": model.get(
            "training_split_hash", entry["train_incident_ids_sha256"]),
        "validation_split_sha256": model.get(
            "validation_split_hash", entry["validation_incident_ids_sha256"]),
        "expanded_test_split_sha256": extension["expanded_test_incident_ids_sha256"],
    }
    if learned and (
        expected["feature_schema_sha256"] != model.get("feature_schema_sha256")
        or expected["training_split_sha256"] != entry["train_incident_ids_sha256"]
        or expected["validation_split_sha256"] != entry["validation_incident_ids_sha256"]
    ):
        raise RuntimeError("H1_H2_EXTENSION_RESUME_MODEL_DATASET_BINDING_MISMATCH")
    return expected


def _expected_skip_observation(
    row: dict[str, Any], extension: dict[str, Any], contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build provenance for an inapplicable run without inventing a dataset.

    No evaluation feature schema is bound.  Learned methods retain their frozen
    artifact/training identity; heuristics retain their implementation identity.
    """
    learned = row["method"] in ML_MODELS
    if not learned and contract is None:
        raise RuntimeError("H1_H2_SKIP_HEURISTIC_CONTRACT_MISSING")
    model = contract.get("expected", {}) if learned and contract is not None else {}
    return {
        **row,
        "expanded_evaluation_tranche_sha256": extension["selection_sha256"],
        "feature_schema_sha256": None,
        "trained_model_artifact_hash": model.get("serialized_artifact_sha256"),
        "method_implementation_hash": (
            None if learned else contract["expected_method_implementation_hash"]),
        "model_specification_hash": model.get(
            "model_specification_hash",
            file_sha256(ROOT / "experiments/configs/final-v3/scientific-model-specification.json"),
        ),
        "training_split_sha256": model.get("training_split_hash"),
        "validation_split_sha256": model.get("validation_split_hash"),
        "expanded_test_split_sha256": extension["expanded_test_incident_ids_sha256"],
    }


def _learned_contract_coverage_audit(
    plan: dict[str, Any], model_audit: dict[str, Any],
) -> dict[str, Any]:
    dimensions = ("family", "method", "feature_view")
    applicable = {tuple(row[key] for key in dimensions) for row in plan["runs"]
                  if row["applicability"] == "APPLICABLE" and row["method"] in ML_MODELS}
    skipped = {tuple(row[key] for key in dimensions) for row in plan["runs"]
               if row["applicability"] == "SKIP_NOT_APPLICABLE"
               and row["method"] in ML_MODELS}
    available = {(item["family"], item["method"], item["feature_view"])
                 for item in model_audit.get("learned_contracts", ())}
    missing_applicable = sorted(applicable - available)
    missing_skip_only = sorted((skipped - applicable) - available)
    return {
        "applicable_learned_contracts_required": [list(item) for item in sorted(applicable)],
        "applicable_learned_contracts_available": [
            list(item) for item in sorted(applicable & available)],
        "available_learned_contracts": [list(item) for item in sorted(available)],
        "missing_applicable_learned_contracts": [list(item) for item in missing_applicable],
        "skip_only_missing_learned_contracts": [list(item) for item in missing_skip_only],
        "status": "PASS" if not missing_applicable else "FAIL",
    }


def _quarantine_resume_checkpoint(path: Path, checkpoint_root: Path) -> Path:
    """Move an invalid checkpoint aside without overwriting recovery evidence."""
    recovery = checkpoint_root / "quarantine" / path.stem
    recovery.mkdir(parents=True, exist_ok=True)
    target = recovery / f"{file_sha256(path)}.json"
    if target.exists():
        raise RuntimeError(f"H1_H2_RESUME_QUARANTINE_CONFLICT:{target}")
    path.replace(target)
    return target


def selective_resume_implementation_audit() -> dict[str, Any]:
    """Derive the wiring gate; behavioral tests exercise each reported branch."""
    signature = inspect.signature(execute)
    source = inspect.getsource(execute)
    checks = {
        "explicit_resume_parameter": "resume" in signature.parameters,
        "checkpoint_load_wired": "load_resume_checkpoint(" in source,
        "checkpoint_write_wired": "write_resume_checkpoint(" in source,
        "pending_only_batching_wired": "pending_rows" in source,
        "invalid_quarantine_wired": "_quarantine_resume_checkpoint(" in source,
        "catalog_independent_skip_wired": "_expected_skip_observation(" in source,
        "resume_audit_wired": "resume-audit.json" in inspect.getsource(main),
    }
    return {"checks": checks, "behavioral_test_contract": ["A", "B", "C", "D"],
            "status": "PASS" if all(checks.values()) else "FAIL"}


def execute(extension: dict[str, Any], plan: dict[str, Any],
            model_audit: dict[str, Any], *, resume: bool,
            checkpoint_root: Path | None = None,
            resume_audit_path: Path | None = None,
            ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checkpoint_root = checkpoint_root or (EXTENSION_ROOT / "checkpoints")
    resume_audit_path = resume_audit_path or (EXTENSION_ROOT / "resume-audit.json")
    by_view_system = _catalog()
    caps = _capabilities()
    learned = {(item["family"], item["method"], item["feature_view"]): item
               for item in model_audit["learned_contracts"]}
    heuristic = {item["method"]: item for item in model_audit["heuristic_contracts"]}
    datasets, artifacts, models = {}, {}, {}
    guard = TrainingCallGuard()
    observations, inference_counts = [], defaultdict(int)
    counts = {"valid_success_reused": 0, "valid_skips_reused_or_reconstructed": 0,
              "missing_executed": 0, "invalid_quarantined": 0,
              "inference_observations_executed": 0}
    structural = sum(item["planned"] for item in plan["counts"].values())
    applicable = sum(item["applicable"] for item in plan["counts"].values())
    skips = sum(item["expected_structured_skips"] for item in plan["counts"].values())
    try:
        for family in FAMILY_CONTRACTS:
            family_rows = [row for row in plan["runs"] if row["family"] == family]
            for row in family_rows:
                if row["applicability"] == "APPLICABLE":
                    continue
                capability = caps[row["incident_id"]]
                if _applicable(row["feature_view"], capability):
                    raise RuntimeError("H1_H2_FALSE_STRUCTURED_SKIP")
                if row["method"] in ML_MODELS:
                    contract = learned.get((family, row["method"], row["feature_view"]))
                else:
                    contract = heuristic.get(row["method"])
                    if contract is None:
                        raise RuntimeError("H1_H2_SKIP_HEURISTIC_CONTRACT_MISSING")
                expected = _expected_skip_observation(row, extension, contract)
                observations.append({**expected, "execution_status": "SKIP_NOT_APPLICABLE",
                                     "result_origin": "test_extension_147"})
                counts["valid_skips_reused_or_reconstructed"] += 1
            for method, view in FAMILY_CONTRACTS[family]:
                contract_rows = [row for row in family_rows
                                 if row["method"] == method and row["feature_view"] == view
                                 and row["applicability"] == "APPLICABLE"]
                for source_system in sorted({row["source_system"] for row in contract_rows}):
                    group_rows = sorted(
                        (row for row in contract_rows if row["source_system"] == source_system),
                        key=lambda row: row["incident_id"])
                    if method in ML_MODELS:
                        contract = learned.get((family, method, view))
                        if contract is None:
                            raise RuntimeError(
                                "H1_H2_APPLICABLE_LEARNED_CONTRACT_MISSING:"
                                f"{family}:{method}:{view}")
                    else:
                        contract = heuristic.get(method)
                        if contract is None:
                            raise RuntimeError(
                                f"H1_H2_APPLICABLE_HEURISTIC_CONTRACT_MISSING:{method}")
                    entry = by_view_system.get((view, source_system))
                    if entry is None:
                        raise RuntimeError("H1_H2_EXTENSION_SOURCE_SYSTEM_BINDING_MISSING")
                    if (entry.get("source_system") != source_system
                            or entry.get("protocol") != "within-dataset" or entry.get("seed") != 42
                            or entry.get("held_out") is not None):
                        raise RuntimeError("H1_H2_EXTENSION_SPLIT_BINDING_INVALID")
                    path = ROOT / entry["path"]
                    dataset = datasets.setdefault(str(path), BenchmarkDataset.from_json(path))
                    x_test, _, test_rows = dataset.select("test")
                    group_ids = {row["incident_id"] for row in group_rows}
                    indexes_by_id = {item.incident_id: index for index, item in enumerate(test_rows)
                                     if item.incident_id in group_ids}
                    if (set(indexes_by_id) != group_ids
                            or any(caps[item]["source_system"] != source_system for item in group_ids)):
                        raise RuntimeError("H1_H2_EXTENSION_SOURCE_SYSTEM_BINDING_MISMATCH")
                    if method in ML_MODELS:
                        artifact_path = ROOT / contract["artifact_dir"]
                        verified = artifacts.setdefault(
                            str(artifact_path), load_verified_artifact(artifact_path))
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
                    else:
                        actual_hash = hashlib.sha256(
                            f"benchmark-v3:{method}:HeuristicEstimator".encode()).hexdigest()
                        validate_heuristic_identity(
                            contract["expected_method_implementation_hash"], actual_hash)
                        model = HeuristicEstimator(method, dataset.feature_names)
                    contract_key = contract["contract_key"]
                    expected_by_id = {row["incident_id"]: _resume_expected_observation(
                        row, extension, entry, contract) for row in group_rows}
                    pending_rows = []
                    missing_ids = set()
                    for row in group_rows:
                        expected = expected_by_id[row["incident_id"]]
                        checkpoint = checkpoint_root / f"{resume_key(expected)}.json"
                        if resume:
                            cached = load_resume_checkpoint(checkpoint, expected)
                            if cached is not None:
                                observations.append({key: value for key, value in cached.items()
                                                     if key != "checkpoint_sha256"})
                                counts["valid_success_reused"] += 1
                                continue
                            if checkpoint.exists():
                                _quarantine_resume_checkpoint(checkpoint, checkpoint_root)
                                counts["invalid_quarantined"] += 1
                            else:
                                missing_ids.add(row["incident_id"])
                        elif checkpoint.exists():
                            raise RuntimeError("H1_H2_RESUME_CHECKPOINT_EXISTS_USE_RESUME")
                        else:
                            missing_ids.add(row["incident_id"])
                        pending_rows.append(row)
                    if not pending_rows:
                        continue
                    pending_indexes = [indexes_by_id[row["incident_id"]] for row in pending_rows]
                    scoped_rows = tuple(test_rows[index] for index in pending_indexes)
                    scores = inference_only_scores(model, x_test[pending_indexes], guard)
                    inference_counts[contract_key] += len(pending_rows)
                    counts["inference_observations_executed"] += len(pending_rows)
                    evaluated = evaluate_scores(
                        scores, scoped_rows, inference_seconds=0.0, memory_peak_bytes=0,
                        model_size_bytes=0, training_seconds=0.0)
                    predictions = {item["incident_id"]: item
                                   for item in evaluated["incident_predictions"]}
                    if set(predictions) != {row["incident_id"] for row in pending_rows}:
                        raise RuntimeError("H1_H2_EXTENSION_PREDICTION_ACCOUNTING_MISMATCH")
                    for row in pending_rows:
                        prediction = predictions[row["incident_id"]]
                        ranking = list(prediction["ranking"])
                        reciprocal = prediction["reciprocal_rank"]
                        observation = {
                            **expected_by_id[row["incident_id"]],
                            "execution_status": "SUCCESS", "result_origin": "test_extension_147",
                            "predicted_service": ranking[0]["service_name"],
                            "ranked_services": ranking, "top1_correct": reciprocal == 1.0,
                            "top3_correct": reciprocal >= 1 / 3,
                            "reciprocal_rank": reciprocal, "confidence": ranking[0]["score"],
                            "base_training_tranche_id": "tranche-a-180",
                            "base_training_tranche_sha256": extension["base_tranche_selection_sha256"],
                            "expanded_evaluation_tranche_id": extension["tranche_id"],
                            "base_test_split_sha256": extension["base_test_incident_ids_sha256"],
                            "model_retrained": False, "model_retuned": False,
                        }
                        checkpoint = checkpoint_root / f"{resume_key(observation)}.json"
                        write_resume_checkpoint(checkpoint, observation)
                        observations.append(observation)
                        if row["incident_id"] in missing_ids:
                            counts["missing_executed"] += 1
            summary = {state: sum(row["execution_status"] == state for row in observations
                                  if row["family"] == family) for state in (
                                      "SUCCESS", "SKIP_NOT_APPLICABLE", "FAILED_INPUT",
                                      "FAILED_EXECUTION", "FAILED_VALIDATION")}
            print(canonical_json(
                {"stage": "extension-progress", "family": family, **summary}), flush=True)
        if len(observations) != structural:
            raise RuntimeError("EXTENSION_EXECUTION_ACCOUNTING_MISMATCH")
    finally:
        terminal = len(observations)
        audit = {"schema_version": "h1h2-extension-selective-resume-audit-v1",
                 "resume_requested": resume, "structural_planned": structural,
                 "applicable_planned": applicable, "structured_skips": skips, **counts,
                 "observations_terminal": terminal,
                 "observations_remaining": structural - terminal, **guard.counters}
        audit["status"] = "PASS" if (
            audit["observations_remaining"] == 0
            and counts["valid_skips_reused_or_reconstructed"] == skips
            and all(value == 0 for value in guard.counters.values())) else "FAIL"
        _write(resume_audit_path, audit)
    reuse = {"schema_version": "h1h2-final-model-reuse-audit-v2",
             "distinct_learned_contracts_used": len(learned),
             "inference_count_per_contract": dict(inference_counts), **guard.counters,
             "model_retrained": False, "model_retuned": False,
             "selective_resume": audit,
             "status": "PASS" if audit["status"] == "PASS" else "FAIL"}
    return observations, reuse


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
        canonical = evaluate_observation_rankings(success)["metrics"]
        rows.append({"family": family, "method": method, "feature_view": view,
            "total_test_incidents": 147, "applicable_denominator": len(success),
            "success_count": len(success),
            "skip_not_applicable_count": sum(item["execution_status"] == "SKIP_NOT_APPLICABLE" for item in values),
            "failure_count": sum(item["execution_status"].startswith("FAILED") for item in values),
            "top1": canonical.get("top_1"), "top3": canonical.get("top_3"),
            "mrr": canonical.get("mrr"), "fpr": canonical.get("fpr"),
            "precision": canonical.get("precision"), "recall": canonical.get("recall"),
            "macro_f1": canonical.get("macro_f1"),
            "mean_confidence": (sum(item["confidence"] for item in success) / len(success)
                                if success else None)})
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


def preflight(extension: dict[str, Any], model_audit: dict[str, Any],
              *, v2: dict[str, Any] | None = None,
              h3: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate frozen bindings only; this function never calls inference."""
    parent = reconstruct_parent(); plan = build_plan(extension)
    complement = (set(extension["added_test_incident_ids"]) | set(extension["locked_base_test_incident_ids"])
                  == set(parent["test"]) and not (set(extension["added_test_incident_ids"])
                  & set(extension["locked_base_test_incident_ids"])))
    result = {"schema_version": "h1h2-final-147-preflight-v1", "plan": plan["counts"],
              "inference_calls": 0,
              "H1_H2_PARENT_TEST_COMPLEMENT_AUDIT": "PASS" if complement else "FAIL"}
    try:
        catalog = _catalog(); caps = _capabilities(); missing = []; mismatches = []
        checked, systems, materialized = 0, set(), True
        split_ids_by_path = {}
        for row in plan["runs"]:
            if row["applicability"] != "APPLICABLE": continue
            checked += 1; systems.add(row["source_system"])
            entry = catalog.get((row["feature_view"], row["source_system"]))
            if entry is None: missing.append((row["feature_view"], row["source_system"])); continue
            dataset_path = ROOT / entry["path"]
            if dataset_path not in split_ids_by_path:
                dataset = BenchmarkDataset.from_json(dataset_path)
                split_ids_by_path[dataset_path] = {
                    item.incident_id for item in dataset.rows if item.split == "test"}
            split_ids = split_ids_by_path[dataset_path]
            if (caps[row["incident_id"]]["source_system"] != row["source_system"]
                    or entry["source_system"] != row["source_system"]): mismatches.append(row["incident_id"])
            if row["incident_id"] not in split_ids: materialized = False
        source_ok = not missing and not mismatches
        result["source_system_binding"] = {"contracts_checked": len(catalog),
            "applicable_observations_checked": checked, "source_systems": sorted(systems),
            "missing_bindings": missing, "mismatches": mismatches,
            "status": "PASS" if source_ok else "FAIL"}
        result["split_binding"] = {"status": "PASS" if source_ok and materialized else "FAIL"}
        result["input_materialization"] = {"status": "PASS" if materialized and source_ok else "FAIL"}
    except Exception as exc:
        failure = {"status": "FAIL", "error": f"{type(exc).__name__}:{exc}"}
        result.update({"source_system_binding": failure, "split_binding": failure,
                       "input_materialization": failure})
    contract_coverage = _learned_contract_coverage_audit(plan, model_audit)
    identity_ok = (contract_coverage["status"] == "PASS" and all(
                      item.get("identity_validation") == "PASS"
                      for item in model_audit.get("learned_contracts", ())) and all(
                      item.get("identity_validation") == "PASS"
                      for item in model_audit.get("heuristic_contracts", ())))
    result["model_identity"] = {
        **contract_coverage, "status": "PASS" if identity_ok else "FAIL"}
    guard = TrainingCallGuard()
    result["inference_only_readiness"] = {**guard.counters,
        "status": "PASS" if all(value == 0 for value in guard.counters.values()) else "FAIL"}
    try:
        historical_snapshot = _historical_snapshot()
        historical_status = bool(historical_snapshot) and all(
            item["bound_files"] for item in historical_snapshot.values())
        result["historical_immutability"] = {
            "snapshot": historical_snapshot,
            "status": "PASS" if historical_status else "FAIL"}
    except Exception as exc:
        result["historical_immutability"] = {
            "status": "FAIL", "error": f"{type(exc).__name__}:{exc}"}
    metric_wiring = (callable(evaluate_observation_rankings)
                     and "evaluate_observation_rankings(" in inspect.getsource(_metrics))
    result["authoritative_metrics"] = {
        "callable": callable(evaluate_observation_rankings),
        "observation_aggregator_wired": metric_wiring,
        "status": "PASS" if metric_wiring else "FAIL"}
    v2 = v2 or verify_v2_freeze(ROOT)
    h3 = h3 or h3_freeze_audit()
    result["v2_freeze"] = {"ok": v2["ok"], "total": v2["total"],
                            "status": "PASS" if v2["valid"] else "FAIL"}
    result["h3_static_freeze"] = {**h3, "status": h3["status"]}
    result["selective_resume_implementation"] = selective_resume_implementation_audit()
    input_ready = (result["H1_H2_PARENT_TEST_COMPLEMENT_AUDIT"] == "PASS"
                   and all(result[key]["status"] == "PASS" for key in (
                       "source_system_binding", "split_binding", "input_materialization",
                       "model_identity", "inference_only_readiness",
                       "historical_immutability", "authoritative_metrics",
                       "v2_freeze", "h3_static_freeze")))
    result["input_readiness"] = "PASS" if input_ready else "FAIL"
    execution_ready = (input_ready
                       and result["selective_resume_implementation"]["status"] == "PASS")
    result["execution_readiness"] = "PASS" if execution_ready else "FAIL"
    result["status"] = result["execution_readiness"]
    return result


def _contract_compatibility_audit(
    observations: list[dict[str, Any]], extension: dict[str, Any],
    model_audit: dict[str, Any],
) -> dict[str, Any]:
    catalog = _catalog()
    learned = {(item["family"], item["method"], item["feature_view"]): item
               for item in model_audit["learned_contracts"]}
    heuristic = {item["method"]: item for item in model_audit["heuristic_contracts"]}
    capabilities = _capabilities()
    mismatches = []
    for observation in observations:
        if observation.get("execution_status") == "SKIP_NOT_APPLICABLE":
            capability = capabilities.get(observation["incident_id"])
            if capability is None or _applicable(observation["feature_view"], capability):
                mismatches.append({"identity": resume_key(observation),
                                   "reason": "false_structured_skip"})
                continue
            if observation["method"] in ML_MODELS:
                contract = learned.get((observation["family"], observation["method"],
                                        observation["feature_view"]))
            else:
                contract = heuristic.get(observation["method"])
                if contract is None:
                    mismatches.append({"identity": resume_key(observation),
                                       "reason": "heuristic_method_binding"})
                    continue
            expected = _expected_skip_observation(observation, extension, contract)
        elif observation.get("execution_status") == "SUCCESS":
            contract = (learned.get((observation["family"], observation["method"],
                                     observation["feature_view"]))
                        if observation["method"] in ML_MODELS
                        else heuristic.get(observation["method"]))
            if contract is None:
                mismatches.append({"identity": resume_key(observation),
                                   "reason": "method_binding"})
                continue
            entry = catalog.get((observation["feature_view"], observation["source_system"]))
            if entry is None:
                mismatches.append({"identity": resume_key(observation),
                                   "reason": "catalog_binding"})
                continue
            expected = _resume_expected_observation(observation, extension, entry, contract)
        else:
            mismatches.append({"identity": resume_key(observation),
                               "reason": "non_terminal_or_failed_status"})
            continue
        if resume_binding(observation) != resume_binding(expected):
            mismatches.append({"identity": resume_key(observation), "reason": "scientific_binding"})
    return {"schema_version": "h1h2-final-contract-compatibility-audit-v1",
            "observations_checked": len(observations), "mismatches": mismatches,
            "status": "PASS" if observations and not mismatches else "FAIL"}


def _final_campaign_exit_code(final_campaign_pass: bool) -> int:
    return 0 if final_campaign_pass else 1


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--resume", action="store_true"); parser.add_argument("--preflight", action="store_true"); args = parser.parse_args()
    gate = ROOT / "artifacts/final-experiments-v3/h1h2-expansion-gate"
    validate_sums(gate)
    extension = load_json(gate / "extension-manifest.json")
    gate_audit = load_json(gate / "expansion-audit.json")
    model_audit = load_json(gate / "model-reuse-audit.json")
    if not gate_audit["existing_40_results_reusable"] or gate_audit["gates"]["H1_H2_FROZEN_MODEL_REUSE_GATE"] != "PASS":
        raise RuntimeError("HARDENED_GATE_NOT_APPROVED")
    v2, h3 = verify_v2_freeze(ROOT), h3_freeze_audit()
    if not v2["valid"]: raise RuntimeError("V2_FREEZE_FAIL")
    if h3["status"] != "PASS": raise RuntimeError("H3_LLM_FREEZE_STATIC_AUDIT_FAIL")
    if args.preflight:
        input_sums_before = file_sha256(INPUT_ROOT / "SHA256SUMS")
        audit = preflight(extension, model_audit, v2=v2, h3=h3)
        input_sums_after = file_sha256(INPUT_ROOT / "SHA256SUMS")
        audit["input_sha256s_immutable"] = {
            "before": input_sums_before, "after": input_sums_after,
            "status": "PASS" if input_sums_before == input_sums_after else "FAIL"}
        if audit["input_sha256s_immutable"]["status"] != "PASS":
            audit["input_readiness"] = audit["execution_readiness"] = audit["status"] = "FAIL"
        _write(EXTENSION_ROOT / "preflight-audit.json", audit)
        print(f"H1_H2_FINAL_147_INPUT_READINESS = {audit['input_readiness']}")
        print(f"H1_H2_EXTENSION_SOURCE_SYSTEM_BINDING_AUDIT = {audit['source_system_binding']['status']}")
        print(f"H1_H2_EXTENSION_SPLIT_BINDING_AUDIT = {audit['split_binding']['status']}")
        print(f"H1_H2_EXTENSION_INPUT_MATERIALIZATION_AUDIT = {audit['input_materialization']['status']}")
        print(f"H1_H2_EXTENSION_MODEL_IDENTITY_AUDIT = {audit['model_identity']['status']}")
        print(f"H1_H2_EXTENSION_INFERENCE_ONLY_READINESS_AUDIT = {audit['inference_only_readiness']['status']}")
        print(f"H1_H2_HISTORICAL_IMMUTABILITY_PRECHECK = {audit['historical_immutability']['status']}")
        print(f"H1_H2_AUTHORITATIVE_METRICS_BINDING = {audit['authoritative_metrics']['status']}")
        print(f"H1_H2_EXTENSION_SELECTIVE_RESUME_IMPLEMENTATION_AUDIT = {audit['selective_resume_implementation']['status']}")
        print(f"V2_FREEZE_HASHES_OK = {v2['ok']}/{v2['total']}")
        print(f"H3_LLM_FREEZE_STATIC_AUDIT = {h3['status']}")
        print(f"H1_H2_FINAL_147_EXECUTION_READINESS = {audit['execution_readiness']}")
        print("H1_H2_FULL_107_EXECUTION_PERFORMED = NO")
        print("FINAL_H1_H2_INFERENCE_PERFORMED = NO")
        return 0 if audit["status"] == "PASS" else 1
    parent = reconstruct_parent(); historical = set(extension["locked_base_test_incident_ids"]); added = set(extension["added_test_incident_ids"])
    if len(parent["test"]) != 147 or len(historical) != 40 or len(added) != 107 or historical & added or historical | added != set(parent["test"]):
        raise RuntimeError("H1_H2_PARENT_TEST_COMPLEMENT_AUDIT_FAIL")
    input_sums_before = file_sha256(INPUT_ROOT / "SHA256SUMS")
    runtime_preflight = preflight(extension, model_audit, v2=v2, h3=h3)
    if runtime_preflight["execution_readiness"] != "PASS":
        raise RuntimeError("H1_H2_FINAL_147_EXECUTION_READINESS_FAIL")
    historical_before = _historical_snapshot()
    plan = build_plan(extension); _write(EXTENSION_ROOT / "execution-plan.json", plan)
    observations, reuse = execute(
        extension, plan, model_audit, resume=args.resume,
        checkpoint_root=EXTENSION_ROOT / "checkpoints",
        resume_audit_path=EXTENSION_ROOT / "resume-audit.json")
    for family in FAMILY_CONTRACTS:
        _write(EXTENSION_ROOT / family / "observations.json",
               {"schema_version": "h1h2-extension-observations-v1",
                "observations": [item for item in observations if item["family"] == family]})
    _write(EXTENSION_ROOT / "model-reuse-final-audit.json", reuse)
    base = load_json(ROOT / "experiments/configs/final-v3/scientific-tranche-a-180.json")
    old = historical_observations(base)
    combined = aggregate_observations(old, observations)
    union_ids = {item["incident_id"] for item in combined}
    coverage_ok = union_ids == set(parent["test"])
    if not coverage_ok:
        raise RuntimeError("H1_H2_FINAL_147_COVERAGE_AUDIT_FAIL")
    metrics = _metrics(list(combined)); pairing = _pairing(list(combined))
    for family in FAMILY_CONTRACTS:
        family_observations = [item for item in combined if item["family"] == family]
        _write(FINAL_ROOT / family / "observations.json", {"observations": family_observations})
        _write(FINAL_ROOT / family / "metrics.json", {"metrics": [item for item in metrics if item["family"] == family]})
        _write(FINAL_ROOT / family / "statistical-analysis.json", {"comparisons": pairing["campaigns"][family]})
    _write(FINAL_ROOT / "pairing-audit.json", pairing)
    _write(FINAL_ROOT / "coverage-audit.json", {"historical": len(historical),
        "extension": len(added), "union": len(union_ids),
        "parent_test_sha256": ids_sha256(parent["test"]),
        "union_sha256": ids_sha256(union_ids),
        "status": "PASS" if coverage_ok else "FAIL"})
    historical_after = _historical_snapshot()
    historical_unchanged = historical_before == historical_after
    input_sums_after = file_sha256(INPUT_ROOT / "SHA256SUMS")
    inputs_unchanged = input_sums_before == input_sums_after
    if not historical_unchanged:
        raise RuntimeError("H1_H2_HISTORICAL_RESULTS_MUTATED")
    if not inputs_unchanged:
        raise RuntimeError("H1_H2_FINAL_147_INPUTS_MUTATED")
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
    identities = [(item.get("family"), item.get("incident_id"), item.get("method"),
                   item.get("feature_view"), item.get("protocol"), item.get("seed"))
                  for item in combined]
    duplicate_ok = len(identities) == len(set(identities))
    contract_audit = _contract_compatibility_audit(observations, extension, model_audit)
    _write(FINAL_ROOT / "contract-compatibility-audit.json", contract_audit)
    structural = sum(value["planned"] for value in plan["counts"].values())
    applicable = sum(value["applicable"] for value in plan["counts"].values())
    skips = sum(value["expected_structured_skips"] for value in plan["counts"].values())
    success = sum(item["execution_status"] == "SUCCESS" for item in observations)
    skipped = sum(item["execution_status"] == "SKIP_NOT_APPLICABLE" for item in observations)
    failures = sum(item["execution_status"].startswith("FAILED") for item in observations)
    complement_ok = (len(parent["test"]) == 147 and len(historical) == 40 and len(added) == 107
                     and not historical & added and historical | added == set(parent["test"]))
    execution_ok = (structural == 2033 and applicable == 1612 and skips == 421
                    and success == applicable and skipped == skips and failures == 0
                    and {item["incident_id"] for item in observations} == added
                    and reuse["selective_resume"]["status"] == "PASS")
    inference_only_ok = (reuse["status"] == "PASS"
                         and all(reuse[name] == 0 for name in (
                             "training_calls", "tuning_calls", "fit_calls", "partial_fit_calls"))
                         and not reuse["model_retrained"] and not reuse["model_retuned"])
    mandatory = {
        "complement": complement_ok,
        "input_materialization": runtime_preflight["input_materialization"]["status"] == "PASS",
        "model_identity": runtime_preflight["model_identity"]["status"] == "PASS",
        "inference_only": inference_only_ok,
        "extension_execution": execution_ok,
        "historical_immutable": historical_unchanged,
        "inputs_immutable": inputs_unchanged,
        "coverage": coverage_ok,
        "duplicates": duplicate_ok,
        "contract_compatibility": contract_audit["status"] == "PASS",
        "model_reuse": reuse["status"] == "PASS",
        "v2": v2["valid"], "h3": h3["status"] == "PASS",
    }
    final_campaign_pass = all(mandatory.values())
    summary = {"H1_H2_PARENT_TEST_COMPLEMENT_AUDIT": "PASS" if complement_ok else "FAIL",
        "H1_H2_EXTENSION_PLANNED_UNIQUE_INCIDENTS": plan["planned_unique_incidents"],
        "H1_H2_HISTORICAL_INCIDENTS_IN_EXTENSION_PLAN": plan["historical_incidents_in_plan"],
        "H1_H2_EXTENSION_INPUT_MATERIALIZATION": runtime_preflight["input_materialization"]["status"],
        "H1_H2_EXTENSION_MODEL_IDENTITY_AUDIT": runtime_preflight["model_identity"]["status"],
        "H1_H2_EXTENSION_INFERENCE_ONLY_AUDIT": "PASS" if inference_only_ok else "FAIL",
        "H1_H2_107_EXTENSION_EXECUTION": "PASS" if execution_ok else "NOT_PASS",
        "H1_H2_HISTORICAL_RESULTS_MUTATED": "NO" if historical_unchanged else "YES",
        "H1_H2_FINAL_UNIQUE_TEST_INCIDENTS": len(union_ids),
        "H1_H2_FINAL_147_COVERAGE_AUDIT": "PASS" if coverage_ok else "FAIL",
        "H1_H2_FINAL_AGGREGATION_DUPLICATE_AUDIT": "PASS" if duplicate_ok else "FAIL",
        "H1_H2_FINAL_CONTRACT_COMPATIBILITY_AUDIT": contract_audit["status"],
        "H1_H2_FINAL_MODEL_REUSE_AUDIT": reuse["status"],
        "V2_FREEZE_HASHES_OK": f"{v2['ok']}/{v2['total']}",
        "H3_LLM_FREEZE_STATIC_AUDIT": h3["status"],
        "H1_H2_FINAL_147_CAMPAIGN": "PASS" if final_campaign_pass else "NOT_PASS"}
    _write(FINAL_ROOT / "execution-summary.json", summary)
    files = sorted(path for path in FINAL_ROOT.rglob("*.json"))
    (FINAL_ROOT / "SHA256SUMS").write_text("\n".join(
        f"{file_sha256(path)}  {path.relative_to(FINAL_ROOT)}" for path in files) + "\n")
    for key, value in summary.items(): print(f"{key} = {value}")
    return _final_campaign_exit_code(final_campaign_pass)


if __name__ == "__main__":
    raise SystemExit(main())
