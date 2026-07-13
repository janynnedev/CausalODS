import os

import joblib
import pandas as pd

from config.settings import Settings



class Predictor:
    """
    Executa inferência utilizando o modelo XGBoost treinado.
    """



    def __init__(self):

        self.model = self.load_model()



    def load_model(self):

        model_path = (
            Settings.MODEL_PATH
        )


        if not os.path.exists(model_path):

            raise FileNotFoundError(
                f"Modelo não encontrado: {model_path}"
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



        results = []



        for feature, probability in zip(
            features,
            probabilities
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



            prediction = (
                int(
                    self.model.predict(
                        dataframe[
                            dataframe.index[
                                len(results)
                            ]
                        :
                            dataframe.index[
                                len(results)
                            ] + 1
                        ]
                    )[0]
                )
            )



            results.append(

                {

                    "service":
                        feature.service_name,


                    "prediction":
                        prediction,


                    "confidence":
                        confidence,


                    "features":
                        feature.to_dict()

                }

            )



        return results




    def to_dataframe(
        self,
        features
    ):
        """
        Converte RCAFeature para dataframe
        compatível com o treinamento.
        """


        rows = []


        for feature in features:

            rows.append(

                {

                    "request_count":
                        feature.request_count,


                    "error_count":
                        feature.error_count,


                    "error_rate":
                        feature.error_rate,


                    "avg_latency_ms":
                        feature.avg_latency_ms,


                    "max_latency_ms":
                        feature.max_latency_ms,


                    "incoming_dependencies":
                        feature.incoming_dependencies,


                    "outgoing_dependencies":
                        feature.outgoing_dependencies,


                    "dependency_count":
                        feature.dependency_count,


                    "is_first_failure":
                        int(
                            feature.is_first_failure
                        ),


                    "failure_order":
                        feature.failure_order,


                    "failure_lead_time_ms":
                        feature.failure_lead_time_ms,


                    "latency_zscore":
                        feature.latency_zscore,


                    "anomaly_score":
                        feature.anomaly_score,


                    "is_anomalous":
                        int(
                            feature.is_anomalous
                        )

                }

            )


        return pd.DataFrame(
            rows
        )