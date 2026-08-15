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
GENERATOR_VERSION = "causalods-local-v3-generator-v4"
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
class ObservedFaultServiceState:
    service_name: str
    mode: str
    rate: float
    delay_ms: int
    seed: int
    container_id: str
    container_started_at: str

    def __post_init__(self) -> None:
        if self.service_name not in {"order-service", "payment-service"}:
            raise ValueError("Serviço observado fora do fault controller local.")
        if self.mode not in {"none", "error", "latency"}:
            raise ValueError("Fault mode observado inválido.")
        if not 0.0 <= self.rate <= 1.0 or self.delay_ms < 0 or self.seed < 0:
            raise ValueError("Parâmetros observados do fault controller inválidos.")
        if not self.container_id or not self.container_started_at:
            raise ValueError("Geração observada do container é obrigatória.")
        parse_time(self.container_started_at)

    @classmethod
    def from_mapping(cls, service_name: str, value: Mapping[str, Any]):
        return cls(
            service_name=service_name,
            mode=str(value.get("mode", "")),
            rate=float(value.get("rate")),
            delay_ms=int(value.get("delay_ms")),
            seed=int(value.get("seed")),
            container_id=str(value.get("container_id", "")),
            container_started_at=str(value.get("container_started_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ObservedFaultControllerState:
    services: tuple[ObservedFaultServiceState, ...]

    def __post_init__(self) -> None:
        names = tuple(item.service_name for item in self.services)
        if names != ("order-service", "payment-service"):
            raise ValueError("Estado observado deve conter os dois serviços em ordem canônica.")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]):
        raw = value.get("services", value)
        if not isinstance(raw, Mapping):
            raise TypeError("Estado observado do fault controller deve ser objeto.")
        return cls(tuple(
            ObservedFaultServiceState.from_mapping(service, raw.get(service, {}))
            for service in ("order-service", "payment-service")
        ))

    def service(self, name: str) -> ObservedFaultServiceState:
        return next(item for item in self.services if item.service_name == name)

    def to_dict(self) -> dict[str, Any]:
        return {"services": {item.service_name: item.to_dict() for item in self.services}}


def requested_fault_configuration(scenario: Scenario, seed: int) -> dict[str, Any]:
    services = {}
    for service in ("order-service", "payment-service"):
        selected = scenario.target_service == service
        services[service] = {
            "service_name": service,
            "mode": scenario.fault_type if selected else "none",
            "rate": scenario.rate if selected else 0.0,
            "delay_ms": scenario.delay_ms if selected else 0,
            "seed": seed,
        }
    return {"scenario": scenario.name, "repeat_seed": seed, "services": services}


def validate_fault_controller_binding(
        scenario: Scenario, seed: int,
        observed: ObservedFaultControllerState | Mapping[str, Any]) -> dict[str, Any]:
    state = (observed if isinstance(observed, ObservedFaultControllerState)
             else ObservedFaultControllerState.from_mapping(observed))
    requested = requested_fault_configuration(scenario, seed)
    violations: list[dict[str, Any]] = []
    active = [item.service_name for item in state.services if item.mode != "none"]
    expected_active = [] if scenario.target_service is None else [scenario.target_service]
    if active != expected_active:
        violations.append({"field": "active_services", "requested": expected_active,
                           "observed": active})
    for service in state.services:
        expected = requested["services"][service.service_name]
        for field in ("mode", "rate", "delay_ms", "seed"):
            actual = getattr(service, field)
            if actual != expected[field]:
                violations.append({"service_name": service.service_name, "field": field,
                                   "requested": expected[field], "observed": actual})
    return {
        "status": "PASS" if not violations else "FAIL",
        "requested_configuration_sha256": sha256_value(requested),
        "observed_state_sha256": sha256_value(state.to_dict()),
        "requested": requested,
        "observed": state.to_dict(),
        "active_services": active,
        "violations": violations,
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
    observed_fault_controller_state_sha256: str
    fault_controller_binding_audit_sha256: str

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError("Cenário do ground truth inválido.")
        expected_id = f"{DATASET_ID}/{self.scenario}/{self.repeat_index:04d}"
        if self.experiment_run_id != expected_id:
            raise ValueError("experiment_run_id não é canônico.")
        if self.fault_injected:
            if (self.fault_target_service not in {"order-service", "payment-service"}
                    or self.root_cause_service != self.fault_target_service
                    or self.root_cause_type not in {"error", "latency"}):
                raise ValueError("Ground truth de falha observado é inconsistente.")
        elif (self.fault_target_service is not None or self.root_cause_service is not None
              or self.root_cause_type != "healthy"):
            raise ValueError("Ground truth healthy observado é inconsistente.")
        if self.repeat_seed != derive_repeat_seed(self.base_seed, self.scenario,
                                                  self.repeat_index):
            raise ValueError("repeat_seed não é determinístico.")
        if self.fault_parameters.get("seed") != self.repeat_seed:
            raise ValueError("Seed do ground truth não deriva do estado observado.")
        for digest in (self.observed_fault_controller_state_sha256,
                       self.fault_controller_binding_audit_sha256):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("Hash de provenance do fault controller inválido.")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def ground_truth_from_observed_state(
        *, run_id: str, scenario_name: str, repeat_index: int, base_seed: int,
        observed: ObservedFaultControllerState, binding_audit: Mapping[str, Any],
        fault_started_at: str, fault_stopped_at: str | None, request_id: str,
        correlation_id: str, observed_state_sha256: str | None = None,
        binding_audit_sha256: str | None = None) -> GroundTruth:
    if binding_audit.get("status") != "PASS":
        raise ValueError("FAULT_CONTROLLER_BINDING=FAIL; ground truth não pode ser criado.")
    active = [item for item in observed.services if item.mode != "none"]
    repeat_seed = derive_repeat_seed(base_seed, scenario_name, repeat_index)
    if not active:
        injected, target, cause_type = False, None, "healthy"
        parameters = {"mode": "none", "rate": 0.0, "delay_ms": 0,
                      "seed": repeat_seed}
    elif len(active) == 1:
        controller = active[0]
        injected, target, cause_type = True, controller.service_name, controller.mode
        parameters = {"mode": controller.mode, "rate": controller.rate,
                      "delay_ms": controller.delay_ms, "seed": controller.seed}
    else:
        raise ValueError("Mais de um fault ativo no estado observado validado.")
    observed_hash = observed_state_sha256 or sha256_value(observed.to_dict())
    binding_hash = binding_audit_sha256 or sha256_value(binding_audit)
    return GroundTruth(
        experiment_run_id=run_id, scenario=scenario_name, repeat_index=repeat_index,
        fault_injected=injected, fault_target_service=target,
        root_cause_service=target, root_cause_type=cause_type,
        fault_parameters=parameters, base_seed=base_seed,
        scenario_seed=derive_repeat_seed(base_seed, scenario_name, 1),
        repeat_seed=repeat_seed, fault_started_at=fault_started_at,
        fault_stopped_at=fault_stopped_at, request_id=request_id,
        business_correlation_id=correlation_id,
        observed_fault_controller_state_sha256=observed_hash,
        fault_controller_binding_audit_sha256=binding_hash,
    )


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
    metrics = [item for item in rows if item.get("signal_type") == "metric"]
    invalid_metric_sources = sorted({str(item.get("source_reference")) for item in metrics
                                     if not str(item.get("source_reference", ""))
                                     .startswith("kafka:metrics-v1:")})
    return {
        "counts": {item: counts[item] for item in EXPECTED_MODALITIES},
        "modality_status": "PASS" if modality_pass else "FAIL",
        "identity_status": "PASS" if identity_pass else "FAIL",
        "correlation_ids": identities,
        "services_observed": services,
        "trace_services": sorted(item for item in trace_services if item),
        "log_services": sorted(item for item in log_services if item),
        "expected_services": sorted(required_services),
        "metric_pipeline_provenance": {
            "status": "PASS" if metrics and not invalid_metric_sources else "FAIL",
            "required_source_prefix": "kafka:metrics-v1:",
            "invalid_sources": invalid_metric_sources,
            "metric_events": len(metrics),
        },
        "anti_leakage": audit_anti_leakage(rows),
    }


def audit_run_isolation(checkpoints: Iterable[Mapping[str, Any]], output: Path) -> dict[str, Any]:
    root = Path(output)
    allowed_services = {"order-service", "payment-service"}
    runs: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    event_owners: dict[str, set[str]] = {}
    correlation_owners: dict[str, set[str]] = {}
    for checkpoint in checkpoints:
        run_id = str(checkpoint["run_id"])
        metadata_paths = [relative for relative in checkpoint["artifact_hashes"]
                          if relative.endswith("request-metadata.json")]
        if len(metadata_paths) != 1:
            violations.append({"run_id": run_id, "reason": "request_metadata_missing"})
            continue
        metadata = json.loads((root / metadata_paths[0]).read_text(encoding="utf-8"))
        expected = metadata["business.correlation.id"]
        correlation_owners.setdefault(expected, set()).add(run_id)
        start = parse_time(checkpoint["timing"]["experiment_started_at"])
        end = parse_time(checkpoint["timing"]["collection_finished_at"])
        events: list[dict[str, Any]] = []
        for relative in checkpoint["artifact_hashes"]:
            if not relative.endswith(tuple(f"/{item}.json.gz" for item in EXPECTED_MODALITIES)):
                continue
            with gzip.open(root / relative, "rt", encoding="utf-8") as source:
                events.extend(json.load(source))
        runs.append({"run_id": run_id, "expected": expected, "start": start,
                     "end": end, "events": events})

    for correlation_id, owners in sorted(correlation_owners.items()):
        if len(owners) != 1:
            violations.append({"correlation_id": correlation_id,
                               "reason": "foreign_correlation_id",
                               "runs": sorted(owners)})
    for index, left in enumerate(runs):
        for right in runs[index + 1:]:
            if max(left["start"], right["start"]) <= min(left["end"], right["end"]):
                violations.append({"reason": "overlapping_metric_windows",
                                   "runs": [left["run_id"], right["run_id"]]})
        for event in left["events"]:
            event_id = str(event.get("event_id", ""))
            if event_id:
                event_owners.setdefault(event_id, set()).add(left["run_id"])
            correlation_id = event.get("correlation_id")
            if correlation_id and correlation_id != left["expected"]:
                violations.append({"run_id": left["run_id"], "event_id": event_id,
                                   "reason": "foreign_correlation_id",
                                   "observed": correlation_id,
                                   "expected": left["expected"]})
            if event.get("signal_type") != "metric":
                continue
            timestamp = parse_time(event["event_timestamp"])
            if not left["start"] <= timestamp <= left["end"]:
                violations.append({"run_id": left["run_id"], "event_id": event_id,
                                   "reason": "metric_outside_run_window"})
            if event.get("service_name") not in allowed_services:
                violations.append({"run_id": left["run_id"], "event_id": event_id,
                                   "reason": "unknown_service",
                                   "service_name": event.get("service_name")})
            matching = [run["run_id"] for run in runs
                        if run["start"] <= timestamp <= run["end"]]
            if len(matching) > 1:
                violations.append({"run_id": left["run_id"], "event_id": event_id,
                                   "reason": "metric_ambiguous_between_runs",
                                   "matching_runs": matching})
    for event_id, owners in sorted(event_owners.items()):
        if len(owners) > 1:
            violations.append({"event_id": event_id,
                               "reason": "duplicate_event_id_across_runs",
                               "runs": sorted(owners)})
    return {"status": "PASS" if not violations else "FAIL", "violations": violations,
            "correlation_ids": len(correlation_owners),
            "scientific_event_ids": len(event_owners), "runs_inspected": len(runs)}


def audit_topic_offsets(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    scientific = ("traces-v3", "logs-v1", "metrics-v1", "business-events-v1")
    topics = (*scientific, "multimodal-dead-letter-v1")
    deltas: dict[str, Any] = {}
    for topic in topics:
        before_total = sum(int(value) for value in before.get(topic, {}).values())
        after_total = sum(int(value) for value in after.get(topic, {}).values())
        deltas[topic] = {"before_total": before_total, "after_total": after_total,
                         "delta": after_total - before_total}
    passed = (all(deltas[topic]["delta"] > 0 for topic in scientific)
              and deltas["multimodal-dead-letter-v1"]["delta"] == 0)
    return {"status": "PASS" if passed else "FAIL", "topics": deltas,
            "note": "Kafka deltas prove transport activity, not a 1:1 event count."}


def audit_service_state_reset(service_state: Mapping[str, Any],
                              request_started_at: str) -> dict[str, Any]:
    request_start = parse_time(request_started_at)
    rows = {}
    for service in ("order-service", "payment-service"):
        state = service_state.get(service, {})
        started_at = state.get("container_started_at")
        passed = bool(state.get("container_id") and started_at
                      and parse_time(started_at) < request_start)
        rows[service] = {**dict(state), "precedes_request": passed}
    return {"status": "PASS" if all(row["precedes_request"] for row in rows.values())
            else "FAIL", "request_started_at": request_started_at, "services": rows}


def resource_snapshot(root: Path) -> dict[str, Any]:
    disk = shutil.disk_usage(root)
    return {"recorded_at": utc_now(), "free_disk_bytes": disk.free,
            "total_disk_bytes": disk.total,
            "coordinator_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


class Campaign:
    """Serial materializer. A live executor is injected to keep policy testable."""

    def __init__(self, output: Path, *, base_seed: int = 20260814,
                 disk_gate_bytes: int = 5 * 1024**3,
                 mode: str = "smoke",
                 executor: Callable[[Scenario, int, str, str], Mapping[str, Any]] | None = None):
        self.output = Path(output)
        self.base_seed = base_seed
        self.disk_gate_bytes = disk_gate_bytes
        if mode not in {"smoke", "final"}:
            raise ValueError("Campaign mode deve ser smoke ou final.")
        lowered_parts = {part.lower() for part in self.output.parts}
        if mode == "final" and (any(part.startswith("smoke") for part in lowered_parts)
                                or "promotion-validation" in lowered_parts):
            raise ValueError("Campaign final exige output separado de smoke/validation.")
        self.mode = mode
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
                    or checkpoint.get("integrity_status") != "CLEAN"
                    or checkpoint.get("usable_for_benchmark") is not True
                    or checkpoint.get("dlq_count") != 0
                    or checkpoint.get("audit", {}).get("modality_status") != "PASS"
                    or checkpoint.get("audit", {}).get("identity_status") != "PASS"
                    or checkpoint.get("audit", {}).get("metric_pipeline_provenance", {}).get("status") != "PASS"
                    or checkpoint.get("transport_audit", {}).get("status") != "PASS"
                    or checkpoint.get("service_state_reset_audit", {}).get("status") != "PASS"
                    or checkpoint.get("fault_controller_binding_audit", {}).get("status") != "PASS"
                    or checkpoint.get("audit", {}).get("anti_leakage", {}).get("status") != "PASS"):
                return False
            for relative, digest in checkpoint["artifact_hashes"].items():
                target = self.output / relative
                if not target.is_file() or file_sha256(target) != digest:
                    return False
            binding_paths = [item for item in checkpoint["artifact_hashes"]
                             if item.endswith("/fault-controller-binding-audit.json")]
            observed_paths = [item for item in checkpoint["artifact_hashes"]
                              if item.endswith("/observed-fault-controller-state.json")]
            ground_paths = [item for item in checkpoint["artifact_hashes"]
                            if item.endswith("/ground-truth.json")]
            if not (len(binding_paths) == len(observed_paths) == len(ground_paths) == 1):
                return False
            persisted_binding = json.loads(
                (self.output / binding_paths[0]).read_text(encoding="utf-8"))
            ground_truth = json.loads(
                (self.output / ground_paths[0]).read_text(encoding="utf-8"))
            if (persisted_binding.get("status") != "PASS"
                    or persisted_binding != checkpoint["fault_controller_binding_audit"]
                    or ground_truth.get("observed_fault_controller_state_sha256")
                        != checkpoint["artifact_hashes"][observed_paths[0]]
                    or ground_truth.get("fault_controller_binding_audit_sha256")
                        != checkpoint["artifact_hashes"][binding_paths[0]]):
                return False
            from multimodal_v1.public_datasets_v3.contracts import IncidentManifest
            manifest = IncidentManifest.from_dict(json.loads(
                (self.output / checkpoint["manifest_path"]).read_text(encoding="utf-8")
            ))
            if (manifest.fingerprint != checkpoint.get("manifest_sha256")
                    or manifest.usable_for_benchmark is not True
                    or set(manifest.observed_modalities) != set(EXPECTED_MODALITIES)
                    or manifest.missing_expected_modalities):
                return False
            return True
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def run(self, selected: Iterable[str], *, repeat: int = 1,
            resume: bool = False) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("Campaign requer executor live explícito.")
        names = tuple(selected)
        if not names or any(name not in SCENARIOS for name in names):
            raise ValueError("Seleção de cenários inválida.")
        if self.mode == "smoke" and repeat != 1:
            raise ValueError("MODE=smoke exige repeat=1.")
        if self.mode == "final" and (repeat != 100 or names != tuple(SCENARIOS)):
            raise ValueError("MODE=final exige scenario=all e repeat=100.")
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
        report = {"attempted": len(names) * repeat, "completed": len(rows),
                  "skipped": skipped, "resumed": skipped,
                  "new_executions": new, "duration_seconds": time.monotonic() - started,
                  "executed_at": utc_now()}
        if resume:
            atomic_json(self.output / "audits" / "resume-report.json", report)
        return report

    def _materialize(self, name: str, repeat_index: int, seed: int, run_id: str,
                     correlation_id: str, result: Mapping[str, Any],
                     resource_before: Mapping[str, Any]) -> dict[str, Any]:
        scenario = SCENARIOS[name]
        requested = requested_fault_configuration(scenario, seed)
        observed = ObservedFaultControllerState.from_mapping(
            result.get("observed_fault_controller_state",
                       result.get("fault_controller_state", result.get("service_state", {})))
        )
        binding = validate_fault_controller_binding(scenario, seed, observed)
        safe = self.safe_id(run_id)
        incident_dir = self.output / "incidents" / safe
        incident_dir.mkdir(parents=True, exist_ok=True)
        requested_relative = f"incidents/{safe}/requested-fault-configuration.json"
        observed_relative = f"incidents/{safe}/observed-fault-controller-state.json"
        binding_relative = f"incidents/{safe}/fault-controller-binding-audit.json"
        atomic_json(self.output / requested_relative, requested)
        atomic_json(self.output / observed_relative, observed.to_dict())
        atomic_json(self.output / binding_relative, binding)
        if binding["status"] != "PASS":
            raise RuntimeError(
                f"FAULT_CONTROLLER_BINDING=FAIL para {run_id}: {binding['violations']}"
            )
        events = list(result["events"])
        expected_services = ("order-service",) if name == "order_error" else (
            "order-service", "payment-service")
        audit = audit_events(events, correlation_id, expected_services)
        counts = audit["counts"]
        transport = audit_topic_offsets(result.get("kafka_offsets_before", {}),
                                        result.get("kafka_offsets_after", {}))
        reset_audit = audit_service_state_reset(result.get("service_state", {}),
                                                result["request_started_at"])
        usable = (audit["modality_status"] == "PASS"
                  and audit["identity_status"] == "PASS"
                  and audit["metric_pipeline_provenance"]["status"] == "PASS"
                  and transport["status"] == "PASS"
                  and reset_audit["status"] == "PASS"
                  and binding["status"] == "PASS"
                  and audit["anti_leakage"]["status"] == "PASS")
        integrity = "CLEAN" if usable else ("PARTIAL_BUT_USABLE" if result.get("usable") else "UNUSABLE")
        artifacts: dict[str, str] = {
            requested_relative: file_sha256(self.output / requested_relative),
            observed_relative: file_sha256(self.output / observed_relative),
            binding_relative: file_sha256(self.output / binding_relative),
        }
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
        ground_truth = ground_truth_from_observed_state(
            run_id=run_id, scenario_name=name, repeat_index=repeat_index,
            base_seed=self.base_seed, observed=observed, binding_audit=binding,
            fault_started_at=result["fault_started_at"],
            fault_stopped_at=result.get("fault_stopped_at"),
            request_id=request_metadata["request_id"], correlation_id=correlation_id,
            observed_state_sha256=artifacts[observed_relative],
            binding_audit_sha256=artifacts[binding_relative],
        )
        ground_relative = f"incidents/{safe}/ground-truth.json"
        atomic_json(self.output / ground_relative, ground_truth.to_dict())
        ground_hash = file_sha256(self.output / ground_relative)
        artifacts[ground_relative] = ground_hash
        request_relative = f"incidents/{safe}/request-metadata.json"
        atomic_json(self.output / request_relative, request_metadata)
        artifacts[request_relative] = file_sha256(self.output / request_relative)
        for filename, value in (
            ("kafka-offsets-before.json", result.get("kafka_offsets_before", {})),
            ("kafka-offsets-after.json", result.get("kafka_offsets_after", {})),
            ("kafka-topic-deltas.json", transport),
            ("service-state-reset.json", reset_audit),
        ):
            relative = f"incidents/{safe}/{filename}"
            atomic_json(self.output / relative, value)
            artifacts[relative] = file_sha256(self.output / relative)
        audit_relative = f"audits/{safe}.json"
        atomic_json(self.output / audit_relative, audit)
        artifacts[audit_relative] = file_sha256(self.output / audit_relative)
        manifest = self._manifest(run_id, name, ground_truth, result, counts,
                                  source_hashes, accounting, ground_hash, usable)
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
            "transport_audit": transport, "service_state_reset_audit": reset_audit,
            "fault_controller_binding_audit": binding,
            "resource_before": dict(resource_before), "resource_after": after,
        }
        atomic_json(self.checkpoint_path(run_id), checkpoint)
        return checkpoint

    def _manifest(self, run_id: str, name: str, ground_truth: GroundTruth,
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
            fault_type=ground_truth.root_cause_type,
            fault_service=ground_truth.root_cause_service,
            analysis_window=AnalysisWindow(start, end), modalities=observed,
            source_files_sha256=source_hashes, event_count_by_modality=counts,
            adapter_version=GENERATOR_VERSION,
            adapter_configuration_sha256=sha256_value({
                "scenario": name,
                "observed_fault_controller_state_sha256":
                    ground_truth.observed_fault_controller_state_sha256,
            }),
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
            "campaign_mode": self.mode,
            "repeat_target": 100, "workers": 1, "parallel_scenarios": False,
            "seed_policy": "sha256(base_seed|dataset|scenario|repeat_index)",
            "base_seed": self.base_seed,
            "telemetry_topics": ["traces-v3", "logs-v1", "metrics-v1",
                                 "business-events-v1", "multimodal-dead-letter-v1"],
            "metric_export_policy": {"transport": "OTLP through OpenTelemetry Collector",
                                     "export_interval_ms": 30000,
                                     "collection_timeout_seconds": 240,
                                     "allowlisted_series": [
                                         "http.server.request.duration",
                                         "http.client.request.duration",
                                         "jvm.cpu.recent_utilization"],
                                     "actuator_diagnostics_excluded": True},
            "services": ["order-service", "payment-service", "correlation-engine"],
            "fault_controller_version": "causalods-compose-fault-controller-v1",
            "business_event_origin": (
                "controlled experimental business-event emitter correlated with the real request"),
            "business_event_provenance": {
                "request": "real", "services": "real", "correlation_id": "real",
                "emitter": "controlled experimental harness",
                "transport": "business-events-v1 through multimodal ingestion",
            },
            "schemas": ["unified-event-v1", "business-event-v1", "incident-manifest-v2"],
        })
        atomic_json(self.output / "INDEX.json", {"count": len(index), "incidents": index})
        atomic_json(self.output / "capabilities.json",
                    {"count": len(capabilities), "capabilities": capabilities})
        atomic_json(self.output / "campaign-manifest.json", {
            "dataset_id": DATASET_ID, "target_runs": 500, "materialized_runs": len(index),
            "repeat_target_per_scenario": 100, "scenario_count": 5,
            "workers": 1, "parallel": False,
            "parallel_scenario_execution": False,
            "smoke_runs_included": False,
            "smoke_runs_excluded_from_final_by_default": True,
            "mode": self.mode,
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
