from __future__ import annotations

import json
import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar


class SignalType(str, Enum):
    TRACE = "trace"
    LOG = "log"
    METRIC = "metric"
    BUSINESS_EVENT = "business_event"


_SAFE_ID = re.compile(r"^[^\s\x00-\x1f\x7f]+$")
_OTLP_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_OTLP_SPAN_ID = re.compile(r"^[0-9a-fA-F]{16}$")


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} deve ser uma string não vazia.")
    return value.strip()


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _identifier(value: Any, name: str) -> str | None:
    result = _optional_text(value, name)
    if result is not None and not _SAFE_ID.fullmatch(result):
        raise ValueError(
            f"{name} contém espaços ou caracteres de controle inválidos."
        )
    return result


def _required_identifier(value: Any, name: str) -> str:
    result = _identifier(value, name)
    if result is None:
        raise ValueError(f"{name} deve ser uma string não vazia.")
    return result


def _utc_datetime(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(candidate)
        except ValueError as exception:
            raise ValueError(f"{name} não é um timestamp ISO 8601 válido.") from exception
    if not isinstance(value, datetime):
        raise ValueError(f"{name} deve ser datetime ou ISO 8601.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} deve possuir timezone UTC.")
    return value.astimezone(timezone.utc)


def _finite_number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} deve ser numérico.")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} deve ser finito.")
    return converted


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=str))
    return value


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Metadados JSON não podem conter NaN ou infinito.")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        normalized = _utc_datetime(value, "timestamp serializado")
        return normalized.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    "Todas as chaves de objetos JSON devem ser strings."
                )
            result[key] = _json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_json_value(item) for item in value]
    raise ValueError(f"Valor não serializável no contrato: {type(value).__name__}.")


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{name} deve ser uma sequência de strings.")
    result = tuple(_required_text(item, name) for item in value)
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class UnifiedEvent:
    """Envelope normalizado e imutável do multimodal schema v1."""

    SCHEMA_VERSION: ClassVar[str] = "unified-event-v1"

    event_id: str
    event_name: str
    event_type: str
    signal_type: SignalType | str
    event_timestamp: datetime | str

    observed_timestamp: datetime | str | None = None
    ingested_timestamp: datetime | str | None = None

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    trace_flags: int | None = None
    trace_state: str | None = None
    span_links: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    correlation_id: str | None = None
    business_entity_type: str | None = None
    business_entity_id: str | None = None
    business_operation: str | None = None
    business_outcome: str | None = None

    service_name: str | None = None
    service_instance_id: str | None = None
    service_version: str | None = None
    deployment_environment: str | None = None

    severity: str | None = None
    error_type: str | None = None
    status_code: str | int | None = None

    metric_name: str | None = None
    metric_unit: str | None = None
    metric_value: float | int | None = None
    metric_aggregation: str | None = None

    attributes: Mapping[str, Any] = field(default_factory=dict)
    resource_attributes: Mapping[str, Any] = field(default_factory=dict)
    instrumentation_scope: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    source_reference: str | None = None
    source_dataset: str | None = None

    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    late_arrival_ms: float | int | None = None
    data_quality_flags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_id", _required_identifier(self.event_id, "event_id")
        )
        object.__setattr__(self, "event_name", _required_text(self.event_name, "event_name"))
        object.__setattr__(self, "event_type", _required_text(self.event_type, "event_type"))
        try:
            signal_type = SignalType(self.signal_type)
        except (TypeError, ValueError) as exception:
            raise ValueError(
                "signal_type deve ser trace, log, metric ou business_event."
            ) from exception
        object.__setattr__(self, "signal_type", signal_type)

        for name in ("event_timestamp", "observed_timestamp", "ingested_timestamp"):
            object.__setattr__(self, name, _utc_datetime(getattr(self, name), name))
        for name in (
            "trace_id", "span_id", "parent_span_id", "correlation_id",
            "business_entity_id", "service_instance_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        for name in (
            "trace_state", "business_entity_type", "business_operation",
            "business_outcome", "service_name", "service_version",
            "deployment_environment", "severity", "error_type", "metric_name",
            "metric_unit", "metric_aggregation", "source_reference", "source_dataset",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))

        if self.trace_flags is not None and (
            isinstance(self.trace_flags, bool)
            or not isinstance(self.trace_flags, int)
            or not 0 <= self.trace_flags <= 255
        ):
            raise ValueError("trace_flags deve ser inteiro entre 0 e 255.")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, (str, int))
        ):
            raise ValueError(
                "status_code deve ser string, inteiro não booleano ou null."
            )
        object.__setattr__(self, "metric_value", _finite_number(self.metric_value, "metric_value"))
        late_arrival = _finite_number(self.late_arrival_ms, "late_arrival_ms")
        if late_arrival is not None and late_arrival < 0:
            raise ValueError("late_arrival_ms não pode ser negativo.")
        object.__setattr__(self, "late_arrival_ms", late_arrival)

        for name in ("attributes", "resource_attributes", "instrumentation_scope"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} deve ser um mapping.")
            _json_value(value)
            object.__setattr__(self, name, _freeze(value))
        if isinstance(self.span_links, (str, bytes)) or not isinstance(self.span_links, Sequence):
            raise ValueError("span_links deve ser uma sequência de mappings.")
        if any(not isinstance(link, Mapping) for link in self.span_links):
            raise ValueError("Cada span_link deve ser um mapping.")
        _json_value(self.span_links)
        object.__setattr__(self, "span_links", _freeze(self.span_links))

        object.__setattr__(self, "schema_version", _required_text(self.schema_version, "schema_version"))
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"schema_version inválido: esperado {self.SCHEMA_VERSION!r}."
            )

        missing = list(_string_tuple(self.missing_fields, "missing_fields"))
        flags = list(_string_tuple(self.data_quality_flags, "data_quality_flags"))
        modality_missing = self._validate_modality()
        for name in modality_missing:
            if name not in missing:
                missing.append(name)
        if modality_missing and "missing_modality_fields" not in flags:
            flags.append("missing_modality_fields")
        for name, pattern in (
            ("trace_id", _OTLP_TRACE_ID),
            ("span_id", _OTLP_SPAN_ID),
            ("parent_span_id", _OTLP_SPAN_ID),
        ):
            value = getattr(self, name)
            if value and not pattern.fullmatch(value):
                flag = f"legacy_{name}_format"
                if flag not in flags:
                    flags.append(flag)
        object.__setattr__(self, "missing_fields", tuple(missing))
        object.__setattr__(self, "data_quality_flags", tuple(flags))

    def _validate_modality(self) -> tuple[str, ...]:
        if self.signal_type is SignalType.TRACE:
            required = ("trace_id", "span_id")
        elif self.signal_type is SignalType.METRIC:
            required = ("metric_name", "metric_value")
        elif self.signal_type is SignalType.BUSINESS_EVENT:
            if not (
                self.business_operation
                or (self.business_entity_type and self.business_entity_id)
            ):
                return ("business_operation_or_entity",)
            required = ()
        else:
            required = ()
        return tuple(name for name in required if getattr(self, name) is None)

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _json_value(getattr(self, item.name))
            for item in fields(self)
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )

    @property
    def contract_fingerprint(self) -> str:
        """SHA-256 do contrato canônico completo, normalizado e validado."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UnifiedEvent":
        if not isinstance(payload, Mapping):
            raise ValueError("payload deve ser um mapping.")
        allowed = {item.name for item in fields(cls)}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Campos desconhecidos: {sorted(unknown)}.")
        try:
            return cls(**dict(payload))
        except TypeError as exception:
            raise ValueError(f"Payload UnifiedEvent inválido: {exception}.") from exception

    @classmethod
    def from_json(cls, payload: str) -> "UnifiedEvent":
        try:
            decoded = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exception:
            raise ValueError("JSON UnifiedEvent inválido.") from exception
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class DerivedEvidence:
    """Relação inferida mantida fora do evento observado."""

    SCHEMA_VERSION: ClassVar[str] = "derived-evidence-v1"

    evidence_id: str
    relation_type: str
    source_event_ids: tuple[str, ...]
    target_event_id: str
    inferred_at: datetime | str
    source_component: str
    confidence: float | int | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_id",
            _required_identifier(self.evidence_id, "evidence_id"),
        )
        object.__setattr__(self, "relation_type", _required_text(self.relation_type, "relation_type"))
        sources = _string_tuple(self.source_event_ids, "source_event_ids")
        if not sources:
            raise ValueError("source_event_ids não pode ser vazio.")
        if len(sources) != len(self.source_event_ids):
            raise ValueError("source_event_ids deve conter itens únicos.")
        for value in sources:
            _identifier(value, "source_event_ids")
        object.__setattr__(self, "source_event_ids", sources)
        object.__setattr__(
            self, "target_event_id",
            _required_identifier(self.target_event_id, "target_event_id"),
        )
        object.__setattr__(self, "inferred_at", _utc_datetime(self.inferred_at, "inferred_at"))
        object.__setattr__(self, "source_component", _required_text(self.source_component, "source_component"))
        confidence = _finite_number(self.confidence, "confidence")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence deve estar entre 0 e 1.")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.attributes, Mapping):
            raise ValueError("attributes deve ser um mapping.")
        _json_value(self.attributes)
        object.__setattr__(self, "attributes", _freeze(self.attributes))
        object.__setattr__(self, "schema_version", _required_text(self.schema_version, "schema_version"))
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("schema_version inválido para DerivedEvidence.")

    def to_dict(self) -> dict[str, Any]:
        return {
            item.name: _json_value(getattr(self, item.name))
            for item in fields(self)
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DerivedEvidence":
        if not isinstance(payload, Mapping):
            raise ValueError("payload deve ser um mapping.")
        allowed = {item.name for item in fields(cls)}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Campos desconhecidos: {sorted(unknown)}.")
        try:
            return cls(**dict(payload))
        except TypeError as exception:
            raise ValueError(f"Payload DerivedEvidence inválido: {exception}.") from exception
