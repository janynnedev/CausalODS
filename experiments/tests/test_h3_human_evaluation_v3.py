from __future__ import annotations

import copy
import csv
import json
import socket
from collections import Counter
from pathlib import Path

import pytest

from experiments.scripts.prepare_h3_human_evaluation_v3 import (
    CANONICAL_ROOT,
    HUMAN_ROOT,
    PACKET_FIELDS,
    RATING_DIMENSIONS,
    assign_items,
    audit_assignments,
    audit_reviewer_packets,
    build_items,
    canonical_json_bytes,
    csv_bytes,
    load_canonical,
    render_explanation,
    sanitize_supporting_context,
    sha256_file,
)
from experiments.scripts.validate_h3_human_ratings_v3 import (
    OUTPUT_FIELDS,
    RatingsValidationError,
    validate_and_lock,
    validate_rows,
)


@pytest.fixture(scope="module")
def canonical():
    return load_canonical()


@pytest.fixture(scope="module")
def items(canonical):
    return build_items(canonical)


@pytest.fixture(scope="module")
def assignment_document(items):
    return {
        "schema_version": "h3-human-assignment-map-v1",
        "assignments": assign_items(items),
    }


@pytest.fixture
def valid_ratings(assignment_document):
    return [
        {
            "evaluation_item_id": row["evaluation_item_id"],
            "reviewer_id": row["reviewer_id"],
            **{dimension: "3" for dimension in RATING_DIMENSIONS},
        }
        for row in assignment_document["assignments"]
    ]


def test_canonical_root_is_not_mutated_by_read(canonical):
    before = canonical["sha256sums_sha256"]
    load_canonical()
    assert sha256_file(CANONICAL_ROOT / "SHA256SUMS") == before


def test_exactly_120_canonical_items(items):
    assert len(items) == len({item["evaluation_item_id"] for item in items}) == 120


def test_full_render_is_deterministic_and_ignores_non_contract_fields(canonical):
    result = copy.deepcopy(canonical["runs"][0]["result"])
    first = render_explanation(result)
    result["private_mode"] = "MUST_NOT_RENDER"
    assert render_explanation(result) == first
    assert "MUST_NOT_RENDER" not in first
    for heading in (
        "Root cause", "Summary", "Evidence statements", "Propagation path",
        "Recommendations", "Cited evidence IDs",
    ):
        assert heading in first


def test_preparation_functions_make_no_network_call(monkeypatch, canonical):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert len(assign_items(build_items(canonical))) == 240


def test_assignment_cardinality_balance_and_one_incident(items, assignment_document):
    rows = assignment_document["assignments"]
    audit = audit_assignments(rows, items)
    assert audit["status"] == "PASS"
    assert audit["reviewer_counts"] == {f"R{i:02d}": 40 for i in range(1, 7)}
    assert all(sorted(counts.values(), reverse=True) == [14, 13, 13]
               for counts in audit["condition_counts_per_reviewer"].values())


def test_every_item_exactly_twice(assignment_document):
    counts = Counter(row["evaluation_item_id"] for row in assignment_document["assignments"])
    assert len(counts) == 120
    assert set(counts.values()) == {2}


def test_seed42_assignment_is_byte_stable(items):
    assert canonical_json_bytes(assign_items(items, 42)) == canonical_json_bytes(assign_items(items, 42))


def test_reviewer_packet_has_no_private_fields_and_linux_generic_is_allowed(tmp_path):
    row = {
        "evaluation_item_id": "item-1",
        "item_order": "1",
        "explanation": "Frozen explanation",
        "supporting_context": json.dumps({"kernel": "Linux 6.8.0-52-generic telemetry"}),
        **{dimension: "" for dimension in RATING_DIMENSIONS},
        "reviewer_id": "R01",
    }
    path = tmp_path / "R01.csv"
    path.write_bytes(csv_bytes(PACKET_FIELDS, [row]))
    assert audit_reviewer_packets([path])["status"] == "PASS"


def test_context_redaction_is_minimal_and_structural():
    original = json.dumps({
        "kernel": "Linux 6.8.0-52-generic",
        "nested": {"source_dataset": "private", "metric": 7},
    })
    cleaned, count = sanitize_supporting_context(original)
    assert count == 1
    assert json.loads(cleaned) == {"kernel": "Linux 6.8.0-52-generic", "nested": {"metric": 7}}


def test_generated_packets_are_blinded_and_complete():
    packets = sorted((HUMAN_ROOT / "reviewers").glob("R*.csv"))
    assert len(packets) == 6
    assert audit_reviewer_packets(packets)["status"] == "PASS"
    for path in packets:
        with path.open(newline="", encoding="utf-8") as handle:
            assert len(list(csv.DictReader(handle))) == 40


def test_private_condition_map_preserves_canonical_bytes_and_permissions():
    private = HUMAN_ROOT / "private/condition-map.json"
    canonical = CANONICAL_ROOT / "h3/h3-human-condition-map.json"
    assert private.read_bytes() == canonical.read_bytes()
    assert private.stat().st_mode & 0o777 == 0o600
    assert (HUMAN_ROOT / "private/assignment-map.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("invalid", ["0", "6", "3.5", ""])
def test_invalid_rating_fails(valid_ratings, assignment_document, invalid):
    rows = copy.deepcopy(valid_ratings)
    rows[0]["utility"] = invalid
    with pytest.raises(RatingsValidationError):
        validate_rows(rows, assignment_document)


def test_duplicate_reviewer_item_fails(valid_ratings, assignment_document):
    rows = copy.deepcopy(valid_ratings)
    rows[1] = copy.deepcopy(rows[0])
    with pytest.raises(RatingsValidationError):
        validate_rows(rows, assignment_document)


def test_unassigned_reviewer_item_fails(valid_ratings, assignment_document):
    rows = copy.deepcopy(valid_ratings)
    rows[0]["reviewer_id"] = "R06" if rows[0]["reviewer_id"] != "R06" else "R05"
    with pytest.raises(RatingsValidationError):
        validate_rows(rows, assignment_document)


def test_incomplete_239_rows_fails(valid_ratings, assignment_document):
    with pytest.raises(RatingsValidationError):
        validate_rows(valid_ratings[:-1], assignment_document)


def test_complete_valid_240_rows_pass(valid_ratings, assignment_document):
    audit = validate_rows(valid_ratings, assignment_document)
    assert audit["status"] == "PASS"
    assert audit["total_rating_rows"] == 240
    assert audit["unique_evaluation_item_ids"] == 120


def test_valid_240_rows_are_merged_and_locked_before_unblinding(
    tmp_path, valid_ratings, assignment_document
):
    input_dir = tmp_path / "returned"
    input_dir.mkdir()
    for reviewer in (f"R{i:02d}" for i in range(1, 7)):
        rows = [row for row in valid_ratings if row["reviewer_id"] == reviewer]
        (input_dir / f"{reviewer}.csv").write_bytes(csv_bytes(OUTPUT_FIELDS, rows))
    assignment_path = tmp_path / "assignment.json"
    assignment_path.write_bytes(canonical_json_bytes(assignment_document))
    result = validate_and_lock(input_dir, assignment_path, tmp_path / "ratings")
    assert result["validation"]["status"] == "PASS"
    assert result["lock"]["condition_unblinding"] == "NOT_PERFORMED"
    ratings_path = tmp_path / "ratings/h3-human-ratings.csv"
    assert result["lock"]["ratings_sha256"] == sha256_file(ratings_path)
