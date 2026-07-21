class LabelGenerator:

    ROOT_CAUSE_BY_SCENARIO = {
        "normal": None,
        "database_latency": "database",
        "payment_failure": "payment",
        "cache_failure": "cache",
        "kafka_delay": "kafka-broker",
    }

    def generate(
        self,
        feature,
        scenario,
    ):

        if (
            scenario
            not in self.ROOT_CAUSE_BY_SCENARIO
        ):

            raise ValueError(
                "Cenário desconhecido para geração "
                f"de rótulo: {scenario}"
            )

        root_cause_service = (
            self.ROOT_CAUSE_BY_SCENARIO[
                scenario
            ]
        )

        if root_cause_service is None:
            return 0

        return int(
            feature["service_name"]
            == root_cause_service
        )