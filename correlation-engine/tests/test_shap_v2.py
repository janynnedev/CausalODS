import pandas as pd
import pytest

from shap_analysis.shap_service_v2 import (
    SHAPServiceV2,
)
from training.predictor_v2 import (
    PredictorV2,
)


def test_shap_v2_explains_fault_sample():

    dataframe = pd.read_csv(
        (
            "training/data/"
            "rca_dataset_v2.csv"
        )
    )

    root_cause_row = (
        dataframe[
            dataframe[
                "root_cause"
            ]
            == 1
        ]
        .iloc[0]
    )

    sample_id = (
        root_cause_row[
            "sample_id"
        ]
    )

    expected_root_service = (
        root_cause_row[
            "service_name"
        ]
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

    predictor = PredictorV2()

    service = SHAPServiceV2(
        predictor=predictor
    )

    results = service.explain(
        rows
    )

    assert len(
        results
    ) == 2

    indexed = {
        item[
            "service_name"
        ]:
            item
        for item in results
    }

    assert (
        expected_root_service
        in indexed
    )

    root_explanation = (
        indexed[
            expected_root_service
        ]
    )

    for result in results:

        assert len(
            result[
                "contributions"
            ]
        ) == 16

        assert (
            result[
                "score"
            ]
            == pytest.approx(
                result[
                    "support_score"
                ]
            )
        )

        assert (
            result[
                "shap_score"
            ]
            == pytest.approx(
                result[
                    "support_score"
                ]
            )
        )

        assert (
            result[
                "shap_support_score"
            ]
            == pytest.approx(
                result[
                    "support_score"
                ]
            )
        )

        feature_names = {
            contribution[
                "feature_name"
            ]
            for contribution
            in result[
                "contributions"
            ]
        }

        assert (
            "exclusive_latency_ms"
            in feature_names
        )

        assert (
            "exclusive_latency_ratio"
            in feature_names
        )

    assert (
        root_explanation[
            "predicted_probability"
        ]
        > 0.5
    )

    assert (
        root_explanation[
            "raw_positive_support"
        ]
        > 0.0
    )

    assert (
        root_explanation[
            "support_score"
        ]
        > 0.0
    )

    assert (
        root_explanation[
            "additivity_error"
        ]
        < 1e-3
    )

    assert (
        root_explanation[
            "shap_orientation"
        ]
        in {
            "direct_positive_class",
            "inverted_to_positive_class",
        }
    )

    non_root_explanations = [
        result
        for result
        in results
        if (
            result[
                "service_name"
            ]
            != expected_root_service
        )
    ]

    assert len(
        non_root_explanations
    ) == 1

    non_root_explanation = (
        non_root_explanations[0]
    )

    assert (
        root_explanation[
            "support_score"
        ]
        > non_root_explanation[
            "support_score"
        ]
    )