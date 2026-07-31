from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from robustness import RobustnessScenario, build_payment_latency_trace
from robustness.mutations import (
    delayed_span_after_timeout,
    delayed_span_before_timeout,
    duplicate_span,
    incomplete_trace,
    out_of_order_spans,
)


def test_duplicate_mutation_reuses_span_id():
    fixture = build_payment_latency_trace()
    mutation = duplicate_span(fixture)

    ids = [
        item.span.span_id
        for item in mutation.envelopes
    ]

    assert mutation.scenario == RobustnessScenario.DUPLICATE_SPAN
    assert len(ids) == len(fixture.spans) + 1
    assert len(set(ids)) == len(fixture.spans)
    assert mutation.introduced_duplicates == 1


def test_out_of_order_preserves_span_set():
    fixture = build_payment_latency_trace()
    mutation = out_of_order_spans(fixture)

    assert mutation.reordered is True
    assert {
        item.span.span_id
        for item in mutation.envelopes
    } == set(fixture.expected_span_ids)


def test_incomplete_trace_drops_expected_span():
    fixture = build_payment_latency_trace()
    mutation = incomplete_trace(fixture)

    assert "span-payment-server" in mutation.dropped_span_ids
    assert "span-payment-server" not in {
        item.span.span_id
        for item in mutation.envelopes
    }


def test_delayed_mutations_straddle_timeout():
    fixture = build_payment_latency_trace()
    timeout_ms = 5_000

    before = delayed_span_before_timeout(
        fixture,
        timeout_ms=timeout_ms,
    )
    after = delayed_span_after_timeout(
        fixture,
        timeout_ms=timeout_ms,
    )

    before_arrival = next(
        item.arrival_offset_ms
        for item in before.envelopes
        if item.span.span_id == "span-payment-server"
    )
    after_arrival = next(
        item.arrival_offset_ms
        for item in after.envelopes
        if item.span.span_id == "span-payment-server"
    )

    assert before_arrival < timeout_ms
    assert after_arrival > timeout_ms
