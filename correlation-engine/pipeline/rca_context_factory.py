from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from models.rca_context import RCAContext

from pipeline.result_adapters import (
    FeatureEvidenceAdapter,
    PredictionResultAdapter,
    ShapResultAdapter,
    TemporalResultAdapter,
    normalize_feature_vectors,
)


class RCAContextFactory:

    def __init__(self):

        self.temporal_adapter = (
            TemporalResultAdapter()
        )

        self.prediction_adapter = (
            PredictionResultAdapter()
        )

        self.shap_adapter = (
            ShapResultAdapter()
        )

        self.feature_evidence_adapter = (
            FeatureEvidenceAdapter()
        )

    def from_legacy(
        self,
        *,
        trace: Any,
        service_graph: Any = None,
        feature_vectors: Any = None,
        temporal_results: Any = None,
        predictions: Any = None,
        shap_results: Any = None,
        trace_id: str | None = None,
        services: Sequence[str] | None = None,
        model_version: str = "unknown",
        pipeline_version: str = (
            "causalods-rca-v1"
        ),
    ) -> RCAContext:

        normalized_features = (
            normalize_feature_vectors(
                feature_vectors
            )
        )

        resolved_trace_id = (
            trace_id
            or self._extract_trace_id(trace)
        )

        resolved_services = (
            self._resolve_services(
                explicit_services=services,
                trace=trace,
                feature_vectors=(
                    normalized_features
                ),
                predictions=predictions,
            )
        )

        temporal = (
            self.temporal_adapter.adapt(
                raw=temporal_results,
                services=resolved_services,
            )
        )

        graph = (
            self.feature_evidence_adapter
            .adapt_graph(
                normalized_features
            )
        )

        anomaly = (
            self.feature_evidence_adapter
            .adapt_anomaly(
                normalized_features
            )
        )

        prediction = (
            self.prediction_adapter.adapt(
                raw=predictions,
                services=resolved_services,
            )
        )

        shap = (
            self.shap_adapter.adapt(
                raw=shap_results,
                services=resolved_services,
                feature_vectors=(
                    normalized_features
                ),
            )
        )

        context = RCAContext(
            trace_id=resolved_trace_id,
            services=tuple(
                resolved_services
            ),
            trace=trace,
            service_graph=service_graph,
            feature_vectors=(
                normalized_features
            ),
            temporal_results=temporal,
            graph_results=graph,
            anomaly_results=anomaly,
            predictions=prediction,
            shap_results=shap,
            model_version=model_version,
            pipeline_version=(
                pipeline_version
            ),
            adapter_metadata={
                "factory": (
                    "RCAContextFactory"
                ),
                "factory_version": "1.0",
            },
        )

        context.validate(
            strict=True
        )

        return context

    def _extract_trace_id(
        self,
        trace: Any,
    ) -> str:

        if trace is None:
            raise ValueError(
                "trace ou trace_id deve "
                "ser informado."
            )

        if isinstance(trace, Mapping):

            trace_id = (
                trace.get("trace_id")
                or trace.get("traceId")
                or trace.get("id")
            )

        else:

            trace_id = (
                getattr(
                    trace,
                    "trace_id",
                    None,
                )
                or getattr(
                    trace,
                    "traceId",
                    None,
                )
                or getattr(
                    trace,
                    "id",
                    None,
                )
            )

        if not trace_id:
            raise ValueError(
                "Não foi possível identificar "
                "o trace_id."
            )

        return str(trace_id)

    def _resolve_services(
        self,
        *,
        explicit_services: (
            Sequence[str] | None
        ),
        trace: Any,
        feature_vectors: Mapping[
            str,
            Any,
        ],
        predictions: Any,
    ) -> tuple[str, ...]:

        services: list[str] = []

        if explicit_services:
            services.extend(
                str(service)
                for service in explicit_services
            )

        services.extend(
            feature_vectors.keys()
        )

        services.extend(
            self._extract_trace_services(
                trace
            )
        )

        if isinstance(predictions, Mapping):

            nested = (
                predictions.get(
                    "probabilities"
                )
                or predictions.get(
                    "predictions"
                )
            )

            if isinstance(nested, Mapping):
                services.extend(
                    str(service)
                    for service in nested.keys()
                )
            else:
                services.extend(
                    str(service)
                    for service in predictions.keys()
                    if service
                    not in {
                        "predicted_service",
                        "confidence",
                        "model_version",
                    }
                )

        normalized = tuple(
            dict.fromkeys(
                service.strip()
                for service in services
                if service
                and service.strip()
            )
        )

        if not normalized:
            raise ValueError(
                "Não foi possível identificar "
                "os serviços do RCAContext."
            )

        return normalized

    def _extract_trace_services(
        self,
        trace: Any,
    ) -> list[str]:

        if trace is None:
            return []

        if isinstance(trace, Mapping):

            direct_services = trace.get(
                "services"
            )

            spans = trace.get(
                "spans",
                {},
            )

        else:

            direct_services = getattr(
                trace,
                "services",
                None,
            )

            spans = getattr(
                trace,
                "spans",
                {},
            )

        services = []

        if isinstance(
            direct_services,
            str,
        ):
            services.append(
                direct_services
            )

        elif isinstance(
            direct_services,
            Sequence,
        ):
            services.extend(
                str(service)
                for service in direct_services
            )

        span_values = (
            spans.values()
            if isinstance(spans, Mapping)
            else spans
        )

        if isinstance(
            span_values,
            Sequence,
        ) or isinstance(
            spans,
            Mapping,
        ):

            for span in span_values:

                if isinstance(span, Mapping):
                    service_name = (
                        span.get(
                            "service_name"
                        )
                        or span.get(
                            "service"
                        )
                    )
                else:
                    service_name = (
                        getattr(
                            span,
                            "service_name",
                            None,
                        )
                        or getattr(
                            span,
                            "service",
                            None,
                        )
                    )

                if service_name:
                    services.append(
                        str(service_name)
                    )

        return services