#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from robustness_live.common import atomic_write_json, write_sha256_manifest


def safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("scenario"))].append(row)
    scenarios: list[dict[str, Any]] = []
    for scenario, items in sorted(grouped.items()):
        recovery = [float(item["recovery_time_ms"]) for item in items if item.get("recovery_time_ms") is not None]
        processing = [float(item["processing_latency_ms"]) for item in items if item.get("processing_latency_ms") is not None]
        validations = [bool((item.get("validation") or {}).get("valid")) for item in items]
        rca_duplicates = sum(int(((item.get("rca_summary") or {}).get("count") or 0) > 1) for item in items)
        incident_duplicates = sum(int(((item.get("incident_summary") or {}).get("count") or 0) > 1) for item in items)
        data_loss = sum(int(((item.get("observation") or {}).get("data_loss_count") or 0) > 0) for item in items)
        provenance_invalid = sum(int(((item.get("observation") or {}).get("provenance_valid") is False)) for item in items)
        scenarios.append({
            "scenario": scenario,
            "runs": len(items),
            "technical_valid": sum(bool(item.get("technical_valid")) for item in items),
            "invariant_valid": sum(validations),
            "invariant_pass_rate": sum(validations) / len(items),
            "rca_duplicate_runs": rca_duplicates,
            "incident_duplicate_runs": incident_duplicates,
            "data_loss_runs": data_loss,
            "provenance_invalid_runs": provenance_invalid,
            "mean_recovery_time_ms": safe_mean(recovery),
            "median_recovery_time_ms": safe_median(recovery),
            "mean_processing_latency_ms": safe_mean(processing),
            "median_processing_latency_ms": safe_median(processing),
        })
    return {
        "schema_version": "1.0",
        "runs": len(rows),
        "scenarios": scenarios,
        "overall": {
            "technical_valid": sum(bool(row.get("technical_valid")) for row in rows),
            "invariant_valid": sum(bool((row.get("validation") or {}).get("valid")) for row in rows),
            "all_restored": all(bool(row.get("restoration_ok")) for row in rows),
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# CausalODS — Robustez live",
        "",
        f"- Execuções: {summary['runs']}",
        f"- Válidas tecnicamente: {summary['overall']['technical_valid']}",
        f"- Invariantes aprovados: {summary['overall']['invariant_valid']}",
        f"- Restauração completa: {summary['overall']['all_restored']}",
        "",
        "| Cenário | Runs | Pass rate | RCA duplicado | Incidente duplicado | Perda | Proveniência inválida | Recuperação mediana (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["scenarios"]:
        median = item["median_recovery_time_ms"]
        lines.append(
            f"| {item['scenario']} | {item['runs']} | {item['invariant_pass_rate']:.3f} | "
            f"{item['rca_duplicate_runs']} | {item['incident_duplicate_runs']} | "
            f"{item['data_loss_runs']} | {item['provenance_invalid_runs']} | "
            f"{median if median is not None else 'n/a'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analisa uma campanha live de robustez.")
    parser.add_argument("--campaign-dir", type=Path, required=True)
    args = parser.parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    rows = json.loads((campaign_dir / "results.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("results.json deve conter uma lista")
    summary = summarize(rows)
    output = campaign_dir / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "analysis.json", summary)
    write_csv(output / "scenario_metrics.csv", summary["scenarios"])
    write_report(output / "report.md", summary)
    write_sha256_manifest(output)
    print(f"Análise: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
