#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import e2e_campaign as campaign_runtime
    import e2e_runner as base_runner
except ImportError as exc:
    raise SystemExit(
        "Não foi possível importar experiments/e2e_campaign.py e "
        "experiments/e2e_runner.py. Instale este script em experiments/ "
        "e execute-o a partir do repositório CausalODS."
    ) from exc


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPOSITORY_ROOT / "results" / "e2e" / "sensitivity"
LOCK_PATH = RESULTS_ROOT / ".latency-sensitivity.lock"
EXPECTED_VERSIONS = ("v1", "v2")
EXPECTED_SERVICES = ("payment-service", "order-service")
ACTIVE = True


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def timestamp_now() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_manifest(root: Path) -> None:
    paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root)}"
        for path in paths
    ]
    (root / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_capture(path: Path, arguments: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    path.write_text(
        "$ " + " ".join(arguments) + "\n\n"
        + completed.stdout
        + ("\n[STDERR]\n" + completed.stderr if completed.stderr else "")
        + f"\n[return_code={completed.returncode}]\n",
        encoding="utf-8",
    )


def capture_metadata(directory: Path, configuration: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / "configuration.json", configuration)
    run_capture(directory / "git-commit.txt", ["git", "rev-parse", "HEAD"])
    run_capture(directory / "git-branch.txt", ["git", "branch", "--show-current"])
    run_capture(directory / "git-status.txt", ["git", "status", "--short"])
    run_capture(directory / "docker-ps.txt", ["docker", "compose", "ps"])
    run_capture(directory / "docker-images.txt", ["docker", "compose", "images"])
    run_capture(directory / "docker-compose.resolved.yml", ["docker", "compose", "config"])


def handle_signal(signum: int, _frame: Any) -> None:
    global ACTIVE
    ACTIVE = False
    raise KeyboardInterrupt(f"Sinal recebido: {signum}")


def scenario_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("runtime_model_version")),
            str(row.get("condition_kind")),
            str(row.get("sensitivity_service")),
            str(row.get("configured_delay_ms")),
            str(row.get("iteration")),
        ]
    )


def build_conditions(
    *,
    delays: list[int],
    services: list[str],
    include_healthy: bool,
    fault_seed: int,
) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []

    if include_healthy:
        conditions.append(
            {
                "kind": "healthy",
                "service": None,
                "delay_ms": 0,
                "scenario": base_runner.Scenario(
                    name="healthy_sensitivity_control",
                    fault_target=None,
                    fault_mode="none",
                    fault_rate=0.0,
                    fault_delay_ms=0,
                    fault_seed=fault_seed,
                    expected_http_family=2,
                    expected_top_service=None,
                    expected_reliable=False,
                    expected_incident=False,
                ),
            }
        )

    for service in services:
        prefix = "payment" if service == "payment-service" else "order"
        for delay_ms in delays:
            minimum_duration = max(100.0, float(delay_ms) * 0.75)
            conditions.append(
                {
                    "kind": "latency",
                    "service": service,
                    "delay_ms": delay_ms,
                    "scenario": base_runner.Scenario(
                        name=f"{prefix}_latency_{delay_ms:04d}ms",
                        fault_target=service,
                        fault_mode="latency",
                        fault_rate=1.0,
                        fault_delay_ms=delay_ms,
                        fault_seed=fault_seed,
                        expected_http_family=2,
                        expected_top_service=service,
                        expected_reliable=True,
                        expected_incident=True,
                        expected_min_request_duration_ms=minimum_duration,
                    ),
                }
            )

    return conditions


def enrich_result(
    raw_result: Any,
    *,
    condition: dict[str, Any],
    matrix_order: int,
    randomization_seed: int,
) -> dict[str, Any]:
    row = asdict(raw_result)
    row.update(
        {
            "experiment": "latency_sensitivity",
            "condition_kind": condition["kind"],
            "sensitivity_service": condition["service"],
            "configured_delay_ms": condition["delay_ms"],
            "matrix_order": matrix_order,
            "randomization_seed": randomization_seed,
            "technical_valid": technical_valid(row, condition),
            "top1_correct": top1_correct(row, condition),
            "detected": bool(row.get("is_reliable")),
            "incident_created": incident_created(row),
        }
    )
    return row


def incident_created(row: dict[str, Any]) -> bool:
    before = row.get("incident_count_before")
    after = row.get("incident_count_after")
    if before is not None and after is not None:
        return int(after) > int(before)
    return bool(row.get("incident_id"))


def top1_correct(row: dict[str, Any], condition: dict[str, Any]) -> bool | None:
    expected = condition["service"]
    if expected is None:
        return None
    return row.get("top_service") == expected


def technical_valid(row: dict[str, Any], condition: dict[str, Any]) -> bool:
    if not row.get("trace_id") or not row.get("rca_result_id"):
        return False

    status = row.get("http_status")
    if status is None or int(status) // 100 != 2:
        return False

    before = row.get("rca_count_before")
    after = row.get("rca_count_after")
    if before is None or after is None or int(after) - int(before) < 1:
        return False

    restart_before = row.get("restart_count_before")
    restart_after = row.get("restart_count_after")
    if (
        restart_before is not None
        and restart_after is not None
        and int(restart_after) > int(restart_before)
    ):
        return False

    runtime_version = row.get("runtime_model_version")
    if runtime_version not in EXPECTED_VERSIONS:
        return False

    if row.get("pipeline_version") != f"causalods-rca-{runtime_version}":
        return False

    if row.get("model_version") != f"xgboost-rca-{runtime_version}":
        return False

    # Top-1, confiabilidade e incidente são variáveis de resultado.
    # Não devem invalidar tecnicamente uma execução válida.
    return True


def save_version_results(directory: Path, rows: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "results.json"
    csv_path = directory / "results.csv"
    atomic_write_json(json_path, rows)

    if not rows:
        csv_path.write_text("", encoding="utf-8")
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serializable = {
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                )
                for key, value in row.items()
            }
            writer.writerow(serializable)


def summary_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = sum(bool(row.get("technical_valid")) for row in rows)
    detected = sum(bool(row.get("detected")) for row in rows)
    passed = sum(bool(row.get("passed")) for row in rows)
    incidents = sum(bool(row.get("incident_created")) for row in rows)

    raw_counts = Counter(
        (
            row.get("condition_kind"),
            row.get("sensitivity_service"),
            row.get("configured_delay_ms"),
        )
        for row in rows
    )
    condition_counts = [
        {
            "condition_kind": kind,
            "service": service,
            "delay_ms": delay_ms,
            "count": count,
        }
        for (kind, service, delay_ms), count in sorted(
            raw_counts.items(),
            key=lambda item: tuple(str(value) for value in item[0]),
        )
    ]

    return {
        "rows": len(rows),
        "technical_valid": valid,
        "detected": detected,
        "incidents": incidents,
        "passed": passed,
        "failed": len(rows) - passed,
        "condition_counts": condition_counts,
    }


def runtime_warmup_scenario(version: str, fault_seed: int) -> Any:
    return base_runner.Scenario(
        name=f"runtime_warmup_{version}",
        fault_target=None,
        fault_mode="none",
        fault_rate=0.0,
        fault_delay_ms=0,
        fault_seed=fault_seed,
        expected_http_family=2,
        expected_top_service=None,
        expected_reliable=False,
        expected_incident=False,
    )


def run_runtime_warmup(
    *,
    phase_dir: Path,
    version: str,
    fault_seed: int,
) -> None:
    warmup_path = phase_dir / "warmup.json"

    if warmup_path.exists():
        payload = json.loads(warmup_path.read_text(encoding="utf-8"))
        if (
            isinstance(payload, dict)
            and payload.get("completed") is True
            and payload.get("runtime_model_version") == version
            and payload.get("trace_id")
            and payload.get("rca_result_id")
        ):
            print(f"Runtime warm-up already completed | version={version}")
            return

    print(f"Running runtime warm-up | version={version}")
    restore_faults()
    result = base_runner.run_scenario(
        runtime_warmup_scenario(version, fault_seed),
        0,
        version,
    )
    row = asdict(result)

    completed = bool(
        row.get("trace_id")
        and row.get("rca_result_id")
        and row.get("runtime_model_version") == version
        and row.get("pipeline_version") == f"causalods-rca-{version}"
        and row.get("http_status") is not None
        and int(row["http_status"]) // 100 == 2
    )

    payload = {
        "completed": completed,
        "purpose": "runtime_warmup_excluded_from_analysis",
        **row,
    }
    atomic_write_json(warmup_path, payload)

    if not completed:
        raise RuntimeError(
            f"Warm-up inválido para {version}; consulte {warmup_path}"
        )

    print(
        f"Runtime warm-up completed | version={version} "
        f"| trace_id={row.get('trace_id')}"
    )


def restore_faults() -> None:
    try:
        base_runner.configure_faults(
            target=None,
            mode="none",
            rate=0.0,
            delay_ms=0,
            seed=42,
        )
    except Exception as exc:
        print(f"AVISO: não foi possível restaurar faults: {exc}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Executa a campanha controlada de sensibilidade à intensidade "
            "da latência do CausalODS."
        )
    )
    parser.add_argument(
        "--campaign-id",
        default=f"latency-sensitivity-{timestamp_now()}",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=EXPECTED_VERSIONS,
        default=list(EXPECTED_VERSIONS),
    )
    parser.add_argument(
        "--services",
        nargs="+",
        choices=EXPECTED_SERVICES,
        default=list(EXPECTED_SERVICES),
    )
    parser.add_argument(
        "--delays",
        nargs="+",
        type=int,
        default=[250, 500, 750, 1000, 1500, 2000, 2500],
    )
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--random-seed", type=int, default=20260729)
    parser.add_argument("--fault-seed", type=int, default=42)
    parser.add_argument("--settle-seconds", type=float, default=12.0)
    parser.add_argument(
        "--final-runtime",
        choices=EXPECTED_VERSIONS,
        default="v2",
    )
    parser.add_argument(
        "--no-healthy-control",
        action="store_true",
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.repeat < 1:
        raise ValueError("--repeat deve ser maior que zero")
    if not args.delays:
        raise ValueError("Informe pelo menos um delay")
    if any(delay <= 0 for delay in args.delays):
        raise ValueError("Todos os delays devem ser maiores que zero")
    if len(set(args.delays)) != len(args.delays):
        raise ValueError("--delays contém valores duplicados")
    if len(set(args.versions)) != len(args.versions):
        raise ValueError("--versions contém valores duplicados")
    if len(set(args.services)) != len(args.services):
        raise ValueError("--services contém valores duplicados")


def main() -> int:
    args = parse_args()
    validate_args(args)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(
            f"Outra campanha de sensibilidade parece ativa: {LOCK_PATH}"
        ) from exc

    campaign_root = RESULTS_ROOT / args.campaign_id
    status_path = campaign_root / "status.json"

    configuration = {
        "schema_version": "1.0",
        "campaign_id": args.campaign_id,
        "versions": list(args.versions),
        "services": list(args.services),
        "delays_ms": sorted(args.delays),
        "repeat": args.repeat,
        "random_seed": args.random_seed,
        "fault_seed": args.fault_seed,
        "include_healthy_control": not args.no_healthy_control,
        "final_runtime": args.final_runtime,
        "settle_seconds": args.settle_seconds,
        "runtime_warmup_per_version": 1,
        "warmup_included_in_analysis": False,
        "expected_runs_per_version": (
            args.repeat
            * (
                len(args.services) * len(args.delays)
                + (0 if args.no_healthy_control else 1)
            )
        ),
    }

    if campaign_root.exists() and not args.resume:
        raise FileExistsError(
            f"A campanha já existe: {campaign_root}. Use --resume."
        )

    campaign_root.mkdir(parents=True, exist_ok=True)
    metadata_dir = campaign_root / "metadata"
    capture_metadata(metadata_dir, configuration)

    existing_status: dict[str, Any] = {}
    if status_path.exists():
        existing_status = json.loads(status_path.read_text(encoding="utf-8"))
        previous = existing_status.get("configuration")
        if previous and previous != configuration:
            raise RuntimeError(
                "A configuração informada difere da campanha existente; "
                "não é seguro retomar."
            )

    status: dict[str, Any] = {
        "campaign_id": args.campaign_id,
        "state": "running",
        "started_at": existing_status.get("started_at", iso_now()),
        "finished_at": None,
        "current_version": None,
        "current_condition": None,
        "configuration": configuration,
        "phases": existing_status.get("phases", {}),
        "errors": existing_status.get("errors", []),
        "original_runtime": None,
        "final_runtime": None,
    }
    atomic_write_json(status_path, status)

    original_runtime: str | None = None
    try:
        original_runtime = campaign_runtime.read_runtime_version()
        status["original_runtime"] = original_runtime
        atomic_write_json(status_path, status)

        all_conditions = build_conditions(
            delays=sorted(args.delays),
            services=list(args.services),
            include_healthy=not args.no_healthy_control,
            fault_seed=args.fault_seed,
        )

        for version_index, version in enumerate(args.versions):
            status["current_version"] = version
            atomic_write_json(status_path, status)

            phase_dir = campaign_root / version
            results_path = phase_dir / "results.json"
            rows: list[dict[str, Any]] = []
            if results_path.exists():
                payload = json.loads(results_path.read_text(encoding="utf-8"))
                if not isinstance(payload, list):
                    raise RuntimeError(f"Formato inválido: {results_path}")
                rows = payload

            completed_keys = {scenario_key(row) for row in rows}

            campaign_runtime.activate_runtime(
                version,
                settle_seconds=args.settle_seconds,
                metadata_directory=metadata_dir,
                dry_run=False,
            )

            run_runtime_warmup(
                phase_dir=phase_dir,
                version=version,
                fault_seed=args.fault_seed,
            )

            for iteration in range(1, args.repeat + 1):
                # A mesma seed por iteração produz a mesma ordem de condições
                # nas duas versões, reduzindo o viés de ordem entre v1 e v2.
                randomization_seed = args.random_seed + iteration
                ordered = list(all_conditions)
                random.Random(randomization_seed).shuffle(ordered)

                for matrix_order, condition in enumerate(ordered, start=1):
                    if not ACTIVE:
                        raise KeyboardInterrupt("Campanha interrompida")

                    candidate_key = "|".join(
                        [
                            version,
                            condition["kind"],
                            str(condition["service"]),
                            str(condition["delay_ms"]),
                            str(iteration),
                        ]
                    )
                    if candidate_key in completed_keys:
                        print(f"Resume: ignorando condição concluída {candidate_key}")
                        continue

                    status["current_condition"] = {
                        "version": version,
                        "iteration": iteration,
                        "kind": condition["kind"],
                        "service": condition["service"],
                        "delay_ms": condition["delay_ms"],
                        "matrix_order": matrix_order,
                    }
                    atomic_write_json(status_path, status)

                    result = base_runner.run_scenario(
                        condition["scenario"],
                        iteration,
                        version,
                    )
                    row = enrich_result(
                        result,
                        condition=condition,
                        matrix_order=matrix_order,
                        randomization_seed=randomization_seed,
                    )
                    rows.append(row)
                    completed_keys.add(scenario_key(row))
                    save_version_results(phase_dir, rows)

                    phase_summary = summary_for(rows)
                    phase_summary.update(
                        {
                            "state": "running",
                            "updated_at": iso_now(),
                            "expected_rows": configuration[
                                "expected_runs_per_version"
                            ],
                        }
                    )
                    status["phases"][version] = phase_summary
                    atomic_write_json(status_path, status)

            final_summary = summary_for(rows)
            final_summary.update(
                {
                    "state": "completed",
                    "updated_at": iso_now(),
                    "expected_rows": configuration["expected_runs_per_version"],
                }
            )
            if len(rows) != configuration["expected_runs_per_version"]:
                raise RuntimeError(
                    f"Quantidade incompleta em {version}: "
                    f"{len(rows)} != "
                    f"{configuration['expected_runs_per_version']}"
                )
            status["phases"][version] = final_summary
            atomic_write_json(status_path, status)
            restore_faults()

        status["state"] = "completed"

    except KeyboardInterrupt as exc:
        status["state"] = "interrupted"
        status["errors"].append(
            {"type": type(exc).__name__, "message": str(exc), "at": iso_now()}
        )
        raise

    except Exception as exc:
        status["state"] = "failed"
        status["errors"].append(
            {"type": type(exc).__name__, "message": str(exc), "at": iso_now()}
        )
        raise

    finally:
        restore_faults()
        try:
            campaign_runtime.activate_runtime(
                args.final_runtime,
                settle_seconds=args.settle_seconds,
                metadata_directory=metadata_dir,
                dry_run=False,
            )
            status["final_runtime"] = campaign_runtime.read_runtime_version()
        except Exception as exc:
            status["errors"].append(
                {
                    "type": "FinalRuntimeError",
                    "message": str(exc),
                    "at": iso_now(),
                }
            )
            if status["state"] == "completed":
                status["state"] = "failed"

        status["current_version"] = None
        status["current_condition"] = None
        status["finished_at"] = iso_now()
        atomic_write_json(status_path, status)
        write_sha256_manifest(campaign_root)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    print()
    print(f"Campaign: {campaign_root}")
    print(f"State: {status['state']}")
    for version, phase in status["phases"].items():
        print(
            f"{version}: rows={phase.get('rows')} "
            f"valid={phase.get('technical_valid')} "
            f"detected={phase.get('detected')} "
            f"passed={phase.get('passed')}"
        )

    return 0 if status["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
