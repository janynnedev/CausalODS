#!/usr/bin/env python3
"""Crash-safe cache-only feature materialization for one extension shard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "correlation-engine")]

from experiments.final_campaigns_v3.core import FrozenDatasetRegistry  # noqa: E402
from experiments.final_campaigns_v3.preprocessing import (  # noqa: E402
    FrozenEvidenceLoader, _baseline_snapshot_bindings, _checkpoint_bindings,
    _checkpoint_name, _load_valid_incident_checkpoint, _run_incident_worker,
)


def shard_incident_ids(extension: dict, *, shard: int, shards: int) -> tuple[str, ...]:
    """Return the deterministic, disjoint frozen shard membership."""
    if shards < 1 or not 0 <= shard < shards:
        raise ValueError("INVALID_SHARD")
    return tuple(sorted(extension["added_test_incident_ids"])[shard::shards])


def validate_shard_partition(extension: dict, shards: int) -> dict:
    expected = set(extension["added_test_incident_ids"])
    memberships = [set(shard_incident_ids(extension, shard=index, shards=shards))
                   for index in range(shards)]
    union = set().union(*memberships) if memberships else set()
    overlap = any(left & right for index, left in enumerate(memberships)
                  for right in memberships[index + 1:])
    return {"shards": shards, "sizes": [len(item) for item in memberships],
            "unique_incidents": len(union), "complete_union": union == expected,
            "disjoint": not overlap,
            "status": "PASS" if union == expected and not overlap else "FAIL"}


def resolve_extension_work(extension: dict, output: Path) -> tuple[Path, dict]:
    """Resolve exactly one scientific work root; never trust an arbitrary spec."""
    binding = json.loads((output / "preprocessing-tranche-binding.json").read_text())
    if (binding.get("expanded_evaluation_tranche_id") != extension["tranche_id"]
            or binding.get("base_extension_selection_sha256") != extension["selection_sha256"]
            or set(binding.get("test_incident_ids", ())) != set(extension["test_incident_ids"])):
        raise RuntimeError("H1_H2_EXTENSION_WORK_BINDING_INVALID")
    candidates = []
    for work in sorted((output / "work").glob("*")):
        state = work / "state.json"
        incidents = work / "incidents"
        if not state.is_file() or not incidents.is_dir():
            continue
        payload = json.loads(state.read_text())
        if payload.get("split_key") != binding.get("selection_sha256"):
            continue
        specs = []
        for path in incidents.glob("*.worker.json"):
            try:
                spec = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if (spec.get("base_key") == payload.get("base_key")
                    and spec.get("split_key") == payload.get("split_key")
                    and spec.get("baseline_sha256")
                    and spec.get("baseline_snapshot_path")
                    and spec.get("baseline_snapshot_bindings")):
                specs.append(spec)
        if specs:
            signatures = {(item["base_key"], item["split_key"], item["baseline_sha256"],
                           item["baseline_snapshot_path"],
                           json.dumps(item["baseline_snapshot_bindings"], sort_keys=True))
                          for item in specs}
            if len(signatures) != 1:
                raise RuntimeError("H1_H2_EXTENSION_WORK_TEMPLATE_AMBIGUOUS")
            candidates.append((work, specs[0]))
    if len(candidates) != 1:
        raise RuntimeError("H1_H2_EXTENSION_WORK_ROOT_AMBIGUOUS")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--rcaeval-cache", type=Path, required=True,
                        help="Read-only, already materialized RCAEval cache.")
    parser.add_argument("--incident-id",
                        help="Materialize one frozen extension incident only.")
    args = parser.parse_args()
    extension = json.loads((ROOT / "artifacts/final-experiments-v3/h1h2-expansion-gate/extension-manifest.json").read_text())
    output = ROOT / "artifacts/final-experiments-v3/scientific-inputs-h1h2-test-147"
    work, spec = resolve_extension_work(extension, output)
    registry = FrozenDatasetRegistry.load(ROOT / "experiments/configs/final-v3/datasets.json", ROOT)
    if not args.rcaeval_cache.is_dir():
        raise RuntimeError("RCAEval_CACHE_UNAVAILABLE")
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=args.rcaeval_cache)
    partition = validate_shard_partition(extension, args.shards)
    if partition["status"] != "PASS":
        raise RuntimeError("H1_H2_EXTENSION_SHARD_PARTITION_FAIL")
    ids = shard_incident_ids(extension, shard=args.shard, shards=args.shards)
    if args.incident_id:
        if args.incident_id not in extension["added_test_incident_ids"]:
            raise RuntimeError("INCIDENT_OUTSIDE_FROZEN_EXTENSION")
        if args.incident_id not in ids:
            raise RuntimeError("INCIDENT_NOT_IN_REQUESTED_SHARD")
        ids = [args.incident_id]
    completed = hits = 0
    for index, incident_id in enumerate(ids, 1):
        source = loader.sources[incident_id]
        target = work / "incidents" / f"{_checkpoint_name(incident_id)}.json"
        bindings = _checkpoint_bindings(
            base_key=spec["base_key"], incident_id=incident_id, source=source,
            baseline_sha256=spec["baseline_sha256"], split_key=spec["split_key"])
        if _load_valid_incident_checkpoint(target, bindings) is not None:
            hits += 1
        else:
            result = _run_incident_worker(
                registry=registry, rcaeval_cache=args.rcaeval_cache,
                incident_id=incident_id, split="test", base_key=spec["base_key"],
                split_key=spec["split_key"],
                baseline_snapshot_path=spec["baseline_snapshot_path"],
                baseline_sha256=spec["baseline_sha256"],
                baseline_snapshot_bindings=spec["baseline_snapshot_bindings"], output=target)
            if result.get("status") == "FAILED_WORKER":
                raise RuntimeError(f"EXTENSION_WORKER_FAILED:{incident_id}:{result}")
        completed += 1
        print(f"SHARD {args.shard + 1}/{args.shards} {completed}/{len(ids)} "
              f"incident={incident_id} hits={hits}", flush=True)
    print(json.dumps({"status": "COMPLETED", "shard": args.shard,
                      "shards": args.shards, "incidents": len(ids), "hits": hits,
                      "shard_partition": partition},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
