from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, TypeVar

from multimodal_v1.canonical import canonical_json_value, deep_freeze_json

KNOWN_MODALITIES = frozenset({"trace", "log", "metric", "business_event"})
CELL_REJECTION_PREFIXES = ("invalid_metric_cell:",)
CELL_REJECTION_REASONS = frozenset({"invalid_metric_value"})
ROW_REJECTION_REASONS_ALSO_CELL_LEVEL = frozenset({"invalid_metric_value"})
WINDOW_POLICIES_ALLOWING_EXCLUSION = frozenset({"adapter-window-filter-v1"})
INCIDENT_MANIFEST_SCHEMA_VERSION = "incident-manifest-v2"
SCHEMA_FILE_KINDS = frozenset({"faults", "metrics", "business_metrics",
                               "platform_metrics", "logs", "traces"})
METRIC_SCHEMA_KINDS = frozenset({"metrics", "business_metrics", "platform_metrics"})
NON_METRIC_SCHEMA_KINDS = frozenset({"faults", "logs", "traces"})
SCHEMA_REPORT_SCOPES = frozenset({"dataset_schema_report", "incident_normalization_report"})
_T = TypeVar("_T")


def cell_rejection_count(reasons: Mapping[str, int]) -> int:
    return sum(count for reason, count in reasons.items()
               if reason in CELL_REJECTION_REASONS
               or reason.startswith(CELL_REJECTION_PREFIXES))


def row_rejection_count(reasons: Mapping[str, int]) -> int:
    return sum(count for reason, count in reasons.items()
               if (reason in ROW_REJECTION_REASONS_ALSO_CELL_LEVEL
                   or (reason not in CELL_REJECTION_REASONS
                       and not reason.startswith(CELL_REJECTION_PREFIXES))))


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} deve ser texto não vazio.")
    return value.strip()


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} deve ser booleano.")
    return value


def _require_fields(payload: Mapping[str, Any], required_fields: Iterable[str]) -> None:
    missing = tuple(sorted(set(required_fields) - set(payload)))
    if missing:
        raise ValueError(f"Campos obrigatórios ausentes no IncidentManifest: {', '.join(missing)}.")


def canonical_relative_path(value: str, field_name: str = "path") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} deve ser texto não vazio.")
    normalized = value.replace("\\", "/")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(f"{field_name} contém path traversal ou segmento inválido.")
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"{field_name} contém path traversal ou não é relativo.")
    return str(parsed)


def canonical_unique_paths(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} deve ser coleção, não texto.")
    canonical = tuple(canonical_relative_path(value, f"{field_name} path")
                      for value in tuple(values))
    if len(canonical) != len(set(canonical)):
        raise ValueError(f"{field_name} contém paths duplicados após canonicalização.")
    return tuple(sorted(canonical))


def canonical_path_mapping(mapping: Mapping[str, _T], field_name: str,
                           value_converter: Callable[[_T], Any] | None = None) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        raise TypeError(f"{field_name} deve ser mapping.")
    result: dict[str, Any] = {}
    for raw_path, raw_value in tuple(mapping.items()):
        path = canonical_relative_path(raw_path, f"{field_name} path")
        if path in result:
            raise ValueError(f"{field_name} contém paths duplicados após canonicalização.")
        result[path] = value_converter(raw_value) if value_converter else raw_value
    return dict(sorted(result.items()))


_relative_path = canonical_relative_path


def _text_collection(value: Iterable[str], name: str, *, canonical_order: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} deve ser coleção, não texto.")
    items = tuple(_nonempty_text(item, f"{name} item") for item in tuple(value))
    if len(items) != len(set(items)):
        raise ValueError(f"{name} não aceita duplicatas.")
    return tuple(sorted(items)) if canonical_order else items


def _canonical_text_collection(value: Iterable[str], name: str) -> tuple[str, ...]:
    return _text_collection(value, name, canonical_order=True)


@dataclass(frozen=True)
class NormalizedFileAccounting:
    relative_path: str
    kind: str
    signal_type: str | None
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    partially_accepted_rows: int
    excluded_rows: int
    produced_events: int
    accepted_metric_cells: int
    rejected_metric_cells: int
    rejected_row_units: int = 0
    rejected_cell_units: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path, "relative_path"))
        # ``business_events`` is an additive source kind used by the controlled
        # causalods-local-v3 generator.  It maps to the already frozen
        # ``business_event`` modality and does not change the manifest wire
        # format or the semantics of any historical source kind.
        allowed_kinds = {
            "metrics", "business_metrics", "platform_metrics", "logs", "traces",
            "business_events",
        }
        if self.kind not in allowed_kinds:
            raise ValueError("kind de accounting inválido.")
        if self.signal_type not in KNOWN_MODALITIES | {None}:
            raise ValueError("signal_type de accounting inválido.")
        for name in ("total_rows", "accepted_rows", "rejected_rows",
                     "partially_accepted_rows", "excluded_rows", "produced_events",
                     "accepted_metric_cells", "rejected_metric_cells",
                     "rejected_row_units", "rejected_cell_units"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if self.accepted_rows + self.rejected_rows + self.excluded_rows != self.total_rows:
            raise ValueError("Accounting de linhas não reconcilia total_rows.")
        if self.partially_accepted_rows > self.accepted_rows:
            raise ValueError("partially_accepted_rows excede accepted_rows.")
        metric = self.kind in {"metrics", "business_metrics", "platform_metrics"}
        expected_signal = {
            "logs": "log",
            "traces": "trace",
            "business_events": "business_event",
        }.get(self.kind, "metric")
        if self.signal_type != expected_signal:
            raise ValueError("kind e signal_type são incompatíveis.")
        if self.partially_accepted_rows and (self.signal_type != "metric"
                                             or not self.accepted_metric_cells
                                             or not self.rejected_metric_cells):
            raise ValueError("Linha parcial requer sinal métrico e células aceitas/rejeitadas.")
        if self.rejected_metric_cells and not (
                self.partially_accepted_rows or self.rejected_rows):
            raise ValueError("Células rejeitadas requerem linha parcial ou rejeitada.")
        if metric:
            if self.produced_events != self.accepted_metric_cells:
                raise ValueError("Eventos métricos divergem das células aceitas.")
        else:
            if any((self.accepted_metric_cells, self.rejected_metric_cells,
                    self.partially_accepted_rows)):
                raise ValueError("Arquivo não métrico não aceita accounting de células/parciais.")
            if self.produced_events != self.accepted_rows:
                raise ValueError(
                    "Eventos não métricos devem igualar linhas aceitas."
                )
        if self.produced_events < self.accepted_rows:
            raise ValueError("produced_events não pode ser menor que accepted_rows.")
        if self.rejected_row_units != self.rejected_rows:
            raise ValueError("rejected_row_units diverge de rejected_rows.")
        if self.rejected_cell_units != self.rejected_metric_cells:
            raise ValueError("rejected_cell_units diverge de rejected_metric_cells.")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "relative_path", "kind", "signal_type", "total_rows", "accepted_rows",
            "rejected_rows", "partially_accepted_rows", "excluded_rows",
            "produced_events", "accepted_metric_cells", "rejected_metric_cells",
            "rejected_row_units", "rejected_cell_units")}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedFileAccounting":
        if not isinstance(value, Mapping):
            raise TypeError("Accounting serializado deve ser mapping.")
        return cls(**dict(value))


def canonical_json(value: Any) -> str:
    return json.dumps(canonical_json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} deve ser SHA-256 hexadecimal.")
    return value


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp deve possuir timezone.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AnalysisWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise TypeError("AnalysisWindow.start e end devem ser datetime.")
        if self.start.tzinfo is None or self.start.utcoffset() is None or self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("AnalysisWindow requer timestamps timezone-aware.")
        if self.end < self.start:
            raise ValueError("Fim da janela não pode preceder o início.")
        object.__setattr__(self, "start", self.start.astimezone(timezone.utc))
        object.__setattr__(self, "end", self.end.astimezone(timezone.utc))

    def to_dict(self) -> dict[str, str]:
        return {"start": utc_iso(self.start), "end": utc_iso(self.end)}


@dataclass(frozen=True)
class IncidentDescriptor:
    incident_id: str
    source_dataset: str
    system: str
    case: str
    path: str
    fault_type: str | None = None
    fault_service: str | None = None

    def __post_init__(self) -> None:
        for name in ("incident_id", "source_dataset", "system", "case", "path"):
            object.__setattr__(self, name, _nonempty_text(getattr(self, name), name))
        for name in ("fault_type", "fault_service"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonempty_text(value, name))


@dataclass(frozen=True)
class SchemaClassification:
    identity_fields: tuple[str, ...] = ()
    interpreted_metric_fields: tuple[str, ...] = ()
    preserved_metadata_fields: tuple[str, ...] = ()
    unmapped_fields: tuple[str, ...] = ()
    rejected_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        names = ("identity_fields", "interpreted_metric_fields",
                 "preserved_metadata_fields", "unmapped_fields", "rejected_fields")
        for name in names:
            object.__setattr__(self, name, _canonical_text_collection(getattr(self, name), name))
        for index, left_name in enumerate(names):
            for right_name in names[index + 1:]:
                overlap = set(getattr(self, left_name)) & set(getattr(self, right_name))
                if overlap:
                    field_name = sorted(overlap)[0]
                    raise ValueError(
                        f"Campo {field_name!r} aparece em {left_name} e {right_name}.")

    def to_dict(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in (
            "identity_fields", "interpreted_metric_fields", "preserved_metadata_fields",
            "unmapped_fields", "rejected_fields")}


@dataclass(frozen=True)
class SchemaFileReport:
    relative_path: str
    kind: str
    columns: tuple[str, ...]
    row_count: int
    checksum_sha256: str
    unmapped_fields: tuple[str, ...] = ()
    accepted_row_count: int = 0
    rejected_row_count: int = 0
    event_count: int = 0
    rejection_reasons: Mapping[str, int] = field(default_factory=dict)
    classification: SchemaClassification = field(default_factory=SchemaClassification)
    missing_normalized_fields: tuple[str, ...] = ()
    excluded_by_window_count: int = 0
    quality_issue_reasons: Mapping[str, int] = field(default_factory=dict)
    wide_row_policy: str | None = None
    rows_fully_accepted: int = 0
    rows_partially_accepted: int = 0
    rows_fully_rejected: int = 0
    accepted_metric_cell_count: int = 0
    rejected_metric_cell_count: int = 0
    layout_policy: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", canonical_relative_path(self.relative_path, "relative_path"))
        object.__setattr__(self, "kind", _nonempty_text(self.kind, "kind"))
        if self.kind not in SCHEMA_FILE_KINDS:
            raise ValueError("kind de SchemaFileReport inválido.")
        object.__setattr__(self, "columns", _text_collection(self.columns, "columns", canonical_order=False))
        object.__setattr__(self, "unmapped_fields", _canonical_text_collection(self.unmapped_fields, "unmapped_fields"))
        object.__setattr__(self, "missing_normalized_fields", _canonical_text_collection(
            self.missing_normalized_fields, "missing_normalized_fields"))
        if not isinstance(self.classification, SchemaClassification):
            raise TypeError("classification deve ser SchemaClassification.")
        for name in ("row_count", "accepted_row_count", "rejected_row_count", "event_count",
                     "excluded_by_window_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if self.accepted_row_count + self.rejected_row_count + self.excluded_by_window_count != self.row_count:
            raise ValueError("accepted + rejected + excluded deve igualar row_count.")
        _sha(self.checksum_sha256, "checksum_sha256")
        for name in ("rows_fully_accepted", "rows_partially_accepted",
                     "rows_fully_rejected", "accepted_metric_cell_count",
                     "rejected_metric_cell_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        for mapping_name in ("rejection_reasons", "quality_issue_reasons"):
            mapping = getattr(self, mapping_name)
            if any(not isinstance(key, str) or not key.strip() or isinstance(value, bool)
                   or not isinstance(value, int) or value < 0
                   for key, value in mapping.items()):
                raise ValueError(f"{mapping_name} inválido.")
        if row_rejection_count(self.rejection_reasons) != self.rejected_row_count:
            raise ValueError("Razões row-level não reconciliam rejected_row_count.")
        allowed_layouts = {"non-metric-v1", "long-metric-row-v1",
                           "partial-wide-row-v1", "metric-json-point-v1"}
        layout = self.layout_policy or (
            "partial-wide-row-v1" if self.wide_row_policy == "partial-wide-row-v1"
            else "non-metric-v1")
        if layout not in allowed_layouts:
            raise ValueError("layout_policy inválida.")
        if self.wide_row_policy not in {None, "partial-wide-row-v1"}:
            raise ValueError("wide_row_policy inválida.")
        if (layout == "partial-wide-row-v1") != (self.wide_row_policy == "partial-wide-row-v1"):
            raise ValueError("layout_policy e wide_row_policy divergem.")
        object.__setattr__(self, "layout_policy", layout)
        if self.kind in NON_METRIC_SCHEMA_KINDS:
            if layout != "non-metric-v1" or self.wide_row_policy is not None:
                raise ValueError("kind não métrico requer layout non-metric-v1.")
            if self.accepted_metric_cell_count or self.rejected_metric_cell_count:
                raise ValueError("kind não métrico não aceita células métricas.")
            if self.kind == "faults" and self.event_count:
                raise ValueError("faults requer event_count=0.")
        elif layout not in {"long-metric-row-v1", "partial-wide-row-v1", "metric-json-point-v1"}:
            raise ValueError("kind métrico requer layout métrico.")
        if self.kind in {"logs", "traces"} and self.event_count != self.accepted_row_count:
            raise ValueError("event_count de logs/traces deve igualar accepted_row_count.")
        if layout in {"long-metric-row-v1", "metric-json-point-v1"} \
                and self.event_count != self.accepted_row_count:
            raise ValueError("Layout métrico long/json requer relação 1:1 linha-evento.")
        if layout == "partial-wide-row-v1" and self.event_count < self.accepted_row_count:
            raise ValueError("Layout wide requer event_count >= accepted_row_count.")
        if layout == "partial-wide-row-v1":
            if (self.rows_fully_accepted + self.rows_partially_accepted
                    + self.rows_fully_rejected + self.excluded_by_window_count != self.row_count):
                raise ValueError("Accounting wide não reconcilia row_count.")
            if self.rows_fully_accepted + self.rows_partially_accepted != self.accepted_row_count:
                raise ValueError("Accounting wide não reconcilia accepted_row_count.")
            if self.rows_fully_rejected != self.rejected_row_count:
                raise ValueError("Accounting wide não reconcilia rejected_row_count.")
            if self.accepted_metric_cell_count != self.event_count:
                raise ValueError("Células métricas aceitas divergem de event_count.")
            if self.rejected_metric_cell_count != cell_rejection_count(self.rejection_reasons):
                raise ValueError("Células métricas rejeitadas divergem das razões cell-level.")
            if self.rows_partially_accepted and (not self.accepted_metric_cell_count
                                                 or not self.rejected_metric_cell_count):
                raise ValueError("Linha parcialmente aceita requer células aceitas e rejeitadas.")
            if self.rejected_metric_cell_count and not (
                    self.rows_partially_accepted or self.rows_fully_rejected):
                raise ValueError("Células rejeitadas requerem linha parcial ou totalmente rejeitada.")
        else:
            if any((self.rows_fully_accepted, self.rows_partially_accepted,
                    self.rows_fully_rejected)):
                raise ValueError("Counters wide não são permitidos em layout não wide.")
            if layout == "non-metric-v1" and any(
                    (self.accepted_metric_cell_count, self.rejected_metric_cell_count)):
                raise ValueError("Layout não métrico não aceita células métricas.")
            if layout in {"long-metric-row-v1", "metric-json-point-v1"}:
                if self.accepted_metric_cell_count != self.event_count:
                    raise ValueError("Células métricas aceitas divergem de event_count.")
                if self.rejected_metric_cell_count != cell_rejection_count(self.rejection_reasons):
                    raise ValueError("Células métricas rejeitadas divergem das razões cell-level.")
        if self.classification.unmapped_fields != self.unmapped_fields:
            raise ValueError("classification.unmapped_fields diverge de unmapped_fields.")
        classified = set().union(
            self.classification.identity_fields,
            self.classification.interpreted_metric_fields,
            self.classification.preserved_metadata_fields,
            self.classification.unmapped_fields,
            self.classification.rejected_fields)
        column_set = set(self.columns)
        if classified != column_set:
            missing = sorted(column_set - classified)
            unknown = sorted(classified - column_set)
            raise ValueError(
                f"Classificação deve cobrir columns exatamente; não classificados={missing}, ausentes={unknown}.")
        object.__setattr__(self, "rejection_reasons", deep_freeze_json(self.rejection_reasons))
        object.__setattr__(self, "quality_issue_reasons", deep_freeze_json(self.quality_issue_reasons))

    def to_dict(self) -> dict[str, Any]:
        return ({"relative_path": self.relative_path, "kind": self.kind,
                "columns": list(self.columns), "row_count": self.row_count,
                "accepted_row_count": self.accepted_row_count,
                "rejected_row_count": self.rejected_row_count,
                "event_count": self.event_count, "checksum_sha256": self.checksum_sha256,
                "unmapped_fields": list(self.unmapped_fields),
                "rejection_reasons": canonical_json_value(self.rejection_reasons),
                "classification": self.classification.to_dict(),
                "missing_normalized_fields": list(self.missing_normalized_fields),
                "quality_issue_reasons": canonical_json_value(self.quality_issue_reasons),
                "wide_row_policy": self.wide_row_policy,
                "rows_fully_accepted": self.rows_fully_accepted,
                "rows_partially_accepted": self.rows_partially_accepted,
                "rows_fully_rejected": self.rows_fully_rejected,
                "accepted_metric_cell_count": self.accepted_metric_cell_count,
                "rejected_metric_cell_count": self.rejected_metric_cell_count,
                "layout_policy": self.layout_policy}
                | {"excluded_by_window_count": self.excluded_by_window_count})


@dataclass(frozen=True)
class SchemaReport:
    dataset: str
    files: tuple[SchemaFileReport, ...]
    generated_at: datetime
    report_scope: str = "dataset_schema_report"

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset", _nonempty_text(self.dataset, "dataset"))
        object.__setattr__(self, "report_scope", _nonempty_text(self.report_scope, "report_scope"))
        if self.report_scope not in SCHEMA_REPORT_SCOPES:
            raise ValueError("report_scope de SchemaReport inválido.")
        if not isinstance(self.generated_at, datetime) or self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at deve possuir timezone.")
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(timezone.utc))
        if isinstance(self.files, (str, bytes)):
            raise TypeError("files deve ser coleção, não texto.")
        files = tuple(self.files)
        if any(not isinstance(item, SchemaFileReport) for item in files):
            raise TypeError("files deve conter apenas SchemaFileReport.")
        paths = tuple(item.relative_path for item in files)
        if len(paths) != len(set(paths)):
            raise ValueError("SchemaReport contém relative_path duplicado.")
        object.__setattr__(self, "files", tuple(sorted(files, key=lambda item: item.relative_path)))

    @property
    def fingerprint(self) -> str:
        # generated_at is operational metadata, not schema identity.
        return sha256_value({"dataset": self.dataset, "report_scope": self.report_scope,
                             "files": [item.to_dict() for item in self.files]})

    def to_dict(self) -> dict[str, Any]:
        return {"dataset": self.dataset, "report_scope": self.report_scope,
                "generated_at": utc_iso(self.generated_at),
                "files": [item.to_dict() for item in self.files], "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_report: SchemaReport | None = None

    def __post_init__(self) -> None:
        _require_bool(self.valid, "valid")
        for name in ("errors", "warnings"):
            value = getattr(self, name)
            if isinstance(value, (str, bytes)):
                raise TypeError(f"{name} deve ser coleção, não texto.")
            items = tuple(_nonempty_text(item, f"{name} item") for item in tuple(value))
            object.__setattr__(self, name, items)
        if self.valid != (len(self.errors) == 0):
            raise ValueError("ValidationReport.valid deve equivaler à ausência de errors.")
        if self.schema_report is not None and not isinstance(self.schema_report, SchemaReport):
            raise TypeError("schema_report deve ser SchemaReport ou None.")


@dataclass(frozen=True)
class IncidentManifest:
    incident_id: str
    source_dataset: str
    source_system: str
    source_case: str
    fault_type: str | None
    fault_service: str | None
    analysis_window: AnalysisWindow
    modalities: tuple[str, ...]
    source_files_sha256: Mapping[str, str]
    event_count_by_modality: Mapping[str, int]
    unmapped_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    adapter_version: str = "unknown"
    adapter_configuration_sha256: str = "0" * 64
    cache_key: str = "0" * 64
    normalized_events_sha256: str = "0" * 64
    ground_truth_sha256: str = "0" * 64
    schema_report_sha256: str = "0" * 64
    window_policy: str = "unknown"
    observed_interval: AnalysisWindow | None = None
    outside_window_event_count: int = 0
    window_decision: str = "unknown"
    accepted_row_count: int = 0
    rejected_row_count: int = 0
    total_event_count: int = 0
    considered_row_count: int = 0
    excluded_by_window_count: int = 0
    rejection_reasons_by_file: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    dataset_schema_report_sha256: str = "0" * 64
    incident_normalization_report_sha256: str = "0" * 64
    schema_version: str = INCIDENT_MANIFEST_SCHEMA_VERSION
    normalization_mode: str = "strict"
    empty_incident_reason: str | None = None
    usable_for_benchmark: bool = True
    quality_issue_reasons_by_file: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    modality_policy_version: str = "unspecified"
    expected_modalities: tuple[str, ...] = ()
    observed_modalities: tuple[str, ...] = ()
    missing_expected_modalities: tuple[str, ...] = ()
    unexpected_modalities: tuple[str, ...] = ()
    normalization_accounting_by_file: Mapping[str, NormalizedFileAccounting | Mapping[str, Any]] = field(default_factory=dict)
    normalized_source_files: tuple[str, ...] = ()
    modality_policy_status: str = "detected"

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_window, AnalysisWindow):
            raise TypeError("analysis_window deve ser AnalysisWindow.")
        if self.observed_interval is not None and not isinstance(self.observed_interval, AnalysisWindow):
            raise TypeError("observed_interval deve ser AnalysisWindow ou None.")
        _require_bool(self.usable_for_benchmark, "usable_for_benchmark")
        for name in ("incident_id", "source_dataset", "source_system", "source_case",
                     "adapter_version", "window_policy", "window_decision",
                     "modality_policy_version", "schema_version"):
            object.__setattr__(self, name, _nonempty_text(getattr(self, name), name))
        if self.schema_version != INCIDENT_MANIFEST_SCHEMA_VERSION:
            raise ValueError("schema_version do IncidentManifest inválida.")
        for name in ("fault_type", "fault_service", "empty_incident_reason"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonempty_text(value, name))
        for value, name in ((self.adapter_configuration_sha256, "adapter_configuration_sha256"),
                            (self.cache_key, "cache_key"),
                            (self.normalized_events_sha256, "normalized_events_sha256"),
                            (self.ground_truth_sha256, "ground_truth_sha256"),
                            (self.schema_report_sha256, "schema_report_sha256"),
                            (self.dataset_schema_report_sha256, "dataset_schema_report_sha256"),
                            (self.incident_normalization_report_sha256,
                             "incident_normalization_report_sha256")):
            _sha(value, name)
        if isinstance(self.modalities, (str, bytes)):
            raise TypeError("modalities deve ser coleção, não texto.")
        raw_modalities = tuple(self.modalities)
        if len(raw_modalities) != len(set(raw_modalities)):
            raise ValueError("Modalidades devem ser únicas.")
        modalities = tuple(sorted(raw_modalities))
        object.__setattr__(self, "modalities", modalities)
        for name in ("expected_modalities", "observed_modalities",
                     "missing_expected_modalities", "unexpected_modalities"):
            value = getattr(self, name)
            if isinstance(value, (str, bytes)):
                raise TypeError(f"{name} deve ser coleção.")
            raw = tuple(value)
            if len(raw) != len(set(raw)):
                raise ValueError(f"{name} não aceita duplicatas.")
            canonical = tuple(sorted(raw))
            if any(item not in KNOWN_MODALITIES for item in canonical):
                raise ValueError(f"{name} contém modalidade desconhecida.")
            object.__setattr__(self, name, canonical)
        if any(modality not in KNOWN_MODALITIES for modality in modalities):
            raise ValueError("Manifest contém modalidade desconhecida.")
        counts = dict(self.event_count_by_modality)
        if any(key not in KNOWN_MODALITIES or isinstance(value, bool) or not isinstance(value, int)
               or value < 0 for key, value in counts.items()):
            raise ValueError("Contagens por modalidade inválidas.")
        if sum(counts.values()) != self.total_event_count:
            raise ValueError("Soma das modalidades diverge de total_event_count.")
        if set(modalities) != {key for key, value in counts.items() if value > 0}:
            raise ValueError("Modalidades divergem das contagens positivas.")
        if not isinstance(self.modality_policy_version, str) or not self.modality_policy_version.strip():
            raise ValueError("modality_policy_version deve ser texto não vazio.")
        if set(self.observed_modalities) != set(modalities):
            raise ValueError("observed_modalities diverge das modalidades produzidas.")
        if set(self.missing_expected_modalities) != set(self.expected_modalities) - set(modalities):
            raise ValueError("missing_expected_modalities inconsistente.")
        if set(self.unexpected_modalities) != set(modalities) - set(self.expected_modalities):
            raise ValueError("unexpected_modalities inconsistente.")
        if self.normalization_mode not in {"strict", "permissive"}:
            raise ValueError("normalization_mode inválido.")
        if self.modality_policy_status not in {"official", "detected", "nonofficial"}:
            raise ValueError("modality_policy_status inválido.")
        if self.normalization_mode == "strict" and self.modality_policy_status == "nonofficial":
            raise ValueError("Manifesto strict não aceita política não oficial.")
        if self.modality_policy_status == "nonofficial" and self.usable_for_benchmark:
            raise ValueError("Política não oficial não pode ser utilizável para benchmark.")
        if self.normalization_mode == "strict" and self.total_event_count <= 0:
            raise ValueError("Manifesto strict requer eventos.")
        if self.total_event_count == 0 and (not self.empty_incident_reason or self.usable_for_benchmark):
            raise ValueError("Manifesto vazio requer motivo e usable_for_benchmark=false.")
        if self.schema_report_sha256 != self.incident_normalization_report_sha256:
            raise ValueError("schema_report_sha256 deve ser alias do relatório do incidente.")
        for name in ("accepted_row_count", "rejected_row_count", "total_event_count",
                     "outside_window_event_count", "considered_row_count",
                     "excluded_by_window_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if (self.accepted_row_count + self.rejected_row_count
                + self.excluded_by_window_count != self.considered_row_count):
            raise ValueError("Contagens de linhas do incidente são inconsistentes.")
        if (self.normalization_mode == "strict" and self.excluded_by_window_count
                and self.window_policy not in WINDOW_POLICIES_ALLOWING_EXCLUSION):
            raise ValueError("Política de janela strict não permite linhas excluídas.")
        sources = canonical_path_mapping(self.source_files_sha256, "source_files_sha256")
        if not sources:
            raise ValueError("source_files_sha256 requer paths não vazios.")
        for value in sources.values(): _sha(value, "source_files_sha256")
        object.__setattr__(self, "source_files_sha256", deep_freeze_json(dict(sorted(sources.items()))))
        object.__setattr__(self, "event_count_by_modality", deep_freeze_json(dict(sorted(counts.items()))))
        unmapped = canonical_path_mapping(
            self.unmapped_fields, "unmapped_fields",
            lambda value: _canonical_text_collection(value, "unmapped_fields value"))
        object.__setattr__(self, "unmapped_fields", deep_freeze_json(unmapped))
        reasons = canonical_path_mapping(
            self.rejection_reasons_by_file, "rejection_reasons_by_file", dict)
        if not set(reasons).issubset(sources):
            raise ValueError("Rejection reasons referem-se a arquivo ausente.")
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0
               for values in reasons.values() for count in values.values()):
            raise ValueError("Contagens de rejection reasons inválidas.")
        object.__setattr__(self, "rejection_reasons_by_file", deep_freeze_json(reasons))
        quality = canonical_path_mapping(
            self.quality_issue_reasons_by_file, "quality_issue_reasons_by_file", dict)
        if not set(quality).issubset(sources):
            raise ValueError("Quality issues referem-se a arquivo ausente.")
        for name, mapping in (("rejection", reasons), ("quality", quality)):
            if any(not isinstance(reason, str) or not reason.strip() or isinstance(count, bool)
                   or not isinstance(count, int) or count < 0
                   for values in mapping.values() for reason, count in values.items()):
                raise ValueError(f"Mapa de razões {name} inválido.")
        if self.normalization_mode == "strict" and any(reasons.values()):
            raise ValueError("Manifesto strict não pode conter rejeições.")
        if self.normalization_mode == "strict" and self.rejected_row_count:
            raise ValueError("Manifesto strict requer rejected_row_count=0.")
        if self.normalization_mode == "strict" and self.missing_expected_modalities:
            raise ValueError("Manifesto strict não pode omitir modalidade esperada.")
        if self.normalization_mode == "strict" and not self.usable_for_benchmark:
            raise ValueError("Manifesto strict deve ser utilizável para benchmark.")
        if self.normalization_mode == "strict" and self.empty_incident_reason is not None:
            raise ValueError("Manifesto strict não aceita empty_incident_reason.")
        normalized_files = canonical_unique_paths(
            self.normalized_source_files, "normalized_source_files")
        if not set(normalized_files).issubset(sources):
            raise ValueError("Arquivo normalizado ausente de source_files_sha256.")
        object.__setattr__(self, "normalized_source_files", normalized_files)
        def convert_accounting(value):
            return value if isinstance(value, NormalizedFileAccounting) else \
                NormalizedFileAccounting.from_dict(value)

        accounting = canonical_path_mapping(
            self.normalization_accounting_by_file, "normalization_accounting_by_file",
            convert_accounting)
        for canonical_path, item in accounting.items():
            if item.relative_path != canonical_path:
                raise ValueError("Chave do accounting diverge de relative_path.")
        if set(accounting) != set(normalized_files):
            raise ValueError("Accounting deve cobrir exatamente normalized_source_files.")
        if not set(reasons).issubset(normalized_files):
            raise ValueError("Rejection reasons devem referir-se a arquivo normalizado.")
        if not set(quality).issubset(normalized_files):
            raise ValueError("Quality issues devem referir-se a arquivo normalizado.")
        if not set(unmapped).issubset(normalized_files):
            raise ValueError("Unmapped fields devem referir-se a arquivo normalizado.")
        empty_accounting_allowed = (self.total_event_count == 0 and self.accepted_row_count == 0
                                    and self.rejected_row_count == 0
                                    and self.considered_row_count == 0
                                    and self.normalization_mode == "permissive"
                                    and not self.usable_for_benchmark
                                    and isinstance(self.empty_incident_reason, str)
                                    and bool(self.empty_incident_reason.strip()))
        if not accounting and not empty_accounting_allowed:
            raise ValueError("Accounting vazio não é permitido para este manifesto.")
        if sum(item.total_rows for item in accounting.values()) != self.considered_row_count:
            raise ValueError("Accounting não reconcilia considered_row_count.")
        if sum(item.accepted_rows for item in accounting.values()) != self.accepted_row_count:
            raise ValueError("Accounting não reconcilia accepted_row_count.")
        if sum(item.rejected_rows for item in accounting.values()) != self.rejected_row_count:
            raise ValueError("Accounting não reconcilia rejected_row_count.")
        if sum(item.excluded_rows for item in accounting.values()) != self.excluded_by_window_count:
            raise ValueError("Accounting não reconcilia excluded_by_window_count.")
        if sum(item.produced_events for item in accounting.values()) != self.total_event_count:
            raise ValueError("Accounting não reconcilia total_event_count.")
        for modality in KNOWN_MODALITIES:
            produced = sum(item.produced_events for item in accounting.values()
                           if item.signal_type == modality)
            if produced != counts.get(modality, 0):
                raise ValueError(f"Accounting não reconcilia modalidade {modality}.")
        if sum(item.accepted_metric_cells for item in accounting.values()) != counts.get("metric", 0):
            raise ValueError("Accounting de células não reconcilia eventos métricos.")
        for path, item in accounting.items():
            file_reasons = reasons.get(path, {})
            if item.rejected_cell_units != cell_rejection_count(file_reasons):
                raise ValueError("Accounting de células diverge das razões por arquivo.")
            if item.rejected_row_units != row_rejection_count(file_reasons):
                raise ValueError("Accounting de linhas diverge das razões row-level.")
        rejected_cells = sum(item.rejected_metric_cells for item in accounting.values())
        partial_rows = sum(item.partially_accepted_rows for item in accounting.values())
        zero_event_file = any(item.produced_events == 0 for item in accounting.values())
        if self.normalization_mode == "strict" and rejected_cells:
            raise ValueError("Manifesto strict não aceita células rejeitadas.")
        if self.normalization_mode == "strict" and (not self.accepted_row_count
                or not self.expected_modalities or not normalized_files):
            raise ValueError("Manifesto strict requer linhas, modalidades e arquivos normalizados.")
        if self.normalization_mode == "strict" and (partial_rows or zero_event_file):
            raise ValueError("Manifesto strict não aceita linha parcial ou arquivo sem eventos.")
        if self.usable_for_benchmark and self.empty_incident_reason is not None:
            raise ValueError("Manifesto utilizável não aceita motivo de não utilização.")
        if self.normalization_mode == "permissive" and not self.usable_for_benchmark \
                and not self.empty_incident_reason:
            raise ValueError("Manifesto permissivo não utilizável requer motivo.")
        object.__setattr__(self, "quality_issue_reasons_by_file", deep_freeze_json(quality))
        object.__setattr__(self, "normalization_accounting_by_file",
                           deep_freeze_json({path: item.to_dict() for path, item in accounting.items()}))

    def _payload(self) -> dict[str, Any]:
        return ({"incident_id": self.incident_id, "source_dataset": self.source_dataset,
                "source_system": self.source_system, "source_case": self.source_case,
                "fault_type": self.fault_type, "fault_service": self.fault_service,
                "analysis_window": self.analysis_window.to_dict(),
                "modalities": list(self.modalities), "source_files_sha256": dict(self.source_files_sha256),
                "event_count_by_modality": dict(self.event_count_by_modality),
                "unmapped_fields": {key: list(value) for key, value in self.unmapped_fields.items()},
                "adapter_version": self.adapter_version,
                "adapter_configuration_sha256": self.adapter_configuration_sha256,
                "cache_key": self.cache_key, "normalized_events_sha256": self.normalized_events_sha256,
                "ground_truth_sha256": self.ground_truth_sha256,
                "schema_report_sha256": self.schema_report_sha256,
                "window_policy": self.window_policy,
                "observed_interval": self.observed_interval.to_dict() if self.observed_interval else None,
                "outside_window_event_count": self.outside_window_event_count,
                "window_decision": self.window_decision,
                "accepted_row_count": self.accepted_row_count,
                "rejected_row_count": self.rejected_row_count,
                "total_event_count": self.total_event_count,
                "considered_row_count": self.considered_row_count,
                "excluded_by_window_count": self.excluded_by_window_count,
                "rejection_reasons_by_file": canonical_json_value(self.rejection_reasons_by_file),
                "dataset_schema_report_sha256": self.dataset_schema_report_sha256,
                "incident_normalization_report_sha256": self.incident_normalization_report_sha256,
                "schema_version": self.schema_version,
                "normalization_mode": self.normalization_mode,
                "empty_incident_reason": self.empty_incident_reason,
                "usable_for_benchmark": self.usable_for_benchmark,
                "quality_issue_reasons_by_file": canonical_json_value(self.quality_issue_reasons_by_file)}
        | {"modality_policy_version": self.modality_policy_version,
           "expected_modalities": list(self.expected_modalities),
           "observed_modalities": list(self.observed_modalities),
           "missing_expected_modalities": list(self.missing_expected_modalities),
           "unexpected_modalities": list(self.unexpected_modalities),
           "normalized_source_files": list(self.normalized_source_files),
           "modality_policy_status": self.modality_policy_status,
           "normalization_accounting_by_file": canonical_json_value(
               self.normalization_accounting_by_file)})

    @property
    def fingerprint(self) -> str: return sha256_value(self._payload())

    def to_dict(self) -> dict[str, Any]: return self._payload() | {"fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IncidentManifest":
        if not isinstance(value, Mapping):
            raise TypeError("IncidentManifest serializado deve ser mapping.")
        payload = dict(value)
        provided_fingerprint = payload.pop("fingerprint", None)
        _require_fields(payload, (
            "incident_id", "source_dataset", "source_system", "source_case",
            "fault_type", "fault_service", "analysis_window", "modalities", "source_files_sha256", "event_count_by_modality",
            "adapter_version", "adapter_configuration_sha256", "cache_key",
            "normalized_events_sha256", "ground_truth_sha256", "schema_report_sha256",
            "window_policy", "window_decision", "dataset_schema_report_sha256",
            "incident_normalization_report_sha256", "schema_version", "normalization_mode",
            "usable_for_benchmark", "modality_policy_version", "modality_policy_status"))

        allowed = {item.name for item in fields(cls)}
        unknown = tuple(sorted(set(payload) - allowed))
        if unknown:
            raise ValueError(f"Campos desconhecidos no IncidentManifest: {', '.join(unknown)}.")

        def window(raw, name):
            if raw is None or isinstance(raw, AnalysisWindow):
                return raw
            if not isinstance(raw, Mapping):
                raise ValueError(f"{name} serializada deve ser mapping.")
            missing = tuple(sorted({"start", "end"} - set(raw)))
            if missing:
                raise ValueError(f"{name} sem campo obrigatório: {', '.join(missing)}.")

            def moment(text, field_name):
                if not isinstance(text, str):
                    raise ValueError(f"Timestamp {name}.{field_name} deve ser texto ISO-8601.")
                try:
                    return datetime.fromisoformat(
                        text[:-1] + "+00:00" if text.endswith("Z") else text)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"Timestamp {name}.{field_name} inválido.") from exc

            return AnalysisWindow(moment(raw["start"], "start"), moment(raw["end"], "end"))

        payload["analysis_window"] = window(payload["analysis_window"], "analysis_window")
        payload["observed_interval"] = window(payload.get("observed_interval"), "observed_interval")
        payload["normalization_accounting_by_file"] = {
            path: NormalizedFileAccounting.from_dict(item)
            for path, item in payload.get("normalization_accounting_by_file", {}).items()}
        manifest = cls(**payload)
        if provided_fingerprint is not None:
            _sha(provided_fingerprint, "fingerprint")
            if provided_fingerprint != manifest.fingerprint:
                raise ValueError("Fingerprint do IncidentManifest divergente.")
        return manifest


class EvaluationProtocol(str, Enum):
    WITHIN_DATASET = "within-dataset"
    CROSS_SYSTEM = "cross-system"
    CROSS_SOURCE = "cross-source"
    LEAVE_ONE_SYSTEM_OUT = "leave-one-system-out"


@dataclass(frozen=True)
class ProtocolSplit:
    protocol: EvaluationProtocol | str
    train_incident_ids: tuple[str, ...]
    test_incident_ids: tuple[str, ...]
    held_out: str | None = None
    seed: int | None = None
    test_fraction: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol", EvaluationProtocol(self.protocol))
        if isinstance(self.train_incident_ids, (str, bytes)) or isinstance(self.test_incident_ids, (str, bytes)):
            raise TypeError("IDs de treino/teste devem ser coleções, não texto.")
        train = tuple(sorted(_nonempty_text(item, "train_incident_ids item")
                             for item in tuple(self.train_incident_ids)))
        test = tuple(sorted(_nonempty_text(item, "test_incident_ids item")
                            for item in tuple(self.test_incident_ids)))
        object.__setattr__(self, "train_incident_ids", train)
        object.__setattr__(self, "test_incident_ids", test)
        if not train or not test:
            raise ValueError("Split requer IDs de treino/teste não vazios.")
        if len(train) != len(set(train)) or len(test) != len(set(test)):
            raise ValueError("Split contém IDs duplicados após normalização.")
        if set(train) & set(test): raise ValueError("Train e test devem ser disjuntos.")
        if self.protocol is EvaluationProtocol.WITHIN_DATASET:
            if self.held_out is not None:
                raise ValueError("within-dataset não aceita held_out.")
            if isinstance(self.seed, bool) or not isinstance(self.seed, int):
                raise ValueError("within-dataset requer seed inteira não booleana.")
            if not isinstance(self.test_fraction, (int, float)) or isinstance(self.test_fraction, bool) or not 0 < self.test_fraction < 1:
                raise ValueError("within-dataset requer test_fraction entre 0 e 1.")
        else:
            object.__setattr__(self, "held_out", _nonempty_text(self.held_out, "held_out"))
            if self.seed is not None or self.test_fraction is not None:
                raise ValueError("Protocolos de holdout não usam seed/test_fraction.")


@dataclass(frozen=True)
class ProtocolPlanManifest:
    protocol: EvaluationProtocol | str
    incident_ids: tuple[str, ...]
    systems: tuple[str, ...]
    sources: tuple[str, ...]
    folds: tuple[ProtocolSplit, ...]
    incident_index: Mapping[str, Mapping[str, str]]
    seed: int | None = None
    test_fraction: float | None = None
    version: str = "public-dataset-protocol-v2"

    def __post_init__(self) -> None:
        mode = EvaluationProtocol(self.protocol)
        object.__setattr__(self, "protocol", mode)
        for name in ("incident_ids", "systems", "sources"):
            value = getattr(self, name)
            values = _canonical_text_collection(value, name)
            object.__setattr__(self, name, values)
        if isinstance(self.folds, (str, bytes)):
            raise TypeError("folds deve ser coleção, não texto.")
        folds = tuple(self.folds)
        if any(not isinstance(fold, ProtocolSplit) for fold in folds):
            raise TypeError("folds deve conter somente ProtocolSplit.")
        object.__setattr__(self, "folds", tuple(sorted(
            folds, key=lambda fold: (fold.held_out or "", fold.test_incident_ids))))
        if self.version != "public-dataset-protocol-v2":
            raise ValueError("Versão do protocolo inválida.")
        if not self.incident_ids or len(self.incident_ids) != len(set(self.incident_ids)):
            raise ValueError("Manifesto requer incident IDs únicos.")
        if not self.systems or not self.sources or not self.folds:
            raise ValueError("Manifesto requer sistemas, fontes e folds.")
        if not isinstance(self.incident_index, Mapping):
            raise TypeError("incident_index deve ser Mapping.")
        index = {}
        for incident, metadata in self.incident_index.items():
            key = _nonempty_text(incident, "incident_index key")
            if key in index:
                raise ValueError("incident_index contém keys duplicadas após normalização.")
            if not isinstance(metadata, Mapping):
                raise TypeError("incident_index metadata deve ser Mapping.")
            index[key] = {name: _nonempty_text(value, f"incident_index {name}")
                          for name, value in metadata.items()}
        if set(index) != set(self.incident_ids) or any(
                set(metadata) != {"system", "source"}
                or any(not isinstance(value, str) or not value.strip() for value in metadata.values())
                for metadata in index.values()):
            raise ValueError("incident_index inválido ou incompleto.")
        if tuple(sorted({value["system"] for value in index.values()})) != self.systems:
            raise ValueError("systems diverge do incident_index.")
        if tuple(sorted({value["source"] for value in index.values()})) != self.sources:
            raise ValueError("sources diverge do incident_index.")
        object.__setattr__(self, "incident_index", deep_freeze_json(index))
        known = set(self.incident_ids)
        if any(fold.protocol is not mode for fold in self.folds):
            raise ValueError("Protocolo do fold diverge do manifesto.")
        if any((set(fold.train_incident_ids) | set(fold.test_incident_ids)) != known
               for fold in self.folds):
            raise ValueError("Fold não cobre exatamente os incidentes do manifesto.")
        expected_folds = len(self.systems) if mode is EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT else 1
        if len(self.folds) != expected_folds:
            raise ValueError("Quantidade de folds incompatível com o protocolo.")
        if mode is EvaluationProtocol.WITHIN_DATASET:
            if self.seed != self.folds[0].seed or self.test_fraction != self.folds[0].test_fraction:
                raise ValueError("Parâmetros do manifesto divergem do fold.")
        elif self.seed is not None or self.test_fraction is not None or any(
                fold.seed is not None or fold.test_fraction is not None for fold in self.folds):
            raise ValueError("Protocolos de holdout não usam seed/test_fraction.")
        for fold in self.folds:
            if mode is EvaluationProtocol.WITHIN_DATASET:
                if fold.held_out is not None: raise ValueError("within-dataset não aceita held_out.")
                continue
            field = "source" if mode is EvaluationProtocol.CROSS_SOURCE else "system"
            if any(index[item][field] != fold.held_out for item in fold.test_incident_ids):
                raise ValueError("held_out diverge dos incidentes de teste.")
            if any(index[item][field] == fold.held_out for item in fold.train_incident_ids):
                raise ValueError("Elemento holdout apareceu no treino.")
        if mode is EvaluationProtocol.LEAVE_ONE_SYSTEM_OUT:
            tested = [item for fold in self.folds for item in fold.test_incident_ids]
            if (sorted(tested) != sorted(self.incident_ids)
                    or {fold.held_out for fold in self.folds} != set(self.systems)):
                raise ValueError("Folds LOSO não cobrem cada incidente exatamente uma vez.")

    @property
    def fingerprint(self) -> str:
        parameters = ({"seed": self.seed, "test_fraction": self.test_fraction}
                      if self.protocol is EvaluationProtocol.WITHIN_DATASET else {})
        return sha256_value({"version": self.version, "protocol": self.protocol.value,
                             "parameters": parameters,
                             "incident_ids": self.incident_ids, "systems": self.systems,
                             "sources": self.sources, "incident_index": self.incident_index,
                             "folds": [{"protocol": fold.protocol.value,
                                        "train": fold.train_incident_ids, "test": fold.test_incident_ids,
                                        "held_out": fold.held_out} for fold in self.folds]})


@dataclass(frozen=True)
class ReplayResult:
    attempted: int
    confirmed: int
    failed: int
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("attempted", "confirmed", "failed"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"ReplayResult.{name} deve ser inteiro não negativo.")
        if isinstance(self.errors, (str, bytes)):
            raise TypeError("ReplayResult.errors deve ser coleção, não texto.")
        object.__setattr__(self, "errors", tuple(
            _nonempty_text(item, "ReplayResult.errors item") for item in tuple(self.errors)))
        if self.attempted != self.confirmed + self.failed:
            raise ValueError("attempted deve igualar confirmed + failed.")
        if self.failed != len(self.errors):
            raise ValueError("failed deve igualar len(errors).")
