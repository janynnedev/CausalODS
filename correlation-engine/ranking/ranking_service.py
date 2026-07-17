from models.evidence import EvidenceItem
from models.rca_context import RCAContext
from models.rca_result import RankedService, ScoreBreakdown
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
    
    def rank_context(
        self,
        context: RCAContext,
        evidence: tuple[
            EvidenceItem,
            ...,
        ],
    ) -> tuple[
        RankedService,
        ...,
    ]:
        """
        Novo ranking canônico.

        Diferentemente do método legado, consome scores contínuos
        e produz RankedService tipado.
        """

        evidence_ids_by_service: dict[
            str,
            list[str],
        ] = {
            service_name: []
            for service_name
            in context.services
        }

        for evidence_item in evidence:

            service_name = getattr(
                evidence_item,
                "service_name",
                None,
            )

            evidence_id = getattr(
                evidence_item,
                "evidence_id",
                None,
            )

            if (
                service_name
                in evidence_ids_by_service
                and evidence_id
            ):
                evidence_ids_by_service[
                    service_name
                ].append(
                    str(evidence_id)
                )

        temporary_ranking = []

        for service_name in context.services:

            prediction_payload = (
                context.predictions.get(
                    service_name,
                    {},
                )
            )

            shap_payload = (
                context.shap_results.get(
                    service_name,
                    {},
                )
            )

            temporal_payload = (
                context.temporal_results.get(
                    service_name,
                    {},
                )
            )

            graph_payload = (
                context.graph_results.get(
                    service_name,
                    {},
                )
            )

            anomaly_payload = (
                context.anomaly_results.get(
                    service_name,
                    {},
                )
            )

            model_probability = float(
                prediction_payload.get(
                    "probability",
                    0.0,
                )
            )

            shap_support_score = float(
                shap_payload.get(
                    "support_score",
                    0.0,
                )
            )

            temporal_score = float(
                temporal_payload.get(
                    "score",
                    0.0,
                )
            )

            graph_score = float(
                graph_payload.get(
                    "score",
                    0.0,
                )
            )

            anomaly_score = float(
                anomaly_payload.get(
                    "score",
                    0.0,
                )
            )

            final_score = (
                self.scorer.score_components(
                    model_probability=(
                        model_probability
                    ),
                    shap_support=(
                        shap_support_score
                    ),
                    temporal=(
                        temporal_score
                    ),
                    graph=graph_score,
                    anomaly=anomaly_score,
                )
            )

            scores = ScoreBreakdown(
                model_probability=(
                    model_probability
                ),
                shap_support_score=(
                    shap_support_score
                ),
                temporal_score=(
                    temporal_score
                ),
                graph_score=graph_score,
                anomaly_score=(
                    anomaly_score
                ),
                final_score=final_score,
                component_weights=(
                    self.scorer.weights.to_dict()
                ),
                normalization_method=(
                    "normalized_weighted_ranking_v1"
                ),
            )

            temporary_ranking.append(
                {
                    "service_name": (
                        service_name
                    ),
                    "scores": scores,
                    "evidence_ids": tuple(
                        evidence_ids_by_service.get(
                            service_name,
                            (),
                        )
                    ),
                }
            )

        temporary_ranking.sort(
            key=lambda item: (
                -item[
                    "scores"
                ].final_score,
                -item[
                    "scores"
                ].model_probability,
                item["service_name"],
            )
        )

        return tuple(
            RankedService(
                position=position,
                service_name=item[
                    "service_name"
                ],
                scores=item["scores"],
                evidence_ids=item[
                    "evidence_ids"
                ],
            )
            for position, item
            in enumerate(
                temporary_ranking,
                start=1,
            )
        )