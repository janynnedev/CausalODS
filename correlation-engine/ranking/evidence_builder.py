from __future__ import annotations

import hashlib
import json
import math

from collections.abc import Mapping, Sequence
from inspect import signature
from enum import Enum
from types import UnionType
from typing import (
    Any,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from models.evidence import EvidenceItem
from models.rca_context import RCAContext


class EvidenceBuilder:
    """
    Converte os resultados canônicos do RCAContext em evidências
    auditáveis.

    Nenhuma decisão de causa raiz é tomada nesta classe.
    """

    BUILDER_VERSION = "evidence-builder-v1"

    def __init__(
        self,
        max_shap_features: int = 5,
    ) -> None:

        if max_shap_features < 1:

            raise ValueError(
                "max_shap_features deve ser maior que zero."
            )

        self.max_shap_features = (
            max_shap_features
        )

    def build(
        self,
        context: RCAContext,
    ) -> tuple[EvidenceItem, ...]:

        evidence: list[EvidenceItem] = []

        for service_name in context.services:

            self._append_prediction_evidence(
                evidence=evidence,
                context=context,
                service_name=service_name,
            )

            self._append_shap_evidence(
                evidence=evidence,
                context=context,
                service_name=service_name,
            )

            self._append_temporal_evidence(
                evidence=evidence,
                context=context,
                service_name=service_name,
            )

            self._append_graph_evidence(
                evidence=evidence,
                context=context,
                service_name=service_name,
            )

            self._append_anomaly_evidence(
                evidence=evidence,
                context=context,
                service_name=service_name,
            )

        return tuple(evidence)

    def _append_prediction_evidence(
        self,
        *,
        evidence: list[EvidenceItem],
        context: RCAContext,
        service_name: str,
    ) -> None:

        payload = self._mapping(
            context.predictions.get(
                service_name
            )
        )

        probability = self._score(
            payload.get(
                "probability",
                0.0,
            )
        )

        evidence.append(
            self._create_item(
                context=context,
                service_name=service_name,
                evidence_type="model",
                source_component=(
                    "PredictionPipeline"
                ),
                description=(
                    "O modelo atribuiu probabilidade "
                    f"{probability:.6f} ao serviço "
                    f"{service_name}."
                ),
                metric_name=(
                    "root_cause_probability"
                ),
                raw_value=payload.get(
                    "probability",
                    0.0,
                ),
                normalized_value=(
                    probability
                ),
                unit="probability",
                direction=(
                    "supporting"
                    if probability > 0.0
                    else "neutral"
                ),
                attributes={
                    "evidence_subtype": "model_prediction",
                    "model_version": (
                        context.model_version
                    ),
                },
            )
        )

    def _append_shap_evidence(
        self,
        *,
        evidence: list[EvidenceItem],
        context: RCAContext,
        service_name: str,
    ) -> None:

        payload = self._mapping(
            context.shap_results.get(
                service_name
            )
        )

        support_score = self._score(
            payload.get(
                "support_score",
                0.0,
            )
        )

        contributions = payload.get(
            "contributions",
            (),
        )

        if not isinstance(
            contributions,
            Sequence,
        ) or isinstance(
            contributions,
            (str, bytes, bytearray),
        ):
            contributions = ()

        normalized_contributions = [
            self._mapping(item)
            for item in contributions
        ]

        normalized_contributions.sort(
            key=lambda item: abs(
                self._float(
                    item.get(
                        "shap_value",
                        0.0,
                    )
                )
            ),
            reverse=True,
        )

        for contribution in (
            normalized_contributions[
                :self.max_shap_features
            ]
        ):

            feature_name = str(
                contribution.get(
                    "feature_name",
                    "unknown_feature",
                )
            )

            shap_value = self._float(
                contribution.get(
                    "shap_value",
                    0.0,
                )
            )

            direction = (
                contribution.get(
                    "direction"
                )
                or self._direction_from_value(
                    shap_value
                )
            )

            evidence.append(
                self._create_item(
                    context=context,
                    service_name=service_name,
                    evidence_type="shap", 
                    source_component=(
                        "SHAPService"
                    ),
                    description=(
                        f"A feature {feature_name} apresentou "
                        f"contribuição SHAP {shap_value:.6f} "
                        f"para o serviço {service_name}."
                    ),
                    metric_name=feature_name,
                    raw_value=shap_value,
                    normalized_value=(
                        support_score
                    ),
                    unit="shap_value",
                    direction=str(direction),
                    attributes={
                        "evidence_subtype": (
                            "shap_contribution"
                        ),
                        "feature_value": (
                            contribution.get(
                                "feature_value"
                            )
                        ),
                        "aggregate_support_score": (
                            support_score
                        ),
                    },
                )
            )

        if not normalized_contributions:

            evidence.append(
                self._create_item(
                    context=context,
                    service_name=service_name,
                    evidence_type="shap",
                    source_component=(
                        "SHAPService"
                    ),
                    description=(
                        "O suporte SHAP agregado para "
                        f"{service_name} foi "
                        f"{support_score:.6f}."
                    ),
                    metric_name=(
                        "shap_support_score"
                    ),
                    raw_value=payload.get(
                        "raw_positive_support",
                        support_score,
                    ),
                    normalized_value=(
                        support_score
                    ),
                    unit="normalized_score",
                    direction=(
                        "supporting"
                        if support_score > 0.0
                        else "neutral"
                    ),
                    attributes={
                        "evidence_subtype": "shap_support",
                    },
                )
            )

    def _append_temporal_evidence(
        self,
        *,
        evidence: list[EvidenceItem],
        context: RCAContext,
        service_name: str,
    ) -> None:

        payload = self._mapping(
            context.temporal_results.get(
                service_name
            )
        )

        temporal_score = self._score(
            payload.get(
                "score",
                0.0,
            )
        )

        is_first_anomaly = bool(
            payload.get(
                "is_first_anomaly",
                False,
            )
        )

        evidence.append(
            self._create_item(
                context=context,
                service_name=service_name,
                evidence_type=(
                    "temporal"
                ),
                source_component=(
                    "TemporalCorrelator"
                ),
                description=(
                    f"O serviço {service_name} obteve "
                    f"score temporal {temporal_score:.6f}; "
                    "primeira falha/anomalia: "
                    f"{is_first_anomaly}."
                ),
                metric_name="temporal_score",
                raw_value={
                    "score": temporal_score,
                    "is_first_anomaly": (
                        is_first_anomaly
                    ),
                    "anomaly_started_at": (
                        payload.get(
                            "anomaly_started_at"
                        )
                    ),
                    "lead_ms": payload.get(
                        "lead_ms"
                    ),
                },
                normalized_value=(
                    temporal_score
                ),
                unit="normalized_score",
                direction=(
                    "supporting"
                    if temporal_score > 0.0
                    else "neutral"
                ),
                attributes={
                    "evidence_subtype": (
                        "temporal_correlation"
                    ),
                    "is_first_anomaly": (
                        is_first_anomaly
                    ),
                    "rank": payload.get(
                        "rank"
                    ),
                    "scoring_mode": (
                        payload.get(
                            "scoring_mode"
                        )
                    ),
                },
                timestamp=payload.get(
                    "anomaly_started_at"
                ),
            )
        )

    def _append_graph_evidence(
        self,
        *,
        evidence: list[EvidenceItem],
        context: RCAContext,
        service_name: str,
    ) -> None:

        payload = self._mapping(
            context.graph_results.get(
                service_name
            )
        )

        graph_score = self._score(
            payload.get(
                "score",
                0.0,
            )
        )

        evidence.append(
            self._create_item(
                context=context,
                service_name=service_name,
                evidence_type=(
                    "graph"
                ),
                source_component=(
                    "GraphFeatureExtractor"
                ),
                description=(
                    f"O serviço {service_name} obteve "
                    f"score estrutural {graph_score:.6f}."
                ),
                metric_name="graph_score",
                raw_value={
                    "score": graph_score,
                    "centrality": payload.get(
                        "centrality"
                    ),
                    "in_degree": payload.get(
                        "in_degree"
                    ),
                    "out_degree": payload.get(
                        "out_degree"
                    ),
                },
                normalized_value=(
                    graph_score
                ),
                unit="normalized_score",
                direction=(
                    "supporting"
                    if graph_score > 0.0
                    else "neutral"
                ),
                attributes={
                    "evidence_subtype": "graph_structure"
                },
            )
        )

    def _append_anomaly_evidence(
        self,
        *,
        evidence: list[EvidenceItem],
        context: RCAContext,
        service_name: str,
    ) -> None:

        payload = self._mapping(
            context.anomaly_results.get(
                service_name
            )
        )

        anomaly_score = self._score(
            payload.get(
                "score",
                0.0,
            )
        )

        evidence.append(
            self._create_item(
                context=context,
                service_name=service_name,
                evidence_type=(
                    "anomaly"
                ),
                source_component=(
                    "AnomalyFeatureExtractor"
                ),
                description=(
                    f"O serviço {service_name} obteve "
                    f"score de anomalia "
                    f"{anomaly_score:.6f}."
                ),
                metric_name="anomaly_score",
                raw_value={
                    "raw_score": payload.get(
                        "raw_score"
                    ),
                    "zscore": payload.get(
                        "zscore"
                    ),
                    "error_count": payload.get(
                        "error_count"
                    ),
                    "error_rate": payload.get(
                        "error_rate"
                    ),
                },
                normalized_value=(
                    anomaly_score
                ),
                unit="normalized_score",
                direction=(
                    "supporting"
                    if anomaly_score > 0.0
                    else "neutral"
                ),
                attributes={
                    "evidence_subtype": "anomaly_detection"
                },
            )
        )

    def _create_item(
        self,
        *,
        context: RCAContext,
        service_name: str,
        evidence_type: str,
        source_component: str,
        description: str,
        metric_name: str,
        raw_value: Any,
        normalized_value: float,
        unit: str,
        direction: str,
        attributes: Mapping[str, Any],
        timestamp: Any = None,
    ) -> EvidenceItem:
        """
        O contrato de EvidenceItem pode usar timestamp ou
        observed_at. O filtro por assinatura mantém compatibilidade
        com a versão da Fase 1.
        """

        evidence_id = self._evidence_id(
            trace_id=context.trace_id,
            service_name=service_name,
            evidence_type=evidence_type,
            source_component=source_component,
            metric_name=metric_name,
            raw_value=raw_value,
        )

        candidates = {
            "evidence_id": evidence_id,
            "evidence_type": (
                self._coerce_declared_enum(
                    field_name="evidence_type",
                    value=evidence_type,
                )
            ),
            "source_component": source_component,
            "description": description,
            "service_name": service_name,
            "metric_name": metric_name,
            "feature_name": metric_name,
            "raw_value": raw_value,
            "normalized_value": (
                normalized_value
            ),
            "unit": unit,
            "direction": (
                self._coerce_declared_enum(
                    field_name="direction",
                    value=direction,
                )
            ),
            "timestamp": timestamp,
            "observed_at": timestamp,
            "attributes": {
                **dict(attributes),
                "builder_version": (
                    self.BUILDER_VERSION
                ),
                "trace_id": context.trace_id,
            },
        }

        accepted_parameters = set(
            signature(
                EvidenceItem
            ).parameters.keys()
        )

        kwargs = {
            key: value
            for key, value in candidates.items()
            if key in accepted_parameters
        }

        return EvidenceItem(
            **kwargs
        )

    def _evidence_id(
        self,
        *,
        trace_id: str,
        service_name: str,
        evidence_type: str,
        source_component: str,
        metric_name: str,
        raw_value: Any,
    ) -> str:

        canonical_payload = json.dumps(
            {
                "trace_id": trace_id,
                "service_name": (
                    service_name
                ),
                "evidence_type": (
                    evidence_type
                ),
                "source_component": (
                    source_component
                ),
                "metric_name": metric_name,
                "raw_value": raw_value,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

        digest = hashlib.sha256(
            canonical_payload.encode(
                "utf-8"
            )
        ).hexdigest()[:24]

        return f"ev-{digest}"
    
    def _coerce_declared_enum(
        self,
        *,
        field_name: str,
        value: Any,
    ) -> Any:
        """
        Converte strings para o Enum declarado no contrato
        EvidenceItem.

        Isso permite que o builder continue desacoplado dos nomes
        concretos dos Enums usados em models/evidence.py.
        """

        type_hints = get_type_hints(
            EvidenceItem
        )

        annotation = type_hints.get(
            field_name
        )

        enum_type = self._extract_enum_type(
            annotation
        )

        if enum_type is None:
            return value

        if isinstance(value, enum_type):
            return value

        if not isinstance(value, str):

            raise TypeError(
                f"{field_name} deve ser uma string "
                f"ou uma instância de "
                f"{enum_type.__name__}. "
                f"Recebido: {type(value).__name__}"
            )

        normalized_value = value.strip()

        if not normalized_value:

            raise ValueError(
                f"{field_name} não pode ser vazio."
            )

        aliases = {
            "evidence_type": {
                "model_prediction": "model",
                "shap_contribution": "shap",
                "shap_support": "shap",
                "temporal_correlation": (
                    "temporal"
                ),
                "graph_structure": "graph",
                "anomaly_detection": (
                    "anomaly"
                ),
            },
        }

        normalized_value = (
            aliases
            .get(field_name, {})
            .get(
                normalized_value,
                normalized_value,
            )
        )

        
        # Primeiramente tenta encontrar pelo valor do Enum:
        #
        # EvidenceType.MODEL_PREDICTION.value
        # == "model_prediction"
        try:
            return enum_type(
                normalized_value
            )
        except ValueError:
            pass

        # Depois permite também o nome do membro:
        #
        # "MODEL_PREDICTION"
        for member in enum_type:

            if (
                member.name.casefold()
                == normalized_value.casefold()
            ):
                return member

        valid_values = ", ".join(
            repr(member.value)
            for member in enum_type
        )

        raise ValueError(
            f"Valor inválido para {field_name}: "
            f"{normalized_value!r}. "
            f"Valores permitidos: {valid_values}"
        )

    def _extract_enum_type(
        self,
        annotation: Any,
    ) -> type[Enum] | None:
        """
        Encontra um Enum em anotações simples ou opcionais.

        Exemplos tratados:

            EvidenceType
            EvidenceType | None
            Optional[EvidenceType]
        """

        if annotation is None:
            return None

        if (
            isinstance(annotation, type)
            and issubclass(
                annotation,
                Enum,
            )
        ):
            return annotation

        origin = get_origin(
            annotation
        )

        if origin in (
            Union,
            UnionType,
        ):

            for argument in get_args(
                annotation
            ):

                enum_type = (
                    self._extract_enum_type(
                        argument
                    )
                )

                if enum_type is not None:
                    return enum_type

        return None

    def _mapping(
        self,
        value: Any,
    ) -> dict[str, Any]:

        if isinstance(value, Mapping):
            return dict(value)

        return {}

    def _score(
        self,
        value: Any,
    ) -> float:

        converted = self._float(
            value
        )

        return min(
            max(converted, 0.0),
            1.0,
        )

    def _float(
        self,
        value: Any,
    ) -> float:

        if value is None:
            return 0.0

        try:
            converted = float(value)
        except (TypeError, ValueError):
            return 0.0

        if not math.isfinite(converted):
            return 0.0

        return converted

    def _direction_from_value(
        self,
        value: float,
    ) -> str:

        if value > 0.0:
            return "supporting"

        if value < 0.0:
            return "opposing"

        return "neutral"