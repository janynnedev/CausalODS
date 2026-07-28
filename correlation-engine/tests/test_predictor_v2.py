import pandas as pd
import pytest

from models.rca_feature_schema_v2 import (
    MODEL_FEATURE_COLUMNS,
)
from training.predictor_v2 import (
    PredictorV2,
)


def dataset_rows():

    dataframe = pd.read_csv(
        (
            "training/data/"
            "rca_dataset_v2.csv"
        )
    )

    sample_id = (
        dataframe[
            "sample_id"
        ]
        .iloc[0]
    )

    sample = dataframe[
        dataframe[
            "sample_id"
        ]
        == sample_id
    ]

    return (
        sample.to_dict(
            orient="records"
        )
    )


def test_predictor_v2_predicts_sample():

    predictor = PredictorV2()

    rows = dataset_rows()

    predictions = (
        predictor.predict(
            rows
        )
    )

    assert len(
        predictions
    ) == 2

    for prediction in predictions:

        assert (
            0.0
            <= prediction[
                "confidence"
            ]
            <= 1.0
        )

        assert (
            prediction[
                "model_version"
            ]
            == "xgboost-rca-v2"
        )


def test_predictor_v2_rejects_missing_feature():

    predictor = PredictorV2()

    row = dataset_rows()[0]

    row.pop(
        MODEL_FEATURE_COLUMNS[
            -1
        ]
    )

    with pytest.raises(
        ValueError,
        match="ausente",
    ):

        predictor.predict(
            [row]
        )


def test_predictor_v2_rejects_nan():

    predictor = PredictorV2()

    row = dataset_rows()[0]

    row[
        "exclusive_latency_ms"
    ] = float(
        "nan"
    )

    with pytest.raises(
        ValueError
    ):

        predictor.predict(
            [row]
        )