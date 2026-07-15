from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from models.evidence import freeze_value, json_safe


@dataclass(frozen=True)
class RCAContext:
    """
    Snapshot dos artefatos intermediários de uma execução da RCA.

    Este objeto não representa ainda o resultado final. Ele reúne
    as saídas dos componentes já existentes e fornece uma entrada
    única para:

    - construção das evidências;
    - ranking;
    - reliability gate;
    - baselines;
    - avaliação experimental.
    """

    trace_id: str
    services: tuple[str, ...]

    trace: Any = None
    service_graph: Any = None

    feature_vectors: Mapping[str, Any] = field(
        default_factory=dict
    )

    temporal_results: Mapping[str, Any] = field(
        default_factory=dict
    )

    graph_results: Mapping[str, Any] = field(
        default_factory=dict
    )

    anomaly_results: Mapping[str, Any] = field(
        default_factory=dict
    )

    predictions: Mapping[str, Any] = field(
        default_factory=dict
    )

    shap_results: Mapping[str, Any] = field(
        default_factory=dict
    )

    model_version: str = "unknown"
    pipeline_version: str = "causalods-rca-v1"

    adapter_metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        trace_id = self.trace_id.strip()

        if not trace_id:
            raise ValueError(
                "trace_id não pode ser vazio."
            )

        normalized_services = tuple(
            dict.fromkeys(
                service.strip()
                for service in self.services
                if service and service.strip()
            )
        )

        if not normalized_services:
            raise ValueError(
                "RCAContext deve possuir ao menos um serviço."
            )

        object.__setattr__(
            self,
            "trace_id",
            trace_id,
        )

        object.__setattr__(
            self,
            "services",
            normalized_services,
        )

        for field_name in (
            "feature_vectors",
            "temporal_results",
            "graph_results",
            "anomaly_results",
            "predictions",
            "shap_results",
            "adapter_metadata",
        ):
            current_value = getattr(self, field_name)

            object.__setattr__(
                self,
                field_name,
                freeze_value(dict(current_value)),
            )

    def service_exists(
        self,
        service_name: str,
    ) -> bool:
        return service_name in self.services

    def get_service_payload(
        self,
        source_name: str,
        service_name: str,
    ) -> Any:
        """
        Recupera os dados canônicos de um serviço em determinada
        origem.

        Exemplo:

            context.get_service_payload(
                "temporal_results",
                "payment-service",
            )
        """

        allowed_sources = {
            "feature_vectors",
            "temporal_results",
            "graph_results",
            "anomaly_results",
            "predictions",
            "shap_results",
        }

        if source_name not in allowed_sources:
            raise ValueError(
                f"Origem inválida: {source_name!r}"
            )

        source = getattr(self, source_name)

        return source.get(service_name)

    def unknown_result_services(self) -> dict[str, list[str]]:
        """
        Identifica serviços que aparecem nos resultados, mas não
        foram registrados em services.
        """

        known_services = set(self.services)
        unknown: dict[str, list[str]] = {}

        for field_name in (
            "feature_vectors",
            "temporal_results",
            "graph_results",
            "anomaly_results",
            "predictions",
            "shap_results",
        ):
            source = getattr(self, field_name)

            invalid = sorted(
                set(source.keys()) - known_services
            )

            if invalid:
                unknown[field_name] = invalid

        return unknown

    def validate(
        self,
        strict: bool = True,
    ) -> list[str]:
        """
        Retorna avisos encontrados no contexto.

        Em strict=True, serviços desconhecidos causam ValueError.
        """

        warnings: list[str] = []

        unknown_services = self.unknown_result_services()

        if unknown_services:
            message = (
                "Resultados encontrados para serviços não "
                f"registrados no contexto: {unknown_services}"
            )

            if strict:
                raise ValueError(message)

            warnings.append(message)

        if not self.predictions:
            warnings.append(
                "O contexto não possui predições."
            )

        if not self.feature_vectors:
            warnings.append(
                "O contexto não possui vetores de features."
            )

        return warnings

    def to_debug_dict(self) -> dict[str, Any]:
        """
        Representação serializável sem incluir os objetos completos
        de trace e grafo.
        """

        return {
            "trace_id": self.trace_id,
            "services": list(self.services),
            "feature_vectors": json_safe(
                self.feature_vectors
            ),
            "temporal_results": json_safe(
                self.temporal_results
            ),
            "graph_results": json_safe(
                self.graph_results
            ),
            "anomaly_results": json_safe(
                self.anomaly_results
            ),
            "predictions": json_safe(
                self.predictions
            ),
            "shap_results": json_safe(
                self.shap_results
            ),
            "model_version": self.model_version,
            "pipeline_version": self.pipeline_version,
            "adapter_metadata": json_safe(
                self.adapter_metadata
            ),
        }