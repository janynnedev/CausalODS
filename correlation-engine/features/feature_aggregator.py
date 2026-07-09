from models.rca_feature import RCAFeature



class FeatureAggregator:



    def aggregate(

        self,

        trace_features,

        graph_features,

        temporal_features,

        anomaly_features=None

    ):


        result = []


        services = set()


        services.update(
            trace_features.keys()
        )

        services.update(
            graph_features.keys()
        )

        services.update(
            temporal_features.keys()
        )


        if anomaly_features:

            services.update(
                anomaly_features.keys()
            )



        for service in services:


            trace = (
                trace_features
                .get(
                    service,
                    {}
                )
            )


            graph = (
                graph_features
                .get(
                    service,
                    {}
                )
            )


            temporal = (
                temporal_features
                .get(
                    service,
                    {}
                )
            )


            anomaly = (

                anomaly_features
                .get(
                    service,
                    {}
                )

                if anomaly_features

                else {}

            )


            feature = RCAFeature(

                service_name=service,


                request_count=
                    trace.get(
                        "request_count",
                        0
                    ),


                error_count=
                    trace.get(
                        "error_count",
                        0
                    ),


                error_rate=
                    trace.get(
                        "error_rate",
                        0
                    ),


                avg_latency_ms=
                    trace.get(
                        "avg_latency_ms",
                        0
                    ),


                incoming_dependencies=
                    graph.get(
                        "incoming_dependencies",
                        0
                    ),


                outgoing_dependencies=
                    graph.get(
                        "outgoing_dependencies",
                        0
                    ),


                is_first_failure=
                    temporal.get(
                        "is_first_failure",
                        0
                    ),


                failure_lead_time_ms=
                    temporal.get(
                        "failure_lead_time_ms",
                        0
                    ),


                latency_zscore=
                    anomaly.get(
                        "latency_zscore",
                        0
                    ),


                anomaly_score=
                    anomaly.get(
                        "anomaly_score",
                        0
                    )

            )


            result.append(
                feature
            )


        return result