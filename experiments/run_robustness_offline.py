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

from robustness import (  # noqa: E402
    RobustnessScenario,
    TraceLifecycleSimulator,
    build_offline_mutations,
    build_payment_latency_trace,
    validate_offline_observation,
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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "scenario",
        "valid",
        "received_envelopes",
        "accepted_unique_spans",
        "duplicate_spans_detected",
        "late_spans_rejected",
        "trace_finalization_count",
        "trace_completed",
        "trace_timed_out",
        "decision_allowed",
        "missing_span_count",
        "processing_latency_ms",
        "semantic_fingerprint",
        "issue_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# CausalODS — Robustez semântica de traces",
        "",
        f"- Fixture: {payload['fixture']['trace_id']}",
        f"- Timeout: {payload['configuration']['timeout_ms']} ms",
        f"- Cenários: {payload['scenario_count']}",
        f"- Aprovados: {payload['valid_count']}",
        f"- Reprovados: {payload['invalid_count']}",
        f"- Resultado geral: {'APROVADO' if payload['valid'] else 'REPROVADO'}",
        "",
        "## Resultados",
        "",
        "| Cenário | Válido | Únicos | Duplicados | Tardios | Completo | Timeout | Finalizações | Decisão permitida |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for item in payload["results"]:
        metrics = item["validation"]["metrics"]
        lines.append(
            f"| {item['scenario']} | "
            f"{item['validation']['valid']} | "
            f"{metrics['accepted_unique_spans']} | "
            f"{metrics['duplicate_spans_detected']} | "
            f"{metrics['late_spans_rejected']} | "
            f"{metrics['trace_completed']} | "
            f"{metrics['trace_timed_out']} | "
            f"{metrics['trace_finalization_count']} | "
            f"{metrics['decision_allowed']} |"
        )

    failures = [
        item
        for item in payload["results"]
        if not item["validation"]["valid"]
    ]
    if failures:
        lines.extend(["", "## Falhas", ""])
        for item in failures:
            lines.append(f"### {item['scenario']}")
            lines.append("")
            for issue in item["validation"]["issues"]:
                lines.append(
                    f"- `{issue['code']}`: {issue['message']} "
                    f"(esperado={issue['expected']}; atual={issue['actual']})"
                )
            lines.append("")

    lines.extend(
        [
            "",
            "## Interpretação",
            "",
            "- Duplicação e ordem de chegada não podem alterar a semântica do trace.",
            "- Trace incompleto ou finalizado por timeout não pode liberar decisão confiável.",
            "- Span tardio não pode reabrir o trace nem produzir segunda finalização.",
            "- Esta suíte valida contratos e invariantes offline; Kafka, OpenSearch e reinício serão exercitados na fase live.",
            "",
        ]
    )

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Executa a fundação offline da campanha de robustez "
            "do CausalODS."
        )
    )
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "results/robustness/offline-foundation-v1"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout_ms <= 0:
        raise ValueError("--timeout-ms deve ser positivo")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    fixture = build_payment_latency_trace()
    mutations = build_offline_mutations(
        fixture,
        timeout_ms=args.timeout_ms,
    )
    simulator = TraceLifecycleSimulator(
        timeout_ms=args.timeout_ms,
    )

    observations = {
        mutation.scenario: simulator.run(
            fixture,
            mutation,
        )
        for mutation in mutations
    }
    control_fingerprint = observations[
        RobustnessScenario.CONTROL
    ].semantic_fingerprint

    result_items = []
    csv_rows = []
    for mutation in mutations:
        observation = observations[mutation.scenario]
        validation = validate_offline_observation(
            observation,
            control_fingerprint=control_fingerprint,
            expected_root_cause=fixture.expected_root_cause,
        )
        result_items.append(
            {
                "scenario": mutation.scenario.value,
                "mutation": mutation.to_dict(),
                "observation": observation.to_dict(),
                "validation": validation.to_dict(),
            }
        )
        csv_rows.append(
            {
                "scenario": mutation.scenario.value,
                "valid": validation.valid,
                **validation.metrics,
                "issue_count": len(validation.issues),
            }
        )

    valid_count = sum(
        item["validation"]["valid"]
        for item in result_items
    )
    payload = {
        "schema_version": "1.0",
        "suite": "causalods-robustness-offline-v1",
        "configuration": {
            "timeout_ms": args.timeout_ms,
        },
        "fixture": fixture.to_dict(),
        "scenario_count": len(result_items),
        "valid_count": valid_count,
        "invalid_count": len(result_items) - valid_count,
        "valid": valid_count == len(result_items),
        "control_semantic_fingerprint": control_fingerprint,
        "results": result_items,
    }

    atomic_write_json(
        output_dir / "offline_robustness.json",
        payload,
    )
    write_csv(
        output_dir / "offline_robustness.csv",
        csv_rows,
    )
    write_report(
        output_dir / "report.md",
        payload,
    )
    write_sha256(output_dir)

    print(f"Cenários: {len(result_items)}")
    print(f"Aprovados: {valid_count}")
    print(f"Reprovados: {len(result_items) - valid_count}")
    print(f"Validação: {'OK' if payload['valid'] else 'FALHOU'}")
    print(f"Saída: {output_dir}")

    return 0 if payload["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
