import os
from pathlib import Path

import joblib
import pandas as pd

from config.settings import Settings
from models.rca_feature_schema import (
    BOOLEAN_FEATURE_COLUMNS,
    FLOAT_FEATURE_COLUMNS,
    INTEGER_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

class Predictor:
    """
    Executa inferência utilizando o modelo XGBoost treinado.
    """



    def __init__(
        self,
        model_path: str | Path | None = None,
    ):

        configured_path = (
            model_path
            if model_path is not None
            else Settings.MODEL_PATH
        )

        self.model_path = self._resolve_model_path(
            configured_path
        )

        self.model = self.load_model()


    def _resolve_model_path(
        self,
        model_path: str | Path,
    ) -> Path:

        path = Path(model_path).expanduser()

        if not path.is_absolute():
            path = PROJECT_ROOT / path

        return path.resolve()


    def load_model(self):

        model_path = self.model_path

        if not model_path.is_file():
            raise FileNotFoundError(
                "Modelo XGBoost não encontrado. "
                f"Caminho resolvido: {model_path}. "
                f"Diretório de execução: {Path.cwd()}."
            )


        return joblib.load(
            model_path
        )



    def predict(
        self,
        features
    ):
        """
        Recebe lista de RCAFeature
        e retorna predições por serviço.
        """


        if not features:

            return []



        dataframe = (
            self.to_dataframe(
                features
            )
        )


        probabilities = (
            self.model.predict_proba(
                dataframe
            )
        )

        predicted_classes = (
            self.model.predict(
                dataframe
            )
        )


        results = []



        for feature, probability, predicted_class in zip(
            features,
            probabilities,
            predicted_classes
        ):


            # classe positiva
            confidence = (
                float(
                    probability[1]
                )
                if len(probability) > 1

                else float(
                    probability[0]
                )
            )



            results.append(

                {

                    "service":
                        feature.service_name,


                    "prediction":
                        int(predicted_class),


                    "confidence":
                        confidence,


                    "features":
                        feature.to_dict()

                }

            )

        return results
    
    
    def to_dataframe(
        self,
        features,
    ):
        """
        Converte RCAFeature para o schema canônico
        usado no treinamento.
        """

        rows = []

        for feature in features:

            row = {
                column: getattr(
                    feature,
                    column,
                )
                for column in MODEL_FEATURE_COLUMNS
            }

            rows.append(
                row
            )

        dataframe = pd.DataFrame(
            rows,
            columns=list(
                MODEL_FEATURE_COLUMNS
            ),
        )

        for column in BOOLEAN_FEATURE_COLUMNS:

            dataframe[column] = (
                dataframe[column]
                .fillna(False)
                .astype(int)
            )

        for column in INTEGER_FEATURE_COLUMNS:

            dataframe[column] = pd.to_numeric(
                dataframe[column],
                errors="raise",
            ).astype(int)

        for column in FLOAT_FEATURE_COLUMNS:

            dataframe[column] = pd.to_numeric(
                dataframe[column],
                errors="raise",
            ).astype(float)

        return dataframe




    