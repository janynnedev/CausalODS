class RCAScorer:


    def calculate(

        self,

        xgboost_score,

        shap_score,

        temporal_score,

        graph_score

    ):


        final = (

            0.4 * xgboost_score

            +

            0.25 * shap_score

            +

            0.20 * temporal_score

            +

            0.15 * graph_score

        )


        return final