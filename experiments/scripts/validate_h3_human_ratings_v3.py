#!/usr/bin/env python3
"""Validate, merge, and lock returned H3 reviewer packets before unblinding."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from experiments.scripts.prepare_h3_human_evaluation_v3 import (
        HUMAN_ROOT,
        RATING_DIMENSIONS,
        atomic_write,
        canonical_json_bytes,
        csv_bytes,
        sha256_file,
    )
except ModuleNotFoundError:  # Direct execution from experiments/scripts.
    from prepare_h3_human_evaluation_v3 import (  # type: ignore[no-redef]
        HUMAN_ROOT,
        RATING_DIMENSIONS,
        atomic_write,
        canonical_json_bytes,
        csv_bytes,
        sha256_file,
    )


REVIEWERS = tuple(f"R{index:02d}" for index in range(1, 7))
OUTPUT_FIELDS = ("evaluation_item_id", "reviewer_id", *RATING_DIMENSIONS)


class RatingsValidationError(RuntimeError):
    """Raised when returned ratings violate any frozen study invariant."""


def load_assignment_map(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != "h3-human-assignment-map-v1":
        raise RatingsValidationError("unexpected assignment-map schema")
    return document


def read_rating_files(input_dir: Path) -> list[dict[str, str]]:
    paths = sorted(input_dir.glob("R*.csv"))
    if not paths:
        raise RatingsValidationError(f"no reviewer packets found in {input_dir}")
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not set(OUTPUT_FIELDS).issubset(reader.fieldnames or []):
                raise RatingsValidationError(f"required columns missing from {path.name}")
            rows.extend(dict(row) for row in reader)
    return rows


def validate_rows(rows: Sequence[Mapping[str, str]], assignment_document: Mapping[str, Any]) -> dict[str, Any]:
    assignments = assignment_document.get("assignments", [])
    assigned_pairs = {
        (str(row["evaluation_item_id"]), str(row["reviewer_id"])): row
        for row in assignments
    }
    expected_items = {str(row["evaluation_item_id"]) for row in assignments}
    errors: list[str] = []

    if len(rows) != 240:
        errors.append(f"total rating rows is {len(rows)}, expected 240")
    pairs: list[tuple[str, str]] = []
    item_counts: Counter[str] = Counter()
    reviewer_counts: Counter[str] = Counter()
    reviewer_incidents: Counter[tuple[str, str]] = Counter()
    reviewer_conditions: Counter[tuple[str, str]] = Counter()
    for row_number, row in enumerate(rows, 2):
        item_id = str(row.get("evaluation_item_id", ""))
        reviewer_id = str(row.get("reviewer_id", ""))
        pair = (item_id, reviewer_id)
        pairs.append(pair)
        item_counts[item_id] += 1
        reviewer_counts[reviewer_id] += 1
        assignment = assigned_pairs.get(pair)
        if assignment is None:
            errors.append(f"row {row_number}: reviewer/item pair was not assigned")
        else:
            reviewer_incidents[(reviewer_id, str(assignment["incident_id"]))] += 1
            reviewer_conditions[(reviewer_id, str(assignment["condition_code"]))] += 1
        for dimension in RATING_DIMENSIONS:
            value = str(row.get(dimension, ""))
            if not re.fullmatch(r"[1-5]", value):
                errors.append(f"row {row_number}: {dimension} must be an integer from 1 through 5")

    if set(reviewer_counts) != set(REVIEWERS):
        errors.append("reviewer IDs must be exactly R01 through R06")
    if reviewer_counts != Counter({reviewer: 40 for reviewer in REVIEWERS}):
        errors.append("each reviewer must have exactly 40 rows")
    if len(set(pairs)) != len(pairs):
        errors.append("duplicate evaluation_item_id/reviewer_id pair")
    if set(item_counts) != expected_items or len(item_counts) != 120 or any(count != 2 for count in item_counts.values()):
        errors.append("all 120 assigned items must be rated exactly twice")
    if len(reviewer_incidents) != 240 or any(count != 1 for count in reviewer_incidents.values()):
        errors.append("each reviewer must rate exactly one item per incident")
    for reviewer in REVIEWERS:
        counts = sorted(
            (count for (current, _), count in reviewer_conditions.items() if current == reviewer),
            reverse=True,
        )
        if counts != [14, 13, 13]:
            errors.append(f"{reviewer}: condition balance is not a 14/13/13 permutation")

    if errors:
        raise RatingsValidationError("; ".join(dict.fromkeys(errors)))
    return {
        "schema_version": "h3-human-ratings-validation-v1",
        "status": "PASS",
        "total_rating_rows": len(rows),
        "unique_evaluation_item_ids": len(item_counts),
        "ratings_per_item": 2,
        "reviewer_counts": dict(sorted(reviewer_counts.items())),
        "unique_reviewer_item_pairs": len(set(pairs)),
        "one_item_per_incident_per_reviewer": "PASS",
        "condition_balance_per_reviewer": "PASS",
        "integer_scale_1_5": "PASS",
        "unblinding_performed": False,
    }


def validate_and_lock(input_dir: Path, assignment_path: Path, output_dir: Path) -> dict[str, Any]:
    assignment_document = load_assignment_map(assignment_path)
    rows = read_rating_files(input_dir)
    validation = validate_rows(rows, assignment_document)
    merged = [
        {field: row[field] for field in OUTPUT_FIELDS}
        for row in sorted(rows, key=lambda row: (row["evaluation_item_id"], row["reviewer_id"]))
    ]
    ratings_path = output_dir / "h3-human-ratings.csv"
    atomic_write(ratings_path, csv_bytes(OUTPUT_FIELDS, merged), mode=0o600)
    ratings_hash = sha256_file(ratings_path)
    validation_path = output_dir / "h3-human-ratings-validation.json"
    atomic_write(validation_path, canonical_json_bytes(validation), mode=0o600)
    lock = {
        "schema_version": "h3-human-ratings-lock-v1",
        "status": "LOCKED_BEFORE_UNBLINDING",
        "ratings_file": ratings_path.name,
        "ratings_sha256": ratings_hash,
        "rating_rows": 240,
        "canonical_items": 120,
        "validation_sha256": sha256_file(validation_path),
        "condition_unblinding": "NOT_PERFORMED",
    }
    lock_path = output_dir / "h3-human-ratings-lock.json"
    atomic_write(lock_path, canonical_json_bytes(lock), mode=0o600)
    return {"validation": validation, "lock": lock}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument(
        "--assignment-map",
        type=Path,
        default=HUMAN_ROOT / "private/assignment-map.json",
    )
    parser.add_argument("--output-dir", type=Path, default=HUMAN_ROOT / "ratings")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate_and_lock(args.input_dir, args.assignment_map, args.output_dir)
    print(f"H3_HUMAN_RATINGS_VALIDATION = {result['validation']['status']}")
    print("H3_HUMAN_RATINGS_LOCK = LOCKED_BEFORE_UNBLINDING")
    print("H3_HUMAN_UNBLINDING_STATUS = NOT_PERFORMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
