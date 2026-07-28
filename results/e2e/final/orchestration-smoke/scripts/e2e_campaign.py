from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIRECTORY = REPOSITORY_ROOT / "experiments"
RUNNER_PATH = EXPERIMENTS_DIRECTORY / "e2e_runner.py"
RESULTS_DIRECTORY = REPOSITORY_ROOT / "results" / "e2e"
FINAL_DIRECTORY = RESULTS_DIRECTORY / "final"
LOCK_PATH = RESULTS_DIRECTORY / ".e2e-campaign.lock"
CORRELATION_SERVICE = "correlation-engine"
EXPECTED_SCENARIOS = (
    "healthy",
    "payment_failure",
    "payment_latency",
    "order_failure",
    "order_latency",
)
DEFAULT_MODEL_PATH_V2 = "/app/training/models/xgboost_rca_v2.pkl"
DEFAULT_MANIFEST_PATH_V2 = (
    "/app/training/models/xgboost_rca_v2.manifest.json"
)
ACTIVE_CHILD: subprocess.Popen[str] | None = None


def handle_stop_signal(signum: int, _frame: Any) -> None:
    global ACTIVE_CHILD

    if ACTIVE_CHILD is not None and ACTIVE_CHILD.poll() is None:
        ACTIVE_CHILD.send_signal(signum)

    raise KeyboardInterrupt


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def timestamp_now() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_command(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )

    if check and completed.returncode != 0:
        raise RuntimeError(
            "Comando falhou:\n"
            f"{' '.join(arguments)}\n\n"
            f"STDOUT:\n{completed.stdout}\n\n"
            f"STDERR:\n{completed.stderr}"
        )

    return completed


def command_text(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    return run_command(
        arguments,
        environment=environment,
        check=check,
    ).stdout.strip()


def capture_command(
    path: Path,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> int:
    completed = run_command(
        arguments,
        environment=environment,
        check=False,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        (
            f"$ {' '.join(arguments)}\n"
            f"exit_code={completed.returncode}\n\n"
            f"STDOUT\n{completed.stdout}\n"
            f"STDERR\n{completed.stderr}\n"
        ),
        encoding="utf-8",
    )

    return completed.returncode


def active_runner_processes() -> list[str]:
    processes: list[str] = []
    current_pid = os.getpid()

    for command_line_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(command_line_path.parent.name)
        except ValueError:
            continue

        if pid == current_pid:
            continue

        try:
            raw = command_line_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue

        arguments = [
            part.decode("utf-8", errors="replace")
            for part in raw.split(b"\0")
            if part
        ]

        if not arguments:
            continue

        executable = Path(arguments[0]).name.lower()
        if not executable.startswith("python"):
            continue

        runner_argument = next(
            (
                argument
                for argument in arguments[1:]
                if argument.replace("\\", "/").endswith(
                    "/experiments/e2e_runner.py"
                )
                or argument == "experiments/e2e_runner.py"
            ),
            None,
        )

        if runner_argument is not None:
            processes.append(
                f"{pid} {' '.join(arguments)}"
            )

    return sorted(processes)


def acquire_campaign_lock() -> TextIO:
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("a+", encoding="utf-8")

    try:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError as exception:
        lock_file.close()
        raise RuntimeError(
            "Outra campanha E2E já está em execução. "
            f"Lock: {LOCK_PATH}"
        ) from exception

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(
        f"pid={os.getpid()}\nstarted_at={iso_now()}\n"
    )
    lock_file.flush()
    return lock_file


def validate_runner() -> None:
    if not RUNNER_PATH.is_file():
        raise FileNotFoundError(
            f"Runner não encontrado: {RUNNER_PATH}"
        )

    run_command(
        [
            sys.executable,
            "-m",
            "py_compile",
            str(RUNNER_PATH),
        ]
    )

    help_text = command_text(
        [
            sys.executable,
            str(RUNNER_PATH),
            "--help",
        ]
    )

    required_tokens = (
        "--expected-runtime-version",
        "order_failure",
        "order_latency",
    )

    missing = [
        token
        for token in required_tokens
        if token not in help_text
    ]

    if missing:
        raise RuntimeError(
            "O e2e_runner.py não contém os recursos "
            f"necessários para a campanha: {missing}."
        )


def version_environment(version: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["RCA_MODEL_VERSION"] = version
    environment.setdefault(
        "MODEL_PATH_V2",
        DEFAULT_MODEL_PATH_V2,
    )
    environment.setdefault(
        "MODEL_MANIFEST_PATH_V2",
        DEFAULT_MANIFEST_PATH_V2,
    )
    return environment


def wait_for_container_running(
    container_name: str,
    *,
    timeout_seconds: float = 90.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"

    while time.monotonic() < deadline:
        completed = run_command(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}}",
                container_name,
            ],
            check=False,
        )

        if completed.returncode == 0:
            last_state = completed.stdout.strip()
            if last_state == "running":
                return
        else:
            last_state = "not-found"

        time.sleep(1.0)

    raise TimeoutError(
        f"O container {container_name!r} não ficou running. "
        f"Último estado={last_state!r}."
    )


def read_runtime_version() -> str:
    value = command_text(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            CORRELATION_SERVICE,
            "printenv",
            "RCA_MODEL_VERSION",
        ]
    ).strip()

    if value not in {"v1", "v2"}:
        raise RuntimeError(
            "RCA_MODEL_VERSION inválida no correlation-engine: "
            f"{value!r}."
        )

    return value


def verify_v2_artifacts() -> None:
    run_command(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            CORRELATION_SERVICE,
            "sh",
            "-lc",
            (
                f"test -s {DEFAULT_MODEL_PATH_V2} && "
                f"test -s {DEFAULT_MANIFEST_PATH_V2}"
            ),
        ]
    )


def activate_runtime(
    version: str,
    *,
    settle_seconds: float,
    metadata_directory: Path,
    dry_run: bool,
) -> None:
    environment = version_environment(version)

    print(
        f"\nActivating RCA runtime | version={version}",
        flush=True,
    )

    if dry_run:
        print(
            "DRY-RUN: docker compose up -d --force-recreate "
            "--no-deps correlation-engine",
            flush=True,
        )
        return

    run_command(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            CORRELATION_SERVICE,
        ],
        environment=environment,
    )

    wait_for_container_running(CORRELATION_SERVICE)
    time.sleep(settle_seconds)
    wait_for_container_running(CORRELATION_SERVICE)

    configured = read_runtime_version()
    if configured != version:
        raise RuntimeError(
            "Runtime ativado não corresponde ao solicitado. "
            f"Esperado={version!r}; encontrado={configured!r}."
        )

    if version == "v2":
        verify_v2_artifacts()

    capture_command(
        metadata_directory / f"docker-compose.{version}.resolved.yml",
        ["docker", "compose", "config"],
        environment=environment,
    )
    capture_command(
        metadata_directory / f"correlation-engine.{version}.startup.log",
        [
            "docker",
            "compose",
            "logs",
            "--no-color",
            "--since=10m",
            CORRELATION_SERVICE,
        ],
        environment=environment,
    )

    print(
        f"RCA runtime active | version={configured}",
        flush=True,
    )


def stream_process(
    arguments: list[str],
    *,
    environment: dict[str, str],
    log_path: Path,
) -> int:
    global ACTIVE_CHILD
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"$ {' '.join(arguments)}\n\n")
        log_file.flush()

        process = subprocess.Popen(
            arguments,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        ACTIVE_CHILD = process

        assert process.stdout is not None

        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()

            return process.wait()

        except KeyboardInterrupt:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            raise

        finally:
            ACTIVE_CHILD = None


def result_files() -> set[Path]:
    return set(RESULTS_DIRECTORY.glob("e2e-*.json"))


def locate_new_result(
    before: set[Path],
) -> tuple[Path, Path]:
    after = result_files()
    new_json_files = sorted(
        after - before,
        key=lambda path: path.stat().st_mtime,
    )

    if not new_json_files:
        raise RuntimeError(
            "O runner terminou sem criar um novo arquivo JSON."
        )

    json_path = new_json_files[-1]
    csv_path = json_path.with_suffix(".csv")

    if not csv_path.is_file():
        raise RuntimeError(
            "O JSON foi criado, mas o CSV correspondente não existe: "
            f"{csv_path}"
        )

    return json_path, csv_path


def validate_result_payload(
    json_path: Path,
    *,
    version: str,
    repeat: int,
) -> dict[str, Any]:
    rows = json.loads(json_path.read_text(encoding="utf-8"))

    if not isinstance(rows, list):
        raise RuntimeError(
            f"Resultado inválido em {json_path}: raiz não é uma lista."
        )

    expected_count = repeat * len(EXPECTED_SCENARIOS)
    if len(rows) != expected_count:
        raise RuntimeError(
            "Quantidade de resultados incompleta. "
            f"Esperado={expected_count}; encontrado={len(rows)}."
        )

    scenario_counts = Counter(
        str(row.get("scenario"))
        for row in rows
    )
    expected_scenario_counts = {
        scenario: repeat
        for scenario in EXPECTED_SCENARIOS
    }

    if dict(scenario_counts) != expected_scenario_counts:
        raise RuntimeError(
            "Distribuição de cenários incorreta. "
            f"Esperado={expected_scenario_counts}; "
            f"encontrado={dict(scenario_counts)}."
        )

    expected_pipeline = f"causalods-rca-{version}"
    provenance_errors: list[str] = []

    for index, row in enumerate(rows):
        runtime = row.get("runtime_model_version")
        pipeline = row.get("pipeline_version")

        if runtime != version:
            provenance_errors.append(
                f"linha {index}: runtime={runtime!r}"
            )

        if pipeline != expected_pipeline:
            provenance_errors.append(
                f"linha {index}: pipeline={pipeline!r}"
            )

    if provenance_errors:
        raise RuntimeError(
            "Proveniência incompatível com a versão da campanha: "
            + "; ".join(provenance_errors[:10])
        )

    passed_count = sum(
        bool(row.get("passed"))
        for row in rows
    )
    failure_count = len(rows) - passed_count

    confidence_by_scenario: dict[str, list[float]] = {
        scenario: []
        for scenario in EXPECTED_SCENARIOS
    }

    for row in rows:
        confidence = row.get("confidence")
        if confidence is not None:
            confidence_by_scenario[str(row["scenario"])].append(
                float(confidence)
            )

    confidence_summary = {}
    for scenario, values in confidence_by_scenario.items():
        confidence_summary[scenario] = {
            "count": len(values),
            "mean": (
                sum(values) / len(values)
                if values
                else None
            ),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        }

    return {
        "version": version,
        "repeat": repeat,
        "expected_rows": expected_count,
        "actual_rows": len(rows),
        "passed_count": passed_count,
        "failed_count": failure_count,
        "scenario_counts": dict(scenario_counts),
        "confidence_by_scenario": confidence_summary,
        "completed_at": iso_now(),
    }


def copy_campaign_scripts(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    for source in (
        Path(__file__).resolve(),
        RUNNER_PATH,
    ):
        shutil.copy2(source, destination / source.name)


def collect_initial_metadata(metadata_directory: Path) -> None:
    metadata_directory.mkdir(parents=True, exist_ok=True)

    capture_command(
        metadata_directory / "git-commit.txt",
        ["git", "rev-parse", "HEAD"],
    )
    capture_command(
        metadata_directory / "git-status.txt",
        ["git", "status", "--short"],
    )
    capture_command(
        metadata_directory / "git-diff.patch",
        ["git", "diff", "--binary"],
    )
    capture_command(
        metadata_directory / "git-diff-cached.patch",
        ["git", "diff", "--cached", "--binary"],
    )
    capture_command(
        metadata_directory / "docker-version.txt",
        ["docker", "version"],
    )
    capture_command(
        metadata_directory / "docker-compose-version.txt",
        ["docker", "compose", "version"],
    )
    capture_command(
        metadata_directory / "docker-images.txt",
        ["docker", "compose", "images"],
    )
    capture_command(
        metadata_directory / "docker-ps.before.txt",
        ["docker", "compose", "ps"],
    )
    capture_command(
        metadata_directory / "python-version.txt",
        [sys.executable, "--version"],
    )
    capture_command(
        metadata_directory / "model-artifacts.sha256",
        [
            "sh",
            "-lc",
            (
                "find correlation-engine/training/models -maxdepth 1 "
                "-type f \\( "
                "-name 'xgboost_rca*.pkl' -o "
                "-name 'xgboost_rca*.json' -o "
                "-name 'xgboost_rca*.manifest.json' \\) "
                "-print0 | sort -z | xargs -0 -r sha256sum"
            ),
        ],
    )


def generate_sha256sums(root: Path) -> Path:
    checksum_path = root / "SHA256SUMS"
    lines: list[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path == checksum_path:
            continue

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root)
        lines.append(f"{digest}  {relative.as_posix()}")

    checksum_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return checksum_path


def phase_is_complete(
    version_directory: Path,
    *,
    version: str,
    repeat: int,
) -> bool:
    summary_path = version_directory / "summary.json"
    json_path = version_directory / f"all-repeat{repeat}.json"
    csv_path = version_directory / f"all-repeat{repeat}.csv"

    if not (
        summary_path.is_file()
        and json_path.is_file()
        and csv_path.is_file()
    ):
        return False

    try:
        summary = json.loads(
            summary_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return False

    return (
        summary.get("version") == version
        and summary.get("repeat") == repeat
        and summary.get("actual_rows")
        == repeat * len(EXPECTED_SCENARIOS)
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Orquestra a campanha E2E comparativa do CausalODS, "
            "executando versões RCA sequencialmente."
        )
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=50,
        help="Número de repetições de cada um dos cinco cenários.",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=("v1", "v2"),
        default=("v1", "v2"),
        help="Versões executadas, na ordem informada.",
    )
    parser.add_argument(
        "--campaign-id",
        default=None,
        help="Identificador opcional. O padrão usa timestamp UTC.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Retoma uma campanha existente e pula fases completas.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=10.0,
        help="Espera após recriar o correlation-engine.",
    )
    parser.add_argument(
        "--final-runtime",
        choices=("v1", "v2", "unchanged"),
        default="v2",
        help="Runtime deixado ativo ao final da campanha.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida e exibe o plano sem executar cenários.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    if arguments.repeat < 1:
        raise ValueError("--repeat deve ser maior que zero.")

    if arguments.settle_seconds < 0:
        raise ValueError("--settle-seconds não pode ser negativo.")

    validate_runner()

    active_processes = active_runner_processes()
    if active_processes:
        raise RuntimeError(
            "Existe um e2e_runner.py em execução. Aguarde a "
            "finalização antes de iniciar a campanha:\n"
            + "\n".join(active_processes)
        )

    lock_file = acquire_campaign_lock()

    signal.signal(signal.SIGTERM, handle_stop_signal)
    signal.signal(signal.SIGINT, handle_stop_signal)

    campaign_id = arguments.campaign_id or (
        f"campaign-{timestamp_now()}"
    )
    campaign_root = FINAL_DIRECTORY / campaign_id
    metadata_directory = campaign_root / "metadata"
    scripts_directory = campaign_root / "scripts"
    status_path = campaign_root / "status.json"

    if campaign_root.exists() and not arguments.resume:
        lock_file.close()
        raise FileExistsError(
            f"A campanha já existe: {campaign_root}. "
            "Use --resume para retomá-la."
        )

    campaign_root.mkdir(parents=True, exist_ok=True)
    copy_campaign_scripts(scripts_directory)

    status: dict[str, Any] = {
        "campaign_id": campaign_id,
        "state": "starting",
        "started_at": iso_now(),
        "finished_at": None,
        "repeat": arguments.repeat,
        "versions": list(arguments.versions),
        "current_version": None,
        "current_step": "preflight",
        "repository_root": str(REPOSITORY_ROOT),
        "campaign_root": str(campaign_root),
        "phases": {},
        "errors": [],
    }
    write_json(status_path, status)

    original_runtime: str | None = None
    infrastructure_failed = False

    try:
        if not arguments.dry_run:
            original_runtime = read_runtime_version()
            collect_initial_metadata(metadata_directory)

        status["state"] = "running"
        status["original_runtime"] = original_runtime
        write_json(status_path, status)

        for version in arguments.versions:
            version_directory = campaign_root / version
            version_directory.mkdir(parents=True, exist_ok=True)

            if (
                arguments.resume
                and phase_is_complete(
                    version_directory,
                    version=version,
                    repeat=arguments.repeat,
                )
            ):
                print(
                    f"Skipping completed phase | version={version}",
                    flush=True,
                )
                status["phases"][version] = {
                    "state": "skipped_completed",
                    "finished_at": iso_now(),
                }
                write_json(status_path, status)
                continue

            status["current_version"] = version
            status["current_step"] = "activate_runtime"
            status["phases"][version] = {
                "state": "activating",
                "started_at": iso_now(),
            }
            write_json(status_path, status)

            activate_runtime(
                version,
                settle_seconds=arguments.settle_seconds,
                metadata_directory=metadata_directory,
                dry_run=arguments.dry_run,
            )

            if arguments.dry_run:
                status["phases"][version] = {
                    "state": "dry_run",
                    "finished_at": iso_now(),
                }
                write_json(status_path, status)
                continue

            status["current_step"] = "run_e2e"
            status["phases"][version]["state"] = "running"
            write_json(status_path, status)

            before = result_files()
            runner_log = version_directory / "runner.log"
            environment = version_environment(version)
            runner_arguments = [
                sys.executable,
                str(RUNNER_PATH),
                "--scenario",
                "all",
                "--repeat",
                str(arguments.repeat),
                "--expected-runtime-version",
                version,
            ]

            return_code = stream_process(
                runner_arguments,
                environment=environment,
                log_path=runner_log,
            )

            generated_json, generated_csv = locate_new_result(before)
            summary = validate_result_payload(
                generated_json,
                version=version,
                repeat=arguments.repeat,
            )
            summary["runner_return_code"] = return_code
            summary["runner_completed"] = True
            summary["scenario_failures_present"] = (
                summary["failed_count"] > 0
            )

            destination_json = (
                version_directory
                / f"all-repeat{arguments.repeat}.json"
            )
            destination_csv = (
                version_directory
                / f"all-repeat{arguments.repeat}.csv"
            )
            shutil.copy2(generated_json, destination_json)
            shutil.copy2(generated_csv, destination_csv)

            write_json(
                version_directory / "summary.json",
                summary,
            )

            capture_command(
                version_directory / "docker-ps.after.txt",
                ["docker", "compose", "ps"],
                environment=environment,
            )
            capture_command(
                version_directory / "correlation-engine.after.log",
                [
                    "docker",
                    "compose",
                    "logs",
                    "--no-color",
                    "--since=30m",
                    CORRELATION_SERVICE,
                ],
                environment=environment,
            )

            phase_state = (
                "completed_with_scenario_failures"
                if summary["failed_count"] > 0
                else "completed"
            )
            status["phases"][version] = {
                **summary,
                "state": phase_state,
                "json": str(destination_json),
                "csv": str(destination_csv),
                "log": str(runner_log),
                "finished_at": iso_now(),
            }
            write_json(status_path, status)

            print(
                (
                    "Version campaign completed"
                    f" | version={version}"
                    f" | rows={summary['actual_rows']}"
                    f" | passed={summary['passed_count']}"
                    f" | failed={summary['failed_count']}"
                    f" | runner_exit={return_code}"
                ),
                flush=True,
            )

        status["state"] = (
            "dry_run_completed"
            if arguments.dry_run
            else "completed"
        )
        status["current_version"] = None
        status["current_step"] = "finalize"

    except KeyboardInterrupt:
        infrastructure_failed = True
        status["state"] = "interrupted"
        status["errors"].append(
            {
                "type": "KeyboardInterrupt",
                "message": "Campanha interrompida pelo usuário.",
                "at": iso_now(),
            }
        )
        raise

    except Exception as exception:
        infrastructure_failed = True
        status["state"] = "failed"
        status["errors"].append(
            {
                "type": type(exception).__name__,
                "message": str(exception),
                "at": iso_now(),
            }
        )
        print(
            f"Campaign infrastructure failure: {exception}",
            file=sys.stderr,
            flush=True,
        )

    finally:
        if (
            not arguments.dry_run
            and arguments.final_runtime != "unchanged"
        ):
            try:
                status["current_step"] = "restore_final_runtime"
                write_json(status_path, status)
                activate_runtime(
                    arguments.final_runtime,
                    settle_seconds=arguments.settle_seconds,
                    metadata_directory=metadata_directory,
                    dry_run=False,
                )
                status["final_runtime"] = read_runtime_version()
            except Exception as exception:
                infrastructure_failed = True
                status["errors"].append(
                    {
                        "type": type(exception).__name__,
                        "message": (
                            "Falha ao configurar runtime final: "
                            f"{exception}"
                        ),
                        "at": iso_now(),
                    }
                )
                status["state"] = "failed"

        if not arguments.dry_run:
            capture_command(
                metadata_directory / "docker-ps.after.txt",
                ["docker", "compose", "ps"],
            )

        status["finished_at"] = iso_now()
        status["current_version"] = None
        status["current_step"] = "finished"
        write_json(status_path, status)
        generate_sha256sums(campaign_root)
        lock_file.close()

    print()
    print("=" * 78)
    print(f"Campaign ID: {campaign_id}")
    print(f"State:       {status['state']}")
    print(f"Directory:   {campaign_root}")
    print(f"Status:      {status_path}")
    print("=" * 78)

    return 1 if infrastructure_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
