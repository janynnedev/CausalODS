from __future__ import annotations

from typing import Any

from training.predictor import Predictor
from shap_analysis.shap_service import SHAPService


class PredictionPipeline:
    """
    Responsável exclusivamente por:

    1. executar a predição do modelo;
    2. calcular as explicações SHAP.

    O ranking é responsabilidade da RCAPipeline, pois precisa
    combinar evidências temporais, estruturais, de anomalia,
    predição e SHAP.
    """

    def __init__(
        self,
        predictor: Predictor | None = None,
        shap_service: SHAPService | None = None,
    ) -> None:

        self.predictor = (
            predictor
            if predictor is not None
            else Predictor()
        )

        if self.predictor.model is None:
            raise RuntimeError(
                "O Predictor foi inicializado, mas nenhum "
                "modelo foi carregado."
            )

        self.shap_service = (
            shap_service
            if shap_service is not None
            else SHAPService(
                model=self.predictor.model
            )
        )

    def predict(
        self,
        features: Any,
    ) -> Any:
        """
        Executa somente a predição do modelo.
        """

        return self.predictor.predict(
            features
        )

    def explain(
        self,
        features: Any,
        predictions: Any,
    ) -> Any:
        """
        Calcula os resultados SHAP associados à predição.
        """

        return self.shap_service.explain(
            features,
            predictions,
        )

    def process(
        self,
        features: Any,
    ) -> tuple[Any, Any]:
        """
        Executa predição e SHAP, preservando os dois resultados
        separadamente para a construção do RCAContext.

        Retorno:
            predictions, shap_results
        """

        predictions = self.predict(
            features
        )

        shap_results = self.explain(
            features=features,
            predictions=predictions,
        )

        return (
            predictions,
            shap_results,
        )