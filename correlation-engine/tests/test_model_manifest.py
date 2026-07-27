import pytest

from models.rca_feature_schema_v2 import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)
from training.model_manifest import (
    ModelManifest,
)


def test_model_manifest_round_trip(
    tmp_path,
):

    manifest = ModelManifest.create(
        model_version=(
            "xgboost-rca-v2"
        ),

        feature_schema_version=(
            FEATURE_SCHEMA_VERSION
        ),

        feature_columns=(
            MODEL_FEATURE_COLUMNS
        ),

        model_filename=(
            "xgboost_rca_v2.pkl"
        ),

        metrics={
            "accuracy": 0.95,
            "f1": 0.94,
        },

        training_metadata={
            "dataset_version": (
                "rca-dataset-v2"
            ),
        },
    )

    path = (
        tmp_path
        / "xgboost_rca_v2.manifest.json"
    )

    manifest.save(
        path
    )

    loaded = (
        ModelManifest.load(
            path
        )
    )

    assert (
        loaded.model_version
        == "xgboost-rca-v2"
    )

    assert (
        loaded.feature_schema_version
        == "rca-features-v2"
    )

    assert (
        loaded.feature_columns
        == MODEL_FEATURE_COLUMNS
    )

    assert loaded.metrics[
        "accuracy"
    ] == pytest.approx(
        0.95
    )


def test_manifest_accepts_matching_columns():

    manifest = ModelManifest.create(
        model_version=(
            "xgboost-rca-v2"
        ),

        feature_schema_version=(
            FEATURE_SCHEMA_VERSION
        ),

        feature_columns=(
            MODEL_FEATURE_COLUMNS
        ),

        model_filename=(
            "xgboost_rca_v2.pkl"
        ),
    )

    manifest.validate_feature_columns(
        MODEL_FEATURE_COLUMNS
    )


def test_manifest_rejects_wrong_order():

    manifest = ModelManifest.create(
        model_version=(
            "xgboost-rca-v2"
        ),

        feature_schema_version=(
            FEATURE_SCHEMA_VERSION
        ),

        feature_columns=(
            MODEL_FEATURE_COLUMNS
        ),

        model_filename=(
            "xgboost_rca_v2.pkl"
        ),
    )

    wrong_order = tuple(
        reversed(
            MODEL_FEATURE_COLUMNS
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "Schema de features incompatível"
        ),
    ):

        manifest.validate_feature_columns(
            wrong_order
        )


def test_manifest_rejects_duplicate_columns():

    with pytest.raises(
        ValueError,
        match="nomes duplicados",
    ):

        ModelManifest.create(
            model_version=(
                "xgboost-rca-v2"
            ),

            feature_schema_version=(
                FEATURE_SCHEMA_VERSION
            ),

            feature_columns=(
                "request_count",
                "request_count",
            ),

            model_filename=(
                "xgboost_rca_v2.pkl"
            ),
        )