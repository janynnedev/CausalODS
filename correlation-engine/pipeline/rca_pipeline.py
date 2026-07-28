from graph.graph_builder import GraphBuilder

from correlation.temporal_correlator import (
    TemporalCorrelator
)

import incident
from pipeline.feature_pipeline import (
    FeaturePipeline
)


from pipeline.prediction_pipeline import PredictionPipeline
from pipeline.rca_context_factory import RCAContextFactory
from pipeline.rca_result_factory import RCAResultFactory
from ranking.ranking_service import RCARankingService
from storage.incident_service import IncidentService
from models.rca_result import RCAResult
from pprint import pprint
from config.settings import Settings

from pipeline.prediction_pipeline_v2 import (
    PredictionPipelineV2,
)

class RCAPipeline:

    def __init__(
        self,
        trace_repository,
        incident_repository,
        rca_result_repository,
    ):

        self.graph_builder = GraphBuilder()

        self.temporal_correlator = (
            TemporalCorrelator()
        )

        self.feature_pipeline = (
            FeaturePipeline()
        )

        self.model_runtime_version = (
            Settings.RCA_MODEL_VERSION
        )

        if (
            self.model_runtime_version
            == "v1"
        ):

            self.prediction_pipeline = (
                PredictionPipeline()
            )

        elif (
            self.model_runtime_version
            == "v2"
        ):

            self.prediction_pipeline = (
                PredictionPipelineV2(
                    model_path=(
                        Settings.MODEL_PATH_V2
                    ),
                    manifest_path=(
                        Settings
                        .MODEL_MANIFEST_PATH_V2
                    ),
                )
            )

        else:

            raise ValueError(
                (
                    "RCA_MODEL_VERSION inválida. "
                    "Valores permitidos: v1, v2. "
                    "Valor recebido: "
                    f"{self.model_runtime_version!r}."
                )
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

        if rca_result_repository is None:

            raise ValueError(
                "rca_result_repository é obrigatório "
                "para inicializar o RCAPipeline."
            )

        self.rca_result_repository = (
            rca_result_repository
        )

        if incident_repository is None:

            raise ValueError(
                "incident_repository é obrigatório "
                "para inicializar o RCAPipeline."
            )

        self.incident_repository = (
            incident_repository
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

        self.print_rca_execution(
            rca_execution
        )

        self.persist_trace(
            trace
        )

        self.persist_rca_result(
            rca_result
        )

        incident = self.build_incident(
            trace=trace,
            rca_result=rca_result,
        )

        self.print_incident( incident )

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
                    self._get_pipeline_version()
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

    def persist_trace(
        self,
        trace
    ):

        self.trace_repository.save(
            trace.to_dict()
        )

    def persist_rca_result(
        self,
        rca_result,
    ):

        self.rca_result_repository.save(
            rca_result
        )

        print(
            (
                "RCAResult persisted"
                " | rca_result_id="
                f"{rca_result.rca_result_id}"
                " | trace_id="
                f"{rca_result.trace_id}"
            ),
            flush=True,
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

    def print_rca_execution(
        self,
        rca_execution,
    ):

        rca_result = (
            rca_execution[
                "rca_result"
            ]
        )

        predictions = (
            rca_execution[
                "predictions"
            ]
        )

        shap_results = (
            rca_execution[
                "shap_results"
            ]
        )

        print(
            "\n========== RCA SUMMARY ==========",
            flush=True,
        )

        print(
            (
                "rca_result_id="
                f"{rca_result.rca_result_id}"
            ),
            flush=True,
        )

        print(
            (
                "trace_id="
                f"{rca_result.trace_id}"
            ),
            flush=True,
        )

        print(
            (
                "confidence="
                f"{rca_result.confidence:.6f}"
            ),
            flush=True,
        )

        print(
            (
                "evidence_count="
                f"{len(rca_result.evidence)}"
            ),
            flush=True,
        )

        print(
            "\n========== PREDICTIONS ==========",
            flush=True,
        )

        for prediction in predictions:

            print(
                (
                    "- service="
                    f"{prediction.get('service')}"
                    " | prediction="
                    f"{prediction.get('prediction')}"
                    " | confidence="
                    f"{float(prediction.get('confidence', 0.0)):.6f}"
                ),
                flush=True,
            )

        print(
            "\n========== SHAP SUMMARY ==========",
            flush=True,
        )

        if isinstance(
            shap_results,
            dict,
        ):

            print(
                (
                    "type=dict"
                    f" | keys={list(shap_results.keys())}"
                ),
                flush=True,
            )

        elif isinstance(
            shap_results,
            (list, tuple),
        ):

            print(
                (
                    f"type={type(shap_results).__name__}"
                    f" | items={len(shap_results)}"
                ),
                flush=True,
            )

        else:

            print(
                (
                    "type="
                    f"{type(shap_results).__name__}"
                ),
                flush=True,
            )

        print(
            "\n========== RANKING ==========",
            flush=True,
        )

        if not rca_result.ranking:

            print(
                "Nenhum serviço classificado.",
                flush=True,
            )

        else:

            for position, item in enumerate(
                rca_result.ranking,
                start=1,
            ):

                scores = (
                    item.scores.to_dict()
                )

                print(
                    (
                        f"{position}. "
                        f"service={item.service_name}"
                        " | final_score="
                        f"{item.scores.final_score:.6f}"
                        f" | scores={scores}"
                    ),
                    flush=True,
                )

        print(
            "\n========== EVIDENCE SUMMARY ==========",
            flush=True,
        )

        evidence_groups = {}

        for evidence in rca_result.evidence:

            payload = evidence.to_dict()

            service_name = (
                payload.get(
                    "service_name",
                    payload.get(
                        "service",
                        "unknown",
                    ),
                )
            )

            evidence_type = (
                payload.get(
                    "evidence_type",
                    "unknown",
                )
            )

            group_key = (
                str(service_name),
                str(evidence_type),
            )

            evidence_groups[group_key] = (
                evidence_groups.get(
                    group_key,
                    0,
                )
                + 1
            )

        for (
            service_name,
            evidence_type,
        ), count in sorted(
            evidence_groups.items()
        ):

            print(
                (
                    f"- service={service_name}"
                    f" | type={evidence_type}"
                    f" | count={count}"
                ),
                flush=True,
            )

        print(
            "\n========== RELIABILITY ==========",
            flush=True,
        )

        print(
            rca_result
            .reliability
            .to_dict(),
            flush=True,
        )
    
    def print_incident(
        self,
        incident,
    ):

        print(
            "\n========== INCIDENT ==========",
            flush=True,
        )

        if incident is None:

            print(
                (
                    "created=False"
                    " | reason=RCA result not reliable"
                ),
                flush=True,
            )

            return

        print(
            (
                "created=True"
                f" | incident_id={incident.incident_id}"
                " | root_cause_service="
                f"{incident.root_cause_service}"
                " | confidence="
                f"{incident.confidence:.6f}"
            ),
            flush=True,
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

    def _get_pipeline_version(
        self,
    ) -> str:

        if (
            self.model_runtime_version
            == "v2"
        ):

            return (
                "causalods-rca-v2"
            )

        return (
            "causalods-rca-v1"
        )