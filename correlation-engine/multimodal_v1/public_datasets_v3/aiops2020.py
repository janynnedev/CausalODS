from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from multimodal_v1.contracts import UnifiedEvent
from .base import DatasetAdapter, NormalizedEventCache
from .common import (DURATION_POLICY_VERSION, TIMESTAMP_POLICY_VERSION,
                     duration_attributes, event_id, finite_float, first,
                     parse_timestamp, read_csv, source_path, valid_hex)
from .contracts import (AnalysisWindow, IncidentDescriptor, IncidentManifest,
                        NormalizedFileAccounting, SchemaClassification, SchemaFileReport, SchemaReport,
                        ValidationReport, sha256_file, sha256_value)


FAULT_ALIASES = {
    "time": ("timestamp", "time", "fault_time", "故障时间", "故障发生时间"),
    "location": ("fault_location", "location", "cmdb_id", "故障位置", "故障定位"),
    "type": ("fault_type", "type", "故障类型"),
}
METRIC_ALIASES = {
    "time": ("timestamp", "time"), "name": ("name", "metric_name"),
    "value": ("value", "metric_value"), "service": ("cmdb_id", "omdb_id", "serviceName"),
    "item": ("itemid",),
}
TRACE_ALIASES = {
    "time": ("timestamp", "startTime", "start_time"),
    "id": ("id",), "pid": ("pid",), "trace": ("traceid", "traceID"),
    "service": ("cmdb_id", "omdb_id", "serviceName"),
    "operation": ("operationName", "methodName", "name"),
    "duration": ("elapsedTime", "duration"), "status": ("success", "statusCode", "status_code"),
}
AIOPS2020_ADAPTER_VERSION = "aiops2020-adapter-v2"
AIOPS2020_WINDOW_POLICY = "adapter-window-filter-v1"
DOCUMENTED_BUSINESS_METRICS = frozenset({
    "avg_response_time", "average_response_time", "avg_time", "request_count",
    "success_rate", "success_count", "failure_rate", "non_success_rate",
})


@dataclass(frozen=True)
class AIOpsFileNormalizationResult:
    path: Path
    kind: str
    columns: tuple[str, ...]
    rows_total: int
    rows_fully_accepted: int
    rows_partially_accepted: int
    rows_fully_rejected: int
    rows_excluded_by_window: int
    accepted_metric_cell_count: int
    rejected_metric_cell_count: int
    events: tuple[UnifiedEvent, ...]
    rejection_reasons: Mapping[str, int]
    quality_issue_reasons: Mapping[str, int]
    classification: SchemaClassification
    observed_interval: AnalysisWindow | None
    missing_normalized_fields: tuple[str, ...] = ()
    is_wide_metric: bool = False

    @property
    def rows_accepted(self): return self.rows_fully_accepted + self.rows_partially_accepted

    @property
    def rows_rejected(self): return self.rows_fully_rejected

    def report(self, root: Path) -> SchemaFileReport:
        wide = self.is_wide_metric
        return SchemaFileReport(
            relative_path=source_path(self.path, root), kind=self.kind, columns=self.columns,
            row_count=self.rows_total, checksum_sha256=sha256_file(self.path),
            unmapped_fields=self.classification.unmapped_fields,
            accepted_row_count=self.rows_accepted, rejected_row_count=self.rows_rejected,
            event_count=len(self.events), rejection_reasons=self.rejection_reasons,
            quality_issue_reasons=self.quality_issue_reasons,
            classification=self.classification,
            missing_normalized_fields=self.missing_normalized_fields,
            excluded_by_window_count=self.rows_excluded_by_window,
            wide_row_policy="partial-wide-row-v1" if wide else None,
            rows_fully_accepted=self.rows_fully_accepted if wide else 0,
            rows_partially_accepted=self.rows_partially_accepted if wide else 0,
            rows_fully_rejected=self.rows_fully_rejected if wide else 0,
            accepted_metric_cell_count=self.accepted_metric_cell_count,
            rejected_metric_cell_count=self.rejected_metric_cell_count,
            layout_policy=("partial-wide-row-v1" if wide else
                           ("long-metric-row-v1" if self.kind in {
                               "business_metrics", "platform_metrics"} else "non-metric-v1")))


class AIOps2020Adapter(DatasetAdapter):
    SOURCE = "aiops-challenge-2020"

    def __init__(self, root: str | Path, *, fault_csv: str | Path | None = None,
                 cache_dir: str | Path | None = None,
                 pre_fault: timedelta = timedelta(minutes=10),
                 post_fault: timedelta = timedelta(minutes=10),
                 column_aliases: Mapping[str, Mapping[str, tuple[str, ...]]] | None = None,
                 expected_checksums: Mapping[str, str] | None = None,
                 wide_metric_fields: Mapping[str, tuple[str, ...]] | None = None,
                 preserved_metadata_fields: tuple[str, ...] = (),
                 duration_units: Mapping[str, str] | None = None,
                 require_metric_service: bool = True, strict: bool = True):
        self.root = Path(root).expanduser().resolve()
        self.fault_csv = Path(fault_csv).expanduser().resolve() if fault_csv else None
        self.cache = NormalizedEventCache(cache_dir)
        self.pre_fault, self.post_fault = pre_fault, post_fault
        custom = column_aliases or {}
        self.fault_aliases = dict(FAULT_ALIASES) | dict(custom.get("fault", {}))
        self.metric_aliases = dict(METRIC_ALIASES) | dict(custom.get("metric", {}))
        self.trace_aliases = dict(TRACE_ALIASES) | dict(custom.get("trace", {}))
        self.expected_checksums = dict(expected_checksums or {})
        self.wide_metric_fields = {key: tuple(value) for key, value in (wide_metric_fields or {}).items()}
        self.preserved_metadata_fields = tuple(preserved_metadata_fields)
        self.duration_units = dict(duration_units or {})
        if any(unit not in {"ns", "us", "ms", "s"} for unit in self.duration_units.values()):
            raise ValueError("duration_units aceita somente ns, us, ms ou s.")
        self.require_metric_service = require_metric_service
        self.strict = strict

    def _configuration(self, incident_id):
        return {"adapter": self.SOURCE, "adapter_version": AIOPS2020_ADAPTER_VERSION,
                "incident_id": incident_id, "analysis_window": self.get_analysis_window(incident_id).to_dict(),
                "window_policy": AIOPS2020_WINDOW_POLICY,
                "aliases": {"fault": self.fault_aliases, "metric": self.metric_aliases,
                            "trace": self.trace_aliases},
                "wide_metric_fields": self.wide_metric_fields,
                "documented_business_metrics": sorted(DOCUMENTED_BUSINESS_METRICS),
                "preserved_metadata_fields": self.preserved_metadata_fields,
                "duration_units": self.duration_units,
                "duration_policy_version": DURATION_POLICY_VERSION,
                "timestamp_policy": TIMESTAMP_POLICY_VERSION,
                "require_metric_service": self.require_metric_service,
                "strict": self.strict, "schema_version": UnifiedEvent.SCHEMA_VERSION}

    def _source_hashes(self):
        paths = ([self._fault_file()] if self._fault_file() else []) + [path for _, path in self._telemetry_files()]
        return {source_path(path, self.root): sha256_file(path) for path in paths}

    def _cache_identity(self, incident_id):
        configuration = self._configuration(incident_id)
        configuration_sha = sha256_value(configuration)
        hashes = self._source_hashes()
        return sha256_value({"configuration": configuration, "source_files_sha256": hashes}), configuration_sha, hashes

    def _fault_file(self) -> Path | None:
        if self.fault_csv:
            return self.fault_csv if self.fault_csv.exists() else None
        if not self.root.exists():
            return None
        candidates = [path for path in self.root.rglob("*.csv")
                      if "故障" in path.name or "fault" in path.name.lower()]
        return sorted(candidates)[0] if candidates else None

    def _fault_rows(self):
        path = self._fault_file()
        if path is None:
            return ()
        _, rows = read_csv(path)
        result = []
        for index, row in enumerate(rows):
            timestamp, _ = first(row, self.fault_aliases["time"])
            location, _ = first(row, self.fault_aliases["location"])
            fault_type, _ = first(row, self.fault_aliases["type"])
            result.append((index, row, timestamp, location, fault_type))
        return tuple(result)

    def list_incidents(self) -> tuple[IncidentDescriptor, ...]:
        path = self._fault_file()
        if path is None:
            return ()
        result = []
        for index, _, timestamp, location, fault_type in self._fault_rows():
            case = f"fault-row-{index}"
            if timestamp:
                case += f"-{timestamp}"
            result.append(IncidentDescriptor(
                f"aiops2020:{index}", self.SOURCE, "AIOps2020", case, str(path),
                fault_type, location))
        return tuple(result)

    def _descriptor(self, incident_id):
        try:
            return next(item for item in self.list_incidents() if item.incident_id == incident_id)
        except StopIteration as exc:
            raise KeyError(f"Incidente AIOps2020 desconhecido: {incident_id}") from exc

    def _fault(self, incident_id):
        descriptor = self._descriptor(incident_id)
        index = int(incident_id.rsplit(":", 1)[1])
        return descriptor, self._fault_rows()[index]

    @staticmethod
    def _kind(path: Path) -> str | None:
        value = "/".join(path.parts).lower()
        if any(token in value for token in ("调用链", "call-chain", "call_chain", "trace")):
            return "traces"
        if any(token in value for token in ("业务指标", "business")):
            return "business_metrics"
        if any(token in value for token in ("平台指标", "platform", "container", "docker",
                                             "host", "middleware", "database")):
            return "platform_metrics"
        return None

    def _telemetry_files(self) -> tuple[tuple[str, Path], ...]:
        fault = self._fault_file()
        if not self.root.exists():
            return ()
        return tuple((kind, path) for path in sorted(self.root.rglob("*.csv"))
                     if path != fault and (kind := self._kind(path.relative_to(self.root))) is not None)

    def get_analysis_window(self, incident_id: str) -> AnalysisWindow:
        _, (_, _, timestamp, _, _) = self._fault(incident_id)
        if not timestamp:
            raise ValueError(f"{incident_id}: fault CSV sem timestamp mapeável.")
        center = parse_timestamp(timestamp)
        return AnalysisWindow(center - self.pre_fault, center + self.post_fault)

    def get_ground_truth(self, incident_id: str) -> dict[str, Any]:
        descriptor, (index, row, timestamp, location, fault_type) = self._fault(incident_id)
        return {"incident_id": incident_id, "system": descriptor.system,
                "case": descriptor.case, "fault_timestamp": timestamp,
                "fault_location": location, "fault_type": fault_type,
                "source_row": index, "source_fields": dict(row)}

    def _cache_key(self, incident_id: str) -> str:
        return self._cache_identity(incident_id)[0]

    def _incident_results(self, incident_id: str) -> tuple[AIOpsFileNormalizationResult, ...]:
        window = self.get_analysis_window(incident_id)
        results = []
        for kind, path in self._telemetry_files():
            columns, rows = read_csv(path)
            results.append(self._normalize_trace_file(path, columns, rows, window) if kind == "traces"
                           else self._normalize_metric_file(path, columns, rows, window, kind))
        return tuple(results)

    def _normalize_metric_file(self, path, columns, rows, window, category):
        relative = source_path(path, self.root); events = []
        reasons, quality = Counter(), Counter()
        fully = partial = rejected = excluded = accepted_cells = rejected_cells = 0
        name_columns = set(columns) & set(self.metric_aliases["name"] + self.metric_aliases["item"])
        value_columns = set(columns) & set(self.metric_aliases["value"])
        long_layout = bool(name_columns and value_columns)
        allowed = set(self.wide_metric_fields.get(relative,
                      self.wide_metric_fields.get(path.name,
                      self.wide_metric_fields.get("*", ()))))
        if category == "business_metrics": allowed |= DOCUMENTED_BUSINESS_METRICS
        interpreted = name_columns | value_columns if long_layout else set(columns) & allowed
        alias_fields = {name for values in self.metric_aliases.values() for name in values}
        preserved = set(columns) & set(self.preserved_metadata_fields)
        rejected_fields = {column for column in set(columns) - alias_fields - preserved - interpreted
                           if any(_is_numeric(row.get(column)) for row in rows)}
        for index, row in enumerate(rows):
            timestamp, time_column = first(row, self.metric_aliases["time"])
            service, service_column = first(row, self.metric_aliases["service"])
            if timestamp is None:
                reasons["missing_timestamp"] += 1; rejected += 1; continue
            try: moment = parse_timestamp(timestamp)
            except (TypeError, ValueError, OverflowError):
                reasons["invalid_timestamp"] += 1; rejected += 1; continue
            if not window.start <= moment <= window.end:
                excluded += 1; continue
            if self.require_metric_service and not service:
                reasons["missing_service"] += 1; rejected += 1; continue
            consumed = {item for item in (time_column, service_column) if item}
            extras = {column: row[column] for column in self.preserved_metadata_fields
                      if column in row and row.get(column) not in (None, "")}
            points = []
            if long_layout:
                name, name_column = first(row, self.metric_aliases["name"] + self.metric_aliases["item"])
                value, value_column = first(row, self.metric_aliases["value"])
                consumed.update(item for item in (name_column, value_column) if item)
                if not name:
                    reasons["missing_metric_name"] += 1; rejected += 1; continue
                if not _is_numeric(value):
                    reasons["invalid_metric_value"] += 1; rejected += 1; rejected_cells += 1; continue
                points = [(name, finite_float(value))]
            else:
                invalid = 0
                for column in sorted(interpreted):
                    raw = row.get(column)
                    if raw in (None, ""): continue
                    try: points.append((column, finite_float(raw)))
                    except (TypeError, ValueError):
                        reasons[f"invalid_metric_cell:{column}"] += 1
                        invalid += 1; rejected_cells += 1
                if not points:
                    reasons["no_numeric_interpreted_metric"] += 1; rejected += 1; continue
                if invalid: partial += 1
                else: fully += 1
            if long_layout: fully += 1
            accepted_cells += len(points)
            for metric_name, numeric in points:
                events.append(UnifiedEvent(
                    event_id=event_id(self.SOURCE, relative, index, str(metric_name)),
                    event_name=str(metric_name), event_type="public_dataset_metric",
                    signal_type="metric", event_timestamp=moment, service_name=service,
                    metric_name=str(metric_name), metric_value=numeric,
                    attributes={"aiops2020.metric_category": category, **extras},
                    source_reference=f"{relative}#row={index};metric={metric_name}",
                    source_dataset=self.SOURCE))
        interpreted_fields = set(interpreted)
        identity_fields = (set(columns) & alias_fields) - interpreted_fields
        preserved_fields = set(preserved) - interpreted_fields - identity_fields
        rejected_fields = (set(rejected_fields) - interpreted_fields
                           - identity_fields - preserved_fields)
        unmapped_fields = (set(columns) - identity_fields - interpreted_fields
                           - preserved_fields - rejected_fields)
        unmapped = tuple(sorted(unmapped_fields))
        times = [event.event_timestamp for event in events]
        classification = SchemaClassification(
            tuple(sorted(identity_fields)), tuple(sorted(interpreted_fields)),
            tuple(sorted(preserved_fields)), tuple(sorted(unmapped_fields)),
            tuple(sorted(rejected_fields)))
        return AIOpsFileNormalizationResult(
            path, category, tuple(columns), len(rows), fully, partial, rejected, excluded,
            accepted_cells, rejected_cells, tuple(events), dict(reasons), dict(quality),
            classification, AnalysisWindow(min(times), max(times)) if times else None,
            is_wide_metric=not long_layout)

    def _normalize_trace_file(self, path, columns, rows, window):
        relative = source_path(path, self.root); events = []
        reasons, quality = Counter(), Counter(); accepted = rejected = excluded = 0
        alias_fields = {name for values in self.trace_aliases.values() for name in values}
        missing_duration = set()
        for index, row in enumerate(rows):
            timestamp, time_column = first(row, self.trace_aliases["time"])
            raw_trace, trace_column = first(row, self.trace_aliases["trace"])
            raw_id, id_column = first(row, self.trace_aliases["id"])
            raw_pid, pid_column = first(row, self.trace_aliases["pid"])
            service, service_column = first(row, self.trace_aliases["service"])
            if not timestamp: reasons["missing_timestamp"] += 1; rejected += 1; continue
            try: moment = parse_timestamp(timestamp)
            except (TypeError, ValueError, OverflowError):
                reasons["invalid_timestamp"] += 1; rejected += 1; continue
            if not window.start <= moment <= window.end: excluded += 1; continue
            missing = [name for name, value in (("traceid", raw_trace), ("span_id", raw_id),
                                                ("service", service)) if not value]
            if missing:
                reasons[f"missing_{missing[0]}"] += 1; rejected += 1; continue
            operation, operation_column = first(row, self.trace_aliases["operation"])
            duration, duration_column = first(row, self.trace_aliases["duration"])
            status, status_column = first(row, self.trace_aliases["status"])
            consumed = {item for item in (time_column, trace_column, id_column, pid_column,
                                          service_column, operation_column, duration_column,
                                          status_column) if item}
            attributes = {key: row[key] for key in columns if key not in consumed
                          and row.get(key) not in (None, "")}; flags = []
            normalized_ids = (valid_hex(raw_trace, (32,)), valid_hex(raw_id, (16,)),
                              valid_hex(raw_pid, (16,)))
            for raw, normalized, name in zip((raw_trace, raw_id, raw_pid), normalized_ids,
                                             ("trace_id", "span_id", "parent_span_id")):
                if raw and normalized is None:
                    attributes[f"aiops2020.raw_{name}"] = raw
                    flags.append(f"legacy_or_invalid_{name}")
            if duration is not None:
                unit = self.duration_units.get(duration_column)
                factors = {"ns": 1e-6, "us": 1e-3, "ms": 1.0, "s": 1000.0}
                values, duration_flags = duration_attributes(
                    duration, column=duration_column, unit=unit, factor_to_ms=factors.get(unit))
                attributes.update(values); flags.extend(duration_flags)
            else:
                flags.extend(("missing_modality_fields", "missing_normalized_duration"))
            quality.update(flags); accepted += 1
            if "duration_ms" not in attributes: missing_duration.add("duration_ms")
            events.append(UnifiedEvent(
                event_id=event_id(self.SOURCE, relative, index),
                event_name=operation or "call-chain.span", event_type="public_dataset_span",
                signal_type="trace", event_timestamp=moment,
                trace_id=normalized_ids[0], span_id=normalized_ids[1], parent_span_id=normalized_ids[2],
                service_name=service, status_code=status, attributes=attributes,
                source_reference=f"{relative}#row={index}", source_dataset=self.SOURCE,
                data_quality_flags=tuple(flags)))
        unmapped = tuple(sorted(set(columns) - alias_fields)); times = [event.event_timestamp for event in events]
        return AIOpsFileNormalizationResult(
            path, "traces", tuple(columns), len(rows), accepted, 0, rejected, excluded,
            0, 0, tuple(events), dict(reasons), dict(quality),
            SchemaClassification(tuple(sorted(set(columns) & alias_fields)), (), (), unmapped, ()),
            AnalysisWindow(min(times), max(times)) if times else None,
            tuple(sorted(missing_duration)))

    def _enforce_incident_results(self, incident_id, results, strict):
        issues = []
        if not results: issues.append(f"{incident_id}: não há telemetria")
        for result in results:
            if not result.events: issues.append(f"{incident_id}:{result.path.name}: zero eventos válidos")
            if result.rows_rejected: issues.append(f"{incident_id}:{result.path.name}: linhas rejeitadas")
            if result.rejected_metric_cell_count:
                issues.append(f"{incident_id}:{result.path.name}: células métricas rejeitadas")
        if not any(result.events for result in results): issues.append(f"{incident_id}: zero eventos normalizados")
        if strict and issues: raise ValueError("; ".join(issues))
        return tuple(issues)

    def normalize_events(self, incident_id: str) -> tuple[UnifiedEvent, ...]:
        self._descriptor(incident_id)
        key, configuration_sha, hashes = self._cache_identity(incident_id)
        results = self._incident_results(incident_id)
        self._enforce_incident_results(incident_id, results, self.strict)
        cached = self.cache.load(key, adapter=self.SOURCE,
                                 adapter_version=AIOPS2020_ADAPTER_VERSION,
                                 configuration_sha256=configuration_sha,
                                 source_files_sha256=hashes)
        if cached is not None:
            return cached
        result = tuple(sorted((event for item in results for event in item.events),
                              key=lambda item: (item.event_timestamp, item.event_id)))
        self.cache.store(key, result, adapter=self.SOURCE,
                         adapter_version=AIOPS2020_ADAPTER_VERSION,
                         configuration_sha256=configuration_sha,
                         source_files_sha256=hashes)
        return result

    def _schema(self, *, windows: tuple[AnalysisWindow, ...] | None = None,
                include_faults: bool = True,
                scope: str = "dataset_schema_report") -> SchemaReport:
        audit_windows = windows if windows is not None else tuple(
            self.get_analysis_window(item.incident_id) for item in self.list_incidents())
        reports = []
        if include_faults and (fault := self._fault_file()):
            columns, rows = read_csv(fault); reasons = Counter(); accepted = 0
            for row in rows:
                values = [first(row, self.fault_aliases[name])[0]
                          for name in ("time", "location", "type")]
                if all(values): accepted += 1
                else: reasons["missing_fault_identity"] += 1
            alias_fields = {name for values in self.fault_aliases.values() for name in values}
            unmapped = tuple(sorted(set(columns) - alias_fields))
            reports.append(SchemaFileReport(
                source_path(fault, self.root), "faults", columns, len(rows), sha256_file(fault),
                unmapped, accepted, len(rows) - accepted, 0, dict(reasons),
                SchemaClassification(tuple(sorted(set(columns) & alias_fields)), (), (), unmapped, ())))
        if audit_windows:
            broad = AnalysisWindow(min(window.start for window in audit_windows),
                                   max(window.end for window in audit_windows))
            for kind, path in self._telemetry_files():
                columns, rows = read_csv(path)
                result = self._normalize_trace_file(path, columns, rows, broad) if kind == "traces" else \
                    self._normalize_metric_file(path, columns, rows, broad, kind)
                reports.append(result.report(self.root))
        return SchemaReport(self.SOURCE, tuple(reports), datetime.now(timezone.utc), scope)

    def validate(self) -> ValidationReport:
        errors, warnings = [], []
        if not self.root.is_dir():
            errors.append(f"Diretório inexistente: {self.root}")
        fault = self._fault_file()
        if fault is None:
            errors.append("Fault CSV não localizado.")
        else:
            columns, _ = read_csv(fault)
            for semantic in ("time", "location", "type"):
                if not set(columns).intersection(self.fault_aliases[semantic]):
                    errors.append(f"Fault CSV sem campo mapeado para {semantic}.")
        files = self._telemetry_files()
        if not files:
            (errors if self.strict else warnings).append("Nenhum CSV de telemetria detectado.")
        kinds = {kind for kind, _ in files}
        if "traces" not in kinds:
            warnings.append("Call-chain ausente.")
        if "business_metrics" not in kinds:
            warnings.append("Business metrics ausentes.")
        if "platform_metrics" not in kinds:
            warnings.append("Platform metrics ausentes.")
        paths = ([fault] if fault else []) + [path for _, path in files]
        for path in paths:
            relative = source_path(path, self.root)
            expected = self.expected_checksums.get(relative)
            if expected and sha256_file(path) != expected:
                errors.append(f"Checksum SHA-256 divergente: {relative}")
        incident_windows = {}
        incident_results = {}
        if fault:
            for incident in self.list_incidents():
                try:
                    incident_windows[incident.incident_id] = self.get_analysis_window(incident.incident_id)
                    incident_results[incident.incident_id] = self._incident_results(incident.incident_id)
                except ValueError as exc: errors.append(str(exc))
        try:
            schema = self._schema(windows=tuple(incident_windows.values())) if fault and incident_windows else None
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(exc)); schema = None
        if schema:
            for report in schema.files:
                if report.kind != "faults" and report.event_count == 0:
                    message = f"{report.relative_path}: arquivo detectado produziu zero eventos"
                    (errors if self.strict else warnings).append(message)
                if report.rejected_row_count:
                    warnings.append(f"{report.relative_path}: {report.rejected_row_count} linhas rejeitadas")
                if report.kind in {"business_metrics", "platform_metrics"} and not report.classification.interpreted_metric_fields:
                    errors.append(f"{report.relative_path}: nenhum campo métrico explicitamente interpretado")
            if files and not any(report.event_count for report in schema.files if report.kind != "faults"):
                (errors if self.strict else warnings).append("Telemetria não produziu eventos para as janelas dos incidentes.")
            for incident_id, window in incident_windows.items():
                results = incident_results.get(incident_id, ())
                try: issues = self._enforce_incident_results(incident_id, results, self.strict)
                except ValueError as exc: errors.extend(str(exc).split("; "))
                else: warnings.extend(issues)
        return ValidationReport(not errors, tuple(errors), tuple(warnings), schema)

    def load_incident(self, incident_id: str) -> IncidentManifest:
        descriptor = self._descriptor(incident_id)
        results = self._incident_results(incident_id)
        issues = self._enforce_incident_results(incident_id, results, self.strict)
        events = self.normalize_events(incident_id)
        counts = Counter(event.signal_type.value for event in events)
        window = self.get_analysis_window(incident_id)
        schema = SchemaReport(self.SOURCE, tuple(item.report(self.root) for item in results),
                              datetime.now(timezone.utc), "incident_normalization_report")
        dataset_schema = self._schema(scope="dataset_schema_report")
        unmapped = {item.relative_path: item.unmapped_fields for item in schema.files
                    if item.unmapped_fields}
        key, config_sha, hashes = self._cache_identity(incident_id)
        observed = AnalysisWindow(events[0].event_timestamp, events[-1].event_timestamp) if events else None
        accepted = sum(item.accepted_row_count for item in schema.files)
        rejected = sum(item.rejected_row_count for item in schema.files)
        excluded = sum(item.excluded_by_window_count for item in schema.files)
        truth = self.get_ground_truth(incident_id)
        observed_modalities = tuple(sorted(counts))
        kind_modalities = {"traces": "trace", "business_metrics": "metric",
                           "platform_metrics": "metric"}
        expected_modalities = tuple(sorted({kind_modalities[item.kind] for item in results}))
        return IncidentManifest(
            descriptor.incident_id, self.SOURCE, descriptor.system, descriptor.case,
            descriptor.fault_type, descriptor.fault_service,
            window, tuple(sorted(counts)), hashes, counts, unmapped,
            AIOPS2020_ADAPTER_VERSION, config_sha, key,
            sha256_value([event.to_dict() for event in events]), sha256_value(truth),
            schema.fingerprint, AIOPS2020_WINDOW_POLICY, observed, excluded,
            "events-outside-requested-window-excluded", accepted, rejected, len(events),
            considered_row_count=accepted + rejected + excluded,
            excluded_by_window_count=excluded,
            rejection_reasons_by_file={item.relative_path: dict(item.rejection_reasons)
                                       for item in schema.files},
            dataset_schema_report_sha256=dataset_schema.fingerprint,
            incident_normalization_report_sha256=schema.fingerprint,
            normalization_mode="strict" if self.strict else "permissive",
            empty_incident_reason="; ".join(issues) if issues else None,
            usable_for_benchmark=bool(events and not issues),
            quality_issue_reasons_by_file={item.relative_path: dict(item.quality_issue_reasons)
                                           for item in schema.files},
            modality_policy_version="aiops2020-detected-file-modality-policy-v1",
            expected_modalities=expected_modalities,
            observed_modalities=observed_modalities,
            missing_expected_modalities=tuple(sorted(set(expected_modalities) - set(observed_modalities))),
            unexpected_modalities=tuple(sorted(set(observed_modalities) - set(expected_modalities))),
            normalized_source_files=tuple(source_path(item.path, self.root) for item in results),
            modality_policy_status="detected",
            normalization_accounting_by_file={
                source_path(item.path, self.root): NormalizedFileAccounting(
                    relative_path=source_path(item.path, self.root), kind=item.kind,
                    signal_type="trace" if item.kind == "traces" else "metric",
                    total_rows=item.rows_total, accepted_rows=item.rows_accepted,
                    rejected_rows=item.rows_rejected,
                    partially_accepted_rows=item.rows_partially_accepted,
                    excluded_rows=item.rows_excluded_by_window,
                    produced_events=len(item.events),
                    accepted_metric_cells=item.accepted_metric_cell_count,
                    rejected_metric_cells=item.rejected_metric_cell_count,
                    rejected_row_units=item.rows_rejected,
                    rejected_cell_units=item.rejected_metric_cell_count)
                for item in results})

    def _outside_window_count(self, window):
        count = 0
        for kind, path in self._telemetry_files():
            columns, rows = read_csv(path)
            aliases = self.trace_aliases if kind == "traces" else self.metric_aliases
            for row in rows:
                timestamp, _ = first(row, aliases["time"])
                if timestamp:
                    try: count += not (window.start <= parse_timestamp(timestamp) <= window.end)
                    except (TypeError, ValueError, OverflowError): pass
        return count


def _is_numeric(value):
    try: finite_float(value); return True
    except (TypeError, ValueError): return False
