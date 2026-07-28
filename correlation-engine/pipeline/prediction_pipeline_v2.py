from __future__ import annotations

from pathlib import Path

from shap_analysis.shap_service_v2 import (
    SHAPServiceV2,
)
from training.predictor_v2 import (
    PredictorV2,
)


class PredictionPipelineV2:

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

        self.predictor = (
            PredictorV2(
                model_path=model_path,
                manifest_path=(
                    manifest_path
                ),
            )
        )

        self.shap_service = (
            SHAPServiceV2(
                predictor=(
                    self.predictor
                )
            )
        )

    def predict(
        self,
        features,
    ):

        return (
            self.predictor
            .predict(
                features
            )
        )

    def explain(
        self,
        features,
        predictions,
    ):

        return (
            self.shap_service
            .explain(
                features,
                predictions,
            )
        )

    def process(
        self,
        features,
    ):

        predictions = (
            self.predict(
                features
            )
        )

        shap_results = (
            self.explain(
                features,
                predictions,
            )
        )

        return (
            predictions,
            shap_results,
        )