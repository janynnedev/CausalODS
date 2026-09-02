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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, default=2)
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards:
        raise ValueError("INVALID_SHARD")
    extension = json.loads((ROOT / "artifacts/final-experiments-v3/h1h2-expansion-gate/extension-manifest.json").read_text())
    output = ROOT / "artifacts/final-experiments-v3/scientific-inputs-h1h2-test-147"
    work = next((output / "work").iterdir())
    spec = json.loads(next((work / "incidents").glob("*.worker.json")).read_text())
    registry = FrozenDatasetRegistry.load(ROOT / "experiments/configs/final-v3/datasets.json", ROOT)
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=Path("/home/janynne/mnt/rcaeval-official-v3"))
    ids = sorted(extension["added_test_incident_ids"])[args.shard::args.shards]
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
                registry=registry, rcaeval_cache=Path("/home/janynne/mnt/rcaeval-official-v3"),
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
                      "shards": args.shards, "incidents": len(ids), "hits": hits},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
