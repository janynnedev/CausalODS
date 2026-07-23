from datetime import (
    datetime,
    timedelta,
    timezone,
)

from types import SimpleNamespace

from correlation.temporal_correlator import (
    TemporalCorrelator,
)

from features.temporal_feature_extractor import (
    TemporalFeatureExtractor,
)


def make_span(
    *,
    span_id,
    parent_span_id,
    service_name,
    operation_name,
    start_time,
    duration_ms,
    error,
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
        status_code=(
            "ERROR"
            if error
            else "OK"
        ),
    )


def test_downstream_error_is_selected_as_failure_origin():

    start = datetime(
        2026,
        7,
        22,
        20,
        0,
        tzinfo=timezone.utc,
    )

    order_server = make_span(
        span_id="order-server",
        parent_span_id=None,
        service_name="order-service",
        operation_name="POST /orders",
        start_time=start,
        duration_ms=100.0,
        error=True,
    )

    order_client = make_span(
        span_id="order-client",
        parent_span_id="order-server",
        service_name="order-service",
        operation_name="POST",
        start_time=(
            start
            + timedelta(
                milliseconds=10
            )
        ),
        duration_ms=80.0,
        error=True,
    )

    payment_server = make_span(
        span_id="payment-server",
        parent_span_id="order-client",
        service_name="payment-service",
        operation_name="POST /payments",
        start_time=(
            start
            + timedelta(
                milliseconds=20
            )
        ),
        duration_ms=50.0,
        error=True,
    )

    trace = SimpleNamespace(
        root_span_id="order-server",
        spans={
            order_server.span_id:
                order_server,

            order_client.span_id:
                order_client,

            payment_server.span_id:
                payment_server,
        },
    )

    result = (
        TemporalCorrelator()
        .analyze(
            trace
        )
    )

    assert (
        result["first_failure"][
            "service"
        ]
        == "payment-service"
    )

    payment_result = (
        result["services"][
            "payment-service"
        ]
    )

    order_result = (
        result["services"][
            "order-service"
        ]
    )

    assert (
        payment_result[
            "is_first_failure"
        ]
        is True
    )

    assert (
        payment_result[
            "failure_order"
        ]
        == 1
    )

    assert (
        order_result[
            "is_first_failure"
        ]
        is False
    )

    assert (
        order_result[
            "failure_order"
        ]
        == 2
    )

    assert (
        payment_result["score"]
        > order_result["score"]
    )

    temporal_features = (
        TemporalFeatureExtractor()
        .extract(
            result
        )
    )

    assert (
        temporal_features[
            "payment-service"
        ][
            "is_first_failure"
        ]
        is True
    )

    assert (
        temporal_features[
            "payment-service"
        ][
            "failure_order"
        ]
        == 1
    )

    assert (
        temporal_features[
            "order-service"
        ][
            "is_first_failure"
        ]
        is False
    )

    assert (
        temporal_features[
            "order-service"
        ][
            "failure_order"
        ]
        == 2
    )