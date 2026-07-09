class RankingFormatter:



    def format(

        self,

        ranking

    ):


        return [

            {

            "service":
                item.service_name,


            "confidence":
                round(
                    item.final_score,
                    3
                ),


            "evidence":{

                "xgboost":
                    item.xgboost_score,

                "shap":
                    item.shap_score,

                "temporal":
                    item.temporal_score,

                "graph":
                    item.graph_score

            }

            }

            for item in ranking

        ]