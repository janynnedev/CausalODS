from models.rca_feature import RCAFeature
from features.feature_aggregator import (
    FeatureAggregator,
)


def test_creates_rca_feature_with_all_fields():

    feature = RCAFeature(
        service_name="payment-service",
        request_count=20,
        error_count=5,
        error_rate=0.25,
        avg_latency_ms=180.0,
        max_latency_ms=450.0,
        incoming_dependencies=1,
        outgoing_dependencies=1,
        dependency_count=2,
        is_first_failure=True,
        failure_order=1,
        failure_lead_time_ms=120.0,
        latency_zscore=3.5,
        anomaly_score=0.90,
        is_anomalous=True,
    )

    assert (
        feature.service_name
        == "payment-service"
    )

    assert feature.request_count == 20
    assert feature.error_count == 5
    assert feature.error_rate == 0.25

    assert (
        feature.avg_latency_ms
        == 180.0
    )

    assert (
        feature.max_latency_ms
        == 450.0
    )

    assert (
        feature.incoming_dependencies
        == 1
    )

    assert (
        feature.outgoing_dependencies
        == 1
    )

    assert feature.dependency_count == 2

    assert feature.is_first_failure is True
    assert feature.failure_order == 1

    assert (
        feature.failure_lead_time_ms
        == 120.0
    )

    assert feature.latency_zscore == 3.5
    assert feature.anomaly_score == 0.90
    assert feature.is_anomalous is True


def test_creates_normal_service_feature():

    feature = RCAFeature(
        service_name="order-service",
        request_count=20,
        error_count=0,
        error_rate=0.0,
        avg_latency_ms=80.0,
        max_latency_ms=110.0,
        incoming_dependencies=0,
        outgoing_dependencies=1,
        dependency_count=1,
        is_first_failure=False,
        failure_order=0,
        failure_lead_time_ms=0.0,
        latency_zscore=0.15,
        anomaly_score=0.05,
        is_anomalous=False,
    )

    assert (
        feature.service_name
        == "order-service"
    )

    assert feature.error_count == 0
    assert feature.error_rate == 0.0
    assert feature.is_first_failure is False
    assert feature.failure_order == 0
    assert feature.is_anomalous is False

def test_feature_aggregator_maps_trace_metrics():

    aggregator = FeatureAggregator()

    result = aggregator.aggregate(
        trace_features={
            "order-service": {
                "request_count": 2,
                "error_count": 1,
                "error_rate": 0.5,
                "avg_latency_ms": 75.0,
                "max_latency_ms": 100.0,
            }
        },
        graph_features={
            "order-service": {
                "incoming_dependencies": 0,
                "outgoing_dependencies": 1,
                "dependency_count": 1,
            }
        },
        temporal_features={
            "order-service": {
                "is_first_failure": True,
                "failure_order": 1,
                "failure_lead_time_ms": 20.0,
            }
        },
        anomaly_features={
            "order-service": {
                "latency_zscore": 2.5,
                "anomaly_score": 0.8,
                "is_anomalous": True,
            }
        },
    )

    assert len(result) == 1

    feature = result[0]

    assert (
        feature.service_name
        == "order-service"
    )

    assert feature.request_count == 2
    assert feature.error_count == 1
    assert feature.error_rate == 0.5

    assert (
        feature.avg_latency_ms
        == 75.0
    )

    assert (
        feature.max_latency_ms
        == 100.0
    )

    assert (
        feature.incoming_dependencies
        == 0
    )

    assert (
        feature.outgoing_dependencies
        == 1
    )

    assert (
        feature.dependency_count
        == 1
    )

    assert feature.is_first_failure is True
    assert feature.failure_order == 1

    assert (
        feature.failure_lead_time_ms
        == 20.0
    )

    assert feature.latency_zscore == 2.5
    assert feature.anomaly_score == 0.8
    assert feature.is_anomalous is True

    assert isinstance(
        feature.request_count,
        int,
    )

    assert isinstance(
        feature.avg_latency_ms,
        float,
    )    