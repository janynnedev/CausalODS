"""Fail-closed contracts for the frozen-model H1/H2 test-only extension."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from experiments.final_campaigns_v3.scientific_tranche import (
    DEFAULT_POLICY, _tier_rows, _tie, metadata_rows,
)
from experiments.multimodal_v3.core import sha256_value


SCHEMA_VERSION = "scientific-tranche-h1h2-test-extension-v1"
SELECTION_VERSION = "deterministic-balanced-test-extension-v1"
SPOT_VERSION = "deterministic-balanced-historical-spot-check-v1"
FORBIDDEN_SELECTOR_FIELDS = frozenset({
    "root_cause_service", "ground_truth_service", "is_root_cause", "target",
    "prediction_correct", "top1", "top3", "mrr", "fpr", "confidence",
    "probability", "prediction_margin", "predicted_service", "model_score",
})
MODEL_IDENTITY_FIELDS = (
    "serialized_artifact_sha256", "training_split_hash", "validation_split_hash",
    "feature_schema_sha256", "model_specification_hash", "hyperparameters",
)
STRICT_REPRODUCIBILITY_TOLERANCE = 1e-15
SCIENTIFIC_RESULT_FIELDS = (
    "incident_id", "method", "feature_view", "protocol", "seed",
    "predicted_service", "ranked_services", "top1_correct", "top3_correct",
    "reciprocal_rank", "fpr", "confidence", "abstained",
    "feature_schema_sha256", "model_specification_hash",
    "trained_model_artifact_hash", "method_implementation_hash",
)


def ids_sha256(ids: Iterable[str]) -> str:
    return sha256_value(sorted(ids))


def target_decision(parent_test_count: int, base_test_count: int = 40,
                    target: int = 200) -> dict[str, Any]:
    if parent_test_count < base_test_count:
        raise ValueError("H1_H2_PARENT_TEST_CANNOT_CONTAIN_BASE")
    final = min(parent_test_count, target)
    return {
        "target_final_test_count": target,
        "target_200_feasibility": (
            "PASS" if parent_test_count >= target else "NOT_PASS_PARENT_TEST_LIMIT"
        ),
        "maximum_compatible_final_test_count": parent_test_count,
        "final_proposed_test_count": final,
        "added_test_count": final - base_test_count,
    }


def _assert_safe_metadata(raw: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    values = tuple(raw)
    for item in values:
        forbidden = FORBIDDEN_SELECTOR_FIELDS.intersection(item)
        if forbidden:
            raise ValueError(f"H1_H2_OUTCOME_FIELD_IN_SELECTOR:{sorted(forbidden)[0]}")
    return values


def _select_nested(rows: Sequence[Mapping[str, Any]], locked_ids: Iterable[str], count: int,
                   *, seed: int, namespace: str) -> tuple[str, ...]:
    tiered = _tier_rows(rows)
    by_id = {row["incident_id"]: row for row in tiered}
    locked = set(locked_ids)
    if not locked <= set(by_id):
        raise ValueError("H1_H2_LOCKED_TEST_ID_OUTSIDE_PARENT")
    if count < len(locked) or count > len(rows):
        raise ValueError("H1_H2_EXTENSION_QUOTA_UNSATISFIABLE")
    dimensions = tuple(DEFAULT_POLICY["dimensions"])
    populations = {d: Counter(row[d] for row in tiered) for d in dimensions}
    targets = {d: {v: n * count / len(rows) for v, n in pop.items()}
               for d, pop in populations.items()}
    observed = {d: Counter(by_id[item][d] for item in locked) for d in dimensions}
    remaining = {key: row for key, row in by_id.items() if key not in locked}
    additions: list[str] = []
    while len(locked) + len(additions) < count:
        def score(row: Mapping[str, Any]):
            deficits = sum(max(0.0, targets[d][row[d]] - observed[d][row[d]])
                           / max(1, populations[d][row[d]]) for d in dimensions)
            coverage = sum(observed[d][row[d]] == 0 for d in dimensions)
            return (-coverage, -deficits, _tie(seed, namespace, row["incident_id"]),
                    row["incident_id"])
        chosen = min(remaining.values(), key=score)
        additions.append(chosen["incident_id"])
        remaining.pop(chosen["incident_id"])
        for dimension in dimensions:
            observed[dimension][chosen[dimension]] += 1
    return tuple(sorted(additions))


def extension_selection_hash(payload: Mapping[str, Any]) -> str:
    fields = (
        "schema_version", "tranche_id", "base_tranche_id",
        "base_tranche_selection_sha256", "parent_base_key", "parent_split_key",
        "protocol", "seed", "requested_final_test_count", "actual_final_test_count",
        "selection_policy", "selection_version", "train_incident_ids",
        "validation_incident_ids", "locked_base_test_incident_ids",
        "added_test_incident_ids", "test_incident_ids",
    )
    return sha256_value({field: payload.get(field) for field in fields})


def build_extension_manifest(*, base: Mapping[str, Any], parent_test_ids: Iterable[str],
                             metadata: Iterable[Mapping[str, Any]],
                             requested_final_test_count: int = 200) -> dict[str, Any]:
    raw = _assert_safe_metadata(metadata)
    rows = metadata_rows(raw)
    parent = tuple(sorted(parent_test_ids))
    base_test = tuple(sorted(base["test_incident_ids"]))
    decision = target_decision(len(parent), len(base_test), requested_final_test_count)
    additions = _select_nested(
        [row for row in rows if row["incident_id"] in set(parent)], base_test,
        decision["final_proposed_test_count"], seed=42, namespace="h1h2-test-extension",
    )
    final_test = tuple(sorted(set(base_test) | set(additions)))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tranche_id": f"scientific-tranche-h1h2-test-{len(final_test)}",
        "base_tranche_id": "tranche-a-180",
        "base_tranche_selection_sha256": base["selection_sha256"],
        "parent_base_key": base["parent_base_key"],
        "parent_split_key": base["parent_split_key"],
        "protocol": "within-dataset", "seed": 42,
        "train_incident_ids": sorted(base["train_incident_ids"]),
        "validation_incident_ids": sorted(base["validation_incident_ids"]),
        "locked_base_test_incident_ids": list(base_test),
        "added_test_incident_ids": list(additions), "test_incident_ids": list(final_test),
        "requested_final_test_count": requested_final_test_count,
        "actual_final_test_count": len(final_test), "added_test_count": len(additions),
        "selection_policy": {
            **DEFAULT_POLICY, "allocation": "locked-base-final-union-marginal-deficit-v1",
            "selector_inputs": list(DEFAULT_POLICY["dimensions"]),
            "outcome_fields_permitted": False,
        },
        "selection_version": SELECTION_VERSION,
        "train_incident_ids_sha256": ids_sha256(base["train_incident_ids"]),
        "validation_incident_ids_sha256": ids_sha256(base["validation_incident_ids"]),
        "base_test_incident_ids_sha256": ids_sha256(base_test),
        "expanded_test_incident_ids_sha256": ids_sha256(final_test),
        **decision,
    }
    payload["selection_sha256"] = extension_selection_hash(payload)
    validate_extension_manifest(payload, base=base, parent_test_ids=parent)
    return payload


def validate_extension_manifest(payload: Mapping[str, Any], *, base: Mapping[str, Any],
                                parent_test_ids: Iterable[str]) -> None:
    locked, added, final = (set(payload[name]) for name in (
        "locked_base_test_incident_ids", "added_test_incident_ids", "test_incident_ids"))
    train, validation = set(payload["train_incident_ids"]), set(payload["validation_incident_ids"])
    if sorted(payload["train_incident_ids"]) != sorted(base["train_incident_ids"]):
        raise ValueError("H1_H2_TRAIN_IDS_CHANGED")
    if sorted(payload["validation_incident_ids"]) != sorted(base["validation_incident_ids"]):
        raise ValueError("H1_H2_VALIDATION_IDS_CHANGED")
    if locked != set(base["test_incident_ids"]) or locked & added or final != locked | added:
        raise ValueError("H1_H2_NESTEDNESS_FAIL")
    if not final <= set(parent_test_ids):
        raise ValueError("H1_H2_EXPANDED_TEST_OUTSIDE_PARENT")
    if train & validation or train & final or validation & final:
        raise ValueError("H1_H2_SPLIT_ISOLATION_AUDIT_FAIL")
    if payload.get("selection_sha256") != extension_selection_hash(payload):
        raise ValueError("H1_H2_EXTENSION_SELECTION_SHA256_MISMATCH")


def build_spot_selection(*, base_test_ids: Iterable[str],
                         metadata: Iterable[Mapping[str, Any]], seed: int = 42) -> dict[str, Any]:
    raw = _assert_safe_metadata(metadata)
    allowed = set(base_test_ids)
    rows = [row for row in metadata_rows(raw) if row["incident_id"] in allowed]
    if len(rows) != len(allowed):
        raise ValueError("H1_H2_SPOT_METADATA_INCOMPLETE")
    selected = _select_nested(rows, (), 5, seed=seed, namespace="h1h2-spot-check")
    payload = {"spot_check_selection_version": SPOT_VERSION,
               "spot_check_seed": seed, "selected_incident_ids": list(selected)}
    payload["selection_sha256"] = sha256_value(payload)
    return payload


def validate_model_identity(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    for field in MODEL_IDENTITY_FIELDS:
        left, right = expected.get(field), actual.get(field)
        if field == "hyperparameters":
            left = json.loads(json.dumps(left, sort_keys=True, default=list))
            right = json.loads(json.dumps(right, sort_keys=True, default=list))
        if left != right:
            raise ValueError(f"H1_H2_MODEL_IDENTITY_MISMATCH:{field}")


def compare_spot_records(expected: Mapping[str, Any], actual: Mapping[str, Any],
                         *, tolerance: float = STRICT_REPRODUCIBILITY_TOLERANCE
                         ) -> tuple[bool, list[str]]:
    mismatches = []
    for field in SCIENTIFIC_RESULT_FIELDS:
        if field not in expected and field not in actual:
            continue
        left, right = expected.get(field), actual.get(field)
        if isinstance(left, float) and isinstance(right, float):
            if abs(left - right) > tolerance:
                mismatches.append(field)
        elif left != right:
            mismatches.append(field)
    return not mismatches, mismatches


def compare_spot_records_detailed(
    expected: Mapping[str, Any], actual: Mapping[str, Any],
    *, tolerance: float = STRICT_REPRODUCIBILITY_TOLERANCE,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Compare raw values and expose strict numeric audit accounting."""
    ok, mismatches = compare_spot_records(expected, actual, tolerance=tolerance)
    deltas = []
    for field in SCIENTIFIC_RESULT_FIELDS:
        if field not in expected or field not in actual:
            continue
        left, right = expected[field], actual[field]
        if (isinstance(left, (int, float)) and not isinstance(left, bool)
                and isinstance(right, (int, float)) and not isinstance(right, bool)):
            deltas.append(abs(float(left) - float(right)))
    return ok, mismatches, {
        "numeric_comparisons_checked": len(deltas),
        "numeric_comparisons_failed": sum(delta > tolerance for delta in deltas),
        "maximum_absolute_numeric_delta": max(deltas, default=0.0),
    }


def validate_heuristic_identity(expected_hash: str, actual_hash: str) -> None:
    if expected_hash != actual_hash:
        raise ValueError("H1_H2_HEURISTIC_IDENTITY_MISMATCH")


def aggregate_observations(base: Sequence[Mapping[str, Any]],
                           extension: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    combined = tuple(base) + tuple(extension)
    identities = [(row.get("family"), row.get("incident_id"), row.get("method"), row.get("feature_view"),
                   row.get("protocol"), row.get("seed")) for row in combined]
    if len(identities) != len(set(identities)):
        raise ValueError("H1_H2_AGGREGATION_DUPLICATE_RUN_IDENTITY")
    return combined


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
