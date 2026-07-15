from graph.graph_builder import GraphBuilder

from correlation.temporal_correlator import (
    TemporalCorrelator
)

from pipeline.feature_pipeline import (
    FeaturePipeline
)

from pipeline.prediction_pipeline import PredictionPipeline
from ranking.ranking_service import RCARankingService
from storage.incident_service import IncidentService

class RCAPipeline:

    def __init__(self, trace_repository):

        self.graph_builder = GraphBuilder()

        self.temporal = TemporalCorrelator()

        self.feature_pipeline = FeaturePipeline()

        self.trace_repository = trace_repository

        self.prediction_pipeline = PredictionPipeline()

        self.ranking_service = RCARankingService()

        self.incident_service = IncidentService()
        

    def process(self, trace):

        graph = self.build_graph(trace)

        correlation_result = (self.build_temporal(trace))

        features = self.build_features(
            trace,
            graph,
            correlation_result
        )

        rca_result = self.run_rca(
            features
        )

        incident = None

        if rca_result:
            incident = self.build_incident(
                trace,
                rca_result
            )


        self.persist_trace(trace)

        if incident: 
            self.persist_incident(
                incident
            )

        return {

            "graph": graph,
            "correlation_result": correlation_result,
            "features": features,
            "rca_result": rca_result,
            "incident": incident
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


    def persist(
        self,
        trace
    ):

        self.trace_repository.save(
            trace.to_dict()
        )