from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS))

from robustness_live.drivers import (
    CorrelationEngineRestartDriver,
    KafkaBacklogDriver,
    OpenSearchOutageDriver,
)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")


def test_kafka_backlog_driver_uses_pause_and_unpause():
    runner = FakeRunner()
    driver = KafkaBacklogDriver(runner)
    with patch("robustness_live.drivers.RecoveryProbe.wait_container_running"):
        driver.inject()
        driver.restore()
    assert runner.calls[0] == ["docker", "pause", "correlation-engine"]
    assert runner.calls[1] == ["docker", "unpause", "correlation-engine"]


def test_opensearch_driver_uses_pause_and_unpause():
    runner = FakeRunner()
    driver = OpenSearchOutageDriver(runner)
    with patch("robustness_live.drivers.RecoveryProbe.wait_container_running"), patch(
        "robustness_live.drivers.RecoveryProbe.wait_opensearch"
    ):
        driver.inject()
        driver.restore()
    assert runner.calls[0] == ["docker", "pause", "causalods-opensearch"]
    assert runner.calls[1] == ["docker", "unpause", "causalods-opensearch"]


def test_restart_driver_kills_and_starts_container():
    runner = FakeRunner()
    driver = CorrelationEngineRestartDriver(runner)

    with (
        patch(
            "robustness_live.drivers.RecoveryProbe.started_at",
            return_value="2026-08-01T10:00:00Z",
        ),
        patch(
            "robustness_live.drivers.RecoveryProbe.wait_container_running"
        ),
        patch(
            "robustness_live.drivers.RecoveryProbe.wait_started_at_changed",
            return_value="2026-08-01T10:00:10Z",
        ),
    ):
        driver.inject()
        started_at_after = driver.trigger_restart()

    assert runner.calls[:3] == [
        ["docker", "pause", "correlation-engine"],
        [
            "docker",
            "kill",
            "--signal",
            "KILL",
            "correlation-engine",
        ],
        ["docker", "start", "correlation-engine"],
    ]
    assert started_at_after == "2026-08-01T10:00:10Z"
    assert driver.state.metadata == {
        "mechanism": "pause_then_sigkill_then_explicit_start",
        "container": "correlation-engine",
        "started_at_before": "2026-08-01T10:00:00Z",
        "started_at_after": "2026-08-01T10:00:10Z",
        "restart_observed": True,
    }
