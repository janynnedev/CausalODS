import json

from pathlib import Path

import pytest

from training.artifact_validator_v2 import (
    ArtifactValidationError,
    ArtifactValidatorV2,
)


MODEL_PATH = Path(
    "training/models/"
    "xgboost_rca_v2.pkl"
)

MANIFEST_PATH = Path(
    "training/models/"
    "xgboost_rca_v2.manifest.json"
)


def test_artifact_validator_v2_accepts_bundle():

    bundle = (
        ArtifactValidatorV2()
        .load_and_validate(
            model_path=MODEL_PATH,
            manifest_path=(
                MANIFEST_PATH
            ),
        )
    )

    assert (
        bundle.model_version
        == "xgboost-rca-v2"
    )

    assert (
        bundle.feature_schema_version
        == "rca-features-v2"
    )

    assert len(
        bundle.feature_columns
    ) == 16


def test_artifact_validator_v2_rejects_wrong_order(
    tmp_path,
):

    manifest = json.loads(
        MANIFEST_PATH.read_text(
            encoding="utf-8"
        )
    )

    manifest[
        "feature_columns"
    ] = list(
        reversed(
            manifest[
                "feature_columns"
            ]
        )
    )

    invalid_manifest = (
        tmp_path
        / "invalid.manifest.json"
    )

    invalid_manifest.write_text(
        json.dumps(
            manifest
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ArtifactValidationError
    ):

        (
            ArtifactValidatorV2()
            .load_and_validate(
                model_path=MODEL_PATH,
                manifest_path=(
                    invalid_manifest
                ),
            )
        )