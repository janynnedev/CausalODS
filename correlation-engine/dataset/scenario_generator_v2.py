from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ScenarioV2:
    """
    Descreve uma amostra causal completa.

    Cada cenário posteriormente produzirá uma linha
    por serviço. O rótulo positivo será aplicado
    somente ao serviço definido como causa raiz.
    """

    sample_id: str

    scenario: str

    fault_kind: str

    root_cause_service: str | None

    fault_delay_ms: int

    generation_seed: int


class ScenarioGeneratorV2:
    """
    Gera cenários sintéticos reproduzíveis.

    Distribuição padrão:

    - 34% saudável;
    - 33% falha explícita;
    - 33% degradação de latência.
    """

    SERVICES = (
        "order-service",
        "payment-service",
    )

    LATENCY_DELAYS_MS = (
        300,
        500,
        750,
        1000,
        1500,
        2000,
        3000,
    )

    SCENARIO_TYPES = (
        "healthy",
        "failure",
        "latency",
    )

    SCENARIO_PROBABILITIES = (
        0.34,
        0.33,
        0.33,
    )

    def __init__(
        self,
        *,
        seed: int = 42,
    ) -> None:

        self.seed = int(seed)

        self.random = (
            np.random.default_rng(
                self.seed
            )
        )

    def generate(
        self,
        index: int,
    ) -> ScenarioV2:

        scenario_type = str(
            self.random.choice(
                self.SCENARIO_TYPES,
                p=self.SCENARIO_PROBABILITIES,
            )
        )

        sample_id = (
            f"rca-v2-{self.seed}-"
            f"{int(index):08d}"
        )

        if scenario_type == "healthy":

            return ScenarioV2(
                sample_id=sample_id,
                scenario="healthy",
                fault_kind="none",
                root_cause_service=None,
                fault_delay_ms=0,
                generation_seed=self.seed,
            )

        root_cause_service = str(
            self.random.choice(
                self.SERVICES
            )
        )

        service_prefix = (
            root_cause_service
            .replace(
                "-service",
                "",
            )
        )

        if scenario_type == "failure":

            return ScenarioV2(
                sample_id=sample_id,
                scenario=(
                    f"{service_prefix}_failure"
                ),
                fault_kind="error",
                root_cause_service=(
                    root_cause_service
                ),
                fault_delay_ms=0,
                generation_seed=self.seed,
            )

        delay_ms = int(
            self.random.choice(
                self.LATENCY_DELAYS_MS
            )
        )

        return ScenarioV2(
            sample_id=sample_id,
            scenario=(
                f"{service_prefix}_latency"
            ),
            fault_kind="latency",
            root_cause_service=(
                root_cause_service
            ),
            fault_delay_ms=delay_ms,
            generation_seed=self.seed,
        )