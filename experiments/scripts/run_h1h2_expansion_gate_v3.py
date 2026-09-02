#!/usr/bin/env python3
"""Build and execute the H1/H2 expansion gate (never the full extension)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "correlation-engine"))

import multimodal_v1.benchmark_v3.core as benchmark_core  # noqa: E402
from multimodal_v1.benchmark_v3.core import (  # noqa: E402
    BenchmarkDataset, HeuristicEstimator, ML_MODELS, evaluate_scores,
    load_verified_artifact, positive_scores,
)
from experiments.final_campaigns_v3.core import build_experiment_config, load_campaign_config  # noqa: E402
from experiments.final_campaigns_v3.h3_freeze_audit import audit as h3_freeze_audit  # noqa: E402
from experiments.final_campaigns_v3.h1h2_test_extension import (  # noqa: E402
    STRICT_REPRODUCIBILITY_TOLERANCE, build_extension_manifest, build_spot_selection,
    compare_spot_records_detailed, file_sha256, ids_sha256, validate_extension_manifest,
    validate_heuristic_identity, validate_model_identity,
)
from experiments.multimodal_v3.core import (  # noqa: E402
    DatasetCapabilityV3, ExperimentPlannerV3, canonical_json, sha256_value,
    verify_v2_freeze,
)

CAMPAIGNS = {
    "H1-A": ROOT / "artifacts/final-experiments-v3-h1a-recovery/H1-A/final-h1a-v3",
    "H1-B": ROOT / "artifacts/final-experiments-v3-h1b/H1-B/final-h1b-v3",
    "H1-C": ROOT / "artifacts/final-experiments-v3-h1c/H1-C/final-h1c-v3",
    "H2": ROOT / "artifacts/final-experiments-v3-h2/H2/final-h2-v3",
}
FROZEN_SPOT_IDS = (
    "rcaeval:re1ob_cartservice_disk_5",
    "rcaeval:re1tt_ts-order-service_mem_1",
    "rcaeval:re2ob_productcatalogservice_cpu_2",
    "rcaeval:re2tt_ts-order-service_socket_3",
    "rcaeval:re3ss_carts_f3_4",
)
FROZEN_SPOT_SHA256 = "dd42987c9075602f7b382b581e32aa4c40b760ee53f10626a76bf1d0dcbbb922"


class TrainingCallGuard:
    def __init__(self):
        self.counters = {"training_calls": 0, "fit_calls": 0,
                         "partial_fit_calls": 0, "tuning_calls": 0}

    def block(self, counter: str):
        def forbidden(*_args, **_kwargs):
            self.counters[counter] += 1
            raise RuntimeError(f"H1_H2_FORBIDDEN_TRAINING_ENTRYPOINT:{counter}")
        return forbidden


def inference_only_scores(model: Any, matrix: np.ndarray,
                          guard: TrainingCallGuard) -> np.ndarray:
    with (patch.object(benchmark_core, "train_and_evaluate", guard.block("training_calls")),
          patch.object(benchmark_core, "tune_model", guard.block("tuning_calls")),
          patch.object(Pipeline, "fit", guard.block("fit_calls")),
          patch.object(HeuristicEstimator, "fit", guard.block("fit_calls")),
          patch.object(MLPClassifier, "partial_fit", guard.block("partial_fit_calls"))):
        return positive_scores(model, matrix)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_frozen_spot_selection(spot: dict[str, Any]) -> None:
    if (tuple(spot.get("selected_incident_ids", ())) != FROZEN_SPOT_IDS
            or spot.get("selection_sha256") != FROZEN_SPOT_SHA256):
        raise RuntimeError("H1_H2_SPOT_CHECK_SELECTION_IDENTITY_FAIL")


def bind_model_identity(expected: dict[str, Any], actual: dict[str, Any],
                        identity_validator=validate_model_identity) -> None:
    """Production binding point; actual must be independently reconstructed."""
    if expected is actual:
        raise RuntimeError("H1_H2_MODEL_IDENTITY_SELF_COMPARISON")
    identity_validator(expected, actual)


def reconstruct_parent() -> dict[str, tuple[str, ...]]:
    dataset = ROOT / "artifacts/scientific-datasets-v3/rcaeval"
    capabilities = tuple(DatasetCapabilityV3(**item) for item in
                         load_json(dataset / "capabilities.json")["capabilities"])
    config = build_experiment_config(
        load_campaign_config(ROOT / "experiments/configs/final-v3/h1a.json"), capabilities)
    assignments, _ = ExperimentPlannerV3(config).protocol_assignments("within-dataset", 42)
    assignment = next(item for item in assignments if item.applicable)
    return {"train": tuple(assignment.train_incident_ids),
            "validation": tuple(assignment.validation_incident_ids),
            "test": tuple(assignment.test_incident_ids)}


def campaign_root(base: Path) -> Path:
    roots = [item for item in base.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"H1_H2_CAMPAIGN_ROOT_AMBIGUOUS:{base}")
    return roots[0]


def validate_sums(root: Path) -> None:
    for line in (root / "SHA256SUMS").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        if file_sha256(root / relative) != expected:
            raise RuntimeError(f"H1_H2_HISTORICAL_CHECKSUM_FAIL:{root / relative}")


def historical_results(base: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows, audit = [], {}
    expected = {"H1-A": 160, "H1-B": 80, "H1-C": 124, "H2": 280}
    for family, path in CAMPAIGNS.items():
        root = campaign_root(path)
        validate_sums(root)
        rows = load_json(root / "analysis-ready-results.json")["results"]
        if len(rows) != expected[family]:
            raise RuntimeError(f"H1_H2_HISTORICAL_RESULT_COUNT_FAIL:{family}")
        invalid = [row for row in rows if row["execution_status"] not in
                   {"SUCCESS", "SKIP_NOT_APPLICABLE"}]
        if invalid or any(row["protocol"] != "within-dataset" or row["seed"] != 42
                          for row in rows):
            raise RuntimeError(f"H1_H2_HISTORICAL_STATUS_OR_SCOPE_FAIL:{family}")
        tranche = load_json(root / "scientific-tranche.json")
        if (tranche.get("scientific_tranche_id") != "tranche-a-180"
                or tranche.get("scientific_tranche_sha256") != base["selection_sha256"]):
            raise RuntimeError(f"H1_H2_HISTORICAL_TRANCHE_LINEAGE_FAIL:{family}")
        audit[family] = {"root": str(root.relative_to(ROOT)), "records": len(rows),
                         "status_counts": dict(Counter(row["execution_status"] for row in rows)),
                         "sha256s_valid": True}
        all_rows.extend({**row, "family": family, "campaign_root": str(root)} for row in rows)
    if {row["incident_id"] for row in all_rows} != set(base["test_incident_ids"]):
        raise RuntimeError("H1_H2_HISTORICAL_INCIDENT_SET_FAIL")
    return all_rows, audit


def artifact_for(root: Path, method: str, dataset_hash: str) -> Path:
    path = root / "benchmark-v3" / method / dataset_hash[:12] / "seed-42"
    if not path.is_dir():
        raise RuntimeError(f"H1_H2_FROZEN_ARTIFACT_MISSING:{path}")
    return path


def reproduce_spot(rows: list[dict[str, Any]], selected: set[str], *,
                   identity_validator=validate_model_identity
                   ) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_json(ROOT / "artifacts/final-experiments-v3/scientific-inputs-tranche-a-180/benchmark-catalog.json")
    datasets = {(item["feature_view"], item["source_system"]): item
                for item in catalog["datasets"] if item["protocol"] == "within-dataset"}
    dataset_cache, artifact_cache, model_cache = {}, {}, {}
    comparisons, learned_contracts, heuristic_contracts = [], {}, {}
    guard = TrainingCallGuard()
    numeric_checked = numeric_failed = 0
    maximum_delta = 0.0
    model_spec_path = ROOT / "experiments/configs/final-v3/scientific-model-specification.json"
    current_model_specification_hash = file_sha256(model_spec_path)
    applicable = [row for row in rows if row["incident_id"] in selected
                  and row["execution_status"] == "SUCCESS"]
    for old in applicable:
        entry = datasets[(old["feature_view"], old["source_system"])]
        dataset_path = ROOT / entry["path"]
        dataset_key = str(dataset_path)
        if dataset_key not in dataset_cache:
            dataset_cache[dataset_key] = BenchmarkDataset.from_json(dataset_path)
        dataset = dataset_cache[dataset_key]
        x_test, _, test_rows = dataset.select("test")
        indexes = [i for i, item in enumerate(test_rows) if item.incident_id == old["incident_id"]]
        scoped_rows = tuple(test_rows[i] for i in indexes)
        matrix = x_test[indexes]
        root = Path(old["campaign_root"])
        artifact_path = artifact_for(root, old["method"], dataset.dataset_hash)
        artifact_key = str(artifact_path)
        if artifact_key not in artifact_cache:
            artifact_cache[artifact_key] = load_verified_artifact(artifact_path)
        verified = artifact_cache[artifact_key]
        manifest = verified.manifest
        if old["method"] in ML_MODELS:
            model_file = manifest["model_file"]
            if artifact_key not in model_cache:
                model_cache[artifact_key] = joblib.load(artifact_path / model_file)
            model = model_cache[artifact_key]
            artifact_hash = file_sha256(artifact_path / model_file)
            split_ids = {split: sorted({row.incident_id for row in dataset.rows
                                        if row.split == split})
                         for split in ("train", "validation", "test")}
            historical_hyperparameters = load_json(artifact_path / "hyperparameters.json")
            historical_model_specification_hash = load_json(
                root / "model-manifest.json")["model_hash"]
            expected_identity = {
                "serialized_artifact_sha256": old["trained_model_artifact_hash"],
                "training_split_hash": old["training_split_hash"],
                "validation_split_hash": old["validation_split_hash"],
                "feature_schema_sha256": old["feature_schema_sha256"],
                "model_specification_hash": historical_model_specification_hash,
                "hyperparameters": historical_hyperparameters,
            }
            actual_identity = {
                "serialized_artifact_sha256": artifact_hash,
                "training_split_hash": ids_sha256(split_ids["train"]),
                "validation_split_hash": ids_sha256(split_ids["validation"]),
                "feature_schema_sha256": manifest["feature_view_sha256"],
                "model_specification_hash": current_model_specification_hash,
                "hyperparameters": dict(manifest["hyperparameters"]),
            }
            bind_model_identity(expected_identity, actual_identity, identity_validator)
            if tuple(dataset.feature_names) != tuple(manifest["feature_names"]):
                raise RuntimeError("H1_H2_FEATURE_COLUMN_ORDER_MISMATCH")
            contract_key = ":".join((old["family"], old["method"], old["feature_view"],
                                     str(artifact_path.relative_to(ROOT))))
            learned_contracts[contract_key] = {
                "contract_key": contract_key, "family": old["family"],
                "method": old["method"], "feature_view": old["feature_view"],
                "training_scope": "combined-tranche-a-180",
                "artifact_dir": str(artifact_path.relative_to(ROOT)),
                "expected": expected_identity, "actual": actual_identity,
                "feature_column_order_sha256": sha256_value(list(dataset.feature_names)),
                "identity_validation": "PASS", "model_retrained": False,
                "model_retuned": False,
            }
        else:
            model = HeuristicEstimator(old["method"], dataset.feature_names)
            artifact_hash = None
            actual_implementation_hash = __import__("hashlib").sha256(
                f"benchmark-v3:{old['method']}:HeuristicEstimator".encode()).hexdigest()
            validate_heuristic_identity(old["method_implementation_hash"],
                                        actual_implementation_hash)
            contract_key = ":".join((old["method"], old["method_version"],
                                     old["method_implementation_hash"]))
            heuristic_contracts[contract_key] = {
                "contract_key": contract_key, "method": old["method"],
                "method_version": old["method_version"],
                "expected_method_implementation_hash": old["method_implementation_hash"],
                "actual_method_implementation_hash": actual_implementation_hash,
                "identity_validation": "PASS", "trained_model_artifact_sha256": None,
            }
        scores = inference_only_scores(model, matrix, guard)
        evaluated = evaluate_scores(scores, scoped_rows, inference_seconds=0.0,
                                    memory_peak_bytes=0, model_size_bytes=0,
                                    training_seconds=0.0)
        prediction = evaluated["incident_predictions"][0]
        ranking = [item["service_name"] for item in prediction["ranking"]]
        actual = {
            "incident_id": old["incident_id"], "method": old["method"],
            "feature_view": old["feature_view"], "protocol": old["protocol"],
            "seed": old["seed"], "predicted_service": ranking[0],
            "ranked_services": ranking, "top1_correct": prediction["reciprocal_rank"] == 1.0,
            "top3_correct": prediction["reciprocal_rank"] >= 1 / 3,
            "reciprocal_rank": prediction["reciprocal_rank"],
            "fpr": evaluated["metrics"]["fpr"], "confidence": prediction["ranking"][0]["score"],
            "abstained": False, "feature_schema_sha256": old["feature_schema_sha256"],
            "model_specification_hash": old["model_specification_hash"],
            "trained_model_artifact_hash": artifact_hash,
            "method_implementation_hash": old["method_implementation_hash"],
        }
        expected = {key: old[key] for key in actual if key in old}
        ok, differences, numeric = compare_spot_records_detailed(
            expected, {key: actual[key] for key in expected},
            tolerance=STRICT_REPRODUCIBILITY_TOLERANCE)
        numeric_checked += numeric["numeric_comparisons_checked"]
        numeric_failed += numeric["numeric_comparisons_failed"]
        comparison_delta = numeric["maximum_absolute_numeric_delta"]
        stored_scores = {(item["incident_id"], item["service_name"]): item["score"]
                         for item in verified.predictions}
        score_deltas = [abs(float(score) - stored_scores[(row.incident_id, row.service_name)])
                        for score, row in zip(scores, scoped_rows)]
        numeric_checked += len(score_deltas)
        numeric_failed += sum(delta > STRICT_REPRODUCIBILITY_TOLERANCE
                              for delta in score_deltas)
        comparison_delta = max([comparison_delta, *score_deltas])
        maximum_delta = max(maximum_delta, comparison_delta)
        score_compatible = all(delta <= STRICT_REPRODUCIBILITY_TOLERANCE
                               for delta in score_deltas)
        ok = ok and score_compatible
        if not score_compatible:
            differences.append("candidate_scores")
        comparisons.append({"family": old["family"], "incident_id": old["incident_id"],
                            "method": old["method"], "feature_view": old["feature_view"],
                            "reproduced": ok, "differences": differences,
                            "candidate_scores_within_1e_15": score_compatible,
                            "maximum_absolute_numeric_delta": comparison_delta,
                            "model_retrained": False, "model_retuned": False})
    by_incident = defaultdict(list)
    for item in comparisons:
        by_incident[item["incident_id"]].append(item)
    reproduced = [incident for incident, items in by_incident.items()
                  if items and all(item["reproduced"] for item in items)]
    counters_ok = all(value == 0 for value in guard.counters.values())
    learned_ok = bool(learned_contracts) and all(
        item["identity_validation"] == "PASS" for item in learned_contracts.values())
    heuristic_ok = bool(heuristic_contracts) and all(
        item["identity_validation"] == "PASS" for item in heuristic_contracts.values())
    return ({"schema_version": "h1h2-spot-check-results-v2",
             "spot_check_selection_sha256": FROZEN_SPOT_SHA256,
             "selected_unique_incidents": sorted(selected),
             "selected_unique_incident_count": len(selected),
             "applicable_run_signatures": len(comparisons), "comparisons": comparisons,
             "reproduced_incident_ids": sorted(reproduced),
             "reproduced_unique_incidents": len(reproduced),
             "strict_numeric_tolerance": STRICT_REPRODUCIBILITY_TOLERANCE,
             "maximum_absolute_numeric_delta": maximum_delta,
             "numeric_comparisons_checked": numeric_checked,
             "numeric_comparisons_failed": numeric_failed,
             "gate": "PASS" if len(reproduced) == 5 and numeric_failed == 0 else "FAIL",
             **guard.counters},
            {"schema_version": "h1h2-model-reuse-audit-v2",
             "learned_contracts": list(learned_contracts.values()),
             "learned_model_contracts_checked": len(learned_contracts),
             "learned_model_contracts_passed": len(learned_contracts) if learned_ok else 0,
             "learned_model_contracts_failed": 0 if learned_ok else len(learned_contracts),
             "heuristic_contracts": list(heuristic_contracts.values()),
             "heuristic_contracts_checked": len(heuristic_contracts),
             "heuristic_contracts_passed": len(heuristic_contracts) if heuristic_ok else 0,
             "heuristic_contracts_failed": 0 if heuristic_ok else len(heuristic_contracts),
             "model_identity_audit": "PASS" if learned_ok else "FAIL",
             "heuristic_identity_audit": "PASS" if heuristic_ok else "FAIL",
             "inference_only_path_audit": "PASS" if counters_ok else "FAIL",
             **guard.counters})


def write_artifacts(output: Path, files: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (output / name).write_text(canonical_json(payload) + "\n", encoding="utf-8")
    artifact_names = ("expansion-audit.json", "extension-manifest.json",
                      "model-reuse-audit.json", "spot-check-results.json",
                      "spot-check-selection.json")
    sums = [f"{file_sha256(output / name)}  {name}" for name in artifact_names]
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/final-experiments-v3/h1h2-expansion-gate")
    args = parser.parse_args()
    output = args.output.resolve()
    base = load_json(ROOT / "experiments/configs/final-v3/scientific-tranche-a-180.json")
    parent = reconstruct_parent()
    parent_hashes = {name: ids_sha256(values) for name, values in parent.items()}
    parent_split_hash = sha256_value({name: sorted(values) for name, values in parent.items()})
    if parent_split_hash != base["parent_split_key"]:
        raise RuntimeError("H1_H2_PARENT_SPLIT_IDENTITY_FAIL")
    manifests = [load_json(path) for path in sorted(
        (ROOT / "artifacts/scientific-datasets-v3/rcaeval/manifests").glob("*.json"))]
    frozen_extension = load_json(output / "extension-manifest.json")
    validate_extension_manifest(frozen_extension, base=base, parent_test_ids=parent["test"])
    reconstructed_extension = build_extension_manifest(
        base=base, parent_test_ids=parent["test"], metadata=manifests)
    if frozen_extension != reconstructed_extension:
        raise RuntimeError("H1_H2_EXTENSION_MANIFEST_IDENTITY_FAIL")
    extension = frozen_extension
    spot = load_json(output / "spot-check-selection.json")
    validate_frozen_spot_selection(spot)
    reconstructed_spot = build_spot_selection(
        base_test_ids=base["test_incident_ids"], metadata=manifests)
    if reconstructed_spot != spot:
        raise RuntimeError("H1_H2_SPOT_CHECK_SELECTION_RECONSTRUCTION_FAIL")
    rows, historical_audit = historical_results(base)
    spot_results, model_audit = reproduce_spot(rows, set(spot["selected_incident_ids"]))
    model_identity_ok = (model_audit["model_identity_audit"] == "PASS"
                         and model_audit["heuristic_identity_audit"] == "PASS")
    inference_only_ok = model_audit["inference_only_path_audit"] == "PASS"
    reusable = spot_results["gate"] == "PASS" and model_identity_ok and inference_only_ok
    v2_freeze = verify_v2_freeze(ROOT)
    h3_freeze = h3_freeze_audit()
    reusable = reusable and v2_freeze["valid"] and h3_freeze["status"] == "PASS"
    audit = {
        "schema_version": "h1h2-expansion-audit-v2", "protocol": "within-dataset", "seed": 42,
        "base_tranche_id": "tranche-a-180", "base_counts": {"train": 110, "validation": 30, "test": 40},
        "parent_counts": {name: len(values) for name, values in parent.items()},
        "parent_ids_sha256": parent_hashes, "combined_parent_split_sha256": parent_split_hash,
        "target_final_test_count": 200, "target_200_compatible_with_frozen_parent": False,
        "maximum_compatible_final_test_count": len(parent["test"]),
        "final_proposed_test_count": extension["actual_final_test_count"],
        "new_test_incident_count": extension["added_test_count"],
        "train_ids_unchanged": True, "validation_ids_unchanged": True,
        "no_train_test_overlap": True, "no_validation_test_overlap": True,
        "no_base_new_test_overlap": True, "base_40_nested_in_expanded_test": True,
        "historical_results": historical_audit, "existing_40_results_valid": True,
        "existing_40_results_reusable": reusable,
        "strict_reproducibility_tolerance": STRICT_REPRODUCIBILITY_TOLERANCE,
        "spot_check_selection_identity": "PASS",
        "spot_check_selection_sha256": FROZEN_SPOT_SHA256,
        "model_identity_audit": model_audit["model_identity_audit"],
        "heuristic_identity_audit": model_audit["heuristic_identity_audit"],
        "training_entrypoint_audit": model_audit["inference_only_path_audit"],
        "execution_accounting": {"existing_results_validated": 40,
            "spot_check_incidents_reexecuted": 5,
            "existing_non_spotcheck_incidents_reexecuted": 0,
            "new_incidents_executed": 0, "full_new_extension_campaign_executed": False},
        "scope": {"H1_protocols": ["within-dataset"], "H2_protocols": ["within-dataset"],
                  "H3_protocols": ["within-dataset"], "H4_protocols": ["within-dataset"],
                  "H1_seeds": [42], "H2_seeds": [42], "H3_seeds": [42], "H4_seeds": [42],
                  "H3_sample_size": 40, "H4_sample_size": 40},
        "regression": {"V2_FREEZE_HASHES_OK": f"{v2_freeze['ok']}/{v2_freeze['total']}",
                       "H3_OUTPUT_CONTRACT_BINDING_AUDIT": h3_freeze["output_contract_binding"],
                       "H3_PROMPT_PROVENANCE_AUDIT": h3_freeze["prompt_provenance_binding"],
                       "H3_LLM_FREEZE_STATIC_AUDIT": h3_freeze["status"]},
        "gates": {"H1_H2_TARGET_200_FEASIBILITY": "NOT_PASS_PARENT_TEST_LIMIT",
                  "H1_H2_MAX_COMPATIBLE_EXPANSION": "PASS" if reusable else "NOT_PASS",
                  "H1_H2_FROZEN_MODEL_REUSE_GATE": "PASS" if reusable else "NOT_PASS"},
    }
    write_artifacts(output, {"expansion-audit.json": audit,
                             "spot-check-results.json": spot_results,
                             "model-reuse-audit.json": model_audit})
    print(canonical_json(audit))
    machine = {
        "H1_H2_STRICT_REPRODUCIBILITY_TOLERANCE": STRICT_REPRODUCIBILITY_TOLERANCE,
        "H1_H2_SPOT_CHECK_SELECTION_IDENTITY": "PASS",
        "H1_H2_SPOT_CHECK_UNIQUE_INCIDENTS": len(FROZEN_SPOT_IDS),
        "H1_H2_SPOT_CHECK_SELECTION_SHA256": FROZEN_SPOT_SHA256,
        "H1_H2_SPOT_CHECK_APPLICABLE_RUN_SIGNATURES": spot_results["applicable_run_signatures"],
        "H1_H2_SPOT_CHECK_MAX_ABSOLUTE_NUMERIC_DELTA": spot_results["maximum_absolute_numeric_delta"],
        "H1_H2_SPOT_CHECK_NUMERIC_FAILURES": spot_results["numeric_comparisons_failed"],
        "H1_H2_SPOT_CHECK_REPRODUCED": f"{spot_results['reproduced_unique_incidents']}/5",
        "H1_H2_REPRODUCIBILITY_SPOT_CHECK": spot_results["gate"],
        "H1_H2_LEARNED_MODEL_CONTRACTS_CHECKED": model_audit["learned_model_contracts_checked"],
        "H1_H2_LEARNED_MODEL_CONTRACTS_PASSED": model_audit["learned_model_contracts_passed"],
        "H1_H2_LEARNED_MODEL_CONTRACTS_FAILED": model_audit["learned_model_contracts_failed"],
        "H1_H2_HEURISTIC_CONTRACTS_CHECKED": model_audit["heuristic_contracts_checked"],
        "H1_H2_HEURISTIC_CONTRACTS_PASSED": model_audit["heuristic_contracts_passed"],
        "H1_H2_HEURISTIC_CONTRACTS_FAILED": model_audit["heuristic_contracts_failed"],
        "H1_H2_MODEL_IDENTITY_AUDIT": model_audit["model_identity_audit"],
        "H1_H2_INFERENCE_ONLY_PATH_AUDIT": model_audit["inference_only_path_audit"],
        "EXISTING_40_RESULTS_REUSABLE": "YES" if reusable else "NO",
        "H1_H2_FROZEN_MODEL_REUSE_GATE": audit["gates"]["H1_H2_FROZEN_MODEL_REUSE_GATE"],
    }
    for name, value in machine.items():
        print(f"{name} = {value}")
    return 0 if reusable else 1


if __name__ == "__main__":
    raise SystemExit(main())
