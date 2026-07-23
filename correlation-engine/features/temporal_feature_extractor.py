from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class TemporalFeatureExtractor:

    def extract(
        self,
        temporal_result: Any,
    ) -> dict[str, dict[str, Any]]:
        """
        Extrai as features temporais produzidas pelo contrato
        atual do TemporalCorrelator.

        Contrato esperado:

            {
                "services": {
                    "payment-service": {
                        "is_first_failure": True,
                        "failure_order": 1,
                        "failure_lead_time_ms": 0.0,
                        ...
                    }
                }
            }
        """

        if not isinstance(
            temporal_result,
            Mapping,
        ):
            return {}

        services = temporal_result.get(
            "services",
            {},
        )

        if not isinstance(
            services,
            Mapping,
        ):
            return {}

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for service_name, payload in (
            services.items()
        ):

            if not isinstance(
                payload,
                Mapping,
            ):
                continue

            is_first_failure = bool(
                payload.get(
                    "is_first_failure",
                    payload.get(
                        "is_first_anomaly",
                        False,
                    ),
                )
            )

            failure_order = payload.get(
                "failure_order"
            )

            if failure_order is None:

                failure_order = (
                    1
                    if is_first_failure
                    else -1
                )

            failure_lead_time_ms = (
                payload.get(
                    "failure_lead_time_ms"
                )
            )

            if failure_lead_time_ms is None:

                failure_lead_time_ms = (
                    payload.get(
                        "lead_ms",
                        0.0,
                    )
                )

            result[
                str(service_name)
            ] = {
                "failure_order": int(
                    failure_order
                ),

                "is_first_failure": (
                    is_first_failure
                ),

                "failure_lead_time_ms": float(
                    failure_lead_time_ms
                    or 0.0
                ),
            }

        return result