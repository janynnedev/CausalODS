from __future__ import annotations

import sys
from pathlib import Path

import pytest


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from robustness import (
    RobustnessScenario,
    ScenarioLayer,
    SpanFixture,
    build_payment_latency_trace,
    scenario_catalog,
)


def test_fixture_is_structurally_valid():
    fixture = build_payment_latency_trace()

    assert fixture.expected_root_cause == "payment-service"
    assert len(fixture.spans) == 5
    assert len(fixture.expected_span_ids) == 5
    assert {
        span.service_name
        for span in fixture.spans
    } == {
        "order-service",
        "payment-service",
    }


def test_span_rejects_negative_duration():
    with pytest.raises(ValueError):
        SpanFixture(
            trace_id="trace",
            span_id="span",
            parent_span_id=None,
            service_name="service",
            operation="operation",
            start_ns=2,
            end_ns=1,
        )


def test_catalog_contains_offline_and_live_layers():
    catalog = scenario_catalog()

    assert catalog[
        RobustnessScenario.DUPLICATE_SPAN
    ].layer == ScenarioLayer.OFFLINE_TRACE
    assert catalog[
        RobustnessScenario.KAFKA_BACKLOG
    ].layer == ScenarioLayer.LIVE_INFRASTRUCTURE
    assert catalog[
        RobustnessScenario.CORRELATION_ENGINE_RESTART
    ].expected_rca_result_max == 1
