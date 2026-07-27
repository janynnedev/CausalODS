import json

import joblib

from models.rca_feature_schema_v2 import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)
from training.model_manifest import (
    ModelManifest,
)
from training.run_training_v2 import (
    train_and_save,
)


def test_training_v2_creates_artifacts(
    tmp_path,
):

    dataset_path = (
        tmp_path
        / "data"
        / "dataset.csv"
    )

    model_directory = (
        tmp_path
        / "models"
    )

    result = train_and_save(
        samples=300,
        seed=42,
        test_size=0.20,
        n_estimators=40,
        dataset_path=dataset_path,
        model_directory=(
            model_directory
        ),
    )

    assert (
        result[
            "dataset_path"
        ].is_file()
    )

    assert (
        result[
            "model_path"
        ].is_file()
    )

    assert (
        result[
            "manifest_path"
        ].is_file()
    )

    assert (
        result[
            "metrics_path"
        ].is_file()
    )

    model = joblib.load(
        result[
            "model_path"
        ]
    )

    assert tuple(
        model
        .get_booster()
        .feature_names
    ) == MODEL_FEATURE_COLUMNS

    manifest = (
        ModelManifest.load(
            result[
                "manifest_path"
            ]
        )
    )

    assert (
        manifest
        .feature_schema_version
        == FEATURE_SCHEMA_VERSION
    )

    assert (
        manifest.feature_columns
        == MODEL_FEATURE_COLUMNS
    )

    metrics = json.loads(
        result[
            "metrics_path"
        ]
        .read_text(
            encoding="utf-8"
        )
    )

    assert (
        0.0
        <= metrics["accuracy"]
        <= 1.0
    )

    assert (
        0.0
        <= metrics["f1"]
        <= 1.0
    )