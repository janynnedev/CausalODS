import random


class FeatureGenerator:

    ROOT_CAUSE_BY_SCENARIO = {
        "normal": None,
        "database_latency": "database",
        "payment_failure": "payment",
        "cache_failure": "cache",
        "kafka_delay": "kafka-broker",
    }

    ROOT_PROFILES = {
        "database_latency": {
            "latency_range": (
                900.0,
                1800.0,
            ),
            "error_rate_range": (
                0.02,
                0.15,
            ),
            "zscore_range": (
                5.0,
                9.0,
            ),
            "anomaly_range": (
                0.80,
                0.99,
            ),
        },

        "payment_failure": {
            "latency_range": (
                120.0,
                500.0,
            ),
            "error_rate_range": (
                0.45,
                0.90,
            ),
            "zscore_range": (
                2.0,
                6.0,
            ),
            "anomaly_range": (
                0.80,
                0.99,
            ),
        },

        "cache_failure": {
            "latency_range": (
                200.0,
                800.0,
            ),
            "error_rate_range": (
                0.30,
                0.80,
            ),
            "zscore_range": (
                3.0,
                7.0,
            ),
            "anomaly_range": (
                0.75,
                0.98,
            ),
        },

        "kafka_delay": {
            "latency_range": (
                600.0,
                1600.0,
            ),
            "error_rate_range": (
                0.00,
                0.10,
            ),
            "zscore_range": (
                4.0,
                8.0,
            ),
            "anomaly_range": (
                0.75,
                0.98,
            ),
        },
    }

    # Ordem aproximada de propagação.
    IMPACTED_SERVICES = {
        "database_latency": {
            "order": 2,
            "frontend": 3,
        },

        "payment_failure": {
            "order": 2,
            "frontend": 3,
        },

        "cache_failure": {
            "order": 2,
            "frontend": 3,
        },

        "kafka_delay": {
            "payment": 2,
            "order": 3,
            "frontend": 4,
        },
    }

    # Estrutura aproximada do grafo sintético.
    TOPOLOGY = {
        "frontend": (
            0,
            1,
        ),
        "order": (
            1,
            3,
        ),
        "payment": (
            1,
            1,
        ),
        "database": (
            1,
            0,
        ),
        "cache": (
            1,
            0,
        ),
        "kafka-broker": (
            1,
            0,
        ),
    }

    def __init__(
        self,
        seed: int | None = None,
    ):

        self.random = random.Random(
            seed
        )

    def generate(
        self,
        scenario,
        service,
    ):

        if (
            scenario
            not in self.ROOT_CAUSE_BY_SCENARIO
        ):

            raise ValueError(
                f"Cenário desconhecido: {scenario}"
            )

        request_count = (
            self.random.randint(
                100,
                500,
            )
        )

        avg_latency_ms = (
            self.random.uniform(
                20.0,
                100.0,
            )
        )

        incoming_dependencies, (
            outgoing_dependencies
        ) = self.TOPOLOGY.get(
            service,
            (
                self.random.randint(
                    0,
                    2,
                ),
                self.random.randint(
                    0,
                    2,
                ),
            ),
        )

        feature = {
            "service_name":
                service,

            "request_count":
                request_count,

            "error_count":
                0,

            "error_rate":
                0.0,

            "avg_latency_ms":
                float(
                    avg_latency_ms
                ),

            "max_latency_ms":
                float(
                    avg_latency_ms
                    * self.random.uniform(
                        1.05,
                        1.80,
                    )
                ),

            "incoming_dependencies":
                incoming_dependencies,

            "outgoing_dependencies":
                outgoing_dependencies,

            "dependency_count":
                (
                    incoming_dependencies
                    + outgoing_dependencies
                ),

            "is_first_failure":
                0,

            "failure_order":
                -1,

            "failure_lead_time_ms":
                0.0,

            "latency_zscore":
                float(
                    self.random.uniform(
                        -0.75,
                        0.75,
                    )
                ),

            "anomaly_score":
                float(
                    self.random.uniform(
                        0.0,
                        0.15,
                    )
                ),

            "is_anomalous":
                0,
        }

        root_cause_service = (
            self.ROOT_CAUSE_BY_SCENARIO[
                scenario
            ]
        )

        if root_cause_service is None:
            return feature

        if service == root_cause_service:

            self._apply_root_cause(
                feature=feature,
                scenario=scenario,
            )

            return feature

        propagation_order = (
            self.IMPACTED_SERVICES
            .get(
                scenario,
                {},
            )
            .get(
                service
            )
        )

        if propagation_order is not None:

            self._apply_propagation(
                feature=feature,
                propagation_order=(
                    propagation_order
                ),
            )

        return feature

    def _apply_root_cause(
        self,
        feature,
        scenario,
    ):

        profile = (
            self.ROOT_PROFILES[
                scenario
            ]
        )

        error_rate = (
            self.random.uniform(
                *profile[
                    "error_rate_range"
                ]
            )
        )

        avg_latency_ms = (
            self.random.uniform(
                *profile[
                    "latency_range"
                ]
            )
        )

        feature.update(
            {
                "error_count":
                    min(
                        feature[
                            "request_count"
                        ],
                        int(
                            round(
                                feature[
                                    "request_count"
                                ]
                                * error_rate
                            )
                        ),
                    ),

                "error_rate":
                    float(
                        error_rate
                    ),

                "avg_latency_ms":
                    float(
                        avg_latency_ms
                    ),

                "max_latency_ms":
                    float(
                        avg_latency_ms
                        * self.random.uniform(
                            1.10,
                            1.70,
                        )
                    ),

                "is_first_failure":
                    1,

                "failure_order":
                    1,

                "failure_lead_time_ms":
                    0.0,

                "latency_zscore":
                    float(
                        self.random.uniform(
                            *profile[
                                "zscore_range"
                            ]
                        )
                    ),

                "anomaly_score":
                    float(
                        self.random.uniform(
                            *profile[
                                "anomaly_range"
                            ]
                        )
                    ),

                "is_anomalous":
                    1,
            }
        )

    def _apply_propagation(
        self,
        feature,
        propagation_order,
    ):

        error_rate = (
            self.random.uniform(
                0.01,
                0.30,
            )
        )

        avg_latency_ms = (
            feature[
                "avg_latency_ms"
            ]
            * self.random.uniform(
                1.50,
                4.00,
            )
        )

        anomaly_score = (
            self.random.uniform(
                0.25,
                0.78,
            )
        )

        feature.update(
            {
                "error_count":
                    min(
                        feature[
                            "request_count"
                        ],
                        int(
                            round(
                                feature[
                                    "request_count"
                                ]
                                * error_rate
                            )
                        ),
                    ),

                "error_rate":
                    float(
                        error_rate
                    ),

                "avg_latency_ms":
                    float(
                        avg_latency_ms
                    ),

                "max_latency_ms":
                    float(
                        avg_latency_ms
                        * self.random.uniform(
                            1.05,
                            1.50,
                        )
                    ),

                "failure_order":
                    int(
                        propagation_order
                    ),

                "failure_lead_time_ms":
                    float(
                        (
                            propagation_order
                            - 1
                        )
                        * self.random.uniform(
                            20.0,
                            120.0,
                        )
                    ),

                "latency_zscore":
                    float(
                        self.random.uniform(
                            1.0,
                            4.5,
                        )
                    ),

                "anomaly_score":
                    float(
                        anomaly_score
                    ),

                "is_anomalous":
                    int(
                        anomaly_score
                        >= 0.50
                    ),
            }
        )