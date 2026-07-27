from models.rca_feature_schema import (
    MODEL_FEATURE_COLUMNS as V1_MODEL_FEATURE_COLUMNS,
)

from models.rca_feature_schema_v2 import (
    BOOLEAN_FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
)

from models.rca_feature_schema_v2 import (
    BOOLEAN_FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
)


def test_v2_preserves_v1_column_order():

    v1_size = len(
        V1_MODEL_FEATURE_COLUMNS
    )

    assert (
        MODEL_FEATURE_COLUMNS[
            :v1_size
        ]
        == V1_MODEL_FEATURE_COLUMNS
    )


def test_v2_adds_exclusive_latency_columns():

    assert MODEL_FEATURE_COLUMNS[
        -2:
    ] == (
        "exclusive_latency_ms",
        "exclusive_latency_ratio",
    )


def test_v2_columns_are_unique():

    assert len(
        MODEL_FEATURE_COLUMNS
    ) == len(
        set(
            MODEL_FEATURE_COLUMNS
        )
    )


def test_v2_exclusive_features_are_float():

    assert (
        "exclusive_latency_ms"
        in FLOAT_FEATURE_COLUMNS
    )

    assert (
        "exclusive_latency_ratio"
        in FLOAT_FEATURE_COLUMNS
    )


def test_v2_exclusive_features_are_not_integer():

    assert (
        "exclusive_latency_ms"
        not in INTEGER_FEATURE_COLUMNS
    )

    assert (
        "exclusive_latency_ratio"
        not in INTEGER_FEATURE_COLUMNS
    )


def test_v2_exclusive_features_are_not_boolean():

    assert (
        "exclusive_latency_ms"
        not in BOOLEAN_FEATURE_COLUMNS
    )

    assert (
        "exclusive_latency_ratio"
        not in BOOLEAN_FEATURE_COLUMNS
    )


def test_v2_schema_version():

    assert (
        FEATURE_SCHEMA_VERSION
        == "rca-features-v2"
    )