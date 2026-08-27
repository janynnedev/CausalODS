"""Build a metadata-only, immutable-derived RCAEval validation registry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil



ROOT = Path(__file__).resolve().parents[2]
OFFICIAL = ROOT / "artifacts/scientific-datasets-v3/rcaeval"
OUTPUT = ROOT / "artifacts/validation/rcaeval-stratified-gate-v3"
HEAVY = frozenset({"rcaeval:re3tt_ts-route-service_f3_6", "rcaeval:re2tt_ts-route-service_socket_1"})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _official_snapshot(paths=None) -> dict[str, str]:
    fixed = (
        OFFICIAL / "INDEX.json", OFFICIAL / "capabilities.json", OFFICIAL / "SHA256SUMS",
        OFFICIAL / "materialization-report.json")
    selected = fixed if paths is None else tuple(paths)
    return {str(path.relative_to(OFFICIAL)): _sha(path) for path in selected}


def _snapshot_sha256(snapshot: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(output: Path = OUTPUT) -> dict:
    before = _official_snapshot()
    index, capabilities = _read(OFFICIAL / "INDEX.json"), _read(OFFICIAL / "capabilities.json")
    by_id = {item["incident_id"]: item for item in capabilities["capabilities"]}
    groups = {}
    for item in index["incidents"]:
        key = (item["suite"], item["system"], tuple(item["modalities"]), item["integrity_status"])
        groups.setdefault(key, []).append(item)
    selected = []
    for key in sorted(groups, key=str):
        candidates = sorted(groups[key], key=lambda item: item["incident_id"])
        chosen = next((item for item in candidates if item["incident_id"] not in HEAVY), None)
        if chosen is None:
            raise RuntimeError(f"HEAVY_ONLY_STRATUM:{key}")
        selected.append(chosen)
    if len(selected) != 16:
        raise RuntimeError(f"EXPECTED_16_STRATA_GOT:{len(selected)}")
    selected_manifest_paths = tuple(OFFICIAL / item["manifest_path"] for item in selected)
    selected_manifests_before = _official_snapshot(selected_manifest_paths)
    dataset = output / "dataset"
    if output.exists():
        shutil.rmtree(output)
    (dataset / "manifests").mkdir(parents=True)
    selections = []
    for item in selected:
        source, target = OFFICIAL / item["manifest_path"], dataset / item["manifest_path"]
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        # Preserve source bytes exactly; manifest_sha256 below preserves the
        # official frozen identity carried by INDEX.json.
        if _sha(source) != _sha(target):
            raise RuntimeError(f"MANIFEST_COPY_MISMATCH:{item['incident_id']}")
        selections.append({"incident_id": item["incident_id"], "suite": item["suite"], "system": item["system"],
            "modalities": item["modalities"], "integrity_status": item["integrity_status"], "fault": item["fault"],
            "root_cause_service": item["root_cause_service"], "manifest_sha256": item["manifest_sha256"],
            "manifest_path": item["manifest_path"],
            "cache_identity": item["cache_identity"], "selection_reason": "lexicographic-first-in-stratum"})
    subset = {item["incident_id"] for item in selected}
    subset_index = {"version": index.get("version"), "count": len(selected), "incidents": selected}
    subset_caps = {"count": len(selected), "capabilities": [by_id[item["incident_id"]] for item in selected]}
    material = {"valid": len(selected), "usable_for_benchmark": len(selected), "unusable": 0, "errors": 0}
    for name, value in (("INDEX.json", subset_index), ("capabilities.json", subset_caps), ("materialization-report.json", material)):
        (dataset / name).write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    tracked = sorted(path for path in dataset.rglob("*") if path.is_file())
    sums = "".join(f"{_sha(path)}  {path.relative_to(dataset)}\n" for path in tracked)
    (dataset / "SHA256SUMS").write_text(sums)
    registry = {"schema_version": "frozen-scientific-dataset-registry-v3", "datasets": [{"dataset_id": "rcaeval",
        "kind": "rcaeval", "root": str(dataset.relative_to(ROOT)), "frozen": True, "expected_incidents": 16,
        "expected_usable": 16, "checksum_manifest": "SHA256SUMS", "checksum_manifest_sha256": _sha(dataset / "SHA256SUMS"),
        "index": "INDEX.json", "capabilities": "capabilities.json", "materialization_report": "materialization-report.json"}]}
    (output / "datasets.json").write_text(json.dumps(registry, sort_keys=True, separators=(",", ":")) + "\n")
    after = _official_snapshot()
    selected_manifests_after = _official_snapshot(selected_manifest_paths)
    if before != after or selected_manifests_before != selected_manifests_after:
        raise RuntimeError("OFFICIAL_RCAEVAL_MUTATED")
    summary = {"suites": sorted({item["suite"] for item in selected}),
               "systems": sorted({item["system"] for item in selected}),
               "modality_profiles": sorted({tuple(item["modalities"]) for item in selected}, key=str),
               "integrity_statuses": sorted({item["integrity_status"] for item in selected}),
               "total_strata": len(selected)}
    report = {"schema_version": "rcaeval-stratified-gate-v3", "total_strata": len(selected), "summary": summary, "selected": selections,
              "official_rcaeval_mutated": False,
              "official_metadata_snapshot_sha256": _snapshot_sha256(before),
              "official_selected_manifests_snapshot_sha256": _snapshot_sha256(selected_manifests_before)}
    (output / "selection.json").write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=OUTPUT)
    print(json.dumps(build(parser.parse_args().output), sort_keys=True))
