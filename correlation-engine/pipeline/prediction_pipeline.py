from training.predictor import Predictor

from shap_analysis.shap_service import SHAPService

from ranking.ranking_service import RankingService



class PredictionPipeline:


    def __init__(self):

        self.predictor = Predictor()

        self.shap_service = SHAPService()

        self.ranking = RankingService()



    def predict(
        self,
        features
    ):


        predictions = (
            self.predictor.predict(
                features
            )
        )


        explanations = (
            self.shap_service.explain(
                features,
                predictions
            )
        )


        ranked = (
            self.ranking.rank(
                predictions,
                explanations
            )
        )


        return ranked