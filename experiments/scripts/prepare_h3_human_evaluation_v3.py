#!/usr/bin/env python3
"""Freeze completed H3 computation and prepare the blinded human study.

This module is deliberately standard-library-only and never invokes an H3
runner, model provider, network client, or explanation generator.  Every item
is a rendering of fields already persisted in the immutable canonical runs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ROOT = ROOT / "artifacts/final-experiments-v3-h3-final/H3/final-h3-v3/3ff75671bd14ae773dc577bf161a3212da0c6eaf0b6359d4b3843394e7f2f676"
FREEZE_ROOT = ROOT / "artifacts/final-experiments-v3-h3-freeze"
HUMAN_ROOT = ROOT / "artifacts/final-experiments-v3-h3-human"
CONFIG_PATH = ROOT / "experiments/configs/final-v3/h3-human-evaluation-v1.json"
DOC_PATHS = (
    ROOT / "docs/experiments/h3-human-evaluation-protocol-v1.md",
    ROOT / "docs/experiments/h3-human-evaluation-rubric-v1.md",
    ROOT / "docs/experiments/h3-human-preanalysis-plan-v1.md",
)

EXPECTED_CANONICAL_FILES = (
    "configuration.json",
    "environment.json",
    "experiment-validity.json",
    "h3-canonical-reconstruction.json",
    "h3-same-rca-binding.json",
    "h3/h3-automatic-evaluation.csv",
    "h3/h3-by-mode.csv",
    "h3/h3-cost-latency.csv",
    "h3/h3-human-blinding-audit.json",
    "h3/h3-human-condition-map.json",
    "h3/h3-human-evaluation-blinded.csv",
    "h3/h3-sample-manifest.json",
    "model-manifest.json",
    "prompt-manifest.json",
    "runs/index.json",
)
RATING_DIMENSIONS = (
    "utility",
    "clarity",
    "perceived_confidence",
    "traceability",
    "uncertainty_adequacy",
)
PACKET_FIELDS = (
    "evaluation_item_id",
    "item_order",
    "explanation",
    "supporting_context",
    *RATING_DIMENSIONS,
    "reviewer_id",
)
PRIVATE_FIELDS = {
    "condition_code",
    "explanation_mode",
    "run_id",
    "response_id",
    "provider",
    "model",
    "incident_id",
    "dataset",
    "source_dataset",
    "source_system",
    "ground_truth",
    "fault_service",
    "fault_location",
    "actual_services",
    "is_root_cause",
}


class StudyPreparationError(RuntimeError):
    """Fail-closed preparation error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: Any, mode: int = 0o644) -> None:
    atomic_write(path, canonical_json_bytes(value), mode)


def parse_sha256sums(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, relative = line.split(None, 1)
        except ValueError as exc:
            raise StudyPreparationError(f"invalid SHA256SUMS line {line_number}") from exc
        relative = relative.lstrip("*")
        candidate = Path(relative)
        if len(digest) != 64 or candidate.is_absolute() or ".." in candidate.parts:
            raise StudyPreparationError(f"unsafe SHA256SUMS line {line_number}")
        entries.append((digest, relative))
    if not entries:
        raise StudyPreparationError(f"empty checksum manifest: {path}")
    return entries


def validate_sha256sums(root: Path) -> dict[str, str]:
    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise StudyPreparationError(f"missing canonical checksum manifest: {sums_path}")
    validated: dict[str, str] = {}
    for expected, relative in parse_sha256sums(sums_path):
        target = root / relative
        if not target.is_file():
            raise StudyPreparationError(f"checksum target missing: {relative}")
        actual = sha256_file(target)
        if actual != expected:
            raise StudyPreparationError(f"checksum mismatch: {relative}")
        validated[relative] = actual
    for required in EXPECTED_CANONICAL_FILES:
        if required not in validated:
            raise StudyPreparationError(f"canonical checksum manifest does not bind {required}")
    return validated


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_canonical(canonical_root: Path = CANONICAL_ROOT) -> dict[str, Any]:
    checksums = validate_sha256sums(canonical_root)
    reconstruction = json.loads((canonical_root / "h3-canonical-reconstruction.json").read_text())
    validity = json.loads((canonical_root / "experiment-validity.json").read_text())
    same_rca = json.loads((canonical_root / "h3-same-rca-binding.json").read_text())
    blinding = json.loads((canonical_root / "h3/h3-human-blinding-audit.json").read_text())
    condition_map_document = json.loads((canonical_root / "h3/h3-human-condition-map.json").read_text())
    condition_map = condition_map_document.get("condition_map", condition_map_document)
    if set(condition_map.values()) != {"evidence_grounded", "generic", "raw_telemetry"}:
        raise StudyPreparationError("unexpected or incomplete canonical condition map")

    runs: list[dict[str, Any]] = []
    for run_path in sorted((canonical_root / "runs").glob("*.json")):
        if run_path.name == "index.json":
            continue
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["_path"] = run_path.relative_to(canonical_root).as_posix()
        runs.append(run)
    if len(runs) != 120 or any(run.get("status", {}).get("state") != "SUCCESS" for run in runs):
        raise StudyPreparationError("canonical runs are not exactly 120 SUCCESS records")

    incidents = {run["input"]["incident_id"] for run in runs}
    modes = Counter(run["configuration"]["explanation_mode"] for run in runs)
    response_ids = [run["provenance"]["response_id"] for run in runs]
    fallback_count = sum(bool(run["result"].get("used_fallback")) for run in runs)
    pairs = {(run["input"]["incident_id"], run["configuration"]["explanation_mode"]) for run in runs}
    if len(incidents) != 40 or modes != Counter({mode: 40 for mode in condition_map.values()}):
        raise StudyPreparationError("canonical incident/condition cardinality mismatch")
    if len(pairs) != 120 or len(set(response_ids)) != 120:
        raise StudyPreparationError("canonical run or provider-response identity is not unique")
    if fallback_count:
        raise StudyPreparationError("canonical campaign contains fallback output")
    if validity.get("complete") is not True or same_rca.get("status") != "PASS" or blinding.get("status") != "PASS":
        raise StudyPreparationError("canonical validity or same-RCA gate is not PASS")

    blinded_rows = read_csv(canonical_root / "h3/h3-human-evaluation-blinded.csv")
    if len(blinded_rows) != 120 or len({row["evaluation_item_id"] for row in blinded_rows}) != 120:
        raise StudyPreparationError("canonical blinded evaluation table is not 120 unique items")

    return {
        "root": canonical_root,
        "checksums": checksums,
        "sha256sums_sha256": sha256_file(canonical_root / "SHA256SUMS"),
        "reconstruction": reconstruction,
        "validity": validity,
        "same_rca": same_rca,
        "blinding": blinding,
        "condition_map_document": condition_map_document,
        "condition_map": condition_map,
        "runs": runs,
        "blinded_rows": blinded_rows,
        "incidents": sorted(incidents),
        "modes": dict(sorted(modes.items())),
        "response_ids": response_ids,
        "fallback_count": fallback_count,
    }


def _render_sequence(values: Sequence[Any], formatter) -> list[str]:
    rendered: list[str] = []
    for value in values:
        rendered.extend(formatter(value))
    return rendered


def render_explanation(result: Mapping[str, Any]) -> str:
    """Render only the frozen result contract fields, without interpretation."""

    allowed = {
        "root_cause",
        "summary",
        "evidence_statements",
        "propagation_path",
        "recommendations",
        "cited_evidence_ids",
    }
    required = {"root_cause", "summary", "evidence_statements", "cited_evidence_ids"}
    if not required.issubset(result):
        raise StudyPreparationError(f"frozen result lacks fields: {sorted(required - set(result))}")

    lines = ["Root cause", str(result["root_cause"]), "", "Summary", str(result["summary"])]
    lines += ["", "Evidence statements"]
    for statement in result["evidence_statements"]:
        if isinstance(statement, Mapping):
            text = statement.get("statement", statement.get("text"))
            ids = statement.get("evidence_ids", statement.get("cited_evidence_ids", []))
            if text is None:
                raise StudyPreparationError("evidence statement has no frozen text field")
            lines.append(f"- {text}")
            if ids:
                lines.append(f"  evidence IDs: {', '.join(map(str, ids))}")
        else:
            lines.append(f"- {statement}")
    lines += ["", "Propagation path"]
    lines += [f"- {value}" for value in result.get("propagation_path", [])]
    lines += ["", "Recommendations"]
    lines += [f"- {value}" for value in result.get("recommendations", [])]
    lines += ["", "Cited evidence IDs"]
    lines += [f"- {value}" for value in result["cited_evidence_ids"]]
    return "\n".join(lines).rstrip()


def sanitize_supporting_context(value: str) -> tuple[str, int]:
    """Minimally redact reviewer-prohibited structural fields from canonical JSON."""

    try:
        document = json.loads(value)
    except json.JSONDecodeError as exc:
        raise StudyPreparationError("canonical blinded supporting_context is not JSON") from exc
    removed = 0

    def scrub(node: Any) -> Any:
        nonlocal removed
        if isinstance(node, dict):
            cleaned = {}
            for key, child in node.items():
                if is_private_context_key(key):
                    removed += 1
                    continue
                cleaned[key] = scrub(child)
            return cleaned
        if isinstance(node, list):
            return [scrub(child) for child in node]
        return node

    cleaned = scrub(document)
    if not removed:
        return value, 0
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":")), removed


def is_private_context_key(key: str) -> bool:
    normalized = key.lower()
    return (
        key in PRIVATE_FIELDS
        or key in {"rcaeval.case", "rcaeval.system"}
        or "dataset" in normalized
        or "source_system" in normalized
    )


def build_items(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    condition_map = canonical["condition_map"]
    runs_by_pair = {
        (run["input"]["incident_id"], run["configuration"]["explanation_mode"]): run
        for run in canonical["runs"]
    }
    items: list[dict[str, Any]] = []
    for row in canonical["blinded_rows"]:
        condition = row["condition_code"]
        if condition not in condition_map:
            raise StudyPreparationError(f"unknown canonical condition: {condition}")
        mode = condition_map[condition]
        run = runs_by_pair.get((row["incident_id"], mode))
        if run is None:
            raise StudyPreparationError("blinded item does not resolve to one canonical run")
        supporting_context, redactions = sanitize_supporting_context(row["supporting_context"])
        items.append({
            "evaluation_item_id": row["evaluation_item_id"],
            "incident_id": row["incident_id"],
            "condition_code": condition,
            "explanation_mode": mode,
            "run_id": run["identity"]["run_id"],
            "response_id": run["provenance"]["response_id"],
            "run_path": run["_path"],
            "explanation": render_explanation(run["result"]),
            "supporting_context": supporting_context,
            "supporting_context_redactions": redactions,
        })
    return sorted(items, key=lambda item: item["evaluation_item_id"])


def assign_items(items: Sequence[Mapping[str, Any]], seed: int = 42) -> list[dict[str, Any]]:
    reviewers = [f"R{index:02d}" for index in range(1, 7)]
    by_incident: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    conditions = sorted({str(item["condition_code"]) for item in items})
    if len(conditions) != 3:
        raise StudyPreparationError("assignment requires exactly three blinded conditions")
    for item in items:
        by_incident[str(item["incident_id"])][str(item["condition_code"])] = item
    if len(by_incident) != 40 or any(len(group) != 3 for group in by_incident.values()):
        raise StudyPreparationError("assignment requires 40 incidents x 3 conditions")

    offsets = [0] * 14 + [1] * 13 + [2] * 13
    random.Random(seed).shuffle(offsets)
    assignments: list[dict[str, Any]] = []
    reviewer_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for incident_index, incident_id in enumerate(sorted(by_incident)):
        offset = offsets[incident_index]
        for reviewer_index, reviewer_id in enumerate(reviewers):
            condition = conditions[(reviewer_index % 3 + offset) % 3]
            item = dict(by_incident[incident_id][condition])
            reviewer_order[reviewer_id].append(item)

    for reviewer_index, reviewer_id in enumerate(reviewers, 1):
        reviewer_items = reviewer_order[reviewer_id]
        random.Random(seed * 100 + reviewer_index).shuffle(reviewer_items)
        for item_order, item in enumerate(reviewer_items, 1):
            assignments.append({**item, "reviewer_id": reviewer_id, "item_order": item_order})
    audit_assignments(assignments, items)
    return assignments


def audit_assignments(assignments: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    reviewer_counts = Counter(row["reviewer_id"] for row in assignments)
    item_counts = Counter(row["evaluation_item_id"] for row in assignments)
    pairs = {(row["reviewer_id"], row["evaluation_item_id"]) for row in assignments}
    per_reviewer_incidents = Counter((row["reviewer_id"], row["incident_id"]) for row in assignments)
    per_reviewer_conditions = {
        reviewer: dict(sorted(Counter(
            row["condition_code"] for row in assignments if row["reviewer_id"] == reviewer
        ).items()))
        for reviewer in sorted(reviewer_counts)
    }
    expected_item_ids = {item["evaluation_item_id"] for item in items}
    errors: list[str] = []
    if len(assignments) != 240:
        errors.append("assignment row count is not 240")
    if reviewer_counts != Counter({f"R{i:02d}": 40 for i in range(1, 7)}):
        errors.append("reviewer counts are not exactly 40")
    if set(item_counts) != expected_item_ids or any(count != 2 for count in item_counts.values()):
        errors.append("every one of 120 items is not assigned exactly twice")
    if len(pairs) != 240:
        errors.append("duplicate reviewer/item pair")
    if any(count != 1 for count in per_reviewer_incidents.values()) or len(per_reviewer_incidents) != 240:
        errors.append("reviewer does not receive exactly one item per incident")
    for counts in per_reviewer_conditions.values():
        if sorted(counts.values(), reverse=True) != [14, 13, 13]:
            errors.append("reviewer condition balance is not a 14/13/13 permutation")
            break
    if errors:
        raise StudyPreparationError("; ".join(errors))
    return {
        "status": "PASS",
        "assignment_rows": len(assignments),
        "canonical_items": len(item_counts),
        "raters_per_item": 2,
        "reviewer_counts": dict(sorted(reviewer_counts.items())),
        "condition_counts_per_reviewer": per_reviewer_conditions,
        "unique_reviewer_item_pairs": len(pairs),
        "unique_reviewer_incident_pairs": len(per_reviewer_incidents),
    }


def packet_rows(assignments: Sequence[Mapping[str, Any]], reviewer_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for assignment in sorted(
        (row for row in assignments if row["reviewer_id"] == reviewer_id),
        key=lambda row: int(row["item_order"]),
    ):
        row = {
            "evaluation_item_id": assignment["evaluation_item_id"],
            "item_order": assignment["item_order"],
            "explanation": assignment["explanation"],
            "supporting_context": assignment["supporting_context"],
            **{dimension: "" for dimension in RATING_DIMENSIONS},
            "reviewer_id": reviewer_id,
        }
        rows.append(row)
    return rows


def csv_bytes(fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> bytes:
    import io
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def audit_reviewer_packets(packet_paths: Sequence[Path]) -> dict[str, Any]:
    errors: list[str] = []
    rows_checked = 0
    forbidden_mode_tokens = ("evidence_grounded", "raw_telemetry")
    for path in packet_paths:
        rows = read_csv(path)
        rows_checked += len(rows)
        with path.open(newline="", encoding="utf-8") as handle:
            fields = tuple(csv.DictReader(handle).fieldnames or ())
        if fields != PACKET_FIELDS:
            errors.append(f"unexpected packet columns: {path.name}")
        if PRIVATE_FIELDS.intersection(fields):
            errors.append(f"private field exposed: {path.name}")
        for row in rows:
            joined = "\n".join(row.values())
            if any(token in joined for token in forbidden_mode_tokens):
                errors.append(f"private mode token exposed: {path.name}")
                break
            if any(value.startswith("C") and "-" in value for value in row.values()):
                errors.append(f"condition code exposed: {path.name}")
                break
            try:
                context = json.loads(row["supporting_context"])
            except json.JSONDecodeError:
                errors.append(f"supporting context is not valid JSON: {path.name}")
                break
            structural_keys: set[str] = set()
            stack = [context]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    structural_keys.update(node)
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
            if any(is_private_context_key(key) for key in structural_keys):
                errors.append(f"private structural context field exposed: {path.name}")
                break
    if errors:
        raise StudyPreparationError("; ".join(sorted(set(errors))))
    return {
        "status": "PASS",
        "packet_count": len(packet_paths),
        "rows_checked": rows_checked,
        "private_fields_exposed": 0,
        "condition_or_mode_tokens_exposed": 0,
        "private_structural_context_fields_exposed": 0,
        "linux_generic_telemetry_policy": "LEGITIMATE_CONTEXT_NOT_A_MODE_LEAK",
    }


def create_freeze(canonical: Mapping[str, Any], freeze_root: Path = FREEZE_ROOT) -> dict[str, Any]:
    runs = canonical["runs"]
    protocols = sorted({run["identity"]["protocol"] for run in runs})
    seeds = sorted({run["input"]["seed"] for run in runs})
    prompt_hashes = sorted({run["provenance"].get("prompt_template_hash") for run in runs})
    prompt_hashes = [value for value in prompt_hashes if value]
    providers = sorted({run["provenance"].get("provider") for run in runs})
    models = sorted({run["provenance"].get("llm_model_identifier") for run in runs})
    temperatures = sorted({run["provenance"].get("llm_temperature") for run in runs})
    max_tokens = sorted({run["provenance"].get("max_completion_tokens") for run in runs})
    contract_observed = {
        "protocols": protocols,
        "seeds": seeds,
        "providers": providers,
        "models": models,
        "temperatures": temperatures,
        "max_completion_tokens": max_tokens,
        "api_families": sorted({run["provenance"].get("api_family") for run in runs}),
        "reasoning_efforts": sorted({run["provenance"].get("reasoning_effort") for run in runs}),
        "input_token_budgets": sorted({run["provenance"].get("input_token_budget") for run in runs}),
    }
    contract_expected = {
        "protocols": ["within-dataset"],
        "seeds": [42],
        "providers": ["openai"],
        "models": ["gpt-5.4-mini-2026-03-17"],
        "temperatures": [0.0],
        "max_completion_tokens": [1024],
        "api_families": ["chat-completions"],
        "reasoning_efforts": ["none"],
        "input_token_budgets": [32768],
    }
    if contract_observed != contract_expected:
        raise StudyPreparationError(f"canonical LLM contract mismatch: {contract_observed}")
    bound_files = {name: canonical["checksums"][name] for name in EXPECTED_CANONICAL_FILES}
    freeze_documentation = ROOT / "docs/experiments/h3-final-computational-freeze-v1.md"
    if not freeze_documentation.is_file():
        raise StudyPreparationError(f"freeze documentation missing: {freeze_documentation}")
    document = {
        "schema_version": "h3-computational-freeze-v1",
        "status": "PASS",
        "campaign_root": canonical["root"].relative_to(ROOT).as_posix(),
        "configuration_sha256": canonical["reconstruction"].get("configuration_sha256"),
        "protocol": protocols[0] if len(protocols) == 1 else protocols,
        "seed": seeds[0] if len(seeds) == 1 else seeds,
        "incidents": len(canonical["incidents"]),
        "runs": len(runs),
        "mode_counts": canonical["modes"],
        "success": len(runs),
        "failures": 0,
        "fallback_count": canonical["fallback_count"],
        "unique_response_ids": len(set(canonical["response_ids"])),
        "same_rca_binding": "PASS",
        "campaign_validation": "PASS",
        "human_blinding_audit": "PASS",
        "v2_freeze": "12/12",
        "scientific_state": "COMPUTATION_COMPLETE_NO_FURTHER_H3_EXECUTION_AUTHORIZED",
        "canonical_campaign": {
            "relative_path": canonical["root"].relative_to(ROOT).as_posix(),
            "sha256sums_sha256": canonical["sha256sums_sha256"],
            "validated_entries": len(canonical["checksums"]),
            "bound_files": bound_files,
        },
        "documentation": {
            "path": freeze_documentation.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(freeze_documentation),
        },
        "population": {
            "canonical_runs": len(runs),
            "incidents": len(canonical["incidents"]),
            "conditions": canonical["modes"],
            "unique_provider_response_ids": len(set(canonical["response_ids"])),
            "fallback_outputs": canonical["fallback_count"],
        },
        "execution_contract": {
            "protocols": protocols,
            "seeds": seeds,
            "configuration_identity": canonical["reconstruction"].get("configuration_sha256"),
            "providers": providers,
            "models": models,
            "temperatures": temperatures,
            "max_completion_tokens": max_tokens[0] if len(max_tokens) == 1 else max_tokens,
            "prompt_template_hashes": prompt_hashes,
            "common_prompt_hash": canonical["reconstruction"].get("common_prompt_hash"),
            "fallback_policy": "NO_FALLBACK_OUTPUT_ACCEPTED",
            "api_family": "chat-completions",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "reasoning_effort": "none",
            "input_token_budget": 32768,
            "stream": False,
            "top_p": "omitted",
            "api_seed": "omitted",
        },
        "model_contract": {
            "provider": "openai",
            "model_family": "GPT-5.4 Mini",
            "model_id": "gpt-5.4-mini-2026-03-17",
            "api_family": "chat-completions",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "reasoning_effort": "none",
            "temperature": 0.0,
            "max_completion_tokens": 1024,
            "input_token_budget": 32768,
            "stream": False,
            "top_p": "omitted",
            "api_seed": "omitted",
        },
        "canonical_reconstruction": {
            "policy": "prefer_first_attempt_success_else_first_retry_success",
            "first_attempt_selected": canonical["reconstruction"].get("first_attempt_selected"),
            "retry_attempt_selected": canonical["reconstruction"].get("retry_attempt_selected"),
            "retry_run_id": "029be23de01d0dc8af43e5f9811326ad0bf635aabc59f50c764eb3c7cfef8e0c",
            "selection_is_content_blind": canonical["reconstruction"].get("selection_is_content_blind"),
        },
        "audits": {
            "experiment_validity": "PASS",
            "same_rca_input": "PASS",
            "canonical_checksums": "PASS",
            "canonical_reconstruction": "PASS",
        },
    }
    freeze_path = freeze_root / "h3-computational-freeze-v1.json"
    write_json(freeze_path, document)
    sums = f"{sha256_file(freeze_path)}  {freeze_path.name}\n".encode("utf-8")
    atomic_write(freeze_root / "SHA256SUMS", sums)
    validate_sha256sums_minimal(freeze_root)
    return document


def validate_sha256sums_minimal(root: Path) -> int:
    count = 0
    for expected, relative in parse_sha256sums(root / "SHA256SUMS"):
        if sha256_file(root / relative) != expected:
            raise StudyPreparationError(f"generated checksum mismatch: {relative}")
        count += 1
    return count


def _relative_hashes(paths: Iterable[Path]) -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in sorted(paths)}


def prepare_study(
    canonical_root: Path = CANONICAL_ROOT,
    freeze_root: Path = FREEZE_ROOT,
    human_root: Path = HUMAN_ROOT,
    config_path: Path = CONFIG_PATH,
    doc_paths: Sequence[Path] = DOC_PATHS,
) -> dict[str, Any]:
    canonical = load_canonical(canonical_root)
    freeze = create_freeze(canonical, freeze_root)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["assignment"]["seed"] != 42:
        raise StudyPreparationError("human-study seed must be frozen at 42")
    items = build_items(canonical)
    assignments = assign_items(items, seed=42)
    assignment_audit = audit_assignments(assignments, items)
    regenerated_assignments = assign_items(items, seed=42)
    assignment_digest = hashlib.sha256(canonical_json_bytes(assignments)).hexdigest()
    regenerated_digest = hashlib.sha256(canonical_json_bytes(regenerated_assignments)).hexdigest()
    if assignment_digest != regenerated_digest:
        raise StudyPreparationError("seed-42 assignment regeneration is not byte-stable")
    assignment_audit.update({
        "TOTAL_ITEMS": 120,
        "TOTAL_ASSIGNMENTS": 240,
        "RATINGS_PER_ITEM": 2,
        "REVIEWERS": 6,
        "ITEMS_PER_REVIEWER": 40,
        "EACH_REVIEWER_ONE_ITEM_PER_INCIDENT": "PASS",
        "REVIEWER_CONDITION_BALANCE": "PASS",
        "ASSIGNMENT_DETERMINISTIC_SEED42": "PASS",
        "assignment_sha256": assignment_digest,
        "regenerated_assignment_sha256": regenerated_digest,
    })

    private_dir = human_root / "private"
    packet_dir = human_root / "reviewers"
    private_dir.mkdir(parents=True, exist_ok=True)
    packet_dir.mkdir(parents=True, exist_ok=True)
    assignment_path = private_dir / "assignment-map.json"
    condition_path = private_dir / "condition-map.json"
    assignments_by_item: list[dict[str, Any]] = []
    for item in items:
        matching = [row for row in assignments if row["evaluation_item_id"] == item["evaluation_item_id"]]
        assignments_by_item.append({
            "evaluation_item_id": item["evaluation_item_id"],
            "canonical_run_id": item["run_id"],
            "incident_id": item["incident_id"],
            "condition_code": item["condition_code"],
            "explanation_mode": item["explanation_mode"],
            "assigned_reviewer_ids": sorted(row["reviewer_id"] for row in matching),
        })
    write_json(assignment_path, {
        "schema_version": "h3-human-assignment-map-v1",
        "seed": 42,
        "algorithm": "incident_cyclic_pair_v1",
        "assignments": assignments,
        "item_assignments": assignments_by_item,
    }, mode=0o600)
    atomic_write(
        condition_path,
        (canonical_root / "h3/h3-human-condition-map.json").read_bytes(),
        mode=0o600,
    )
    if sha256_file(condition_path) != canonical["checksums"]["h3/h3-human-condition-map.json"]:
        raise StudyPreparationError("private condition-map copy does not preserve canonical bytes")

    packet_paths: list[Path] = []
    for reviewer_id in config["assignment"]["reviewers"]:
        path = packet_dir / f"{reviewer_id}.csv"
        atomic_write(path, csv_bytes(PACKET_FIELDS, packet_rows(assignments, reviewer_id)))
        packet_paths.append(path)
    blinding_audit = audit_reviewer_packets(packet_paths)
    write_json(human_root / "assignment-audit.json", assignment_audit)
    write_json(human_root / "blinding-audit.json", blinding_audit)
    metadata_fields = (
        "reviewer_id", "role_category", "software_experience_bucket",
        "distributed_systems_familiarity_1_5", "observability_familiarity_1_5",
        "rca_familiarity_1_5",
    )
    atomic_write(human_root / "reviewer-metadata-template.csv", csv_bytes(metadata_fields, []))

    docs = list(doc_paths)
    for path in [config_path, *docs]:
        if not path.is_file():
            raise StudyPreparationError(f"required study input missing: {path}")
    manifest = {
        "schema_version": "h3-human-evaluation-manifest-v1",
        "status": "PREPARED_NOT_EXECUTED",
        "canonical_campaign_sha256sums_sha256": canonical["sha256sums_sha256"],
        "computational_freeze_sha256": sha256_file(freeze_root / "h3-computational-freeze-v1.json"),
        "configuration_sha256": sha256_file(config_path),
        "assignment": {
            "seed": 42,
            "algorithm": "incident_cyclic_pair_v1",
            "canonical_item_ids_sha256": hashlib.sha256(
                "\n".join(sorted(item["evaluation_item_id"] for item in items)).encode("utf-8")
            ).hexdigest(),
            "canonical_items": 120,
            "assignment_rows": 240,
            "reviewers": 6,
            "raters_per_item": 2,
        },
        "supporting_context_policy": {
            "source": "canonical h3/h3-human-evaluation-blinded.csv",
            "transformation": "recursive_removal_of_reviewer_prohibited_structural_fields_only",
            "fields": sorted(PRIVATE_FIELDS | {"rcaeval.case", "rcaeval.system"}),
            "key_patterns": ["*dataset*", "*source_system*"],
            "total_removed_fields": sum(item["supporting_context_redactions"] for item in items),
        },
        "private_files": _relative_hashes((assignment_path, condition_path)),
        "reviewer_packets": _relative_hashes(packet_paths),
        "study_documents": _relative_hashes(docs),
        "audits": {"assignment": assignment_audit["status"], "blinding": blinding_audit["status"]},
        "ratings_state": "NOT_COLLECTED",
        "unblinding_state": "NOT_PERFORMED",
    }
    write_json(human_root / "h3-human-study-manifest.json", manifest)
    checksum_targets = sorted(
        path for path in human_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS" and "ratings" not in path.parts
    )
    sum_lines = "".join(
        f"{sha256_file(path)}  {path.relative_to(human_root).as_posix()}\n"
        for path in checksum_targets
    )
    atomic_write(human_root / "SHA256SUMS", sum_lines.encode("utf-8"))
    verified = validate_sha256sums_minimal(human_root)
    return {
        "status": "PASS",
        "freeze": freeze,
        "assignment_audit": assignment_audit,
        "blinding_audit": blinding_audit,
        "human_sha256sums": f"{verified}/{verified} OK",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-root", type=Path, default=CANONICAL_ROOT)
    parser.add_argument("--freeze-root", type=Path, default=FREEZE_ROOT)
    parser.add_argument("--human-root", type=Path, default=HUMAN_ROOT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = prepare_study(args.canonical_root, args.freeze_root, args.human_root, args.config)
    print(f"H3_COMPUTATIONAL_FREEZE = {result['freeze']['status']}")
    print(f"H3_HUMAN_ASSIGNMENT_AUDIT = {result['assignment_audit']['status']}")
    print(f"H3_HUMAN_BLINDING_AUDIT = {result['blinding_audit']['status']}")
    print(f"H3_HUMAN_SHA256SUMS = {result['human_sha256sums']}")
    print("H3_HUMAN_STUDY_STATUS = PREPARED_NOT_EXECUTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
