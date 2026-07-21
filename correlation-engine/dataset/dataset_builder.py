from dataset.feature_generator import (
    FeatureGenerator,
)
from dataset.label_generator import (
    LabelGenerator,
)
from dataset.scenario_generator import (
    ScenarioGenerator,
)


class DatasetBuilder:

    SERVICES = (
        "frontend",
        "order",
        "payment",
        "database",
        "cache",
        "kafka-broker",
    )

    def __init__(
        self,
        seed: int = 42,
    ):

        self.scenario_generator = (
            ScenarioGenerator(
                seed=seed
            )
        )

        self.feature_generator = (
            FeatureGenerator(
                seed=seed + 1
            )
        )

        self.label_generator = (
            LabelGenerator()
        )

    def generate(
        self,
        samples: int = 1000,
    ):

        if samples <= 0:

            raise ValueError(
                "samples deve ser maior que zero."
            )

        dataset = []

        for sample_index in range(
            samples
        ):

            scenario = (
                self.scenario_generator
                .generate(
                    sample_index=sample_index
                )
            )

            sample_id = (
                f"sample-{sample_index:06d}"
            )

            for service in self.SERVICES:

                feature = (
                    self.feature_generator
                    .generate(
                        scenario=scenario,
                        service=service,
                    )
                )

                feature["sample_id"] = (
                    sample_id
                )

                feature["scenario"] = (
                    scenario
                )

                feature["root_cause"] = (
                    self.label_generator
                    .generate(
                        feature=feature,
                        scenario=scenario,
                    )
                )

                dataset.append(
                    feature
                )

        return dataset