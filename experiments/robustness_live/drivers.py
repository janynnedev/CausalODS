from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from robustness_live.common import (
    CORRELATION_CONTAINER,
    OPENSEARCH_CONTAINER,
    run_command,
)
from robustness_live.probes import RecoveryProbe


@dataclass
class DriverState:
    injected: bool = False
    restored: bool = False
    metadata: dict[str, Any] | None = None


class KafkaBacklogDriver:
    def __init__(self, command_runner: Callable[..., Any] = run_command) -> None:
        self.command_runner = command_runner
        self.state = DriverState(metadata={})

    def inject(self) -> None:
        self.command_runner(["docker", "pause", CORRELATION_CONTAINER], timeout=30)
        self.state.injected = True
        self.state.metadata = {"mechanism": "docker_pause", "container": CORRELATION_CONTAINER}

    def restore(self) -> None:
        completed = self.command_runner(
            ["docker", "unpause", CORRELATION_CONTAINER],
            check=False,
            timeout=30,
        )
        if completed.returncode != 0 and "is not paused" not in completed.stderr:
            self.command_runner(
                ["docker", "compose", "up", "-d", "--no-deps", CORRELATION_CONTAINER],
                timeout=90,
            )
        RecoveryProbe.wait_container_running(CORRELATION_CONTAINER)
        self.state.restored = True


class OpenSearchOutageDriver:
    def __init__(self, command_runner: Callable[..., Any] = run_command) -> None:
        self.command_runner = command_runner
        self.state = DriverState(metadata={})

    def inject(self) -> None:
        self.command_runner(["docker", "pause", OPENSEARCH_CONTAINER], timeout=30)
        self.state.injected = True
        self.state.metadata = {"mechanism": "docker_pause", "container": OPENSEARCH_CONTAINER}

    def restore(self) -> None:
        completed = self.command_runner(
            ["docker", "unpause", OPENSEARCH_CONTAINER],
            check=False,
            timeout=30,
        )
        if completed.returncode != 0 and "is not paused" not in completed.stderr:
            self.command_runner(
                ["docker", "compose", "up", "-d", "--no-deps", "opensearch"],
                timeout=120,
            )
        RecoveryProbe.wait_container_running(OPENSEARCH_CONTAINER, timeout_seconds=120)
        RecoveryProbe.wait_opensearch(timeout_seconds=120)
        self.state.restored = True


class CorrelationEngineRestartDriver:
    def __init__(self, command_runner: Callable[..., Any] = run_command) -> None:
        self.command_runner = command_runner
        self.state = DriverState(metadata={})
        self._started_at_before: str | None = None

    def inject(self) -> None:
        # Pausa primeiro para garantir trabalho pendente no Kafka.
        self._started_at_before = RecoveryProbe.started_at(CORRELATION_CONTAINER)
        self.command_runner(["docker", "pause", CORRELATION_CONTAINER], timeout=30)
        self.state.injected = True
        self.state.metadata = {
            "mechanism": "pause_then_sigkill",
            "container": CORRELATION_CONTAINER,
            "started_at_before": self._started_at_before,
        }

    def trigger_restart(self) -> str:
        if not self.state.injected or self._started_at_before is None:
            raise RuntimeError("Restart driver não foi injetado.")
        self.command_runner(
            ["docker", "kill", "--signal", "KILL", CORRELATION_CONTAINER],
            timeout=30,
        )
        started_at_after = RecoveryProbe.wait_started_at_changed(
            self._started_at_before,
            container_name=CORRELATION_CONTAINER,
            timeout_seconds=120,
        )
        assert self.state.metadata is not None
        self.state.metadata["started_at_after"] = started_at_after
        return started_at_after

    def restore(self) -> None:
        state = self.command_runner(
            ["docker", "inspect", "-f", "{{.State.Status}}", CORRELATION_CONTAINER],
            check=False,
            timeout=15,
        )
        if state.returncode != 0 or state.stdout.strip() != "running":
            self.command_runner(
                ["docker", "compose", "up", "-d", "--no-deps", CORRELATION_CONTAINER],
                timeout=120,
            )
        RecoveryProbe.wait_container_running(CORRELATION_CONTAINER, timeout_seconds=120)
        self.state.restored = True
