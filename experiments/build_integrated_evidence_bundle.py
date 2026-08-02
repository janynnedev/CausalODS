from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCES: list[dict[str, str]] = [
    {
        "id": "e2e_final",
        "category": "e2e",
        "path": "results/e2e/final/final-v1-v2-50",
        "description": (
            "Campanha final v1 versus v2, com cinquenta "
            "repetições por cenário."
        ),
    },
    {
        "id": "e2e_comparison_report",
        "category": "e2e",
        "path": (
            "results/e2e/final/comparison/"
            "relatorio_comparacao_e2e_rca_v1_v2.md"
        ),
        "description": "Relatório comparativo v1 versus v2.",
    },
    {
        "id": "latency_sensitivity",
        "category": "sensitivity",
        "path": (
            "results/e2e/sensitivity/"
            "latency-sensitivity-v1-v2-20260729T191418Z"
        ),
        "description": (
            "Sensibilidade do RCA a diferentes intensidades "
            "de latência."
        ),
    },
    {
        "id": "explanations_final",
        "category": "explanations",
        "path": (
            "results/offline-explanations/"
            "final-v1-v2-50"
        ),
        "description": (
            "Explicações geradas para a campanha final."
        ),
    },
    {
        "id": "explanations_evaluation",
        "category": "explanations",
        "path": (
            "results/offline-explanations/"
            "v1.1-evaluation"
        ),
        "description": (
            "Avaliação de cobertura e consistência das "
            "explicações v1.1."
        ),
    },
    {
        "id": "explanations_run_a",
        "category": "explanations",
        "path": (
            "results/offline-explanations/"
            "v1.1-run-a"
        ),
        "description": (
            "Primeira execução usada na avaliação "
            "de determinismo."
        ),
    },
    {
        "id": "explanations_run_b",
        "category": "explanations",
        "path": (
            "results/offline-explanations/"
            "v1.1-run-b"
        ),
        "description": (
            "Segunda execução usada na avaliação "
            "de determinismo."
        ),
    },
    {
        "id": "robustness_original",
        "category": "robustness",
        "path": (
            "results/robustness/live/"
            "robustness-live-v2-repeat10"
        ),
        "description": (
            "Campanha original de backlog Kafka, "
            "indisponibilidade do OpenSearch e tentativa "
            "inicial de restart."
        ),
    },
    {
        "id": "robustness_restart",
        "category": "robustness",
        "path": (
            "results/robustness/live/"
            "robustness-live-restart-v2-repeat10-"
            "ubuntu-warm-20260802T024249Z"
        ),
        "description": (
            "Campanha corrigida de reinicialização do "
            "Correlation Engine."
        ),
    },
    {
        "id": "robustness_consolidated",
        "category": "robustness",
        "path": (
            "results/robustness/consolidated/"
            "final-robustness-v2"
        ),
        "description": (
            "Conjunto consolidado com trinta execuções "
            "válidas de robustez."
        ),
    },
]


MANIFEST_NAMES = {
    "SHA256SUMS",
    "FINAL_SHA256SUMS",
    "SHA256SUMS.post-export",
    "IMPORTED_SHA256SUMS",
}


KEY_JSON_NAMES = {
    "status.json",
    "summary.json",
    "evaluation.json",
    "fault_only_metrics.json",
    "summary_metrics.json",
    "restart-scientific-summary.json",
}


KEY_REPORT_NAMES = {
    "report.md",
    "paper_tables.md",
    "methodology.md",
    "restart-methodology.md",
    "control-cadence.md",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def git_output(*args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        text=True,
        capture_output=True,
        check=False,
    )

    if process.returncode != 0:
        return "unknown"

    return process.stdout.strip()


def validate_manifest(path: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["sha256sum", "-c", path.name],
        cwd=path.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    output_lines = [
        line
        for line in (
            process.stdout + process.stderr
        ).splitlines()
        if line.strip()
    ]

    return {
        "path": str(path),
        "valid": process.returncode == 0,
        "return_code": process.returncode,
        "output_tail": output_lines[-20:],
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        return {
            "_load_error": str(error),
        }

    if isinstance(value, dict):
        return value

    return {
        "_root_type": type(value).__name__,
        "_item_count": (
            len(value)
            if isinstance(value, list)
            else None
        ),
    }


def inspect_source(specification: dict[str, str]) -> dict[str, Any]:
    path = Path(specification["path"])

    result: dict[str, Any] = {
        **specification,
        "exists": path.exists(),
        "type": (
            "directory"
            if path.is_dir()
            else "file"
            if path.is_file()
            else "missing"
        ),
    }

    if not path.exists():
        return result

    if path.is_file():
        result.update(
            {
                "file_count": 1,
                "total_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "manifests": [],
                "key_json": {},
                "reports": [str(path)],
            }
        )
        return result

    files = sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
    )

    manifests = [
        item
        for item in files
        if item.name in MANIFEST_NAMES
    ]

    key_json_paths = [
        item
        for item in files
        if item.name in KEY_JSON_NAMES
    ]

    report_paths = [
        item
        for item in files
        if item.name in KEY_REPORT_NAMES
    ]

    result.update(
        {
            "file_count": len(files),
            "total_bytes": sum(
                item.stat().st_size
                for item in files
            ),
            "manifests": [
                validate_manifest(item)
                for item in manifests
            ],
            "key_json": {
                str(item.relative_to(path)): load_json(item)
                for item in key_json_paths
            },
            "reports": [
                str(item.relative_to(path))
                for item in report_paths
            ],
        }
    )

    return result


def write_markdown(
    output: Path,
    inventory: dict[str, Any],
) -> None:
    lines = [
        "# Inventário integrado de evidências",
        "",
        f"Gerado em UTC: `{inventory['generated_at_utc']}`",
        "",
        f"Commit: `{inventory['git_commit']}`",
        "",
        f"Branch: `{inventory['git_branch']}`",
        "",
        "## Fontes",
        "",
        (
            "| ID | Categoria | Existe | Arquivos | "
            "Tamanho | Manifestos válidos |"
        ),
        "|---|---|---:|---:|---:|---:|",
    ]

    for source in inventory["sources"]:
        manifests = source.get("manifests", [])

        valid_manifests = sum(
            item.get("valid") is True
            for item in manifests
        )

        manifest_text = (
            f"{valid_manifests}/{len(manifests)}"
            if manifests
            else "não encontrado"
        )

        lines.append(
            "| "
            f"`{source['id']}` | "
            f"{source['category']} | "
            f"{source['exists']} | "
            f"{source.get('file_count', 0)} | "
            f"{source.get('total_bytes', 0)} bytes | "
            f"{manifest_text} |"
        )

    lines.extend(
        [
            "",
            "## Detalhamento",
            "",
        ]
    )

    for source in inventory["sources"]:
        lines.extend(
            [
                f"### {source['id']}",
                "",
                source["description"],
                "",
                f"- Caminho: `{source['path']}`",
                f"- Existe: `{source['exists']}`",
                (
                    "- Arquivos: "
                    f"`{source.get('file_count', 0)}`"
                ),
                (
                    "- Tamanho total: "
                    f"`{source.get('total_bytes', 0)}` bytes"
                ),
                "",
            ]
        )

        manifests = source.get("manifests", [])

        if manifests:
            lines.append("Manifestos:")
            lines.append("")

            for manifest in manifests:
                lines.append(
                    "- "
                    f"`{manifest['path']}`: "
                    f"`valid={manifest['valid']}`"
                )

            lines.append("")

        reports = source.get("reports", [])

        if reports:
            lines.append("Relatórios localizados:")
            lines.append("")

            for report in reports:
                lines.append(f"- `{report}`")

            lines.append("")

    output.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "results/integrated/final-paper-v1/evidence"
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Retorna erro quando uma fonte estiver ausente "
            "ou um manifesto existente for inválido."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory = {
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "git_commit": git_output("rev-parse", "HEAD"),
        "git_branch": git_output(
            "branch",
            "--show-current",
        ),
        "sources": [
            inspect_source(source)
            for source in SOURCES
        ],
    }

    json_path = args.output_dir / "source-inventory.json"
    markdown_path = (
        args.output_dir / "source-inventory.md"
    )

    json_path.write_text(
        json.dumps(
            inventory,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    write_markdown(
        markdown_path,
        inventory,
    )

    missing_sources = [
        source["id"]
        for source in inventory["sources"]
        if not source["exists"]
    ]

    invalid_manifests = [
        manifest["path"]
        for source in inventory["sources"]
        for manifest in source.get("manifests", [])
        if not manifest["valid"]
    ]

    print(f"Inventário JSON: {json_path}")
    print(f"Inventário Markdown: {markdown_path}")
    print(f"Fontes ausentes: {len(missing_sources)}")
    print(f"Manifestos inválidos: {len(invalid_manifests)}")

    if missing_sources:
        print(
            "Fontes ausentes:",
            ", ".join(missing_sources),
        )

    if invalid_manifests:
        print(
            "Manifestos inválidos:",
            ", ".join(invalid_manifests),
        )

    if args.strict and (
        missing_sources
        or invalid_manifests
    ):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
