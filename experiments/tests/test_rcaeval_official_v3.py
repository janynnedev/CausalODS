from __future__ import annotations

from dataclasses import replace
import inspect
import json
from pathlib import Path

import pandas as pd

from experiments.multimodal_v3.core import (
    DatasetCapabilityResolverV3, DatasetCapabilityV3, ExperimentConfigV3,
    ExperimentPlannerV3,
)
from experiments.rcaeval_official_v3 import (
    INTEGRITY_POLICY_VERSION,
    RCAEvalOfficialMaterializerV3,
    RCAEvalOfficialParquetAdapterV3,
    SUITE_MODALITIES,
    classify_incident_integrity,
    reclassify_checkpoints,
)
from multimodal_v1.public_datasets_v3 import (
    IncidentDescriptor, IncidentManifest, SchemaClassification, SchemaFileReport,
)
from multimodal_v1.public_datasets_v3.contracts import sha256_file, sha256_value
from multimodal_v1.public_datasets_v3.rcaeval import RCAEvalAdapter


CATALOG_DEFAULTS = {
    "dataset": "RE1-OB", "suite": "RE1", "system": "ob",
    "system_name": "Online Boutique", "root_cause_service": "svc",
    "fault": "cpu", "fault_description": "CPU stress", "repetition": 1,
    "inject_time": 1_700_000_001, "n_metrics": 2, "n_timesteps": 2,
    "time_start": 1_700_000_000, "time_end": 1_700_000_001,
    "duration_minutes": 1.0, "normal_timesteps": 1, "faulty_timesteps": 1,
    "has_logs": False, "n_logs": 0, "has_traces": False, "n_traces": 0,
    "has_root_cause_file": False,
}


def _metric_report(*, accepted=1, rejected=0, partial=0, events=1,
                   reasons=None, quality=None):
    reasons = reasons or {}
    cell_rejections = sum(value for key, value in reasons.items()
                          if key.startswith("invalid_metric_cell:"))
    return SchemaFileReport(
        "case/metrics.parquet", "metrics", ("time", "svc_cpu", "svc_mem"),
        accepted + rejected, "a" * 64, (), accepted, rejected, events, reasons,
        SchemaClassification(("time",), ("svc_cpu", "svc_mem"), (), (), ()),
        (), 0, quality or {}, "partial-wide-row-v1",
        accepted - partial, partial, rejected, events, cell_rejections,
        "partial-wide-row-v1",
    )


def _classify(report, **changes):
    payload = {
        "reports": (report,), "expected_modalities": ("metric",),
        "observed_modalities": ("metric",), "total_event_count": report.event_count,
        "event_count_by_modality": {"metric": report.event_count},
        "ground_truth_conflict": None,
    }
    payload.update(changes)
    return classify_incident_integrity(**payload)


def test_integrity_policy_clean_partial_and_blocking_semantics():
    clean = _classify(_metric_report())
    assert clean["integrity_status"] == "CLEAN" and clean["usable_for_benchmark"]

    partial_cell = _metric_report(
        partial=1, reasons={"invalid_metric_cell:svc_mem": 1})
    partial = _classify(partial_cell)
    assert partial["integrity_status"] == "PARTIAL_BUT_USABLE"
    assert partial["usable_for_benchmark"]
    assert partial_cell.rejected_metric_cell_count == 1
    assert partial_cell.rows_partially_accepted == 1

    invalid_row = _metric_report(
        accepted=1, rejected=1, reasons={"invalid_metric_timestamp": 1})
    assert _classify(invalid_row)["integrity_status"] == "PARTIAL_BUT_USABLE"

    zero = _metric_report(
        accepted=0, rejected=1, events=0,
        reasons={"invalid_metric_timestamp": 1})
    assert _classify(zero)["integrity_status"] == "UNUSABLE"
    assert _classify(zero)["usable_for_benchmark"] is False

    missing = _classify(
        _metric_report(), expected_modalities=("metric", "log"),
        observed_modalities=("metric",))
    assert missing["integrity_status"] == "UNUSABLE"

    conflict = _classify(_metric_report(), ground_truth_conflict="conflict")
    assert conflict["integrity_status"] == "UNUSABLE"

    structural = _classify(_metric_report(), structural_errors=("schema_failure",))
    assert structural["integrity_status"] == "UNUSABLE"

    quality = _classify(_metric_report(quality={"metric_service_not_derivable": 1}))
    assert quality["integrity_status"] == "PARTIAL_BUT_USABLE"
    assert quality["usable_for_benchmark"]
    assert "model" not in inspect.signature(classify_incident_integrity).parameters
    assert "prediction" not in inspect.signature(classify_incident_integrity).parameters


def _dataset(tmp_path: Path, *, suite: str = "RE1", count: int = 3) -> Path:
    root = tmp_path / "raw"
    root.mkdir()
    rows = []
    systems = (
        ("ob", "OB", "Online Boutique"),
        ("ss", "SS", "Sock Shop"),
        ("tt", "TT", "Train Ticket"),
    )
    for offset in range(count):
        index = offset + 1
        system, canonical, system_name = systems[offset % len(systems)]
        case = f"{suite.lower()}{system}_svc-{index}_cpu_{index}"
        directory = root / case
        directory.mkdir()
        (directory / "inject_time.txt").write_text("1700000001", encoding="utf-8")
        pd.DataFrame({"time": [1_700_000_000, 1_700_000_001],
                      "svc_cpu": [1.0, 2.0]}).to_parquet(directory / "metrics.parquet")
        row = {**CATALOG_DEFAULTS, "case": case, "suite": suite,
               "dataset": f"{suite}-{canonical}", "system": system,
               "system_name": system_name, "repetition": index}
        rows.append(row)
    pd.DataFrame(rows).to_parquet(root / "cases.parquet")
    return root


def _adapter(tmp_path: Path, root: Path, *, expected_cases: int = 3) \
        -> RCAEvalOfficialParquetAdapterV3:
    return RCAEvalOfficialParquetAdapterV3(
        root, cache_dir=tmp_path / "cache", artifact_dir=tmp_path / "artifacts",
        expected_catalog_cases=expected_cases,
    )


def test_catalog_is_authoritative_and_systems_are_canonical(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    descriptors = adapter.list_incidents()
    assert len(descriptors) == 3
    assert {item.system for item in descriptors} == {"OB", "SS", "TT"}
    assert {adapter.catalog_row(item)["suite"] for item in descriptors} == {"RE1"}
    assert all(adapter.get_ground_truth(item.incident_id)["fault_service"] == "svc"
               for item in descriptors)


def test_catalog_declared_missing_log_is_legitimate_capability(tmp_path):
    root = _dataset(tmp_path, suite="RE2")
    frame = pd.read_parquet(root / "cases.parquet")
    frame.loc[frame.system == "tt", "has_traces"] = True
    tt = frame.loc[frame.system == "tt", "case"].iloc[0]
    pd.DataFrame({"startTime": [1_700_000_000], "serviceName": ["svc"]}).to_parquet(
        root / tt / "traces.parquet"
    )
    frame.to_parquet(root / "cases.parquet")
    adapter = _adapter(tmp_path, root)
    descriptor = next(item for item in adapter.list_incidents() if item.system == "TT")
    assert SUITE_MODALITIES[("RE2", "TT")] == ("metric", "log", "trace")
    assert adapter._modality_policy(descriptor) == (("metric", "trace"), True)


def test_bridge_reuses_legacy_normalization_without_changing_event_semantics(tmp_path):
    root = _dataset(tmp_path)
    adapter = _adapter(tmp_path, root)
    descriptor = adapter.list_incidents()[0]
    path = Path(descriptor.path) / "metrics.parquet"
    official = adapter._parse_incident_results(descriptor)[0]
    legacy = RCAEvalAdapter(root, strict=False)
    legacy_descriptor = IncidentDescriptor(
        descriptor.incident_id, "rcaeval", "RE1-OB", descriptor.case,
        descriptor.path, descriptor.fault_type, descriptor.fault_service,
    )
    frozen = legacy._normalize_metrics(legacy_descriptor, path)
    assert [item.to_dict() for item in official.events] == [item.to_dict() for item in frozen.events]
    assert official.report(root).to_dict() == frozen.report(root).to_dict()
    assert sha256_file(path) == adapter._source_hashes(descriptor)[str(path.relative_to(root))]


def test_materialization_schema_once_resume_and_capability_resolver_are_lightweight(tmp_path):
    root = _dataset(tmp_path)
    adapter = _adapter(tmp_path, root)
    output = tmp_path / "artifacts"
    first = RCAEvalOfficialMaterializerV3(adapter, output)
    report = first.materialize(adapter.list_incidents(), resume=False, mode="test")
    assert report["processed"] == 3
    assert report["global_schema_computations"] == 1
    assert report["heavy_recomputations"] == 3
    manifests = tuple(
        IncidentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((output / "manifests").glob("*.json"))
    )
    assert len({item.dataset_schema_report_sha256 for item in manifests}) == 1
    assert all(item.usable_for_benchmark for item in manifests)
    assert all(DatasetCapabilityV3.from_incident_manifest(item).manifest_sha256 == item.fingerprint
               for item in manifests)

    def forbidden(_descriptor):
        raise AssertionError("resolver não pode reler Parquet após materialização")

    adapter._incident_results = forbidden
    capabilities = DatasetCapabilityResolverV3.resolve({"rcaeval": adapter})
    assert len(capabilities) == 3

    resumed_adapter = _adapter(tmp_path, root)
    resumed = RCAEvalOfficialMaterializerV3(resumed_adapter, output)
    second = resumed.materialize(resumed_adapter.list_incidents(), resume=True, mode="test-resume")
    assert second["cache_hits"] == 3
    assert second["heavy_recomputations"] == 0


def test_partial_policy_propagates_manifest_capability_index_and_planner(tmp_path):
    root = _dataset(tmp_path)
    metric_path = next(path for path in root.rglob("metrics.parquet"))
    for path in root.rglob("metrics.parquet"):
        frame = pd.read_parquet(path)
        frame["svc_mem"] = ["3.0", "invalid"]
        frame.to_parquet(path)
    adapter = _adapter(tmp_path, root)
    descriptor = next(
        item for item in adapter.list_incidents()
        if Path(item.path) == metric_path.parent
    )
    output = tmp_path / "artifacts"
    report = RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        adapter.list_incidents(), resume=False, mode="partial-policy"
    )
    checkpoint = json.loads(
        (output / "checkpoints" / f"{descriptor.incident_id.replace(':', '__')}.json").read_text()
    )
    manifest = IncidentManifest.from_dict(
        json.loads((output / "manifests" /
                    f"{descriptor.incident_id.replace(':', '__')}.json").read_text())
    )
    index = next(
        item for item in json.loads((output / "INDEX.json").read_text())["incidents"]
        if item["incident_id"] == descriptor.incident_id
    )
    capability = DatasetCapabilityV3.from_incident_manifest(manifest)
    assert report["usable_for_benchmark"] == 3
    assert checkpoint["integrity_status"] == "PARTIAL_BUT_USABLE"
    assert checkpoint["usable_for_benchmark"] is True
    assert manifest.normalization_mode == "permissive"
    assert manifest.usable_for_benchmark is True
    assert manifest.empty_incident_reason is None
    assert manifest.rejection_reasons_by_file
    assert any(
        item["rejected_metric_cells"] == 1
        and item["partially_accepted_rows"] == 1
        for item in manifest.normalization_accounting_by_file.values()
    )
    assert capability.usable_for_benchmark is True
    assert index["usable_for_benchmark"] is True
    assert "integrity_policy_usable_for_benchmark" not in index

    manifests = tuple(
        IncidentManifest.from_dict(json.loads(path.read_text()))
        for path in sorted((output / "manifests").glob("*.json"))
    )
    capabilities = tuple(DatasetCapabilityV3.from_incident_manifest(item)
                         for item in manifests)
    planner = ExperimentPlannerV3(ExperimentConfigV3(
        "partial-propagation", capabilities, hypotheses=("H2",),
        protocols=("within-dataset",),
    ))
    partial_runs = planner.plan()
    assert partial_runs and all(item.eligibility for item in partial_runs)

    target_incident_id = partial_runs[0].incident_id
    target_manifest = next(item for item in manifests
                           if item.incident_id == target_incident_id)
    unusable = replace(
        target_manifest, modalities=(), event_count_by_modality={}, observed_modalities=(),
        expected_modalities=("metric",), missing_expected_modalities=("metric",),
        unexpected_modalities=(), total_event_count=0, accepted_row_count=0,
        rejected_row_count=0, considered_row_count=0,
        normalized_source_files=(), normalization_accounting_by_file={},
        rejection_reasons_by_file={}, quality_issue_reasons_by_file={},
        unmapped_fields={}, observed_interval=None, normalization_mode="permissive",
        usable_for_benchmark=False, empty_incident_reason="zero valid metric events",
    )
    unusable_capability = DatasetCapabilityV3.from_incident_manifest(unusable)
    assert not unusable_capability.usable_for_benchmark
    blocking_capabilities = tuple(
        unusable_capability if item.incident_id == target_incident_id else item
        for item in capabilities
    )
    blocked = ExperimentPlannerV3(ExperimentConfigV3(
        "unusable-propagation", blocking_capabilities, hypotheses=("H2",),
        protocols=("within-dataset",),
    )).plan()
    unusable_runs = [item for item in blocked if item.incident_id == target_incident_id]
    assert unusable_runs
    assert all(not item.eligibility for item in unusable_runs)
    assert {item.skip_reason for item in unusable_runs} == {
        "dataset_not_usable_for_benchmark"
    }


def test_changed_source_invalidates_only_its_checkpoint(tmp_path):
    root = _dataset(tmp_path)
    adapter = _adapter(tmp_path, root)
    output = tmp_path / "artifacts"
    RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        adapter.list_incidents(), resume=False, mode="first"
    )
    changed = Path(adapter.list_incidents()[0].path) / "metrics.parquet"
    frame = pd.read_parquet(changed)
    frame.loc[0, "svc_cpu"] = 9.0
    frame.to_parquet(changed)
    second_adapter = _adapter(tmp_path, root)
    report = RCAEvalOfficialMaterializerV3(second_adapter, output).materialize(
        second_adapter.list_incidents(), resume=True, mode="changed"
    )
    assert report["cache_hits"] == 2
    assert report["cache_misses"] == 1
    assert report["heavy_recomputations"] == 1


def test_ground_truth_never_enters_unified_events(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    descriptor = adapter.list_incidents()[0]
    materializer = RCAEvalOfficialMaterializerV3(adapter, tmp_path / "artifacts")
    materializer.materialize((descriptor,), resume=False, mode="anti-leakage")
    events = adapter.normalize_events(descriptor.incident_id)
    forbidden = {"root_cause_service", "fault", "fault_service", "is_root_cause", "target"}
    for event in events:
        payload = event.to_dict()
        assert forbidden.isdisjoint(payload)
        assert forbidden.isdisjoint(payload["attributes"])


def test_processing_is_serial_and_current_cache_is_discarded(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    materializer = RCAEvalOfficialMaterializerV3(adapter, tmp_path / "artifacts")
    materializer.materialize((adapter.list_incidents()[0],), resume=False, mode="serial")
    assert adapter._current_incident_id is None
    assert adapter._current_incident_results is None
    source = inspect.getsource(RCAEvalOfficialMaterializerV3)
    assert "ProcessPoolExecutor" not in source
    assert "multiprocessing" not in source
    assert "joblib" not in source


def test_normalized_hash_matches_canonical_event_stream(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    descriptor = adapter.list_incidents()[0]
    output = tmp_path / "artifacts"
    RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        (descriptor,), resume=False, mode="hash"
    )
    manifest = adapter.load_incident(descriptor.incident_id)
    events = adapter.normalize_events(descriptor.incident_id)
    assert manifest.normalized_events_sha256 == sha256_value(
        [event.to_dict() for event in events]
    )


def test_integrity_policy_change_reuses_cache_without_heavy_recomputation(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    output = tmp_path / "artifacts"
    descriptor = adapter.list_incidents()[0]
    RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        (descriptor,), resume=False, mode="policy-v1-fixture"
    )
    checkpoint_path = next((output / "checkpoints").glob("*.json"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint.pop("integrity_policy_version", None)
    checkpoint["usable_for_benchmark"] = False
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reclassificação metadata-only não pode normalizar RAW")

    adapter._parse_incident_results = forbidden
    result = reclassify_checkpoints(adapter, output)
    updated = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert result["heavy_recomputations"] == 0
    assert result["before_unusable"] == 1
    assert result["after_usable"] == 1
    assert updated["integrity_policy_version"] == INTEGRITY_POLICY_VERSION
    assert updated["integrity_status"] == "CLEAN"
    assert updated["usable_for_benchmark"] is True


def test_worker_resource_metadata_is_persisted(tmp_path):
    adapter = _adapter(tmp_path, _dataset(tmp_path))
    output = tmp_path / "artifacts"
    descriptor = adapter.list_incidents()[0]
    report = RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        (descriptor,), resume=False, mode="worker-resources"
    )
    checkpoint = json.loads(next((output / "checkpoints").glob("*.json")).read_text())
    worker = checkpoint["worker_resources"][0]
    assert worker["worker_peak_rss_bytes"] > 0
    assert worker["worker_elapsed_seconds"] >= 0
    assert worker["source_bytes"] > 0
    assert worker["event_count"] == checkpoint["total_event_count"]
    assert set(worker["phase_timings_seconds"]) == {
        "normalize", "sort", "gzip_intermediate_write",
    }
    assert checkpoint["phase_timings_seconds"][
        "merge_and_parquet_cache_write"
    ] >= 0
    assert report["coordinator_peak_rss_bytes"] > 0
    assert report["max_worker_peak_rss_bytes"] == worker["worker_peak_rss_bytes"]


def test_37_checkpoint_metadata_alignment_preserves_scientific_identity(tmp_path):
    root = _dataset(tmp_path, count=37)
    metric_path = next(root.rglob("metrics.parquet"))
    frame = pd.read_parquet(metric_path)
    frame["svc_mem"] = ["3.0", "invalid"]
    frame.to_parquet(metric_path)
    adapter = _adapter(tmp_path, root, expected_cases=37)
    output = tmp_path / "artifacts"
    RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        adapter.list_incidents(), resume=False, mode="alignment-fixture"
    )
    partial_checkpoint_path = next(
        path for path in (output / "checkpoints").glob("*.json")
        if json.loads(path.read_text())["integrity_status"] == "PARTIAL_BUT_USABLE"
    )
    checkpoint_before = json.loads(partial_checkpoint_path.read_text())
    incident_id = checkpoint_before["identity"]["incident_id"]
    manifest_path = output / "manifests" / f"{incident_id.replace(':', '__')}.json"
    partial_manifest = IncidentManifest.from_dict(json.loads(manifest_path.read_text()))
    legacy_manifest = replace(
        partial_manifest, usable_for_benchmark=False,
        empty_incident_reason="legacy partial-loss limitation",
    )
    manifest_path.write_text(json.dumps(legacy_manifest.to_dict()), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("alinhamento metadata-only não pode normalizar RAW")

    adapter._parse_incident_results = forbidden
    result = reclassify_checkpoints(adapter, output)
    audit = json.loads(
        (output / "manifest-capability-alignment-audit.json").read_text()
    )
    fingerprint = json.loads(
        (output / "manifest-fingerprint-evolution-audit.json").read_text()
    )
    regenerated = IncidentManifest.from_dict(json.loads(manifest_path.read_text()))
    checkpoint_after = json.loads(partial_checkpoint_path.read_text())
    assert result["heavy_recomputations"] == 0
    assert audit["checkpoint_count"] == audit["aligned_count"] == 37
    assert audit["misaligned_count"] == 0
    assert regenerated.usable_for_benchmark
    assert regenerated.fingerprint != legacy_manifest.fingerprint
    assert regenerated.normalized_events_sha256 == partial_manifest.normalized_events_sha256
    assert regenerated.source_files_sha256 == partial_manifest.source_files_sha256
    assert regenerated.ground_truth_sha256 == partial_manifest.ground_truth_sha256
    assert regenerated.normalization_accounting_by_file \
        == partial_manifest.normalization_accounting_by_file
    assert checkpoint_after["normalized_events_sha256"] \
        == checkpoint_before["normalized_events_sha256"]
    row = next(item for item in fingerprint["incidents"]
               if item["incident_id"] == incident_id)
    assert row["manifest_fingerprint_changed"]
    assert not row["normalized_events_sha256_changed"]
    assert not row["source_files_sha256_changed"]
    assert not row["ground_truth_sha256_changed"]
    assert not row["normalization_accounting_changed"]
