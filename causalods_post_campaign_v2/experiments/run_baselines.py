from __future__ import annotations

import argparse
import json
import sys

from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis_utils import (
    describe,
    flatten_mapping,
    generate_sha256sums,
    mcnemar_exact,
    proportion_summary,
    read_json,
    write_csv,
    write_json,
)
from baselines import (
    BaselineSample,
    FirstAnomalyBaseline,
    MaxAnomalyZScoreBaseline,
    MaxTemporalScoreBaseline,
)


METHODS = {
    "max_temporal_score": MaxTemporalScoreBaseline,
    "max_anomaly_zscore": MaxAnomalyZScoreBaseline,
    "first_anomaly_in_time": FirstAnomalyBaseline,
}


class BaselineEvaluationError(RuntimeError):
    pass


def load_samples(path: Path) -> list[BaselineSample]:
    payload = read_json(path)

    if isinstance(payload, dict):
        documents = payload.get("documents")
    else:
        documents = payload

    if not isinstance(documents, list):
        raise BaselineEvaluationError(
            "Entrada deve ser lista ou objeto com campo documents"
        )

    return [BaselineSample.from_dict(item) for item in documents]


def causalods_correct(sample: BaselineSample) -> bool | None:
    if sample.ground_truth is None:
        return None
    return sample.campaign_row.get("top_service") == sample.ground_truth


def evaluate(samples: list[BaselineSample], method_names: list[str]) -> list[dict[str, Any]]:
    methods = []

    for name in method_names:
        if name == "max_temporal_score":
            methods.append(MaxTemporalScoreBaseline(minimum_score=0.0))
        elif name == "max_anomaly_zscore":
            methods.append(MaxAnomalyZScoreBaseline(minimum_zscore=0.0))
        elif name == "first_anomaly_in_time":
            methods.append(FirstAnomalyBaseline())
        else:
            raise BaselineEvaluationError(f"Método desconhecido: {name}")

    results: list[dict[str, Any]] = []

    for sample in samples:
        for method in methods:
            prediction = method.predict(sample).to_dict()
            prediction["causalods_top_service"] = sample.campaign_row.get("top_service")
            prediction["causalods_correct"] = causalods_correct(sample)
            prediction["causalods_confidence"] = sample.campaign_row.get("confidence")
            prediction["causalods_is_reliable"] = sample.campaign_row.get("is_reliable")
            results.append(prediction)

    results.sort(
        key=lambda item: (
            item["method"],
            item["source_version"],
            item["scenario"],
            item["iteration"],
        )
    )
    return results


def summarize(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_method_version_scenario: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_method_version: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in results:
        by_method_version_scenario[
            (row["method"], row["source_version"], row["scenario"])
        ].append(row)
        by_method_version[(row["method"], row["source_version"])].append(row)

    scenario_metrics: list[dict[str, Any]] = []
    for key in sorted(by_method_version_scenario):
        method, source_version, scenario = key
        rows = by_method_version_scenario[key]
        accuracy = proportion_summary(row["correct"] for row in rows)
        abstention = proportion_summary(row["abstained"] for row in rows)
        execution = describe(row["execution_time_ms"] for row in rows)
        scores = describe(row["score"] for row in rows)

        scenario_metrics.append(
            {
                "method": method,
                "source_version": source_version,
                "scenario": scenario,
                "ground_truth": rows[0]["ground_truth"],
                "executions": len(rows),
                **{
                    f"accuracy.{name}": value
                    for name, value in accuracy.items()
                },
                **{
                    f"abstention.{name}": value
                    for name, value in abstention.items()
                },
                **{
                    f"execution_time_ms.{name}": value
                    for name, value in execution.items()
                },
                **{
                    f"score.{name}": value
                    for name, value in scores.items()
                },
            }
        )

    overall_metrics: list[dict[str, Any]] = []
    for key in sorted(by_method_version):
        method, source_version = key
        rows = by_method_version[key]
        fault_rows = [row for row in rows if row["ground_truth"] is not None]
        healthy_rows = [row for row in rows if row["ground_truth"] is None]

        fault_accuracy = proportion_summary(row["correct"] for row in fault_rows)
        healthy_abstention = proportion_summary(row["abstained"] for row in healthy_rows)
        overall_execution = describe(row["execution_time_ms"] for row in rows)

        overall_metrics.append(
            {
                "method": method,
                "source_version": source_version,
                "executions": len(rows),
                "fault_executions": len(fault_rows),
                "healthy_executions": len(healthy_rows),
                **{
                    f"fault_accuracy.{name}": value
                    for name, value in fault_accuracy.items()
                },
                **{
                    f"healthy_abstention.{name}": value
                    for name, value in healthy_abstention.items()
                },
                **{
                    f"execution_time_ms.{name}": value
                    for name, value in overall_execution.items()
                },
            }
        )

    return scenario_metrics, overall_metrics


def build_statistical_tests(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        if row["ground_truth"] is not None:
            grouped[(row["method"], row["source_version"])].append(row)

    output: dict[str, Any] = {}

    for (method, source_version), rows in sorted(grouped.items()):
        evaluable = [
            row
            for row in rows
            if row.get("correct") is not None
            and row.get("causalods_correct") is not None
        ]
        output[f"{method}:{source_version}"] = {
            "comparison": "baseline_vs_causalods_on_same_runs",
            "baseline": method,
            "causalods_version": source_version,
            **mcnemar_exact(
                [bool(row["correct"]) for row in evaluable],
                [bool(row["causalods_correct"]) for row in evaluable],
            ),
        }

    return output


def render_report(
    input_path: Path,
    scenario_metrics: list[dict[str, Any]],
    overall_metrics: list[dict[str, Any]],
    tests: dict[str, Any],
) -> str:
    lines = [
        "# Avaliação dos baselines do CausalODS",
        "",
        f"Entrada: `{input_path}`",
        "",
        "## Definições",
        "",
        "- `max_temporal_score`: escolhe o serviço com maior score temporal pré-modelo.",
        "- `max_anomaly_zscore`: escolhe o maior z-score estritamente acima de zero; caso contrário, abstém.",
        "- `first_anomaly_in_time`: escolhe a evidência temporal com timestamp de anomalia mais antigo.",
        "- Acurácia principal é calculada nos cenários de falha. No cenário healthy é avaliada a abstenção.",
        "",
        "## Métricas gerais",
        "",
        "| Baseline | Fonte | N falhas | Acurácia fault-only | N healthy | Abstenção healthy | Tempo médio (ms) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    for row in overall_metrics:
        fault_rate = row.get("fault_accuracy.rate")
        healthy_rate = row.get("healthy_abstention.rate")
        execution = row.get("execution_time_ms.mean")
        lines.append(
            f"| {row['method']} | {row['source_version']} | {row['fault_executions']} | "
            f"{fault_rate * 100:.2f}%" if fault_rate is not None else "—"
        )
        # A construção acima é deliberadamente substituída abaixo para evitar
        # precedência de operador pouco legível.
        lines.pop()
        fault_text = "—" if fault_rate is None else f"{fault_rate * 100:.2f}%"
        healthy_text = "—" if healthy_rate is None else f"{healthy_rate * 100:.2f}%"
        execution_text = "—" if execution is None else f"{execution:.6f}"
        lines.append(
            f"| {row['method']} | {row['source_version']} | {row['fault_executions']} | "
            f"{fault_text} | {row['healthy_executions']} | {healthy_text} | {execution_text} |"
        )

    lines.extend(
        [
            "",
            "## Métricas por cenário",
            "",
            "| Baseline | Fonte | Cenário | N | Acurácia | Abstenção | Score médio |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )

    for row in scenario_metrics:
        accuracy = row.get("accuracy.rate")
        abstention = row.get("abstention.rate")
        score = row.get("score.mean")
        lines.append(
            f"| {row['method']} | {row['source_version']} | {row['scenario']} | {row['executions']} | "
            f"{'—' if accuracy is None else f'{accuracy * 100:.2f}%'} | "
            f"{'—' if abstention is None else f'{abstention * 100:.2f}%'} | "
            f"{'—' if score is None else f'{score:.6f}'} |"
        )

    lines.extend(
        [
            "",
            "## Comparação pareada com o CausalODS",
            "",
            "McNemar exato é calculado somente nos quatro cenários de falha e usa os mesmos `(scenario, iteration)`.",
            "",
            "| Comparação | Pares | Baseline somente correto | CausalODS somente correto | p-valor |",
            "|---|---:|---:|---:|---:|",
        ]
    )

    for name, payload in tests.items():
        p_value = payload.get("p_value_two_sided")
        lines.append(
            f"| {name} | {payload.get('pairs', 0)} | {payload.get('left_only_correct', 0)} | "
            f"{payload.get('right_only_correct', 0)} | "
            f"{'—' if p_value is None else f'{p_value:.6f}'} |"
        )

    lines.extend(
        [
            "",
            "## Observações de validade",
            "",
            "- Os baselines usam somente evidências pré-modelo registradas no RCAResult; não usam o ranking final como entrada.",
            "- O mesmo trace e a mesma iteração são usados na comparação pareada entre baseline e CausalODS.",
            "- Um baseline pode abster quando não existe evidência compatível; a abstenção em falha é contabilizada como erro.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa e avalia os três baselines de RCA."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=tuple(METHODS),
        default=tuple(METHODS),
    )
    parser.add_argument(
        "--source-versions",
        nargs="+",
        choices=("v1", "v2"),
        default=("v1", "v2"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    input_path = arguments.input.expanduser().resolve()
    output_directory = (
        arguments.output_dir.expanduser().resolve()
        if arguments.output_dir
        else input_path.parent.parent / "baselines"
    )

    samples = [
        sample
        for sample in load_samples(input_path)
        if sample.source_version in set(arguments.source_versions)
    ]

    if not samples:
        raise BaselineEvaluationError("Nenhuma amostra após os filtros")

    results = evaluate(samples, list(arguments.methods))
    scenario_metrics, overall_metrics = summarize(results)
    tests = build_statistical_tests(results)

    output_directory.mkdir(parents=True, exist_ok=True)
    write_json(output_directory / "baseline_results.json", results)
    write_csv(
        output_directory / "baseline_results.csv",
        [
            {
                **{key: value for key, value in row.items() if key != "evidence"},
                "evidence": json.dumps(row["evidence"], ensure_ascii=False),
            }
            for row in results
        ],
    )
    write_json(output_directory / "baseline_metrics.json", {
        "overall": overall_metrics,
        "by_scenario": scenario_metrics,
    })
    write_csv(output_directory / "baseline_overall_metrics.csv", overall_metrics)
    write_csv(output_directory / "baseline_scenario_metrics.csv", scenario_metrics)
    write_json(output_directory / "statistical_tests.json", tests)
    (output_directory / "report.md").write_text(
        render_report(input_path, scenario_metrics, overall_metrics, tests),
        encoding="utf-8",
    )
    generate_sha256sums(output_directory)

    print(f"Baselines concluídos: {output_directory}")
    for row in overall_metrics:
        print(
            f"{row['method']} | source={row['source_version']} "
            f"| fault_accuracy={row.get('fault_accuracy.rate')} "
            f"| healthy_abstention={row.get('healthy_abstention.rate')}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BaselineEvaluationError, FileNotFoundError, ValueError, json.JSONDecodeError) as exception:
        print(f"ERRO: {exception}", file=sys.stderr)
        raise SystemExit(2)
