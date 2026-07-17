from graph.graph_builder import GraphBuilder

from correlation.temporal_correlator import (
    TemporalCorrelator
)

from pipeline.feature_pipeline import (
    FeaturePipeline
)


from pipeline.prediction_pipeline import PredictionPipeline
from pipeline.rca_context_factory import RCAContextFactory
from pipeline.rca_result_factory import RCAResultFactory
from ranking.ranking_service import RCARankingService
from storage.incident_service import IncidentService
from models.rca_result import RCAResult

class RCAPipeline:

    def __init__(
        self,
        trace_repository,
        incident_repository=None,
    ):

        self.graph_builder = GraphBuilder()

        self.temporal_correlator = (
            TemporalCorrelator()
        )

        self.feature_pipeline = (
            FeaturePipeline()
        )

        self.prediction_pipeline = (
            PredictionPipeline()
        )

        self.context_factory = (
            RCAContextFactory()
        )

        self.ranking_service = (
            RCARankingService()
        )

        self.result_factory = (
            RCAResultFactory(
                ranking_service=(
                    self.ranking_service
                )
            )
        )

        self.trace_repository = (
            trace_repository
        )

        self.incident_repository = (
            incident_repository
            if incident_repository is not None
            else trace_repository
        )

        self.incident_service = (
            IncidentService(
                repository=self.incident_repository
            )
        )
        

    def process(
        self,
        trace,
    ):

        graph = self.build_graph(
            trace
        )

        correlation_result = (
            self.build_temporal(
                trace
            )
        )

        features = self.build_features(
            trace,
            graph,
            correlation_result,
        )

        rca_execution = self.run_rca(
            trace=trace,
            graph=graph,
            temporal_results=(
                correlation_result
            ),
            features=features,
        )

        rca_result = (
            rca_execution[
                "rca_result"
            ]
        )

        incident = self.build_incident(
            trace=trace,
            rca_result=rca_result,
        )

        # Preserve aqui os nomes reais dos seus métodos
        # de persistência.
        self.persist_trace(
            trace
        )

        if incident is not None:

            self.persist_incident(
                incident
            )

        return {
            "trace": trace,
            "graph": graph,
            "temporal": (
                correlation_result
            ),
            "features": features,
            "predictions": (
                rca_execution[
                    "predictions"
                ]
            ),
            "shap_results": (
                rca_execution[
                    "shap_results"
                ]
            ),
            "rca_context": (
                rca_execution[
                    "context"
                ]
            ),
            "rca_result": rca_result,
            "incident": incident,
        }


    def build_graph(
        self,
        trace
    ):

        graph = (
            self.graph_builder
            .build(trace)
        )

        print()

        print(
            "========== SERVICE GRAPH =========="
        )

        print(
            graph.to_dict()
        )

        return graph


    def build_temporal(
        self,
        trace
    ):

        correlation_result = (
            self.temporal_correlator
            .analyze(trace)
        )

        print()

        print(
            "========== TEMPORAL =========="
        )

        print(
            correlation_result
        )

        return correlation_result


    def build_features(

        self,

        trace,

        graph,

        correlation_result

    ):

        features = (
            self.feature_pipeline
            .build(

                trace,

                graph,

                correlation_result

            )
        )

        print()

        print(
            "========== FEATURES =========="
        )

        for feature in features:

            print(feature)

        return features
    
    def run_rca(
        self,
        *,
        trace,
        graph,
        temporal_results,
        features,
    ):

        predictions, shap_results = (
            self.prediction_pipeline.process(
                features
            )
        )

        context = (
            self.context_factory.from_legacy(
                trace=trace,
                service_graph=graph,
                feature_vectors=features,
                temporal_results=(
                    temporal_results
                ),
                predictions=predictions,
                shap_results=shap_results,
                model_version=(
                    self._get_model_version()
                ),
                pipeline_version=(
                    "causalods-rca-v1"
                ),
            )
        )

        rca_result = (
            self.result_factory.build(
                context
            )
        )

        return {
            "context": context,
            "predictions": predictions,
            "shap_results": shap_results,
            "ranking": (
                rca_result.ranking
            ),
            "evidence": (
                rca_result.evidence
            ),
            "reliability": (
                rca_result.reliability
            ),
            "rca_result": rca_result,
        }

    def persist(
        self,
        trace
    ):

        self.trace_repository.save(
            trace.to_dict()
        )

    def build_incident(
        self,
        *,
        trace,
        rca_result: RCAResult,
    ):

        if not (
            rca_result.reliability
            .is_reliable
        ):
            return None

        if not rca_result.ranking:
            return None

        top_service = (
            rca_result.ranking[0]
        )

        service_evidence = (
            rca_result
            .evidence_for_service(
                top_service.service_name
            )
        )

        legacy_ranking_item = {
            "service": (
                top_service.service_name
            ),
            "confidence": (
                top_service
                .scores
                .final_score
            ),
            "evidence": {
                "rca_result_id": (
                    rca_result.rca_result_id
                ),
                "trace_id": (
                    rca_result.trace_id
                ),
                "reliability": (
                    rca_result
                    .reliability
                    .to_dict()
                ),
                "score_breakdown": (
                    top_service
                    .scores
                    .to_dict()
                ),
                "items": [
                    item.to_dict()
                    for item
                    in service_evidence
                ],
            },
        }

        return (
            self.incident_service
            .create_incident(
                legacy_ranking_item
            )
        )
    
    def _get_model_version(self) -> str:

        predictor = (
            self.prediction_pipeline.predictor
        )

        for attribute_name in (
            "model_version",
            "version",
        ):

            value = getattr(
                predictor,
                attribute_name,
                None,
            )

            if value:
                return str(value)

        return "xgboost-rca-v1"    