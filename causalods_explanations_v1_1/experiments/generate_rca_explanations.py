#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPOSITORY_ROOT / "correlation-engine"
sys.path.insert(0, str(ENGINE_ROOT))

from explanations import generate_explanation  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gera explicações determinísticas e auditáveis "
            "a partir de RCAResults exportados."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reliable-only", action="store_true")
    parser.add_argument("--samples-per-stratum", type=int, default=2)
    return parser.parse_args()


def read_documents(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(payload, list):
        documents = payload
    elif isinstance(payload, dict):
        documents = (
            payload.get("documents")
            or payload.get("results")
            or payload.get("rows")
        )
    else:
        documents = None

    if not isinstance(documents, list):
        raise ValueError(
            "O arquivo não contém uma lista de documentos."
        )

    return [
        dict(item)
        for item in documents
        if isinstance(item, dict)
    ]


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
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    temporary.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "explanation_id",
        "rca_result_id",
        "trace_id",
        "root_cause_service",
        "confidence",
        "is_reliable",
        "model_version",
        "pipeline_version",
        "generator_version",
        "summary",
        "decision",
        "evidence_count",
        "evidence_sources",
        "alternatives",
        "limitations",
    ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            sources = sorted({
                item["source"]
                for item in row["evidence"]
            })
            writer.writerow(
                {
                    "explanation_id": row["explanation_id"],
                    "rca_result_id": row["rca_result_id"],
                    "trace_id": row["trace_id"],
                    "root_cause_service": row["root_cause_service"],
                    "confidence": row["confidence"],
                    "is_reliable": row["is_reliable"],
                    "model_version": row["model_version"],
                    "pipeline_version": row["pipeline_version"],
                    "generator_version": row["generator_version"],
                    "summary": row["summary"],
                    "decision": row["decision"],
                    "evidence_count": len(row["evidence"]),
                    "evidence_sources": json.dumps(
                        sources,
                        ensure_ascii=False,
                    ),
                    "alternatives": json.dumps(
                        row["alternatives"],
                        ensure_ascii=False,
                    ),
                    "limitations": json.dumps(
                        row["limitations"],
                        ensure_ascii=False,
                    ),
                }
            )


def confidence_bucket(value: Any) -> str:
    if value is None:
        return "missing"
    number = float(value)
    if number < 0.10:
        return "very_low"
    if number < 0.50:
        return "below_gate"
    if number < 0.75:
        return "moderate"
    return "high"


def stratified_samples(
    rows: list[dict[str, Any]],
    per_stratum: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        key = (
            row["model_version"],
            bool(row["is_reliable"]),
            row["root_cause_service"],
            confidence_bucket(row["confidence"]),
        )
        grouped[key].append(row)

    samples: list[dict[str, Any]] = []
    for key in sorted(
        grouped,
        key=lambda value: tuple(str(part) for part in value),
    ):
        ordered = sorted(
            grouped[key],
            key=lambda row: (
                row["rca_result_id"],
                row["trace_id"],
            ),
        )
        samples.extend(ordered[:per_stratum])

    return samples


def evidence_coverage(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_counts: Counter[str] = Counter()
    explanation_counts: Counter[str] = Counter()
    evidence_counts: list[int] = []

    for row in rows:
        sources = {
            item["source"]
            for item in row["evidence"]
        }
        evidence_counts.append(len(row["evidence"]))

        for item in row["evidence"]:
            source_counts[item["source"]] += 1
        for source in sources:
            explanation_counts[source] += 1

    total = len(rows)
    by_source = []
    for source in sorted(
        set(source_counts) | set(explanation_counts)
    ):
        count = explanation_counts[source]
        by_source.append(
            {
                "source": source,
                "evidence_items": source_counts[source],
                "explanations_with_source": count,
                "coverage_rate": (
                    count / total
                    if total else 0.0
                ),
            }
        )

    return {
        "explanation_count": total,
        "minimum_evidence_count": (
            min(evidence_counts)
            if evidence_counts else 0
        ),
        "maximum_evidence_count": (
            max(evidence_counts)
            if evidence_counts else 0
        ),
        "mean_evidence_count": (
            sum(evidence_counts) / len(evidence_counts)
            if evidence_counts else 0.0
        ),
        "by_source": by_source,
    }


def write_coverage_csv(
    path: Path,
    coverage: dict[str, Any],
) -> None:
    rows = coverage["by_source"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "evidence_items",
                "explanations_with_source",
                "coverage_rate",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path,
    rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    coverage: dict[str, Any],
    samples: list[dict[str, Any]],
) -> None:
    reliable = sum(bool(row["is_reliable"]) for row in rows)
    lines = [
        "# Explicações auditáveis do CausalODS",
        "",
        f"- Explicações geradas: {len(rows)}",
        f"- Decisões confiáveis: {reliable}",
        f"- Decisões não confiáveis: {len(rows) - reliable}",
        f"- Falhas de adaptação: {len(failures)}",
        f"- Evidências por explicação: "
        f"{coverage['minimum_evidence_count']}–"
        f"{coverage['maximum_evidence_count']}",
        "",
        "## Cobertura por tipo de evidência",
        "",
        "| Fonte | Itens | Explicações | Cobertura |",
        "|---|---:|---:|---:|",
    ]

    for item in coverage["by_source"]:
        lines.append(
            f"| {item['source']} | {item['evidence_items']} | "
            f"{item['explanations_with_source']} | "
            f"{item['coverage_rate']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Amostra estratificada",
            "",
        ]
    )

    for row in samples:
        lines.extend(
            [
                f"### {row['rca_result_id']}",
                "",
                f"- Modelo: {row['model_version']}",
                f"- Confiável: {row['is_reliable']}",
                f"- Serviço: {row['root_cause_service']}",
                f"- Confiança: {row['confidence']}",
                "",
                row["summary"],
                "",
                f"**Decisão:** {row['decision']}",
                "",
                "**Evidências:**",
                "",
            ]
        )

        for item in row["evidence"]:
            lines.append(
                f"- [{item['source']}] {item['statement']}"
            )

        if row["limitations"]:
            lines.extend(["", "**Limitações:**", ""])
            for item in row["limitations"]:
                lines.append(f"- {item}")

        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


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


def main() -> int:
    args = parse_args()
    if args.samples_per_stratum < 1:
        raise ValueError("--samples-per-stratum deve ser maior que zero")

    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    documents = read_documents(input_path)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, document in enumerate(documents):
        try:
            row = generate_explanation(document).to_dict()
            if args.reliable_only and not row["is_reliable"]:
                continue
            rows.append(row)
        except Exception as exc:
            failures.append(
                {
                    "index": index,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    coverage = evidence_coverage(rows)
    samples = stratified_samples(
        rows,
        args.samples_per_stratum,
    )

    payload = {
        "schema_version": "1.1",
        "source": str(input_path),
        "input_count": len(documents),
        "explanation_count": len(rows),
        "failure_count": len(failures),
        "evidence_coverage": coverage,
        "sample_count": len(samples),
        "explanations": rows,
        "failures": failures,
    }

    atomic_write_json(
        output_dir / "explanations.json",
        payload,
    )
    write_csv(output_dir / "explanations.csv", rows)
    atomic_write_json(
        output_dir / "evidence_coverage.json",
        coverage,
    )
    write_coverage_csv(
        output_dir / "evidence_coverage.csv",
        coverage,
    )
    write_report(
        output_dir / "report.md",
        rows,
        failures,
        coverage,
        samples,
    )
    write_sha256(output_dir)

    print(f"Entrada: {len(documents)}")
    print(f"Explicações: {len(rows)}")
    print(f"Falhas: {len(failures)}")
    print(f"Amostras: {len(samples)}")
    print(f"Saída: {output_dir}")

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
