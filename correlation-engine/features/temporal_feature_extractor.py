from __future__ import annotations

from numbers import Real
from typing import Any


class TemporalFeatureExtractor:
    """
    Converte o resultado do TemporalCorrelator em
    features por serviço.

    Suporta:

    - contrato canônico temporal-correlation-v2;
    - contrato legado com ordered_failures.
    """

    def extract(
        self,
        temporal_result: Any,
    ) -> dict[
        str,
        dict[str, Any],
    ]:

        if not isinstance(
            temporal_result,
            dict,
        ):
            return {}

        services = temporal_result.get(
            "services"
        )

        if isinstance(
            services,
            dict,
        ):

            return (
                self._extract_canonical(
                    temporal_result
                )
            )

        return self._extract_legacy(
            temporal_result
        )

    def _extract_canonical(
        self,
        temporal_result: dict[
            str,
            Any,
        ],
    ) -> dict[
        str,
        dict[str, Any],
    ]:

        services = temporal_result.get(
            "services",
            {},
        )

        trace_duration_ms = self._number(
            temporal_result.get(
                "trace_duration_ms",
                0.0,
            )
        )

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for service_name, payload in (
            services.items()
        ):

            if not isinstance(
                payload,
                dict,
            ):
                payload = {}

            exclusive_latency_ms = (
                self._number(
                    payload.get(
                        "exclusive_latency_ms",
                        0.0,
                    )
                )
            )

            exclusive_latency_ratio = (
                exclusive_latency_ms
                / trace_duration_ms
                if trace_duration_ms > 0.0
                else 0.0
            )

            exclusive_latency_ratio = min(
                max(
                    exclusive_latency_ratio,
                    0.0,
                ),
                1.0,
            )

            result[
                str(service_name)
            ] = {
                "is_first_failure": bool(
                    payload.get(
                        "is_first_failure",
                        False,
                    )
                ),

                "failure_order": int(
                    self._number(
                        payload.get(
                            "failure_order",
                            -1,
                        )
                    )
                ),

                "failure_lead_time_ms": (
                    self._number(
                        payload.get(
                            "failure_lead_time_ms",
                            0.0,
                        )
                    )
                ),

                "temporal_score": (
                    self._number(
                        payload.get(
                            "score",
                            payload.get(
                                "raw_score",
                                0.0,
                            ),
                        )
                    )
                ),

                "latency_rank": int(
                    self._number(
                        payload.get(
                            "latency_rank",
                            payload.get(
                                "rank",
                                -1,
                            ),
                        )
                    )
                ),

                "exclusive_latency_ms": (
                    exclusive_latency_ms
                ),

                "exclusive_latency_ratio": (
                    exclusive_latency_ratio
                ),
            }

        return result

    def _extract_legacy(
        self,
        temporal_result: dict[
            str,
            Any,
        ],
    ) -> dict[
        str,
        dict[str, Any],
    ]:

        ordered = temporal_result.get(
            "ordered_failures",
            [],
        )

        if not isinstance(
            ordered,
            list,
        ) or not ordered:
            return {}

        first = ordered[0]

        if not isinstance(
            first,
            dict,
        ):
            return {}

        initial_time = first.get(
            "timestamp"
        )

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for index, failure in enumerate(
            ordered,
            start=1,
        ):

            if not isinstance(
                failure,
                dict,
            ):
                continue

            service_name = failure.get(
                "service"
            )

            if not service_name:
                continue

            timestamp = failure.get(
                "timestamp"
            )

            lead_ms = 0.0

            if (
                initial_time is not None
                and timestamp is not None
            ):

                try:

                    lead_ms = (
                        timestamp
                        - initial_time
                    ).total_seconds() * 1000.0

                except (
                    AttributeError,
                    TypeError,
                ):

                    lead_ms = 0.0

            result[
                str(service_name)
            ] = {
                "failure_order": index,
                "is_first_failure": (
                    index == 1
                ),
                "failure_lead_time_ms": (
                    lead_ms
                ),
                "temporal_score": (
                    1.0
                    if index == 1
                    else 0.0
                ),
                "latency_rank": -1,
                "exclusive_latency_ms": 0.0,
                "exclusive_latency_ratio": 0.0,
            }

        return result

    @staticmethod
    def _number(
        value: Any,
    ) -> float:

        if value is None:
            return 0.0

        if isinstance(
            value,
            bool,
        ):
            return float(
                value
            )

        if isinstance(
            value,
            Real,
        ):
            return float(
                value
            )

        try:
            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.0