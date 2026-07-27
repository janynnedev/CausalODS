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

    METHOD_VERSION = "temporal-correlation-v2"

    EXCLUSIVE_DOMINANCE_MIN_SHARE = 0.70

    EXCLUSIVE_DOMINANCE_MIN_MARGIN = 0.20

    EXCLUSIVE_DOMINANCE_MIN_LATENCY_MS = 500.0

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

            trace_duration_ms = (
                self._get_trace_duration_ms(
                    trace=trace,
                    timeline=timeline,
                )
            )

            has_explicit_error = any(
                bool(
                    item.get(
                        "error",
                        False,
                    )
                )
                for item in timeline
            )

            exclusive_latency_by_service = (
                self.compute_exclusive_latency_by_service(
                    trace
                )
            )

            if has_explicit_error:

                # Preserva a lógica já validada para
                # payment_failure.
                #
                # Quando existem erros explícitos, a origem
                # da falha será determinada principalmente
                # por detect_failure_origin().
                latency_order = (
                    self.compute_latency_order(
                        timeline
                    )
                )

                latency_order_method = (
                    "inclusive_latency_order"
                )

            else:

                # Quando não existem erros explícitos, usamos
                # duração exclusiva para não culpar o serviço
                # upstream pelo tempo gasto aguardando um
                # serviço downstream.
                (
                    latency_order,
                    exclusive_latency_by_service,
                ) = self.compute_exclusive_latency_order(
                    trace
                )

                latency_order_method = (
                    "exclusive_latency_order"
                )

                # Fallback defensivo. Deve ser utilizado
                # somente se não for possível produzir a
                # ordenação exclusiva.
                if not latency_order:

                    latency_order = (
                        self.compute_latency_order(
                            timeline
                        )
                    )

                    latency_order_method = (
                        "inclusive_latency_fallback"
                    )

            exclusive_latency_dominance = (
                self.compute_exclusive_latency_dominance(
                    exclusive_latency_by_service
                )
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

            first_failure = (
                self.detect_failure_origin(
                    timeline=timeline,
                    summaries=service_summaries,
                    propagation=propagation,
                )
            )

            service_results = (
                self.build_service_results(
                    summaries=service_summaries,
                    first_failure=first_failure,
                    latency_order=latency_order,
                    propagation=propagation,
                    latency_order_method=(
                        latency_order_method
                    ),
                    exclusive_latency_by_service=(
                        exclusive_latency_by_service
                    ),
                    exclusive_latency_dominance=(
                        exclusive_latency_dominance
                    ),
                )
            )

            return {
                "services": service_results,
                "timeline": timeline,
                "first_failure": first_failure,
                "latency_order": latency_order,
                "latency_order_method": (
                    latency_order_method
                ),
                "exclusive_latency_by_service": (
                    exclusive_latency_by_service
                ),
                "exclusive_latency_dominance": (
                    exclusive_latency_dominance
                ),
                 "trace_duration_ms": (
                    trace_duration_ms
                ),
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

    def compute_exclusive_latency_by_service(
        self,
        trace: Any,
    ) -> dict[str, float]:
        """
        Calcula o tempo exclusivo aproximado por serviço.

        Para cada span:
            duração exclusiva =
                duração do span
                - união dos intervalos dos filhos diretos

        Isso impede que o serviço pai seja culpado apenas
        porque ficou aguardando um serviço downstream.
        """

        spans_by_id = self._spans_by_id(
            trace
        )

        children_by_parent: dict[
            Any,
            list[Any],
        ] = defaultdict(list)

        for span in spans_by_id.values():

            parent_span_id = getattr(
                span,
                "parent_span_id",
                None,
            )

            if (
                parent_span_id
                and parent_span_id in spans_by_id
            ):
                children_by_parent[
                    parent_span_id
                ].append(span)

        exclusive_by_service: dict[
            str,
            float,
        ] = defaultdict(float)

        for span_id, span in spans_by_id.items():

            service_name = getattr(
                span,
                "service_name",
                None,
            )

            if not service_name:
                continue

            span_duration_ms = max(
                self._to_float(
                    getattr(
                        span,
                        "duration_ms",
                        0.0,
                    )
                )
                or 0.0,
                0.0,
            )

            parent_start = self._time_key(
                getattr(
                    span,
                    "start_time",
                    None,
                )
            )

            parent_end = self._time_key(
                getattr(
                    span,
                    "end_time",
                    None,
                )
            )

            child_intervals: list[
                tuple[float, float]
            ] = []

            if (
                parent_start != float("inf")
                and parent_end != float("inf")
                and parent_end > parent_start
            ):

                for child in children_by_parent.get(
                    span_id,
                    [],
                ):

                    child_start = self._time_key(
                        getattr(
                            child,
                            "start_time",
                            None,
                        )
                    )

                    child_end = self._time_key(
                        getattr(
                            child,
                            "end_time",
                            None,
                        )
                    )

                    if (
                        child_start == float("inf")
                        or child_end == float("inf")
                    ):
                        continue

                    clipped_start = max(
                        parent_start,
                        child_start,
                    )

                    clipped_end = min(
                        parent_end,
                        child_end,
                    )

                    if clipped_end > clipped_start:
                        child_intervals.append(
                            (
                                clipped_start,
                                clipped_end,
                            )
                        )

            covered_ms = (
                self._merged_interval_duration_ms(
                    child_intervals
                )
            )

            exclusive_duration_ms = max(
                span_duration_ms - covered_ms,
                0.0,
            )

            exclusive_by_service[
                str(service_name)
            ] += exclusive_duration_ms

        return dict(
            exclusive_by_service
        )

    def compute_exclusive_latency_order(
        self,
        trace: Any,
    ) -> tuple[
        list[str],
        dict[str, float],
    ]:

        exclusive_latency = (
            self.compute_exclusive_latency_by_service(
                trace
            )
        )

        ordered = sorted(
            exclusive_latency.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )

        return (
            [
                service_name
                for service_name, _
                in ordered
            ],
            exclusive_latency,
        )

    def compute_exclusive_latency_dominance(
        self,
        exclusive_latency_by_service: Mapping[
            str,
            float,
        ],
    ) -> dict[str, Any]:
        """
        Calcula a participação relativa de cada serviço
        no tempo exclusivo total.

        Uma dominância só é considerada significativa
        quando:

        - o serviço líder possui participação mínima;
        - existe margem mínima sobre o segundo colocado;
        - existem pelo menos dois serviços comparáveis.

        Isso impede que diferenças normais de poucos
        milissegundos sejam tratadas como evidência
        causal forte.
        """

        normalized: dict[
            str,
            float,
        ] = {}

        for service_name, value in (
            exclusive_latency_by_service.items()
        ):

            latency = max(
                self._to_float(
                    value
                )
                or 0.0,
                0.0,
            )

            normalized[
                str(service_name)
            ] = latency

        total_exclusive_latency_ms = sum(
            normalized.values()
        )

        if total_exclusive_latency_ms > 0.0:

            shares = {
                service_name: (
                    latency
                    / total_exclusive_latency_ms
                )
                for service_name, latency
                in normalized.items()
            }

        else:

            shares = {
                service_name: 0.0
                for service_name
                in normalized
            }

        ordered = sorted(
            shares.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )

        top_service = (
            ordered[0][0]
            if ordered
            else None
        )

        top_latency_ms = (
            float(
                normalized.get(
                    top_service,
                    0.0,
                )
            )
            if top_service
            else 0.0
        )

        top_share = (
            float(
                ordered[0][1]
            )
            if ordered
            else 0.0
        )

        second_share = (
            float(
                ordered[1][1]
            )
            if len(ordered) > 1
            else 0.0
        )

        margin = max(
            top_share - second_share,
            0.0,
        )

        is_meaningful = (
            len(ordered) >= 2

            and top_share
            >= self.EXCLUSIVE_DOMINANCE_MIN_SHARE

            and margin
            >= self.EXCLUSIVE_DOMINANCE_MIN_MARGIN

            and top_latency_ms
            >= self.EXCLUSIVE_DOMINANCE_MIN_LATENCY_MS
        )

        return {
            "shares": shares,

            "ordered_services": [
                service_name
                for service_name, _
                in ordered
            ],

            "top_service": top_service,

            "top_latency_ms": top_latency_ms,

            "minimum_latency_ms": self.EXCLUSIVE_DOMINANCE_MIN_LATENCY_MS,

            "top_share": top_share,

            "second_share": second_share,

            "margin": margin,

            "is_meaningful": (
                is_meaningful
            ),

            "minimum_share": (
                self.EXCLUSIVE_DOMINANCE_MIN_SHARE
            ),

            "minimum_margin": (
                self.EXCLUSIVE_DOMINANCE_MIN_MARGIN
            ),

            "total_exclusive_latency_ms": (
                total_exclusive_latency_ms
            ),
        }

    @staticmethod
    def _merged_interval_duration_ms(
        intervals: list[
            tuple[float, float]
        ],
    ) -> float:

        if not intervals:
            return 0.0

        ordered = sorted(
            intervals,
            key=lambda item: (
                item[0],
                item[1],
            ),
        )

        merged: list[
            list[float]
        ] = []

        for start, end in ordered:

            if (
                not merged
                or start > merged[-1][1]
            ):
                merged.append(
                    [start, end]
                )

            else:
                merged[-1][1] = max(
                    merged[-1][1],
                    end,
                )

        covered_seconds = sum(
            end - start
            for start, end in merged
        )

        return max(
            covered_seconds * 1000.0,
            0.0,
        )
    
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

    def detect_failure_origin(
        self,
        *,
        timeline: list[dict[str, Any]],
        summaries: Mapping[
            str,
            Mapping[str, Any],
        ],
        propagation: Mapping[
            str,
            list[str],
        ],
    ) -> dict[str, Any] | None:
        
        error_services = {
            service_name
            for service_name, summary
            in summaries.items()
            if int(
                summary.get(
                    "error_count",
                    0,
                )
            ) > 0
        }

        if not error_services:
            return None

        origin_candidates = [
            service_name
            for service_name in error_services
            if not any(
                child_service
                in error_services
                for child_service
                in propagation.get(
                    service_name,
                    [],
                )
            )
        ]

        if not origin_candidates:

            origin_candidates = list(
                error_services
            )

        selected_service = min(
            origin_candidates,
            key=lambda service_name: (
                self._time_key(
                    summaries[
                        service_name
                    ].get(
                        "first_error_start"
                    )
                ),
                -int(
                    summaries[
                        service_name
                    ].get(
                        "depth"
                    )
                    or 0
                ),
                service_name,
            ),
        )

        matching_items = [
            item
            for item in timeline
            if (
                item.get("service")
                == selected_service
                and item.get("error")
            )
        ]

        if matching_items:

            selected_item = min(
                matching_items,
                key=lambda item: (
                    self._time_key(
                        item.get("start")
                    )
                ),
            )

            result = dict(
                selected_item
            )

        else:

            result = {
                "service": selected_service,
                "operation": None,
                "span_id": None,
                "parent": None,
                "start": summaries[
                    selected_service
                ].get(
                    "first_error_start"
                ),
                "end": None,
                "duration": 0.0,
                "error": True,
            }

        result[
            "detection_method"
        ] = "downstream_error_leaf"

        return result

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
        propagation: Mapping[
            str,
            list[str],
        ] | None = None,
        latency_order_method: str = (
            "inclusive_latency_order"
        ),
        exclusive_latency_by_service: (
            Mapping[str, float] | None
        ) = None,
        exclusive_latency_dominance: (
            Mapping[str, Any] | None
        ) = None,
    ) -> dict[str, dict[str, Any]]:

        exclusive_latency_by_service = (
            exclusive_latency_by_service
            or {}
        )

        exclusive_latency_dominance = (
            exclusive_latency_dominance
            or {}
        )

        dominance_shares = (
            exclusive_latency_dominance
            .get(
                "shares",
                {},
            )
        )

        if not isinstance(
            dominance_shares,
            Mapping,
        ):

            dominance_shares = {}

        dominance_top_service = (
            exclusive_latency_dominance
            .get(
                "top_service"
            )
        )

        dominance_margin = float(
            exclusive_latency_dominance
            .get(
                "margin",
                0.0,
            )
            or 0.0
        )

        dominance_is_meaningful = bool(
            exclusive_latency_dominance
            .get(
                "is_meaningful",
                False,
            )
        )

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
            first_failure.get(
                "service"
            )
            if first_failure
            else None
        )

        failure_order_by_service = (
            self._compute_failure_order(
                summaries=summaries,
                propagation=(
                    propagation
                    or {}
                ),
                first_failure_service=(
                    first_failure_service
                ),
            )
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

            exclusive_latency_ms = float(
                exclusive_latency_by_service.get(
                    service_name,
                    0.0,
                )
                or 0.0
            )

            exclusive_latency_share = float(
                dominance_shares.get(
                    service_name,
                    0.0,
                )
                or 0.0
            )

            is_dominant_service = (
                dominance_is_meaningful
                and service_name
                == dominance_top_service
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

                active_latency_component = (
                    latency_score
                )

                active_score_weights = {
                    "first_failure": (
                        self.FIRST_FAILURE_WEIGHT
                    ),
                    "error_presence": (
                        self.ERROR_PRESENCE_WEIGHT
                    ),
                    "latency_order": (
                        self.LATENCY_ORDER_WEIGHT
                    ),
                }

            else:

                if dominance_is_meaningful:

                    # Em traces sem erro, o score temporal
                    # representa a participação real do
                    # serviço no tempo exclusivo total.
                    temporal_score = (
                        exclusive_latency_share
                    )

                    scoring_mode = (
                        "exclusive_latency_dominance"
                    )

                    active_latency_component = (
                        exclusive_latency_share
                    )

                else:

                    # Diferenças pequenas e normais de
                    # latência não constituem evidência
                    # causal suficiente.
                    temporal_score = 0.0

                    scoring_mode = (
                        "exclusive_latency_no_dominance"
                    )

                    active_latency_component = 0.0

                active_score_weights = {
                    "first_failure": 0.0,
                    "error_presence": 0.0,
                    "latency_order": 1.0,
                }
            
            temporal_score = min(
                max(
                    float(
                        temporal_score
                    ),
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

                "failure_order": (
                    failure_order_by_service.get(
                        service_name,
                        -1,
                    )
                ),

                "anomaly_started_at": (
                    summary.get(
                        "first_error_start"
                    )
                ),

                "lead_ms": (
                    lead_by_service.get(
                        service_name
                    )
                ),

                "failure_lead_time_ms": float(
                    lead_by_service.get(
                        service_name
                    )
                    or 0.0
                ),

                "rank": rank,
                "latency_rank": rank,

                "latency_rank_score": (
                    latency_score
                ),

                "exclusive_latency_share": (
                    exclusive_latency_share
                ),

                "exclusive_latency_dominance_margin": (
                    dominance_margin
                ),

                "exclusive_latency_dominance_detected": (
                    dominance_is_meaningful
                ),

                "is_exclusive_latency_dominant": (
                    is_dominant_service
                ),

                "latency_order_method": (
                    latency_order_method
                ),

                "exclusive_latency_ms": (
                    exclusive_latency_ms
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
                        active_latency_component
                    ),
                },

                "score_weights": (
                    active_score_weights
                ),

                "scoring_mode": scoring_mode,
                "method": self.METHOD_VERSION,
            }

        return results
    
    def _compute_failure_order(
        self,
        *,
        summaries: Mapping[
            str,
            Mapping[str, Any],
        ],
        propagation: Mapping[
            str,
            list[str],
        ],
        first_failure_service: str | None,
    ) -> dict[str, int]:
        """
        Ordena a falha pela propagação causal observada.

        A origem recebe ordem 1. Serviços upstream afetados recebem
        ordens 2, 3 e assim por diante.
        """

        if not first_failure_service:
            return {}

        error_services = {
            service_name
            for service_name, summary
            in summaries.items()
            if int(
                summary.get(
                    "error_count",
                    0,
                )
            ) > 0
        }

        reverse_propagation: dict[
            str,
            list[str],
        ] = defaultdict(list)

        for parent_service, children in (
            propagation.items()
        ):

            for child_service in children:

                if (
                    parent_service
                    not in reverse_propagation[
                        child_service
                    ]
                ):
                    reverse_propagation[
                        child_service
                    ].append(
                        parent_service
                    )

        orders = {
            first_failure_service: 1
        }

        queue = deque(
            [first_failure_service]
        )

        while queue:

            current_service = (
                queue.popleft()
            )

            current_order = orders[
                current_service
            ]

            for upstream_service in (
                reverse_propagation.get(
                    current_service,
                    [],
                )
            ):

                if (
                    upstream_service
                    not in error_services
                ):
                    continue

                if upstream_service in orders:
                    continue

                orders[
                    upstream_service
                ] = current_order + 1

                queue.append(
                    upstream_service
                )

        remaining_services = sorted(
            error_services
            - set(
                orders.keys()
            ),
            key=lambda service_name: (
                self._time_key(
                    summaries[
                        service_name
                    ].get(
                        "first_error_start"
                    )
                ),
                service_name,
            ),
        )

        next_order = (
            max(
                orders.values(),
                default=0,
            )
            + 1
        )

        for service_name in (
            remaining_services
        ):

            orders[
                service_name
            ] = next_order

            next_order += 1

        return orders

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

    def _get_trace_duration_ms(
        self,
        *,
        trace: Any,
        timeline: list[
            dict[str, Any]
        ],
    ) -> float:
        """
        Obtém a duração total do trace.

        Prioridade:
        1. trace.get_duration_ms();
        2. trace.duration_ms;
        3. maior duração de span observada.

        A terceira alternativa funciona como fallback
        para objetos artificiais usados em testes.
        """

        duration_getter = getattr(
            trace,
            "get_duration_ms",
            None,
        )

        if callable(duration_getter):

            try:

                duration = self._to_float(
                    duration_getter()
                )

            except (
                TypeError,
                ValueError,
            ):

                duration = None

            if (
                duration is not None
                and duration > 0.0
            ):
                return float(
                    duration
                )

        direct_duration = self._to_float(
            getattr(
                trace,
                "duration_ms",
                None,
            )
        )

        if (
            direct_duration is not None
            and direct_duration > 0.0
        ):
            return float(
                direct_duration
            )

        observed_durations = [
            float(
                item.get(
                    "duration",
                    0.0,
                )
                or 0.0
            )
            for item in timeline
        ]

        return max(
            observed_durations,
            default=0.0,
        )

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

    