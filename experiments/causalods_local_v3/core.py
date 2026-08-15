from __future__ import annotations

import gzip
import hashlib
import json
import os
import resource
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DATASET_ID = "causalods-local-v3"
DATASET_VERSION = "3.0.0"
GENERATOR_VERSION = "causalods-local-v3-generator-v2"
EXPECTED_MODALITIES = ("trace", "log", "metric", "business_event")
FORBIDDEN_EVENT_KEYS = frozenset({
    "ground_truth", "root_cause", "root_cause_service", "ground_truth_service",
    "fault_target", "fault_target_service", "fault_type", "scenario",
    "scenario_label", "expected_service", "expected_rca_answer",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2,
                                    allow_nan=False))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_gzip_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                compressed.write(canonical_json(value).encode("utf-8"))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def derive_repeat_seed(base_seed: int, scenario: str, repeat_index: int) -> int:
    if isinstance(base_seed, bool) or not isinstance(base_seed, int):
        raise TypeError("base_seed deve ser inteiro.")
    if scenario not in SCENARIOS:
        raise ValueError(f"Cenário desconhecido: {scenario!r}.")
    if isinstance(repeat_index, bool) or not isinstance(repeat_index, int) or repeat_index < 1:
        raise ValueError("repeat_index deve ser inteiro positivo.")
    material = f"{base_seed}|{DATASET_ID}|{scenario}|{repeat_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & 0x7FFF_FFFF


@dataclass(frozen=True)
class Scenario:
    name: str
    fault_injected: bool
    target_service: str | None
    fault_type: str
    rate: float
    delay_ms: int
    expected_http_family: int

    def __post_init__(self) -> None:
        if self.name not in {"healthy", "order_error", "order_latency",
                             "payment_error", "payment_latency"}:
            raise ValueError("Cenário fora da matriz causalods-local-v3.")
        if self.fault_type not in {"healthy", "error", "latency"}:
            raise ValueError("fault_type inválido.")
        if self.fault_injected != (self.target_service is not None):
            raise ValueError("fault_injected e target_service divergem.")
        if self.target_service not in {None, "order-service", "payment-service"}:
            raise ValueError("target_service inválido.")
        if self.name == "healthy" and (self.fault_type != "healthy" or self.rate != 0):
            raise ValueError("healthy não pode configurar falha.")
        if not 0 <= self.rate <= 1 or self.delay_ms < 0:
            raise ValueError("Parâmetros de falha inválidos.")

    def parameters(self, seed: int) -> dict[str, Any]:
        return {"mode": "none" if not self.fault_injected else self.fault_type,
                "rate": self.rate, "delay_ms": self.delay_ms, "seed": seed}


SCENARIOS: Mapping[str, Scenario] = {
    "healthy": Scenario("healthy", False, None, "healthy", 0.0, 0, 2),
    "order_error": Scenario("order_error", True, "order-service", "error", 1.0, 0, 5),
    "order_latency": Scenario("order_latency", True, "order-service", "latency", 1.0, 1500, 2),
    "payment_error": Scenario("payment_error", True, "payment-service", "error", 1.0, 0, 5),
    "payment_latency": Scenario("payment_latency", True, "payment-service", "latency", 1.0, 1500, 2),
}


@dataclass(frozen=True)
class GroundTruth:
    experiment_run_id: str
    scenario: str
    repeat_index: int
    fault_injected: bool
    fault_target_service: str | None
    root_cause_service: str | None
    root_cause_type: str
    fault_parameters: Mapping[str, Any]
    base_seed: int
    scenario_seed: int
    repeat_seed: int
    fault_started_at: str
    fault_stopped_at: str | None
    request_id: str
    business_correlation_id: str

    def __post_init__(self) -> None:
        scenario = SCENARIOS[self.scenario]
        expected_id = f"{DATASET_ID}/{self.scenario}/{self.repeat_index:04d}"
        if self.experiment_run_id != expected_id:
            raise ValueError("experiment_run_id não é canônico.")
        if self.fault_injected != scenario.fault_injected:
            raise ValueError("Ground truth diverge do fault controller.")
        if self.fault_target_service != scenario.target_service:
            raise ValueError("Alvo gravado diverge do fault controller.")
        if self.root_cause_service != scenario.target_service:
            raise ValueError("Causa gravada diverge do fault controller.")
        if self.root_cause_type != scenario.fault_type:
            raise ValueError("Tipo gravado diverge do fault controller.")
        if self.repeat_seed != derive_repeat_seed(self.base_seed, self.scenario,
                                                  self.repeat_index):
            raise ValueError("repeat_seed não é determinístico.")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def forbidden_key_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            current = f"{path}.{key}"
            if str(key).strip().lower() in FORBIDDEN_EVENT_KEYS:
                found.append(current)
            found.extend(forbidden_key_paths(item, current))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(forbidden_key_paths(item, f"{path}[{index}]"))
    return found


def audit_anti_leakage(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    inspected = list(events)
    found = []
    for index, event in enumerate(inspected):
        found.extend(forbidden_key_paths(event, f"$[{index}]"))
    return {"status": "PASS" if not found else "FAIL", "event_count": len(inspected),
            "forbidden_keys": sorted(found),
            "inspected_contract_fields": sorted(FORBIDDEN_EVENT_KEYS)}


def audit_events(events: Iterable[Mapping[str, Any]], correlation_id: str,
                 expected_services: Iterable[str] = ("order-service",)) -> dict[str, Any]:
    rows = list(events)
    counts = Counter(
        signal.value if hasattr(signal := item.get("signal_type"), "value") else str(signal)
        for item in rows
    )
    modality_pass = all(counts[item] > 0 for item in EXPECTED_MODALITIES)
    identity_rows = [item for item in rows if item.get("signal_type") != "metric"]
    identities = sorted({item.get("correlation_id") for item in identity_rows
                         if item.get("correlation_id")})
    services = sorted({item.get("service_name") for item in rows if item.get("service_name")})
    required_services = set(expected_services)
    trace_services = {item.get("service_name") for item in identity_rows
                      if item.get("signal_type") == "trace" and item.get("correlation_id") == correlation_id}
    log_services = {item.get("service_name") for item in identity_rows
                    if item.get("signal_type") == "log" and item.get("correlation_id") == correlation_id}
    identity_pass = (identities == [correlation_id]
                     and required_services.issubset(trace_services)
                     and required_services.issubset(log_services))
    return {
        "counts": {item: counts[item] for item in EXPECTED_MODALITIES},
        "modality_status": "PASS" if modality_pass else "FAIL",
        "identity_status": "PASS" if identity_pass else "FAIL",
        "correlation_ids": identities,
        "services_observed": services,
        "trace_services": sorted(item for item in trace_services if item),
        "log_services": sorted(item for item in log_services if item),
        "expected_services": sorted(required_services),
        "anti_leakage": audit_anti_leakage(rows),
    }


def audit_run_isolation(checkpoints: Iterable[Mapping[str, Any]], output: Path) -> dict[str, Any]:
    ownership: dict[str, set[str]] = {}
    violations: list[dict[str, str]] = []
    for checkpoint in checkpoints:
        run_id = str(checkpoint["run_id"])
        expected = None
        for relative in checkpoint["artifact_hashes"]:
            if not relative.endswith("request-metadata.json"):
                continue
            metadata = json.loads((Path(output) / relative).read_text(encoding="utf-8"))
            expected = metadata["business.correlation.id"]
        if expected is None:
            violations.append({"run_id": run_id, "reason": "request_metadata_missing"})
            continue
        owners = ownership.setdefault(expected, set())
        owners.add(run_id)
    for correlation_id, owners in sorted(ownership.items()):
        if len(owners) != 1:
            violations.append({"correlation_id": correlation_id,
                               "reason": f"shared_by:{','.join(sorted(owners))}"})
    return {"status": "PASS" if not violations else "FAIL", "violations": violations,
            "correlation_ids": len(ownership)}


def resource_snapshot(root: Path) -> dict[str, Any]:
    disk = shutil.disk_usage(root)
    return {"recorded_at": utc_now(), "free_disk_bytes": disk.free,
            "total_disk_bytes": disk.total,
            "coordinator_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


class Campaign:
    """Serial materializer. A live executor is injected to keep policy testable."""

    def __init__(self, output: Path, *, base_seed: int = 20260814,
                 disk_gate_bytes: int = 5 * 1024**3,
                 executor: Callable[[Scenario, int, str, str], Mapping[str, Any]] | None = None):
        self.output = Path(output)
        self.base_seed = base_seed
        self.disk_gate_bytes = disk_gate_bytes
        self.executor = executor

    @staticmethod
    def run_id(scenario: str, repeat_index: int) -> str:
        return f"{DATASET_ID}/{scenario}/{repeat_index:04d}"

    @staticmethod
    def safe_id(run_id: str) -> str:
        return run_id.replace("/", "__")

    def checkpoint_path(self, run_id: str) -> Path:
        return self.output / "checkpoints" / f"{self.safe_id(run_id)}.json"

    def _checkpoint_valid(self, run_id: str) -> bool:
        path = self.checkpoint_path(run_id)
        if not path.is_file():
            return False
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            if (checkpoint.get("status") != "COMPLETE"
                    or checkpoint.get("run_id") != run_id
                    or checkpoint.get("generator_version") != GENERATOR_VERSION
                    or checkpoint.get("usable_for_benchmark") is not True
                    or checkpoint.get("audit", {}).get("modality_status") != "PASS"
                    or checkpoint.get("audit", {}).get("identity_status") != "PASS"
                    or checkpoint.get("audit", {}).get("anti_leakage", {}).get("status") != "PASS"):
                return False
            for relative, digest in checkpoint["artifact_hashes"].items():
                target = self.output / relative
                if not target.is_file() or file_sha256(target) != digest:
                    return False
            return True
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return False

    def run(self, selected: Iterable[str], *, repeat: int = 1,
            resume: bool = False) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("Campaign requer executor live explícito.")
        names = tuple(selected)
        if not names or any(name not in SCENARIOS for name in names):
            raise ValueError("Seleção de cenários inválida.")
        started = time.monotonic()
        completed = skipped = new = 0
        rows = []
        self.output.mkdir(parents=True, exist_ok=True)
        for repeat_index in range(1, repeat + 1):
            for name in names:
                run_id = self.run_id(name, repeat_index)
                if resume and self._checkpoint_valid(run_id):
                    skipped += 1
                    rows.append(json.loads(self.checkpoint_path(run_id).read_text(encoding="utf-8")))
                    continue
                before = resource_snapshot(self.output)
                if before["free_disk_bytes"] < self.disk_gate_bytes:
                    raise RuntimeError("Disk gate bloqueou nova execução.")
                seed = derive_repeat_seed(self.base_seed, name, repeat_index)
                # A run identity is deterministic; the transport correlation ID
                # is unique per physical attempt so an invalid retry cannot join
                # stale Kafka/OpenSearch evidence from its previous attempt.
                correlation_id = f"clv3-{uuid.uuid4().hex[:24]}"
                result = dict(self.executor(SCENARIOS[name], seed, run_id, correlation_id))
                checkpoint = self._materialize(name, repeat_index, seed, run_id,
                                               correlation_id, result, before)
                rows.append(checkpoint)
                completed += 1
                new += 1
        self._write_dataset_metadata(rows)
        return {"completed": len(rows), "skipped": skipped, "resumed": skipped,
                "new_executions": new, "duration_seconds": time.monotonic() - started}

    def _materialize(self, name: str, repeat_index: int, seed: int, run_id: str,
                     correlation_id: str, result: Mapping[str, Any],
                     resource_before: Mapping[str, Any]) -> dict[str, Any]:
        scenario = SCENARIOS[name]
        events = list(result["events"])
        expected_services = ("order-service",) if name == "order_error" else (
            "order-service", "payment-service")
        audit = audit_events(events, correlation_id, expected_services)
        counts = audit["counts"]
        usable = (audit["modality_status"] == "PASS"
                  and audit["identity_status"] == "PASS"
                  and audit["anti_leakage"]["status"] == "PASS")
        integrity = "CLEAN" if usable else ("PARTIAL_BUT_USABLE" if result.get("usable") else "UNUSABLE")
        safe = self.safe_id(run_id)
        incident_dir = self.output / "incidents" / safe
        incident_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, str] = {}
        source_hashes: dict[str, str] = {}
        accounting: dict[str, dict[str, Any]] = {}
        kinds = {"trace": "traces", "log": "logs", "metric": "metrics",
                 "business_event": "business_events"}
        for modality in EXPECTED_MODALITIES:
            relative = f"incidents/{safe}/{modality}.json.gz"
            target = self.output / relative
            modality_events = [item for item in events if item.get("signal_type") == modality]
            atomic_gzip_json(target, modality_events)
            digest = file_sha256(target)
            artifacts[relative] = digest
            source_hashes[relative] = digest
            count = len(modality_events)
            accounting[relative] = {
                "relative_path": relative, "kind": kinds[modality],
                "signal_type": modality, "total_rows": count,
                "accepted_rows": count, "rejected_rows": 0,
                "partially_accepted_rows": 0, "excluded_rows": 0,
                "produced_events": count,
                "accepted_metric_cells": count if modality == "metric" else 0,
                "rejected_metric_cells": 0, "rejected_row_units": 0,
                "rejected_cell_units": 0,
            }
        request_metadata = dict(result["request_metadata"])
        ground_truth = GroundTruth(
            experiment_run_id=run_id, scenario=name, repeat_index=repeat_index,
            fault_injected=scenario.fault_injected,
            fault_target_service=scenario.target_service,
            root_cause_service=scenario.target_service,
            root_cause_type=scenario.fault_type,
            fault_parameters=scenario.parameters(seed), base_seed=self.base_seed,
            scenario_seed=derive_repeat_seed(self.base_seed, name, 1), repeat_seed=seed,
            fault_started_at=result["fault_started_at"],
            fault_stopped_at=result.get("fault_stopped_at"),
            request_id=request_metadata["request_id"],
            business_correlation_id=correlation_id,
        )
        ground_relative = f"incidents/{safe}/ground-truth.json"
        atomic_json(self.output / ground_relative, ground_truth.to_dict())
        ground_hash = file_sha256(self.output / ground_relative)
        artifacts[ground_relative] = ground_hash
        request_relative = f"incidents/{safe}/request-metadata.json"
        atomic_json(self.output / request_relative, request_metadata)
        artifacts[request_relative] = file_sha256(self.output / request_relative)
        audit_relative = f"audits/{safe}.json"
        atomic_json(self.output / audit_relative, audit)
        artifacts[audit_relative] = file_sha256(self.output / audit_relative)
        manifest = self._manifest(run_id, name, scenario, result, counts, source_hashes,
                                  accounting, ground_hash, usable)
        manifest_relative = f"manifests/{safe}.json"
        atomic_json(self.output / manifest_relative, manifest.to_dict())
        artifacts[manifest_relative] = file_sha256(self.output / manifest_relative)
        after = resource_snapshot(self.output)
        checkpoint = {
            "status": "COMPLETE", "generator_version": GENERATOR_VERSION,
            "run_id": run_id, "incident_id": run_id,
            "scenario": name, "repeat_index": repeat_index,
            "manifest_path": manifest_relative, "manifest_sha256": manifest.fingerprint,
            "capabilities": list(manifest.modalities), "integrity_status": integrity,
            "usable_for_benchmark": manifest.usable_for_benchmark,
            "artifact_hashes": dict(sorted(artifacts.items())), "audit": audit,
            "http_status": result.get("http_status"), "dlq_count": result.get("dlq_count", 0),
            "pipeline": result.get("pipeline", {}), "timing": result.get("timing", {}),
            "resource_before": dict(resource_before), "resource_after": after,
        }
        atomic_json(self.checkpoint_path(run_id), checkpoint)
        return checkpoint

    def _manifest(self, run_id: str, name: str, scenario: Scenario,
                  result: Mapping[str, Any], counts: Mapping[str, int],
                  source_hashes: Mapping[str, str], accounting: Mapping[str, Any],
                  ground_hash: str, usable: bool):
        from multimodal_v1.public_datasets_v3.contracts import AnalysisWindow, IncidentManifest
        observed = tuple(item for item in EXPECTED_MODALITIES if counts[item] > 0)
        start = parse_time(result["experiment_started_at"])
        end = parse_time(result["collection_finished_at"])
        total = sum(counts.values())
        source_digest = sha256_value(source_hashes)
        report_digest = sha256_value({"counts": counts, "source": source_hashes})
        return IncidentManifest(
            incident_id=run_id, source_dataset=DATASET_ID,
            source_system="causalods", source_case=name,
            fault_type=scenario.fault_type, fault_service=scenario.target_service,
            analysis_window=AnalysisWindow(start, end), modalities=observed,
            source_files_sha256=source_hashes, event_count_by_modality=counts,
            adapter_version=GENERATOR_VERSION,
            adapter_configuration_sha256=sha256_value({"scenario": name}),
            cache_key=sha256_value({"run_id": run_id, "source": source_digest}),
            normalized_events_sha256=source_digest, ground_truth_sha256=ground_hash,
            schema_report_sha256=report_digest,
            window_policy="causalods-local-v3-request-window-v1",
            observed_interval=AnalysisWindow(start, end),
            window_decision="controller-bounded-before-during-after",
            accepted_row_count=total, rejected_row_count=0, total_event_count=total,
            considered_row_count=total, excluded_by_window_count=0,
            dataset_schema_report_sha256=report_digest,
            incident_normalization_report_sha256=report_digest,
            normalization_mode="strict" if usable else "permissive",
            empty_incident_reason=None if usable else "missing_required_modality_or_identity",
            usable_for_benchmark=usable,
            modality_policy_version="causalods-local-v3-full-v1",
            expected_modalities=EXPECTED_MODALITIES, observed_modalities=observed,
            missing_expected_modalities=tuple(sorted(set(EXPECTED_MODALITIES)-set(observed))),
            unexpected_modalities=(), normalization_accounting_by_file=accounting,
            normalized_source_files=tuple(source_hashes), modality_policy_status="official",
        )

    def _write_dataset_metadata(self, checkpoints: Iterable[Mapping[str, Any]]) -> None:
        from experiments.multimodal_v3.core import DatasetCapabilityV3
        from multimodal_v1.public_datasets_v3.contracts import IncidentManifest
        rows = sorted(checkpoints, key=lambda item: item["run_id"])
        index = []
        capabilities = []
        for checkpoint in rows:
            manifest = IncidentManifest.from_dict(json.loads(
                (self.output / checkpoint["manifest_path"]).read_text(encoding="utf-8")))
            capability = DatasetCapabilityV3.from_incident_manifest(manifest)
            capabilities.append(capability.to_dict())
            index.append({key: checkpoint[key] for key in (
                "incident_id", "run_id", "scenario", "repeat_index", "manifest_path",
                "capabilities", "integrity_status", "usable_for_benchmark", "artifact_hashes")})
        commit = subprocess.run(("git", "rev-parse", "HEAD"), capture_output=True,
                                text=True, check=False).stdout.strip() or "unknown"
        atomic_json(self.output / "SOURCE.json", {
            "dataset_id": DATASET_ID, "dataset_version": DATASET_VERSION,
            "generator_version": GENERATOR_VERSION, "platform_version": "causalods-platform-v3",
            "git_commit": commit, "created_at": utc_now(),
            "scenario_definitions": {name: scenario.__dict__ for name, scenario in SCENARIOS.items()},
            "repeat_target": 100, "workers": 1, "parallel_scenarios": False,
            "seed_policy": "sha256(base_seed|dataset|scenario|repeat_index)",
            "base_seed": self.base_seed,
            "telemetry_topics": ["traces-v3", "logs-v1", "metrics-v1",
                                 "business-events-v1", "multimodal-dead-letter-v1"],
            "services": ["order-service", "payment-service", "correlation-engine"],
            "fault_controller_version": "causalods-compose-fault-controller-v1",
            "schemas": ["unified-event-v1", "business-event-v1", "incident-manifest-v2"],
        })
        atomic_json(self.output / "INDEX.json", {"count": len(index), "incidents": index})
        atomic_json(self.output / "capabilities.json",
                    {"count": len(capabilities), "capabilities": capabilities})
        atomic_json(self.output / "campaign-manifest.json", {
            "dataset_id": DATASET_ID, "target_runs": 500, "materialized_runs": len(index),
            "workers": 1, "parallel_scenario_execution": False,
            "smoke_runs_excluded_from_final_by_default": True,
        })
        atomic_json(self.output / "dataset-schema-report.json", {
            "expected_modalities": list(EXPECTED_MODALITIES), "manifest_schema": "incident-manifest-v2",
            "event_schema": "unified-event-v1", "business_event_schema": "business-event-v1",
        })
        atomic_json(self.output / "materialization-report.json", {
            "completed": len(index), "usable": sum(item["usable_for_benchmark"] for item in index),
            "integrity": dict(Counter(item["integrity_status"] for item in index)),
        })
        atomic_json(self.output / "audits" / "run-isolation.json",
                    audit_run_isolation(rows, self.output))
        included = []
        for path in sorted(self.output.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                included.append(f"{file_sha256(path)}  {path.relative_to(self.output)}")
        (self.output / "SHA256SUMS").write_text("\n".join(included) + "\n", encoding="utf-8")
