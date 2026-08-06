from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from multimodal_v1.contracts import UnifiedEvent
from .base import DatasetAdapter, NormalizedEventCache
from .common import (DURATION_POLICY_VERSION, TIMESTAMP_POLICY_VERSION,
                     duration_attributes, event_id, finite_float, parse_timestamp,
                     read_csv, valid_hex)
from .contracts import (AnalysisWindow, IncidentDescriptor, IncidentManifest,
                        NormalizedFileAccounting, SchemaClassification, SchemaFileReport, SchemaReport,
                        ValidationReport, cell_rejection_count, sha256_file, sha256_value)

RCA_LOG_COLUMNS = {"time", "timestamp", "container_name", "message", "level",
                   "req_path", "error", "cluster_id", "log_template"}
RCA_TRACE_COLUMNS = {"time", "traceID", "spanID", "serviceName", "methodName",
                     "operationName", "startTimeMillis", "startTime", "duration",
                     "statusCode", "parentSpanID"}
RCAEVAL_ADAPTER_VERSION = "rcaeval-adapter-v3"
RCAEVAL_PREWINDOWED_POLICY = "source-prewindowed-v1"
RCAEVAL_RAW_WINDOW_POLICY = "raw-observed-interval-v1"
RCAEVAL_DURATION_POLICY = {"column": "duration", "unit": "microsecond",
                           "factor_to_ms": 0.001, "version": DURATION_POLICY_VERSION}
RCAEVAL_MODALITY_POLICY_VERSION = "rcaeval-suite-modality-policy-v1"
RCAEVAL_SUITE_MODALITIES = {
    "RE1-OB": ("metric",), "RE1-SS": ("metric",), "RE1-TT": ("metric",),
    "RE2-OB": ("metric", "log", "trace"), "RE2-TT": ("metric", "log", "trace"),
    "RE2-SS": ("metric", "log"),
    "RE3-OB": ("metric", "log", "trace"), "RE3-TT": ("metric", "log", "trace"),
    "RE3-SS": ("metric", "log"),
}


@dataclass(frozen=True)
class _FileResult:
    path: Path
    kind: str
    columns: tuple[str, ...]
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    events: tuple[UnifiedEvent, ...]
    rejection_reasons: dict[str, int]
    classification: SchemaClassification
    observed_interval: AnalysisWindow | None
    missing_normalized_fields: tuple[str, ...] = ()
    quality_issue_reasons: dict[str, int] = None
    wide_accounting: dict[str, int] = None

    def report(self, root: Path) -> SchemaFileReport:
        relative = str(self.path.relative_to(root))
        accounting = self.wide_accounting or {}
        metric_json = self.kind == "metrics" and self.path.suffix == ".json"
        return SchemaFileReport(
            relative_path=relative, kind=self.kind, columns=self.columns,
            row_count=self.rows_total, checksum_sha256=sha256_file(self.path),
            unmapped_fields=self.classification.unmapped_fields,
            accepted_row_count=self.rows_accepted, rejected_row_count=self.rows_rejected,
            event_count=len(self.events), rejection_reasons=self.rejection_reasons,
            classification=self.classification,
            missing_normalized_fields=self.missing_normalized_fields,
            quality_issue_reasons=self.quality_issue_reasons or {},
            wide_row_policy="partial-wide-row-v1" if self.kind == "metrics" and self.path.suffix != ".json" else None,
            layout_policy=("metric-json-point-v1" if metric_json else
                           ("partial-wide-row-v1" if self.kind == "metrics" else "non-metric-v1")),
            accepted_metric_cell_count=(len(self.events) if metric_json else
                                        accounting.get("accepted_metric_cell_count", 0)),
            rejected_metric_cell_count=(cell_rejection_count(self.rejection_reasons)
                                        if metric_json else accounting.get("rejected_metric_cell_count", 0)),
            **{key: value for key, value in accounting.items()
               if key not in {"accepted_metric_cell_count", "rejected_metric_cell_count"}})


class RCAEvalAdapter(DatasetAdapter):
    SOURCE = "rcaeval"

    def __init__(self, root: str | Path, *, cache_dir: str | Path | None = None,
                 pre_injection: timedelta = timedelta(minutes=10),
                 post_injection: timedelta = timedelta(minutes=10),
                 expected_checksums: dict[str, str] | None = None, strict: bool = True):
        self.root = Path(root).expanduser().resolve()
        self.cache = NormalizedEventCache(cache_dir)
        self.pre_injection, self.post_injection = pre_injection, post_injection
        self.expected_checksums = dict(expected_checksums or {})
        self.strict = strict

    def _modality_policy(self, descriptor):
        expected = RCAEVAL_SUITE_MODALITIES.get(descriptor.system)
        return (tuple(expected or ()), expected is not None)

    def list_incidents(self) -> tuple[IncidentDescriptor, ...]:
        if not self.root.exists(): return ()
        directories = {path.parent for path in self.root.rglob("inject_time.txt")}
        directories |= {path.parent for name in ("metrics.json", "metrics.parquet", "metrics.csv")
                        for path in self.root.rglob(name)}
        result = []
        for directory in sorted(directories):
            parts = directory.relative_to(self.root).parts
            system = next((part.upper() for part in parts
                           if part.lower().startswith(("re1-", "re2-", "re3-"))),
                          parts[0] if len(parts) > 1 else "unknown")
            case, leaf = "/".join(parts), parts[-1]
            label = parts[-2] if leaf.isdigit() and len(parts) > 1 else leaf
            tokens = label.rsplit("_", 2)
            if leaf.isdigit():
                service, fault = (tokens[0], tokens[-1]) if len(tokens) >= 2 else (None, None)
            else:
                service = tokens[0] if len(tokens) >= 2 else None
                fault = tokens[-2] if len(tokens) == 3 else (tokens[-1] if len(tokens) == 2 else None)
            result.append(IncidentDescriptor(
                f"rcaeval:{case.replace('/', ':')}", self.SOURCE, system, case,
                str(directory), fault, service))
        return tuple(result)

    def _descriptor(self, incident_id: str) -> IncidentDescriptor:
        try: return next(item for item in self.list_incidents() if item.incident_id == incident_id)
        except StopIteration as exc: raise KeyError(f"Incidente RCAEval desconhecido: {incident_id}") from exc

    def _files(self, descriptor: IncidentDescriptor) -> dict[str, Path]:
        directory = Path(descriptor.path)
        candidates = {kind: next((directory / name for name in names if (directory / name).exists()), None)
                      for kind, names in {"metrics": ("metrics.parquet", "metrics.json", "metrics.csv"),
                                          "logs": ("logs.parquet", "logs.csv"),
                                          "traces": ("traces.parquet", "traces.csv"),
                                          "inject_time": ("inject_time.txt",)}.items()}
        return {key: value for key, value in candidates.items() if value is not None}

    @staticmethod
    def _table(path: Path) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
        if path.suffix == ".csv": return read_csv(path)
        if path.suffix == ".parquet":
            try: import pandas as pd
            except ImportError as exc: raise RuntimeError("Leitura Parquet requer pandas/pyarrow.") from exc
            frame = pd.read_parquet(path)
            return tuple(str(item) for item in frame.columns), frame.to_dict("records")
        raise ValueError(f"Tabela não suportada: {path}")

    def _source_hashes(self, descriptor):
        return {str(path.relative_to(self.root)): sha256_file(path)
                for path in self._files(descriptor).values()}

    def _raw_timestamps(self, descriptor: IncidentDescriptor) -> list[datetime]:
        timestamps = []
        for kind, path in self._files(descriptor).items():
            if kind == "inject_time": continue
            if kind == "metrics" and path.suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                values = [point[0] for series in payload.values() if isinstance(series, list)
                          for point in series if isinstance(point, (list, tuple)) and point]
            else:
                columns, rows = self._table(path)
                column = "time" if kind == "metrics" else ("timestamp" if kind == "logs" else "startTime")
                values = [row.get(column) for row in rows if column in columns]
            for value in values:
                try: timestamps.append(parse_timestamp(value))
                except (TypeError, ValueError, OverflowError): continue
        return timestamps

    def _raw_observed_interval(self, descriptor: IncidentDescriptor) -> AnalysisWindow:
        timestamps = self._raw_timestamps(descriptor)
        if not timestamps:
            raise ValueError(f"{descriptor.incident_id}: nenhum timestamp fonte válido para derivar janela.")
        return AnalysisWindow(min(timestamps), max(timestamps))

    def get_analysis_window(self, incident_id: str) -> AnalysisWindow:
        descriptor = self._descriptor(incident_id)
        injection = self._files(descriptor).get("inject_time")
        if injection:
            try: center = parse_timestamp(injection.read_text(encoding="utf-8").strip())
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{incident_id}: inject_time inválido.") from exc
            return AnalysisWindow(center - self.pre_injection, center + self.post_injection)
        return self._raw_observed_interval(descriptor)

    def _window_policy(self, descriptor):
        return RCAEVAL_PREWINDOWED_POLICY if "inject_time" in self._files(descriptor) else RCAEVAL_RAW_WINDOW_POLICY

    def _configuration(self, descriptor):
        return {"adapter": self.SOURCE, "adapter_version": RCAEVAL_ADAPTER_VERSION,
                "incident_id": descriptor.incident_id,
                "analysis_window": self.get_analysis_window(descriptor.incident_id).to_dict(),
                "window_policy": self._window_policy(descriptor), "strict": self.strict,
                "timestamp_policy": TIMESTAMP_POLICY_VERSION,
                "duration_policy": RCAEVAL_DURATION_POLICY,
                "modality_policy_version": RCAEVAL_MODALITY_POLICY_VERSION,
                "expected_modalities": self._modality_policy(descriptor)[0],
                "schema_version": UnifiedEvent.SCHEMA_VERSION}

    def _cache_identity(self, descriptor):
        configuration = self._configuration(descriptor); configuration_sha = sha256_value(configuration)
        hashes = self._source_hashes(descriptor)
        return sha256_value({"configuration": configuration, "source_files_sha256": hashes}), configuration_sha, hashes

    def _cache_key(self, descriptor): return self._cache_identity(descriptor)[0]

    def _incident_results(self, descriptor: IncidentDescriptor) -> tuple[_FileResult, ...]:
        results = []
        for kind, path in self._files(descriptor).items():
            if kind != "inject_time": results.append(self._normalize_file(descriptor, kind, path))
        return tuple(results)

    def _normalize_file(self, descriptor, kind, path) -> _FileResult:
        if kind == "metrics": return self._normalize_metrics(descriptor, path)
        columns, rows = self._table(path)
        return self._normalize_logs(descriptor, path, columns, rows) if kind == "logs" else \
            self._normalize_traces(descriptor, path, columns, rows)

    def _normalize_metrics(self, descriptor, path):
        relative = str(path.relative_to(self.root)); events = []; reasons = Counter(); accepted = 0
        quality = Counter(); accounting = {}
        if path.suffix == ".json":
            try: payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc: raise ValueError(f"JSON métrico inválido: {path}") from exc
            columns = tuple(payload) if isinstance(payload, dict) else ()
            items = []
            if not isinstance(payload, dict): reasons["metrics_json_not_object"] += 1
            else:
                for name, series in payload.items():
                    if not isinstance(series, list):
                        items.append((name, None)); continue
                    items.extend((name, point) for point in series)
            for index, (name, point) in enumerate(items):
                reason = None
                if not isinstance(name, str) or not name.strip(): reason = "invalid_metric_name"
                elif not isinstance(point, (list, tuple)) or len(point) < 2: reason = "invalid_metric_point_structure"
                else:
                    try: moment, value = parse_timestamp(point[0]), finite_float(point[1])
                    except (TypeError, ValueError, OverflowError):
                        try: parse_timestamp(point[0]); reason = "invalid_metric_value"
                        except (TypeError, ValueError, OverflowError): reason = "invalid_metric_timestamp"
                if reason: reasons[reason] += 1; continue
                accepted += 1; events.append(self._metric_event(descriptor, relative, index, name, moment, value))
            total = len(items) or sum(reasons.values())
            interpreted = columns
        else:
            columns, rows = self._table(path); total = len(rows); interpreted = tuple(c for c in columns if c != "time")
            fully_accepted = partially_accepted = fully_rejected = accepted_cells = rejected_cells = 0
            if "time" not in columns:
                reasons["missing_time_column"] = len(rows) or 1
                fully_rejected = len(rows)
            else:
                for row_index, row in enumerate(rows):
                    invalid_cells = 0; produced = 0
                    try: moment = parse_timestamp(row.get("time"))
                    except (TypeError, ValueError, OverflowError):
                        reasons["invalid_metric_timestamp"] += 1; fully_rejected += 1; continue
                    for name in interpreted:
                        value = row.get(name)
                        if value in (None, ""): continue
                        try: number = finite_float(value)
                        except (TypeError, ValueError):
                            reasons[f"invalid_metric_cell:{name}"] += 1
                            invalid_cells += 1; rejected_cells += 1; continue
                        events.append(self._metric_event(descriptor, relative, row_index, name, moment, number)); produced += 1
                    accepted_cells += produced
                    if not produced:
                        reasons["row_without_valid_metric"] += 1; fully_rejected += 1
                    elif invalid_cells:
                        accepted += 1; partially_accepted += 1
                    else:
                        accepted += 1; fully_accepted += 1
            accounting = {"rows_fully_accepted": fully_accepted,
                          "rows_partially_accepted": partially_accepted,
                          "rows_fully_rejected": fully_rejected,
                          "accepted_metric_cell_count": accepted_cells,
                          "rejected_metric_cell_count": rejected_cells}
        rejected = total - accepted
        for event in events:
            quality.update(event.data_quality_flags)
        times = [event.event_timestamp for event in events]
        classification = SchemaClassification(("time",) if "time" in columns else (), tuple(interpreted), (), (), ())
        return _FileResult(path, "metrics", tuple(columns), total, accepted, rejected,
                           tuple(events), dict(reasons), classification,
                           AnalysisWindow(min(times), max(times)) if times else None,
                           quality_issue_reasons=dict(quality), wide_accounting=accounting)

    def _metric_event(self, descriptor, relative, index, name, moment, value):
        service = name.split("_", 1)[0] if isinstance(name, str) and "_" in name else None
        flags = () if service else ("metric_service_not_derivable",)
        return UnifiedEvent(event_id=event_id(self.SOURCE, relative, index, str(name)),
            event_name=str(name), event_type="public_dataset_metric", signal_type="metric",
            event_timestamp=moment, service_name=service, metric_name=str(name), metric_value=value,
            attributes={"rcaeval.case": descriptor.case, "rcaeval.system": descriptor.system},
            source_reference=f"{relative}#row={index};metric={name}", source_dataset=self.SOURCE,
            data_quality_flags=flags)

    def _normalize_logs(self, descriptor, path, columns, rows):
        relative = str(path.relative_to(self.root)); events = []; reasons = Counter(); accepted = 0
        required = {"timestamp", "container_name", "message"}
        for index, row in enumerate(rows):
            reason = None
            try: moment = parse_timestamp(row.get("timestamp"))
            except (TypeError, ValueError, OverflowError): reason = "invalid_log_timestamp"
            if not isinstance(row.get("container_name"), str) or not row.get("container_name", "").strip(): reason = reason or "invalid_container_name"
            if not isinstance(row.get("message"), str) or not row.get("message", "").strip(): reason = reason or "missing_log_message"
            if reason: reasons[reason] += 1; continue
            accepted += 1
            extras = {key: row.get(key) for key in set(columns) - required if row.get(key) not in (None, "")}
            events.append(UnifiedEvent(event_id=event_id(self.SOURCE, relative, index),
                event_name="log.record", event_type="public_dataset_log", signal_type="log",
                event_timestamp=moment, service_name=row["container_name"],
                severity=row.get("level") or None, error_type=row.get("error") or None,
                attributes={"body": row["message"], **extras},
                source_reference=f"{relative}#row={index}", source_dataset=self.SOURCE))
        times = [event.event_timestamp for event in events]; unmapped = tuple(sorted(set(columns) - RCA_LOG_COLUMNS))
        return _FileResult(path, "logs", tuple(columns), len(rows), accepted, len(rows)-accepted,
                           tuple(events), dict(reasons), SchemaClassification(
                               tuple(sorted(set(columns) & RCA_LOG_COLUMNS)), (), (), unmapped, ()),
                           AnalysisWindow(min(times), max(times)) if times else None)

    def _normalize_traces(self, descriptor, path, columns, rows):
        relative = str(path.relative_to(self.root)); events = []; reasons = Counter(); quality = Counter(); accepted = 0
        missing_duration = set()
        for index, row in enumerate(rows):
            reason = None
            try: moment = parse_timestamp(row.get("startTime"))
            except (TypeError, ValueError, OverflowError): reason = "invalid_trace_start_time"
            if not isinstance(row.get("serviceName"), str) or not row.get("serviceName", "").strip(): reason = reason or "invalid_trace_service"
            if reason: reasons[reason] += 1; continue
            attributes = {"methodName": row.get("methodName")}
            extras = {key: row.get(key) for key in set(columns)-RCA_TRACE_COLUMNS if row.get(key) not in (None, "")}; attributes.update(extras)
            trace_id, span_id, parent = (valid_hex(str(row.get(name) or ""), lengths)
                for name, lengths in (("traceID", (32,)), ("spanID", (16,)), ("parentSpanID", (16,))))
            flags = []
            for source, canonical, normalized in (("traceID", "trace_id", trace_id),
                                                   ("spanID", "span_id", span_id),
                                                   ("parentSpanID", "parent_span_id", parent)):
                raw = row.get(source)
                if raw not in (None, "") and normalized is None:
                    attributes[f"rcaeval.raw_{canonical}"] = str(raw); flags.append(f"legacy_or_invalid_{canonical}")
            if row.get("duration") not in (None, ""):
                values, duration_flags = duration_attributes(row["duration"], column="duration",
                    unit="microsecond", factor_to_ms=.001)
                attributes.update(values); flags.extend(duration_flags)
                if duration_flags: missing_duration.add("duration_ms")
            else: missing_duration.add("duration_ms")
            for flag in flags: quality[flag] += 1
            if "duration_ms" not in attributes and "missing_normalized_duration" not in flags:
                quality["missing_normalized_duration"] += 1
            accepted += 1; events.append(UnifiedEvent(
                event_id=event_id(self.SOURCE, relative, index),
                event_name=str(row.get("operationName") or row.get("methodName") or "span"),
                event_type="public_dataset_span", signal_type="trace", event_timestamp=moment,
                trace_id=trace_id, span_id=span_id, parent_span_id=parent,
                service_name=row["serviceName"], status_code=row.get("statusCode") or None,
                attributes=attributes, source_reference=f"{relative}#row={index}",
                source_dataset=self.SOURCE, data_quality_flags=tuple(flags)))
        times = [event.event_timestamp for event in events]; unmapped = tuple(sorted(set(columns)-RCA_TRACE_COLUMNS))
        return _FileResult(path, "traces", tuple(columns), len(rows), accepted, len(rows)-accepted,
            tuple(events), dict(reasons), SchemaClassification(
                tuple(sorted(set(columns)&RCA_TRACE_COLUMNS)), (), (), unmapped, ()),
            AnalysisWindow(min(times), max(times)) if times else None,
            tuple(sorted(missing_duration)), dict(quality))

    def _enforce_incident_results(self, descriptor: IncidentDescriptor,
                                  results: tuple[_FileResult, ...], strict: bool) -> tuple[str, ...]:
        files = self._files(descriptor)
        expected, official = self._modality_policy(descriptor)
        kind_to_modality = {"metrics": "metric", "logs": "log", "traces": "trace"}
        observed_files = {kind_to_modality[kind] for kind in files if kind in kind_to_modality}
        missing = set(expected) - observed_files
        unexpected = observed_files - set(expected)
        issues, fatal = [], []
        if not official:
            message = f"{descriptor.incident_id}: sistema sem política oficial de modalidades"
            issues.append(message); fatal.append(message)
        for modality in sorted(missing):
            message = f"{descriptor.incident_id}: modalidade esperada ausente: {modality}"
            issues.append(message); fatal.append(message)
        for modality in sorted(unexpected):
            issues.append(f"{descriptor.incident_id}: modalidade extra preservada: {modality}")
        for result in results:
            if len(result.events) == 0:
                message = f"{descriptor.incident_id}:{result.path.name}: zero eventos válidos"
                issues.append(message); fatal.append(message)
            if result.rows_rejected > 0 or sum(result.rejection_reasons.values()) > 0:
                message = f"{descriptor.incident_id}:{result.path.name}: rejeições detectadas"
                issues.append(message); fatal.append(message)
        total_event_count = sum(len(result.events) for result in results)
        metric_event_count = sum(len(result.events) for result in results if result.kind == "metrics")
        if total_event_count == 0:
            message = f"{descriptor.incident_id}: zero eventos no incidente"
            issues.append(message); fatal.append(message)
        if metric_event_count == 0:
            message = f"{descriptor.incident_id}: zero métricas válidas"
            issues.append(message); fatal.append(message)
        if strict and fatal:
            raise ValueError("; ".join(fatal))
        return tuple(issues)

    def normalize_events(self, incident_id: str) -> tuple[UnifiedEvent, ...]:
        descriptor = self._descriptor(incident_id); key, config_sha, hashes = self._cache_identity(descriptor)
        results = self._incident_results(descriptor)
        self._enforce_incident_results(descriptor, results, self.strict)
        cached = self.cache.load(key, adapter=self.SOURCE, adapter_version=RCAEVAL_ADAPTER_VERSION,
                                 configuration_sha256=config_sha, source_files_sha256=hashes)
        if cached is not None: return cached
        events = tuple(sorted((event for result in results for event in result.events),
                              key=lambda item: (item.event_timestamp, item.event_id)))
        self.cache.store(key, events, adapter=self.SOURCE, adapter_version=RCAEVAL_ADAPTER_VERSION,
                         configuration_sha256=config_sha, source_files_sha256=hashes)
        return events

    def get_ground_truth(self, incident_id: str) -> dict[str, Any]:
        item = self._descriptor(incident_id)
        return {"incident_id": item.incident_id, "system": item.system, "case": item.case,
                "fault_type": item.fault_type, "fault_service": item.fault_service}

    def _schema(self) -> SchemaReport:
        reports = tuple(result.report(self.root) for incident in self.list_incidents()
                        for result in self._incident_results(incident))
        return SchemaReport(self.SOURCE, reports, datetime.now(timezone.utc))

    def validate(self) -> ValidationReport:
        errors, warnings = [], []
        if not self.root.is_dir(): errors.append(f"Diretório inexistente: {self.root}")
        incidents = self.list_incidents()
        if self.root.is_dir() and not incidents: errors.append("Nenhum caso RCAEval detectado.")
        reports = []
        for incident in incidents:
            files = self._files(incident)
            try: results = self._incident_results(incident)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append(f"{incident.incident_id}: {exc}"); continue
            reports.extend(result.report(self.root) for result in results)
            try:
                issues = self._enforce_incident_results(incident, results, self.strict)
            except ValueError as exc:
                errors.extend(str(exc).split("; "))
            else:
                warnings.extend(issues)
            for path in files.values():
                relative = str(path.relative_to(self.root)); expected = self.expected_checksums.get(relative)
                if expected and sha256_file(path) != expected: errors.append(f"Checksum SHA-256 divergente: {relative}")
            try: self.get_analysis_window(incident.incident_id)
            except ValueError as exc: errors.append(str(exc))
        schema = SchemaReport(self.SOURCE, tuple(reports), datetime.now(timezone.utc)) if reports else None
        return ValidationReport(not errors, tuple(errors), tuple(warnings), schema)

    def load_incident(self, incident_id: str) -> IncidentManifest:
        descriptor = self._descriptor(incident_id); results = self._incident_results(descriptor)
        issues = self._enforce_incident_results(descriptor, results, self.strict)
        events = self.normalize_events(incident_id); counts = Counter(event.signal_type.value for event in events)
        schema = SchemaReport(self.SOURCE, tuple(result.report(self.root) for result in results),
                              datetime.now(timezone.utc), "incident_normalization_report")
        dataset_schema = self._schema()
        key, config_sha, hashes = self._cache_identity(descriptor); requested = self.get_analysis_window(incident_id)
        observed_times = [event.event_timestamp for event in events]
        observed = AnalysisWindow(min(observed_times), max(observed_times)) if observed_times else None
        outside = sum(not (requested.start <= value <= requested.end) for value in observed_times)
        expected, official = self._modality_policy(descriptor)
        observed_modalities = tuple(sorted(counts))
        missing_modalities = tuple(sorted(set(expected) - set(observed_modalities)))
        unexpected_modalities = tuple(sorted(set(observed_modalities) - set(expected)))
        semantic_loss = (bool(missing_modalities) or not official or not events
                         or not counts.get("metric", 0)
                         or any(r.rows_rejected or r.rejection_reasons or not r.events for r in results))
        return IncidentManifest(
            descriptor.incident_id, self.SOURCE, descriptor.system, descriptor.case,
            descriptor.fault_type, descriptor.fault_service, requested, tuple(sorted(counts)),
            hashes, counts, {report.relative_path: report.unmapped_fields for report in schema.files if report.unmapped_fields},
            RCAEVAL_ADAPTER_VERSION, config_sha, key, sha256_value([event.to_dict() for event in events]),
            sha256_value(self.get_ground_truth(incident_id)), schema.fingerprint,
            self._window_policy(descriptor), observed, outside,
            "raw-source-events-included", sum(r.rows_accepted for r in results),
            sum(r.rows_rejected for r in results), len(events),
            considered_row_count=sum(r.rows_total for r in results),
            excluded_by_window_count=0,
            rejection_reasons_by_file={str(r.path.relative_to(self.root)): r.rejection_reasons for r in results},
            dataset_schema_report_sha256=dataset_schema.fingerprint,
            incident_normalization_report_sha256=schema.fingerprint,
            normalization_mode="strict" if self.strict else "permissive",
            empty_incident_reason="; ".join(issues) if semantic_loss else None,
            usable_for_benchmark=not semantic_loss,
            quality_issue_reasons_by_file={str(r.path.relative_to(self.root)): r.quality_issue_reasons or {}
                                           for r in results},
            modality_policy_version=RCAEVAL_MODALITY_POLICY_VERSION,
            expected_modalities=expected, observed_modalities=observed_modalities,
            missing_expected_modalities=missing_modalities,
            unexpected_modalities=unexpected_modalities,
            normalized_source_files=tuple(str(r.path.relative_to(self.root)) for r in results),
            modality_policy_status="official" if official else "nonofficial",
            normalization_accounting_by_file={
                str(r.path.relative_to(self.root)): NormalizedFileAccounting(
                    relative_path=str(r.path.relative_to(self.root)), kind=r.kind,
                    signal_type={"metrics": "metric", "logs": "log", "traces": "trace"}[r.kind],
                    total_rows=r.rows_total, accepted_rows=r.rows_accepted,
                    rejected_rows=r.rows_rejected,
                    partially_accepted_rows=(r.wide_accounting or {}).get(
                        "rows_partially_accepted", 0), excluded_rows=0,
                    produced_events=len(r.events),
                    accepted_metric_cells=len(r.events) if r.kind == "metrics" else 0,
                    rejected_metric_cells=cell_rejection_count(r.rejection_reasons),
                    rejected_row_units=r.rows_rejected,
                    rejected_cell_units=cell_rejection_count(r.rejection_reasons))
                for r in results})
