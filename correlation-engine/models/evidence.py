from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any


class EvidenceType(str, Enum):
    """
    Origem semântica da evidência.

    Os tipos METRIC, LOG e BUSINESS_EVENT já são reservados para
    permitir a expansão futura da plataforma sem alterar o contrato.
    """

    TRACE = "trace"
    TEMPORAL = "temporal"
    GRAPH = "graph"
    ANOMALY = "anomaly"
    MODEL = "model"
    SHAP = "shap"
    RANKING = "ranking"

    METRIC = "metric"
    LOG = "log"
    BUSINESS_EVENT = "business_event"


class EvidenceDirection(str, Enum):
    """
    Relação da evidência com a hipótese de que um serviço seja
    a causa raiz.
    """

    SUPPORTING = "supporting"
    OPPOSING = "opposing"
    NEUTRAL = "neutral"


def ensure_finite_number(
    value: float | int,
    field_name: str,
) -> float:
    """
    Converte um número para float e impede NaN ou infinito.
    """

    converted = float(value)

    if not math.isfinite(converted):
        raise ValueError(
            f"{field_name} deve ser um número finito. "
            f"Valor recebido: {value!r}"
        )

    return converted


def ensure_score(
    value: float | int,
    field_name: str,
) -> float:
    """
    Valida scores normalizados no intervalo [0, 1].
    """

    converted = ensure_finite_number(
        value=value,
        field_name=field_name,
    )

    if not 0.0 <= converted <= 1.0:
        raise ValueError(
            f"{field_name} deve estar entre 0 e 1. "
            f"Valor recebido: {converted}"
        )

    return converted


def normalize_datetime(
    value: datetime | None,
) -> datetime | None:
    """
    Garante que timestamps possuam timezone.

    Timestamps sem timezone são considerados UTC.
    """

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def freeze_value(value: Any) -> Any:
    """
    Converte estruturas mutáveis em estruturas somente leitura.

    Isso ajuda a preservar os snapshots usados pela RCA e,
    futuramente, pelo LLM.
    """

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): freeze_value(item)
                for key, item in value.items()
            }
        )

    if isinstance(value, set):
        return tuple(
            freeze_value(item)
            for item in sorted(
                value,
                key=lambda current: str(current),
            )
        )

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(
            freeze_value(item)
            for item in value
        )

    return value


def json_safe(value: Any) -> Any:
    """
    Converte objetos comuns da pipeline em estruturas serializáveis
    por JSON.

    A função também trata escalares NumPy sem depender diretamente
    da biblioteca NumPy.
    """

    if value is None:
        return None

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return normalize_datetime(value).isoformat()

    if isinstance(value, Mapping):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, set):
        return [
            json_safe(item)
            for item in sorted(
                value,
                key=lambda current: str(current),
            )
        ]

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return json_safe(value.to_dict())

    # Suporte a escalares NumPy, como np.float64 e np.int64.
    if hasattr(value, "item") and callable(value.item):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return ensure_finite_number(
            value,
            field_name="valor serializado",
        )

    if isinstance(value, str):
        return value

    return str(value)


@dataclass(frozen=True)
class EvidenceItem:
    """
    Evidência auditável produzida por um componente da pipeline.

    raw_value:
        Valor original observado ou calculado.

    normalized_value:
        Valor entre 0 e 1 utilizado em comparações ou no ranking.

    description:
        Descrição determinística produzida pela própria aplicação.
        O LLM não será responsável por inventar o significado da
        evidência.
    """

    evidence_id: str
    evidence_type: EvidenceType
    source_component: str
    description: str

    service_name: str | None = None
    metric_name: str | None = None

    raw_value: Any = None
    normalized_value: float | None = None
    unit: str | None = None

    direction: EvidenceDirection = EvidenceDirection.NEUTRAL
    observed_at: datetime | None = None

    attributes: Mapping[str, Any] = field(
        default_factory=dict
    )

    tags: tuple[str, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        evidence_id = self.evidence_id.strip()
        source_component = self.source_component.strip()
        description = self.description.strip()

        if not evidence_id:
            raise ValueError(
                "evidence_id não pode ser vazio."
            )

        if any(character.isspace() for character in evidence_id):
            raise ValueError(
                "evidence_id não pode conter espaços: "
                f"{evidence_id!r}"
            )

        if not source_component:
            raise ValueError(
                "source_component não pode ser vazio."
            )

        if not description:
            raise ValueError(
                "description não pode ser vazia."
            )

        if self.normalized_value is not None:
            normalized_value = ensure_score(
                self.normalized_value,
                field_name="normalized_value",
            )

            object.__setattr__(
                self,
                "normalized_value",
                normalized_value,
            )

        object.__setattr__(
            self,
            "evidence_id",
            evidence_id,
        )

        object.__setattr__(
            self,
            "source_component",
            source_component,
        )

        object.__setattr__(
            self,
            "description",
            description,
        )

        object.__setattr__(
            self,
            "observed_at",
            normalize_datetime(self.observed_at),
        )

        object.__setattr__(
            self,
            "raw_value",
            freeze_value(self.raw_value),
        )

        object.__setattr__(
            self,
            "attributes",
            freeze_value(dict(self.attributes)),
        )

        object.__setattr__(
            self,
            "tags",
            tuple(
                str(tag).strip()
                for tag in self.tags
                if str(tag).strip()
            ),
        )

    def supports_service(
        self,
        service_name: str,
    ) -> bool:
        return (
            self.service_name == service_name
            and self.direction == EvidenceDirection.SUPPORTING
        )

    def opposes_service(
        self,
        service_name: str,
    ) -> bool:
        return (
            self.service_name == service_name
            and self.direction == EvidenceDirection.OPPOSING
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type.value,
            "source_component": self.source_component,
            "description": self.description,
            "service_name": self.service_name,
            "metric_name": self.metric_name,
            "raw_value": json_safe(self.raw_value),
            "normalized_value": self.normalized_value,
            "unit": self.unit,
            "direction": self.direction.value,
            "observed_at": (
                self.observed_at.isoformat()
                if self.observed_at
                else None
            ),
            "attributes": json_safe(self.attributes),
            "tags": list(self.tags),
        }