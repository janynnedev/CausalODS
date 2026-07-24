import pytest

from features.temporal_feature_extractor import (
    TemporalFeatureExtractor,
)


def test_extracts_exclusive_latency_features():

    temporal_result = {
        "trace_duration_ms": 1600.0,

        "services": {
            "order-service": {
                "score": 0.0,
                "is_first_failure": False,
                "failure_order": -1,
                "failure_lead_time_ms": 0.0,
                "latency_rank": 2,
                "exclusive_latency_ms": 100.0,
            },

            "payment-service": {
                "score": 1.0,
                "is_first_failure": False,
                "failure_order": -1,
                "failure_lead_time_ms": 0.0,
                "latency_rank": 1,
                "exclusive_latency_ms": 1500.0,
            },
        },
    }

    extractor = (
        TemporalFeatureExtractor()
    )

    result = extractor.extract(
        temporal_result
    )

    assert result[
        "order-service"
    ][
        "exclusive_latency_ms"
    ] == pytest.approx(
        100.0
    )

    assert result[
        "order-service"
    ][
        "exclusive_latency_ratio"
    ] == pytest.approx(
        0.0625
    )

    assert result[
        "payment-service"
    ][
        "exclusive_latency_ms"
    ] == pytest.approx(
        1500.0
    )

    assert result[
        "payment-service"
    ][
        "exclusive_latency_ratio"
    ] == pytest.approx(
        0.9375
    )

    assert result[
        "payment-service"
    ][
        "temporal_score"
    ] == pytest.approx(
        1.0
    )

    assert result[
        "payment-service"
    ][
        "latency_rank"
    ] == 1


def test_ratio_is_clamped_to_one():

    temporal_result = {
        "trace_duration_ms": 1000.0,

        "services": {
            "payment-service": {
                "exclusive_latency_ms": (
                    1200.0
                ),
            },
        },
    }

    result = (
        TemporalFeatureExtractor()
        .extract(
            temporal_result
        )
    )

    assert result[
        "payment-service"
    ][
        "exclusive_latency_ratio"
    ] == pytest.approx(
        1.0
    )


def test_missing_temporal_result_returns_empty():

    extractor = (
        TemporalFeatureExtractor()
    )

    assert extractor.extract(
        None
    ) == {}

    assert extractor.extract(
        {}
    ) == {}