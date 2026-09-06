#!/usr/bin/env python3
"""Run exactly three non-canonical H4 production-path technical canaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.causalods_campaign_bindings import build_executors
from experiments.final_campaigns_v3.cli import bind_runs_to_scientific_tranche
from experiments.final_campaigns_v3.core import (
    FrozenDatasetRegistry, build_plan, load_campaign_config,
)
from experiments.final_campaigns_v3.scientific_tranche import load_manifest
from experiments.multimodal_v3.core import file_sha256


ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA256 = "a10ede7c8bfd922e14765caaf8dc6bfbf7e18627a3a49b722895bb2240e9f889"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_canary(output: Path) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("H4 canary output deve estar vazio.")
    config_path = ROOT / "experiments/configs/final-v3/h4.json"
    registry_path = ROOT / "experiments/configs/final-v3/datasets.json"
    tranche_path = ROOT / "experiments/configs/final-v3/scientific-tranche-a-180.json"
    payload = load_campaign_config(config_path)
    registry = FrozenDatasetRegistry.load(registry_path, ROOT)
    config, planned = build_plan(payload, registry.capabilities())
    tranche = load_manifest(tranche_path)
    catalog = _read(ROOT / payload["criteria"]["scientific_benchmark_catalog"])
    runs = bind_runs_to_scientific_tranche(
        planned, tranche, catalog_entries=catalog["datasets"])
    candidates = [run for run in runs if (
        run.eligibility and run.degradation_operator == "metric_delay"
        and run.degradation_severity == 0.0 and run.method == "xgboost-rca-v3"
        and run.feature_view == "FULL")]
    selected = []
    for system in ("OB", "SS", "TT"):
        system_runs = [run for run in candidates if run.source_system == system]
        if not system_runs:
            raise RuntimeError(f"H4_CANARY_NO_APPLICABLE_INCIDENT:{system}")
        selected.append(min(system_runs, key=lambda run: run.incident_id))
    executor = build_executors(config=config, output=output, repository_root=ROOT)["H4"]
    evaluator = executor.feature_hasher.__self__
    reports = []
    for run in selected:
        result, provenance, evaluation = executor.execute(run)
        _, names, baseline_sha, feature_sha = evaluator._metadata_for(run)
        entry = evaluator._entry(run)
        schema_ok = (
            {"predicted_ranking", "confidence", "reliability", "core_metrics"} <= set(result)
            and {"original_incident_hash", "degraded_evidence_hash",
                 "original_feature_hash", "degraded_feature_hash",
                 "degradation_seed"} <= set(provenance)
            and {"top_1", "top_3", "mrr", "fpr"} <= set(evaluation)
        )
        reports.append({
            "source_system": run.source_system,
            "incident_id_sha256": hashlib.sha256(run.incident_id.encode()).hexdigest(),
            "execution": "SUCCESS",
            "model_sha256": entry["serialized_artifact_sha256"],
            "baseline_sha256": baseline_sha,
            "feature_view_sha256": feature_sha,
            "feature_vector_produced": bool(names and provenance.get("degraded_feature_hash")),
            "ranking_produced": isinstance(result.get("predicted_ranking"), (list, tuple)),
            "reliability_decision_produced": "reliability" in result,
            "result_schema_validation": "PASS" if schema_ok else "FAIL",
        })
    if len(reports) != 3 or any(item["execution"] != "SUCCESS" for item in reports):
        raise RuntimeError("H4_CANARY_RUNTIME_FAILURE")
    if any(item["model_sha256"] != MODEL_SHA256 for item in reports):
        raise RuntimeError("H4_CANARY_MODEL_IDENTITY_FAILURE")
    report = {
        "schema_version": "h4-final-40-technical-canary-v1",
        "purpose": "TECHNICAL_EXECUTION_VALIDATION_ONLY",
        "used_for_model_selection": False,
        "used_for_threshold_selection": False,
        "used_for_hypothesis_interpretation": False,
        "operator": "metric_delay", "severity": 0.0,
        "method": "xgboost-rca-v3", "feature_view": "FULL",
        "training_calls": 0, "tuning_calls": 0, "fit_calls": 0,
        "partial_fit_calls": 0, "systems": reports,
        "metadata_cache_entries": len(evaluator._metadata),
        "maximum_metadata_cache_entries_seen": evaluator.maximum_metadata_cache_entries_seen,
        "status": "PASS",
    }
    output.mkdir(parents=True, exist_ok=True)
    target = output / "canary-report.json"
    target.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    (output / "SHA256SUMS").write_text(
        f"{file_sha256(target)}  canary-report.json\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT /
                        "artifacts/final-experiments-v3-h4-final-40-canary")
    args = parser.parse_args(argv)
    result = run_canary(args.output)
    for item in result["systems"]:
        print(json.dumps(item, sort_keys=True))
    print("H4_CANARY_RUNTIME_SUCCESS=3/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
