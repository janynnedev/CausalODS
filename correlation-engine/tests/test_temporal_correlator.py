from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace

import pytest

from correlation.temporal_correlator import (
    TemporalCorrelator,
)


def make_span(
    *,
    span_id: str,
    service_name: str,
    start_time: datetime,
    duration_ms: float,
    parent_span_id: str | None = None,
    operation_name: str = (
        "test-operation"
    ),
    status_code: str = "OK",
):
    return SimpleNamespace(
        span_id=span_id,
        parent_span_id=parent_span_id,
        service_name=service_name,
        operation_name=operation_name,
        start_time=start_time,
        end_time=(
            start_time
            + timedelta(
                milliseconds=duration_ms
            )
        ),
        duration_ms=duration_ms,
        status_code=status_code,
    )


def make_latency_trace(
    *,
    with_errors: bool = False,
):
    started_at = datetime(
        2026,
        7,
        24,
        12,
        0,
        0,
        tzinfo=timezone.utc,
    )

    root_status = (
        "ERROR"
        if with_errors
        else "OK"
    )

    client_status = (
        "ERROR"
        if with_errors
        else "OK"
    )

    payment_status = (
        "ERROR"
        if with_errors
        else "OK"
    )

    root_span = make_span(
        span_id="order-root",
        service_name="order-service",
        operation_name="POST /orders",
        start_time=started_at,
        duration_ms=1600.0,
        status_code=root_status,
    )

    order_client_span = make_span(
        span_id="order-payment-client",
        parent_span_id="order-root",
        service_name="order-service",
        operation_name="POST",
        start_time=(
            started_at
            + timedelta(
                milliseconds=20.0
            )
        ),
        duration_ms=1550.0,
        status_code=client_status,
    )

    payment_span = make_span(
        span_id="payment-server",
        parent_span_id=(
            "order-payment-client"
        ),
        service_name="payment-service",
        operation_name="POST /payments",
        start_time=(
            started_at
            + timedelta(
                milliseconds=40.0
            )
        ),
        duration_ms=1500.0,
        status_code=payment_status,
    )

    return SimpleNamespace(
        root_span_id="order-root",
        spans={
            root_span.span_id: root_span,
            order_client_span.span_id: (
                order_client_span
            ),
            payment_span.span_id: (
                payment_span
            ),
        },
    )

def make_balanced_healthy_trace():

    started_at = datetime(
        2026,
        7,
        24,
        12,
        30,
        0,
        tzinfo=timezone.utc,
    )

    root_span = make_span(
        span_id="healthy-order-root",
        service_name="order-service",
        operation_name="POST /orders",
        start_time=started_at,
        duration_ms=105.0,
        status_code="OK",
    )

    client_span = make_span(
        span_id="healthy-order-client",
        parent_span_id=(
            "healthy-order-root"
        ),
        service_name="order-service",
        operation_name="POST",
        start_time=(
            started_at
            + timedelta(
                milliseconds=20.0
            )
        ),
        duration_ms=55.0,
        status_code="OK",
    )

    payment_span = make_span(
        span_id="healthy-payment",
        parent_span_id=(
            "healthy-order-client"
        ),
        service_name="payment-service",
        operation_name="POST /payments",
        start_time=(
            started_at
            + timedelta(
                milliseconds=25.0
            )
        ),
        duration_ms=50.0,
        status_code="OK",
    )

    return SimpleNamespace(
        root_span_id=(
            "healthy-order-root"
        ),
        duration_ms=105.0,
        spans={
            root_span.span_id: root_span,
            client_span.span_id: (
                client_span
            ),
            payment_span.span_id: (
                payment_span
            ),
        },
    )

def test_exclusive_latency_attributes_wait_to_child():

    trace = make_latency_trace(
        with_errors=False
    )

    correlator = TemporalCorrelator()

    result = (
        correlator
        .compute_exclusive_latency_by_service(
            trace
        )
    )

    assert result[
        "order-service"
    ] == pytest.approx(
        100.0,
        abs=1.0,
    )

    assert result[
        "payment-service"
    ] == pytest.approx(
        1500.0,
        abs=1.0,
    )

    assert (
        result["payment-service"]
        > result["order-service"]
    )


def test_analyze_uses_exclusive_latency_without_errors():

    trace = make_latency_trace(
        with_errors=False
    )

    correlator = TemporalCorrelator()

    result = correlator.analyze(
        trace
    )

    assert result[
        "trace_duration_ms"
    ] == pytest.approx(
        1600.0,
        abs=1.0,
    )

    assert result[
        "method"
    ] == "temporal-correlation-v2"

    assert result[
        "latency_order_method"
    ] == "exclusive_latency_order"

    assert result[
        "latency_order"
    ] == [
        "payment-service",
        "order-service",
    ]

    assert result[
        "exclusive_latency_by_service"
    ][
        "payment-service"
    ] == pytest.approx(
        1500.0,
        abs=1.0,
    )

    payment_result = result[
        "services"
    ][
        "payment-service"
    ]

    order_result = result[
        "services"
    ][
        "order-service"
    ]

    assert payment_result[
        "scoring_mode"
    ] == "exclusive_latency_dominance"

    assert result[
        "exclusive_latency_dominance"
    ][
        "is_meaningful"
    ] is True

    assert result[
        "exclusive_latency_dominance"
    ][
        "top_service"
    ] == "payment-service"

    assert result[
        "exclusive_latency_dominance"
    ][
        "margin"
    ] == pytest.approx(
        0.875,
        abs=0.001,
    )

    assert payment_result[
        "latency_rank"
    ] == 1

    assert payment_result[
        "score"
    ] == pytest.approx(
        0.9375,
        abs=0.001,
    )

    assert order_result[
        "latency_rank"
    ] == 2

    assert order_result[
        "score"
    ] == pytest.approx(
        0.0625,
        abs=0.001,
    )

    assert (
        payment_result["score"]
        > order_result["score"]
    )


def test_analyze_preserves_failure_precedence():

    trace = make_latency_trace(
        with_errors=True
    )

    correlator = TemporalCorrelator()

    result = correlator.analyze(
        trace
    )

    assert result[
        "latency_order_method"
    ] == "inclusive_latency_order"

    first_failure = result[
        "first_failure"
    ]

    assert first_failure is not None

    assert first_failure[
        "service"
    ] == "payment-service"

    payment_result = result[
        "services"
    ][
        "payment-service"
    ]

    order_result = result[
        "services"
    ][
        "order-service"
    ]

    assert payment_result[
        "scoring_mode"
    ] == (
        "failure_precedence_and_latency"
    )

    assert payment_result[
        "is_first_failure"
    ] is True

    assert payment_result[
        "failure_order"
    ] == 1

    assert order_result[
        "failure_order"
    ] == 2

    assert (
        payment_result["score"]
        > order_result["score"]
    )

def test_analyze_suppresses_temporal_score_without_dominance():

    trace = (
        make_balanced_healthy_trace()
    )

    correlator = (
        TemporalCorrelator()
    )

    result = correlator.analyze(
        trace
    )

    dominance = result[
        "exclusive_latency_dominance"
    ]

    assert dominance[
        "is_meaningful"
    ] is False

    assert dominance[
        "top_share"
    ] < correlator.EXCLUSIVE_DOMINANCE_MIN_SHARE

    order_result = result[
        "services"
    ][
        "order-service"
    ]

    payment_result = result[
        "services"
    ][
        "payment-service"
    ]

    assert order_result[
        "score"
    ] == pytest.approx(
        0.0
    )

    assert payment_result[
        "score"
    ] == pytest.approx(
        0.0
    )

    assert order_result[
        "scoring_mode"
    ] == (
        "exclusive_latency_no_dominance"
    )

    assert payment_result[
        "scoring_mode"
    ] == (
        "exclusive_latency_no_dominance"
    )    