from __future__ import annotations

import math

from collections.abc import (
    Mapping,
    Sequence,
)
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import Settings
from models.rca_feature_schema_v2 import (
    BOOLEAN_FEATURE_COLUMNS,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
)
from training.artifact_validator_v2 import (
    ArtifactValidatorV2,
)


class PredictorV2:

    def __init__(
        self,
        *,
        model_path: (
            str | Path | None
        ) = None,
        manifest_path: (
            str | Path | None
        ) = None,
    ) -> None:

        configured_model_path = (
            model_path
            if model_path is not None
            else getattr(
                Settings,
                "MODEL_PATH_V2",
                (
                    "training/models/"
                    "xgboost_rca_v2.pkl"
                ),
            )
        )

        configured_manifest_path = (
            manifest_path
            if manifest_path is not None
            else getattr(
                Settings,
                "MODEL_MANIFEST_PATH_V2",
                (
                    "training/models/"
                    "xgboost_rca_v2.manifest.json"
                ),
            )
        )

        self.validator = (
            ArtifactValidatorV2()
        )

        self.bundle = (
            self.validator
            .load_and_validate(
                model_path=(
                    configured_model_path
                ),
                manifest_path=(
                    configured_manifest_path
                ),
            )
        )

        self.model = (
            self.bundle.model
        )

        self.model_path = (
            self.bundle.model_path
        )

        self.manifest_path = (
            self.bundle.manifest_path
        )

        self.manifest = (
            self.bundle.manifest
        )

        self.model_version = (
            self.bundle.model_version
        )

        self.feature_schema_version = (
            self.bundle
            .feature_schema_version
        )

        self.feature_columns = (
            self.bundle.feature_columns
        )

    def predict(
        self,
        features: Sequence[Any],
    ) -> list[dict[str, Any]]:

        if not features:
            return []

        dataframe = (
            self.to_dataframe(
                features
            )
        )

        probabilities = (
            self.predict_dataframe(
                dataframe
            )
        )

        predicted_classes = (
            self.model.predict(
                dataframe
            )
        )

        results = []

        for (
            feature,
            probability,
            predicted_class,
        ) in zip(
            features,
            probabilities,
            predicted_classes,
            strict=True,
        ):

            service_name = (
                self._service_name(
                    feature
                )
            )

            results.append(
                {
                    "service":
                        service_name,

                    "service_name":
                        service_name,

                    "prediction":
                        int(
                            predicted_class
                        ),

                    "confidence":
                        float(
                            probability
                        ),

                    "probability":
                        float(
                            probability
                        ),

                    "features":
                        self._feature_payload(
                            feature
                        ),

                    "model_version":
                        self.model_version,

                    "feature_schema_version":
                        self.feature_schema_version,
                }
            )

        return results

    def predict_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> np.ndarray:

        normalized = (
            self.validate_dataframe(
                dataframe
            )
        )

        probabilities = (
            self.model.predict_proba(
                normalized
            )
        )

        if (
            probabilities.ndim != 2
            or probabilities.shape[0]
            != len(normalized)
        ):

            raise ValueError(
                (
                    "predict_proba retornou "
                    "dimensão inesperada: "
                    f"{probabilities.shape}."
                )
            )

        classes = list(
            getattr(
                self.model,
                "classes_",
                (),
            )
        )

        if classes and 1 in classes:

            positive_index = (
                classes.index(1)
            )

        else:

            positive_index = (
                probabilities.shape[1]
                - 1
            )

        positive_probabilities = (
            probabilities[
                :,
                positive_index,
            ]
            .astype(float)
        )

        if not np.isfinite(
            positive_probabilities
        ).all():

            raise ValueError(
                (
                    "O modelo retornou "
                    "probabilidades não finitas."
                )
            )

        return positive_probabilities

    def to_dataframe(
        self,
        features: Sequence[Any],
    ) -> pd.DataFrame:

        rows = []

        for feature in features:

            row = {}

            for column in self.feature_columns:

                value = (
                    self._feature_value(
                        feature,
                        column,
                    )
                )

                row[
                    column
                ] = value

            rows.append(
                row
            )

        dataframe = pd.DataFrame(
            rows,
            columns=list(
                self.feature_columns
            ),
        )

        return self.validate_dataframe(
            dataframe
        )

    def validate_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> pd.DataFrame:

        actual_columns = (
            dataframe.columns
            .tolist()
        )

        expected_columns = list(
            self.feature_columns
        )

        if (
            actual_columns
            != expected_columns
        ):

            missing_columns = [
                column
                for column
                in expected_columns
                if column
                not in actual_columns
            ]

            extra_columns = [
                column
                for column
                in actual_columns
                if column
                not in expected_columns
            ]

            raise ValueError(
                (
                    "DataFrame incompatível "
                    "com o modelo v2. "
                    f"missing={missing_columns}; "
                    f"extra={extra_columns}; "
                    f"expected_order={expected_columns}; "
                    f"actual_order={actual_columns}."
                )
            )

        normalized = (
            dataframe.copy()
        )

        for column in (
            BOOLEAN_FEATURE_COLUMNS
        ):

            normalized[
                column
            ] = (
                normalized[
                    column
                ]
                .fillna(False)
                .astype(int)
            )

        for column in (
            INTEGER_FEATURE_COLUMNS
        ):

            normalized[
                column
            ] = (
                pd.to_numeric(
                    normalized[
                        column
                    ],
                    errors="raise",
                )
                .astype(int)
            )

        for column in (
            FLOAT_FEATURE_COLUMNS
        ):

            normalized[
                column
            ] = (
                pd.to_numeric(
                    normalized[
                        column
                    ],
                    errors="raise",
                )
                .astype(float)
            )

        if (
            normalized
            .isna()
            .any()
            .any()
        ):

            null_columns = (
                normalized.columns[
                    normalized
                    .isna()
                    .any()
                ]
                .tolist()
            )

            raise ValueError(
                (
                    "Features v2 contêm "
                    "valores nulos: "
                    f"{null_columns}."
                )
            )

        numeric_values = (
            normalized
            .to_numpy(
                dtype=float
            )
        )

        if not np.isfinite(
            numeric_values
        ).all():

            raise ValueError(
                (
                    "Features v2 contêm "
                    "NaN ou Infinity."
                )
            )

        return normalized

    @staticmethod
    def _feature_value(
        feature: Any,
        column: str,
    ) -> Any:

        if isinstance(
            feature,
            Mapping,
        ):

            if column not in feature:

                raise ValueError(
                    (
                        "Feature obrigatória "
                        f"ausente: {column!r}."
                    )
                )

            value = feature[
                column
            ]

        else:

            if not hasattr(
                feature,
                column,
            ):

                raise ValueError(
                    (
                        "Objeto de feature não "
                        "possui o campo "
                        f"{column!r}."
                    )
                )

            value = getattr(
                feature,
                column,
            )

        if value is Ellipsis:

            raise ValueError(
                (
                    "Feature contém placeholder "
                    f"não implementado: {column}."
                )
            )

        if isinstance(
            value,
            float,
        ) and not math.isfinite(
            value
        ):

            raise ValueError(
                (
                    "Feature não finita: "
                    f"{column}={value}."
                )
            )

        return value

    @staticmethod
    def _service_name(
        feature: Any,
    ) -> str:

        if isinstance(
            feature,
            Mapping,
        ):

            value = (
                feature.get(
                    "service_name"
                )
                or feature.get(
                    "service"
                )
            )

        else:

            value = getattr(
                feature,
                "service_name",
                None,
            )

        if not value:

            raise ValueError(
                (
                    "Feature não contém "
                    "service_name."
                )
            )

        return str(value)

    @staticmethod
    def _feature_payload(
        feature: Any,
    ) -> dict[str, Any]:

        if isinstance(
            feature,
            Mapping,
        ):

            return dict(
                feature
            )

        to_dict = getattr(
            feature,
            "to_dict",
            None,
        )

        if callable(
            to_dict
        ):

            return dict(
                to_dict()
            )

        return dict(
            vars(
                feature
            )
        )