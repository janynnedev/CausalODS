from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.final_campaigns_v3.preprocessing import (
    _baseline_from_json, _baseline_snapshot_bindings, _incident_worker_payload,
    _load_worker_baseline,
    _run_incident_worker,
)
from experiments.multimodal_v3.core import sha256_value


class _Registry:
    def __init__(self, root: Path):
        self.repository_root = root

    def snapshot(self):
        return {"schema_version": "fixture-registry-v3", "datasets": [],
                "registry_sha256": "a" * 64}


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _invoke(tmp_path, monkeypatch, completed, *, remove_spec_before_return=False):
    output = tmp_path / "checkpoint.json"
    original = {"status": "COMPLETED", "rows": [{"scientific": 1}],
                "scientific_payload_sha256": "b" * 64}
    before = json.dumps(original, sort_keys=True).encode()
    baseline_value = {"schema_version": "rca-feature-baselines-v3", "statistics": {},
                      "log_templates": {}, "training_incident_ids": []}
    baseline_sha256 = sha256_value(baseline_value)
    baseline_path = tmp_path / "baseline-snapshot.json"
    baseline_bindings = _baseline_snapshot_bindings(
        base_key="baseline-base", split_key="baseline-split", train_incident_ids=())
    baseline_path.write_text(json.dumps({
        **baseline_bindings, "status": "COMPLETED",
        "baseline_json": json.dumps(baseline_value),
        "baseline_sha256": baseline_sha256,
    }))

    def run(*_args, **_kwargs):
        if completed.returncode == 0:
            output.write_bytes(before)
            if remove_spec_before_return:
                output.with_suffix(".worker.json").unlink()
        return completed

    monkeypatch.setattr("experiments.final_campaigns_v3.preprocessing.subprocess.run", run)
    result = _run_incident_worker(
        registry=_Registry(tmp_path), rcaeval_cache=None, incident_id="incident-1",
        split="test", base_key="base", split_key="split",
        baseline_snapshot_path=baseline_path, baseline_sha256=baseline_sha256,
        baseline_snapshot_bindings=baseline_bindings, output=output)
    return output, output.with_suffix(".worker.json"), before, result


def test_success_removes_only_worker_spec_and_preserves_checkpoint_and_payload(
        tmp_path, monkeypatch):
    output, spec, before, result = _invoke(tmp_path, monkeypatch, _completed())
    assert not spec.exists()  # SUCCESSFUL_INCIDENT_WORKER_SPEC_REMOVED
    assert output.is_file()  # SUCCESSFUL_INCIDENT_CHECKPOINT_PRESERVED
    assert output.read_bytes() == before  # SCIENTIFIC_PAYLOAD_UNCHANGED on disk
    assert result["rows"] == [{"scientific": 1}]
    assert result["worker"]["returncode"] == 0


def test_success_cleanup_is_idempotent_when_worker_spec_is_already_absent(
        tmp_path, monkeypatch):
    output, spec, before, result = _invoke(
        tmp_path, monkeypatch, _completed(), remove_spec_before_return=True)
    assert result["status"] == "COMPLETED"
    assert result["worker"]["returncode"] == 0
    assert not spec.exists()  # INCIDENT_WORKER_SPEC_CLEANUP_IDEMPOTENCY
    assert output.read_bytes() == before  # SCIENTIFIC_CHECKPOINT_BYTES_UNCHANGED


def test_failure_preserves_worker_spec_for_diagnostics(tmp_path, monkeypatch):
    output, spec, _, result = _invoke(
        tmp_path, monkeypatch, _completed(returncode=9, stderr="disk failure"))
    assert result["status"] == "FAILED_WORKER"
    assert spec.is_file()  # FAILED_INCIDENT_WORKER_SPEC_PRESERVED
    assert not output.exists()
    spec_payload = json.loads(spec.read_text())
    assert "baseline_json" not in spec_payload
    assert spec_payload["baseline_snapshot_path"].endswith("baseline-snapshot.json")


def test_success_without_readable_checkpoint_preserves_spec(tmp_path, monkeypatch):
    output = tmp_path / "checkpoint.json"
    monkeypatch.setattr(
        "experiments.final_campaigns_v3.preprocessing.subprocess.run",
        lambda *_args, **_kwargs: _completed())
    try:
        _run_incident_worker(
            registry=_Registry(tmp_path), rcaeval_cache=None, incident_id="incident-1",
            split="test", base_key="base", split_key="split",
            baseline_snapshot_path=tmp_path / "missing-baseline.json",
            baseline_sha256="a" * 64,
            baseline_snapshot_bindings=_baseline_snapshot_bindings(
                base_key="base", split_key="split", train_incident_ids=()), output=output)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing checkpoint should fail")
    assert output.with_suffix(".worker.json").is_file()


def test_baseline_reference_missing_corrupt_and_wrong_hash_fail_deterministically(tmp_path):
    bindings = _baseline_snapshot_bindings(
        base_key="base", split_key="split", train_incident_ids=())
    missing = tmp_path / "missing.json"
    with pytest.raises(RuntimeError, match="BASELINE_REFERENCE_INVALID"):
        _load_worker_baseline(snapshot_path=missing, expected_sha256="a" * 64,
                              bindings=bindings)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not-json")
    with pytest.raises(RuntimeError, match="BASELINE_REFERENCE_INVALID"):
        _load_worker_baseline(snapshot_path=corrupt, expected_sha256="a" * 64,
                              bindings=bindings)
    baseline = {"statistics": {}, "log_templates": {}, "training_incident_ids": []}
    canonical_baseline = _baseline_from_json(baseline).to_json()
    actual = sha256_value(json.loads(canonical_baseline))
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({**bindings, "status": "COMPLETED",
                                 "baseline_json": canonical_baseline,
                                 "baseline_sha256": actual}))
    loaded = _load_worker_baseline(snapshot_path=valid, expected_sha256=actual,
                                   bindings=bindings)
    assert loaded.to_json() == canonical_baseline
    with pytest.raises(RuntimeError, match="BASELINE_SHA256_MISMATCH"):
        _load_worker_baseline(snapshot_path=valid, expected_sha256="0" * 64,
                              bindings=bindings)


def test_phase_diagnostic_survives_failure_during_cache_load(tmp_path, monkeypatch):
    diagnostic = tmp_path / "checkpoint.phases.json"

    class Loader:
        def __init__(self, *_args, **_kwargs):
            self.sources = {"incident-oom": SimpleNamespace()}

        def events(self, _source):
            raise MemoryError("synthetic cache OOM")

    monkeypatch.setattr("experiments.final_campaigns_v3.preprocessing.FrozenEvidenceLoader",
                        Loader)
    monkeypatch.setattr("experiments.final_campaigns_v3.preprocessing._load_worker_baseline",
                        lambda **_kwargs: SimpleNamespace())
    with pytest.raises(MemoryError, match="synthetic cache OOM"):
        _incident_worker_payload(
            registry=_Registry(tmp_path), rcaeval_cache=None,
            incident_id="incident-oom", split="test", base_key="base",
            split_key="split", baseline_snapshot_path=tmp_path / "baseline.json",
            baseline_sha256="a" * 64, baseline_snapshot_bindings={},
            source=SimpleNamespace(), target=tmp_path / "checkpoint.json",
            phase_diagnostic_path=diagnostic)
    payload = json.loads(diagnostic.read_text())
    assert payload["current_phase"] == "CACHE_START"
    assert payload["phases"][0]["phase"] == "CACHE_START"
    assert set(payload["phases"][0]) == {
        "phase", "elapsed_seconds", "rss_kb", "peak_rss_kb"}
