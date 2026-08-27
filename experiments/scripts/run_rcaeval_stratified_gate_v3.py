"""Host-only runner for the derived 16-case RCAEval validation gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL = ROOT / "artifacts/scientific-datasets-v3/rcaeval"

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _snapshot(selection: dict) -> dict[str, str]:
    return {item["manifest_path"]: _sha(OFFICIAL / item["manifest_path"])
            for item in selection["selected"]}

def _inventory_sha256(output: Path) -> str:
    excluded = {"stratified-gate-report.json"}
    values = {str(path.relative_to(output)): _sha(path) for path in sorted(output.rglob("*"))
              if path.is_file() and path.name not in excluded and "work" not in path.parts}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _worker_peak(payloads) -> int:
    values = []
    for payload in payloads:
        worker = payload.get("worker") or {}
        if isinstance(worker.get("peak_rss_kb"), int):
            values.append(worker["peak_rss_kb"])
    return max(values, default=0)

def _last_json(text: str) -> dict:
    for line in reversed(text.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("CLI_FINAL_JSON_MISSING")

def _fail_report(output: Path, *, stage: str, completed: subprocess.CompletedProcess) -> int:
    report = {"status": "FAIL", "stage": stage,
              "returncode": completed.returncode,
              "stdout": completed.stdout, "stderr": completed.stderr}
    output.mkdir(parents=True, exist_ok=True)
    (output / "stratified-gate-report.json").write_text(
        json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 1

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=ROOT / "artifacts/validation/rcaeval-stratified-gate-v3/datasets.json")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "experiments/configs/final-v3")
    parser.add_argument("--model-specification", type=Path, default=ROOT / "experiments/configs/final-v3/scientific-model-specification.json")
    parser.add_argument("--rcaeval-cache", type=Path, default=Path("/home/janynne/mnt/rcaeval-official-v3"))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/validation/rcaeval-stratified-gate-v3/run")
    args = parser.parse_args()
    selection_path = args.registry.parent / "selection.json"
    selection = json.loads(selection_path.read_text())
    if selection.get("total_strata") != 16 or len(selection.get("selected", ())) != 16:
        raise RuntimeError("SELECTION_MUST_CONTAIN_16_STRATA")
    official_before = _snapshot(selection)
    from experiments.final_campaigns_v3.core import FrozenDatasetRegistry
    registry = FrozenDatasetRegistry.load(args.registry, ROOT)
    preflight = registry.preflight()
    if not preflight.get("valid"):
        raise RuntimeError("REGISTRY_PREFLIGHT_FAILED")
    first = selection["selected"][0]["incident_id"]
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'correlation-engine'}"}
    base = [sys.executable, "-m", "experiments.final_campaigns_v3"]
    resolve = subprocess.run([*base, "resolve-preprocessing-binding", "--registry", str(args.registry), "--config-dir", str(args.config_dir), "--model-specification", str(args.model_specification), "--incident-id", first, "--protocol", "within-dataset", "--seed", "42"], cwd=ROOT, text=True, capture_output=True, env=env, check=False)
    if resolve.returncode != 0:
        return _fail_report(args.output, stage="resolve-preprocessing-binding", completed=resolve)
    try:
        binding = _last_json(resolve.stdout)
    except RuntimeError:
        return _fail_report(args.output, stage="resolve-output-parse", completed=resolve)
    if binding.get("total_incidents") != 16:
        raise RuntimeError("BINDING_MUST_CONTAIN_16_INCIDENTS")
    runtime = subprocess.run([*base, "validate-rcaeval-runtime",
        "--registry", str(args.registry), "--rcaeval-cache", str(args.rcaeval_cache),
        "--incident-id", first], cwd=ROOT, text=True, capture_output=True,
        env=env, check=False)
    if runtime.returncode != 0:
        return _fail_report(args.output, stage="rcaeval-runtime-preflight", completed=runtime)
    try:
        runtime_payload = _last_json(runtime.stdout)
    except RuntimeError:
        return _fail_report(args.output, stage="rcaeval-runtime-preflight-output-parse", completed=runtime)
    if runtime_payload.get("status") != "PASS":
        return _fail_report(args.output, stage="rcaeval-runtime-preflight", completed=runtime)
    run = subprocess.run([*base, "preprocess", "--registry", str(args.registry), "--config-dir", str(args.config_dir), "--model-specification", str(args.model_specification), "--rcaeval-cache", str(args.rcaeval_cache), "--output", str(args.output), "--resume"], cwd=ROOT, text=True, capture_output=True, env=env)
    if run.returncode != 0:
        return _fail_report(args.output, stage="preprocess", completed=run)
    try:
        result = _last_json(run.stdout)
    except RuntimeError:
        return _fail_report(args.output, stage="preprocess-output-parse", completed=run)
    work = args.output / "work" / binding["base_key"]
    checkpoints = [json.loads(path.read_text()) for path in sorted((work / "incidents").glob("*.json"))]
    contributions = [json.loads(path.read_text()) for path in sorted((work / "baseline/contributions").glob("*.json"))]
    snapshot_path = work / "baseline/snapshot.json"
    baseline_snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.is_file() else {}
    expected_ids = {item["incident_id"] for item in selection["selected"]}
    actual_ids = {item.get("incident_id") for item in checkpoints}
    from experiments.final_campaigns_v3.preprocessing import _incident_scientific_payload
    from experiments.final_campaigns_v3.core import sha256_value
    valid_checkpoints = all(item.get("status") == "COMPLETED" and
        item.get("scientific_payload_sha256") == sha256_value(_incident_scientific_payload(item))
        for item in checkpoints)
    official_after = _snapshot(selection)
    immutable = official_before == official_after
    baseline_valid = (len(contributions) == binding["train_count"] and
                      all(item.get("status") == "COMPLETED" for item in contributions) and
                      baseline_snapshot.get("status") == "COMPLETED")
    passed = (run.returncode == 0 and result.get("status") == "PASS" and
              len(checkpoints) == 16 and actual_ids == expected_ids and valid_checkpoints and
              baseline_valid and immutable)
    report = {"status": "PASS" if passed else "FAIL",
        "selection_sha256": _sha(selection_path), "base_key": binding["base_key"],
        "split_key": binding["split_key"], "train_count": binding["train_count"],
        "validation_count": binding["validation_count"], "test_count": binding["test_count"],
        "total_incidents": 16, "checkpoint_count": len(checkpoints),
        "baseline_contribution_count": len(contributions),
        "worker_peak_rss_kb_max": _worker_peak([*contributions, baseline_snapshot, *checkpoints]),
        "preprocess": result, "preprocess_returncode": run.returncode,
        "official_rcaeval_immutability": "PASS" if immutable else "FAIL",
        "artifact_inventory_sha256": _inventory_sha256(args.output)}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "stratified-gate-report.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
