#!/usr/bin/env python3
"""Generate the official Tranche A manifest from frozen metadata only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.final_campaigns_v3.core import build_experiment_config, load_campaign_config
from experiments.final_campaigns_v3.scientific_tranche import build_manifest, distribution
from experiments.multimodal_v3.core import (
    DatasetCapabilityV3, ExperimentPlannerV3, FeatureRegistryV3, canonical_json, sha256_value,
)
from experiments.final_campaigns_v3.preprocessing import PREPROCESSING_VERSION


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    dataset = root / "artifacts/scientific-datasets-v3/rcaeval"
    capabilities_payload = json.loads((dataset / "capabilities.json").read_text())
    capabilities = tuple(DatasetCapabilityV3(**item)
                         for item in capabilities_payload["capabilities"])
    config = build_experiment_config(
        load_campaign_config(root / "experiments/configs/final-v3/h1a.json"), capabilities)
    assignments, _ = ExperimentPlannerV3(config).protocol_assignments("within-dataset", 42)
    assignment = next(item for item in assignments if item.applicable)
    splits = {"train": assignment.train_incident_ids,
              "validation": assignment.validation_incident_ids,
              "test": assignment.test_incident_ids}
    split_key = sha256_value({name: sorted(values) for name, values in splits.items()})
    expected_split = "036822facc5c69fc2a547fce9040fc6e01cff7ad56dbea6bebb5d1d036b3eff8"
    if split_key != expected_split:
        raise RuntimeError("FROZEN_PARENT_SPLIT_BINDING_FAIL")
    manifests = [json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted((dataset / "manifests").glob("*.json"))]
    registry_payload = json.loads(
        (root / "experiments/configs/final-v3/datasets.json").read_text())
    manifest = build_manifest(
        parent_splits=splits, metadata=manifests, tranche_id="tranche-a-180",
        requested_counts={"train": 110, "validation": 30, "test": 40},
        protocol="within-dataset", seed=42,
        parent_base_key="955a0f0c36739f676b89d39fb4ddde0442ad3820b239ed3021a052f103830cbe",
        parent_split_key=split_key,
        provenance={
            "generated_from_registry_sha256": sha256_value(registry_payload["datasets"]),
            "feature_registry_sha256": FeatureRegistryV3().fingerprint,
            "preprocessing_version": PREPROCESSING_VERSION,
            "created_by": "experiments/scripts/generate_scientific_tranche_v3.py",
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    summary = {
        "train": len(manifest["train_incident_ids"]),
        "validation": len(manifest["validation_incident_ids"]),
        "test": len(manifest["test_incident_ids"]), "total": len(manifest["incident_ids"]),
        "distribution": distribution(manifest, manifests, splits),
        "selection_sha256": manifest["selection_sha256"],
        "parent_base_key": manifest["parent_base_key"],
        "parent_split_key": manifest["parent_split_key"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
