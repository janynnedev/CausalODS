from features.anomaly_feature_extractor import AnomalyFeatureExtractor
from features.feature_aggregator import FeatureAggregator
from features.graph_feature_extractor import GraphFeatureExtractor
from features.temporal_feature_extractor import TemporalFeatureExtractor
from features.trace_feature_extractor import TraceFeatureExtractor


class FeaturePipeline:

    def __init__(self):

        self.trace = (TraceFeatureExtractor())

        self.graph = (GraphFeatureExtractor())

        self.temporal = (TemporalFeatureExtractor())

        self.anomaly = (AnomalyFeatureExtractor())

        self.aggregator = (FeatureAggregator())


    def build(

        self,

        trace,

        graph,

        correlation_result,

        anomaly_result=None

    ):

        trace_features = (self.trace.extract(trace))

        graph_features = (self.graph.extract(graph))

        temporal_features = (self.temporal.extract(
            correlation_result
        ))

        anomaly_features = (self.anomaly.extract(
            anomaly_result
        ))

        return self.aggregator.aggregate(

             trace_features=

                trace_features,

            graph_features=

                graph_features,

            temporal_features=

                temporal_features,

            anomaly_features=

                anomaly_features

        )