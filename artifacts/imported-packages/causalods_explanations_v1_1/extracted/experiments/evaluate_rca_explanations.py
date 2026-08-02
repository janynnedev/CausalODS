#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPOSITORY_ROOT / "correlation-engine"
sys.path.insert(0, str(ENGINE_ROOT))

from explanations.evidence_adapter import normalize_rca_document  # noqa: E402


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def documents_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = (
            payload.get("documents")
            or payload.get("results")
            or payload.get("rows")
        )
    else:
        values = None

    if not isinstance(values, list):
        raise ValueError("Lista de RCAResults não encontrada")

    return [
        dict(item)
        for item in values
        if isinstance(item, dict)
    ]


def explanations_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Formato inválido de explicações")
    values = payload.get("explanations")
    if not isinstance(values, list):
        raise ValueError("Lista de explicações não encontrada")
    return [
        dict(item)
        for item in values
        if isinstance(item, dict)
    ]


def expected_id(
    rca_result_id: str,
    generator_version: str,
) -> str:
    digest = hashlib.sha256(
        (
            rca_result_id
            + "|"
            + generator_version
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"exp-{digest}"


def close_enough(
    left: Any,
    right: Any,
    tolerance: float = 1e-12,
) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(
        float(left),
        float(right),
        rel_tol=tolerance,
        abs_tol=tolerance,
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def write_failures(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "rca_result_id",
        "trace_id",
        "check",
        "expected",
        "actual",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path,
    summary: dict[str, Any],
    source_coverage: list[dict[str, Any]],
) -> None:
    lines = [
        "# Avaliação das explicações RCA",
        "",
        f"- RCAResults: {summary['source_count']}",
        f"- Explicações: {summary['explanation_count']}",
        f"- Cobertura: {summary['coverage_rate']:.3f}",
        f"- Falhas de fidelidade: {summary['faithfulness_failure_count']}",
        f"- IDs determinísticos corretos: {summary['deterministic_id_rate']:.3f}",
        f"- Proveniência completa: {summary['provenance_complete_rate']:.3f}",
        f"- Consistência semântica: {summary['semantic_consistency_rate']:.3f}",
        "",
        "## Cobertura por fonte",
        "",
        "| Fonte | Explicações | Cobertura |",
        "|---|---:|---:|",
    ]

    for item in source_coverage:
        lines.append(
            f"| {item['source']} | "
            f"{item['explanations_with_source']} | "
            f"{item['coverage_rate']:.3f} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sha256(root: Path) -> None:
    lines = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Avalia cobertura, fidelidade e auditabilidade "
            "das explicações determinísticas."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--explanations", type=Path, required=True)
    parser.add_argument("--second-run", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = args.source.expanduser().resolve()
    explanations_path = args.explanations.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    source_documents = documents_list(read_json(source_path))
    explanation_payload = read_json(explanations_path)
    explanations = explanations_list(explanation_payload)

    source_by_id = {}
    for document in source_documents:
        normalized = normalize_rca_document(document)
        source_by_id[normalized.rca_result_id] = normalized

    explanation_by_id = {
        str(item["rca_result_id"]): item
        for item in explanations
    }

    failures: list[dict[str, Any]] = []
    deterministic_correct = 0
    provenance_complete = 0
    semantic_consistent = 0
    evidence_source_explanations: Counter[str] = Counter()

    for rca_result_id, source in source_by_id.items():
        explanation = explanation_by_id.get(rca_result_id)
        if explanation is None:
            failures.append(
                {
                    "rca_result_id": rca_result_id,
                    "trace_id": source.trace_id,
                    "check": "missing_explanation",
                    "expected": "present",
                    "actual": "missing",
                }
            )
            continue

        checks = {
            "trace_id": (
                source.trace_id,
                explanation.get("trace_id"),
            ),
            "root_cause_service": (
                source.root_cause_service,
                explanation.get("root_cause_service"),
            ),
            "is_reliable": (
                source.is_reliable,
                explanation.get("is_reliable"),
            ),
            "model_version": (
                source.model_version,
                explanation.get("model_version"),
            ),
            "pipeline_version": (
                source.pipeline_version,
                explanation.get("pipeline_version"),
            ),
        }

        for check, (expected, actual) in checks.items():
            if expected != actual:
                failures.append(
                    {
                        "rca_result_id": rca_result_id,
                        "trace_id": source.trace_id,
                        "check": check,
                        "expected": expected,
                        "actual": actual,
                    }
                )

        if not close_enough(
            source.confidence,
            explanation.get("confidence"),
        ):
            failures.append(
                {
                    "rca_result_id": rca_result_id,
                    "trace_id": source.trace_id,
                    "check": "confidence",
                    "expected": source.confidence,
                    "actual": explanation.get("confidence"),
                }
            )

        generator_version = str(
            explanation.get("generator_version")
        )
        expected_explanation_id = expected_id(
            rca_result_id,
            generator_version,
        )
        if explanation.get("explanation_id") == expected_explanation_id:
            deterministic_correct += 1
        else:
            failures.append(
                {
                    "rca_result_id": rca_result_id,
                    "trace_id": source.trace_id,
                    "check": "explanation_id",
                    "expected": expected_explanation_id,
                    "actual": explanation.get("explanation_id"),
                }
            )

        if (
            explanation.get("model_version")
            and explanation.get("model_version") != "unknown"
            and explanation.get("pipeline_version")
            and explanation.get("pipeline_version") != "unknown"
            and generator_version
        ):
            provenance_complete += 1

        summary = str(explanation.get("summary", ""))
        decision = str(explanation.get("decision", ""))
        limitations = explanation.get("limitations") or []

        if source.is_reliable:
            consistent = (
                "decisão confiável" in summary
                and "pode acionar" in decision
            )
        else:
            consistent = (
                "não ultrapassou" in summary
                and "hipótese" in decision
                and any(
                    "gate de confiabilidade" in str(item)
                    for item in limitations
                )
            )
        if consistent:
            semantic_consistent += 1
        else:
            failures.append(
                {
                    "rca_result_id": rca_result_id,
                    "trace_id": source.trace_id,
                    "check": "semantic_consistency",
                    "expected": "consistent",
                    "actual": "inconsistent",
                }
            )

        sources = {
            str(item.get("source"))
            for item in explanation.get("evidence", [])
            if item.get("source")
        }
        for evidence_source in sources:
            evidence_source_explanations[evidence_source] += 1

    total = len(source_by_id)
    source_coverage = [
        {
            "source": source,
            "explanations_with_source": count,
            "coverage_rate": count / total if total else 0.0,
        }
        for source, count in sorted(
            evidence_source_explanations.items()
        )
    ]

    byte_deterministic = None
    first_sha = hashlib.sha256(
        explanations_path.read_bytes()
    ).hexdigest()
    second_sha = None

    if args.second_run:
        second_path = args.second_run.expanduser().resolve()
        second_sha = hashlib.sha256(
            second_path.read_bytes()
        ).hexdigest()
        byte_deterministic = (
            explanations_path.read_bytes()
            == second_path.read_bytes()
        )

    summary = {
        "schema_version": "1.0",
        "source_count": total,
        "explanation_count": len(explanations),
        "coverage_rate": (
            len(explanation_by_id) / total
            if total else 0.0
        ),
        "faithfulness_failure_count": len(failures),
        "deterministic_id_count": deterministic_correct,
        "deterministic_id_rate": (
            deterministic_correct / total
            if total else 0.0
        ),
        "provenance_complete_count": provenance_complete,
        "provenance_complete_rate": (
            provenance_complete / total
            if total else 0.0
        ),
        "semantic_consistency_count": semantic_consistent,
        "semantic_consistency_rate": (
            semantic_consistent / total
            if total else 0.0
        ),
        "first_run_sha256": first_sha,
        "second_run_sha256": second_sha,
        "byte_deterministic": byte_deterministic,
        "evidence_source_coverage": source_coverage,
        "valid": (
            len(explanations) == total
            and not failures
            and deterministic_correct == total
            and provenance_complete == total
            and semantic_consistent == total
            and (
                byte_deterministic is not False
            )
        ),
    }

    atomic_write_json(
        output_dir / "evaluation.json",
        summary,
    )
    write_failures(
        output_dir / "failures.csv",
        failures,
    )
    write_report(
        output_dir / "report.md",
        summary,
        source_coverage,
    )
    write_sha256(output_dir)

    print(f"Validação: {'OK' if summary['valid'] else 'FALHOU'}")
    print(f"Cobertura: {summary['coverage_rate']:.3f}")
    print(
        "IDs determinísticos: "
        f"{summary['deterministic_id_rate']:.3f}"
    )
    print(
        "Consistência semântica: "
        f"{summary['semantic_consistency_rate']:.3f}"
    )
    print(
        "Determinismo byte a byte: "
        f"{summary['byte_deterministic']}"
    )
    print(f"Saída: {output_dir}")

    return 0 if summary["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
