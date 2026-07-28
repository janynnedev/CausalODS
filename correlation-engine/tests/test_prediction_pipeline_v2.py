import pandas as pd

from pipeline.prediction_pipeline_v2 import (
    PredictionPipelineV2,
)


def test_prediction_pipeline_v2_returns_contract():

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

    rows = (
        dataframe[
            dataframe[
                "sample_id"
            ]
            == sample_id
        ]
        .to_dict(
            orient="records"
        )
    )

    pipeline = (
        PredictionPipelineV2()
    )

    predictions, shap_results = (
        pipeline.process(
            rows
        )
    )

    assert len(
        predictions
    ) == 2

    assert len(
        shap_results
    ) == 2

    assert {
        item[
            "service"
        ]
        for item
        in predictions
    } == {
        item[
            "service"
        ]
        for item
        in shap_results
    }