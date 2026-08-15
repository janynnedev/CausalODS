"""Materialização resource-safe do RCAEval oficial distribuído no Hugging Face.

Este módulo é uma borda experimental. Ele não altera o adapter RCAEval legado nem
os contratos congelados. O catálogo ``cases.parquet`` é autoritativo, cada caso é
processado serialmente e os eventos normalizados são persistidos em gzip fora do
Git para permitir retomada sem reler os Parquets.
"""

from __future__ import annotations

import argparse
import csv
import gc
import gzip
import hashlib
import heapq
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.multimodal_v3.core import DatasetCapabilityV3
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.public_datasets_v3 import (
    DatasetAdapter,
    IncidentDescriptor,
    IncidentManifest,
    NormalizedFileAccounting,
    SchemaClassification,
    SchemaFileReport,
    SchemaReport,
    ValidationReport,
)
from multimodal_v1.public_datasets_v3.contracts import (
    AnalysisWindow,
    canonical_json,
    cell_rejection_count,
    row_rejection_count,
    sha256_file,
    sha256_value,
)
from multimodal_v1.public_datasets_v3.rcaeval import (
    RCAEVAL_MODALITY_POLICY_VERSION,
    RCAEvalAdapter,
)


MATERIALIZER_VERSION = "rcaeval-official-hf-materializer-v3"
CACHE_VERSION = "rcaeval-official-normalized-parquet-v1"
INTEGRITY_POLICY_VERSION = "rcaeval-official-integrity-policy-v2"
CONTRACT_EVOLUTION_REASON = (
    "official RCAEval revealed representational mismatch for partial valid observations"
)
JSON_EVENT_FIELDS = frozenset({
    "span_links", "attributes", "resource_attributes", "data_quality_flags",
})
SYSTEM_NAMES = {"ob": "OB", "ss": "SS", "tt": "TT"}
SUITE_MODALITIES = {
    ("RE1", "OB"): ("metric",),
    ("RE1", "SS"): ("metric",),
    ("RE1", "TT"): ("metric",),
    ("RE2", "OB"): ("metric", "log", "trace"),
    ("RE2", "SS"): ("metric", "log"),
    ("RE2", "TT"): ("metric", "log", "trace"),
    ("RE3", "OB"): ("metric", "log", "trace"),
    ("RE3", "SS"): ("metric", "log"),
    ("RE3", "TT"): ("metric", "log", "trace"),
}
CATALOG_COLUMNS = (
    "case", "dataset", "suite", "system", "system_name",
    "root_cause_service", "fault", "fault_description", "repetition",
    "inject_time", "n_metrics", "n_timesteps", "time_start", "time_end",
    "duration_minutes", "normal_timesteps", "faulty_timesteps", "has_logs",
    "n_logs", "has_traces", "n_traces", "has_root_cause_file",
)


def classify_incident_integrity(
    *, reports: Iterable[SchemaFileReport], expected_modalities: Iterable[str],
    observed_modalities: Iterable[str], total_event_count: int,
    event_count_by_modality: Mapping[str, int], ground_truth_conflict: str | None,
    structural_errors: Iterable[str] = (),
) -> dict[str, Any]:
    """Classifica integridade pela semântica, sem threshold ou output de modelo."""
    reports = tuple(reports)
    expected = set(expected_modalities)
    observed = set(observed_modalities)
    blocking = []
    partial = []
    if total_event_count <= 0:
        blocking.append("zero_valid_events")
    if "metric" in expected and event_count_by_modality.get("metric", 0) <= 0:
        blocking.append("zero_valid_metric_events")
    for modality in sorted(expected - observed):
        blocking.append(f"missing_required_modality:{modality}")
    if ground_truth_conflict:
        blocking.append("ground_truth_conflict")
    blocking.extend(
        f"structural_error:{error}" for error in structural_errors if str(error).strip()
    )
    kind_modality = {"metrics": "metric", "logs": "log", "traces": "trace"}
    for report in reports:
        modality = kind_modality.get(report.kind)
        if modality in expected and report.event_count == 0:
            blocking.append(f"required_file_without_valid_events:{report.relative_path}")
        if report.rejected_row_count:
            partial.append(f"rejected_rows:{report.relative_path}")
        if report.rejected_metric_cell_count:
            partial.append(f"rejected_metric_cells:{report.relative_path}")
        if report.rows_partially_accepted:
            partial.append(f"partially_accepted_rows:{report.relative_path}")
        if report.excluded_by_window_count:
            partial.append(f"excluded_by_window:{report.relative_path}")
        if report.quality_issue_reasons:
            partial.append(f"quality_issues:{report.relative_path}")
    if blocking:
        status, usable, rationale = "UNUSABLE", False, "; ".join(sorted(set(blocking)))
    elif partial:
        status, usable = "PARTIAL_BUT_USABLE", True
        rationale = "; ".join(sorted(set(partial)))
    else:
        status, usable, rationale = "CLEAN", True, "complete_observation"
    return {
        "integrity_policy_version": INTEGRITY_POLICY_VERSION,
        "integrity_status": status,
        "usable_for_benchmark": usable,
        "rationale": rationale,
        "blocking_reasons": sorted(set(blocking)),
        "partial_reasons": sorted(set(partial)),
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _safe_name(incident_id: str) -> str:
    return incident_id.replace(":", "__").replace("/", "_")


def _rss_bytes() -> tuple[int, int]:
    try:
        import psutil

        current = psutil.Process().memory_info().rss
    except ImportError:
        current = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return int(current), int(max(current, peak))


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _catalog_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    return value


def _schema_file_from_dict(value: Mapping[str, Any]) -> SchemaFileReport:
    payload = dict(value)
    payload["columns"] = tuple(payload["columns"])
    payload["unmapped_fields"] = tuple(payload.get("unmapped_fields", ()))
    payload["missing_normalized_fields"] = tuple(
        payload.get("missing_normalized_fields", ())
    )
    classification = payload["classification"]
    payload["classification"] = SchemaClassification(
        tuple(classification.get("identity_fields", ())),
        tuple(classification.get("interpreted_metric_fields", ())),
        tuple(classification.get("preserved_metadata_fields", ())),
        tuple(classification.get("unmapped_fields", ())),
        tuple(classification.get("rejected_fields", ())),
    )
    return SchemaFileReport(**payload)


class CompressedNormalizedEventCache:
    """Cache Parquet colunar serial e hash-bound, mantido fora do Git."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def target(self, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("cache key deve ser SHA-256.")
        return self.root / f"{key}.parquet"

    def metadata_target(self, key: str) -> Path:
        return self.root / f"{key}.metadata.json"

    @staticmethod
    def metadata(*, key: str, adapter_version: str, configuration_sha256: str,
                 source_files_sha256: Mapping[str, str], event_count: int,
                 events_sha256: str) -> dict[str, Any]:
        return {
            "cache_version": CACHE_VERSION,
            "cache_key": key,
            "adapter": "rcaeval",
            "adapter_version": adapter_version,
            "configuration_sha256": configuration_sha256,
            "source_files_sha256": dict(sorted(source_files_sha256.items())),
            "event_count": event_count,
            "events_sha256": events_sha256,
        }

    def inspect(self, *, key: str, adapter_version: str, configuration_sha256: str,
                source_files_sha256: Mapping[str, str]) -> Mapping[str, Any] | None:
        target = self.target(key)
        metadata = self.metadata_target(key)
        if not target.is_file() or not metadata.is_file():
            return None
        try:
            header = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Metadados do cache Parquet ilegíveis: {metadata}") from exc
        expected = {
            "cache_version": CACHE_VERSION,
            "cache_key": key,
            "adapter": "rcaeval",
            "adapter_version": adapter_version,
            "configuration_sha256": configuration_sha256,
            "source_files_sha256": dict(sorted(source_files_sha256.items())),
        }
        if any(header.get(name) != value for name, value in expected.items()):
            return None
        return header

    def store(self, *, key: str, adapter_version: str, configuration_sha256: str,
              source_files_sha256: Mapping[str, str], events: tuple[UnifiedEvent, ...]) -> Mapping[str, Any]:
        return self.store_payloads(
            key=key, adapter_version=adapter_version,
            configuration_sha256=configuration_sha256,
            source_files_sha256=source_files_sha256,
            payloads=(event.to_dict() for event in events),
        )

    def store_payloads(self, *, key: str, adapter_version: str,
                       configuration_sha256: str,
                       source_files_sha256: Mapping[str, str],
                       payloads: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
        """Persiste um iterável ordenado em batches sem manter o stream em RAM."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        target = self.target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        digest.update(b"[")
        count = 0
        temporary = target.parent / f".{target.name}.{os.getpid()}.tmp"
        writer = None
        arrow_schema = None
        batch = []

        def parquet_row(payload: Mapping[str, Any]) -> dict[str, Any]:
            # JSON escalar por coluna mantém schema estável entre modalidades e
            # remove a repetição dos nomes dos campos presente no JSONL.
            return {name: canonical_json(value) for name, value in payload.items()}

        def flush() -> None:
            nonlocal writer, arrow_schema
            if not batch:
                return
            table = pa.Table.from_pylist(batch, schema=arrow_schema)
            if writer is None:
                arrow_schema = table.schema
                writer = pq.ParquetWriter(
                    temporary, table.schema, compression="zstd", compression_level=9,
                    use_dictionary=True, write_statistics=True,
                )
            writer.write_table(table)
            batch.clear()

        try:
            for payload in payloads:
                line = canonical_json(payload)
                if count:
                    digest.update(b",")
                digest.update(line.encode("utf-8"))
                batch.append(parquet_row(payload))
                count += 1
                if len(batch) >= 10_000:
                    flush()
            flush()
            if writer is None:
                raise ValueError("Cache não aceita stream vazio.")
            writer.close()
            writer = None
            digest.update(b"]")
            header = self.metadata(
                key=key, adapter_version=adapter_version,
                configuration_sha256=configuration_sha256,
                source_files_sha256=source_files_sha256, event_count=count,
                events_sha256=digest.hexdigest(),
            )
            os.replace(temporary, target)
            _atomic_json(self.metadata_target(key), header)
            return header
        finally:
            if writer is not None:
                writer.close()
            if temporary.exists():
                temporary.unlink()

    def load(self, *, key: str, adapter_version: str, configuration_sha256: str,
             source_files_sha256: Mapping[str, str]) -> tuple[UnifiedEvent, ...] | None:
        header = self.inspect(
            key=key, adapter_version=adapter_version,
            configuration_sha256=configuration_sha256,
            source_files_sha256=source_files_sha256,
        )
        if header is None:
            return None
        import pyarrow.parquet as pq

        events = []
        digest = hashlib.sha256()
        digest.update(b"[")
        parquet = pq.ParquetFile(self.target(key))
        index = 0
        for batch in parquet.iter_batches(batch_size=10_000):
            for row in batch.to_pylist():
                payload = {
                    name: json.loads(value)
                    for name, value in row.items()
                }
                canonical = canonical_json(payload)
                if index:
                    digest.update(b",")
                digest.update(canonical.encode("utf-8"))
                events.append(UnifiedEvent.from_dict(payload))
                index += 1
        digest.update(b"]")
        if len(events) != header["event_count"] or digest.hexdigest() != header["events_sha256"]:
            raise RuntimeError("Cache comprimido adulterado.")
        return tuple(events)


class RCAEvalOfficialParquetAdapterV3(RCAEvalAdapter):
    """Bridge do layout plano oficial, com ground truth vindo do catálogo."""

    SOURCE = "rcaeval"
    ADAPTER_VERSION = MATERIALIZER_VERSION

    def __init__(self, root: str | Path, *, cache_dir: str | Path,
                 artifact_dir: str | Path | None = None, strict: bool = False,
                 expected_catalog_cases: int = 735):
        super().__init__(root, cache_dir=None, strict=strict)
        self.compressed_cache = CompressedNormalizedEventCache(cache_dir)
        self.artifact_dir = Path(artifact_dir).resolve() if artifact_dir else None
        self.expected_catalog_cases = expected_catalog_cases
        self._catalog_rows = self._read_catalog()
        self._catalog_index = {str(row["case"]): row for row in self._catalog_rows}
        self._descriptor_index = self._build_descriptors()
        self._global_schema_report: SchemaReport | None = None
        self._global_schema_computations = 0
        self._current_incident_id: str | None = None
        self._current_incident_results = None

    def _read_catalog(self) -> tuple[dict[str, Any], ...]:
        path = self.root / "cases.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"Catálogo oficial ausente: {path}")
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("RCAEval oficial requer pandas e pyarrow.") from exc
        frame = pd.read_parquet(path)
        missing = set(CATALOG_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"cases.parquet sem colunas: {sorted(missing)}")
        rows = tuple(
            {name: _catalog_value(value) for name, value in row.items()}
            for row in frame.loc[:, CATALOG_COLUMNS].to_dict("records")
        )
        cases = [str(row["case"]) for row in rows]
        if len(rows) != self.expected_catalog_cases:
            raise ValueError(
                f"Catálogo requer {self.expected_catalog_cases} casos; observado={len(rows)}."
            )
        if len(cases) != len(set(cases)):
            raise ValueError("cases.parquet contém case duplicado.")
        if {str(row["system"]).lower() for row in rows} != set(SYSTEM_NAMES):
            raise ValueError("Catálogo deve conter exatamente ob, ss e tt.")
        return rows

    def _build_descriptors(self) -> dict[str, IncidentDescriptor]:
        result = {}
        for row in self._catalog_rows:
            case = str(row["case"])
            system = SYSTEM_NAMES[str(row["system"]).lower()]
            descriptor = IncidentDescriptor(
                f"rcaeval:{case}", self.SOURCE, system, case,
                str(self.root / case), str(row["fault"]),
                str(row["root_cause_service"]),
            )
            result[descriptor.incident_id] = descriptor
        return result

    def list_incidents(self) -> tuple[IncidentDescriptor, ...]:
        return tuple(self._descriptor_index[key] for key in sorted(self._descriptor_index))

    def _descriptor(self, incident_id: str) -> IncidentDescriptor:
        try:
            return self._descriptor_index[incident_id]
        except KeyError as exc:
            raise KeyError(f"Incidente RCAEval oficial desconhecido: {incident_id}") from exc

    def catalog_row(self, descriptor_or_id: IncidentDescriptor | str) -> Mapping[str, Any]:
        descriptor = (descriptor_or_id if isinstance(descriptor_or_id, IncidentDescriptor)
                      else self._descriptor(descriptor_or_id))
        return self._catalog_index[descriptor.case]

    def _catalog_row_sha256(self, descriptor: IncidentDescriptor) -> str:
        return sha256_value(self.catalog_row(descriptor))

    def _modality_policy(self, descriptor):
        row = self.catalog_row(descriptor)
        expected = list(SUITE_MODALITIES[(str(row["suite"]), descriptor.system)])
        # Ausências explicitamente declaradas pelo catálogo são capabilities reais.
        if not bool(row["has_logs"]) and "log" in expected:
            expected.remove("log")
        if not bool(row["has_traces"]) and "trace" in expected:
            expected.remove("trace")
        return tuple(expected), True

    def _configuration(self, descriptor):
        base = super()._configuration(descriptor)
        row = self.catalog_row(descriptor)
        return {
            **base,
            "adapter_version": self.ADAPTER_VERSION,
            "distribution": "huggingface-official-parquet",
            "catalog_row_sha256": self._catalog_row_sha256(descriptor),
            "suite": row["suite"],
            "canonical_system": descriptor.system,
        }

    def _source_hashes(self, descriptor):
        hashes = super()._source_hashes(descriptor)
        root_cause = Path(descriptor.path) / "root_cause.txt"
        if root_cause.is_file():
            hashes[str(root_cause.relative_to(self.root))] = sha256_file(root_cause)
        return dict(sorted(hashes.items()))

    def _incident_results(self, descriptor):
        if (self._current_incident_id == descriptor.incident_id
                and self._current_incident_results is not None):
            return self._current_incident_results
        return self._parse_incident_results(descriptor)

    def _parse_incident_results(self, descriptor: IncidentDescriptor):
        """Preserva os atributos neutros suite-system do normalizador congelado.

        ``IncidentManifest.source_system`` continua canônico (OB/SS/TT), enquanto
        ``rcaeval.system`` dentro do evento permanece RE1-OB/RE2-OB/etc. Isso torna
        o stream semanticamente idêntico ao layout legado e mantém suite explícita.
        """
        row = self.catalog_row(descriptor)
        normalization_descriptor = IncidentDescriptor(
            descriptor.incident_id, descriptor.source_dataset,
            f"{row['suite']}-{descriptor.system}", descriptor.case,
            descriptor.path, descriptor.fault_type, descriptor.fault_service,
        )
        return RCAEvalAdapter._incident_results(self, normalization_descriptor)

    def get_ground_truth(self, incident_id: str) -> dict[str, Any]:
        descriptor = self._descriptor(incident_id)
        row = self.catalog_row(descriptor)
        return {
            "incident_id": descriptor.incident_id,
            "system": descriptor.system,
            "case": descriptor.case,
            "suite": row["suite"],
            "fault_type": row["fault"],
            "fault_service": row["root_cause_service"],
            "source": "cases.parquet",
        }

    def _root_cause_conflict(self, descriptor: IncidentDescriptor) -> str | None:
        path = Path(descriptor.path) / "root_cause.txt"
        if not path.is_file():
            return None
        try:
            row = next(csv.reader([path.read_text(encoding="utf-8").strip()]))
            observed = row[2].strip()
        except (OSError, csv.Error, IndexError) as exc:
            return f"GROUND_TRUTH_CONFLICT: root_cause.txt ilegível: {exc}"
        expected = str(self.catalog_row(descriptor)["root_cause_service"]).strip()
        if observed != expected:
            return f"GROUND_TRUTH_CONFLICT: catalog={expected};root_cause.txt={observed}"
        return None

    def normalize_events(self, incident_id: str) -> tuple[UnifiedEvent, ...]:
        descriptor = self._descriptor(incident_id)
        key, config_sha, hashes = self._cache_identity(descriptor)
        cached = self.compressed_cache.load(
            key=key, adapter_version=self.ADAPTER_VERSION,
            configuration_sha256=config_sha, source_files_sha256=hashes,
        )
        if cached is not None:
            return cached
        results = self._incident_results(descriptor)
        self._enforce_incident_results(descriptor, results, self.strict)
        events = tuple(sorted(
            (event for result in results for event in result.events),
            key=lambda item: (item.event_timestamp, item.event_id),
        ))
        self.compressed_cache.store(
            key=key, adapter_version=self.ADAPTER_VERSION,
            configuration_sha256=config_sha, source_files_sha256=hashes,
            events=events,
        )
        return events

    def _schema(self) -> SchemaReport:
        if self._global_schema_report is None:
            raise RuntimeError("Schema global ainda não foi pré-computado.")
        return self._global_schema_report

    def set_global_schema(self, report: SchemaReport) -> None:
        if self._global_schema_report is not None:
            if self._global_schema_report.fingerprint != report.fingerprint:
                raise ValueError("Tentativa de substituir schema global divergente.")
            return
        self._global_schema_report = report
        self._global_schema_computations += 1

    @property
    def global_schema_computations(self) -> int:
        return self._global_schema_computations

    def load_incident(self, incident_id: str) -> IncidentManifest:
        if self.artifact_dir is not None:
            path = self.artifact_dir / "manifests" / f"{_safe_name(incident_id)}.json"
            if path.is_file():
                return IncidentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
        raise RuntimeError("Manifest não materializado; execute o materializador primeiro.")

    def validate(self) -> ValidationReport:
        errors = []
        indexed = {str(row["case"]) for row in self._catalog_rows}
        directories = {
            path.name for path in self.root.iterdir()
            if path.is_dir() and path.name != ".cache"
        }
        if indexed - directories:
            errors.append(f"Diretórios ausentes: {sorted(indexed - directories)}")
        if directories - indexed:
            errors.append(f"Diretórios desconhecidos: {sorted(directories - indexed)}")
        return ValidationReport(not errors, tuple(errors), (), self._global_schema_report)

    def clear_current(self) -> None:
        self._current_incident_id = None
        self._current_incident_results = None
        gc.collect()


@dataclass
class MaterializationStats:
    processed: int = 0
    valid: int = 0
    usable: int = 0
    unusable: int = 0
    errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    heavy_recomputations: int = 0
    peak_rss: int = 0
    max_worker_peak_rss: int = 0


def _normalized_worker(*, root: Path, descriptor: IncidentDescriptor, kind: str,
                       path: Path, events_path: Path, metadata_path: Path) -> None:
    """Worker serial de uma modalidade; o processo termina e devolve toda a RAM."""
    started = time.monotonic()
    source_bytes = path.stat().st_size
    adapter = RCAEvalAdapter(root, strict=False)
    normalize_started = time.monotonic()
    result = adapter._normalize_file(descriptor, kind, path)
    normalize_seconds = time.monotonic() - normalize_started
    sort_started = time.monotonic()
    ordered = sorted(result.events, key=lambda item: (item.event_timestamp, item.event_id))
    sort_seconds = time.monotonic() - sort_started
    events_path.parent.mkdir(parents=True, exist_ok=True)
    write_started = time.monotonic()
    with gzip.GzipFile(filename=str(events_path), mode="wb", compresslevel=6, mtime=0) as stream:
        for event in ordered:
            stream.write((canonical_json(event.to_dict()) + "\n").encode("utf-8"))
    intermediate_write_seconds = time.monotonic() - write_started
    worker_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    _atomic_json(metadata_path, {
        "report": result.report(root).to_dict(),
        "event_count": len(ordered),
        "worker_peak_rss_bytes": int(worker_peak),
        "worker_elapsed_seconds": time.monotonic() - started,
        "source_bytes": source_bytes,
        "phase_timings_seconds": {
            "normalize": normalize_seconds,
            "sort": sort_seconds,
            "gzip_intermediate_write": intermediate_write_seconds,
        },
    })


def _merge_sorted_event_files(paths: Iterable[Path]):
    streams = [gzip.open(path, "rt", encoding="utf-8") for path in paths]
    heap = []
    try:
        for index, stream in enumerate(streams):
            line = stream.readline()
            if line:
                payload = json.loads(line)
                heapq.heappush(heap, (
                    payload["event_timestamp"], payload["event_id"], index, payload,
                ))
        while heap:
            _, _, index, payload = heapq.heappop(heap)
            yield payload
            line = streams[index].readline()
            if line:
                following = json.loads(line)
                heapq.heappush(heap, (
                    following["event_timestamp"], following["event_id"], index, following,
                ))
    finally:
        for stream in streams:
            stream.close()


class RCAEvalOfficialMaterializerV3:
    def __init__(self, adapter: RCAEvalOfficialParquetAdapterV3, output: str | Path):
        self.adapter = adapter
        self.output = Path(output).expanduser().resolve()
        self.checkpoints = self.output / "checkpoints"
        self.stats = MaterializationStats()

    def _checkpoint_path(self, descriptor: IncidentDescriptor) -> Path:
        return self.checkpoints / f"{_safe_name(descriptor.incident_id)}.json"

    def _identity(self, descriptor: IncidentDescriptor) -> dict[str, Any]:
        key, config_sha, hashes = self.adapter._cache_identity(descriptor)
        return {
            "case": descriptor.case,
            "incident_id": descriptor.incident_id,
            "adapter_version": self.adapter.ADAPTER_VERSION,
            "source_files_sha256": hashes,
            "configuration_sha256": config_sha,
            "cache_key": key,
            "catalog_row_sha256": self.adapter._catalog_row_sha256(descriptor),
        }

    def _load_checkpoint(self, descriptor: IncidentDescriptor) -> Mapping[str, Any] | None:
        path = self._checkpoint_path(descriptor)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("identity") != self._identity(descriptor):
            return None
        identity = payload["identity"]
        cached = self.adapter.compressed_cache.inspect(
            key=identity["cache_key"], adapter_version=self.adapter.ADAPTER_VERSION,
            configuration_sha256=identity["configuration_sha256"],
            source_files_sha256=identity["source_files_sha256"],
        )
        if cached is None or cached.get("events_sha256") != payload.get("normalized_events_sha256"):
            return None
        return self._apply_integrity_policy(payload)

    @staticmethod
    def _apply_integrity_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
        updated = dict(payload)
        legacy = payload.get("legacy_usable_for_benchmark")
        if legacy is None:
            legacy = bool(payload.get("usable_for_benchmark"))
        updated["legacy_usable_for_benchmark"] = bool(legacy)
        reports = tuple(_schema_file_from_dict(item) for item in payload["schema_files"])
        integrity = classify_incident_integrity(
            reports=reports,
            expected_modalities=payload["expected_modalities"],
            observed_modalities=payload["observed_modalities"],
            total_event_count=payload["total_event_count"],
            event_count_by_modality=payload["event_count_by_modality"],
            ground_truth_conflict=payload.get("ground_truth_conflict"),
        )
        updated.update(integrity)
        return updated

    def _process_case(self, descriptor: IncidentDescriptor, *, resume: bool) -> Mapping[str, Any]:
        if resume:
            checkpoint = self._load_checkpoint(descriptor)
            if checkpoint is not None:
                _atomic_json(self._checkpoint_path(descriptor), checkpoint)
                self.stats.cache_hits += 1
                return checkpoint
        self.stats.cache_misses += 1
        self.stats.heavy_recomputations += 1
        started = time.monotonic()
        identity = self._identity(descriptor)
        self.adapter.compressed_cache.root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(
            prefix=f"rcaeval-{_safe_name(descriptor.incident_id)}-",
            dir=self.adapter.compressed_cache.root,
        ))
        try:
            row = self.adapter.catalog_row(descriptor)
            normalized_system = f"{row['suite']}-{descriptor.system}"
            event_files = []
            reports = []
            worker_resources = []
            for kind, path in self.adapter._files(descriptor).items():
                if kind == "inject_time":
                    continue
                events_path = work / f"{kind}.events.jsonl.gz"
                metadata_path = work / f"{kind}.metadata.json"
                command = [
                    sys.executable, "-m", "experiments.rcaeval_official_v3",
                    "normalize-file", "--root", str(self.adapter.root),
                    "--incident-id", descriptor.incident_id,
                    "--case", descriptor.case, "--system", normalized_system,
                    "--fault", descriptor.fault_type or "unspecified",
                    "--fault-service", descriptor.fault_service or "unspecified",
                    "--kind", kind, "--path", str(path),
                    "--events-output", str(events_path),
                    "--metadata-output", str(metadata_path),
                ]
                subprocess.run(command, check=True)
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                reports.append(_schema_file_from_dict(metadata["report"]))
                worker_resources.append({
                    "kind": kind,
                    "worker_peak_rss_bytes": metadata["worker_peak_rss_bytes"],
                    "worker_elapsed_seconds": metadata["worker_elapsed_seconds"],
                    "source_bytes": metadata["source_bytes"],
                    "event_count": metadata["event_count"],
                    "phase_timings_seconds": metadata["phase_timings_seconds"],
                })
                self.stats.max_worker_peak_rss = max(
                    self.stats.max_worker_peak_rss,
                    metadata["worker_peak_rss_bytes"],
                )
                event_files.append(events_path)

            counts = Counter()
            first_timestamp = None
            last_timestamp = None
            outside = 0
            requested = self.adapter.get_analysis_window(descriptor.incident_id)
            requested_dict = requested.to_dict()

            def observed_payloads():
                nonlocal first_timestamp, last_timestamp, outside
                for event in _merge_sorted_event_files(event_files):
                    signal = str(event["signal_type"])
                    counts[signal] += 1
                    timestamp = event["event_timestamp"]
                    first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
                    last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)
                    outside += int(not (
                        requested_dict["start"] <= timestamp <= requested_dict["end"]
                    ))
                    yield event

            cache_write_started = time.monotonic()
            cache_header = self.adapter.compressed_cache.store_payloads(
                key=identity["cache_key"], adapter_version=self.adapter.ADAPTER_VERSION,
                configuration_sha256=identity["configuration_sha256"],
                source_files_sha256=identity["source_files_sha256"],
                payloads=observed_payloads(),
            )
            merge_and_parquet_seconds = time.monotonic() - cache_write_started
            expected, _ = self.adapter._modality_policy(descriptor)
            observed_modalities = tuple(sorted(counts))
            missing = tuple(sorted(set(expected) - set(observed_modalities)))
            unexpected = tuple(sorted(set(observed_modalities) - set(expected)))
            conflict = self.adapter._root_cause_conflict(descriptor)
            issues = []
            if missing:
                issues.extend(f"{descriptor.incident_id}: modalidade esperada ausente: {item}"
                              for item in missing)
            if unexpected:
                issues.extend(f"{descriptor.incident_id}: modalidade extra preservada: {item}"
                              for item in unexpected)
            for report in reports:
                if report.event_count == 0:
                    issues.append(f"{descriptor.incident_id}:{Path(report.relative_path).name}: zero eventos válidos")
                if report.rejected_row_count or report.rejection_reasons:
                    issues.append(f"{descriptor.incident_id}:{Path(report.relative_path).name}: rejeições detectadas")
            integrity = classify_incident_integrity(
                reports=reports, expected_modalities=expected,
                observed_modalities=observed_modalities,
                total_event_count=cache_header["event_count"],
                event_count_by_modality=counts, ground_truth_conflict=conflict,
            )
            observed = ({"start": first_timestamp, "end": last_timestamp}
                        if first_timestamp is not None else None)
            payload = {
                "checkpoint_version": MATERIALIZER_VERSION,
                "identity": identity,
                "catalog": dict(self.adapter.catalog_row(descriptor)),
                "schema_files": [report.to_dict() for report in reports],
                "normalized_events_sha256": cache_header["events_sha256"],
                "event_count_by_modality": dict(sorted(counts.items())),
                "total_event_count": cache_header["event_count"],
                "analysis_window": requested.to_dict(),
                "observed_interval": observed,
                "outside_window_event_count": outside,
                "expected_modalities": list(expected),
                "observed_modalities": list(observed_modalities),
                "missing_expected_modalities": list(missing),
                "unexpected_modalities": list(unexpected),
                "issues": list(issues),
                "ground_truth_conflict": conflict,
                **integrity,
                "worker_resources": worker_resources,
                "phase_timings_seconds": {
                    "merge_and_parquet_cache_write": merge_and_parquet_seconds,
                },
                "elapsed_seconds": time.monotonic() - started,
            }
            _atomic_json(self._checkpoint_path(descriptor), payload)
            return payload
        finally:
            self.adapter.clear_current()
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _window(value: Mapping[str, str] | None) -> AnalysisWindow | None:
        if value is None:
            return None
        def parse(text: str) -> datetime:
            return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        return AnalysisWindow(parse(value["start"]), parse(value["end"]))

    def _manifest(self, descriptor: IncidentDescriptor, checkpoint: Mapping[str, Any],
                  global_schema: SchemaReport) -> IncidentManifest:
        reports = tuple(_schema_file_from_dict(item) for item in checkpoint["schema_files"])
        incident_schema = SchemaReport(
            self.adapter.SOURCE, reports, datetime.now(timezone.utc),
            "incident_normalization_report",
        )
        counts = checkpoint["event_count_by_modality"]
        expected = tuple(checkpoint["expected_modalities"])
        observed = tuple(checkpoint["observed_modalities"])
        conflict = checkpoint.get("ground_truth_conflict")
        reasons = {report.relative_path: dict(report.rejection_reasons) for report in reports}
        quality = {report.relative_path: dict(report.quality_issue_reasons) for report in reports}
        normalized_files = tuple(report.relative_path for report in reports)
        accounting = {}
        for report in reports:
            signal = {"metrics": "metric", "logs": "log", "traces": "trace"}[report.kind]
            accounting[report.relative_path] = NormalizedFileAccounting(
                report.relative_path, report.kind, signal, report.row_count,
                report.accepted_row_count, report.rejected_row_count,
                report.rows_partially_accepted, report.excluded_by_window_count,
                report.event_count,
                report.accepted_metric_cell_count if signal == "metric" else 0,
                report.rejected_metric_cell_count if signal == "metric" else 0,
                report.rejected_row_count,
                cell_rejection_count(report.rejection_reasons) if signal == "metric" else 0,
            )
        policy_usable = bool(checkpoint["usable_for_benchmark"])
        integrity_status = checkpoint["integrity_status"]
        issues = list(checkpoint.get("issues", ()))
        if conflict:
            issues.append(conflict)
        if not policy_usable and checkpoint.get("rationale"):
            issues.append(str(checkpoint["rationale"]))
        return IncidentManifest(
            descriptor.incident_id, self.adapter.SOURCE, descriptor.system, descriptor.case,
            descriptor.fault_type, descriptor.fault_service,
            self._window(checkpoint["analysis_window"]), tuple(sorted(counts)),
            checkpoint["identity"]["source_files_sha256"], counts,
            {report.relative_path: report.unmapped_fields for report in reports if report.unmapped_fields},
            self.adapter.ADAPTER_VERSION, checkpoint["identity"]["configuration_sha256"],
            checkpoint["identity"]["cache_key"], checkpoint["normalized_events_sha256"],
            sha256_value(self.adapter.get_ground_truth(descriptor.incident_id)),
            incident_schema.fingerprint, self.adapter._window_policy(descriptor),
            self._window(checkpoint["observed_interval"]), checkpoint["outside_window_event_count"],
            "raw-source-events-included", sum(report.accepted_row_count for report in reports),
            sum(report.rejected_row_count for report in reports), checkpoint["total_event_count"],
            considered_row_count=sum(report.row_count for report in reports),
            excluded_by_window_count=sum(report.excluded_by_window_count for report in reports),
            rejection_reasons_by_file=reasons,
            dataset_schema_report_sha256=global_schema.fingerprint,
            incident_normalization_report_sha256=incident_schema.fingerprint,
            normalization_mode="strict" if integrity_status == "CLEAN" else "permissive",
            empty_incident_reason=(
                None if policy_usable else "; ".join(dict.fromkeys(issues)) or "unusable"
            ),
            usable_for_benchmark=policy_usable,
            quality_issue_reasons_by_file=quality,
            modality_policy_version=f"{RCAEVAL_MODALITY_POLICY_VERSION}+catalog-capability-v1",
            expected_modalities=expected, observed_modalities=observed,
            missing_expected_modalities=tuple(checkpoint["missing_expected_modalities"]),
            unexpected_modalities=tuple(checkpoint["unexpected_modalities"]),
            normalized_source_files=normalized_files, modality_policy_status="official",
            normalization_accounting_by_file=accounting,
        )

    def materialize(self, descriptors: Iterable[IncidentDescriptor], *, resume: bool,
                    mode: str = "full") -> Mapping[str, Any]:
        descriptors = tuple(descriptors)
        if not descriptors:
            raise ValueError("Nenhum caso selecionado.")
        self.output.mkdir(parents=True, exist_ok=True)
        free_before = _disk_free(self.output)
        started = time.monotonic()
        checkpoints = []
        total = len(descriptors)
        for index, descriptor in enumerate(descriptors, 1):
            checkpoint = self._process_case(descriptor, resume=resume)
            checkpoints.append((descriptor, checkpoint))
            self.stats.processed += 1
            self.stats.valid += 1
            self.stats.usable += int(checkpoint["usable_for_benchmark"])
            self.stats.unusable += int(not checkpoint["usable_for_benchmark"])
            current, peak = _rss_bytes()
            self.stats.peak_rss = max(self.stats.peak_rss, peak)
            elapsed = time.monotonic() - started
            percent = index * 100 / total
            eta = elapsed / index * (total - index)
            row = self.adapter.catalog_row(descriptor)
            print(
                f"[{index:03d}/{total:03d}] {row['suite']} {descriptor.system} {descriptor.case} "
                f"modalities={','.join(checkpoint['observed_modalities'])} "
                f"events={checkpoint['total_event_count']} usable={str(checkpoint['usable_for_benchmark']).lower()} "
                f"elapsed={elapsed:.1f}s percent={percent:.1f} eta={eta:.1f}s "
                f"rss={current} peak_rss={self.stats.peak_rss} "
                f"cache_hits={self.stats.cache_hits} cache_misses={self.stats.cache_misses}",
                flush=True,
            )
            # Margem operacional fixa, não critério científico.
            if _disk_free(self.output) < 2 * 1024 ** 3:
                raise RuntimeError("RESOURCE_DISK_GATE=FAIL: menos de 2 GiB livres.")

        schema_files = tuple(
            _schema_file_from_dict(item)
            for _, checkpoint in checkpoints for item in checkpoint["schema_files"]
        )
        global_schema = SchemaReport(
            self.adapter.SOURCE, schema_files, datetime.now(timezone.utc),
            "dataset_schema_report",
        )
        self.adapter.set_global_schema(global_schema)
        _atomic_json(self.output / "dataset-schema-report.json", global_schema.to_dict())

        manifests = []
        index_rows = []
        capabilities = []
        manifest_dir = self.output / "manifests"
        for descriptor, checkpoint in checkpoints:
            manifest = self._manifest(descriptor, checkpoint, global_schema)
            path = manifest_dir / f"{_safe_name(descriptor.incident_id)}.json"
            _atomic_json(path, manifest.to_dict())
            manifests.append(manifest)
            capability = DatasetCapabilityV3.from_incident_manifest(manifest)
            if not (checkpoint["usable_for_benchmark"]
                    == manifest.usable_for_benchmark
                    == capability.usable_for_benchmark):
                raise RuntimeError("Usabilidade divergiu entre checkpoint, manifest e capability.")
            capabilities.append(capability)
            row = self.adapter.catalog_row(descriptor)
            index_rows.append({
                "incident_id": descriptor.incident_id, "case": descriptor.case,
                "suite": row["suite"], "system": descriptor.system,
                "manifest_path": str(path.relative_to(self.output)),
                "manifest_sha256": manifest.fingerprint,
                "usable_for_benchmark": manifest.usable_for_benchmark,
                "integrity_policy_version": checkpoint["integrity_policy_version"],
                "integrity_status": checkpoint["integrity_status"],
                "modalities": list(manifest.modalities), "fault": descriptor.fault_type,
                "root_cause_service": descriptor.fault_service,
                "cache_identity": checkpoint["identity"]["cache_key"],
            })
        _atomic_json(self.output / "INDEX.json", {
            "version": MATERIALIZER_VERSION, "count": len(index_rows), "incidents": index_rows,
        })
        _atomic_json(self.output / "capabilities.json", {
            "version": MATERIALIZER_VERSION, "count": len(capabilities),
            "capabilities": [item.to_dict() for item in capabilities],
        })
        source = self._source_payload(len(descriptors))
        _atomic_json(self.output / "SOURCE.json", source)
        report = {
            "version": MATERIALIZER_VERSION, "mode": mode, "planned": total,
            "processed": self.stats.processed, "valid": self.stats.valid,
            "usable_for_benchmark": self.stats.usable, "unusable": self.stats.unusable,
            "manifest_usable_for_benchmark": sum(
                int(manifest.usable_for_benchmark) for manifest in manifests
            ),
            "errors": self.stats.errors, "cache_hits": self.stats.cache_hits,
            "cache_misses": self.stats.cache_misses,
            "heavy_recomputations": self.stats.heavy_recomputations,
            "global_schema_computations": self.adapter.global_schema_computations,
            "global_schema_sha256": global_schema.fingerprint,
            "peak_rss": self.stats.peak_rss,
            "coordinator_peak_rss_bytes": self.stats.peak_rss,
            "max_worker_peak_rss_bytes": self.stats.max_worker_peak_rss,
            "elapsed_seconds": time.monotonic() - started,
            "free_disk_before": free_before, "free_disk_after": _disk_free(self.output),
        }
        _atomic_json(self.output / "materialization-report.json", report)
        write_sha256s(self.output, self.output / "SHA256SUMS", exclude={"SHA256SUMS"})
        return report

    def _source_payload(self, cases: int) -> dict[str, Any]:
        rows = tuple(self.adapter._catalog_rows)
        suite_counts = dict(Counter(str(row["suite"]) for row in rows))
        system_counts = dict(Counter(SYSTEM_NAMES[str(row["system"])] for row in rows))
        return {
            "dataset": "RCAEval", "source_dataset": "rcaeval",
            "distribution": "HuggingFace official parquet",
            "local_root": (str(self.adapter.root.relative_to(Path.cwd()))
                           if self.adapter.root.is_relative_to(Path.cwd())
                           else str(self.adapter.root)),
            "cases": cases, "catalog_cases": len(rows),
            "suite_counts": suite_counts, "system_counts": system_counts,
            "download_size_bytes": sum(
                path.stat().st_size for path in self.adapter.root.rglob("*") if path.is_file()
            ),
            "cases_parquet_sha256": sha256_file(self.adapter.root / "cases.parquet"),
            "materialized_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "materializer_version": MATERIALIZER_VERSION,
            "project_provenance": "docs/datasets/public-dataset-environment-v3.json",
        }


def write_sha256s(root: Path, target: Path, *, exclude: set[str] | None = None) -> int:
    exclude = set(exclude or ())
    files = tuple(sorted(
        path for path in root.rglob("*") if path.is_file()
        and str(path.relative_to(root)) not in exclude
        and ".cache" not in path.relative_to(root).parts
    ))
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(files)


def validate_materialization(adapter: RCAEvalOfficialParquetAdapterV3,
                             output: Path) -> Mapping[str, Any]:
    required = (
        "SOURCE.json", "INDEX.json", "capabilities.json",
        "dataset-schema-report.json", "materialization-report.json", "SHA256SUMS",
    )
    errors = [f"missing:{name}" for name in required if not (output / name).is_file()]
    if errors:
        return {"valid": False, "errors": errors}
    index = json.loads((output / "INDEX.json").read_text(encoding="utf-8"))
    capabilities = json.loads((output / "capabilities.json").read_text(encoding="utf-8"))
    if index["count"] != len(index["incidents"]):
        errors.append("index_count")
    if capabilities["count"] != len(capabilities["capabilities"]):
        errors.append("capability_count")
    for item in index["incidents"]:
        path = output / item["manifest_path"]
        try:
            manifest = IncidentManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            errors.append(f"manifest:{item['incident_id']}:{exc}")
            continue
        if manifest.fingerprint != item["manifest_sha256"]:
            errors.append(f"manifest_hash:{item['incident_id']}")
    declared = {}
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        declared[relative] = digest
    for relative, digest in declared.items():
        path = output / relative
        if not path.is_file() or sha256_file(path) != digest:
            errors.append(f"artifact_checksum:{relative}")
    return {"valid": not errors, "errors": errors, "incidents": index["count"]}


def reclassify_checkpoints(adapter: RCAEvalOfficialParquetAdapterV3,
                           output: Path) -> Mapping[str, Any]:
    """Reclassifica somente metadata; não abre Parquet RAW nem event streams."""
    checkpoint_paths = tuple(sorted((output / "checkpoints").glob("*.json")))
    before_usable = before_unusable = after_usable = after_unusable = reclassified = 0
    audit_incidents = []
    classified_records = []
    updated_checkpoints = []
    reason_summary: dict[str, dict[str, int]] = {}
    for path in checkpoint_paths:
        original = json.loads(path.read_text(encoding="utf-8"))
        identity = original["identity"]
        cached = adapter.compressed_cache.inspect(
            key=identity["cache_key"], adapter_version=adapter.ADAPTER_VERSION,
            configuration_sha256=identity["configuration_sha256"],
            source_files_sha256=identity["source_files_sha256"],
        )
        if cached is None or cached.get("events_sha256") != original["normalized_events_sha256"]:
            continue
        previous = bool(original.get(
            "legacy_usable_for_benchmark",
            original.get("usable_for_benchmark"),
        ))
        before_usable += int(previous)
        before_unusable += int(not previous)
        updated = RCAEvalOfficialMaterializerV3._apply_integrity_policy(original)
        current = bool(updated["usable_for_benchmark"])
        after_usable += int(current)
        after_unusable += int(not current)
        reclassified += int(
            previous != current
            or original.get("integrity_policy_version") != INTEGRITY_POLICY_VERSION
        )
        _atomic_json(path, updated)
        updated_checkpoints.append(updated)
        classified_records.append({
            "incident_id": identity["incident_id"],
            "previous_usable": previous,
            "integrity_status": updated["integrity_status"],
            "usable_for_benchmark": current,
            "rationale": updated["rationale"],
            "normalized_events_sha256": updated["normalized_events_sha256"],
        })
        if updated["integrity_status"] == "PARTIAL_BUT_USABLE":
            files = []
            for raw_report in updated["schema_files"]:
                report = _schema_file_from_dict(raw_report)
                files.append({
                    "file": report.relative_path, "kind": report.kind,
                    "row_count": report.row_count,
                    "accepted_row_count": report.accepted_row_count,
                    "rejected_row_count": report.rejected_row_count,
                    "rows_partially_accepted": report.rows_partially_accepted,
                    "accepted_metric_cell_count": report.accepted_metric_cell_count,
                    "rejected_metric_cell_count": report.rejected_metric_cell_count,
                    "event_count": report.event_count,
                    "rejection_reasons": dict(report.rejection_reasons),
                    "quality_issue_reasons": dict(report.quality_issue_reasons),
                })
                for reason, count in report.rejection_reasons.items():
                    summary = reason_summary.setdefault(reason, {
                        "incidents_affected": 0, "rows_affected": 0,
                        "cells_affected": 0, "events_preserved": 0,
                    })
                    summary["incidents_affected"] += 1
                    cells = count if reason.startswith("invalid_metric_cell:") else 0
                    summary["cells_affected"] += cells
                    summary["rows_affected"] += 0 if cells else count
                    summary["events_preserved"] += report.event_count
            catalog = updated.get("catalog", {})
            audit_incidents.append({
                "incident_id": identity["incident_id"], "suite": catalog.get("suite"),
                "system": SYSTEM_NAMES.get(str(catalog.get("system", "")).lower(),
                                           catalog.get("system")),
                "files": files,
                "expected_modalities": updated["expected_modalities"],
                "observed_modalities": updated["observed_modalities"],
                "current_usable": previous,
                "proposed_integrity_status": updated["integrity_status"],
                "proposed_usable": current, "rationale": updated["rationale"],
            })
    audit = {
        "integrity_policy_version": INTEGRITY_POLICY_VERSION,
        "incidents": audit_incidents, "reason_summary": reason_summary,
        "source": "checkpoint_metadata_only",
    }
    rejection_type_summary: dict[str, dict[str, Any]] = {}
    for incident in audit_incidents:
        for report in incident["files"]:
            categories = []
            if report["rejected_metric_cell_count"]:
                categories.append((
                    "invalid_metric_cells", 0,
                    report["rejected_metric_cell_count"],
                ))
            rejected_rows = sum(
                count for reason, count in report["rejection_reasons"].items()
                if not reason.startswith("invalid_metric_cell:")
            )
            if rejected_rows:
                categories.append(("invalid_rows", rejected_rows, 0))
            if report["quality_issue_reasons"]:
                categories.append((
                    "quality_issues",
                    sum(report["quality_issue_reasons"].values()), 0,
                ))
            for category, rows, cells in categories:
                summary = rejection_type_summary.setdefault(category, {
                    "incident_ids": [], "rows_affected": 0,
                    "cells_affected": 0, "events_preserved": 0,
                    "_event_sources": [],
                })
                if incident["incident_id"] not in summary["incident_ids"]:
                    summary["incident_ids"].append(incident["incident_id"])
                summary["rows_affected"] += rows
                summary["cells_affected"] += cells
                event_source = [incident["incident_id"], report["file"]]
                if event_source not in summary["_event_sources"]:
                    summary["_event_sources"].append(event_source)
                    summary["events_preserved"] += report["event_count"]
    for summary in rejection_type_summary.values():
        summary["incidents_affected"] = len(summary.pop("incident_ids"))
        summary.pop("_event_sources")
    audit["rejection_type_summary"] = rejection_type_summary
    _atomic_json(output / "partial-rejection-audit.json", audit)

    schema_files = tuple(
        _schema_file_from_dict(item)
        for checkpoint in updated_checkpoints for item in checkpoint["schema_files"]
    )
    global_schema = SchemaReport(
        adapter.SOURCE, schema_files, datetime.now(timezone.utc),
        "dataset_schema_report",
    )
    adapter.set_global_schema(global_schema)
    _atomic_json(output / "dataset-schema-report.json", global_schema.to_dict())
    materializer = RCAEvalOfficialMaterializerV3(adapter, output)
    old_manifests = {}
    for path in sorted((output / "manifests").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            old_manifests[payload["incident_id"]] = payload
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    manifests = []
    capabilities = []
    index_rows = []
    alignment_rows = []
    fingerprint_rows = []
    for checkpoint in sorted(
            updated_checkpoints,
            key=lambda item: item["identity"]["incident_id"]):
        incident_id = checkpoint["identity"]["incident_id"]
        descriptor = adapter._descriptor(incident_id)
        manifest = materializer._manifest(descriptor, checkpoint, global_schema)
        capability = DatasetCapabilityV3.from_incident_manifest(manifest)
        aligned = (
            checkpoint["usable_for_benchmark"]
            == manifest.usable_for_benchmark
            == capability.usable_for_benchmark
        )
        if not aligned:
            raise RuntimeError(f"Usabilidade divergente em {incident_id}.")
        manifest_path = output / "manifests" / f"{_safe_name(incident_id)}.json"
        _atomic_json(manifest_path, manifest.to_dict())
        manifests.append(manifest)
        capabilities.append(capability)
        row = adapter.catalog_row(descriptor)
        index_rows.append({
            "incident_id": incident_id, "case": descriptor.case,
            "suite": row["suite"], "system": descriptor.system,
            "manifest_path": str(manifest_path.relative_to(output)),
            "manifest_sha256": manifest.fingerprint,
            "usable_for_benchmark": manifest.usable_for_benchmark,
            "integrity_policy_version": checkpoint["integrity_policy_version"],
            "integrity_status": checkpoint["integrity_status"],
            "modalities": list(manifest.modalities), "fault": descriptor.fault_type,
            "root_cause_service": descriptor.fault_service,
            "cache_identity": checkpoint["identity"]["cache_key"],
        })
        alignment_rows.append({
            "incident_id": incident_id,
            "integrity_status": checkpoint["integrity_status"],
            "checkpoint_usable": checkpoint["usable_for_benchmark"],
            "manifest_usable": manifest.usable_for_benchmark,
            "capability_usable": capability.usable_for_benchmark,
            "planner_eligible_if_other_requirements_met": capability.usable_for_benchmark,
            "aligned": aligned,
        })
        old = old_manifests.get(incident_id)
        new = manifest.to_dict()
        fingerprint_rows.append({
            "incident_id": incident_id,
            "previous_manifest_present": old is not None,
            "manifest_fingerprint_changed": (
                old is not None and old.get("fingerprint") != new["fingerprint"]
            ),
            "normalized_events_sha256_changed": (
                old is not None
                and old.get("normalized_events_sha256") != new["normalized_events_sha256"]
            ),
            "source_files_sha256_changed": (
                old is not None
                and old.get("source_files_sha256") != new["source_files_sha256"]
            ),
            "ground_truth_sha256_changed": (
                old is not None
                and old.get("ground_truth_sha256") != new["ground_truth_sha256"]
            ),
            "normalization_accounting_changed": (
                old is not None and old.get("normalization_accounting_by_file")
                != new["normalization_accounting_by_file"]
            ),
            "old_manifest_sha256": old.get("fingerprint") if old else None,
            "new_manifest_sha256": new["fingerprint"],
        })
    _atomic_json(output / "INDEX.json", {
        "version": MATERIALIZER_VERSION, "count": len(index_rows),
        "incidents": index_rows,
    })
    _atomic_json(output / "capabilities.json", {
        "version": MATERIALIZER_VERSION, "count": len(capabilities),
        "capabilities": [item.to_dict() for item in capabilities],
    })
    alignment_audit = {
        "integrity_policy_version": INTEGRITY_POLICY_VERSION,
        "contract_evolution_reason": CONTRACT_EVOLUTION_REASON,
        "checkpoint_count": len(alignment_rows),
        "aligned_count": sum(item["aligned"] for item in alignment_rows),
        "misaligned_count": sum(not item["aligned"] for item in alignment_rows),
        "heavy_recomputations": 0,
        "incidents": alignment_rows,
    }
    _atomic_json(output / "manifest-capability-alignment-audit.json", alignment_audit)
    fingerprint_audit = {
        "contract_evolution_reason": CONTRACT_EVOLUTION_REASON,
        "manifests_previously_present": sum(
            item["previous_manifest_present"] for item in fingerprint_rows
        ),
        "manifest_fingerprints_changed": sum(
            item["manifest_fingerprint_changed"] for item in fingerprint_rows
        ),
        "normalized_event_hashes_changed": sum(
            item["normalized_events_sha256_changed"] for item in fingerprint_rows
        ),
        "source_hashes_changed": sum(
            item["source_files_sha256_changed"] for item in fingerprint_rows
        ),
        "ground_truth_hashes_changed": sum(
            item["ground_truth_sha256_changed"] for item in fingerprint_rows
        ),
        "normalization_accounting_changed": sum(
            item["normalization_accounting_changed"] for item in fingerprint_rows
        ),
        "incidents": fingerprint_rows,
    }
    _atomic_json(output / "manifest-fingerprint-evolution-audit.json", fingerprint_audit)
    cache_entries = []
    for path in adapter.compressed_cache.root.rglob("*"):
        if path.is_file():
            cache_entries.append({
                "path": str(path.relative_to(adapter.compressed_cache.root)),
                "bytes": path.stat().st_size,
            })
    cache_entries.sort(key=lambda item: (-item["bytes"], item["path"]))
    checkpoint_payloads = [
        json.loads(path.read_text(encoding="utf-8")) for path in checkpoint_paths
    ]
    resource_checkpoints = [
        checkpoint for checkpoint in checkpoint_payloads
        if checkpoint.get("worker_resources")
    ]
    source_bytes = sum(
        item.get("source_bytes", 0)
        for checkpoint in resource_checkpoints
        for item in checkpoint["worker_resources"]
    )
    event_count = sum(
        checkpoint.get("total_event_count", 0) for checkpoint in checkpoint_payloads
    )
    resource_event_count = sum(
        checkpoint.get("total_event_count", 0) for checkpoint in resource_checkpoints
    )
    resource_cache_bytes = 0
    for checkpoint in resource_checkpoints:
        key = checkpoint["identity"]["cache_key"]
        resource_cache_bytes += sum(
            path.stat().st_size for path in (
                adapter.compressed_cache.target(key),
                adapter.compressed_cache.metadata_target(key),
            ) if path.is_file()
        )
    cache_bytes = sum(item["bytes"] for item in cache_entries)
    cache_report = {
        "current_cases": len(checkpoint_paths), "total_cache_bytes": cache_bytes,
        "largest_cache_entries": cache_entries[:20],
        "observed_source_bytes_with_worker_metadata": source_bytes,
        "worker_metadata_coverage_cases": len(resource_checkpoints),
        "cache_bytes_with_worker_metadata": resource_cache_bytes,
        "events_with_worker_metadata": resource_event_count,
        "observed_events": event_count,
        "bytes_cache_per_raw_byte_observed": (
            resource_cache_bytes / source_bytes if source_bytes else None),
        "bytes_cache_per_event_worker_observed": (
            resource_cache_bytes / resource_event_count
            if resource_event_count else None
        ),
        "bytes_cache_per_event_observed": cache_bytes / event_count if event_count else None,
        "projection_is_diagnostic_only": True,
    }
    _atomic_json(output / "cache-size-report.json", cache_report)
    workers = [
        worker for checkpoint in resource_checkpoints
        for worker in checkpoint["worker_resources"]
    ]
    previous_report_path = output / "materialization-report.json"
    previous_report = (
        json.loads(previous_report_path.read_text(encoding="utf-8"))
        if previous_report_path.is_file() else {}
    )
    largest_checkpoint = max(
        checkpoint_payloads,
        key=lambda checkpoint: checkpoint.get("total_event_count", 0),
        default=None,
    )
    resources_report = {
        "coordinator_peak_rss_bytes": previous_report.get(
            "coordinator_peak_rss_bytes", previous_report.get("peak_rss")
        ),
        "max_worker_peak_rss_bytes": max(
            (worker["worker_peak_rss_bytes"] for worker in workers), default=None
        ),
        "largest_case": (
            largest_checkpoint["identity"]["incident_id"]
            if largest_checkpoint else None
        ),
        "largest_case_elapsed_seconds": (
            largest_checkpoint.get("elapsed_seconds") if largest_checkpoint else None
        ),
        "largest_case_worker_resources": (
            largest_checkpoint.get("worker_resources", ())
            if largest_checkpoint else ()
        ),
        "peaks_are_reported_separately_not_summed": True,
    }
    _atomic_json(output / "resource-observability-report.json", resources_report)
    clean = [item for item in sorted(classified_records, key=lambda value: value["incident_id"])
             if item["integrity_status"] == "CLEAN"][:5]
    partial_ids = {
        item["incident_id"] for item in classified_records
        if item["integrity_status"] == "PARTIAL_BUT_USABLE"
    }
    diagnostic_ids = (
        {item["incident_id"] for item in clean}
        | partial_ids
        | ({largest_checkpoint["identity"]["incident_id"]} if largest_checkpoint else set())
    )
    diagnostic = [item for item in classified_records if item["incident_id"] in diagnostic_ids]
    diagnostic.sort(key=lambda item: item["incident_id"])
    _atomic_json(output / "integrity-diagnostic-subset.json", {
        "integrity_policy_version": INTEGRITY_POLICY_VERSION,
        "selection": "five-clean-plus-eight-initially-unusable-plus-largest-deduplicated",
        "incident_count": len(diagnostic), "incidents": diagnostic,
        "heavy_recomputations": 0,
    })
    write_sha256s(output, output / "SHA256SUMS", exclude={"SHA256SUMS"})
    return {
        "checkpoint_count": len(checkpoint_paths),
        "before_usable": before_usable, "before_unusable": before_unusable,
        "after_usable": after_usable, "after_unusable": after_unusable,
        "reclassified": reclassified, "heavy_recomputations": 0,
        "audit_incidents": len(audit_incidents),
        "diagnostic_subset_incidents": len(diagnostic), "cache": cache_report,
        "resources": resources_report,
        "alignment": alignment_audit,
        "fingerprints": fingerprint_audit,
    }


def smoke_descriptors(adapter: RCAEvalOfficialParquetAdapterV3) -> tuple[IncidentDescriptor, ...]:
    selected = []
    for suite in ("RE1", "RE2", "RE3"):
        for system in ("OB", "SS", "TT"):
            candidates = [
                descriptor for descriptor in adapter.list_incidents()
                if adapter.catalog_row(descriptor)["suite"] == suite
                and descriptor.system == system
            ]
            candidates.sort(key=lambda item: item.incident_id)
            preferred = [item for item in candidates if set(adapter._modality_policy(item)[0])
                         == set(SUITE_MODALITIES[(suite, system)])]
            selected.append((preferred or candidates)[0])
    return tuple(selected)


def largest_descriptor(adapter: RCAEvalOfficialParquetAdapterV3) -> tuple[IncidentDescriptor, int]:
    values = []
    for descriptor in adapter.list_incidents():
        size = sum(path.stat().st_size for path in Path(descriptor.path).iterdir() if path.is_file())
        values.append((size, descriptor.incident_id, descriptor))
    size, _, descriptor = max(values)
    return descriptor, size


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="RCAEval official v3 resource-safe materializer")
    commands = root.add_subparsers(dest="command", required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--root", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.add_argument("--cache", type=Path, required=True)
    materialize.add_argument("--resume", action="store_true")
    materialize.add_argument("--validate-only", action="store_true")
    materialize.add_argument("--smoke", action="store_true")
    materialize.add_argument("--largest-case", action="store_true")
    materialize.add_argument("--reclassify-checkpoints", action="store_true")
    materialize.add_argument("--limit", type=int)
    checksum = commands.add_parser("raw-checksums")
    checksum.add_argument("--root", type=Path, required=True)
    worker = commands.add_parser("normalize-file", help=argparse.SUPPRESS)
    worker.add_argument("--root", type=Path, required=True)
    worker.add_argument("--incident-id", required=True)
    worker.add_argument("--case", required=True)
    worker.add_argument("--system", required=True)
    worker.add_argument("--fault", required=True)
    worker.add_argument("--fault-service", required=True)
    worker.add_argument("--kind", choices=("metrics", "logs", "traces"), required=True)
    worker.add_argument("--path", type=Path, required=True)
    worker.add_argument("--events-output", type=Path, required=True)
    worker.add_argument("--metadata-output", type=Path, required=True)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "normalize-file":
        descriptor = IncidentDescriptor(
            args.incident_id, "rcaeval", args.system, args.case,
            str(args.path.parent.resolve()), args.fault, args.fault_service,
        )
        _normalized_worker(
            root=args.root.resolve(), descriptor=descriptor, kind=args.kind,
            path=args.path.resolve(), events_path=args.events_output.resolve(),
            metadata_path=args.metadata_output.resolve(),
        )
        return 0
    if args.command == "raw-checksums":
        count = write_sha256s(args.root.resolve(), args.root.resolve() / "SHA256SUMS",
                              exclude={"SHA256SUMS"})
        print(canonical_json({"raw_files_hashed": count}))
        return 0
    adapter = RCAEvalOfficialParquetAdapterV3(
        args.root, cache_dir=args.cache, artifact_dir=args.output, strict=False,
    )
    output = args.output.resolve()
    if args.reclassify_checkpoints:
        result = reclassify_checkpoints(adapter, output)
        print(canonical_json(result))
        return 0
    if args.validate_only:
        result = validate_materialization(adapter, output)
        print(canonical_json(result))
        return 0 if result["valid"] else 2
    descriptors = adapter.list_incidents()
    mode = "full"
    if args.smoke:
        descriptors = smoke_descriptors(adapter)
        mode = "smoke"
    elif args.largest_case:
        descriptor, source_bytes = largest_descriptor(adapter)
        print(canonical_json({"largest_case": descriptor.case, "source_bytes": source_bytes}))
        descriptors = (descriptor,)
        mode = "largest-case"
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit deve ser positivo.")
        descriptors = descriptors[:args.limit]
        mode = f"{mode}-limit-{args.limit}"
    result = RCAEvalOfficialMaterializerV3(adapter, output).materialize(
        descriptors, resume=args.resume, mode=mode,
    )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
