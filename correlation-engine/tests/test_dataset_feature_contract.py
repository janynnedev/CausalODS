from dataset.feature_generator import (
    FeatureGenerator,
)

from models.rca_feature_schema import (
    MODEL_FEATURE_COLUMNS,
)


EXPECTED_GENERATED_COLUMNS = (
    set(
        MODEL_FEATURE_COLUMNS
    )
    | {
        "service_name",
    }
)


def test_generated_normal_record_matches_schema():

    generator = FeatureGenerator(
        seed=42
    )

    record = generator.generate(
        scenario="normal",
        service="order",
    )

    assert (
        set(record.keys())
        == EXPECTED_GENERATED_COLUMNS
    )

    assert (
        record["dependency_count"]
        == (
            record[
                "incoming_dependencies"
            ]
            + record[
                "outgoing_dependencies"
            ]
        )
    )

    assert (
        record["max_latency_ms"]
        >= record["avg_latency_ms"]
    )

    assert (
        record["error_count"]
        <= record["request_count"]
    )


def test_database_latency_generates_anomaly():

    generator = FeatureGenerator(
        seed=42
    )

    record = generator.generate(
        scenario="database_latency",
        service="database",
    )

    assert record["is_first_failure"] == 1
    assert record["failure_order"] == 1
    assert record["is_anomalous"] == 1

    zscore_min, zscore_max = (
        FeatureGenerator
        .ROOT_PROFILES[
            "database_latency"
        ][
            "zscore_range"
        ]
    )

    assert (
        zscore_min
        <= record["latency_zscore"]
        <= zscore_max
    )

    anomaly_min, anomaly_max = (
        FeatureGenerator
        .ROOT_PROFILES[
            "database_latency"
        ][
            "anomaly_range"
        ]
    )

    assert (
        anomaly_min
        <= record["anomaly_score"]
        <= anomaly_max
    )

    assert (
        record["max_latency_ms"]
        >= record["avg_latency_ms"]
    )

    assert (
        record["error_count"]
        <= record["request_count"]
    )


def test_payment_failure_has_consistent_error_count():

    generator = FeatureGenerator(
        seed=42
    )

    record = generator.generate(
        scenario="payment_failure",
        service="payment",
    )

    assert record["is_first_failure"] == 1
    assert record["failure_order"] == 1
    assert record["is_anomalous"] == 1

    assert (
        record["error_count"]
        <= record["request_count"]
    )

    calculated_rate = (
        record["error_count"]
        / record["request_count"]
    )

    assert abs(
        calculated_rate
        - record["error_rate"]
    ) < 0.01