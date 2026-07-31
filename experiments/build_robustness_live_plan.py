#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPOSITORY_ROOT / "correlation-engine"
sys.path.insert(0, str(ENGINE_ROOT))

from robustness import ScenarioLayer, scenario_catalog  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gera o plano versionado da futura campanha live "
            "de robustez."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results/robustness/live-plan-v1.json"
        ),
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=10,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat deve ser maior que zero")

    catalog = scenario_catalog()
    live_specs = [
        spec.to_dict()
        for spec in catalog.values()
        if spec.layer == ScenarioLayer.LIVE_INFRASTRUCTURE
    ]

    payload = {
        "schema_version": "1.0",
        "plan_version": "causalods-robustness-live-plan-v1",
        "repeat_per_scenario": args.repeat,
        "execution_status": "not_executed",
        "execution_guard": (
            "Executar somente após encerrar, analisar e arquivar "
            "a campanha de sensibilidade."
        ),
        "scenarios": live_specs,
        "required_observations": [
            "trace_id",
            "rca_result_count",
            "incident_count",
            "top_service",
            "is_reliable",
            "data_loss_count",
            "restart_count_before",
            "restart_count_after",
            "recovery_time_ms",
            "processing_latency_ms",
            "provenance_valid",
        ],
        "global_invariants": [
            "at_most_one_rca_result_per_trace",
            "at_most_one_incident_per_trace",
            "no_silent_data_loss",
            "runtime_and_pipeline_provenance_preserved",
            "fault_restored_after_each_iteration",
        ],
    }

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n",
        encoding="utf-8",
    )

    print(f"Cenários live planejados: {len(live_specs)}")
    print(f"Execução: {payload['execution_status']}")
    print(f"Plano: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
