from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS))

from robustness_live.drivers import KafkaBacklogDriver, OpenSearchOutageDriver


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
