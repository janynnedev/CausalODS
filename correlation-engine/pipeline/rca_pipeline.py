from graph.graph_builder import GraphBuilder

from correlation.temporal_correlator import (
    TemporalCorrelator
)

from features.feature_pipeline import (
    FeaturePipeline
)

from persistence.opensearch.trace_repository import (
    TraceRepository
)


class RCAPipeline:

    def __init__(self):

        self.graph_builder = GraphBuilder()

        self.temporal = TemporalCorrelator()

        self.feature_pipeline = FeaturePipeline()

        self.trace_repository = TraceRepository()


    def process(self, trace):

        print()
        print("=" * 70)
        print("RCA PIPELINE")
        print("=" * 70)

        #
        # 1. Grafo
        #

        graph = self.graph_builder.build(trace)

        print()

        print("SERVICE GRAPH")

        print(graph.to_dict())


        #
        # 2. Correlação temporal
        #

        temporal_result = self.temporal.correlate(
            trace
        )

        print()

        print("TEMPORAL")

        print(temporal_result)


        #
        # 3. Features
        #

        features = self.feature_pipeline.build(

            trace,

            graph,

            temporal_result

        )

        print()

        print("FEATURES")

        for feature in features:

            print(feature)


        #
        # 4. Persistência do trace
        #

        self.trace_repository.save(
            trace.to_dict()
        )

        return features