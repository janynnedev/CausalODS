from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import shap


class SHAPServiceV2:

    METHOD = (
        "tree-shap-v2"
    )

    def __init__(
        self,
        *,
        predictor,
    ) -> None:

        self.predictor = predictor

        self.model = (
            predictor.model
        )

        self.feature_columns = tuple(
            predictor.feature_columns
        )

        self.explainer = (
            shap.TreeExplainer(
                self.model,
                model_output="raw",
            )
        )

    def explain(
        self,
        features,
        predictions=None,
    ) -> list[dict[str, Any]]:

        if not features:
            return []

        dataframe = (
            self.predictor
            .to_dataframe(
                features
            )
        )

        probabilities = (
            self.predictor
            .predict_dataframe(
                dataframe
            )
        )

        shap_values, base_values = (
            self._calculate_shap(
                dataframe
            )
        )

        (
            shap_values,
            base_values,
            model_margins,
            reconstructed_margins,
            shap_orientation,
        ) = self._align_with_positive_class(
            dataframe=dataframe,
            shap_values=shap_values,
            base_values=base_values,
        )

        results = []

        for index, feature in enumerate(
            features
        ):

            service_name = (
                self.predictor
                ._service_name(
                    feature
                )
            )

            row_values = (
                shap_values[
                    index
                ]
            )

            positive_support = float(
                np.clip(
                    row_values,
                    0.0,
                    None,
                )
                .sum()
            )

            negative_support = float(
                np.abs(
                    np.clip(
                        row_values,
                        None,
                        0.0,
                    )
                )
                .sum()
            )

            total_support = (
                positive_support
                + negative_support
            )

            support_score = (
                positive_support
                / total_support
                if total_support > 0.0
                else 0.0
            )

            contributions = []

            for (
                feature_name,
                feature_value,
                shap_value,
            ) in zip(
                self.feature_columns,
                dataframe
                .iloc[
                    index
                ]
                .tolist(),
                row_values.tolist(),
                strict=True,
            ):

                numeric_shap_value = float(
                    shap_value
                )

                if numeric_shap_value > 0.0:

                    direction = (
                        "supporting"
                    )

                elif numeric_shap_value < 0.0:

                    direction = (
                        "opposing"
                    )

                else:

                    direction = (
                        "neutral"
                    )

                contributions.append(
                    {
                        "feature_name":
                            feature_name,

                        "feature_value":
                            float(
                                feature_value
                            ),

                        "shap_value":
                            numeric_shap_value,

                        "absolute_shap_value":
                            abs(
                                numeric_shap_value
                            ),

                        "direction":
                            direction,
                    }
                )

            contributions.sort(
                key=lambda item: (
                    item[
                        "absolute_shap_value"
                    ]
                ),
                reverse=True,
            )

            results.append(
                {
                    "service":
                        service_name,

                    "service_name":
                        service_name,

                    "support_score": float(
                        support_score
                    ),

                    "score": float(
                        support_score
                    ),

                    "shap_score": float(
                        support_score
                    ),

                    "shap_support_score": float(
                        support_score
                    ),

                    "raw_positive_support":
                        positive_support,

                    "raw_negative_support":
                        negative_support,

                    "base_value":
                        float(
                            base_values[
                                index
                            ]
                        ),

                    "model_margin":
                        float(
                            model_margins[
                                index
                            ]
                        ),

                    "reconstructed_margin":
                        float(
                            reconstructed_margins[
                                index
                            ]
                        ),

                    "additivity_error":
                        float(
                            abs(
                                model_margins[
                                    index
                                ]
                                - reconstructed_margins[
                                    index
                                ]
                            )
                        ),

                    "shap_orientation":
                        shap_orientation,    

                    "predicted_probability":
                        float(
                            probabilities[
                                index
                            ]
                        ),

                    "contributions":
                        contributions,

                    "model_version":
                        self.predictor
                        .model_version,

                    "feature_schema_version":
                        self.predictor
                        .feature_schema_version,

                    "method":
                        self.METHOD,
                }
            )

        return results

    def _align_with_positive_class(
        self,
        *,
        dataframe: pd.DataFrame,
        shap_values: np.ndarray,
        base_values: np.ndarray,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        str,
    ]:
        """
        Garante que os valores SHAP estejam orientados
        para a margem da classe positiva root_cause=1.

        Dependendo das versões de SHAP e XGBoost, uma
        matriz bidimensional pode representar a classe
        positiva ou a classe negativa. A orientação é
        determinada usando a propriedade de aditividade:

            margem = base_value + soma(shap_values)
        """

        try:

            model_margins = np.asarray(
                self.model.predict(
                    dataframe,
                    output_margin=True,
                ),
                dtype=float,
            ).reshape(
                -1
            )

        except TypeError:

            # Compatibilidade com versões em que a
            # wrapper sklearn não encaminha output_margin.
            import xgboost as xgb

            matrix = xgb.DMatrix(
                dataframe,
                feature_names=(
                    dataframe.columns
                    .tolist()
                ),
            )

            model_margins = np.asarray(
                self.model
                .get_booster()
                .predict(
                    matrix,
                    output_margin=True,
                ),
                dtype=float,
            ).reshape(
                -1
            )

        direct_reconstruction = (
            base_values
            + shap_values.sum(
                axis=1
            )
        )

        inverted_values = (
            -shap_values
        )

        inverted_base_values = (
            -base_values
        )

        inverted_reconstruction = (
            inverted_base_values
            + inverted_values.sum(
                axis=1
            )
        )

        direct_error = float(
            np.mean(
                np.abs(
                    direct_reconstruction
                    - model_margins
                )
            )
        )

        inverted_error = float(
            np.mean(
                np.abs(
                    inverted_reconstruction
                    - model_margins
                )
            )
        )

        if (
            inverted_error
            + 1e-8
            < direct_error
        ):

            selected_values = (
                inverted_values
            )

            selected_base_values = (
                inverted_base_values
            )

            reconstructed_margins = (
                inverted_reconstruction
            )

            orientation = (
                "inverted_to_positive_class"
            )

        else:

            selected_values = (
                shap_values
            )

            selected_base_values = (
                base_values
            )

            reconstructed_margins = (
                direct_reconstruction
            )

            orientation = (
                "direct_positive_class"
            )

        if not np.isfinite(
            model_margins
        ).all():

            raise ValueError(
                (
                    "O XGBoost retornou margens "
                    "não finitas."
                )
            )

        if not np.isfinite(
            reconstructed_margins
        ).all():

            raise ValueError(
                (
                    "A reconstrução SHAP retornou "
                    "margens não finitas."
                )
            )

        return (
            selected_values,
            selected_base_values,
            model_margins,
            reconstructed_margins,
            orientation,
        )

    def _calculate_shap(
        self,
        dataframe: pd.DataFrame,
    ) -> tuple[
        np.ndarray,
        np.ndarray,
    ]:

        try:

            explanation = (
                self.explainer(
                    dataframe
                )
            )

            raw_values = (
                explanation.values
            )

            raw_base_values = (
                explanation.base_values
            )

        except (
            TypeError,
            AttributeError,
        ):

            raw_values = (
                self.explainer
                .shap_values(
                    dataframe
                )
            )

            raw_base_values = (
                self.explainer
                .expected_value
            )

        values = (
            self._positive_class_values(
                raw_values=raw_values,
                rows=len(
                    dataframe
                ),
                columns=len(
                    self.feature_columns
                ),
            )
        )

        base_values = (
            self._normalize_base_values(
                raw_base_values=(
                    raw_base_values
                ),
                rows=len(
                    dataframe
                ),
            )
        )

        if values.shape != (
            len(dataframe),
            len(
                self.feature_columns
            ),
        ):

            raise ValueError(
                (
                    "SHAP retornou dimensões "
                    "incompatíveis. "
                    f"Esperado="
                    f"{(len(dataframe), len(self.feature_columns))}; "
                    f"encontrado={values.shape}."
                )
            )

        if not np.isfinite(
            values
        ).all():

            raise ValueError(
                (
                    "SHAP retornou valores "
                    "não finitos."
                )
            )

        return (
            values.astype(float),
            base_values.astype(float),
        )

    @staticmethod
    def _positive_class_values(
        *,
        raw_values,
        rows: int,
        columns: int,
    ) -> np.ndarray:

        if isinstance(
            raw_values,
            list,
        ):

            if not raw_values:

                raise ValueError(
                    (
                        "SHAP retornou lista "
                        "vazia."
                    )
                )

            raw_values = (
                raw_values[
                    -1
                ]
            )

        values = np.asarray(
            raw_values
        )

        if values.ndim == 2:

            return values

        if values.ndim != 3:

            raise ValueError(
                (
                    "Formato SHAP não "
                    "suportado: "
                    f"{values.shape}."
                )
            )

        if (
            values.shape[0] == rows
            and values.shape[1]
            == columns
        ):

            class_index = (
                1
                if values.shape[2] > 1
                else 0
            )

            return values[
                :,
                :,
                class_index,
            ]

        if (
            values.shape[1] == rows
            and values.shape[2]
            == columns
        ):

            class_index = (
                1
                if values.shape[0] > 1
                else 0
            )

            return values[
                class_index,
                :,
                :,
            ]

        raise ValueError(
            (
                "Não foi possível identificar "
                "a dimensão da classe positiva "
                f"nos valores SHAP: {values.shape}."
            )
        )

    @staticmethod
    def _normalize_base_values(
        *,
        raw_base_values,
        rows: int,
    ) -> np.ndarray:

        values = np.asarray(
            raw_base_values
        )

        if values.ndim == 0:

            return np.full(
                rows,
                float(values),
            )

        if values.ndim == 1:

            if len(values) == rows:

                return values.astype(
                    float
                )

            if len(values) >= 2:

                return np.full(
                    rows,
                    float(
                        values[-1]
                    ),
                )

        if values.ndim == 2:

            if values.shape[0] == rows:

                class_index = (
                    1
                    if values.shape[1] > 1
                    else 0
                )

                return values[
                    :,
                    class_index,
                ]

        return np.full(
            rows,
            float(
                values.reshape(
                    -1
                )[-1]
            ),
        )