import json
from pathlib import Path

import pandas as pd
from sklearn.model_selection import (
    GroupShuffleSplit,
)

from config.settings import Settings
from dataset.dataset_builder import (
    DatasetBuilder,
)
from dataset.exporter import (
    DatasetExporter,
)
from training.evaluator import (
    Evaluator,
)
from training.model_registry import (
    ModelRegistry,
)
from training.preprocessing import (
    Preprocessor,
)
from training.train import (
    XGBoostTrainer,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

DATASET_PATH = (
    PROJECT_ROOT
    / "results_rca_dataset.csv"
)

METRICS_PATH = (
    PROJECT_ROOT
    / "training"
    / "models"
    / "xgboost_rca_metrics.json"
)


def main():

    random_state = 42

    builder = DatasetBuilder(
        seed=random_state
    )

    dataset = builder.generate(
        samples=2000
    )

    DatasetExporter().export(
        dataset=dataset,
        filename=str(
            DATASET_PATH
        ),
    )

    dataframe = pd.DataFrame(
        dataset
    )

    groups = dataframe[
        "sample_id"
    ]

    splitter = GroupShuffleSplit(
        n_splits=1,
        test_size=0.20,
        random_state=random_state,
    )

    train_indexes, test_indexes = next(
        splitter.split(
            dataframe,
            groups=groups,
        )
    )

    train_dataframe = (
        dataframe.iloc[
            train_indexes
        ]
        .reset_index(
            drop=True
        )
    )

    test_dataframe = (
        dataframe.iloc[
            test_indexes
        ]
        .reset_index(
            drop=True
        )
    )

    preprocessor = Preprocessor()

    X_train, y_train = (
        preprocessor.prepare(
            train_dataframe
        )
    )

    X_test, y_test = (
        preprocessor.prepare(
            test_dataframe
        )
    )

    trainer = XGBoostTrainer(
        random_state=random_state
    )

    model = trainer.train(
        X_train=X_train,
        y_train=y_train,
    )

    evaluator = Evaluator()

    metrics = evaluator.evaluate(
        model=model,
        X_test=X_test,
        y_test=y_test,
    )

    registry = ModelRegistry()

    registry.save(
        model=model,
        path=Settings.MODEL_PATH,
    )

    METRICS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    METRICS_PATH.write_text(
        json.dumps(
            metrics,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "Dataset:",
        DATASET_PATH,
    )

    print(
        "Modelo:",
        Settings.MODEL_PATH,
    )

    print(
        "Amostras de treino:",
        len(X_train),
    )

    print(
        "Amostras de teste:",
        len(X_test),
    )

    print(
        "Features:",
        X_train.columns.tolist(),
    )

    print(
        "Métricas:"
    )

    print(
        json.dumps(
            metrics,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()