#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
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
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Arquivo baseline-input/rca-documents.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--reliable-only",
        action="store_true",
    )
    return parser.parse_args()


def read_documents(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(
        path.read_text(encoding="utf-8")
    )

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


def atomic_write_json(
    path: Path,
    payload: Any,
) -> None:
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


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    fields = [
        "explanation_id",
        "rca_result_id",
        "trace_id",
        "root_cause_service",
        "confidence",
        "is_reliable",
        "model_version",
        "pipeline_version",
        "summary",
        "decision",
        "evidence_count",
        "alternatives",
        "limitations",
    ]

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "explanation_id": row["explanation_id"],
                    "rca_result_id": row["rca_result_id"],
                    "trace_id": row["trace_id"],
                    "root_cause_service": (
                        row["root_cause_service"]
                    ),
                    "confidence": row["confidence"],
                    "is_reliable": row["is_reliable"],
                    "model_version": row["model_version"],
                    "pipeline_version": row["pipeline_version"],
                    "summary": row["summary"],
                    "decision": row["decision"],
                    "evidence_count": len(row["evidence"]),
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


def write_report(
    path: Path,
    rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    reliable = sum(
        bool(row["is_reliable"])
        for row in rows
    )
    lines = [
        "# Explicações auditáveis do CausalODS",
        "",
        f"- Explicações geradas: {len(rows)}",
        f"- Decisões confiáveis: {reliable}",
        f"- Decisões não confiáveis: {len(rows) - reliable}",
        f"- Falhas de adaptação: {len(failures)}",
        "",
        "## Amostra",
        "",
    ]

    for row in rows[:20]:
        lines.extend(
            [
                f"### {row['rca_result_id']}",
                "",
                row["summary"],
                "",
                f"**Decisão:** {row['decision']}",
                "",
                "**Evidências:**",
                "",
            ]
        )

        if row["evidence"]:
            for item in row["evidence"]:
                lines.append(
                    f"- [{item['source']}] "
                    f"{item['statement']}"
                )
        else:
            lines.append("- Nenhuma evidência textual disponível.")

        if row["limitations"]:
            lines.extend(
                [
                    "",
                    "**Limitações:**",
                    "",
                ]
            )
            for item in row["limitations"]:
                lines.append(f"- {item}")

        lines.append("")

    path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_sha256(root: Path) -> None:
    lines = []
    for path in sorted(root.iterdir()):
        if (
            not path.is_file()
            or path.name == "SHA256SUMS"
        ):
            continue

        digest = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

        lines.append(
            f"{digest}  {path.name}"
        )

    (root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    documents = read_documents(input_path)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, document in enumerate(documents):
        try:
            explanation = generate_explanation(
                document
            )
            row = explanation.to_dict()
            if (
                args.reliable_only
                and not row["is_reliable"]
            ):
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

    atomic_write_json(
        output_dir / "explanations.json",
        {
            "schema_version": "1.0",
            "source": str(input_path),
            "input_count": len(documents),
            "explanation_count": len(rows),
            "failure_count": len(failures),
            "explanations": rows,
            "failures": failures,
        },
    )
    write_csv(
        output_dir / "explanations.csv",
        rows,
    )
    write_report(
        output_dir / "report.md",
        rows,
        failures,
    )
    write_sha256(output_dir)

    print(f"Entrada: {len(documents)}")
    print(f"Explicações: {len(rows)}")
    print(f"Falhas: {len(failures)}")
    print(f"Saída: {output_dir}")

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
