#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import fcntl
import importlib
import inspect
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPOSITORY_ROOT / "correlation-engine"
EXPERIMENTS_ROOT = REPOSITORY_ROOT / "experiments"
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(EXPERIMENTS_ROOT))

from robustness import (  # noqa: E402
    LiveObservation,
    RobustnessScenario,
    validate_live_observation,
)
from robustness_live.common import (  # noqa: E402
    CORRELATION_CONTAINER,
    INCIDENT_INDEX,
    OPENSEARCH_CONTAINER,
    RCA_INDEX,
    atomic_write_json,
    iso_now,
    run_command,
    timestamp_now,
    utc_now,
    write_sha256_manifest,
)
from robustness_live.drivers import (  # noqa: E402
    CorrelationEngineRestartDriver,
    KafkaBacklogDriver,
    OpenSearchOutageDriver,
)
from robustness_live.probes import (  # noqa: E402
    IncidentProbe,
    KafkaLagProbe,
    ProvenanceProbe,
    RCAResultProbe,
    RecoveryProbe,
)

base_runner = importlib.import_module("e2e_runner")

RESULTS_ROOT = REPOSITORY_ROOT / "results" / "robustness" / "live"
LOCK_PATH = RESULTS_ROOT / ".robustness-live.lock"
SCENARIOS = {
    "kafka_backlog": RobustnessScenario.KAFKA_BACKLOG,
    "opensearch_unavailable": RobustnessScenario.OPENSEARCH_UNAVAILABLE,
    "correlation_engine_restart": RobustnessScenario.CORRELATION_ENGINE_RESTART,
}
EXPLICIT_PERSISTENCE_ERROR_TOKENS = (
    "opensearch",
    "connection refused",
    "connectionerror",
    "connection timeout",
    "read timed out",
    "failed to persist",
    "persistence",
    "requesterror",
)


@dataclass
class LiveRunResult:
    campaign_id: str
    scenario: str
    iteration: int
    run_id: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float | None = None
    trace_id: str | None = None
    product: str | None = None
    http_status: int | None = None
    request_duration_ms: float | None = None
    lag_before: int | None = None
    lag_during: int | None = None
    lag_after: int | None = None
    restart_count_before: int | None = None
    restart_count_after: int | None = None
    started_at_before: str | None = None
    started_at_after: str | None = None
    recovery_time_ms: float | None = None
    processing_latency_ms: float | None = None
    explicit_failure_observed: bool = False
    restoration_ok: bool = False
    technical_valid: bool = False
    observation: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    rca_summary: dict[str, Any] | None = None
    incident_summary: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    driver_metadata: dict[str, Any] | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def acquire_lock() -> TextIO:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"Outra campanha live está ativa: {LOCK_PATH}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"started_at={iso_now()}\n")
    handle.flush()
    return handle


def call_runner_scenario(name: str, iteration: int, runtime: str) -> dict[str, Any]:
    scenario = base_runner.SCENARIOS[name]
    signature = inspect.signature(base_runner.run_scenario)
    kwargs: dict[str, Any] = {"scenario": scenario, "iteration": iteration}
    if "runtime_model_version" in signature.parameters:
        kwargs["runtime_model_version"] = runtime
    result = base_runner.run_scenario(**kwargs)
    return asdict(result)


def warmup_healthy_environment(
    *,
    runtime: str,
    attempts: int,
    position: str,
) -> list[dict[str, Any]]:
    """Executa aquecimentos excluídos das métricas experimentais."""
    results: list[dict[str, Any]] = []

    if attempts < 1:
        return results

    print(
        "Healthy warm-up started "
        f"| position={position} "
        f"| attempts={attempts}",
        flush=True,
    )

    for warmup_iteration in range(1, attempts + 1):
        result = call_runner_scenario(
            "healthy",
            warmup_iteration,
            runtime,
        )
        result["position"] = position
        result["warmup_iteration"] = warmup_iteration
        result["included_in_metrics"] = False
        results.append(result)

        print(
            "Healthy warm-up completed "
            f"| position={position} "
            f"| iteration={warmup_iteration} "
            f"| passed={result.get('passed')} "
            f"| reliable={result.get('reliable')} "
            f"| confidence={result.get('confidence')}",
            flush=True,
        )

    KafkaLagProbe().wait_zero(timeout_seconds=180)
    base_runner.wait_for_index_quiet(
        index_name=RCA_INDEX,
        quiet_seconds=10.0,
        timeout_seconds=120.0,
    )

    print(
        "Healthy warm-up settled "
        f"| position={position}",
        flush=True,
    )

    return results


def ensure_runtime_v2() -> None:
    version = base_runner.read_rca_runtime_version()
    if version != "v2":
        raise RuntimeError(
            f"Campanha live exige runtime v2; encontrado={version!r}."
        )


def configure_payment_latency(delay_ms: int, seed: int) -> None:
    base_runner.configure_faults(
        target="payment-service",
        mode="latency",
        rate=1.0,
        delay_ms=delay_ms,
        seed=seed,
    )
    base_runner.wait_for_index_quiet(
        index_name=RCA_INDEX,
        quiet_seconds=7.0,
        timeout_seconds=45.0,
    )


def restore_faults() -> None:
    base_runner.configure_faults(
        target=None,
        mode="none",
        rate=0.0,
        delay_ms=0,
        seed=42,
    )


def send_order(scenario_name: str, run_id: str) -> tuple[int, Any, str, float, str]:
    product = f"e2e-{scenario_name}-{run_id}"
    request_started_at = iso_now()
    started = time.monotonic()
    status, payload = base_runner.http_request(
        method="POST",
        url=base_runner.ORDER_URL,
        payload={"product": product, "quantity": 2},
        allow_http_error=True,
        timeout_seconds=30.0,
    )
    duration_ms = (time.monotonic() - started) * 1000.0
    trace_id = base_runner.wait_for_order_trace_id(
        scenario_name=scenario_name,
        run_id=run_id,
        started_at=request_started_at,
        timeout_seconds=20.0,
    )
    return status, payload, trace_id, duration_ms, product


def logs_since(container: str, since: str) -> str:
    completed = run_command(
        ["docker", "logs", "--since", since, container],
        check=False,
        timeout=30,
    )
    return f"{completed.stdout}\n{completed.stderr}"


def persistence_failure_observed(logs: str) -> bool:
    normalized = logs.lower()
    return "opensearch" in normalized and any(
        token in normalized for token in EXPLICIT_PERSISTENCE_ERROR_TOKENS[1:]
    )


def wait_incidents(probe: IncidentProbe, trace_id: str, timeout_seconds: float = 15.0):
    return probe.wait(
        trace_id,
        timeout_seconds=timeout_seconds,
        minimum_count=1,
        allow_absent=True,
    )


def run_iteration(
    *,
    campaign_id: str,
    scenario: RobustnessScenario,
    iteration: int,
    delay_ms: int,
    fault_seed: int,
    hold_seconds: float,
    result_timeout: float,
    recovery_timeout: float,
    output_dir: Path,
) -> LiveRunResult:
    run_id = uuid.uuid4().hex[:12]
    started_dt = utc_now()
    monotonic_started = time.monotonic()
    row = LiveRunResult(
        campaign_id=campaign_id,
        scenario=scenario.value,
        iteration=iteration,
        run_id=run_id,
        started_at=started_dt.isoformat(),
    )
    lag_probe = KafkaLagProbe()
    rca_probe = RCAResultProbe()
    incident_probe = IncidentProbe()
    provenance_probe = ProvenanceProbe("v2")
    driver: Any = None
    recovery_started: float | None = None
    rca_observed_at: float | None = None
    logs_path = output_dir / "logs" / f"{scenario.value}-{iteration:02d}-correlation-engine.log"

    try:
        restore_faults()
        ensure_runtime_v2()
        RecoveryProbe.wait_container_running(CORRELATION_CONTAINER)
        RecoveryProbe.wait_container_running(OPENSEARCH_CONTAINER)
        RecoveryProbe.wait_opensearch()
        row.lag_before = lag_probe.wait_zero(timeout_seconds=90)
        configure_payment_latency(delay_ms, fault_seed)
        row.restart_count_before = RecoveryProbe.restart_count()
        row.started_at_before = RecoveryProbe.started_at()

        scenario_name = f"robustness-{scenario.value}"
        if scenario == RobustnessScenario.KAFKA_BACKLOG:
            driver = KafkaBacklogDriver()
            driver.inject()
            status, _, trace_id, request_ms, product = send_order(scenario_name, run_id)
            row.http_status, row.trace_id = status, trace_id
            row.request_duration_ms, row.product = request_ms, product
            row.lag_during = lag_probe.wait_at_least(1, timeout_seconds=30)
            recovery_started = time.monotonic()
            driver.restore()
            rca_hits = rca_probe.wait(trace_id, timeout_seconds=result_timeout)
            rca_observed_at = time.monotonic()
            row.lag_after = lag_probe.wait_zero(timeout_seconds=recovery_timeout)

        elif scenario == RobustnessScenario.OPENSEARCH_UNAVAILABLE:
            driver = OpenSearchOutageDriver()
            driver.inject()
            status, _, trace_id, request_ms, product = send_order(scenario_name, run_id)
            row.http_status, row.trace_id = status, trace_id
            row.request_duration_ms, row.product = request_ms, product
            time.sleep(hold_seconds)
            logs = logs_since(CORRELATION_CONTAINER, row.started_at)
            logs_path.parent.mkdir(parents=True, exist_ok=True)
            logs_path.write_text(logs, encoding="utf-8")
            row.explicit_failure_observed = persistence_failure_observed(logs)
            recovery_started = time.monotonic()
            driver.restore()
            rca_hits = rca_probe.wait(
                trace_id,
                timeout_seconds=result_timeout,
                allow_absent=True,
            )
            rca_observed_at = time.monotonic()
            row.lag_after = lag_probe.wait_zero(timeout_seconds=recovery_timeout)

        elif scenario == RobustnessScenario.CORRELATION_ENGINE_RESTART:
            driver = CorrelationEngineRestartDriver()
            driver.inject()
            status, _, trace_id, request_ms, product = send_order(scenario_name, run_id)
            row.http_status, row.trace_id = status, trace_id
            row.request_duration_ms, row.product = request_ms, product
            row.lag_during = lag_probe.wait_at_least(1, timeout_seconds=30)
            recovery_started = time.monotonic()
            row.started_at_after = driver.trigger_restart()
            rca_hits = rca_probe.wait(trace_id, timeout_seconds=result_timeout)
            rca_observed_at = time.monotonic()
            row.lag_after = lag_probe.wait_zero(timeout_seconds=recovery_timeout)

        else:
            raise ValueError(f"Cenário live não suportado: {scenario.value}")

        row.rca_summary = rca_probe.summarize(rca_hits)
        incident_hits = wait_incidents(incident_probe, row.trace_id)
        row.incident_summary = incident_probe.summarize(incident_hits)
        provenance_valid, provenance = provenance_probe.validate(rca_hits)
        row.provenance = provenance
        row.restart_count_after = RecoveryProbe.restart_count()
        if row.started_at_after is None:
            row.started_at_after = RecoveryProbe.started_at()
        if recovery_started is not None and rca_observed_at is not None:
            row.recovery_time_ms = (rca_observed_at - recovery_started) * 1000.0
        rca_count = int((row.rca_summary or {}).get("count") or 0)
        incident_count = int((row.incident_summary or {}).get("count") or 0)
        silent_loss = 0
        if rca_count == 0:
            if scenario == RobustnessScenario.OPENSEARCH_UNAVAILABLE and row.explicit_failure_observed:
                silent_loss = 0
                row.notes.append("RCA ausente com falha de persistência explícita; não classificado como perda silenciosa.")
            else:
                silent_loss = 1

        restart_observed: bool | None = None
        if scenario == RobustnessScenario.CORRELATION_ENGINE_RESTART:
            restart_observed = bool(
                row.started_at_before
                and row.started_at_after
                and row.started_at_before != row.started_at_after
            )

        row.processing_latency_ms = (time.monotonic() - monotonic_started) * 1000.0
        observation = LiveObservation(
            scenario=scenario,
            trace_id=row.trace_id,
            rca_result_count=rca_count,
            incident_count=incident_count,
            top_service=(row.rca_summary or {}).get("top_service"),
            is_reliable=(row.rca_summary or {}).get("is_reliable"),
            data_loss_count=silent_loss,
            restart_count_before=row.restart_count_before,
            restart_count_after=row.restart_count_after,
            recovery_time_ms=row.recovery_time_ms,
            processing_latency_ms=row.processing_latency_ms,
            provenance_valid=provenance_valid,
            metadata={
                "explicit_failure_observed": row.explicit_failure_observed,
                "lag_before": row.lag_before,
                "lag_during": row.lag_during,
                "lag_after": row.lag_after,
                "restart_observed": restart_observed,
                "started_at_before": row.started_at_before,
                "started_at_after": row.started_at_after,
            },
        )
        validation = validate_live_observation(observation)
        row.observation = observation.to_dict()
        row.validation = validation.to_dict()
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"
    finally:
        restoration_errors: list[str] = []
        if driver is not None:
            try:
                driver.restore()
            except Exception as exc:
                restoration_errors.append(f"driver: {type(exc).__name__}: {exc}")
        try:
            restore_faults()
        except Exception as exc:
            restoration_errors.append(f"faults: {type(exc).__name__}: {exc}")
        try:
            RecoveryProbe.wait_container_running(CORRELATION_CONTAINER, timeout_seconds=120)
            RecoveryProbe.wait_container_running(OPENSEARCH_CONTAINER, timeout_seconds=120)
            RecoveryProbe.wait_opensearch(timeout_seconds=120)
        except Exception as exc:
            restoration_errors.append(f"health: {type(exc).__name__}: {exc}")
        row.restoration_ok = not restoration_errors
        if restoration_errors:
            row.notes.extend(restoration_errors)
        row.driver_metadata = getattr(getattr(driver, "state", None), "metadata", None)
        finished_dt = utc_now()
        row.finished_at = finished_dt.isoformat()
        row.duration_ms = (finished_dt - started_dt).total_seconds() * 1000.0
        if row.processing_latency_ms is None:
            row.processing_latency_ms = (time.monotonic() - monotonic_started) * 1000.0
        if row.observation is not None:
            row.observation["processing_latency_ms"] = row.processing_latency_ms
        restart_evidence_valid = True
        if scenario == RobustnessScenario.CORRELATION_ENGINE_RESTART:
            restart_evidence_valid = bool(
                row.started_at_before
                and row.started_at_after
                and row.started_at_before != row.started_at_after
            )

        row.technical_valid = bool(
            row.trace_id
            and row.http_status is not None
            and row.http_status // 100 == 2
            and row.restoration_ok
            and row.error is None
            and restart_evidence_valid
        )
    return row


def save_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def capture_metadata(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    commands = {
        "docker-compose.resolved.yml": ["docker", "compose", "config"],
        "docker-ps.txt": ["docker", "compose", "ps"],
        "docker-images.txt": ["docker", "images", "--digests"],
        "git-commit.txt": ["git", "rev-parse", "HEAD"],
        "git-status.txt": ["git", "status", "--short"],
    }
    for name, arguments in commands.items():
        completed = run_command(arguments, check=False, timeout=60)
        (directory / name).write_text(
            f"$ {' '.join(arguments)}\n\n{completed.stdout}\n{completed.stderr}",
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Campanha live de robustez do CausalODS.")
    parser.add_argument("--campaign-id", default=f"robustness-live-{timestamp_now()}")
    parser.add_argument("--scenarios", nargs="+", choices=tuple(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--fault-delay-ms", type=int, default=1500)
    parser.add_argument("--fault-seed", type=int, default=42)
    parser.add_argument("--hold-seconds", type=float, default=8.0)
    parser.add_argument("--result-timeout", type=float, default=90.0)
    parser.add_argument("--recovery-timeout", type=float, default=120.0)
    parser.add_argument(
        "--warmup-attempts",
        type=int,
        default=2,
        help=(
            "Quantidade fixa de execuções healthy de aquecimento "
            "antes de cada controle medido."
        ),
    )
    parser.add_argument("--skip-controls", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat deve ser maior que zero")
    if args.warmup_attempts < 0:
        raise ValueError("--warmup-attempts não pode ser negativo")
    lock = acquire_lock()
    campaign_dir = RESULTS_ROOT / args.campaign_id
    if campaign_dir.exists():
        raise FileExistsError(f"Campanha já existe: {campaign_dir}")
    campaign_dir.mkdir(parents=True)
    capture_metadata(campaign_dir / "metadata" / "before")
    configuration = {
        "campaign_id": args.campaign_id,
        "runtime": "v2",
        "scenarios": args.scenarios,
        "repeat": args.repeat,
        "fault_delay_ms": args.fault_delay_ms,
        "fault_seed": args.fault_seed,
        "hold_seconds": args.hold_seconds,
        "result_timeout": args.result_timeout,
        "recovery_timeout": args.recovery_timeout,
        "warmup_attempts": args.warmup_attempts,
        "control_cadence": "before_each_iteration_and_after_scenario",
        "started_at": iso_now(),
    }
    atomic_write_json(campaign_dir / "configuration.json", configuration)
    status = {**configuration, "state": "running", "completed": 0, "total": len(args.scenarios) * args.repeat}
    atomic_write_json(campaign_dir / "status.json", status)

    rows: list[LiveRunResult] = []
    controls: list[dict[str, Any]] = []
    warmups: list[dict[str, Any]] = []
    try:
        ensure_runtime_v2()
        restore_faults()
        for scenario_name in args.scenarios:
            for iteration in range(1, args.repeat + 1):
                if not args.skip_controls:
                    position = (
                        f"before_{scenario_name}_iteration_{iteration}"
                    )
                    warmups.extend(
                        warmup_healthy_environment(
                            runtime="v2",
                            attempts=args.warmup_attempts,
                            position=position,
                        )
                    )
                    control = call_runner_scenario(
                        "healthy",
                        0,
                        "v2",
                    )
                    control["position"] = position
                    control["iteration"] = iteration
                    controls.append(control)
                    if not control.get("passed"):
                        raise RuntimeError(
                            "Controle saudável falhou antes de "
                            f"{scenario_name}/{iteration}"
                        )

                row = run_iteration(
                    campaign_id=args.campaign_id,
                    scenario=SCENARIOS[scenario_name],
                    iteration=iteration,
                    delay_ms=args.fault_delay_ms,
                    fault_seed=args.fault_seed,
                    hold_seconds=args.hold_seconds,
                    result_timeout=args.result_timeout,
                    recovery_timeout=args.recovery_timeout,
                    output_dir=campaign_dir,
                )
                rows.append(row)
                atomic_write_json(campaign_dir / "results.json", [asdict(item) for item in rows])
                status["completed"] = len(rows)
                status["current_scenario"] = scenario_name
                status["current_iteration"] = iteration
                atomic_write_json(campaign_dir / "status.json", status)
                if not row.restoration_ok:
                    raise RuntimeError(f"Restauração falhou em {scenario_name}/{iteration}")
                validation_valid = bool((row.validation or {}).get("valid"))
                if not validation_valid:
                    print(f"AVISO: invariantes falharam em {scenario_name}/{iteration}", flush=True)
            if not args.skip_controls:
                position = f"after_{scenario_name}"
                warmups.extend(
                    warmup_healthy_environment(
                        runtime="v2",
                        attempts=args.warmup_attempts,
                        position=position,
                    )
                )
                control = call_runner_scenario(
                    "healthy",
                    0,
                    "v2",
                )
                control["position"] = position
                controls.append(control)
                if not control.get("passed"):
                    raise RuntimeError(
                        f"Controle saudável falhou após {scenario_name}"
                    )
        status["state"] = "completed"
        status["finished_at"] = iso_now()
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["finished_at"] = iso_now()
        raise
    finally:
        atomic_write_json(campaign_dir / "status.json", status)
        atomic_write_json(campaign_dir / "results.json", [asdict(item) for item in rows])
        atomic_write_json(campaign_dir / "controls.json", controls)
        atomic_write_json(campaign_dir / "warmups.json", warmups)
        save_csv(campaign_dir / "results.csv", [asdict(item) for item in rows])
        capture_metadata(campaign_dir / "metadata" / "after")
        write_sha256_manifest(campaign_dir)
        lock.close()

    valid = sum(bool((row.validation or {}).get("valid")) for row in rows)
    print(f"Campanha: {campaign_dir}")
    print(f"Execuções: {len(rows)} | invariantes válidos: {valid}")
    return 0 if valid == len(rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
