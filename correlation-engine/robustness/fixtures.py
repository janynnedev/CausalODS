from __future__ import annotations

from robustness.contracts import SpanEnvelope, SpanFixture, TraceFixture


MILLISECOND_NS = 1_000_000


def build_payment_latency_trace(
    *,
    trace_id: str = "trace-robustness-payment-latency",
) -> TraceFixture:
    spans = (
        SpanFixture(
            trace_id=trace_id,
            span_id="span-order-root",
            parent_span_id=None,
            service_name="order-service",
            operation="POST /orders",
            start_ns=0,
            end_ns=2_200 * MILLISECOND_NS,
            status_code="OK",
            attributes={
                "span.kind": "SERVER",
                "causalods.run_id": "robustness-fixture",
            },
        ),
        SpanFixture(
            trace_id=trace_id,
            span_id="span-order-payment-client",
            parent_span_id="span-order-root",
            service_name="order-service",
            operation="POST http://payment-service:8082/payments",
            start_ns=100 * MILLISECOND_NS,
            end_ns=1_850 * MILLISECOND_NS,
            status_code="OK",
            attributes={
                "span.kind": "CLIENT",
                "peer.service": "payment-service",
            },
        ),
        SpanFixture(
            trace_id=trace_id,
            span_id="span-payment-server",
            parent_span_id="span-order-payment-client",
            service_name="payment-service",
            operation="POST /payments",
            start_ns=120 * MILLISECOND_NS,
            end_ns=1_800 * MILLISECOND_NS,
            status_code="OK",
            attributes={
                "span.kind": "SERVER",
                "causalods.fault.mode": "latency",
                "causalods.fault.delay_ms": 1500,
            },
        ),
        SpanFixture(
            trace_id=trace_id,
            span_id="span-payment-validation",
            parent_span_id="span-payment-server",
            service_name="payment-service",
            operation="PaymentValidator.validate",
            start_ns=140 * MILLISECOND_NS,
            end_ns=1_720 * MILLISECOND_NS,
            status_code="OK",
            attributes={
                "span.kind": "INTERNAL",
                "causalods.expected_root_cause": True,
            },
        ),
        SpanFixture(
            trace_id=trace_id,
            span_id="span-order-response",
            parent_span_id="span-order-root",
            service_name="order-service",
            operation="OrderResponse.serialize",
            start_ns=1_900 * MILLISECOND_NS,
            end_ns=2_050 * MILLISECOND_NS,
            status_code="OK",
            attributes={"span.kind": "INTERNAL"},
        ),
    )

    return TraceFixture(
        trace_id=trace_id,
        spans=spans,
        expected_span_ids=frozenset(span.span_id for span in spans),
        expected_root_cause="payment-service",
        expected_reliable=True,
        metadata={
            "fixture_version": "robustness-fixture-v1",
            "fault_mode": "latency",
            "fault_delay_ms": 1500,
        },
    )


def baseline_envelopes(
    fixture: TraceFixture,
    *,
    step_ms: int = 25,
) -> tuple[SpanEnvelope, ...]:
    return tuple(
        SpanEnvelope(
            span=span,
            arrival_offset_ms=index * step_ms,
            sequence=index,
        )
        for index, span in enumerate(fixture.spans)
    )
