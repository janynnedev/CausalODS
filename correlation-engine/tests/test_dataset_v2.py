import pytest

from dataset.dataset_builder_v2 import (
    DATASET_COLUMNS,
    DatasetBuilderV2,
)
from models.rca_feature_schema import MODEL_FEATURE_COLUMNS, TARGET_COLUMN
from models.rca_feature_schema_v2 import MODEL_FEATURE_COLUMNS, TARGET_COLUMN


def test_dataset_v2_respects_schema():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=100
        )
    )

    assert (
        dataframe.columns.tolist()
        == list(
            DATASET_COLUMNS
        )
    )

    assert len(
        dataframe
    ) == 200

    required_non_null_columns = [
        "sample_id",
        "scenario",
        "service_name",
        "fault_delay_ms",
        "generation_seed",
        *MODEL_FEATURE_COLUMNS,
        TARGET_COLUMN,
    ]

    assert not (
        dataframe[required_non_null_columns].isna()
        .any()
        .any()
    )

    healthy = dataframe[
        dataframe["scenario"]
        == "healthy"
    ]

    faults = dataframe[
        dataframe["scenario"]
        != "healthy"
    ]

    assert (
        healthy[
            "root_cause_service"
        ]
        .isna()
        .all()
    )

    assert (
        faults[
            "root_cause_service"
        ]
        .notna()
        .all()
    )


def test_each_sample_has_two_services():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=100
        )
    )

    counts = (
        dataframe
        .groupby(
            "sample_id"
        )
        .size()
    )

    assert set(
        counts.unique()
    ) == {2}


def test_healthy_samples_have_no_root_cause():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=300
        )
    )

    healthy = dataframe[
        dataframe[
            "scenario"
        ] == "healthy"
    ]

    assert not healthy.empty

    assert healthy[
        TARGET_COLUMN
    ].sum() == 0


def test_fault_samples_have_one_root_cause():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=300
        )
    )

    faults = dataframe[
        dataframe[
            "scenario"
        ] != "healthy"
    ]

    positives = (
        faults
        .groupby(
            "sample_id"
        )[
            TARGET_COLUMN
        ]
        .sum()
    )

    assert set(
        positives.unique()
    ) == {1}


def test_latency_root_has_largest_exclusive_latency():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=500
        )
    )

    latency = dataframe[
        dataframe[
            "scenario"
        ].str.endswith(
            "_latency"
        )
    ]

    assert not latency.empty

    for _, group in latency.groupby(
        "sample_id"
    ):

        root_service = (
            group[
                "root_cause_service"
            ]
            .iloc[0]
        )

        root_row = group[
            group[
                "service_name"
            ] == root_service
        ].iloc[0]

        other_row = group[
            group[
                "service_name"
            ] != root_service
        ].iloc[0]

        assert (
            root_row[
                "exclusive_latency_ms"
            ]
            > other_row[
                "exclusive_latency_ms"
            ]
        )

        assert (
            root_row[
                "exclusive_latency_ratio"
            ]
            > other_row[
                "exclusive_latency_ratio"
            ]
        )