from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from sklearn.model_selection import (
    GroupShuffleSplit,
)

from models.rca_feature_schema_v2 import (
    BOOLEAN_FEATURE_COLUMNS,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    TARGET_COLUMN,
)


class TrainingPreprocessorV2:
    """
    Prepara e divide o dataset v2.

    A divisão ocorre por sample_id, não por linha.
    Isso impede que order-service de uma amostra
    fique no treino e payment-service da mesma
    amostra fique no teste.
    """

    REQUIRED_COLUMNS = (
        "sample_id",
        *MODEL_FEATURE_COLUMNS,
        TARGET_COLUMN,
    )

    def prepare(
        self,
        dataframe: pd.DataFrame,
    ) -> tuple[
        pd.DataFrame,
        pd.Series,
    ]:

        self._validate_required_columns(
            dataframe
        )

        features = dataframe.loc[
            :,
            list(
                MODEL_FEATURE_COLUMNS
            ),
        ].copy()

        target = pd.to_numeric(
            dataframe[
                TARGET_COLUMN
            ],
            errors="raise",
        ).astype(int)

        for column in (
            BOOLEAN_FEATURE_COLUMNS
        ):

            features[column] = (
                features[column]
                .fillna(False)
                .astype(int)
            )

        for column in (
            INTEGER_FEATURE_COLUMNS
        ):

            features[column] = (
                pd.to_numeric(
                    features[column],
                    errors="raise",
                )
                .astype(int)
            )

        for column in (
            FLOAT_FEATURE_COLUMNS
        ):

            features[column] = (
                pd.to_numeric(
                    features[column],
                    errors="raise",
                )
                .astype(float)
            )

        if features.isna().any().any():

            raise ValueError(
                (
                    "Features v2 contêm "
                    "valores nulos."
                )
            )

        numeric_values = (
            features.to_numpy(
                dtype=float
            )
        )

        if not np.isfinite(
            numeric_values
        ).all():

            raise ValueError(
                (
                    "Features v2 contêm "
                    "valores não finitos."
                )
            )

        unexpected_targets = (
            set(
                target.unique()
            )
            - {0, 1}
        )

        if unexpected_targets:

            raise ValueError(
                (
                    "Target deve ser binário. "
                    f"Valores encontrados: "
                    f"{sorted(unexpected_targets)}."
                )
            )

        return (
            features,
            target,
        )

    def split(
        self,
        dataframe: pd.DataFrame,
        *,
        test_size: float = 0.20,
        random_state: int = 42,
    ) -> dict[
        str,
        Any,
    ]:

        if not (
            0.0 < test_size < 1.0
        ):

            raise ValueError(
                (
                    "test_size deve estar "
                    "entre 0 e 1."
                )
            )

        self._validate_required_columns(
            dataframe
        )

        groups = dataframe[
            "sample_id"
        ].astype(str)

        splitter = GroupShuffleSplit(
            n_splits=1,
            test_size=test_size,
            random_state=random_state,
        )

        train_indices, test_indices = next(
            splitter.split(
                dataframe,
                groups=groups,
            )
        )

        train_dataframe = (
            dataframe.iloc[
                train_indices
            ]
            .reset_index(
                drop=True
            )
        )

        test_dataframe = (
            dataframe.iloc[
                test_indices
            ]
            .reset_index(
                drop=True
            )
        )

        train_groups = set(
            train_dataframe[
                "sample_id"
            ]
        )

        test_groups = set(
            test_dataframe[
                "sample_id"
            ]
        )

        overlap = (
            train_groups
            & test_groups
        )

        if overlap:

            raise RuntimeError(
                (
                    "Vazamento entre treino e teste. "
                    f"sample_ids sobrepostos: "
                    f"{sorted(overlap)[:10]}."
                )
            )

        X_train, y_train = (
            self.prepare(
                train_dataframe
            )
        )

        X_test, y_test = (
            self.prepare(
                test_dataframe
            )
        )

        return {
            "X_train": X_train,
            "X_test": X_test,
            "y_train": y_train,
            "y_test": y_test,
            "train_dataframe": (
                train_dataframe
            ),
            "test_dataframe": (
                test_dataframe
            ),
        }

    @classmethod
    def _validate_required_columns(
        cls,
        dataframe: pd.DataFrame,
    ) -> None:

        missing = (
            set(
                cls.REQUIRED_COLUMNS
            )
            - set(
                dataframe.columns
            )
        )

        if missing:

            raise ValueError(
                (
                    "Dataset v2 não contém "
                    "todas as colunas obrigatórias: "
                    f"{sorted(missing)}."
                )
            )