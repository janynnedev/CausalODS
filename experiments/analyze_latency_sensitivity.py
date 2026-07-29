#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


VERSIONS = ("v1", "v2")
SERVICES = ("payment-service", "order-service")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def stdev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else 0.0 if values else None


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = probability * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total == 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = (
        z
        * math.sqrt(
            p * (1 - p) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def log_combination(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_probability(a: int, b: int, c: int, d: int) -> float:
    row1 = a + b
    row2 = c + d
    col1 = a + c
    total = row1 + row2
    log_p = (
        log_combination(col1, a)
        + log_combination(total - col1, row1 - a)
        - log_combination(total, row1)
    )
    return math.exp(log_p)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    row1 = a + b
    row2 = c + d
    col1 = a + c
    lower = max(0, row1 - (row1 + row2 - col1))
    upper = min(row1, col1)
    observed = hypergeom_probability(a, b, c, d)
    probability = 0.0
    for candidate_a in range(lower, upper + 1):
        candidate_b = row1 - candidate_a
        candidate_c = col1 - candidate_a
        candidate_d = row2 - candidate_c
        p = hypergeom_probability(
            candidate_a, candidate_b, candidate_c, candidate_d
        )
        if p <= observed + 1e-12:
            probability += p
    return min(1.0, probability)


def cliffs_delta(left: list[float], right: list[float]) -> float | None:
    if not left or not right:
        return None
    greater = 0
    lower = 0
    for x in left:
        for y in right:
            if x > y:
                greater += 1
            elif x < y:
                lower += 1
    return (greater - lower) / (len(left) * len(right))


def holm_adjust(p_values: list[float]) -> list[float]:
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, (index, value) in enumerate(indexed):
        candidate = min(1.0, (count - rank) * value)
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def load_rows(campaign_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = read_json(campaign_dir / "status.json")
    rows: list[dict[str, Any]] = []
    for version in status["configuration"]["versions"]:
        path = campaign_dir / version / "results.json"
        payload = read_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"Formato inválido: {path}")
        rows.extend(payload)
    return rows, status


def validate_rows(rows: list[dict[str, Any]], status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_per_version = status["configuration"]["expected_runs_per_version"]
    for version in status["configuration"]["versions"]:
        subset = [row for row in rows if row.get("runtime_model_version") == version]
        if len(subset) != expected_per_version:
            errors.append(
                f"{version}: {len(subset)} linhas; esperado={expected_per_version}"
            )
        if any(row.get("pipeline_version") != f"causalods-rca-{version}" for row in subset):
            errors.append(f"{version}: pipeline_version divergente")
        if any(row.get("runtime_model_version") != version for row in subset):
            errors.append(f"{version}: runtime divergente")
    keys = [
        (
            row.get("runtime_model_version"),
            row.get("condition_kind"),
            row.get("sensitivity_service"),
            row.get("configured_delay_ms"),
            row.get("iteration"),
        )
        for row in rows
    ]
    if len(keys) != len(set(keys)):
        errors.append("Existem condições duplicadas")
    return errors


def condition_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    valid = sum(bool(row.get("technical_valid")) for row in rows)
    detected = sum(bool(row.get("is_reliable")) for row in rows)
    incidents = sum(bool(row.get("incident_created")) for row in rows)
    passed = sum(bool(row.get("passed")) for row in rows)
    top1_values = [
        bool(row.get("top1_correct"))
        for row in rows
        if row.get("top1_correct") is not None
    ]
    confidence = [
        float(row["confidence"])
        for row in rows
        if row.get("confidence") is not None
    ]
    request_duration = [
        float(row["request_duration_ms"])
        for row in rows
        if row.get("request_duration_ms") is not None
    ]
    low, high = wilson_interval(detected, total)
    return {
        "n": total,
        "technical_valid_count": valid,
        "technical_valid_rate": valid / total if total else None,
        "detected_count": detected,
        "detection_rate": detected / total if total else None,
        "detection_ci95_low": low,
        "detection_ci95_high": high,
        "incident_count": incidents,
        "incident_rate": incidents / total if total else None,
        "passed_count": passed,
        "end_to_end_rate": passed / total if total else None,
        "top1_count": len(top1_values),
        "top1_accuracy": (
            sum(top1_values) / len(top1_values)
            if top1_values else None
        ),
        "confidence_mean": mean(confidence),
        "confidence_median": median(confidence),
        "confidence_stdev": stdev(confidence),
        "confidence_min": min(confidence) if confidence else None,
        "confidence_max": max(confidence) if confidence else None,
        "confidence_p95": percentile(confidence, 0.95),
        "request_duration_mean_ms": mean(request_duration),
        "request_duration_median_ms": median(request_duration),
        "request_duration_p95_ms": percentile(request_duration, 0.95),
    }


def build_condition_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("runtime_model_version"),
            row.get("condition_kind"),
            row.get("sensitivity_service"),
            int(row.get("configured_delay_ms") or 0),
        )
        grouped[key].append(row)

    output: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: tuple(str(part) for part in item)):
        version, kind, service, delay = key
        metrics = condition_metrics(grouped[key])
        output.append(
            {
                "version": version,
                "condition_kind": kind,
                "service": service,
                "delay_ms": delay,
                **metrics,
            }
        )
    return output


def threshold_table(condition_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thresholds = (0.50, 0.80, 0.95)
    output: list[dict[str, Any]] = []
    for version in VERSIONS:
        for service in SERVICES:
            rows = sorted(
                [
                    row for row in condition_rows
                    if row["version"] == version
                    and row["condition_kind"] == "latency"
                    and row["service"] == service
                ],
                key=lambda row: row["delay_ms"],
            )
            if not rows:
                continue
            rates = [float(row["detection_rate"]) for row in rows]
            monotonic_violations = sum(
                1 for left, right in zip(rates, rates[1:])
                if right + 1e-12 < left
            )
            record: dict[str, Any] = {
                "version": version,
                "service": service,
                "monotonic_violations": monotonic_violations,
            }
            for threshold in thresholds:
                delay = next(
                    (
                        row["delay_ms"]
                        for row in rows
                        if float(row["detection_rate"]) >= threshold
                    ),
                    None,
                )
                record[f"minimum_delay_for_{int(threshold * 100)}pct_detection_ms"] = delay
            output.append(record)
    return output


def version_comparisons(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("condition_kind") != "latency":
            continue
        grouped[
            (
                str(row.get("sensitivity_service")),
                int(row.get("configured_delay_ms")),
                str(row.get("runtime_model_version")),
            )
        ].append(row)

    output: list[dict[str, Any]] = []
    p_values: list[float] = []
    for service in SERVICES:
        delays = sorted(
            {
                delay
                for (candidate_service, delay, _version) in grouped
                if candidate_service == service
            }
        )
        for delay in delays:
            v1 = grouped.get((service, delay, "v1"), [])
            v2 = grouped.get((service, delay, "v2"), [])
            if not v1 or not v2:
                continue

            v1_detected = sum(bool(row.get("is_reliable")) for row in v1)
            v2_detected = sum(bool(row.get("is_reliable")) for row in v2)
            a = v2_detected
            b = len(v2) - v2_detected
            c = v1_detected
            d = len(v1) - v1_detected
            p_value = fisher_exact_two_sided(a, b, c, d)
            p_values.append(p_value)

            v1_rate = v1_detected / len(v1)
            v2_rate = v2_detected / len(v2)
            odds_ratio = ((a + 0.5) * (d + 0.5)) / ((b + 0.5) * (c + 0.5))
            v1_confidence = [
                float(row["confidence"])
                for row in v1 if row.get("confidence") is not None
            ]
            v2_confidence = [
                float(row["confidence"])
                for row in v2 if row.get("confidence") is not None
            ]
            output.append(
                {
                    "service": service,
                    "delay_ms": delay,
                    "v1_n": len(v1),
                    "v2_n": len(v2),
                    "v1_detection_rate": v1_rate,
                    "v2_detection_rate": v2_rate,
                    "risk_difference_v2_minus_v1": v2_rate - v1_rate,
                    "odds_ratio_haldane": odds_ratio,
                    "fisher_exact_p_value": p_value,
                    "confidence_cliffs_delta_v2_vs_v1": cliffs_delta(
                        v2_confidence, v1_confidence
                    ),
                }
            )

    adjusted = holm_adjust(p_values)
    for row, value in zip(output, adjusted):
        row["holm_adjusted_p_value"] = value
    return output


def healthy_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for version in VERSIONS:
        subset = [
            row for row in rows
            if row.get("runtime_model_version") == version
            and row.get("condition_kind") == "healthy"
        ]
        if not subset:
            continue
        false_positive = sum(
            bool(row.get("is_reliable")) or bool(row.get("incident_created"))
            for row in subset
        )
        metrics = condition_metrics(subset)
        output.append(
            {
                "version": version,
                "n": len(subset),
                "false_positive_count": false_positive,
                "false_positive_rate": false_positive / len(subset),
                "confidence_mean": metrics["confidence_mean"],
                "confidence_max": metrics["confidence_max"],
                "technical_valid_rate": metrics["technical_valid_rate"],
            }
        )
    return output


def markdown_report(
    status: dict[str, Any],
    condition_rows: list[dict[str, Any]],
    thresholds: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    healthy: list[dict[str, Any]],
    validation_errors: list[str],
) -> str:
    config = status["configuration"]
    lines = [
        "# CausalODS — Sensibilidade à Intensidade da Latência",
        "",
        "## Configuração",
        "",
        f"- Versões: {', '.join(config['versions'])}",
        f"- Serviços: {', '.join(config['services'])}",
        f"- Delays: {', '.join(str(value) for value in config['delays_ms'])} ms",
        f"- Repetições por condição: {config['repeat']}",
        f"- Controle saudável: {config['include_healthy_control']}",
        f"- Estado: {status['state']}",
        "",
        "## Validação",
        "",
    ]
    if validation_errors:
        lines.extend(f"- ERRO: {error}" for error in validation_errors)
    else:
        lines.append("- Integridade estrutural: OK")

    lines.extend(
        [
            "",
            "## Taxa de detecção por condição",
            "",
            "| Versão | Serviço | Delay (ms) | n | Detecção | Incidente | Top-1 | Confiança média |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in condition_rows:
        if row["condition_kind"] != "latency":
            continue
        lines.append(
            "| {version} | {service} | {delay_ms} | {n} | {detection_rate:.3f} "
            "| {incident_rate:.3f} | {top1_accuracy:.3f} | {confidence_mean:.4f} |".format(
                **row
            )
        )

    lines.extend(
        [
            "",
            "## Limiar empírico",
            "",
            "| Versão | Serviço | ≥50% | ≥80% | ≥95% | Violações monotônicas |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in thresholds:
        lines.append(
            f"| {row['version']} | {row['service']} | "
            f"{row['minimum_delay_for_50pct_detection_ms']} | "
            f"{row['minimum_delay_for_80pct_detection_ms']} | "
            f"{row['minimum_delay_for_95pct_detection_ms']} | "
            f"{row['monotonic_violations']} |"
        )

    lines.extend(
        [
            "",
            "## Controle saudável",
            "",
            "| Versão | n | Falso positivo | Confiança média | Confiança máxima |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in healthy:
        lines.append(
            f"| {row['version']} | {row['n']} | "
            f"{row['false_positive_rate']:.3f} | "
            f"{row['confidence_mean']:.4f} | "
            f"{row['confidence_max']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Comparação v2 × v1",
            "",
            "| Serviço | Delay (ms) | Detecção v1 | Detecção v2 | Diferença | p Fisher | p Holm | Cliff δ confiança |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparisons:
        lines.append(
            f"| {row['service']} | {row['delay_ms']} | "
            f"{row['v1_detection_rate']:.3f} | "
            f"{row['v2_detection_rate']:.3f} | "
            f"{row['risk_difference_v2_minus_v1']:.3f} | "
            f"{row['fisher_exact_p_value']:.6g} | "
            f"{row['holm_adjusted_p_value']:.6g} | "
            f"{row['confidence_cliffs_delta_v2_vs_v1']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Critérios de interpretação",
            "",
            "- A taxa de detecção é a proporção de resultados com `is_reliable=true`.",
            "- A taxa end-to-end também exige causa Top-1 e incidente corretos.",
            "- O limiar empírico é o menor delay que atinge a taxa definida.",
            "- O teste de Fisher compara proporções independentes de v1 e v2 em cada condição.",
            "- Holm-Bonferroni corrige as comparações múltiplas.",
            "- A campanha mede sensibilidade à magnitude; não mede ainda validade externa.",
            "",
        ]
    )
    return "\n".join(lines)


def sha256_manifest(root: Path) -> None:
    files = sorted(
        path for path in root.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analisa a campanha de sensibilidade à latência."
    )
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else campaign_dir / "analysis"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    rows, status = load_rows(campaign_dir)
    validation_errors = validate_rows(rows, status)
    condition_rows = build_condition_table(rows)
    thresholds = threshold_table(condition_rows)
    comparisons = version_comparisons(rows)
    healthy = healthy_summary(rows)

    write_csv(output_dir / "condition_metrics.csv", condition_rows)
    write_csv(output_dir / "empirical_thresholds.csv", thresholds)
    write_csv(output_dir / "version_comparisons.csv", comparisons)
    write_csv(output_dir / "healthy_controls.csv", healthy)
    write_csv(output_dir / "run_level_results.csv", rows)

    summary = {
        "schema_version": "1.0",
        "campaign_id": status["campaign_id"],
        "configuration": status["configuration"],
        "validation": {
            "valid": not validation_errors,
            "errors": validation_errors,
        },
        "condition_metrics": condition_rows,
        "empirical_thresholds": thresholds,
        "healthy_controls": healthy,
        "version_comparisons": comparisons,
    }
    atomic_write_json(output_dir / "analysis.json", summary)
    (output_dir / "report.md").write_text(
        markdown_report(
            status,
            condition_rows,
            thresholds,
            comparisons,
            healthy,
            validation_errors,
        ),
        encoding="utf-8",
    )
    sha256_manifest(output_dir)

    print(f"Análise concluída: {output_dir}")
    print(f"Validação: {'OK' if not validation_errors else 'FALHOU'}")
    print(f"Execuções: {len(rows)}")
    for row in thresholds:
        print(
            f"{row['version']} | {row['service']} | "
            f"delay>=95%: {row['minimum_delay_for_95pct_detection_ms']}"
        )
    return 0 if not validation_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
