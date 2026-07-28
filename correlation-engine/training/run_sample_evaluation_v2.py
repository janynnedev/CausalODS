from __future__ import annotations

import argparse
import json

from pathlib import Path
from typing import Any

import pandas as pd

from training.predictor_v2 import (
    PredictorV2,
)
from training.sample_evaluator_v2 import (
    SampleEvaluatorV2,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


def json_safe(
    value: Any,
) -> Any:

    if isinstance(
        value,
        dict,
    ):

        return {
            str(key):
                json_safe(item)
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        (list, tuple),
    ):

        return [
            json_safe(item)
            for item in value
        ]

    if hasattr(
        value,
        "item",
    ):

        return value.item()

    return value


def resolve(
    value: str,
) -> Path:

    path = Path(
        value
    ).expanduser()

    if not path.is_absolute():

        path = (
            PROJECT_ROOT
            / path
        )

    return path.resolve()


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Avalia o XGBoost v2 por sample_id."
        )
    )

    parser.add_argument(
        "--dataset",
        default=(
            "training/data/"
            "rca_dataset_v2.csv"
        ),
    )

    parser.add_argument(
        "--model",
        default=(
            "training/models/"
            "xgboost_rca_v2.pkl"
        ),
    )

    parser.add_argument(
        "--manifest",
        default=(
            "training/models/"
            "xgboost_rca_v2.manifest.json"
        ),
    )

    parser.add_argument(
        "--test-size",
        type=float,
        default=0.20,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=0.50,
    )

    parser.add_argument(
        "--metrics-output",
        default=(
            "training/models/"
            "xgboost_rca_v2_sample_metrics.json"
        ),
    )

    parser.add_argument(
        "--predictions-output",
        default=(
            "training/models/"
            "xgboost_rca_v2_sample_predictions.csv"
        ),
    )

    arguments = (
        parser.parse_args()
    )

    dataset_path = resolve(
        arguments.dataset
    )

    dataframe = pd.read_csv(
        dataset_path
    )

    predictor = PredictorV2(
        model_path=arguments.model,
        manifest_path=(
            arguments.manifest
        ),
    )

    evaluator = (
        SampleEvaluatorV2()
    )

    metrics, predictions = (
        evaluator
        .evaluate_held_out_dataset(
            dataframe=dataframe,
            predictor=predictor,
            test_size=(
                arguments.test_size
            ),
            random_seed=(
                arguments.seed
            ),
            decision_threshold=(
                arguments.threshold
            ),
        )
    )

    metrics.update(
        {
            "model_version":
                predictor.model_version,

            "feature_schema_version":
                predictor
                .feature_schema_version,

            "decision_threshold":
                float(
                    arguments.threshold
                ),
        }
    )

    metrics_output = resolve(
        arguments.metrics_output
    )

    predictions_output = resolve(
        arguments.predictions_output
    )

    metrics_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    predictions_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics_output.write_text(
        json.dumps(
            json_safe(
                metrics
            ),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    predictions.to_csv(
        predictions_output,
        index=False,
    )

    print(
        json.dumps(
            json_safe(
                metrics
            ),
            indent=2,
            ensure_ascii=False,
        )
    )

    print(
        (
            "Metrics: "
            f"{metrics_output}"
        )
    )

    print(
        (
            "Predictions: "
            f"{predictions_output}"
        )
    )


if __name__ == "__main__":

    main()