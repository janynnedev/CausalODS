from typing import Any

import features
from shap_analysis.shap_service import SHAPService
from training.predictor import Predictor


class PredictionPipeline:

    def __init__(
        self,
        predictor=None,
        shap_service=None,
    ):

        self.predictor = (
            predictor
            if predictor is not None
            else Predictor()
        )

        if self.predictor.model is None:
            raise RuntimeError(
                "O Predictor foi inicializado, "
                "mas nenhum modelo foi carregado."
            )

        self.shap_service = (
            shap_service
            if shap_service is not None
            else SHAPService(
                self.predictor.model
            )
        )

    def predict(
        self,
        features: Any,
    ) -> Any:

        return self.predictor.predict(
            features
        )

    def explain(
        self,
        features
    ) -> Any:

        dataframe = (
            self.predictor
            .to_dataframe(
                features
            )
        )

        return self.shap_service.explain(
            dataframe
        )

    def process(
        self,
        features,
    ):

        predictions = self.predict(
            features
        )

        shap_results = self.explain(
            features
        )

        return (
            predictions,
            shap_results,
        )