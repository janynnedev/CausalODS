"""Immutable deterministic scientific-tranche selection for CausalODS v3.

The selector is deliberately independent from feature extraction and model
outputs.  It consumes only the already-frozen planner split and frozen manifest
metadata.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from experiments.multimodal_v3.core import sha256_value


SCHEMA_VERSION = "scientific-tranche-v3"
SELECTION_VERSION = "deterministic-balanced-marginals-v1"
DEFAULT_POLICY = {
    "algorithm": SELECTION_VERSION,
    "dimensions": ["source_system", "quality", "modalities", "fault_family", "event_size_tier"],
    "event_size_policy": "parent-split-rank-quartiles-v1",
    "allocation": "greedy-largest-marginal-deficit-with-seeded-sha256-tie-break-v1",
    "canonical_order": "incident_id-lexicographic-v1",
    "redistribution": "global-best-remaining-deficit-v1",
}
HASH_FIELDS = (
    "schema_version", "tranche_id", "selection_version", "protocol", "seed",
    "parent_base_key", "parent_split_key", "requested_counts", "selection_policy",
)


def _ids(payload: Mapping[str, Any], split: str) -> tuple[str, ...]:
    value = payload.get(f"{split}_incident_ids")
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"SCIENTIFIC_TRANCHE_{split.upper()}_IDS_MALFORMED")
    return tuple(value)


def selection_hash(payload: Mapping[str, Any]) -> str:
    """Hash stable selection semantics; provenance and timestamps are excluded."""
    bound = {name: payload.get(name) for name in HASH_FIELDS}
    for split in ("train", "validation", "test"):
        bound[f"{split}_incident_ids"] = sorted(_ids(payload, split))
    return sha256_value(bound)


def event_size(manifest: Mapping[str, Any]) -> int:
    counts = manifest.get("event_count_by_modality", {})
    if not isinstance(counts, Mapping) or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        for value in counts.values()
    ):
        raise ValueError("SCIENTIFIC_TRANCHE_EVENT_SIZE_METADATA_MALFORMED")
    return int(sum(counts.values()))


def _quality(manifest: Mapping[str, Any]) -> str:
    explicit = manifest.get("integrity_status") or manifest.get("quality_status")
    if explicit in {"CLEAN", "PARTIAL"}:
        return explicit
    accounting = manifest.get("normalization_accounting_by_file", {})
    partial = manifest.get("partially_accepted_row_count", 0) or any(
        isinstance(item, Mapping) and (
            item.get("partially_accepted_rows", 0)
            or item.get("rejected_rows", 0)
            or item.get("rejected_metric_cells", 0)
            or item.get("rejected_row_units", 0)
        ) for item in accounting.values()
    )
    reasons = manifest.get("quality_issue_reasons_by_file", {})
    return "PARTIAL" if partial or any(reasons.values()) else "CLEAN"


def metadata_rows(manifests: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Normalize frozen metadata without loading normalized event payloads."""
    rows = []
    for raw in manifests:
        incident_id = raw.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            raise ValueError("SCIENTIFIC_TRANCHE_INCIDENT_ID_MISSING")
        modalities = raw.get("modalities", raw.get("available_modalities", ()))
        rows.append({
            "incident_id": incident_id,
            "source_system": raw.get("source_system", raw.get("system")),
            "quality": _quality(raw),
            "modalities": "+".join(sorted(modalities)),
            "fault_family": raw.get("fault_type", "other") or "other",
            "event_size": event_size(raw),
        })
    if len({row["incident_id"] for row in rows}) != len(rows):
        raise ValueError("SCIENTIFIC_TRANCHE_METADATA_DUPLICATE_ID")
    return tuple(sorted(rows, key=lambda row: row["incident_id"]))


def _tier_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Assign deterministic rank quartiles inside one parent split.

    Rank tiers avoid platform-dependent interpolation and ensure heavy cases are
    eligible even when many incidents have identical event counts.
    """
    ordered = sorted(rows, key=lambda row: (row["event_size"], row["incident_id"]))
    total = len(ordered)
    tiers = {}
    for index, row in enumerate(ordered):
        tiers[row["incident_id"]] = f"Q{min(4, (index * 4 // max(1, total)) + 1)}"
    return tuple({**row, "event_size_tier": tiers[row["incident_id"]]} for row in rows)


def _tie(seed: int, split: str, incident_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{split}\0{incident_id}".encode()).hexdigest()


def _select_split(rows: Sequence[Mapping[str, Any]], count: int, *, seed: int,
                  split: str) -> tuple[str, ...]:
    if count < 0 or count > len(rows):
        raise ValueError(f"SCIENTIFIC_TRANCHE_QUOTA_UNSATISFIABLE:{split}:{count}:{len(rows)}")
    rows = _tier_rows(rows)
    dimensions = tuple(DEFAULT_POLICY["dimensions"])
    populations = {dimension: Counter(row[dimension] for row in rows) for dimension in dimensions}
    targets = {
        dimension: {value: population * count / len(rows) for value, population in values.items()}
        for dimension, values in populations.items()
    }
    selected: list[Mapping[str, Any]] = []
    observed = {dimension: Counter() for dimension in dimensions}
    remaining = {row["incident_id"]: row for row in rows}
    while len(selected) < count:
        def score(row: Mapping[str, Any]):
            # Positive deficit first.  The coverage term makes rare categories
            # represented before adding a second member of an existing category.
            deficits = sum(max(0.0, targets[d][row[d]] - observed[d][row[d]])
                           / max(1, populations[d][row[d]]) for d in dimensions)
            coverage = sum(observed[d][row[d]] == 0 for d in dimensions)
            return (-coverage, -deficits, _tie(seed, split, row["incident_id"]), row["incident_id"])
        chosen = min(remaining.values(), key=score)
        selected.append(chosen)
        remaining.pop(chosen["incident_id"])
        for dimension in dimensions:
            observed[dimension][chosen[dimension]] += 1
    return tuple(sorted(row["incident_id"] for row in selected))


def build_manifest(*, parent_splits: Mapping[str, Iterable[str]],
                   metadata: Iterable[Mapping[str, Any]], tranche_id: str,
                   requested_counts: Mapping[str, int], protocol: str, seed: int,
                   parent_base_key: str, parent_split_key: str,
                   provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = {row["incident_id"]: row for row in metadata_rows(metadata)}
    selected = {}
    for split in ("train", "validation", "test"):
        parent = tuple(sorted(parent_splits[split]))
        missing = sorted(set(parent) - set(rows))
        if missing:
            raise ValueError(f"SCIENTIFIC_TRANCHE_METADATA_MISSING:{missing[0]}")
        selected[split] = _select_split([rows[item] for item in parent],
                                        int(requested_counts[split]), seed=seed, split=split)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "tranche_id": tranche_id,
        "selection_version": SELECTION_VERSION, "protocol": protocol, "seed": seed,
        "parent_base_key": parent_base_key, "parent_split_key": parent_split_key,
        "requested_counts": {name: int(requested_counts[name])
                             for name in ("train", "validation", "test")},
        "selection_policy": dict(DEFAULT_POLICY),
        **{f"{name}_incident_ids": list(selected[name])
           for name in ("train", "validation", "test")},
    }
    payload["incident_ids"] = sorted(item for values in selected.values() for item in values)
    if provenance:
        payload.update(provenance)
    payload["selection_sha256"] = selection_hash(payload)
    return payload


def validate_manifest(payload: Mapping[str, Any], *, parent_splits: Mapping[str, Iterable[str]],
                      protocol: str, seed: int, parent_base_key: str,
                      parent_split_key: str, expected_feature_registry_sha256: str | None = None,
                      expected_preprocessing_version: str | None = None) -> dict[str, str]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("SCIENTIFIC_TRANCHE_SCHEMA_VERSION_MISMATCH")
    if not isinstance(payload.get("tranche_id"), str) or not payload["tranche_id"].strip():
        raise ValueError("SCIENTIFIC_TRANCHE_ID_MISSING")
    expected = {"protocol": protocol, "seed": seed, "parent_base_key": parent_base_key,
                "parent_split_key": parent_split_key}
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ValueError(f"SCIENTIFIC_TRANCHE_{field.upper()}_MISMATCH")
    if payload.get("selection_version") != SELECTION_VERSION \
            or payload.get("selection_policy") != DEFAULT_POLICY:
        raise ValueError("SCIENTIFIC_TRANCHE_SELECTION_POLICY_MALFORMED")
    requested = payload.get("requested_counts")
    if not isinstance(requested, Mapping):
        raise ValueError("SCIENTIFIC_TRANCHE_COUNTS_MALFORMED")
    parent = {name: set(parent_splits[name]) for name in ("train", "validation", "test")}
    values = {name: _ids(payload, name) for name in parent}
    all_ids = [item for split_ids in values.values() for item in split_ids]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("SCIENTIFIC_TRANCHE_DUPLICATE_OR_OVERLAPPING_IDS")
    for split, split_ids in values.items():
        if len(split_ids) != requested.get(split):
            raise ValueError(f"SCIENTIFIC_TRANCHE_{split.upper()}_COUNT_MISMATCH")
        if not set(split_ids) <= parent[split]:
            raise ValueError(f"SCIENTIFIC_TRANCHE_{split.upper()}_SPLIT_MIGRATION")
    if sorted(all_ids) != payload.get("incident_ids"):
        raise ValueError("SCIENTIFIC_TRANCHE_INCIDENT_IDS_MISMATCH")
    if payload["tranche_id"] == "tranche-a-180" and (requested != {
        "train": 110, "validation": 30, "test": 40
    } or len(all_ids) != 180):
        raise ValueError("SCIENTIFIC_TRANCHE_A_180_COUNTS_MISMATCH")
    if payload.get("selection_sha256") != selection_hash(payload):
        raise ValueError("SCIENTIFIC_TRANCHE_SELECTION_SHA256_MISMATCH")
    optional = (("feature_registry_sha256", expected_feature_registry_sha256),
                ("preprocessing_version", expected_preprocessing_version))
    for field, expected_value in optional:
        if field in payload and expected_value is not None and payload[field] != expected_value:
            raise ValueError(f"SCIENTIFIC_TRANCHE_{field.upper()}_MISMATCH")
    return {
        "TRANCHE_IDS_SUBSET_OF_PARENT": "PASS", "TRAIN_IDS_STAY_IN_TRAIN": "PASS",
        "VALIDATION_IDS_STAY_IN_VALIDATION": "PASS", "TEST_IDS_STAY_IN_TEST": "PASS",
        "NO_SPLIT_OVERLAP": "PASS", "NO_DUPLICATES": "PASS",
        "TRANCHE_COUNTS_110_30_40": "PASS" if len(all_ids) == 180 else "NOT_APPLICABLE",
    }


def complement(parent_splits: Mapping[str, Iterable[str]],
               manifest: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    return {split: tuple(sorted(set(parent_splits[split]) - set(_ids(manifest, split))))
            for split in ("train", "validation", "test")}


def distribution(manifest: Mapping[str, Any], metadata: Iterable[Mapping[str, Any]],
                 parent_splits: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    rows0 = {row["incident_id"]: row for row in metadata_rows(metadata)}
    result = {}
    for split in ("train", "validation", "test"):
        tiered = {row["incident_id"]: row for row in _tier_rows(
            [rows0[item] for item in parent_splits[split]])}
        chosen = [tiered[item] for item in _ids(manifest, split)]
        result[split] = {dimension: dict(sorted(Counter(row[dimension] for row in chosen).items()))
                         for dimension in DEFAULT_POLICY["dimensions"]}
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"SCIENTIFIC_TRANCHE_MANIFEST_INVALID:{path}") from exc
    if not isinstance(value, dict):
        raise ValueError("SCIENTIFIC_TRANCHE_MANIFEST_NOT_OBJECT")
    return value
