from __future__ import annotations

import math

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from models.evidence import (
    EvidenceItem,
    ensure_finite_number,
    ensure_score,
    freeze_value,
    json_safe,
    normalize_datetime,
)


@dataclass(frozen=True)
class ScoreBreakdown:
    """
    Decomposição dos scores utilizados para calcular o ranking
    de um serviço.

    Todos os scores usados pelo ranking devem estar normalizados
    no intervalo [0, 1].

    Os valores SHAP brutos não devem ser inseridos diretamente em
    shap_support_score. Esse campo deve receber o suporte SHAP
    normalizado produzido pelo ShapResultAdapter.
    """

    model_probability: float

    shap_support_score: float

    temporal_score: float

    graph_score: float

    anomaly_score: float

    final_score: float

    component_weights: Mapping[str, float] = field(
        default_factory=dict
    )

    normalization_method: str = (
        "normalized_weighted_ranking_v1"
    )

    def __post_init__(self) -> None:

        score_fields = (
            "model_probability",
            "shap_support_score",
            "temporal_score",
            "graph_score",
            "anomaly_score",
            "final_score",
        )

        for field_name in score_fields:

            validated_score = ensure_score(
                getattr(
                    self,
                    field_name,
                ),
                field_name=field_name,
            )

            object.__setattr__(
                self,
                field_name,
                validated_score,
            )

        normalized_weights: dict[str, float] = {}

        for component_name, value in (
            self.component_weights.items()
        ):

            weight = ensure_finite_number(
                value,
                field_name=(
                    "component_weights."
                    f"{component_name}"
                ),
            )

            if weight < 0:

                raise ValueError(
                    "O peso de um componente não pode "
                    "ser negativo. "
                    f"Componente: {component_name!r}"
                )

            normalized_weights[
                str(component_name)
            ] = weight

        object.__setattr__(
            self,
            "component_weights",
            freeze_value(
                normalized_weights
            ),
        )

        normalization_method = (
            self.normalization_method.strip()
        )

        if not normalization_method:

            raise ValueError(
                "normalization_method não pode ser vazio."
            )

        object.__setattr__(
            self,
            "normalization_method",
            normalization_method,
        )

    def to_dict(self) -> dict[str, Any]:

        return {
            "model_probability": (
                self.model_probability
            ),
            "shap_support_score": (
                self.shap_support_score
            ),
            "temporal_score": (
                self.temporal_score
            ),
            "graph_score": (
                self.graph_score
            ),
            "anomaly_score": (
                self.anomaly_score
            ),
            "final_score": (
                self.final_score
            ),
            "component_weights": json_safe(
                self.component_weights
            ),
            "normalization_method": (
                self.normalization_method
            ),
        }


@dataclass(frozen=True)
class RankedService:
    """
    Representa um serviço dentro do ranking final da RCA.

    evidence_ids contém somente referências a evidências existentes
    no campo evidence do RCAResult.
    """

    position: int

    service_name: str

    scores: ScoreBreakdown

    evidence_ids: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:

        if self.position < 1:

            raise ValueError(
                "position deve ser maior ou igual a 1."
            )

        service_name = self.service_name.strip()

        if not service_name:

            raise ValueError(
                "service_name não pode ser vazio."
            )

        normalized_evidence_ids = tuple(
            dict.fromkeys(
                evidence_id.strip()
                for evidence_id in self.evidence_ids
                if (
                    evidence_id
                    and evidence_id.strip()
                )
            )
        )

        object.__setattr__(
            self,
            "service_name",
            service_name,
        )

        object.__setattr__(
            self,
            "evidence_ids",
            normalized_evidence_ids,
        )

    def to_dict(self) -> dict[str, Any]:

        return {
            "position": self.position,
            "service_name": self.service_name,
            "scores": self.scores.to_dict(),
            "evidence_ids": list(
                self.evidence_ids
            ),
        }


@dataclass(frozen=True)
class ReliabilityDecision:
    """
    Resultado do Reliability Gate.

    Um RCAResult pode existir mesmo quando is_reliable=False.

    Quando is_reliable=False, o resultado pode ser armazenado para
    auditoria e avaliação, mas não deve necessariamente produzir
    um incidente operacional.
    """

    is_reliable: bool

    confidence: float

    confidence_method: str

    minimum_confidence: float

    margin_to_second: float | None

    minimum_margin: float

    passed_rules: tuple[str, ...] = field(
        default_factory=tuple
    )

    failed_rules: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:

        confidence = ensure_score(
            self.confidence,
            field_name="confidence",
        )

        minimum_confidence = ensure_score(
            self.minimum_confidence,
            field_name="minimum_confidence",
        )

        minimum_margin = ensure_score(
            self.minimum_margin,
            field_name="minimum_margin",
        )

        object.__setattr__(
            self,
            "confidence",
            confidence,
        )

        object.__setattr__(
            self,
            "minimum_confidence",
            minimum_confidence,
        )

        object.__setattr__(
            self,
            "minimum_margin",
            minimum_margin,
        )

        if self.margin_to_second is not None:

            margin_to_second = ensure_finite_number(
                self.margin_to_second,
                field_name="margin_to_second",
            )

            if not 0.0 <= margin_to_second <= 1.0:

                raise ValueError(
                    "margin_to_second deve estar "
                    "entre 0 e 1."
                )

            object.__setattr__(
                self,
                "margin_to_second",
                margin_to_second,
            )

        confidence_method = (
            self.confidence_method.strip()
        )

        if not confidence_method:

            raise ValueError(
                "confidence_method não pode ser vazio."
            )

        object.__setattr__(
            self,
            "confidence_method",
            confidence_method,
        )

        passed_rules = tuple(
            dict.fromkeys(
                rule.strip()
                for rule in self.passed_rules
                if rule and rule.strip()
            )
        )

        failed_rules = tuple(
            dict.fromkeys(
                rule.strip()
                for rule in self.failed_rules
                if rule and rule.strip()
            )
        )

        object.__setattr__(
            self,
            "passed_rules",
            passed_rules,
        )

        object.__setattr__(
            self,
            "failed_rules",
            failed_rules,
        )

        if self.is_reliable and failed_rules:

            raise ValueError(
                "Uma decisão marcada como confiável "
                "não pode possuir failed_rules."
            )

    def to_dict(self) -> dict[str, Any]:

        return {
            "is_reliable": (
                self.is_reliable
            ),
            "confidence": (
                self.confidence
            ),
            "confidence_method": (
                self.confidence_method
            ),
            "minimum_confidence": (
                self.minimum_confidence
            ),
            "margin_to_second": (
                self.margin_to_second
            ),
            "minimum_margin": (
                self.minimum_margin
            ),
            "passed_rules": list(
                self.passed_rules
            ),
            "failed_rules": list(
                self.failed_rules
            ),
        }


@dataclass(frozen=True)
class RCAResult:
    """
    Contrato final da análise de causa raiz.

    Este objeto deverá ser consumido por:

    - IncidentBuilder;
    - IncidentService;
    - OpenSearch;
    - baselines e avaliação;
    - dashboards;
    - futura camada de explicação com LLM.

    O LLM não poderá modificar root_cause_service, confidence,
    ranking ou evidence.
    """

    rca_result_id: str

    trace_id: str

    root_cause_service: str | None

    confidence: float

    ranking: tuple[RankedService, ...]

    evidence: tuple[EvidenceItem, ...]

    reliability: ReliabilityDecision

    model_version: str

    pipeline_version: str

    generated_at: datetime

    schema_version: str = "1.0"

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:

        rca_result_id = (
            self.rca_result_id.strip()
        )

        trace_id = self.trace_id.strip()

        model_version = (
            self.model_version.strip()
        )

        pipeline_version = (
            self.pipeline_version.strip()
        )

        schema_version = (
            self.schema_version.strip()
        )

        if not rca_result_id:

            raise ValueError(
                "rca_result_id não pode ser vazio."
            )

        if not trace_id:

            raise ValueError(
                "trace_id não pode ser vazio."
            )

        if not model_version:

            raise ValueError(
                "model_version não pode ser vazio."
            )

        if not pipeline_version:

            raise ValueError(
                "pipeline_version não pode ser vazio."
            )

        if not schema_version:

            raise ValueError(
                "schema_version não pode ser vazio."
            )

        confidence = ensure_score(
            self.confidence,
            field_name="confidence",
        )

        generated_at = normalize_datetime(
            self.generated_at
        )

        if generated_at is None:

            raise ValueError(
                "generated_at não pode ser None."
            )

        normalized_root_cause = (
            self.root_cause_service.strip()
            if self.root_cause_service
            else None
        )

        object.__setattr__(
            self,
            "rca_result_id",
            rca_result_id,
        )

        object.__setattr__(
            self,
            "trace_id",
            trace_id,
        )

        object.__setattr__(
            self,
            "root_cause_service",
            normalized_root_cause,
        )

        object.__setattr__(
            self,
            "confidence",
            confidence,
        )

        object.__setattr__(
            self,
            "ranking",
            tuple(self.ranking),
        )

        object.__setattr__(
            self,
            "evidence",
            tuple(self.evidence),
        )

        object.__setattr__(
            self,
            "model_version",
            model_version,
        )

        object.__setattr__(
            self,
            "pipeline_version",
            pipeline_version,
        )

        object.__setattr__(
            self,
            "schema_version",
            schema_version,
        )

        object.__setattr__(
            self,
            "generated_at",
            generated_at,
        )

        object.__setattr__(
            self,
            "metadata",
            freeze_value(
                dict(self.metadata)
            ),
        )

        self._validate_ranking()
        self._validate_evidence()
        self._validate_reliability()

    def _validate_ranking(self) -> None:

        if not self.ranking:

            if self.root_cause_service is not None:

                raise ValueError(
                    "root_cause_service deve ser None "
                    "quando o ranking estiver vazio."
                )

            if not math.isclose(
                self.confidence,
                0.0,
                abs_tol=1e-9,
            ):

                raise ValueError(
                    "confidence deve ser 0 quando "
                    "o ranking estiver vazio."
                )

            return

        expected_positions = list(
            range(
                1,
                len(self.ranking) + 1,
            )
        )

        actual_positions = [
            ranked_service.position
            for ranked_service in self.ranking
        ]

        if actual_positions != expected_positions:

            raise ValueError(
                "As posições do ranking devem ser "
                "sequenciais a partir de 1. "
                f"Recebido: {actual_positions}"
            )

        service_names = [
            ranked_service.service_name
            for ranked_service in self.ranking
        ]

        if len(service_names) != len(
            set(service_names)
        ):

            raise ValueError(
                "O ranking não pode possuir "
                "serviços duplicados."
            )

        top_service = self.ranking[0]

        if (
            self.root_cause_service
            != top_service.service_name
        ):

            raise ValueError(
                "root_cause_service deve corresponder "
                "ao primeiro serviço do ranking."
            )

        if not math.isclose(
            self.confidence,
            top_service.scores.final_score,
            abs_tol=1e-6,
        ):

            raise ValueError(
                "confidence deve corresponder ao "
                "final_score do primeiro colocado."
            )

        final_scores = [
            ranked_service.scores.final_score
            for ranked_service in self.ranking
        ]

        if final_scores != sorted(
            final_scores,
            reverse=True,
        ):

            raise ValueError(
                "O ranking deve estar ordenado pelo "
                "final_score em ordem decrescente."
            )

    def _validate_evidence(self) -> None:

        evidence_ids = [
            item.evidence_id
            for item in self.evidence
        ]

        if len(evidence_ids) != len(
            set(evidence_ids)
        ):

            raise ValueError(
                "Existem evidence_ids duplicados "
                "no RCAResult."
            )

        known_evidence_ids = set(
            evidence_ids
        )

        for ranked_service in self.ranking:

            unknown_ids = (
                set(
                    ranked_service.evidence_ids
                )
                - known_evidence_ids
            )

            if unknown_ids:

                raise ValueError(
                    "O ranking referencia evidências "
                    "inexistentes: "
                    f"{sorted(unknown_ids)}"
                )

    def _validate_reliability(self) -> None:

        if not math.isclose(
            self.confidence,
            self.reliability.confidence,
            abs_tol=1e-6,
        ):

            raise ValueError(
                "A confiança do RCAResult deve "
                "corresponder à confiança da "
                "ReliabilityDecision."
            )

    @classmethod
    def create(
        cls,
        trace_id: str,
        ranking: list[RankedService],
        evidence: list[EvidenceItem],
        reliability: ReliabilityDecision,
        model_version: str,
        pipeline_version: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> "RCAResult":
        """
        Cria o RCAResult utilizando o primeiro colocado do ranking
        como causa raiz.

        A RCAPipeline não deve definir root_cause_service
        separadamente, evitando inconsistência.
        """

        normalized_ranking = tuple(
            ranking
        )

        if normalized_ranking:

            root_cause_service = (
                normalized_ranking[
                    0
                ].service_name
            )

            confidence = (
                normalized_ranking[
                    0
                ].scores.final_score
            )

        else:

            root_cause_service = None

            confidence = 0.0

        return cls(
            rca_result_id=str(
                uuid4()
            ),
            trace_id=trace_id,
            root_cause_service=(
                root_cause_service
            ),
            confidence=confidence,
            ranking=normalized_ranking,
            evidence=tuple(evidence),
            reliability=reliability,
            model_version=model_version,
            pipeline_version=(
                pipeline_version
            ),
            generated_at=datetime.now(
                timezone.utc
            ),
            metadata=metadata or {},
        )

    def evidence_by_id(
        self,
    ) -> dict[str, EvidenceItem]:

        return {
            item.evidence_id: item
            for item in self.evidence
        }

    def evidence_for_service(
        self,
        service_name: str,
    ) -> tuple[EvidenceItem, ...]:

        return tuple(
            item
            for item in self.evidence
            if (
                item.service_name
                == service_name
            )
        )

    def ranked_service(
        self,
        service_name: str,
    ) -> RankedService | None:

        for item in self.ranking:

            if item.service_name == service_name:
                return item

        return None

    def to_dict(self) -> dict[str, Any]:

        return {
            "rca_result_id": (
                self.rca_result_id
            ),
            "trace_id": (
                self.trace_id
            ),
            "root_cause_service": (
                self.root_cause_service
            ),
            "confidence": (
                self.confidence
            ),
            "ranking": [
                item.to_dict()
                for item in self.ranking
            ],
            "evidence": [
                item.to_dict()
                for item in self.evidence
            ],
            "reliability": (
                self.reliability.to_dict()
            ),
            "model_version": (
                self.model_version
            ),
            "pipeline_version": (
                self.pipeline_version
            ),
            "generated_at": (
                self.generated_at.isoformat()
            ),
            "schema_version": (
                self.schema_version
            ),
            "metadata": json_safe(
                self.metadata
            ),
        }