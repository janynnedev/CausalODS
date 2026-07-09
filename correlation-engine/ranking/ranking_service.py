from ranking.evidence import RCAEvidence
from ranking.scorer import RCAScorer



class RCARankingService:


    def __init__(self):

        self.scorer = RCAScorer()



    def rank(

        self,

        predictions,

        shap_scores,

        temporal,

        graph

    ):


        ranking=[]



        for service,score in predictions.items():


            evidence = RCAEvidence(

                service_name=service,


                xgboost_score=score,


                shap_score=
                    shap_scores.get(
                        service,
                        0
                    ),


                temporal_score=

                    1
                    if temporal.get(service)

                    else 0,


                graph_score=

                    graph.get(
                        service,
                        0
                    )

            )



            evidence.final_score = (

                self.scorer.calculate(

                    evidence.xgboost_score,

                    evidence.shap_score,

                    evidence.temporal_score,

                    evidence.graph_score

                )

            )


            ranking.append(
                evidence
            )



        return sorted(

            ranking,

            key=lambda x:

                x.final_score,

            reverse=True

        )