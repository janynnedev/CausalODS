from __future__ import annotations

import argparse
import json

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from xgboost import XGBClassifier

from dataset.dataset_builder_v2 import (
    DatasetBuilderV2,
)
from models.rca_feature_schema_v2 import (
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)
from training.evaluator_v2 import (
    EvaluatorV2,
)
from training.model_manifest import (
    ModelManifest,
)
from training.preprocessing_v2 import (
    TrainingPreprocessorV2,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

DEFAULT_DATASET_PATH = (
    PROJECT_ROOT
    / "training"
    / "data"
    / "rca_dataset_v2.csv"
)

DEFAULT_MODEL_DIRECTORY = (
    PROJECT_ROOT
    / "training"
    / "models"
)

MODEL_FILENAME = (
    "xgboost_rca_v2.pkl"
)

MANIFEST_FILENAME = (
    "xgboost_rca_v2.manifest.json"
)

METRICS_FILENAME = (
    "xgboost_rca_v2_metrics.json"
)

MODEL_VERSION = (
    "xgboost-rca-v2"
)

DATASET_VERSION = (
    "rca-dataset-v2"
)


def calculate_scale_pos_weight(
    y_train: pd.Series,
) -> float:

    positive_count = int(
        (y_train == 1).sum()
    )

    negative_count = int(
        (y_train == 0).sum()
    )

    if positive_count <= 0:

        raise ValueError(
            (
                "O conjunto de treino não "
                "possui exemplos positivos."
            )
        )

    return max(
        float(
            negative_count
            / positive_count
        ),
        1.0,
    )


def create_model(
    *,
    random_state: int,
    scale_pos_weight: float,
    n_estimators: int = 300,
) -> XGBClassifier:

    return XGBClassifier(
        n_estimators=int(
            n_estimators
        ),

        max_depth=4,

        learning_rate=0.05,

        subsample=0.90,

        colsample_bytree=0.90,

        min_child_weight=1.0,

        reg_alpha=0.0,

        reg_lambda=1.0,

        objective="binary:logistic",

        eval_metric="logloss",

        random_state=int(
            random_state
        ),

        scale_pos_weight=float(
            scale_pos_weight
        ),

        n_jobs=-1,
    )


def validate_model_feature_contract(
    model,
) -> None:

    booster = model.get_booster()

    feature_names = tuple(
        booster.feature_names
        or ()
    )

    if (
        feature_names
        != MODEL_FEATURE_COLUMNS
    ):

        raise ValueError(
            (
                "O modelo treinado não preservou "
                "o schema v2. "
                f"esperado={MODEL_FEATURE_COLUMNS}; "
                f"encontrado={feature_names}."
            )
        )


def train_and_save(
    *,
    samples: int = 5000,
    seed: int = 42,
    test_size: float = 0.20,
    n_estimators: int = 300,
    dataset_path: (
        str | Path
    ) = DEFAULT_DATASET_PATH,
    model_directory: (
        str | Path
    ) = DEFAULT_MODEL_DIRECTORY,
) -> dict[str, Any]:

    dataset_path = Path(
        dataset_path
    )

    model_directory = Path(
        model_directory
    )

    dataset_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    model_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    builder = DatasetBuilderV2(
        seed=seed
    )

    dataframe = builder.to_dataframe(
        samples=samples
    )

    dataframe.to_csv(
        dataset_path,
        index=False,
    )

    preprocessor = (
        TrainingPreprocessorV2()
    )

    split = preprocessor.split(
        dataframe,
        test_size=test_size,
        random_state=seed,
    )

    X_train = split[
        "X_train"
    ]

    X_test = split[
        "X_test"
    ]

    y_train = split[
        "y_train"
    ]

    y_test = split[
        "y_test"
    ]

    scale_pos_weight = (
        calculate_scale_pos_weight(
            y_train
        )
    )

    model = create_model(
        random_state=seed,
        scale_pos_weight=(
            scale_pos_weight
        ),
        n_estimators=n_estimators,
    )

    model.fit(
        X_train,
        y_train,
    )

    validate_model_feature_contract(
        model
    )

    evaluator = EvaluatorV2()

    metrics = evaluator.evaluate(
        model=model,
        X_test=X_test,
        y_test=y_test,
    )

    metrics.update(
        {
            "model_version": (
                MODEL_VERSION
            ),

            "dataset_version": (
                DATASET_VERSION
            ),

            "feature_schema_version": (
                FEATURE_SCHEMA_VERSION
            ),

            "samples": int(
                samples
            ),

            "dataset_rows": int(
                len(dataframe)
            ),

            "train_rows": int(
                len(X_train)
            ),

            "test_rows": int(
                len(X_test)
            ),

            "positive_train_rows": int(
                (y_train == 1).sum()
            ),

            "negative_train_rows": int(
                (y_train == 0).sum()
            ),

            "positive_test_rows": int(
                (y_test == 1).sum()
            ),

            "negative_test_rows": int(
                (y_test == 0).sum()
            ),

            "scale_pos_weight": float(
                scale_pos_weight
            ),

            "random_seed": int(
                seed
            ),

            "test_size": float(
                test_size
            ),

            "n_estimators": int(
                n_estimators
            ),

            "feature_columns": list(
                MODEL_FEATURE_COLUMNS
            ),
        }
    )

    model_path = (
        model_directory
        / MODEL_FILENAME
    )

    manifest_path = (
        model_directory
        / MANIFEST_FILENAME
    )

    metrics_path = (
        model_directory
        / METRICS_FILENAME
    )

    joblib.dump(
        model,
        model_path,
    )

    manifest = ModelManifest.create(
        model_version=MODEL_VERSION,

        feature_schema_version=(
            FEATURE_SCHEMA_VERSION
        ),

        feature_columns=(
            MODEL_FEATURE_COLUMNS
        ),

        model_filename=(
            MODEL_FILENAME
        ),

        metrics={
            "accuracy": metrics[
                "accuracy"
            ],

            "precision": metrics[
                "precision"
            ],

            "recall": metrics[
                "recall"
            ],

            "f1": metrics[
                "f1"
            ],

            "roc_auc": metrics[
                "roc_auc"
            ],
        },

        training_metadata={
            "dataset_version": (
                DATASET_VERSION
            ),

            "dataset_path": str(
                dataset_path
            ),

            "samples": int(
                samples
            ),

            "dataset_rows": int(
                len(dataframe)
            ),

            "random_seed": int(
                seed
            ),

            "test_size": float(
                test_size
            ),

            "scale_pos_weight": float(
                scale_pos_weight
            ),
        },
    )

    manifest.save(
        manifest_path
    )

    metrics_path.write_text(
        json.dumps(
            metrics,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "dataset_path": (
            dataset_path
        ),

        "model_path": (
            model_path
        ),

        "manifest_path": (
            manifest_path
        ),

        "metrics_path": (
            metrics_path
        ),

        "metrics": metrics,
    }


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Gera o dataset v2 e treina "
            "o XGBoost RCA v2."
        )
    )

    parser.add_argument(
        "--samples",
        type=int,
        default=5000,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--test-size",
        type=float,
        default=0.20,
    )

    parser.add_argument(
        "--n-estimators",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=DEFAULT_DATASET_PATH,
    )

    parser.add_argument(
        "--model-directory",
        type=Path,
        default=DEFAULT_MODEL_DIRECTORY,
    )

    return parser


def main() -> None:

    arguments = (
        build_parser()
        .parse_args()
    )

    result = train_and_save(
        samples=arguments.samples,
        seed=arguments.seed,
        test_size=(
            arguments.test_size
        ),
        n_estimators=(
            arguments.n_estimators
        ),
        dataset_path=(
            arguments.dataset_path
        ),
        model_directory=(
            arguments.model_directory
        ),
    )

    print(
        "\n========== TRAINING V2 =========="
    )

    print(
        f"Dataset: {result['dataset_path']}"
    )

    print(
        f"Model: {result['model_path']}"
    )

    print(
        f"Manifest: {result['manifest_path']}"
    )

    print(
        f"Metrics: {result['metrics_path']}"
    )

    metrics = result[
        "metrics"
    ]

    print(
        (
            "accuracy="
            f"{metrics['accuracy']:.6f}"
            " | precision="
            f"{metrics['precision']:.6f}"
            " | recall="
            f"{metrics['recall']:.6f}"
            " | f1="
            f"{metrics['f1']:.6f}"
            " | roc_auc="
            f"{metrics['roc_auc']}"
        )
    )


if __name__ == "__main__":
    main()