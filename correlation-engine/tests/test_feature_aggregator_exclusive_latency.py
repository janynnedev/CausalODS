import pytest

from features.feature_aggregator import (
    FeatureAggregator,
)


def test_aggregates_exclusive_latency():

    aggregator = FeatureAggregator()

    features = aggregator.aggregate(
        trace_features={
            "order-service": {
                "request_count": 2,
                "error_count": 0,
                "error_rate": 0.0,
                "avg_latency_ms": 1550.0,
                "max_latency_ms": 1600.0,
            },

            "payment-service": {
                "request_count": 1,
                "error_count": 0,
                "error_rate": 0.0,
                "avg_latency_ms": 1500.0,
                "max_latency_ms": 1500.0,
            },
        },

        graph_features={
            "order-service": {
                "incoming_dependencies": 0,
                "outgoing_dependencies": 1,
                "dependency_count": 1,
            },

            "payment-service": {
                "incoming_dependencies": 1,
                "outgoing_dependencies": 0,
                "dependency_count": 1,
            },
        },

        temporal_features={
            "order-service": {
                "is_first_failure": False,
                "failure_order": -1,
                "failure_lead_time_ms": 0.0,
                "exclusive_latency_ms": 100.0,
                "exclusive_latency_ratio": (
                    0.0625
                ),
            },

            "payment-service": {
                "is_first_failure": False,
                "failure_order": -1,
                "failure_lead_time_ms": 0.0,
                "exclusive_latency_ms": (
                    1500.0
                ),
                "exclusive_latency_ratio": (
                    0.9375
                ),
            },
        },

        anomaly_features={},
    )

    by_service = {
        feature.service_name: feature
        for feature in features
    }

    order = by_service[
        "order-service"
    ]

    payment = by_service[
        "payment-service"
    ]

    assert (
        order.exclusive_latency_ms
        == pytest.approx(
            100.0
        )
    )

    assert (
        order.exclusive_latency_ratio
        == pytest.approx(
            0.0625
        )
    )

    assert (
        payment.exclusive_latency_ms
        == pytest.approx(
            1500.0
        )
    )

    assert (
        payment.exclusive_latency_ratio
        == pytest.approx(
            0.9375
        )
    )

    assert (
        payment.exclusive_latency_ms
        > order.exclusive_latency_ms
    )


def test_exclusive_latency_is_serialized():

    aggregator = FeatureAggregator()

    features = aggregator.aggregate(
        trace_features={
            "payment-service": {
                "request_count": 1,
                "error_count": 0,
                "error_rate": 0.0,
                "avg_latency_ms": 1500.0,
                "max_latency_ms": 1500.0,
            },
        },

        graph_features={},

        temporal_features={
            "payment-service": {
                "exclusive_latency_ms": (
                    1500.0
                ),
                "exclusive_latency_ratio": (
                    0.95
                ),
            },
        },

        anomaly_features={},
    )

    payload = features[0].to_dict()

    assert payload[
        "exclusive_latency_ms"
    ] == pytest.approx(
        1500.0
    )

    assert payload[
        "exclusive_latency_ratio"
    ] == pytest.approx(
        0.95
    )