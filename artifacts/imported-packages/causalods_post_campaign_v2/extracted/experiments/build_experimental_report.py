from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path
from typing import Any

from analysis_utils import as_float, generate_sha256sums, read_json, write_json


def percent(value: Any) -> str:
    number = as_float(value)
    return "—" if number is None else f"{number * 100:.2f}%"


def number(value: Any, digits: int = 4) -> str:
    converted = as_float(value)
    return "—" if converted is None else f"{converted:.{digits}f}"


def overall_by_version(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["version"]): row
        for row in comparison.get("overall_metrics", [])
        if isinstance(row, dict) and row.get("version")
    }


def scenarios_by_version(comparison: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["version"]), str(row["scenario"])): row
        for row in comparison.get("scenario_metrics", [])
        if isinstance(row, dict) and row.get("version") and row.get("scenario")
    }


def render(
    campaign_directory: Path,
    comparison: dict[str, Any],
    baseline_metrics: dict[str, Any] | None,
    baseline_tests: dict[str, Any] | None,
) -> str:
    overall = overall_by_version(comparison)
    scenarios = scenarios_by_version(comparison)

    lines = [
        "# Resultados experimentais consolidados — CausalODS",
        "",
        f"Campanha: `{campaign_directory}`",
        "",
        "## 1. Configuração avaliada",
        "",
        "Foram avaliadas as versões RCA v1 e v2 nos cenários `healthy`, "
        "`payment_failure`, `payment_latency`, `order_failure` e `order_latency`. "
        "O cenário saudável é usado para medir falsos positivos; os quatro cenários "
        "de falha possuem ground truth de serviço causa raiz.",
        "",
        "## 2. Resultado geral v1 × v2",
        "",
        "| Versão | Execuções | Pass rate | Top-1 fault-only | Reliability | Incidente | FP healthy | FN falhas |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for version in ("v1", "v2"):
        row = overall.get(version)
        if not row:
            continue
        lines.append(
            f"| {version} | {row.get('executions')} | {percent(row.get('runner_pass.rate'))} | "
            f"{percent(row.get('top1_accuracy_fault_only.rate'))} | "
            f"{percent(row.get('reliability_accuracy.rate'))} | "
            f"{percent(row.get('incident_decision_accuracy.rate'))} | "
            f"{percent(row.get('healthy_false_positive_rate'))} | "
            f"{percent(row.get('fault_false_negative_rate'))} |"
        )

    lines.extend(
        [
            "",
            "## 3. Resultado por cenário",
            "",
            "| Versão | Cenário | Pass rate | Top-1 | Reliability | Incidente | Confiança média |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )

    for version in ("v1", "v2"):
        for scenario in (
            "healthy",
            "payment_failure",
            "payment_latency",
            "order_failure",
            "order_latency",
        ):
            row = scenarios.get((version, scenario))
            if not row:
                continue
            lines.append(
                f"| {version} | {scenario} | {percent(row.get('runner_pass.rate'))} | "
                f"{percent(row.get('top1_accuracy_fault_only.rate'))} | "
                f"{percent(row.get('reliability_accuracy.rate'))} | "
                f"{percent(row.get('incident_decision_accuracy.rate'))} | "
                f"{number(row.get('confidence.mean'))} |"
            )

    tests = comparison.get("statistical_tests", {})
    lines.extend(
        [
            "",
            "## 4. Comparação estatística v1 × v2",
            "",
        ]
    )

    if tests.get("available"):
        lines.extend(
            [
                "| Métrica | Pares | v1 somente correta | v2 somente correta | p-valor McNemar |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for metric, payload in tests.get("overall", {}).get("boolean_metrics", {}).items():
            lines.append(
                f"| {metric} | {payload.get('pairs')} | {payload.get('left_only_correct')} | "
                f"{payload.get('right_only_correct')} | {number(payload.get('p_value_two_sided'), 6)} |"
            )
    else:
        lines.append(str(tests.get("reason", "Testes pareados indisponíveis.")))

    lines.extend(["", "## 5. Baselines", ""])

    if baseline_metrics:
        lines.extend(
            [
                "| Método | Fonte | Acurácia fault-only | Abstenção healthy | Tempo médio (ms) |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in baseline_metrics.get("overall", []):
            lines.append(
                f"| {row.get('method')} | {row.get('source_version')} | "
                f"{percent(row.get('fault_accuracy.rate'))} | "
                f"{percent(row.get('healthy_abstention.rate'))} | "
                f"{number(row.get('execution_time_ms.mean'), 6)} |"
            )

        if baseline_tests:
            lines.extend(
                [
                    "",
                    "### Comparação pareada baseline × CausalODS",
                    "",
                    "| Comparação | Pares | Baseline somente correto | CausalODS somente correto | p-valor |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for name, payload in baseline_tests.items():
                lines.append(
                    f"| {name} | {payload.get('pairs')} | {payload.get('left_only_correct')} | "
                    f"{payload.get('right_only_correct')} | {number(payload.get('p_value_two_sided'), 6)} |"
                )
    else:
        lines.append(
            "Os baselines ainda não foram executados. Exporte os RCAResults completos "
            "e execute `experiments/run_baselines.py`."
        )

    lines.extend(
        [
            "",
            "## 6. Interpretação",
            "",
            "1. Top-1 deve ser analisado somente nos cenários de falha; no saudável, qualquer Top-1 é apenas uma ordenação fraca.",
            "2. A decisão operacional depende do reliability gate e da criação correta de incidente.",
            "3. Diferenças entre versões devem ser discutidas por cenário e não apenas pela média geral.",
            "4. Os baselines precisam usar exatamente os mesmos traces de cada execução para preservar a comparação pareada.",
            "",
            "## 7. Ameaças à validade que devem acompanhar as tabelas",
            "",
            "- ambiente controlado e número reduzido de serviços;",
            "- falhas sintéticas com intensidade fixa;",
            "- dependência da representatividade dos dados de treinamento;",
            "- possíveis efeitos de ordem e aquecimento dos containers;",
            "- validade externa ainda dependente de dataset ou aplicação pública adicional.",
            "",
        ]
    )

    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera o documento consolidado dos resultados experimentais."
    )
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    campaign = arguments.campaign_dir.expanduser().resolve()
    output = (
        arguments.output_dir.expanduser().resolve()
        if arguments.output_dir
        else campaign / "final-analysis"
    )

    comparison_path = campaign / "comparison" / "comparison.json"
    if not comparison_path.is_file():
        raise FileNotFoundError(
            f"Execute compare_e2e_results.py antes: {comparison_path}"
        )

    comparison = read_json(comparison_path)
    baseline_metrics_path = campaign / "baselines" / "baseline_metrics.json"
    baseline_tests_path = campaign / "baselines" / "statistical_tests.json"

    baseline_metrics = read_json(baseline_metrics_path) if baseline_metrics_path.is_file() else None
    baseline_tests = read_json(baseline_tests_path) if baseline_tests_path.is_file() else None

    output.mkdir(parents=True, exist_ok=True)
    report = render(campaign, comparison, baseline_metrics, baseline_tests)
    (output / "report.md").write_text(report, encoding="utf-8")
    write_json(
        output / "report-data.json",
        {
            "campaign": str(campaign),
            "comparison": comparison,
            "baseline_metrics": baseline_metrics,
            "baseline_tests": baseline_tests,
        },
    )
    generate_sha256sums(output)
    print(f"Relatório consolidado: {output / 'report.md'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exception:
        print(f"ERRO: {exception}", file=sys.stderr)
        raise SystemExit(2)
