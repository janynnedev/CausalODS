"""Sandbox-safe contracts for the 16-case RCAEval host gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from types import SimpleNamespace

from experiments.scripts import build_rcaeval_stratified_gate_v3 as builder
from experiments.scripts import run_rcaeval_stratified_gate_v3 as runner


ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_builder_preserves_selected_official_manifests_and_records_snapshots():
    output = ROOT / ".pytest-preprocessing-fixtures" / uuid.uuid4().hex / "stratified"
    try:
        report = builder.build(output)
        assert report["total_strata"] == len(report["selected"]) == 16
        assert report["official_rcaeval_mutated"] is False
        assert len(report["official_metadata_snapshot_sha256"]) == 64
        assert len(report["official_selected_manifests_snapshot_sha256"]) == 64
        for selected in report["selected"]:
            official = builder.OFFICIAL / selected["manifest_path"]
            derived = output / "dataset" / selected["manifest_path"]
            assert _sha(official) == _sha(derived)
    finally:
        shutil.rmtree(output.parent, ignore_errors=True)


def test_stratified_runner_help_and_memory_contract():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "experiments/scripts/run_rcaeval_stratified_gate_v3.py"), "--help"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert completed.returncode == 0
    assert all(flag in completed.stdout for flag in
               ("--registry", "--config-dir", "--model-specification", "--rcaeval-cache", "--output"))
    assert runner._worker_peak([
        {"worker": {"peak_rss_kb": 10}}, {"worker": {"peak_rss_kb": 40}},
        {"worker": {"peak_rss_kb": 20}},
    ]) == 40
    source = (ROOT / "experiments/scripts/run_rcaeval_stratified_gate_v3.py").read_text()
    assert "RUSAGE_SELF" not in source
    assert "check=False" in source
    assert 'stage="resolve-preprocessing-binding"' in source
    assert "resolve.returncode != 0" in source
    assert 'stage="rcaeval-runtime-preflight"' in source
    assert 'stage="preprocess"' in source
    assert 'stage="preprocess-output-parse"' in source
    assert source.index("runtime = subprocess.run") < source.index("run = subprocess.run")
    for key in ("worker_peak_rss_kb_max", "artifact_inventory_sha256",
                "official_rcaeval_immutability", "checkpoint_count"):
        assert key in source


def test_runner_failure_report_preserves_preprocess_stderr(tmp_path, capsys):
    completed = SimpleNamespace(returncode=1, stdout="partial", stderr="PREPROCESS_SENTINEL")
    assert runner._fail_report(tmp_path, stage="preprocess", completed=completed) == 1
    report = json.loads((tmp_path / "stratified-gate-report.json").read_text())
    assert report == {"status": "FAIL", "stage": "preprocess", "returncode": 1,
                      "stdout": "partial", "stderr": "PREPROCESS_SENTINEL"}
    assert "PREPROCESS_SENTINEL" in capsys.readouterr().out


def test_scientific_work_inventory_excludes_worker_specs_and_strange_json(tmp_path):
    incidents = tmp_path / "incidents"
    contributions = tmp_path / "baseline" / "contributions"
    incidents.mkdir(parents=True)
    contributions.mkdir(parents=True)
    incident = {"schema_version": "scientific-preprocessing-incident-checkpoint-v1",
                "status": "COMPLETED"}
    contribution = {
        "schema_version": "scientific-preprocessing-baseline-contribution-v1",
        "status": "COMPLETED",
    }
    for name in ("a", "b"):
        (incidents / f"{name}.json").write_text(json.dumps({**incident, "incident_id": name}))
        (incidents / f"{name}.worker.json").write_text(json.dumps({"incident_id": name}))
    (incidents / "strange.json").write_text(json.dumps({"status": "COMPLETED"}))
    (incidents / "invalid.json").write_text("not-json")
    (contributions / "x.json").write_text(
        json.dumps({**contribution, "incident_id": "x"}))
    (contributions / "x.worker.json").write_text(json.dumps({"incident_id": "x"}))
    (contributions / "strange.json").write_text(json.dumps({
        "schema_version": "unrelated", "status": "COMPLETED"}))

    inventory = runner._scientific_work_inventory(tmp_path)
    assert [item["incident_id"] for item in inventory["checkpoints"]] == ["a", "b"]
    assert [item["incident_id"] for item in inventory["contributions"]] == ["x"]
    assert inventory["incident_worker_spec_count"] == 2
    assert inventory["baseline_worker_spec_count"] == 1
