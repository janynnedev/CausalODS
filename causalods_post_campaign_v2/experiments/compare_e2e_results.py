from __future__ import annotations

import argparse
import json
import math
import re
import sys

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analysis_utils import (
    as_float,
    as_int,
    bootstrap_mean_interval,
    describe,
    flatten_mapping,
    generate_sha256sums,
    mcnemar_exact,
    paired_bootstrap_difference,
    proportion_summary,
    read_json,
    write_csv,
    write_json,
)


SCENARIOS = (
    "healthy",
    "payment_failure",
    "payment_latency",
    "order_failure",
    "order_latency",
)

GROUND_TRUTH = {
    "healthy": None,
    "payment_failure": "payment-service",
    "payment_latency": "payment-service",
    "order_failure": "order-service",
    "order_latency": "order-service",
}

EXPECTED_HTTP_FAMILY = {
    "healthy": 2,
    "payment_failure": 5,
    "payment_latency": 2,
    "order_failure": 5,
    "order_latency": 2,
}

EXPECTED_RELIABLE = {
    scenario: scenario != "healthy"
    for scenario in SCENARIOS
}

EXPECTED_INCIDENT = EXPECTED_RELIABLE.copy()

LATENCY_SCENARIOS = {
    "payment_latency",
    "order_latency",
}

REQUIRED_FIELDS = {
    "run_id",
    "scenario",
    "iteration",
    "runtime_model_version",
    "model_version",
    "pipeline_version",
    "http_status",
    "request_duration_ms",
    "trace_id",
    "top_service",
    "confidence",
    "is_reliable",
    "passed",
    "failures",
}


class ValidationError(RuntimeError):
    pass


def parse_repeat(path: Path) -> int | None:
    match = re.fullmatch(r"all-repeat(\d+)\.json", path.name)
    return int(match.group(1)) if match else None


def find_result_file(version_directory: Path) -> Path:
    candidates = [
        path
        for path in version_directory.glob("all-repeat*.json")
        if parse_repeat(path) is not None
    ]

    if not candidates:
        raise FileNotFoundError(
            f"Nenhum all-repeat*.json em {version_directory}"
        )

    return max(candidates, key=lambda path: (parse_repeat(path) or -1, path.stat().st_mtime))


def expected_model_version(version: str) -> str:
    return f"xgboost-rca-{version}"


def expected_pipeline_version(version: str) -> str:
    return f"causalods-rca-{version}"


def validate_campaign_status(campaign_directory: Path) -> dict[str, Any] | None:
    status_path = campaign_directory / "status.json"
    if not status_path.is_file():
        return None

    status = read_json(status_path)
    if not isinstance(status, dict):
        raise ValidationError("status.json não contém objeto JSON")

    if status.get("state") not in {"completed", "running", "failed", "interrupted"}:
        raise ValidationError(
            f"state inválido no status.json: {status.get('state')!r}"
        )

    return status


def validate_rows(
    rows: Any,
    *,
    version: str,
    expected_repeat: int | None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(rows, list):
        raise ValidationError(f"Resultado {version} não é uma lista")

    if not rows:
        raise ValidationError(f"Resultado {version} está vazio")

    keys: set[tuple[str, int]] = set()
    scenario_counts: Counter[str] = Counter()

    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"linha {position}: não é objeto")
            continue

        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            errors.append(f"linha {position}: campos ausentes={missing}")

        scenario = str(row.get("scenario"))
        iteration = as_int(row.get("iteration"))

        if scenario not in SCENARIOS:
            errors.append(f"linha {position}: cenário inválido={scenario!r}")
        else:
            scenario_counts[scenario] += 1

        if iteration is None or iteration < 1:
            errors.append(f"linha {position}: iteration inválida={row.get('iteration')!r}")
        else:
            key = (scenario, iteration)
            if key in keys:
                errors.append(f"linha {position}: par cenário/iteração duplicado={key}")
            keys.add(key)

        if row.get("runtime_model_version") != version:
            errors.append(
                f"linha {position}: runtime_model_version={row.get('runtime_model_version')!r}; esperado={version!r}"
            )

        if row.get("pipeline_version") != expected_pipeline_version(version):
            errors.append(
                f"linha {position}: pipeline_version={row.get('pipeline_version')!r}; "
                f"esperado={expected_pipeline_version(version)!r}"
            )

        model_version = row.get("model_version")
        if model_version != expected_model_version(version):
            errors.append(
                f"linha {position}: model_version={model_version!r}; "
                f"esperado={expected_model_version(version)!r}"
            )

        if not row.get("trace_id"):
            errors.append(f"linha {position}: trace_id ausente")

        if not row.get("rca_result_id"):
            errors.append(f"linha {position}: rca_result_id ausente")

        before = as_int(row.get("restart_count_before"))
        after = as_int(row.get("restart_count_after"))
        if before is not None and after is not None and after != before:
            warnings.append(
                f"linha {position}: restart_count mudou de {before} para {after}"
            )

    if expected_repeat is None:
        counts = set(scenario_counts.values())
        if len(counts) != 1:
            errors.append(
                f"distribuição desigual de cenários: {dict(scenario_counts)}"
            )
        inferred_repeat = next(iter(counts), 0)
    else:
        inferred_repeat = expected_repeat
        expected_counts = {scenario: expected_repeat for scenario in SCENARIOS}
        if dict(scenario_counts) != expected_counts:
            errors.append(
                f"distribuição de cenários incorreta: esperado={expected_counts}; "
                f"encontrado={dict(scenario_counts)}"
            )

    if len(rows) != inferred_repeat * len(SCENARIOS):
        errors.append(
            f"quantidade incorreta: esperado={inferred_repeat * len(SCENARIOS)}; encontrado={len(rows)}"
        )

    return {
        "version": version,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "rows": len(rows),
        "repeat": inferred_repeat,
        "scenario_counts": dict(scenario_counts),
    }


def row_metrics(row: dict[str, Any], version: str) -> dict[str, Any]:
    scenario = str(row["scenario"])
    expected_service = GROUND_TRUTH[scenario]
    expected_reliable = EXPECTED_RELIABLE[scenario]
    expected_incident = EXPECTED_INCIDENT[scenario]

    top_service = row.get("top_service")
    is_reliable = row.get("is_reliable")
    incident_created = bool(row.get("incident_id"))
    incident_root_cause = row.get("incident_root_cause")
    http_status = as_int(row.get("http_status"))
    request_duration = as_float(row.get("request_duration_ms"))
    delay_ms = as_int(row.get("fault_delay_ms")) or 0

    rca_before = as_int(row.get("rca_count_before"))
    rca_after = as_int(row.get("rca_count_after"))
    incident_before = as_int(row.get("incident_count_before"))
    incident_after = as_int(row.get("incident_count_after"))
    restart_before = as_int(row.get("restart_count_before"))
    restart_after = as_int(row.get("restart_count_after"))

    top1_evaluable = expected_service is not None
    top1_correct = (
        top_service == expected_service
        if top1_evaluable
        else None
    )

    reliability_correct = (
        bool(is_reliable) == expected_reliable
        if is_reliable is not None
        else False
    )

    incident_decision_correct = incident_created == expected_incident
    incident_root_cause_correct = (
        incident_root_cause == expected_service
        if expected_incident
        else None
    )

    http_family_correct = (
        http_status is not None
        and http_status // 100 == EXPECTED_HTTP_FAMILY[scenario]
    )

    minimum_duration = max(1200.0, delay_ms * 0.8) if scenario in LATENCY_SCENARIOS else None
    latency_duration_correct = (
        request_duration is not None and request_duration >= minimum_duration
        if minimum_duration is not None
        else None
    )

    rca_delta = (
        rca_after - rca_before
        if rca_before is not None and rca_after is not None
        else None
    )
    incident_delta = (
        incident_after - incident_before
        if incident_before is not None and incident_after is not None
        else None
    )

    return {
        "version": version,
        "run_id": row.get("run_id"),
        "scenario": scenario,
        "iteration": as_int(row.get("iteration")),
        "ground_truth": expected_service,
        "top_service": top_service,
        "top1_evaluable": top1_evaluable,
        "top1_correct": top1_correct,
        "expected_reliable": expected_reliable,
        "is_reliable": is_reliable,
        "reliability_correct": reliability_correct,
        "expected_incident": expected_incident,
        "incident_created": incident_created,
        "incident_decision_correct": incident_decision_correct,
        "incident_root_cause": incident_root_cause,
        "incident_root_cause_correct": incident_root_cause_correct,
        "http_status": http_status,
        "http_family_correct": http_family_correct,
        "request_duration_ms": request_duration,
        "latency_duration_correct": latency_duration_correct,
        "duration_ms": as_float(row.get("duration_ms")),
        "confidence": as_float(row.get("confidence")),
        "runner_passed": bool(row.get("passed")),
        "runner_failures": json.dumps(row.get("failures") or [], ensure_ascii=False),
        "rca_delta": rca_delta,
        "rca_document_created": rca_delta == 1 if rca_delta is not None else None,
        "incident_delta": incident_delta,
        "incident_delta_correct": (
            incident_delta == (1 if expected_incident else 0)
            if incident_delta is not None
            else None
        ),
        "restart_stable": (
            restart_after == restart_before
            if restart_before is not None and restart_after is not None
            else None
        ),
        "trace_id": row.get("trace_id"),
        "rca_result_id": row.get("rca_result_id"),
        "runtime_model_version": row.get("runtime_model_version"),
        "model_version": row.get("model_version"),
        "pipeline_version": row.get("pipeline_version"),
    }


def summarize_scope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    top1 = proportion_summary(row["top1_correct"] for row in rows)
    reliability = proportion_summary(row["reliability_correct"] for row in rows)
    incident = proportion_summary(row["incident_decision_correct"] for row in rows)
    incident_root = proportion_summary(row["incident_root_cause_correct"] for row in rows)
    runner = proportion_summary(row["runner_passed"] for row in rows)
    http = proportion_summary(row["http_family_correct"] for row in rows)
    latency = proportion_summary(row["latency_duration_correct"] for row in rows)
    rca_created = proportion_summary(row["rca_document_created"] for row in rows)
    incident_delta = proportion_summary(row["incident_delta_correct"] for row in rows)
    restart = proportion_summary(row["restart_stable"] for row in rows)

    confidence = describe(row["confidence"] for row in rows)
    request_duration = describe(row["request_duration_ms"] for row in rows)
    total_duration = describe(row["duration_ms"] for row in rows)

    return {
        "executions": len(rows),
        "runner_pass": runner,
        "top1_accuracy_fault_only": top1,
        "reliability_accuracy": reliability,
        "incident_decision_accuracy": incident,
        "incident_root_cause_accuracy": incident_root,
        "http_family_accuracy": http,
        "latency_duration_accuracy": latency,
        "rca_document_creation_accuracy": rca_created,
        "incident_delta_accuracy": incident_delta,
        "restart_stability": restart,
        "confidence": confidence,
        "request_duration_ms": request_duration,
        "scenario_duration_ms": total_duration,
    }


def build_summaries(run_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in run_rows:
        grouped[(row["version"], row["scenario"])].append(row)
        by_version[row["version"]].append(row)

    scenario_rows: list[dict[str, Any]] = []
    for version in sorted(by_version):
        for scenario in SCENARIOS:
            summary = summarize_scope(grouped[(version, scenario)])
            scenario_rows.append(
                {
                    "version": version,
                    "scenario": scenario,
                    "ground_truth": GROUND_TRUTH[scenario],
                    **flatten_mapping(summary),
                }
            )

    overall_rows: list[dict[str, Any]] = []
    for version in sorted(by_version):
        rows = by_version[version]
        summary = summarize_scope(rows)
        healthy = [row for row in rows if row["scenario"] == "healthy"]
        faults = [row for row in rows if row["scenario"] != "healthy"]

        healthy_false_positives = sum(bool(row["is_reliable"]) for row in healthy)
        fault_false_negatives = sum(not bool(row["is_reliable"]) for row in faults)

        overall_rows.append(
            {
                "version": version,
                **flatten_mapping(summary),
                "healthy_executions": len(healthy),
                "healthy_false_positives": healthy_false_positives,
                "healthy_false_positive_rate": (
                    healthy_false_positives / len(healthy) if healthy else None
                ),
                "fault_executions": len(faults),
                "fault_false_negatives": fault_false_negatives,
                "fault_false_negative_rate": (
                    fault_false_negatives / len(faults) if faults else None
                ),
                "healthy_confidence_mean": describe(
                    row["confidence"] for row in healthy
                )["mean"],
                "fault_confidence_mean": describe(
                    row["confidence"] for row in faults
                )["mean"],
            }
        )

    return scenario_rows, overall_rows


def pair_rows(
    rows_by_version: dict[str, list[dict[str, Any]]],
    left_version: str,
    right_version: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    left = {
        (row["scenario"], row["iteration"]): row
        for row in rows_by_version[left_version]
    }
    right = {
        (row["scenario"], row["iteration"]): row
        for row in rows_by_version[right_version]
    }

    common = sorted(set(left) & set(right), key=lambda item: (SCENARIOS.index(item[0]), item[1]))
    return [(left[key], right[key]) for key in common]


def statistical_tests(
    run_rows: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        by_version[row["version"]].append(row)

    versions = sorted(by_version)
    if "v1" not in by_version or "v2" not in by_version:
        return {
            "available": False,
            "reason": "São necessárias v1 e v2 para testes pareados.",
            "versions": versions,
        }

    pairs = pair_rows(by_version, "v1", "v2")
    result: dict[str, Any] = {
        "available": True,
        "left_version": "v1",
        "right_version": "v2",
        "pairs": len(pairs),
        "overall": {},
        "by_scenario": {},
    }

    boolean_metrics = (
        "runner_passed",
        "top1_correct",
        "reliability_correct",
        "incident_decision_correct",
        "incident_root_cause_correct",
    )

    def test_group(group_pairs: list[tuple[dict[str, Any], dict[str, Any]]], group_seed: int) -> dict[str, Any]:
        payload: dict[str, Any] = {"boolean_metrics": {}, "continuous_metrics": {}}

        for metric in boolean_metrics:
            evaluated = [
                (left[metric], right[metric])
                for left, right in group_pairs
                if left[metric] is not None and right[metric] is not None
            ]
            payload["boolean_metrics"][metric] = (
                mcnemar_exact(
                    [bool(item[0]) for item in evaluated],
                    [bool(item[1]) for item in evaluated],
                )
                if evaluated
                else {"pairs": 0, "p_value_two_sided": None}
            )

        for metric in ("confidence", "request_duration_ms", "duration_ms"):
            evaluated = [
                (as_float(left[metric]), as_float(right[metric]))
                for left, right in group_pairs
            ]
            numeric = [
                (left, right)
                for left, right in evaluated
                if left is not None and right is not None
            ]
            payload["continuous_metrics"][metric] = paired_bootstrap_difference(
                [item[0] for item in numeric],
                [item[1] for item in numeric],
                samples=bootstrap_samples,
                seed=group_seed,
            )

        return payload

    result["overall"] = test_group(pairs, seed)

    for index, scenario in enumerate(SCENARIOS, start=1):
        group_pairs = [
            pair for pair in pairs if pair[0]["scenario"] == scenario
        ]
        result["by_scenario"][scenario] = test_group(
            group_pairs,
            seed + index,
        )

    return result


def build_confidence_rows(run_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in run_rows:
        value = as_float(row["confidence"])
        if value is not None:
            grouped[(row["version"], row["scenario"])].append(value)

    rows: list[dict[str, Any]] = []
    for version, scenario in sorted(grouped, key=lambda item: (item[0], SCENARIOS.index(item[1]))):
        rows.append(
            {
                "version": version,
                "scenario": scenario,
                **describe(grouped[(version, scenario)]),
            }
        )
    return rows


def build_metric_table(
    run_rows: list[dict[str, Any]],
    metric: str,
    *,
    output_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[bool | None]] = defaultdict(list)
    for row in run_rows:
        grouped[(row["version"], row["scenario"])].append(row[metric])

    rows: list[dict[str, Any]] = []
    for version, scenario in sorted(grouped, key=lambda item: (item[0], SCENARIOS.index(item[1]))):
        rows.append(
            {
                "version": version,
                "scenario": scenario,
                "metric": output_name,
                **proportion_summary(grouped[(version, scenario)]),
            }
        )
    return rows


def markdown_rate(value: Any) -> str:
    number = as_float(value)
    return "—" if number is None else f"{number * 100:.2f}%"


def markdown_number(value: Any, digits: int = 4) -> str:
    number = as_float(value)
    return "—" if number is None else f"{number:.{digits}f}"


def render_report(
    *,
    campaign_directory: Path,
    validation: dict[str, Any],
    scenario_metrics: list[dict[str, Any]],
    overall_metrics: list[dict[str, Any]],
    tests: dict[str, Any],
) -> str:
    lines = [
        "# Comparação experimental CausalODS v1 × v2",
        "",
        f"Campanha analisada: `{campaign_directory}`",
        "",
        "## Integridade e proveniência",
        "",
    ]

    for version, payload in validation["versions"].items():
        lines.append(
            f"- **{version}**: válido={payload['valid']}; "
            f"linhas={payload['rows']}; repetições={payload['repeat']}; "
            f"erros={len(payload['errors'])}; avisos={len(payload['warnings'])}."
        )

    lines.extend(
        [
            "",
            "## Métricas gerais",
            "",
            "| Versão | Execuções | Pass rate | Top-1 fault-only | Reliability | Incidente | FP healthy | FN falhas | Confiança healthy | Confiança falhas |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    for row in overall_metrics:
        lines.append(
            "| {version} | {executions} | {pass_rate} | {top1} | {reliability} | {incident} | {fp} | {fn} | {healthy_conf} | {fault_conf} |".format(
                version=row["version"],
                executions=row["executions"],
                pass_rate=markdown_rate(row.get("runner_pass.rate")),
                top1=markdown_rate(row.get("top1_accuracy_fault_only.rate")),
                reliability=markdown_rate(row.get("reliability_accuracy.rate")),
                incident=markdown_rate(row.get("incident_decision_accuracy.rate")),
                fp=markdown_rate(row.get("healthy_false_positive_rate")),
                fn=markdown_rate(row.get("fault_false_negative_rate")),
                healthy_conf=markdown_number(row.get("healthy_confidence_mean")),
                fault_conf=markdown_number(row.get("fault_confidence_mean")),
            )
        )

    lines.extend(
        [
            "",
            "## Resultados por cenário",
            "",
            "| Versão | Cenário | N | Pass rate | Top-1 | Reliability | Incidente | Confiança média | Request p95 (ms) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    for row in scenario_metrics:
        lines.append(
            "| {version} | {scenario} | {n} | {pass_rate} | {top1} | {reliability} | {incident} | {confidence} | {request_p95} |".format(
                version=row["version"],
                scenario=row["scenario"],
                n=row["executions"],
                pass_rate=markdown_rate(row.get("runner_pass.rate")),
                top1=markdown_rate(row.get("top1_accuracy_fault_only.rate")),
                reliability=markdown_rate(row.get("reliability_accuracy.rate")),
                incident=markdown_rate(row.get("incident_decision_accuracy.rate")),
                confidence=markdown_number(row.get("confidence.mean")),
                request_p95=markdown_number(row.get("request_duration_ms.p95"), 2),
            )
        )

    lines.extend(
        [
            "",
            "## Testes estatísticos pareados",
            "",
        ]
    )

    if not tests.get("available"):
        lines.append(str(tests.get("reason")))
    else:
        lines.append(
            "As execuções foram pareadas por `(scenario, iteration)`. "
            "Métricas booleanas usam McNemar exato bicaudal; métricas contínuas "
            "usam bootstrap pareado da diferença média `v2 − v1`."
        )
        lines.extend(
            [
                "",
                "| Métrica | Pares | v1 somente correta | v2 somente correta | p-valor |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for metric, payload in tests["overall"]["boolean_metrics"].items():
            lines.append(
                f"| {metric} | {payload.get('pairs', 0)} | "
                f"{payload.get('left_only_correct', 0)} | "
                f"{payload.get('right_only_correct', 0)} | "
                f"{markdown_number(payload.get('p_value_two_sided'), 6)} |"
            )

        lines.extend(
            [
                "",
                "| Métrica contínua | Pares | Diferença média v2−v1 | IC95% inferior | IC95% superior |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for metric, payload in tests["overall"]["continuous_metrics"].items():
            lines.append(
                f"| {metric} | {payload.get('pairs', 0)} | "
                f"{markdown_number(payload.get('mean_difference_right_minus_left'))} | "
                f"{markdown_number(payload.get('ci95_lower'))} | "
                f"{markdown_number(payload.get('ci95_upper'))} |"
            )

    lines.extend(
        [
            "",
            "## Interpretação metodológica",
            "",
            "- O cenário `healthy` não possui ground truth de causa raiz; nele são avaliados ausência de confiabilidade e ausência de incidente.",
            "- Top-1 accuracy é calculada apenas nos quatro cenários de falha.",
            "- Resultados de confiança devem ser interpretados junto com reliability e margem, e não como acurácia isolada.",
            "- O relatório é derivado dos JSONs preservados pela campanha e não consulta o OpenSearch.",
            "",
        ]
    )

    return "\n".join(lines)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara os resultados E2E das versões v1 e v2 do CausalODS."
    )
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Gera saídas mesmo com erros de validação; não recomendado para resultados finais.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    campaign_directory = arguments.campaign_dir.expanduser().resolve()
    output_directory = (
        arguments.output_dir.expanduser().resolve()
        if arguments.output_dir
        else campaign_directory / "comparison"
    )

    if not campaign_directory.is_dir():
        raise FileNotFoundError(campaign_directory)

    status = validate_campaign_status(campaign_directory)
    expected_repeat = as_int(status.get("repeat")) if status else None

    raw_by_version: dict[str, list[dict[str, Any]]] = {}
    validation: dict[str, Any] = {
        "campaign_directory": str(campaign_directory),
        "status_present": status is not None,
        "status_state": status.get("state") if status else None,
        "expected_repeat": expected_repeat,
        "versions": {},
    }

    versions = [
        version
        for version in ("v1", "v2")
        if (campaign_directory / version).is_dir()
    ]

    if not versions:
        raise ValidationError("Nenhum diretório v1 ou v2 encontrado")

    for version in versions:
        path = find_result_file(campaign_directory / version)
        rows = read_json(path)
        result = validate_rows(
            rows,
            version=version,
            expected_repeat=expected_repeat,
        )
        result["source_file"] = str(path)
        validation["versions"][version] = result
        raw_by_version[version] = rows

    validation["valid"] = all(
        payload["valid"]
        for payload in validation["versions"].values()
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    write_json(output_directory / "provenance_validation.json", validation)

    if not validation["valid"] and not arguments.allow_incomplete:
        errors = [
            f"{version}: {error}"
            for version, payload in validation["versions"].items()
            for error in payload["errors"]
        ]
        raise ValidationError(
            "Validação da campanha falhou:\n- " + "\n- ".join(errors)
        )

    run_rows = [
        row_metrics(row, version)
        for version, rows in raw_by_version.items()
        for row in rows
    ]
    run_rows.sort(
        key=lambda row: (
            row["version"],
            SCENARIOS.index(row["scenario"]),
            row["iteration"],
        )
    )

    scenario_metrics, overall_metrics = build_summaries(run_rows)
    confidence_rows = build_confidence_rows(run_rows)
    reliability_rows = build_metric_table(
        run_rows,
        "reliability_correct",
        output_name="reliability_correct",
    )
    incident_rows = build_metric_table(
        run_rows,
        "incident_decision_correct",
        output_name="incident_decision_correct",
    )

    duration_rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        grouped[(row["version"], row["scenario"])].append(row)
    for version, scenario in sorted(grouped, key=lambda item: (item[0], SCENARIOS.index(item[1]))):
        group = grouped[(version, scenario)]
        duration_rows.append(
            {
                "version": version,
                "scenario": scenario,
                **{
                    f"request_duration_ms.{key}": value
                    for key, value in describe(row["request_duration_ms"] for row in group).items()
                },
                **{
                    f"scenario_duration_ms.{key}": value
                    for key, value in describe(row["duration_ms"] for row in group).items()
                },
            }
        )

    tests = statistical_tests(
        run_rows,
        bootstrap_samples=arguments.bootstrap_samples,
        seed=arguments.seed,
    )

    write_csv(output_directory / "run_level_metrics.csv", run_rows)
    write_csv(output_directory / "scenario_metrics.csv", scenario_metrics)
    write_csv(output_directory / "overall_metrics.csv", overall_metrics)
    write_csv(output_directory / "confidence_summary.csv", confidence_rows)
    write_csv(output_directory / "reliability_summary.csv", reliability_rows)
    write_csv(output_directory / "incident_summary.csv", incident_rows)
    write_csv(output_directory / "duration_summary.csv", duration_rows)
    write_json(output_directory / "statistical_tests.json", tests)
    write_json(
        output_directory / "comparison.json",
        {
            "validation": validation,
            "overall_metrics": overall_metrics,
            "scenario_metrics": scenario_metrics,
            "statistical_tests": tests,
        },
    )

    report = render_report(
        campaign_directory=campaign_directory,
        validation=validation,
        scenario_metrics=scenario_metrics,
        overall_metrics=overall_metrics,
        tests=tests,
    )
    (output_directory / "report.md").write_text(report, encoding="utf-8")
    generate_sha256sums(output_directory)

    print(f"Análise concluída: {output_directory}")
    print(f"Validação: {'OK' if validation['valid'] else 'COM AVISOS/ERROS'}")
    for row in overall_metrics:
        print(
            f"{row['version']} | executions={row['executions']} "
            f"| pass_rate={row.get('runner_pass.rate')} "
            f"| top1={row.get('top1_accuracy_fault_only.rate')} "
            f"| reliability={row.get('reliability_accuracy.rate')}"
        )

    return 0 if validation["valid"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError, FileNotFoundError, ValueError) as exception:
        print(f"ERRO: {exception}", file=sys.stderr)
        raise SystemExit(2)
