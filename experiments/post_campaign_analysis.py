from __future__ import annotations

import argparse
import subprocess
import sys

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = REPOSITORY_ROOT / "experiments"


def run(arguments: list[str]) -> None:
    print("$", " ".join(arguments), flush=True)
    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Etapa falhou com código {completed.returncode}: {' '.join(arguments)}"
        )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline pós-campanha: comparação, exportação e baselines."
    )
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--opensearch-url", default="http://localhost:9200")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--skip-baselines", action="store_true")
    parser.add_argument(
        "--baseline-input",
        type=Path,
        default=None,
        help="Usa um rca-documents.json já exportado, evitando consulta ao OpenSearch.",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    campaign = arguments.campaign_dir.expanduser().resolve()

    run(
        [
            sys.executable,
            str(EXPERIMENTS / "compare_e2e_results.py"),
            "--campaign-dir",
            str(campaign),
            "--bootstrap-samples",
            str(arguments.bootstrap_samples),
            "--seed",
            str(arguments.seed),
        ]
    )

    if arguments.skip_baselines:
        return 0

    baseline_input = (
        arguments.baseline_input.expanduser().resolve()
        if arguments.baseline_input
        else campaign / "baseline-input" / "rca-documents.json"
    )

    if not arguments.skip_export and arguments.baseline_input is None:
        run(
            [
                sys.executable,
                str(EXPERIMENTS / "export_rca_evidence.py"),
                "--campaign-dir",
                str(campaign),
                "--opensearch-url",
                arguments.opensearch_url,
                "--output",
                str(baseline_input),
            ]
        )

    if not baseline_input.is_file():
        raise FileNotFoundError(
            f"Entrada dos baselines não encontrada: {baseline_input}. "
            "Exporte no computador da campanha ou informe --baseline-input."
        )

    run(
        [
            sys.executable,
            str(EXPERIMENTS / "run_baselines.py"),
            "--input",
            str(baseline_input),
            "--output-dir",
            str(campaign / "baselines"),
        ]
    )

    run(
        [
            sys.executable,
            str(EXPERIMENTS / "build_experimental_report.py"),
            "--campaign-dir",
            str(campaign),
        ]
    )

    print(f"Pipeline pós-campanha concluído: {campaign}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, FileNotFoundError, ValueError) as exception:
        print(f"ERRO: {exception}", file=sys.stderr)
        raise SystemExit(2)
