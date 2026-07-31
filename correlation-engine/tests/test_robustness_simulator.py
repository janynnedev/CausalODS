from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from robustness import (
    RobustnessScenario,
    TraceLifecycleSimulator,
    build_offline_mutations,
    build_payment_latency_trace,
    validate_offline_observation,
)


def observations(timeout_ms: int = 5_000):
    fixture = build_payment_latency_trace()
    simulator = TraceLifecycleSimulator(
        timeout_ms=timeout_ms,
    )
    mutations = build_offline_mutations(
        fixture,
        timeout_ms=timeout_ms,
    )
    values = {
        mutation.scenario: simulator.run(
            fixture,
            mutation,
        )
        for mutation in mutations
    }
    return fixture, values


def test_duplicate_and_out_of_order_preserve_semantics():
    fixture, values = observations()
    control = values[RobustnessScenario.CONTROL]

    duplicate = values[RobustnessScenario.DUPLICATE_SPAN]
    out_of_order = values[
        RobustnessScenario.OUT_OF_ORDER_SPANS
    ]

    assert duplicate.duplicate_spans_detected == 1
    assert duplicate.accepted_unique_spans == len(
        fixture.expected_span_ids
    )
    assert duplicate.semantic_fingerprint == (
        control.semantic_fingerprint
    )

    assert out_of_order.trace_completed is True
    assert out_of_order.semantic_fingerprint == (
        control.semantic_fingerprint
    )


def test_incomplete_trace_times_out_and_blocks_decision():
    _, values = observations()
    incomplete = values[
        RobustnessScenario.INCOMPLETE_TRACE
    ]

    assert incomplete.trace_completed is False
    assert incomplete.trace_timed_out is True
    assert incomplete.decision_allowed is False
    assert incomplete.missing_span_ids
    assert incomplete.trace_finalization_count == 1


def test_delayed_span_before_timeout_is_incorporated():
    _, values = observations()
    delayed = values[
        RobustnessScenario.DELAYED_SPAN_BEFORE_TIMEOUT
    ]

    assert delayed.trace_completed is True
    assert delayed.trace_timed_out is False
    assert delayed.late_spans_rejected == 0
    assert delayed.decision_allowed is True


def test_delayed_span_after_timeout_does_not_reopen_trace():
    _, values = observations()
    delayed = values[
        RobustnessScenario.DELAYED_SPAN_AFTER_TIMEOUT
    ]

    assert delayed.trace_completed is False
    assert delayed.trace_timed_out is True
    assert delayed.late_spans_rejected == 1
    assert delayed.trace_finalization_count == 1
    assert delayed.decision_allowed is False


def test_all_offline_scenarios_pass_oracle():
    fixture, values = observations()
    control_fingerprint = values[
        RobustnessScenario.CONTROL
    ].semantic_fingerprint

    reports = [
        validate_offline_observation(
            observation,
            control_fingerprint=control_fingerprint,
            expected_root_cause=fixture.expected_root_cause,
        )
        for observation in values.values()
    ]

    assert all(report.valid for report in reports)
