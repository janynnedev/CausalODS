from dataset.dataset_builder_v2 import (
    DatasetBuilderV2,
)
from models.rca_feature_schema_v2 import (
    MODEL_FEATURE_COLUMNS,
)
from training.preprocessing_v2 import (
    TrainingPreprocessorV2,
)


def test_preprocessing_v2_preserves_column_order():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=100
        )
    )

    X, y = (
        TrainingPreprocessorV2()
        .prepare(
            dataframe
        )
    )

    assert (
        X.columns.tolist()
        == list(
            MODEL_FEATURE_COLUMNS
        )
    )

    assert len(X) == len(y)


def test_group_split_has_no_sample_leakage():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=300
        )
    )

    split = (
        TrainingPreprocessorV2()
        .split(
            dataframe,
            test_size=0.20,
            random_state=42,
        )
    )

    train_ids = set(
        split[
            "train_dataframe"
        ][
            "sample_id"
        ]
    )

    test_ids = set(
        split[
            "test_dataframe"
        ][
            "sample_id"
        ]
    )

    assert (
        train_ids
        .isdisjoint(
            test_ids
        )
    )


def test_preprocessed_values_are_numeric():

    dataframe = (
        DatasetBuilderV2(
            seed=42
        )
        .to_dataframe(
            samples=100
        )
    )

    X, _ = (
        TrainingPreprocessorV2()
        .prepare(
            dataframe
        )
    )

    assert all(
        str(dtype).startswith(
            (
                "int",
                "float",
            )
        )
        for dtype in X.dtypes
    )