from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path


EXPERIMENTS = Path(__file__).resolve().parents[1]
if str(EXPERIMENTS) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS))

from compare_e2e_results import (  # noqa: E402
    SCENARIOS,
    build_summaries,
    row_metrics,
    statistical_tests,
    validate_rows,
)


GROUND_TRUTH = {
    "healthy": None,
    "payment_failure": "payment-service",
    "payment_latency": "payment-service",
    "order_failure": "order-service",
    "order_latency": "order-service",
}


def build_row(version: str, scenario: str, iteration: int) -> dict:
    ground_truth = GROUND_TRUTH[scenario]
    is_healthy = scenario == "healthy"
    v1_latency_failure = version == "v1" and scenario.endswith("latency")
    reliable = False if is_healthy or v1_latency_failure else True
    incident = reliable and not is_healthy

    confidence = {
        "v1": {
            "healthy": 0.001,
            "payment_failure": 0.57,
            "payment_latency": 0.20,
            "order_failure": 0.60,
            "order_latency": 0.19,
        },
        "v2": {
            "healthy": 0.02,
            "payment_failure": 0.77,
            "payment_latency": 0.82,
            "order_failure": 0.80,
            "order_latency": 0.815,
        },
    }[version][scenario]

    http_status = 200
    if scenario == "payment_failure":
        http_status = 500
    elif scenario == "order_failure":
        http_status = 503

    return {
        "run_id": f"{version}-{scenario}-{iteration}",
        "scenario": scenario,
        "iteration": iteration,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:30+00:00",
        "duration_ms": 30000.0,
        "runtime_model_version": version,
        "model_version": f"xgboost-rca-{version}",
        "pipeline_version": f"causalods-rca-{version}",
        "fault_target": ground_truth,
        "fault_mode": "none" if is_healthy else ("latency" if scenario.endswith("latency") else "error"),
        "fault_rate": 0.0 if is_healthy else 1.0,
        "fault_delay_ms": 1500 if scenario.endswith("latency") else 0,
        "fault_seed": 42,
        "http_status": http_status,
        "request_duration_ms": 1600.0 if scenario.endswith("latency") else 80.0,
        "rca_count_before": 10,
        "rca_count_after": 11,
        "incident_count_before": 20,
        "incident_count_after": 20 + (1 if incident else 0),
        "rca_result_id": f"rca-{version}-{scenario}-{iteration}",
        "trace_id": f"trace-{version}-{scenario}-{iteration}",
        "top_service": ground_truth if ground_truth else "order-service",
        "confidence": confidence,
        "is_reliable": reliable,
        "incident_id": f"incident-{version}-{scenario}-{iteration}" if incident else None,
        "incident_root_cause": ground_truth if incident else None,
        "restart_count_before": 0,
        "restart_count_after": 0,
        "passed": not v1_latency_failure,
        "failures": ["minimum_confidence"] if v1_latency_failure else [],
    }


class CompareE2EResultsTest(unittest.TestCase):
    def test_validation_and_summary(self) -> None:
        v1 = [build_row("v1", scenario, 1) for scenario in SCENARIOS]
        v2 = [build_row("v2", scenario, 1) for scenario in SCENARIOS]

        self.assertTrue(validate_rows(v1, version="v1", expected_repeat=1)["valid"])
        self.assertTrue(validate_rows(v2, version="v2", expected_repeat=1)["valid"])

        metrics = [
            row_metrics(row, version)
            for version, rows in (("v1", v1), ("v2", v2))
            for row in rows
        ]
        _, overall = build_summaries(metrics)
        by_version = {row["version"]: row for row in overall}

        self.assertEqual(by_version["v1"]["runner_pass.successes"], 3)
        self.assertEqual(by_version["v2"]["runner_pass.successes"], 5)
        self.assertEqual(by_version["v1"]["top1_accuracy_fault_only.successes"], 4)
        self.assertEqual(by_version["v2"]["top1_accuracy_fault_only.successes"], 4)
        self.assertEqual(by_version["v1"]["fault_false_negatives"], 2)
        self.assertEqual(by_version["v2"]["fault_false_negatives"], 0)

    def test_statistical_pairing(self) -> None:
        metrics = [
            row_metrics(build_row(version, scenario, iteration), version)
            for version in ("v1", "v2")
            for iteration in (1, 2)
            for scenario in SCENARIOS
        ]
        tests = statistical_tests(metrics, bootstrap_samples=200, seed=42)

        self.assertTrue(tests["available"])
        self.assertEqual(tests["pairs"], 10)
        reliability = tests["overall"]["boolean_metrics"]["reliability_correct"]
        self.assertEqual(reliability["right_only_correct"], 4)
        self.assertEqual(reliability["left_only_correct"], 0)

    def test_cli_generates_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            (campaign / "v1").mkdir(parents=True)
            (campaign / "v2").mkdir(parents=True)

            status = {
                "campaign_id": "test",
                "state": "completed",
                "repeat": 1,
                "versions": ["v1", "v2"],
            }
            (campaign / "status.json").write_text(json.dumps(status), encoding="utf-8")

            for version in ("v1", "v2"):
                rows = [build_row(version, scenario, 1) for scenario in SCENARIOS]
                (campaign / version / "all-repeat1.json").write_text(
                    json.dumps(rows), encoding="utf-8"
                )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENTS / "compare_e2e_results.py"),
                    "--campaign-dir",
                    str(campaign),
                    "--bootstrap-samples",
                    "200",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            comparison = campaign / "comparison"
            for filename in (
                "report.md",
                "scenario_metrics.csv",
                "overall_metrics.csv",
                "statistical_tests.json",
                "provenance_validation.json",
                "SHA256SUMS",
            ):
                self.assertTrue((comparison / filename).is_file(), filename)


if __name__ == "__main__":
    unittest.main()
