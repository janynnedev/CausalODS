from __future__ import annotations

from typing import Any

import numpy as np

from dataset.label_generator_v2 import (
    LabelGeneratorV2,
)
from dataset.scenario_generator_v2 import (
    ScenarioV2,
)


class FeatureGeneratorV2:
    """
    Gera vetores coerentes para order-service e
    payment-service em uma mesma amostra.

    O gerador modela:

    - duração inclusiva no serviço upstream;
    - duração exclusiva por serviço;
    - propagação de erros;
    - precedência temporal da falha;
    - topologia fixa order -> payment.
    """

    SERVICES = (
        "order-service",
        "payment-service",
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

        self.label_generator = (
            LabelGeneratorV2()
        )

    def generate(
        self,
        scenario: ScenarioV2,
    ) -> list[
        dict[str, Any]
    ]:

        order_base_ms = self._bounded_normal(
            mean=150.0,
            deviation=70.0,
            minimum=25.0,
            maximum=450.0,
        )

        payment_base_ms = (
            self._bounded_normal(
                mean=65.0,
                deviation=25.0,
                minimum=15.0,
                maximum=180.0,
            )
        )

        order_exclusive_ms = order_base_ms
        payment_exclusive_ms = (
            payment_base_ms
        )

        if scenario.fault_kind == "latency":

            if (
                scenario.root_cause_service
                == "order-service"
            ):

                order_exclusive_ms += (
                    float(
                        scenario.fault_delay_ms
                    )
                )

            elif (
                scenario.root_cause_service
                == "payment-service"
            ):

                payment_exclusive_ms += (
                    float(
                        scenario.fault_delay_ms
                    )
                )

        total_exclusive_ms = max(
            order_exclusive_ms
            + payment_exclusive_ms,
            1.0,
        )

        order_exclusive_ratio = (
            order_exclusive_ms
            / total_exclusive_ms
        )

        payment_exclusive_ratio = (
            payment_exclusive_ms
            / total_exclusive_ms
        )

        order_max_latency_ms = max(
            total_exclusive_ms
            + self._bounded_normal(
                mean=8.0,
                deviation=4.0,
                minimum=0.0,
                maximum=25.0,
            ),
            1.0,
        )

        order_client_latency_ms = max(
            payment_exclusive_ms
            + self._bounded_normal(
                mean=6.0,
                deviation=3.0,
                minimum=1.0,
                maximum=20.0,
            ),
            1.0,
        )

        order_avg_latency_ms = (
            order_max_latency_ms
            + order_client_latency_ms
        ) / 2.0

        payment_max_latency_ms = (
            payment_exclusive_ms
        )

        payment_avg_latency_ms = (
            payment_exclusive_ms
        )

        error_state = (
            self._build_error_state(
                scenario
            )
        )

        order_row = self._build_row(
            scenario=scenario,

            service_name="order-service",

            request_count=(
                error_state[
                    "order_request_count"
                ]
            ),

            error_count=(
                error_state[
                    "order_error_count"
                ]
            ),

            avg_latency_ms=(
                order_avg_latency_ms
            ),

            max_latency_ms=(
                order_max_latency_ms
            ),

            incoming_dependencies=0,

            outgoing_dependencies=1,

            is_first_failure=(
                error_state[
                    "order_is_first_failure"
                ]
            ),

            failure_order=(
                error_state[
                    "order_failure_order"
                ]
            ),

            failure_lead_time_ms=(
                error_state[
                    "order_failure_lead_time_ms"
                ]
            ),

            exclusive_latency_ms=(
                order_exclusive_ms
            ),

            exclusive_latency_ratio=(
                order_exclusive_ratio
            ),
        )

        payment_row = self._build_row(
            scenario=scenario,

            service_name=(
                "payment-service"
            ),

            request_count=(
                error_state[
                    "payment_request_count"
                ]
            ),

            error_count=(
                error_state[
                    "payment_error_count"
                ]
            ),

            avg_latency_ms=(
                payment_avg_latency_ms
            ),

            max_latency_ms=(
                payment_max_latency_ms
            ),

            incoming_dependencies=1,

            outgoing_dependencies=0,

            is_first_failure=(
                error_state[
                    "payment_is_first_failure"
                ]
            ),

            failure_order=(
                error_state[
                    "payment_failure_order"
                ]
            ),

            failure_lead_time_ms=(
                error_state[
                    "payment_failure_lead_time_ms"
                ]
            ),

            exclusive_latency_ms=(
                payment_exclusive_ms
            ),

            exclusive_latency_ratio=(
                payment_exclusive_ratio
            ),
        )

        return [
            order_row,
            payment_row,
        ]

    def _build_row(
        self,
        *,
        scenario: ScenarioV2,
        service_name: str,
        request_count: int,
        error_count: int,
        avg_latency_ms: float,
        max_latency_ms: float,
        incoming_dependencies: int,
        outgoing_dependencies: int,
        is_first_failure: bool,
        failure_order: int,
        failure_lead_time_ms: float,
        exclusive_latency_ms: float,
        exclusive_latency_ratio: float,
    ) -> dict[str, Any]:
        if failure_lead_time_ms is None:
            failure_lead_time_ms = (
                failure_lead_time_ms
                if failure_lead_time_ms
                is not None
                else 0.0
            )

        error_rate = (
            float(error_count)
            / float(request_count)
            if request_count > 0
            else 0.0
        )

        root_cause = (
            self.label_generator.generate(
                service_name=service_name,
                root_cause_service=(
                    scenario
                    .root_cause_service
                ),
            )
        )

        return {
            "sample_id": (
                scenario.sample_id
            ),

            "scenario": (
                scenario.scenario
            ),

            "service_name": (
                service_name
            ),

            "root_cause_service": (
                scenario.root_cause_service
            ),

            "fault_delay_ms": int(
                scenario.fault_delay_ms
            ),

            "generation_seed": int(
                scenario.generation_seed
            ),

            "request_count": int(
                request_count
            ),

            "error_count": int(
                error_count
            ),

            "error_rate": float(
                error_rate
            ),

            "avg_latency_ms": float(
                avg_latency_ms
            ),

            "max_latency_ms": float(
                max_latency_ms
            ),

            "incoming_dependencies": int(
                incoming_dependencies
            ),

            "outgoing_dependencies": int(
                outgoing_dependencies
            ),

            "dependency_count": int(
                incoming_dependencies
                + outgoing_dependencies
            ),

            "is_first_failure": bool(
                is_first_failure
            ),

            "failure_order": int(
                failure_order
            ),

            "failure_lead_time_ms": float(
                failure_lead_time_ms
            ),

            # O detector de anomalias ainda
            # não está ativo no runtime.
            "latency_zscore": 0.0,

            "anomaly_score": 0.0,

            "is_anomalous": False,

            "exclusive_latency_ms": float(
                exclusive_latency_ms
            ),

            "exclusive_latency_ratio": float(
                exclusive_latency_ratio
            ),

            "root_cause": int(
                root_cause
            ),
        }

    def _build_error_state(
        self,
        scenario: ScenarioV2,
    ) -> dict[str, Any]:

        state = {
            "order_request_count": 2,
            "order_error_count": 0,
            "order_is_first_failure": (
                False
            ),
            "order_failure_order": -1,
            "order_failure_lead_time_ms": (
                0.0
            ),

            "payment_request_count": 1,
            "payment_error_count": 0,
            "payment_is_first_failure": (
                False
            ),
            "payment_failure_order": -1,
            "payment_failure_lead_time_ms": (
                0.0
            ),
        }

        if scenario.fault_kind != "error":
            return state

        if (
            scenario.root_cause_service
            == "payment-service"
        ):

            # O erro do pagamento se propaga
            # para os spans do order-service.
            state.update(
                {
                    "payment_request_count": 2,
                    "payment_error_count": 1,
                    "payment_is_first_failure": (
                        True
                    ),
                    "payment_failure_order": 1,

                    "order_error_count": 2,
                    "order_is_first_failure": (
                        False
                    ),
                    "order_failure_order": 2,
                    "order_failure_lead_time_ms": (
                        self._bounded_normal(
                            mean=8.0,
                            deviation=3.0,
                            minimum=1.0,
                            maximum=25.0,
                        )
                    ),
                }
            )

        elif (
            scenario.root_cause_service
            == "order-service"
        ):

            state.update(
                {
                    "order_error_count": 1,
                    "order_is_first_failure": (
                        True
                    ),
                    "order_failure_order": 1,

                    "payment_error_count": 0,
                    "payment_is_first_failure": (
                        False
                    ),
                    "payment_failure_order": -1,
                }
            )

        return state

    def _bounded_normal(
        self,
        *,
        mean: float,
        deviation: float,
        minimum: float,
        maximum: float,
    ) -> float:

        value = float(
            self.random.normal(
                mean,
                deviation,
            )
        )

        return float(
            min(
                max(
                    value,
                    minimum,
                ),
                maximum,
            )
        )