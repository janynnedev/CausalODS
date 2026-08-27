"""Small frozen fixture exercising the public preprocessing CLI and workers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from multimodal_v1.benchmark_v3 import BenchmarkDataset
from experiments.final_campaigns_v3.core import sha256_value
from experiments.final_campaigns_v3.preprocessing import _incident_scientific_payload


ROOT = Path(__file__).resolve().parents[2]
BASE = datetime(2026, 8, 18, tzinfo=timezone.utc)
LAST_RESULT = None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(registry: Path, configs: Path, spec: Path, output: Path, *extra: str, env=None):
    values = [sys.executable, "-m", "experiments.final_campaigns_v3", "preprocess",
              "--registry", str(registry), "--config-dir", str(configs), "--model-specification", str(spec),
              "--output", str(output), *extra]
    return subprocess.run(values, cwd=ROOT, text=True, capture_output=True,
                          env={**__import__("os").environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'correlation-engine'}", **(env or {})})


def _cli(command: str, registry: Path, configs: Path, spec: Path, output: Path, incident: str, *extra: str):
    values = [sys.executable, "-m", "experiments.final_campaigns_v3", command,
        "--registry", str(registry), "--config-dir", str(configs), "--model-specification", str(spec),
        "--incident-id", incident, "--protocol", "within-dataset", "--seed", "42"]
    if command != "resolve-preprocessing-binding":
        values.extend(("--output", str(output)))
    values.extend(extra)
    return subprocess.run(values,
        cwd=ROOT, text=True, capture_output=True,
        env={**__import__("os").environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'correlation-engine'}"})


def _last_json(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AssertionError(f"CLI não produziu JSON final: {stdout[-2000:]}")


def _wait_checkpoint_tree_stable(work_root: Path, *, timeout: float = 20, interval: float = .1) -> None:
    """Wait for atomic worker writes to quiesce; ignores temporary files."""
    previous = None
    stable = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = sorted(path for path in work_root.rglob("*.json") if not path.name.startswith("."))
        observed = tuple((str(path.relative_to(work_root)), path.stat().st_size, _sha(path)) for path in files)
        stable = stable + 1 if observed == previous else 0
        if stable >= 2:
            return
        previous = observed
        time.sleep(interval)
    raise AssertionError("CHECKPOINT_TREE_NOT_STABLE")


def _process_group_members(pgid: int) -> tuple[int, ...]:
    """Return Linux processes still attached to *pgid* without signalling them."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # comm may contain spaces and parentheses, so fields begin after
            # its final ')'.  The third and fourth numeric fields are pgrp/SID.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            process_group = int(fields[2])
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
        if process_group == pgid:
            members.append(int(entry.name))
    return tuple(sorted(members))


def _wait_process_group_exit(pgid: int, *, timeout: float = 30, interval: float = .05) -> None:
    """Wait for a coordinator's inherited process group to disappear.

    This helper is deliberately observation-only: the coordinator is the only
    process terminated by the caller and surviving workers finish naturally.
    """
    if not sys.platform.startswith("linux"):
        raise RuntimeError("PROCESS_GROUP_WAIT_REQUIRES_LINUX_PROCFS")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_group_members(pgid):
            return
        time.sleep(interval)
    raise AssertionError(f"PROCESS_GROUP_DID_NOT_EXIT:{pgid}:{_process_group_members(pgid)}")


def _scientific_artifact_inventory(output: Path) -> dict:
    files = [*sorted((output / "datasets").glob("*.json")), *sorted((output / "manifests").glob("*.json"))]
    files += [output / name for name in ("benchmark-catalog.json", "anti-leakage-audit.json", "scientific-model-specification.json") if (output / name).is_file()]
    datasets = {}
    for path in (output / "datasets").glob("*.json"):
        item = BenchmarkDataset.from_json(path)
        manifest = json.loads(path.read_text())["manifest"]
        datasets[path.stem] = {"file_sha256": _sha(path), "dataset_hash": item.dataset_hash,
            "baseline_sha256": manifest["baseline_sha256"], "rows_sha256": manifest["rows_sha256"],
            "feature_view_sha256": manifest["feature_view_sha256"],
            "split_incident_ids": manifest["split_incident_ids"]}
    file_hashes = {}
    for path in files:
        if path.name == "benchmark-catalog.json":
            catalog = json.loads(path.read_text())
            for entry in catalog.get("datasets", ()):
                if "path" in entry:
                    entry["path"] = f"datasets/{Path(entry['path']).name}"
            digest = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        else:
            digest = _sha(path)
        file_hashes[str(path.relative_to(output))] = digest
    payload = {"files": file_hashes, "datasets": datasets}
    return {"payload": payload, "inventory_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _remove_derived_artifacts(output: Path) -> None:
    for name in ("datasets", "manifests"):
        shutil.rmtree(output / name, ignore_errors=True)
    for name in ("benchmark-catalog.json", "preprocessing-report.json", "anti-leakage-audit.json"):
        (output / name).unlink(missing_ok=True)


def _fixture(tmp_path: Path):
    # The CLI intentionally accepts only roots below repository_root.  This
    # ephemeral path is removed by the test and is never a frozen project root.
    base = ROOT / ".pytest-preprocessing-fixtures" / uuid.uuid4().hex
    root = base / "fixture"
    root.mkdir(parents=True)
    try:
        incidents, caps = [], []
        for number in range(6):
            incident_id = f"fixture-{number}"
            directory = root / "incidents" / incident_id
            directory.mkdir(parents=True)
            events = []
            for offset, service in ((2, "payment-service"), (0, "order-service")):
                stamp = (BASE + timedelta(seconds=number * 10 + offset)).isoformat().replace("+00:00", "Z")
                events.extend((
                    {"event_id": f"{incident_id}-trace-{service}", "event_name": "request", "event_type": "trace", "signal_type": "trace", "event_timestamp": stamp, "service_name": service, "source_reference": f"fixture:{incident_id}:trace:{service}", "source_dataset": "fixture", "attributes": {"duration_ms": float(20 + number + offset)}},
                    {"event_id": f"{incident_id}-metric-{service}", "event_name": "cpu", "event_type": "metric", "signal_type": "metric", "event_timestamp": stamp, "service_name": service, "source_reference": f"fixture:{incident_id}:metric:{service}", "source_dataset": "fixture", "metric_name": "system.cpu.utilization", "metric_value": float(10 + number + offset), "attributes": {}},
                    {"event_id": f"{incident_id}-log-{service}", "event_name": "log", "event_type": "log", "signal_type": "log", "event_timestamp": stamp, "service_name": service, "source_reference": f"fixture:{incident_id}:log:{service}", "source_dataset": "fixture", "attributes": {"message": f"{service} fixture"}},
                ))
            source = directory / "events.json.gz"
            with gzip.open(source, "wt", encoding="utf-8") as stream:
                json.dump(events, stream)
            digest = _sha(source)
            manifest = {"source_system": "fixture-system", "normalized_source_files": [str(source.relative_to(root))],
                        "source_files_sha256": {str(source.relative_to(root)): digest}}
            manifest_path = directory / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            truth = directory / "ground-truth.json"
            truth.write_text(json.dumps({"root_cause_service": "payment-service"}))
            incidents.append({"incident_id": incident_id, "manifest_path": str(manifest_path.relative_to(root)),
                              "manifest_sha256": _sha(manifest_path), "root_cause_service": "payment-service",
                              "modalities": ["trace", "metric", "log"], "system": "fixture-system",
                              "usable_for_benchmark": True, "integrity_status": "CLEAN"})
            caps.append({"source_dataset": "fixture", "source_system": "fixture-system", "incident_id": incident_id,
                         "available_modalities": ["trace", "metric", "log"], "manifest_sha256": _sha(manifest_path),
                         "fault_type": "fixture"})
        (root / "INDEX.json").write_text(json.dumps({"count": 6, "incidents": incidents}))
        (root / "capabilities.json").write_text(json.dumps({"count": 6, "capabilities": caps}))
        (root / "materialization-report.json").write_text(json.dumps({"valid": 6, "usable": 6}))
        tracked = [root / "INDEX.json", root / "capabilities.json", root / "materialization-report.json"]
        sums = root / "SHA256SUMS"
        sums.write_text("".join(f"{_sha(item)}  {item.name}\n" for item in tracked))
        registry = base / "registry.json"
        registry.write_text(json.dumps({"schema_version": "frozen-scientific-dataset-registry-v3", "datasets": [{
            "dataset_id": "fixture", "kind": "fixture", "root": str(root.relative_to(ROOT)), "frozen": True,
            "expected_incidents": 6, "expected_usable": 6, "checksum_manifest": "SHA256SUMS",
            "checksum_manifest_sha256": _sha(sums), "index": "INDEX.json", "capabilities": "capabilities.json",
            "materialization_report": "materialization-report.json"}]}))
        configs = base / "configs"; configs.mkdir()
        for name in ("h1a", "h1b", "h1c", "h2"):
            shutil.copy(ROOT / "experiments/configs/final-v3/h1a.json", configs / f"{name}.json")
            path = configs / f"{name}.json"
            config = json.loads(path.read_text())
            config["protocols"] = ["within-dataset"]
            config["seeds"] = [42]
            path.write_text(json.dumps(config))
        spec = ROOT / "experiments/configs/final-v3/scientific-model-specification.json"
        yield registry, configs, spec
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_public_cli_clean_fixture_within_repository(tmp_path):
    fixture = _fixture(tmp_path)
    registry, configs, spec = next(fixture)
    clean = registry.parent / "outputs" / "clean"
    assert ROOT.resolve() in clean.resolve().parents
    result = _run(registry, configs, spec, clean)
    assert result.returncode == 0, result.stderr
    final = _last_json(result.stdout)
    assert final["status"] == "PASS"
    report = json.loads((clean / "preprocessing-report.json").read_text())
    assert report["status"] == "PASS"
    dataset = next((clean / "datasets").glob("*.json"))
    assert BenchmarkDataset.from_json(dataset).dataset_hash
    fixture.close()


def test_public_cli_clean_sigterm_resume_and_corrupt_checkpoint(tmp_path):
    fixture = _fixture(tmp_path)
    registry, configs, spec = next(fixture)
    fixture_base = registry.parent
    clean, interrupted = fixture_base / "outputs" / "clean", fixture_base / "outputs" / "interrupted"
    assert ROOT.resolve() in clean.resolve().parents
    assert ROOT.resolve() in interrupted.resolve().parents
    clean_result = _run(registry, configs, spec, clean)
    assert clean_result.returncode == 0, clean_result.stderr
    clean_payload = _last_json(clean_result.stdout)
    dataset = next((clean / "datasets").glob("*.json"))
    clean_dataset_payload = json.loads(dataset.read_text())
    clean_manifest = clean_dataset_payload["manifest"]
    clean_inventory = _scientific_artifact_inventory(clean)
    clean_benchmark = BenchmarkDataset.from_json(dataset)
    command = [sys.executable, "-m", "experiments.final_campaigns_v3", "preprocess", "--registry", str(registry), "--config-dir", str(configs), "--model-specification", str(spec), "--output", str(interrupted)]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
        env={**__import__("os").environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'correlation-engine'}"})
    deadline = time.time() + 30
    while time.time() < deadline and not list(interrupted.glob("work/*/baseline/contributions/*.json")):
        time.sleep(.05)
    process.terminate(); assert process.wait(timeout=30) != 0
    assert list(interrupted.glob("work/*/baseline/contributions/*.json"))
    _wait_process_group_exit(process.pid)
    _wait_checkpoint_tree_stable(interrupted / "work")
    resumed = _run(registry, configs, spec, interrupted, "--resume")
    assert resumed.returncode == 0, resumed.stderr
    resumed_payload = _last_json(resumed.stdout)
    checkpoint = next(path for path in (interrupted / "work").glob("*/incidents/*.json")
                      if json.loads(path.read_text()).get("scientific_payload_sha256"))
    corrupted_base_root = checkpoint.parent.parent
    corrupted_state_path = corrupted_base_root / "state.json"
    state = json.loads(corrupted_state_path.read_text())
    assert state["checkpoint_hits"] > 0 or state["baseline_checkpoint_hits"] > 0
    resumed_dataset = next((interrupted / "datasets").glob("*.json"))
    resumed_dataset_payload = json.loads(resumed_dataset.read_text())
    resumed_manifest = resumed_dataset_payload["manifest"]
    assert _scientific_artifact_inventory(interrupted) == clean_inventory
    assert BenchmarkDataset.from_json(resumed_dataset).dataset_hash == clean_benchmark.dataset_hash
    corrupted = json.loads(checkpoint.read_text())
    corrupted["graph_policy_provenance"] = {"corrupt": True}
    checkpoint.write_text(json.dumps(corrupted))
    corrupted_payload = json.loads(checkpoint.read_text())
    assert corrupted_payload["scientific_payload_sha256"] != sha256_value(
        _incident_scientific_payload(corrupted_payload))
    _remove_derived_artifacts(interrupted)
    recovered = _run(registry, configs, spec, interrupted, "--resume")
    assert recovered.returncode == 0, recovered.stderr
    recovered_state = json.loads(corrupted_state_path.read_text())
    repaired_payload = json.loads(checkpoint.read_text())
    assert repaired_payload["scientific_payload_sha256"] == sha256_value(
        _incident_scientific_payload(repaired_payload))
    assert recovered_state["checkpoint_invalid"] >= 1
    assert recovered_state["checkpoint_recomputed"] >= 1
    assert recovered_state["checkpoint_hits"] > 0
    assert _scientific_artifact_inventory(interrupted) == clean_inventory
    assert clean_manifest["baseline_sha256"] == resumed_manifest["baseline_sha256"]
    global LAST_RESULT
    LAST_RESULT = {"status": "PASS", "sigterm_resume": "PASS", "corruption_resume": "PASS",
                   "clean_dataset_sha256": _sha(dataset), "resumed_dataset_sha256": _sha(resumed_dataset),
                   "clean_benchmark_hash": clean_benchmark.dataset_hash,
                   "resumed_benchmark_hash": BenchmarkDataset.from_json(resumed_dataset).dataset_hash,
                   "clean_baseline_sha256": clean_manifest["baseline_sha256"],
                   "resumed_baseline_sha256": resumed_manifest["baseline_sha256"],
                   "checkpoint_hits": recovered_state["checkpoint_hits"],
                   "baseline_checkpoint_hits": recovered_state["baseline_checkpoint_hits"],
                   "checkpoint_invalid": recovered_state["checkpoint_invalid"],
                   "checkpoint_recomputed": recovered_state["checkpoint_recomputed"]}
    fixture.close()


def test_prepare_incident_resume_interoperability(tmp_path):
    fixture = _fixture(tmp_path); registry, configs, spec = next(fixture)
    output, incident = registry.parent / "outputs" / "interop", "fixture-3"
    resolved = _cli("resolve-preprocessing-binding", registry, configs, spec, output, incident)
    assert resolved.returncode == 0; binding = _last_json(resolved.stdout)
    prepared = _cli("prepare-baseline", registry, configs, spec, output, incident)
    assert prepared.returncode == 0; baseline = _last_json(prepared.stdout)
    first = _cli("preprocess-incident", registry, configs, spec, output, incident)
    assert first.returncode == 0; first_json = _last_json(first.stdout)
    hit = _cli("preprocess-incident", registry, configs, spec, output, incident, "--resume")
    assert hit.returncode == 0; hit_json = _last_json(hit.stdout)
    assert hit_json["checkpoint_status"] == "HIT" and not hit_json["worker_executed"]
    assert binding["base_key"] == baseline["base_key"] == first_json["base_key"] == hit_json["base_key"]
    assert binding["split_key"] == baseline["split_key"] == first_json["split_key"] == hit_json["split_key"]
    checkpoint = next(path for path in (output / "work" / binding["base_key"] / "incidents").glob("*.json")
                      if json.loads(path.read_text()).get("scientific_payload_sha256"))
    checkpoint_bytes = checkpoint.read_bytes()
    checkpoint_payload_hash = json.loads(checkpoint_bytes)["scientific_payload_sha256"]
    full = _run(registry, configs, spec, output, "--resume")
    assert full.returncode == 0, full.stderr
    assert _last_json(full.stdout)["status"] == "PASS"
    state = json.loads((output / "work" / binding["base_key"] / "state.json").read_text())
    assert state["checkpoint_hits"] >= 1
    assert checkpoint.read_bytes() == checkpoint_bytes or json.loads(checkpoint.read_text())["scientific_payload_sha256"] == checkpoint_payload_hash
    assert [item.name for item in (output / "work").iterdir() if item.is_dir()] == [binding["base_key"]]
    fixture.close()


def test_preprocessing_binding_accepts_train_validation_and_test_incidents(tmp_path):
    fixture = _fixture(tmp_path)
    registry, configs, spec = next(fixture)
    output = registry.parent / "outputs" / "all-splits"
    try:
        bindings = {}
        for number in range(6):
            incident_id = f"fixture-{number}"
            resolved = _cli(
                "resolve-preprocessing-binding", registry, configs, spec,
                output, incident_id,
            )
            assert resolved.returncode == 0, resolved.stderr
            bindings[incident_id] = _last_json(resolved.stdout)

        by_split = {
            split: next(payload for payload in bindings.values()
                        if payload["incident_split"] == split)
            for split in ("train", "validation", "test")
        }
        assert len({payload["base_key"] for payload in by_split.values()}) == 1
        assert len({payload["split_key"] for payload in by_split.values()}) == 1
        counts = {(payload["train_count"], payload["validation_count"],
                   payload["test_count"]) for payload in by_split.values()}
        assert len(counts) == 1

        prepared = {}
        for split in ("test", "train", "validation"):
            incident_id = by_split[split]["incident_id"]
            result = _cli(
                "prepare-baseline", registry, configs, spec, output,
                incident_id, *(('--resume',) if prepared else ()),
            )
            assert result.returncode == 0, result.stderr
            prepared[split] = _last_json(result.stdout)
        assert len({payload["base_key"] for payload in prepared.values()}) == 1
        assert len({payload["split_key"] for payload in prepared.values()}) == 1
        assert len({payload["baseline_sha256"] for payload in prepared.values()}) == 1

        train_id = by_split["train"]["incident_id"]
        processed = _cli(
            "preprocess-incident", registry, configs, spec, output, train_id,
        )
        assert processed.returncode == 0, processed.stderr
        processed_payload = _last_json(processed.stdout)
        checkpoint = json.loads(Path(processed_payload["checkpoint"]).read_text())
        assert checkpoint["status"] == "COMPLETED"
        assert {row["split"] for row in checkpoint["rows"]} == {"train"}
        materialized = _run(registry, configs, spec, output, "--resume")
        assert materialized.returncode == 0, materialized.stderr
        work_bases = {path.name for path in (output / "work").iterdir()
                      if path.is_dir()}
        assert work_bases == {by_split["train"]["base_key"]}
    finally:
        fixture.close()


def test_wait_process_group_exit_observes_natural_exit_without_signalling():
    process = subprocess.Popen(
        ["sh", "-c", "sleep .15 &"],
        start_new_session=True,
    )
    pgid = process.pid
    assert process.wait(timeout=1) == 0
    _wait_process_group_exit(pgid, timeout=5)


def test_feature_worker_failure_then_resume_matches_clean_oracle(tmp_path):
    fixture = _fixture(tmp_path); registry, configs, spec = next(fixture)
    clean = registry.parent / "outputs" / "clean-oracle"
    failed = registry.parent / "outputs" / "worker-failure"
    oracle = _run(registry, configs, spec, clean)
    assert oracle.returncode == 0, oracle.stderr
    # fixture-4 is deliberately non-initial, guaranteeing earlier checkpoints.
    first = _run(registry, configs, spec, failed, env={"CAUSALODS_TEST_FAIL_INCIDENT_WORKER": "fixture-4"})
    assert first.returncode != 0
    failed_states = [path for path in (failed / "work").glob("*/state.json")
                     if json.loads(path.read_text()).get("incident_id") == "fixture-4"]
    assert len(failed_states) == 1
    state_path = failed_states[0]
    state = json.loads(state_path.read_text())
    assert (state["status"], state["phase"], state["worker_kind"], state["incident_id"]) == (
        "FAILED_WORKER", "FEATURES", "FEATURE", "fixture-4")
    completed = [json.loads(path.read_text()) for path in state_path.parent.glob("incidents/*.json")]
    assert any(item.get("status") == "COMPLETED" and item.get("incident_id") != "fixture-4" for item in completed)
    resumed = _run(registry, configs, spec, failed, "--resume")
    assert resumed.returncode == 0, resumed.stderr
    assert _last_json(resumed.stdout)["status"] == "PASS"
    final_state = json.loads(state_path.read_text())
    assert final_state["checkpoint_hits"] > 0
    assert _scientific_artifact_inventory(failed) == _scientific_artifact_inventory(clean)
    fixture.close()


def test_baseline_worker_failure_preserves_diagnostic_and_resumes(tmp_path):
    fixture = _fixture(tmp_path); registry, configs, spec = next(fixture)
    output = registry.parent / "outputs" / "baseline-worker-failure"
    try:
        bindings = {}
        for number in range(6):
            incident = f"fixture-{number}"
            resolved = _cli("resolve-preprocessing-binding", registry, configs, spec,
                            output, incident)
            assert resolved.returncode == 0, resolved.stderr
            bindings[incident] = _last_json(resolved.stdout)
        train_incident = next(key for key, value in bindings.items()
                              if value["incident_split"] == "train")
        failed = _run(registry, configs, spec, output, env={
            "CAUSALODS_TEST_FAIL_BASELINE_WORKER": train_incident,
            "CAUSALODS_TEST_BASELINE_WORKER_STDERR": "SENTINEL_BASELINE_WORKER_ERROR",
        })
        assert failed.returncode != 0
        state_path = output / "work" / bindings[train_incident]["base_key"] / "state.json"
        state = json.loads(state_path.read_text())
        assert state["status"] == "FAILED_WORKER"
        assert state["phase"] == "BASELINE"
        assert state["worker_kind"] == "BASELINE"
        assert state["incident_id"] == train_incident
        assert state["worker_returncode"] != 0
        assert "SENTINEL_BASELINE_WORKER_ERROR" in state["stderr_tail"]
        assert state["worker_spec"].endswith(".worker.json")
        partial = Path(state["worker_spec"].removesuffix(".worker.json") + ".json")
        assert not partial.exists()
        resumed = _run(registry, configs, spec, output, "--resume")
        assert resumed.returncode == 0, resumed.stderr
        assert _last_json(resumed.stdout)["status"] == "PASS"
        assert json.loads(state_path.read_text())["status"] == "COMPLETED"
    finally:
        fixture.close()


def test_corrupt_checkpoint_is_forced_to_rebuild_and_inventory_matches(tmp_path):
    fixture = _fixture(tmp_path); registry, configs, spec = next(fixture)
    output = registry.parent / "outputs" / "corruption"
    clean = _run(registry, configs, spec, output)
    assert clean.returncode == 0, clean.stderr
    oracle = _scientific_artifact_inventory(output)
    checkpoint = next(path for path in (output / "work").glob("*/incidents/*.json")
                      if json.loads(path.read_text()).get("scientific_payload_sha256"))
    corrupted_base_root = checkpoint.parent.parent
    corrupted_state_path = corrupted_base_root / "state.json"
    payload = json.loads(checkpoint.read_text())
    payload["graph_policy_provenance"] = {"corrupt": True}
    checkpoint.write_text(json.dumps(payload))
    corrupted_payload = json.loads(checkpoint.read_text())
    assert corrupted_payload["scientific_payload_sha256"] != sha256_value(
        _incident_scientific_payload(corrupted_payload))
    _remove_derived_artifacts(output)
    recovered = _run(registry, configs, spec, output, "--resume")
    assert recovered.returncode == 0, recovered.stderr
    state = json.loads(corrupted_state_path.read_text())
    repaired_payload = json.loads(checkpoint.read_text())
    assert repaired_payload["scientific_payload_sha256"] == sha256_value(
        _incident_scientific_payload(repaired_payload))
    assert state["checkpoint_invalid"] >= 1
    assert state["checkpoint_recomputed"] >= 1
    assert state["checkpoint_hits"] > 0
    assert _scientific_artifact_inventory(output) == oracle
    fixture.close()


def test_prepare_baseline_persists_monotonic_progress(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path); registry, configs, spec = next(fixture)
    output, incident = registry.parent / "outputs" / "baseline-progress", "fixture-3"
    from experiments.final_campaigns_v3 import cli
    from experiments.final_campaigns_v3 import preprocessing
    observed = []
    original = preprocessing._atomic_json

    def recording_write(path, payload):
        if Path(path).name == "state.json":
            observed.append(dict(payload))
        return original(path, payload)

    monkeypatch.setattr(preprocessing, "_atomic_json", recording_write)
    rc = cli.main(["prepare-baseline", "--registry", str(registry), "--config-dir", str(configs),
        "--model-specification", str(spec), "--output", str(output), "--incident-id", incident,
        "--protocol", "within-dataset", "--seed", "42"])
    assert rc == 0
    progress = [item["completed_baseline"] for item in observed if "completed_baseline" in item]
    final = observed[-1]
    assert progress == sorted(progress)
    assert set(range(1, final["total_baseline"] + 1)).issubset(progress)
    assert final["status"] == "COMPLETED" and final["phase"] == "BASELINE_FINALIZE"
    assert final["completed_baseline"] == final["total_baseline"]
    assert final["baseline_sha256"] and final["base_key"] and final["split_key"]
    fixture.close()
