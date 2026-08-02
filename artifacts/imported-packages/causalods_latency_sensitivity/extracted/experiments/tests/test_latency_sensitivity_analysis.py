from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "analyze_latency_sensitivity.py"
)
SPEC = importlib.util.spec_from_file_location(
    "analyze_latency_sensitivity",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def row(
    version: str,
    service: str | None,
    delay: int,
    detected: bool,
    confidence: float,
    *,
    top1_correct: bool | None = True,
    condition_kind: str = "latency",
    passed: bool | None = None,
):
    if passed is None:
        passed = (
            detected
            if condition_kind == "latency"
            else not detected
        )

    return {
        "runtime_model_version": version,
        "condition_kind": condition_kind,
        "sensitivity_service": service,
        "configured_delay_ms": delay,
        "technical_valid": True,
        "is_reliable": detected,
        "incident_created": detected,
        "passed": passed,
        "top1_correct": top1_correct,
        "confidence": confidence,
        "request_duration_ms": delay + 200.0,
    }

def test_top1_metrics_with_nine_correct_results():
    rows = [
        row(
            "v1",
            "payment-service",
            500,
            detected=False,
            confidence=0.15,
            top1_correct=index < 9,
        )
        for index in range(10)
    ]

    condition_rows = module.build_condition_table(rows)

    assert len(condition_rows) == 1

    result = condition_rows[0]

    assert result["top1_evaluable_count"] == 10
    assert result["top1_correct_count"] == 9
    assert result["top1_accuracy"] == 0.9


def test_top1_metrics_with_zero_correct_results():
    rows = [
        row(
            "v1",
            "payment-service",
            250,
            detected=False,
            confidence=0.001,
            top1_correct=False,
        )
        for _ in range(10)
    ]

    condition_rows = module.build_condition_table(rows)

    assert len(condition_rows) == 1

    result = condition_rows[0]

    assert result["top1_evaluable_count"] == 10
    assert result["top1_correct_count"] == 0
    assert result["top1_accuracy"] == 0.0


def test_top1_metrics_are_not_applicable_to_healthy_control():
    rows = [
        row(
            "v2",
            None,
            0,
            detected=False,
            confidence=0.01,
            top1_correct=None,
            condition_kind="healthy",
            passed=True,
        )
        for _ in range(10)
    ]

    condition_rows = module.build_condition_table(rows)

    assert len(condition_rows) == 1

    result = condition_rows[0]

    assert result["top1_evaluable_count"] == 0
    assert result["top1_correct_count"] == 0
    assert result["top1_accuracy"] is None


def test_threshold_is_first_delay_reaching_rate():
    rows = []
    for delay, detections in ((250, 0), (500, 5), (750, 10)):
        for index in range(10):
            rows.append(
                row(
                    "v2",
                    "payment-service",
                    delay,
                    index < detections,
                    0.1 + delay / 10000,
                )
            )

    condition_rows = module.build_condition_table(rows)
    thresholds = module.threshold_table(condition_rows)

    assert len(thresholds) == 1
    result = thresholds[0]
    assert result["minimum_delay_for_50pct_detection_ms"] == 500
    assert result["minimum_delay_for_80pct_detection_ms"] == 750
    assert result["minimum_delay_for_95pct_detection_ms"] == 750


def test_fisher_detects_extreme_difference():
    p_value = module.fisher_exact_two_sided(
        10, 0,
        0, 10,
    )
    assert p_value < 0.001


def test_holm_adjustment_is_monotonic_in_sorted_order():
    adjusted = module.holm_adjust([0.01, 0.03, 0.02])
    assert all(0.0 <= value <= 1.0 for value in adjusted)
    assert adjusted[0] <= adjusted[2] <= adjusted[1]


def test_cliffs_delta_extremes():
    assert module.cliffs_delta([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert module.cliffs_delta([1.0, 2.0], [3.0, 4.0]) == -1.0
