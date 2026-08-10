from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import math
import json
import re

_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_MODES = {"generic", "raw_telemetry", "evidence_grounded"}
_ALLOWED_UNCERTAINTY = {"low", "medium", "high"}
_ALLOWED_FALLBACK_REASONS = {
    "no_llm_client",
    "timeout",
    "provider_error",
    "empty_response",
    "invalid_json",
    "invalid_schema",
    "root_cause_mismatch",
    "invalid_evidence_reference",
    "missing_evidence_citation",
    "explanation_validation_failed",
}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, set):
        return tuple(_freeze(v) for v in sorted(value, key=str))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw_json(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(v) for v in value]
    if isinstance(value, set):
        return [_thaw_json(v) for v in sorted(value, key=str)]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _ensure_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} deve ser str")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} não pode ser vazio")
    return text


def _ensure_optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _ensure_text(value, field_name)


def _ensure_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} deve ser bool exato")
    return value


def _ensure_int_or_none(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} deve ser int não booleano ou None")
    if value < 0:
        raise ValueError(f"{field_name} não pode ser negativo")
    return value


def _ensure_finite_number_or_none(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} deve ser número finito ou None")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{field_name} inválido")
    return converted


def _ensure_score(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} deve ser número finito")
    converted = float(value)
    if not math.isfinite(converted) or not 0.0 <= converted <= 1.0:
        raise ValueError(f"{field_name} deve estar em [0,1]")
    return converted


def _coerce_datetime_utc(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} deve ser datetime ou None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} deve ser timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_rfc3339_utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} deve ser str")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} não pode ser vazio")
    candidate = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"{field_name} deve conter timezone"
        )
    return parsed.astimezone(timezone.utc)


def _format_rfc3339_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return (
        normalized.replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
    )


def _normalize_analysis_window(
    value: Any,
) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise TypeError(
            "analysis_window deve ser um mapping explícito"
        )
    if set(value) != {"start", "end"}:
        raise ValueError(
            "analysis_window deve conter apenas start e end"
        )

    start = _parse_rfc3339_utc(
        value["start"],
        "analysis_window.start",
    )
    end = _parse_rfc3339_utc(
        value["end"],
        "analysis_window.end",
    )

    if end < start:
        raise ValueError(
            "analysis_window.end deve ser maior ou igual a start"
        )

    return MappingProxyType(
        {
            "start": _format_rfc3339_utc(start),
            "end": _format_rfc3339_utc(end),
        }
    )


def _coerce_source_ids(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} deve ser uma coleção de strings")
    items = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{field_name} contém valor não textual")
        text = item.strip()
        if not text:
            raise ValueError(f"{field_name} contém string vazia")
        if not _EVIDENCE_ID.fullmatch(text):
            raise ValueError(f"{field_name} contém ID inválido")
        items.append(text)
    return tuple(dict.fromkeys(items))


@dataclass(frozen=True)
class EvidenceReferenceV1:
    evidence_id: str
    evidence_type: str
    source_component: str
    service_name: str | None = None
    description: str | None = None
    metric_name: str | None = None
    raw_value: Any = None
    normalized_value: float | None = None
    direction: str | None = None
    observed_at: datetime | None = None
    selected_for_explanation: bool = False
    source_trace_ids: tuple[str, ...] = ()
    source_span_ids: tuple[str, ...] = ()
    source_log_ids: tuple[str, ...] = ()
    source_metric_series_ids: tuple[str, ...] = ()
    source_business_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _ensure_text(self.evidence_id, "evidence_id"))
        if not _EVIDENCE_ID.fullmatch(self.evidence_id):
            raise ValueError("evidence_id inválido")
        object.__setattr__(self, "evidence_type", _ensure_text(self.evidence_type, "evidence_type"))
        object.__setattr__(self, "source_component", _ensure_text(self.source_component, "source_component"))
        object.__setattr__(self, "service_name", _ensure_optional_text(self.service_name, "service_name"))
        object.__setattr__(self, "description", _ensure_optional_text(self.description, "description"))
        object.__setattr__(self, "metric_name", _ensure_optional_text(self.metric_name, "metric_name"))
        object.__setattr__(self, "normalized_value", _ensure_finite_number_or_none(self.normalized_value, "normalized_value"))
        if self.normalized_value is not None and not 0.0 <= self.normalized_value <= 1.0:
            raise ValueError("normalized_value deve estar em [0,1]")
        object.__setattr__(self, "selected_for_explanation", _ensure_bool(self.selected_for_explanation, "selected_for_explanation"))
        object.__setattr__(self, "observed_at", _coerce_datetime_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "source_trace_ids", _coerce_source_ids(self.source_trace_ids, "source_trace_ids"))
        object.__setattr__(self, "source_span_ids", _coerce_source_ids(self.source_span_ids, "source_span_ids"))
        object.__setattr__(self, "source_log_ids", _coerce_source_ids(self.source_log_ids, "source_log_ids"))
        object.__setattr__(self, "source_metric_series_ids", _coerce_source_ids(self.source_metric_series_ids, "source_metric_series_ids"))
        object.__setattr__(self, "source_business_event_ids", _coerce_source_ids(self.source_business_event_ids, "source_business_event_ids"))
        object.__setattr__(self, "raw_value", _freeze(self.raw_value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "source_component": self.source_component,
            "service_name": self.service_name,
            "description": self.description,
            "metric_name": self.metric_name,
            "raw_value": _thaw_json(self.raw_value),
            "normalized_value": self.normalized_value,
            "direction": self.direction,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "selected_for_explanation": self.selected_for_explanation,
            "source_trace_ids": list(self.source_trace_ids),
            "source_span_ids": list(self.source_span_ids),
            "source_log_ids": list(self.source_log_ids),
            "source_metric_series_ids": list(self.source_metric_series_ids),
            "source_business_event_ids": list(self.source_business_event_ids),
        }


@dataclass(frozen=True)
class EvidenceStatementV1:
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        text = _ensure_text(self.text, "text")
        if isinstance(self.evidence_ids, (str, bytes, bytearray)) or not isinstance(self.evidence_ids, Sequence):
            raise TypeError("evidence_ids deve ser uma coleção não textual")
        ids = []
        for item in self.evidence_ids:
            if not isinstance(item, str):
                raise TypeError("evidence_ids deve conter apenas strings")
            item = item.strip()
            if not item:
                raise ValueError("evidence_ids contém string vazia")
            if not _EVIDENCE_ID.fullmatch(item):
                raise ValueError("evidence_ids contém ID inválido")
            ids.append(item)
        if not ids:
            raise ValueError("evidence_ids deve conter ao menos um ID")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(ids)))

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class EvidenceObjectV1:
    rca_result_id: str
    trace_id: str
    incident_id: str | None
    analysis_window: Mapping[str, Any] | None
    generated_at: datetime
    pipeline_version: str
    model_version: str
    model_hash: str
    ranked_root_causes: tuple[str, ...]
    model_probabilities: Mapping[str, float]
    final_ranking_scores: Mapping[str, float]
    confidence: float
    margin_to_second: float | None
    reliability: bool
    evidence_catalog: tuple[EvidenceReferenceV1, ...]
    root_cause_evidence_ids: tuple[str, ...]
    top_shap_features: tuple[EvidenceReferenceV1, ...]
    feature_values: Mapping[str, Any]
    feature_baselines: Mapping[str, Any]
    event_paths: tuple[str, ...]
    dependency_paths: tuple[str, ...]
    temporal_order: tuple[str, ...]
    first_failure_event: str | None
    missing_evidence: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rca_result_id", _ensure_text(self.rca_result_id, "rca_result_id"))
        object.__setattr__(self, "trace_id", _ensure_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "incident_id", _ensure_optional_text(self.incident_id, "incident_id"))
        if self.analysis_window is not None:
            object.__setattr__(
                self,
                "analysis_window",
                _normalize_analysis_window(
                    self.analysis_window
                ),
            )
        else:
            object.__setattr__(self, "analysis_window", None)
        generated_at = _coerce_datetime_utc(self.generated_at, "generated_at")
        if generated_at is None:
            raise TypeError("generated_at deve existir")
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "pipeline_version", _ensure_text(self.pipeline_version, "pipeline_version"))
        object.__setattr__(self, "model_version", _ensure_text(self.model_version, "model_version"))
        object.__setattr__(self, "model_hash", _ensure_optional_text(self.model_hash, "model_hash") or "")
        object.__setattr__(self, "ranked_root_causes", tuple(_ensure_text(item, "ranked_root_causes") for item in self.ranked_root_causes))
        object.__setattr__(self, "model_probabilities", _freeze({k: _ensure_score(v, f"model_probabilities.{k}") for k, v in dict(self.model_probabilities).items()}))
        object.__setattr__(self, "final_ranking_scores", _freeze({k: _ensure_score(v, f"final_ranking_scores.{k}") for k, v in dict(self.final_ranking_scores).items()}))
        object.__setattr__(self, "confidence", _ensure_score(self.confidence, "confidence"))
        object.__setattr__(self, "margin_to_second", _ensure_finite_number_or_none(self.margin_to_second, "margin_to_second"))
        object.__setattr__(self, "reliability", _ensure_bool(self.reliability, "reliability"))
        object.__setattr__(self, "evidence_catalog", tuple(self.evidence_catalog))
        object.__setattr__(self, "root_cause_evidence_ids", _coerce_source_ids(self.root_cause_evidence_ids, "root_cause_evidence_ids"))
        object.__setattr__(self, "top_shap_features", tuple(self.top_shap_features))
        object.__setattr__(self, "feature_values", _freeze(self.feature_values))
        object.__setattr__(self, "feature_baselines", _freeze(self.feature_baselines))
        object.__setattr__(self, "event_paths", tuple(_ensure_text(item, "event_paths") for item in self.event_paths))
        object.__setattr__(self, "dependency_paths", tuple(_ensure_text(item, "dependency_paths") for item in self.dependency_paths))
        object.__setattr__(self, "temporal_order", tuple(_ensure_text(item, "temporal_order") for item in self.temporal_order))
        object.__setattr__(self, "first_failure_event", _ensure_optional_text(self.first_failure_event, "first_failure_event"))
        object.__setattr__(self, "missing_evidence", tuple(dict.fromkeys(_ensure_text(item, "missing_evidence") for item in self.missing_evidence)))
        object.__setattr__(self, "limitations", tuple(dict.fromkeys(_ensure_text(item, "limitations") for item in self.limitations)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rca_result_id": self.rca_result_id,
            "trace_id": self.trace_id,
            "incident_id": self.incident_id,
            "analysis_window": _thaw_json(self.analysis_window) if self.analysis_window is not None else None,
            "generated_at": self.generated_at.isoformat(),
            "pipeline_version": self.pipeline_version,
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "ranked_root_causes": list(self.ranked_root_causes),
            "model_probabilities": _thaw_json(self.model_probabilities),
            "final_ranking_scores": _thaw_json(self.final_ranking_scores),
            "confidence": self.confidence,
            "margin_to_second": self.margin_to_second,
            "reliability": self.reliability,
            "evidence_catalog": [item.to_dict() for item in self.evidence_catalog],
            "root_cause_evidence_ids": list(self.root_cause_evidence_ids),
            "top_shap_features": [item.to_dict() for item in self.top_shap_features],
            "feature_values": _thaw_json(self.feature_values),
            "feature_baselines": _thaw_json(self.feature_baselines),
            "event_paths": list(self.event_paths),
            "dependency_paths": list(self.dependency_paths),
            "temporal_order": list(self.temporal_order),
            "first_failure_event": self.first_failure_event,
            "missing_evidence": list(self.missing_evidence),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class PromptPayload:
    prompt_builder_version: str
    prompt_schema_version: str
    mode: str
    temperature: float
    prompt: str
    prompt_hash: str
    evidence_hash: str
    evidence_object: EvidenceObjectV1
    sanitized_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_builder_version", _ensure_text(self.prompt_builder_version, "prompt_builder_version"))
        object.__setattr__(self, "prompt_schema_version", _ensure_text(self.prompt_schema_version, "prompt_schema_version"))
        object.__setattr__(self, "mode", _ensure_text(self.mode, "mode"))
        if self.mode not in _ALLOWED_MODES:
            raise ValueError("mode inválido")
        object.__setattr__(self, "temperature", _ensure_finite_number_or_none(self.temperature, "temperature"))
        object.__setattr__(self, "prompt", _ensure_text(self.prompt, "prompt"))
        if not _SHA256.fullmatch(self.prompt_hash):
            raise ValueError("prompt_hash inválido")
        if not _SHA256.fullmatch(self.evidence_hash):
            raise ValueError("evidence_hash inválido")
        object.__setattr__(self, "sanitized_context", _freeze(self.sanitized_context))

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_builder_version": self.prompt_builder_version,
            "prompt_schema_version": self.prompt_schema_version,
            "mode": self.mode,
            "temperature": self.temperature,
            "prompt": self.prompt,
            "prompt_hash": self.prompt_hash,
            "evidence_hash": self.evidence_hash,
            "evidence_object": self.evidence_object.to_dict(),
            "sanitized_context": _thaw_json(self.sanitized_context),
        }


@dataclass(frozen=True)
class StructuredExplanation:
    schema_version: str
    root_cause: str | None
    summary: str
    evidence_statements: tuple[EvidenceStatementV1, ...]
    propagation_path: tuple[str, ...]
    uncertainty: str
    missing_evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    recommendations: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    mode: str
    model_name: str
    model_version: str
    model_hash: str
    prompt_hash: str
    confidence: float
    reliability: bool
    uncertainty_policy_version: str = "uncertainty-policy-v1"
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    used_fallback: bool = False
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _ensure_text(self.schema_version, "schema_version"))
        if self.schema_version != "structured-explanation-v1":
            raise ValueError("schema_version inválida")
        object.__setattr__(self, "root_cause", _ensure_optional_text(self.root_cause, "root_cause"))
        object.__setattr__(self, "summary", _ensure_text(self.summary, "summary"))
        if isinstance(self.evidence_statements, (str, bytes, bytearray)) or not isinstance(self.evidence_statements, Sequence):
            raise TypeError("evidence_statements deve ser uma coleção")
        statements = []
        for stmt in self.evidence_statements:
            if not isinstance(stmt, EvidenceStatementV1):
                raise TypeError("evidence_statements deve conter EvidenceStatementV1")
            statements.append(stmt)
        object.__setattr__(self, "evidence_statements", tuple(statements))
        object.__setattr__(self, "propagation_path", tuple(_ensure_text(item, "propagation_path") for item in self.propagation_path))
        object.__setattr__(self, "uncertainty", _ensure_text(self.uncertainty, "uncertainty"))
        if self.uncertainty not in _ALLOWED_UNCERTAINTY:
            raise ValueError("uncertainty inválida")
        object.__setattr__(self, "missing_evidence", tuple(dict.fromkeys(_ensure_text(item, "missing_evidence") for item in self.missing_evidence)))
        object.__setattr__(self, "limitations", tuple(dict.fromkeys(_ensure_text(item, "limitations") for item in self.limitations)))
        object.__setattr__(self, "recommendations", tuple(_ensure_text(item, "recommendations") for item in self.recommendations))
        object.__setattr__(self, "cited_evidence_ids", _coerce_source_ids(self.cited_evidence_ids, "cited_evidence_ids"))
        object.__setattr__(self, "mode", _ensure_text(self.mode, "mode"))
        if self.uncertainty_policy_version != "uncertainty-policy-v1":
            raise ValueError("uncertainty_policy_version inválida")
        if self.mode not in _ALLOWED_MODES:
            raise ValueError("mode inválido")
        object.__setattr__(self, "model_name", _ensure_text(self.model_name, "model_name"))
        object.__setattr__(self, "model_version", _ensure_text(self.model_version, "model_version"))
        object.__setattr__(self, "model_hash", _ensure_optional_text(self.model_hash, "model_hash") or "")
        if not _SHA256.fullmatch(self.prompt_hash):
            raise ValueError("prompt_hash inválido")
        object.__setattr__(self, "confidence", _ensure_score(self.confidence, "confidence"))
        object.__setattr__(self, "reliability", _ensure_bool(self.reliability, "reliability"))
        object.__setattr__(self, "tokens_prompt", _ensure_int_or_none(self.tokens_prompt, "tokens_prompt"))
        object.__setattr__(self, "tokens_completion", _ensure_int_or_none(self.tokens_completion, "tokens_completion"))
        object.__setattr__(self, "latency_ms", _ensure_finite_number_or_none(self.latency_ms, "latency_ms"))
        object.__setattr__(self, "cost_usd", _ensure_finite_number_or_none(self.cost_usd, "cost_usd"))
        object.__setattr__(self, "used_fallback", _ensure_bool(self.used_fallback, "used_fallback"))
        if self.used_fallback:
            if self.fallback_reason not in _ALLOWED_FALLBACK_REASONS:
                raise ValueError("fallback_reason inválido")
        elif self.fallback_reason is not None:
            raise ValueError("fallback_reason deve ser None quando used_fallback=False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root_cause": self.root_cause,
            "summary": self.summary,
            "evidence_statements": [item.to_dict() for item in self.evidence_statements],
            "propagation_path": list(self.propagation_path),
            "uncertainty": self.uncertainty,
            "missing_evidence": list(self.missing_evidence),
            "limitations": list(self.limitations),
            "recommendations": list(self.recommendations),
            "cited_evidence_ids": list(self.cited_evidence_ids),
            "mode": self.mode,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "prompt_hash": self.prompt_hash,
            "confidence": self.confidence,
            "reliability": self.reliability,
            "uncertainty_policy_version": self.uncertainty_policy_version,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class ExplanationProvenanceRecordV1:
    provenance_id: str
    schema_version: str
    created_at: datetime
    rca_result_id: str
    trace_id: str
    root_cause_service: str | None
    evidence_hash: str
    prompt_builder_version: str
    prompt_schema_version: str
    prompt_hash: str
    response_hash: str | None
    requested_model: str | None
    actual_model: str | None
    mode: str
    temperature: float
    used_fallback: bool
    fallback_reason: str | None
    validation_status: str
    tokens_prompt: int | None
    tokens_completion: int | None
    latency_ms: float | None
    cost_usd: float | None
    explanation_hash: str
    uncertainty_policy_version: str = "uncertainty-policy-v1"
    evidence_object: EvidenceObjectV1 | None = None
    prompt_payload: PromptPayload | None = None
    explanation: StructuredExplanation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance_id", _ensure_text(self.provenance_id, "provenance_id"))
        if not _SHA256.fullmatch(self.provenance_id):
            raise ValueError("provenance_id inválido")
        object.__setattr__(self, "schema_version", _ensure_text(self.schema_version, "schema_version"))
        if self.schema_version != "explanation-provenance-v1":
            raise ValueError("schema_version inválida")
        object.__setattr__(self, "created_at", _coerce_datetime_utc(self.created_at, "created_at"))
        object.__setattr__(self, "rca_result_id", _ensure_text(self.rca_result_id, "rca_result_id"))
        object.__setattr__(self, "trace_id", _ensure_text(self.trace_id, "trace_id"))
        object.__setattr__(self, "root_cause_service", _ensure_optional_text(self.root_cause_service, "root_cause_service"))
        object.__setattr__(self, "evidence_hash", _ensure_text(self.evidence_hash, "evidence_hash"))
        if not _SHA256.fullmatch(self.evidence_hash):
            raise ValueError("evidence_hash inválido")
        object.__setattr__(self, "prompt_builder_version", _ensure_text(self.prompt_builder_version, "prompt_builder_version"))
        object.__setattr__(self, "prompt_schema_version", _ensure_text(self.prompt_schema_version, "prompt_schema_version"))
        object.__setattr__(self, "prompt_hash", _ensure_text(self.prompt_hash, "prompt_hash"))
        if not _SHA256.fullmatch(self.prompt_hash):
            raise ValueError("prompt_hash inválido")
        if self.response_hash is not None:
            if not _SHA256.fullmatch(self.response_hash):
                raise ValueError("response_hash inválido")
        object.__setattr__(self, "requested_model", _ensure_optional_text(self.requested_model, "requested_model"))
        object.__setattr__(self, "actual_model", _ensure_optional_text(self.actual_model, "actual_model"))
        object.__setattr__(self, "mode", _ensure_text(self.mode, "mode"))
        if self.mode not in _ALLOWED_MODES:
            raise ValueError("mode inválido")
        object.__setattr__(self, "temperature", _ensure_finite_number_or_none(self.temperature, "temperature"))
        object.__setattr__(self, "used_fallback", _ensure_bool(self.used_fallback, "used_fallback"))
        if self.used_fallback:
            if self.fallback_reason not in _ALLOWED_FALLBACK_REASONS:
                raise ValueError("fallback_reason inválido")
        elif self.fallback_reason is not None:
            raise ValueError("fallback_reason deve ser None quando used_fallback=False")
        object.__setattr__(self, "validation_status", _ensure_text(self.validation_status, "validation_status"))
        if self.validation_status != "passed":
            raise ValueError("validation_status deve ser passed")
        object.__setattr__(self, "tokens_prompt", _ensure_int_or_none(self.tokens_prompt, "tokens_prompt"))
        object.__setattr__(self, "tokens_completion", _ensure_int_or_none(self.tokens_completion, "tokens_completion"))
        object.__setattr__(self, "latency_ms", _ensure_finite_number_or_none(self.latency_ms, "latency_ms"))
        object.__setattr__(self, "cost_usd", _ensure_finite_number_or_none(self.cost_usd, "cost_usd"))
        object.__setattr__(self, "explanation_hash", _ensure_text(self.explanation_hash, "explanation_hash"))
        if not _SHA256.fullmatch(self.explanation_hash):
            raise ValueError("explanation_hash inválido")
        expected_provenance_id = sha256_canonical(
            {
                "prompt_hash": self.prompt_hash,
                "explanation_hash": self.explanation_hash,
                "response_hash": self.response_hash,
                "rca_result_id": self.rca_result_id,
                "trace_id": self.trace_id,
            }
        )
        if self.provenance_id != expected_provenance_id:
            raise ValueError("provenance_id não corresponde ao conteúdo")

        if self.evidence_object is not None:
            if self.evidence_object.rca_result_id != self.rca_result_id:
                raise ValueError(
                    "evidence_object.rca_result_id não corresponde ao provenance"
                )
            if self.evidence_object.trace_id != self.trace_id:
                raise ValueError(
                    "evidence_object.trace_id não corresponde ao provenance"
                )
            if self.evidence_object.ranked_root_causes:
                if self.root_cause_service != self.evidence_object.ranked_root_causes[0]:
                    raise ValueError(
                        "root_cause_service não corresponde ao evidence_object"
                    )
            elif self.root_cause_service is not None:
                raise ValueError(
                    "root_cause_service deve ser None quando o ranking estiver vazio"
                )

        if self.prompt_payload is not None:
            if self.prompt_payload.evidence_hash != self.evidence_hash:
                raise ValueError("prompt_payload.evidence_hash não corresponde")
            if self.prompt_payload.prompt_builder_version != self.prompt_builder_version:
                raise ValueError("prompt_builder_version não corresponde")
            if self.prompt_payload.prompt_schema_version != self.prompt_schema_version:
                raise ValueError("prompt_schema_version não corresponde")
            if self.prompt_payload.prompt_hash != self.prompt_hash:
                raise ValueError("prompt_hash não corresponde")
            if self.prompt_payload.mode != self.mode:
                raise ValueError("mode não corresponde ao prompt payload")
            if self.prompt_payload.temperature != self.temperature:
                raise ValueError("temperature não corresponde ao prompt payload")
            if self.evidence_object is not None and self.prompt_payload.evidence_object != self.evidence_object:
                raise ValueError("prompt_payload.evidence_object não corresponde ao evidence_object")

        if self.explanation is not None:
            if self.root_cause_service != self.explanation.root_cause:
                raise ValueError("root_cause_service não corresponde à explanation")
            if self.prompt_hash != self.explanation.prompt_hash:
                raise ValueError("prompt_hash não corresponde à explanation")
            if self.mode != self.explanation.mode:
                raise ValueError("mode não corresponde à explanation")
            if self.used_fallback != self.explanation.used_fallback:
                raise ValueError("used_fallback não corresponde à explanation")
            if self.fallback_reason != self.explanation.fallback_reason:
                raise ValueError("fallback_reason não corresponde à explanation")
            if self.uncertainty_policy_version != "uncertainty-policy-v1":
                raise ValueError("uncertainty_policy_version inválida")
            if self.uncertainty_policy_version != self.explanation.uncertainty_policy_version:
                raise ValueError("uncertainty_policy_version não corresponde à explanation")
            if self.explanation_hash != sha256_canonical(self.explanation.to_dict()):
                raise ValueError("explanation_hash não corresponde à explanation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "rca_result_id": self.rca_result_id,
            "trace_id": self.trace_id,
            "root_cause_service": self.root_cause_service,
            "evidence_hash": self.evidence_hash,
            "prompt_builder_version": self.prompt_builder_version,
            "prompt_schema_version": self.prompt_schema_version,
            "prompt_hash": self.prompt_hash,
            "response_hash": self.response_hash,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "mode": self.mode,
            "temperature": self.temperature,
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
            "validation_status": self.validation_status,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "explanation_hash": self.explanation_hash,
            "uncertainty_policy_version": self.uncertainty_policy_version,
        }
