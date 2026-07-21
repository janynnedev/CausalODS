from models.rca_feature import RCAFeature


class FeatureAggregator:

    def aggregate(
        self,
        trace_features,
        graph_features,
        temporal_features,
        anomaly_features=None,
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

        # A ordenação deixa o resultado determinístico,
        # o que facilita testes e experimentos.
        for service in sorted(services):

            trace = (
                trace_features
                .get(
                    service,
                    {},
                )
            )

            graph = (
                graph_features
                .get(
                    service,
                    {},
                )
            )

            temporal = (
                temporal_features
                .get(
                    service,
                    {},
                )
            )

            anomaly = (
                anomaly_features
                .get(
                    service,
                    {},
                )
                if anomaly_features
                else {}
            )

            feature = RCAFeature(
                service_name=service,

                request_count=int(
                    trace.get(
                        "request_count",
                        0,
                    )
                ),

                error_count=int(
                    trace.get(
                        "error_count",
                        0,
                    )
                ),

                error_rate=float(
                    trace.get(
                        "error_rate",
                        0.0,
                    )
                ),

                avg_latency_ms=float(
                    trace.get(
                        "avg_latency_ms",
                        0.0,
                    )
                ),

                max_latency_ms=float(
                    trace.get(
                        "max_latency_ms",
                        0.0,
                    )
                ),

                incoming_dependencies=int(
                    graph.get(
                        "incoming_dependencies",
                        0,
                    )
                ),

                outgoing_dependencies=int(
                    graph.get(
                        "outgoing_dependencies",
                        0,
                    )
                ),

                dependency_count=int(
                    graph.get(
                        "dependency_count",
                        0,
                    )
                ),

                is_first_failure=bool(
                    temporal.get(
                        "is_first_failure",
                        False,
                    )
                ),

                failure_order=int(
                    temporal.get(
                        "failure_order",
                        -1,
                    )
                ),

                failure_lead_time_ms=float(
                    temporal.get(
                        "failure_lead_time_ms",
                        0.0,
                    )
                    or 0.0
                ),

                latency_zscore=float(
                    anomaly.get(
                        "latency_zscore",
                        0.0,
                    )
                    or 0.0
                ),

                anomaly_score=float(
                    anomaly.get(
                        "anomaly_score",
                        0.0,
                    )
                    or 0.0
                ),

                is_anomalous=bool(
                    anomaly.get(
                        "is_anomalous",
                        False,
                    )
                ),
            )

            self._validate_feature(
                feature
            )

            result.append(
                feature
            )

        return result

    @staticmethod
    def _validate_feature(
        feature,
    ):
        """
        Impede placeholders ou valores inválidos de
        chegarem ao Pandas e ao XGBoost.
        """

        numeric_fields = (
            "request_count",
            "error_count",
            "error_rate",
            "avg_latency_ms",
            "max_latency_ms",
            "incoming_dependencies",
            "outgoing_dependencies",
            "dependency_count",
            "failure_order",
            "failure_lead_time_ms",
            "latency_zscore",
            "anomaly_score",
        )

        for field_name in numeric_fields:

            value = getattr(
                feature,
                field_name,
            )

            if value is Ellipsis:

                raise ValueError(
                    (
                        "RCAFeature contém um placeholder "
                        f"não implementado: {field_name}. "
                        f"service={feature.service_name}"
                    )
                )

            if not isinstance(
                value,
                (int, float),
            ):

                raise TypeError(
                    (
                        "Campo numérico inválido em RCAFeature: "
                        f"{field_name}={value!r}. "
                        f"service={feature.service_name}"
                    )
                )