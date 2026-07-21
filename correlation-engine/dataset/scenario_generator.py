import random


class ScenarioGenerator:

    SCENARIOS = (
        "normal",
        "database_latency",
        "payment_failure",
        "cache_failure",
        "kafka_delay",
    )

    FAULT_SCENARIOS = (
        "database_latency",
        "payment_failure",
        "cache_failure",
        "kafka_delay",
    )

    def __init__(
        self,
        seed: int | None = None,
    ):

        self.random = random.Random(
            seed
        )

    def generate(
        self,
        sample_index: int | None = None,
    ):
        """
        Quando sample_index é informado, distribui os
        cenários de forma balanceada.

        Sem sample_index, retorna um cenário aleatório.
        """

        if sample_index is not None:

            return self.SCENARIOS[
                sample_index
                % len(self.SCENARIOS)
            ]

        return self.random.choice(
            self.SCENARIOS
        )