from dataset.dataset_builder import (
    DatasetBuilder,
)
from dataset.label_generator import (
    LabelGenerator,
)
from dataset.scenario_generator import (
    ScenarioGenerator,
)


def test_each_fault_scenario_has_one_root_cause():

    generator = LabelGenerator()

    services = (
        DatasetBuilder.SERVICES
    )

    for scenario in (
        ScenarioGenerator
        .FAULT_SCENARIOS
    ):

        labels = [
            generator.generate(
                feature={
                    "service_name":
                        service,
                },
                scenario=scenario,
            )
            for service in services
        ]

        assert sum(labels) == 1


def test_normal_scenario_has_no_root_cause():

    generator = LabelGenerator()

    labels = [
        generator.generate(
            feature={
                "service_name":
                    service,
            },
            scenario="normal",
        )
        for service in (
            DatasetBuilder.SERVICES
        )
    ]

    assert sum(labels) == 0


def test_dataset_contains_group_metadata():

    dataset = (
        DatasetBuilder(
            seed=42
        )
        .generate(
            samples=5
        )
    )

    assert len(dataset) == (
        5
        * len(
            DatasetBuilder.SERVICES
        )
    )

    for record in dataset:

        assert "sample_id" in record
        assert "scenario" in record
        assert "service_name" in record
        assert "root_cause" in record