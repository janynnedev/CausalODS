from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import datetime
from numbers import Real
from typing import Any


class TemporalCorrelator:
    """
    Analisa precedência temporal, falhas e duração dos serviços
    observados em um trace.

    Esta classe não determina sozinha a causa raiz. Ela produz
    evidências temporais que serão posteriormente combinadas com:

    - anomalias;
    - grafo de serviços;
    - XGBoost;
    - SHAP;
    - ranking final.

    O campo `services` é o contrato canônico utilizado pelo
    TemporalResultAdapter.
    """

    METHOD_VERSION = "temporal-correlation-v1"

    # Pesos usados somente quando o trace possui falhas explícitas.
    FIRST_FAILURE_WEIGHT = 0.60
    ERROR_PRESENCE_WEIGHT = 0.25
    LATENCY_ORDER_WEIGHT = 0.15

    def analyze(
        self,
        trace: Any,
    ) -> dict[str, Any]:

        timeline = self.build_service_timeline(
            trace
        )

        first_failure = self.detect_first_failure(
            timeline
        )

        latency_order = self.compute_latency_order(
            timeline
        )

        propagation = self.compute_propagation(
            trace
        )

        service_depth = self.compute_service_depth(
            trace
        )

        service_summaries = (
            self.build_service_summaries(
                timeline=timeline,
                propagation=propagation,
                service_depth=service_depth,
            )
        )

        service_results = self.build_service_results(
            summaries=service_summaries,
            first_failure=first_failure,
            latency_order=latency_order,
        )

        return {
            # Novo contrato canônico por serviço.
            "services": service_results,

            # Campos anteriores preservados para compatibilidade
            # e auditoria.
            "timeline": timeline,
            "first_failure": first_failure,
            "latency_order": latency_order,
            "propagation": propagation,
            "service_depth": service_depth,

            "method": self.METHOD_VERSION,
        }

    def build_service_timeline(
        self,
        trace: Any,
    ) -> list[dict[str, Any]]:

        timeline: list[dict[str, Any]] = []

        for span in self._iter_spans(trace):

            service_name = getattr(
                span,
                "service_name",
                None,
            )

            if not service_name:
                continue

            timeline.append(
                {
                    "service": str(service_name),
                    "operation": getattr(
                        span,
                        "operation_name",
                        None,
                    ),
                    "span_id": getattr(
                        span,
                        "span_id",
                        None,
                    ),
                    "parent": getattr(
                        span,
                        "parent_span_id",
                        None,
                    ),
                    "start": getattr(
                        span,
                        "start_time",
                        None,
                    ),
                    "end": getattr(
                        span,
                        "end_time",
                        None,
                    ),
                    "duration": self._to_float(
                        getattr(
                            span,
                            "duration_ms",
                            0.0,
                        )
                    ) or 0.0,
                    "error": self._is_error_span(
                        span
                    ),
                }
            )

        timeline.sort(
            key=lambda item: self._time_key(
                item["start"]
            )
        )

        return timeline

    def detect_first_failure(
        self,
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any] | None:

        for item in timeline:
            if item["error"]:
                return dict(item)

        return None

    def compute_latency_order(
        self,
        timeline: list[dict[str, Any]],
    ) -> list[str]:
        """
        Retorna os serviços ordenados pela maior duração de span.

        Cada serviço aparece apenas uma vez.
        """

        maximum_duration_by_service: dict[
            str,
            float,
        ] = defaultdict(float)

        for item in timeline:

            service_name = item["service"]
            duration = float(item["duration"])

            maximum_duration_by_service[
                service_name
            ] = max(
                maximum_duration_by_service[
                    service_name
                ],
                duration,
            )

        ordered = sorted(
            maximum_duration_by_service.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )

        return [
            service_name
            for service_name, _ in ordered
        ]

    def compute_propagation(
        self,
        trace: Any,
    ) -> dict[str, list[str]]:

        propagation: dict[
            str,
            list[str],
        ] = defaultdict(list)

        spans_by_id = self._spans_by_id(
            trace
        )

        for span in spans_by_id.values():

            parent_span_id = getattr(
                span,
                "parent_span_id",
                None,
            )

            if not parent_span_id:
                continue

            parent = spans_by_id.get(
                parent_span_id
            )

            if parent is None:
                continue

            parent_service = getattr(
                parent,
                "service_name",
                None,
            )

            child_service = getattr(
                span,
                "service_name",
                None,
            )

            if (
                not parent_service
                or not child_service
                or parent_service == child_service
            ):
                continue

            children = propagation[
                str(parent_service)
            ]

            child_service = str(child_service)

            if child_service not in children:
                children.append(
                    child_service
                )

        return {
            service: children
            for service, children
            in propagation.items()
        }

    def compute_service_depth(
        self,
        trace: Any,
    ) -> dict[str, int]:
        """
        Calcula a menor profundidade observada para cada serviço.

        A implementação usa parent_span_id e não depende da
        existência de span.children.
        """

        spans_by_id = self._spans_by_id(
            trace
        )

        if not spans_by_id:
            return {}

        children_by_parent: dict[
            Any,
            list[Any],
        ] = defaultdict(list)

        roots: list[Any] = []

        for span_id, span in spans_by_id.items():

            parent_span_id = getattr(
                span,
                "parent_span_id",
                None,
            )

            if (
                not parent_span_id
                or parent_span_id not in spans_by_id
            ):
                roots.append(span_id)
            else:
                children_by_parent[
                    parent_span_id
                ].append(span_id)

        explicit_root = getattr(
            trace,
            "root_span_id",
            None,
        )

        if (
            explicit_root is not None
            and explicit_root in spans_by_id
        ):
            roots = [
                explicit_root,
                *[
                    root
                    for root in roots
                    if root != explicit_root
                ],
            ]

        service_depth: dict[str, int] = {}
        visited: set[Any] = set()

        queue = deque(
            (root_span_id, 0)
            for root_span_id in roots
        )

        while queue:

            span_id, level = queue.popleft()

            if span_id in visited:
                continue

            visited.add(span_id)

            span = spans_by_id.get(
                span_id
            )

            if span is None:
                continue

            service_name = getattr(
                span,
                "service_name",
                None,
            )

            if service_name:

                service_name = str(
                    service_name
                )

                previous_depth = (
                    service_depth.get(
                        service_name
                    )
                )

                if (
                    previous_depth is None
                    or level < previous_depth
                ):
                    service_depth[
                        service_name
                    ] = level

            for child_span_id in (
                children_by_parent.get(
                    span_id,
                    [],
                )
            ):
                queue.append(
                    (
                        child_span_id,
                        level + 1,
                    )
                )

        return service_depth

    def build_service_summaries(
        self,
        *,
        timeline: list[dict[str, Any]],
        propagation: Mapping[
            str,
            list[str],
        ],
        service_depth: Mapping[str, int],
    ) -> dict[str, dict[str, Any]]:

        summaries: dict[
            str,
            dict[str, Any],
        ] = defaultdict(
            lambda: {
                "span_count": 0,
                "error_count": 0,
                "earliest_start": None,
                "latest_end": None,
                "first_error_start": None,
                "max_duration_ms": 0.0,
                "total_duration_ms": 0.0,
                "operations": [],
                "upstream_services": [],
                "downstream_services": [],
                "depth": None,
            }
        )

        for item in timeline:

            service_name = item["service"]
            summary = summaries[
                service_name
            ]

            summary["span_count"] += 1

            duration = float(
                item["duration"]
            )

            summary["total_duration_ms"] += (
                duration
            )

            summary["max_duration_ms"] = max(
                summary["max_duration_ms"],
                duration,
            )

            operation = item.get(
                "operation"
            )

            if (
                operation
                and operation
                not in summary["operations"]
            ):
                summary["operations"].append(
                    operation
                )

            start = item.get("start")
            end = item.get("end")

            if self._is_before(
                start,
                summary["earliest_start"],
            ):
                summary[
                    "earliest_start"
                ] = start

            if self._is_after(
                end,
                summary["latest_end"],
            ):
                summary[
                    "latest_end"
                ] = end

            if item["error"]:

                summary["error_count"] += 1

                if self._is_before(
                    start,
                    summary[
                        "first_error_start"
                    ],
                ):
                    summary[
                        "first_error_start"
                    ] = start

        for parent_service, children in (
            propagation.items()
        ):

            parent_summary = summaries[
                parent_service
            ]

            for child_service in children:

                if (
                    child_service
                    not in parent_summary[
                        "downstream_services"
                    ]
                ):
                    parent_summary[
                        "downstream_services"
                    ].append(child_service)

                child_summary = summaries[
                    child_service
                ]

                if (
                    parent_service
                    not in child_summary[
                        "upstream_services"
                    ]
                ):
                    child_summary[
                        "upstream_services"
                    ].append(parent_service)

        for service_name, depth in (
            service_depth.items()
        ):
            summaries[
                service_name
            ]["depth"] = depth

        return {
            service_name: dict(summary)
            for service_name, summary
            in summaries.items()
        }

    def build_service_results(
        self,
        *,
        summaries: Mapping[
            str,
            Mapping[str, Any],
        ],
        first_failure: Mapping[
            str,
            Any,
        ] | None,
        latency_order: list[str],
    ) -> dict[str, dict[str, Any]]:

        service_count = len(
            latency_order
        )

        latency_rank = {
            service_name: position
            for position, service_name
            in enumerate(
                latency_order,
                start=1,
            )
        }

        first_failure_service = (
            first_failure.get("service")
            if first_failure
            else None
        )

        lead_by_service = (
            self._compute_failure_leads(
                summaries
            )
        )

        results: dict[
            str,
            dict[str, Any],
        ] = {}

        for service_name, summary in (
            summaries.items()
        ):

            rank = latency_rank.get(
                service_name,
                service_count or 1,
            )

            latency_score = (
                self._rank_score(
                    position=rank,
                    total=service_count,
                )
            )

            is_first_failure = (
                service_name
                == first_failure_service
            )

            has_error = (
                int(
                    summary.get(
                        "error_count",
                        0,
                    )
                )
                > 0
            )

            first_failure_score = (
                1.0
                if is_first_failure
                else 0.0
            )

            error_presence_score = (
                1.0
                if has_error
                else 0.0
            )

            if first_failure_service is not None:

                temporal_score = (
                    self.FIRST_FAILURE_WEIGHT
                    * first_failure_score

                    + self.ERROR_PRESENCE_WEIGHT
                    * error_presence_score

                    + self.LATENCY_ORDER_WEIGHT
                    * latency_score
                )

                scoring_mode = (
                    "failure_precedence_and_latency"
                )

            else:
                # Sem falha explícita, o correlator não afirma
                # que exista uma anomalia. Ele preserva apenas
                # a ordem relativa de latência como uma heurística.
                temporal_score = latency_score

                scoring_mode = (
                    "latency_order_only"
                )

            temporal_score = min(
                max(
                    float(temporal_score),
                    0.0,
                ),
                1.0,
            )

            results[service_name] = {
                # Campos reconhecidos pelo adapter.
                "score": temporal_score,
                "raw_score": temporal_score,
                "is_first_anomaly": (
                    is_first_failure
                ),
                "is_first_failure": (
                    is_first_failure
                ),
                "anomaly_started_at": (
                    summary.get(
                        "first_error_start"
                    )
                ),
                "lead_ms": lead_by_service.get(
                    service_name
                ),
                "rank": rank,

                # Informações semânticas preservadas.
                "latency_rank": rank,
                "latency_rank_score": (
                    latency_score
                ),
                "span_count": int(
                    summary.get(
                        "span_count",
                        0,
                    )
                ),
                "error_count": int(
                    summary.get(
                        "error_count",
                        0,
                    )
                ),
                "max_duration_ms": float(
                    summary.get(
                        "max_duration_ms",
                        0.0,
                    )
                ),
                "total_duration_ms": float(
                    summary.get(
                        "total_duration_ms",
                        0.0,
                    )
                ),
                "earliest_start": (
                    summary.get(
                        "earliest_start"
                    )
                ),
                "latest_end": (
                    summary.get(
                        "latest_end"
                    )
                ),
                "first_error_start": (
                    summary.get(
                        "first_error_start"
                    )
                ),
                "depth": summary.get(
                    "depth"
                ),
                "upstream_services": list(
                    summary.get(
                        "upstream_services",
                        [],
                    )
                ),
                "downstream_services": list(
                    summary.get(
                        "downstream_services",
                        [],
                    )
                ),
                "operations": list(
                    summary.get(
                        "operations",
                        [],
                    )
                ),
                "score_components": {
                    "first_failure": (
                        first_failure_score
                    ),
                    "error_presence": (
                        error_presence_score
                    ),
                    "latency_order": (
                        latency_score
                    ),
                },
                "score_weights": {
                    "first_failure": (
                        self.FIRST_FAILURE_WEIGHT
                    ),
                    "error_presence": (
                        self.ERROR_PRESENCE_WEIGHT
                    ),
                    "latency_order": (
                        self.LATENCY_ORDER_WEIGHT
                    ),
                },
                "scoring_mode": scoring_mode,
                "method": self.METHOD_VERSION,
            }

        return results

    def _compute_failure_leads(
        self,
        summaries: Mapping[
            str,
            Mapping[str, Any],
        ],
    ) -> dict[str, float | None]:
        """
        Calcula a distância temporal entre a primeira falha de um
        serviço e a primeira falha do serviço seguinte.

        O cálculo em milissegundos só é realizado quando os valores
        podem ser interpretados com segurança como datetime.
        """

        failures = [
            (
                service_name,
                summary.get(
                    "first_error_start"
                ),
            )
            for service_name, summary
            in summaries.items()
            if summary.get(
                "first_error_start"
            ) is not None
        ]

        failures.sort(
            key=lambda item: self._time_key(
                item[1]
            )
        )

        leads: dict[
            str,
            float | None,
        ] = {
            service_name: None
            for service_name, _ in failures
        }

        for index in range(
            len(failures) - 1
        ):

            current_service, current_time = (
                failures[index]
            )

            _, next_time = failures[
                index + 1
            ]

            leads[current_service] = (
                self._difference_ms(
                    current_time,
                    next_time,
                )
            )

        return leads

    def _iter_spans(
        self,
        trace: Any,
    ) -> list[Any]:

        spans = getattr(
            trace,
            "spans",
            {},
        )

        if isinstance(spans, Mapping):
            return list(
                spans.values()
            )

        if isinstance(
            spans,
            (list, tuple, set),
        ):
            return list(spans)

        return []

    def _spans_by_id(
        self,
        trace: Any,
    ) -> dict[Any, Any]:

        spans = getattr(
            trace,
            "spans",
            {},
        )

        if isinstance(spans, Mapping):
            return dict(spans)

        result: dict[Any, Any] = {}

        for span in self._iter_spans(
            trace
        ):

            span_id = getattr(
                span,
                "span_id",
                None,
            )

            if span_id is not None:
                result[span_id] = span

        return result

    def _is_error_span(
        self,
        span: Any,
    ) -> bool:

        status_code = getattr(
            span,
            "status_code",
            None,
        )

        if status_code is None:
            status_code = getattr(
                span,
                "status",
                None,
            )

        if status_code is None:
            return False

        normalized = str(
            getattr(
                status_code,
                "value",
                status_code,
            )
        ).upper()

        return (
            normalized == "ERROR"
            or normalized.endswith(
                "_ERROR"
            )
            or normalized.endswith(
                ".ERROR"
            )
        )

    def _rank_score(
        self,
        *,
        position: int,
        total: int,
    ) -> float:

        if total <= 0:
            return 0.0

        if total == 1:
            return 1.0

        score = 1.0 - (
            (position - 1)
            / (total - 1)
        )

        return min(
            max(score, 0.0),
            1.0,
        )

    def _to_float(
        self,
        value: Any,
    ) -> float | None:

        if value is None:
            return None

        if isinstance(value, bool):
            return float(value)

        if isinstance(value, Real):
            return float(value)

        if hasattr(
            value,
            "item",
        ) and callable(value.item):
            try:
                return float(
                    value.item()
                )
            except (
                TypeError,
                ValueError,
            ):
                return None

        try:
            return float(value)
        except (
            TypeError,
            ValueError,
        ):
            return None

    def _time_key(
        self,
        value: Any,
    ) -> float:

        if value is None:
            return float("inf")

        parsed_datetime = (
            self._as_datetime(value)
        )

        if parsed_datetime is not None:
            return parsed_datetime.timestamp()

        numeric_value = self._to_float(
            value
        )

        if numeric_value is not None:
            return numeric_value

        return float("inf")

    def _is_before(
        self,
        candidate: Any,
        current: Any,
    ) -> bool:

        if candidate is None:
            return False

        if current is None:
            return True

        return (
            self._time_key(candidate)
            < self._time_key(current)
        )

    def _is_after(
        self,
        candidate: Any,
        current: Any,
    ) -> bool:

        if candidate is None:
            return False

        if current is None:
            return True

        return (
            self._time_key(candidate)
            > self._time_key(current)
        )

    def _as_datetime(
        self,
        value: Any,
    ) -> datetime | None:

        if isinstance(value, datetime):
            return value

        if not isinstance(value, str):
            return None

        normalized = value.strip()

        if normalized.endswith("Z"):
            normalized = (
                normalized[:-1]
                + "+00:00"
            )

        try:
            return datetime.fromisoformat(
                normalized
            )
        except ValueError:
            return None

    def _difference_ms(
        self,
        start: Any,
        end: Any,
    ) -> float | None:

        start_datetime = self._as_datetime(
            start
        )

        end_datetime = self._as_datetime(
            end
        )

        if (
            start_datetime is None
            or end_datetime is None
        ):
            # Não assumimos que timestamps numéricos estejam em
            # nanossegundos, microssegundos ou milissegundos.
            return None

        difference = (
            end_datetime
            - start_datetime
        )

        return max(
            difference.total_seconds()
            * 1000.0,
            0.0,
        )