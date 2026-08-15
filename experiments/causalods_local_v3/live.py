from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "correlation-engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from .core import (
    Campaign, DATASET_ID, SCENARIOS, ObservedFaultControllerState, Scenario,
    atomic_json, requested_fault_configuration, utc_now,
    validate_fault_controller_binding,
)


DEFAULT_OUTPUT = ROOT / "artifacts" / "scientific-datasets-v3" / DATASET_ID / "smoke-v2"
FINAL_OUTPUT = ROOT / "artifacts" / "scientific-datasets-v3" / DATASET_ID / "final-500"
COMPOSE = ("docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
           "-f", str(ROOT / "docker-compose.causalods-local-v3.yml"))
REQUIRED_SERVICES = ("kafka", "opensearch", "otel-collector", "payment-service",
                     "order-service", "correlation-engine", "platform-v3-runtime")
SCIENTIFIC_COLLECTION_TIMEOUT_SECONDS = 240


def validate_run_mode(mode: str, scenario: str, repeat: int,
                      output: Path | None = None) -> Path:
    if mode == "smoke":
        if repeat != 1:
            raise ValueError("MODE=smoke exige repeat=1.")
        return Path(output) if output is not None else DEFAULT_OUTPUT
    if mode != "final":
        raise ValueError("Mode deve ser smoke ou final.")
    if repeat != 100 or scenario != "all":
        raise ValueError("MODE=final exige scenario=all e repeat=100.")
    destination = Path(output) if output is not None else FINAL_OUTPUT
    lowered = {part.lower() for part in destination.parts}
    if any(part.startswith("smoke") for part in lowered) or "promotion-validation" in lowered:
        raise ValueError("Campanha final exige output separado de smoke/validation.")
    return destination


def command(arguments: tuple[str, ...] | list[str], *, environment=None,
            check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True,
                               text=True, check=False, input=input_text)
    if check and completed.returncode:
        raise RuntimeError(f"Comando falhou: {' '.join(arguments)}\n{completed.stderr.strip()}")
    return completed


def request(url: str, *, payload: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None, timeout: float = 10,
            allow_error: bool = False) -> tuple[int, dict[str, str], Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    all_headers = {"Accept": "application/json", **dict(headers or {})}
    if data is not None:
        all_headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=all_headers,
                  method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                body = raw
            return response.status, dict(response.headers.items()), body
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        return exc.code, dict(exc.headers.items()), body


def wait_until(function, *, timeout: float, description: str) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = function()
            if last:
                return last
        except (OSError, HTTPError, URLError, RuntimeError, json.JSONDecodeError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    raise TimeoutError(f"Timeout aguardando {description}; último={last!r}")


def _http_ready(url: str) -> dict[str, Any] | None:
    status, _, body = request(url, timeout=2, allow_error=True)
    if status != 200:
        return None
    if isinstance(body, dict) and body.get("status") not in (None, "UP"):
        return None
    if url.endswith("/ready") and isinstance(body, dict) and body.get("ready") is not True:
        return None
    return body if isinstance(body, dict) else {"status": status}


def infrastructure(*, start: bool) -> dict[str, Any]:
    before = command((*COMPOSE, "ps", "--format", "json"), check=False)
    if start:
        command((*COMPOSE, "up", "-d", *REQUIRED_SERVICES))
    kafka = wait_until(
        lambda: command((*COMPOSE, "exec", "-T", "kafka",
                         "kafka-broker-api-versions", "--bootstrap-server", "localhost:9092"),
                        check=False).returncode == 0,
        timeout=120, description="Kafka readiness")
    opensearch = wait_until(lambda: _http_ready("http://localhost:9200"), timeout=120,
                            description="OpenSearch readiness")
    order = wait_until(lambda: _http_ready("http://localhost:8081/actuator/health"),
                       timeout=120, description="order-service readiness")
    payment = wait_until(lambda: _http_ready("http://localhost:8082/actuator/health"),
                         timeout=120, description="payment-service readiness")
    runtime = wait_until(lambda: _http_ready("http://localhost:8090/ready"), timeout=120,
                         description="correlation runtime readiness")
    processes = command((*COMPOSE, "ps", "--format", "json"))
    active = []
    for line in processes.stdout.splitlines():
        try:
            active.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    correlation_running = any(item.get("Service") == "correlation-engine"
                              and str(item.get("State", "")).lower() == "running"
                              for item in active)
    collector_running = any(item.get("Service") == "otel-collector"
                            and str(item.get("State", "")).lower() == "running"
                            for item in active)
    if not correlation_running:
        raise RuntimeError("correlation-engine não está running.")
    if not collector_running:
        raise RuntimeError("OpenTelemetry Collector não está running.")
    report = {
        "recorded_at": utc_now(), "expected_services": list(REQUIRED_SERVICES),
        "before_compose_ps": before.stdout, "active_containers": active,
        "ports_endpoints": {"kafka": "kafka:9092", "opensearch": "http://localhost:9200",
                            "otel_grpc": "localhost:4317", "otel_http": "localhost:4318",
                            "order": "http://localhost:8081/actuator/health",
                            "payment": "http://localhost:8082/actuator/health",
                            "correlation_runtime": "http://localhost:8090/ready"},
        "readiness": {"kafka": bool(kafka), "opensearch": bool(opensearch),
                      "otel-collector": collector_running,
                      "order-service": bool(order), "payment-service": bool(payment),
                      "correlation-engine": correlation_running,
                      "platform-v3-runtime": bool(runtime)},
    }
    return report


def configure_fault(scenario: Scenario, seed: int) -> tuple[str, dict[str, Any]]:
    environment = os.environ.copy()
    for prefix, service in (("ORDER", "order-service"), ("PAYMENT", "payment-service")):
        selected = scenario.target_service == service
        environment[f"CAUSALODS_{prefix}_FAULT_MODE"] = scenario.fault_type if selected else "none"
        environment[f"CAUSALODS_{prefix}_FAULT_RATE"] = str(scenario.rate if selected else 0.0)
        environment[f"CAUSALODS_{prefix}_FAULT_DELAY_MS"] = str(scenario.delay_ms if selected else 0)
        environment[f"CAUSALODS_{prefix}_FAULT_SEED"] = str(seed)
    started = utc_now()
    for service in ("payment-service", "order-service"):
        command((*COMPOSE, "up", "-d", "--force-recreate", "--no-deps", service),
                environment=environment)
        port = 8082 if service == "payment-service" else 8081
        wait_until(lambda port=port: _http_ready(f"http://localhost:{port}/actuator/health"),
                   timeout=120, description=f"{service} após fault reset/config")
    configured = {}
    for service in ("order-service", "payment-service"):
        result = command((*COMPOSE, "exec", "-T", service, "sh", "-lc",
                          "printf '%s|%s|%s|%s' \"$CAUSALODS_FAULT_MODE\" \"$CAUSALODS_FAULT_RATE\" \"$CAUSALODS_FAULT_DELAY_MS\" \"$CAUSALODS_FAULT_SEED\""))
        mode, rate, delay, observed_seed = result.stdout.split("|")
        container = command((*COMPOSE, "ps", "-q", service)).stdout.strip()
        inspection = command(("docker", "inspect", "--format",
                              "{{.Id}}|{{.State.StartedAt}}", container)).stdout.strip()
        container_id, container_started_at = inspection.split("|", 1)
        configured[service] = {"mode": mode, "rate": rate, "delay_ms": delay,
                               "seed": observed_seed, "container_id": container_id,
                               "container_started_at": container_started_at}
    return started, configured


def restore_faults(seed: int) -> str:
    configure_fault(SCENARIOS["healthy"], seed)
    return utc_now()


def publish_business_event(event_name: str, correlation_id: str, entity_id: str,
                           service_name: str, outcome: str) -> None:
    payload = {
        "event_id": f"clv3-be-{uuid.uuid4().hex}", "event_name": event_name,
        "event_timestamp": utc_now(), "correlation_id": correlation_id,
        "business_entity_type": event_name.split(".", 1)[0],
        "business_entity_id": entity_id, "service_name": service_name,
        "business_operation": event_name, "business_outcome": outcome,
        "attributes": {"controlled_dataset": DATASET_ID},
        "source_reference": "causalods-local-v3-business-controller",
        "source_dataset": DATASET_ID, "schema_version": "business-event-v1",
    }
    command((*COMPOSE, "exec", "-T", "kafka", "kafka-console-producer",
             "--bootstrap-server", "localhost:9092", "--topic", "business-events-v1"),
            input_text=json.dumps(payload, separators=(",", ":")) + "\n")


def _search_events(correlation_id: str, started_at: str, finished_at: str) -> list[dict[str, Any]]:
    query = {
        "size": 10000,
        "sort": [{"event_timestamp": "asc"}, {"event_id": "asc"}],
        "query": {"bool": {"should": [
            {"term": {"correlation_id": correlation_id}},
            {"bool": {"must": [
                {"term": {"signal_type": "metric"}},
                {"terms": {"service_name": ["order-service", "payment-service"]}},
                {"range": {"event_timestamp": {"gte": started_at, "lte": finished_at}}},
            ]}},
        ], "minimum_should_match": 1}},
    }
    status, _, body = request("http://localhost:9200/causalods-v3-unified-events/_search",
                              payload=query, timeout=20)
    if status != 200 or not isinstance(body, dict):
        return []
    events = []
    for hit in body.get("hits", {}).get("hits", []):
        event = dict(hit.get("_source", {}))
        for source, target in (("attributes_json", "attributes"),
                               ("resource_attributes_json", "resource_attributes"),
                               ("scope_json", "instrumentation_scope")):
            raw = event.pop(source, None)
            if isinstance(raw, str):
                try:
                    event[target] = json.loads(raw)
                except json.JSONDecodeError:
                    event[target] = {"unparsed": raw}
        events.append(event)
    return events


TELEMETRY_TOPICS = ("traces-v3", "logs-v1", "metrics-v1", "business-events-v1",
                    "multimodal-dead-letter-v1")


def kafka_topic_offsets() -> dict[str, dict[str, int]]:
    topics: dict[str, dict[str, int]] = {}
    for topic in TELEMETRY_TOPICS:
        result = command((*COMPOSE, "exec", "-T", "kafka", "kafka-get-offsets",
                          "--bootstrap-server", "localhost:9092", "--topic", topic),
                         check=False)
        offsets: dict[str, int] = {}
        for line in result.stdout.splitlines():
            parts = line.rsplit(":", 2)
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                offsets[parts[1]] = int(parts[2])
        topics[topic] = offsets
    return topics


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def persistent_resource_snapshot(output: Path) -> dict[str, Any]:
    probe = output if output.exists() else output.parent
    probe.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(probe)
    snapshot: dict[str, Any] = {
        "recorded_at": utc_now(), "filesystem_total_bytes": disk.total,
        "filesystem_free_bytes": disk.free, "filesystem_used_bytes": disk.used,
        "dataset_output_bytes": _directory_size(output),
    }
    status, _, indices = request(
        "http://localhost:9200/_cat/indices/causalods-v3-*?format=json&bytes=b",
        timeout=20, allow_error=True)
    if status == 200 and isinstance(indices, list):
        stores = {str(row.get("index")): int(row.get("store.size", 0)) for row in indices}
        snapshot["opensearch"] = {"available": True, "indices": stores,
                                  "total_bytes": sum(stores.values())}
    else:
        snapshot["opensearch"] = {"available": False, "total_bytes": None}
    kafka = command((*COMPOSE, "exec", "-T", "kafka", "sh", "-lc",
                     "du -sb /var/lib/kafka/data 2>/dev/null | cut -f1"), check=False)
    raw_kafka = kafka.stdout.strip()
    snapshot["kafka"] = {"available": raw_kafka.isdigit(),
                         "total_bytes": int(raw_kafka) if raw_kafka.isdigit() else None}
    docker_df = command(("docker", "system", "df", "--format", "json"), check=False)
    snapshot["docker_system_df"] = {"available": docker_df.returncode == 0,
                                    "raw": docker_df.stdout.strip()}
    log_total = 0
    log_count = 0
    for service in REQUIRED_SERVICES:
        container = command((*COMPOSE, "ps", "-q", service), check=False).stdout.strip()
        if not container:
            continue
        log_path = command(("docker", "inspect", "--format", "{{.LogPath}}", container),
                           check=False).stdout.strip()
        measured = command(("stat", "-c", "%s", log_path), check=False).stdout.strip()
        if measured.isdigit():
            log_total += int(measured)
            log_count += 1
    snapshot["docker_logs"] = {"available": log_count > 0,
                               "measured_containers": log_count,
                               "total_bytes": log_total if log_count else None}
    return snapshot


def _pipeline_status(correlation_id: str) -> dict[str, Any]:
    incident_query = {"size": 10, "query": {"term": {"correlation_ids": correlation_id}}}
    _, _, incidents = request(
        "http://localhost:9200/causalods-v3-incidents/_search",
        payload=incident_query, timeout=20, allow_error=True)
    hits = incidents.get("hits", {}).get("hits", []) if isinstance(incidents, dict) else []
    incident_ids = [item.get("_source", {}).get("incident_id") for item in hits]
    incident_ids = [item for item in incident_ids if item]
    if not incident_ids:
        return {"completed": False, "rca_result_emitted": False,
                "ranking_produced": False, "reliability_gate_executed": False}
    rca_query = {"size": 10, "query": {"terms": {"incident_id": incident_ids}}}
    _, _, rca = request("http://localhost:9200/causalods-v3-rca-results/_search",
                        payload=rca_query, timeout=20, allow_error=True)
    rca_hits = rca.get("hits", {}).get("hits", []) if isinstance(rca, dict) else []
    sources = [item.get("_source", {}) for item in rca_hits]
    return {"completed": bool(sources), "rca_result_emitted": bool(sources),
            "ranking_produced": any(item.get("ranked_root_causes") for item in sources),
            "reliability_gate_executed": any(item.get("reliable") is not None for item in sources),
            "incident_ids": incident_ids,
            "rca_result_ids": [item.get("rca_result_id") for item in sources]}


def execute_local_pipeline(events: list[Mapping[str, Any]], correlation_id: str) -> dict[str, Any]:
    """Runs the frozen v3 pipeline technically; its prediction is never a label."""
    from dataclasses import fields
    from opensearchpy import OpenSearch
    from multimodal_v1.contracts import UnifiedEvent
    from multimodal_v1.anomaly import DEFAULT_ANOMALY_POLICY
    from multimodal_v1.evidence import IncidentEvidenceAssembler
    from platform_v3.opensearch import OpenSearchProjectionRepository
    from platform_v3.operational_model import load_operational_model
    from platform_v3.runtime import RuntimeProcessor, TOPOLOGY
    from ranking.reliability_gate import ReliabilityGate

    allowed = {item.name for item in fields(UnifiedEvent)}
    normalized = []
    for raw in events:
        payload = {key: value for key, value in raw.items() if key in allowed}
        payload.setdefault("schema_version", "unified-event-v1")
        normalized.append(UnifiedEvent.from_dict(payload))
    if not any(DEFAULT_ANOMALY_POLICY.is_anomalous(event) for event in normalized):
        decision = ReliabilityGate().evaluate((), ())
        return {"completed": True, "rca_result_emitted": False,
                "ranking_produced": False, "reliability_gate_executed": True,
                "abstained": True,
                "abstention_reason": "no_anomaly_under_frozen_policy",
                "failed_reliability_rules": list(decision.failed_rules),
                "prediction_excluded_from_ground_truth": True}
    bundles = IncidentEvidenceAssembler(topology=TOPOLOGY).assemble(normalized)
    candidates = [bundle for bundle in bundles if any(
        event.correlation_id == correlation_id for event in bundle.events)]
    if not candidates:
        raise RuntimeError("Pipeline local não formou IncidentEvidenceBundle correlacionado.")
    bundle = max(candidates, key=lambda item: len(item.events))
    client = OpenSearch(hosts=[{"host": "localhost", "port": 9200}], use_ssl=False,
                        verify_certs=False, timeout=20)
    result = RuntimeProcessor(
        load_operational_model(), OpenSearchProjectionRepository(client)
    ).process(bundle)
    return {"completed": True, "rca_result_emitted": bool(result.get("rca_result_id")),
            "ranking_produced": True, "reliability_gate_executed": True,
            "incident_ids": [result.get("incident_id")],
            "rca_result_ids": [result.get("rca_result_id")],
            "prediction_excluded_from_ground_truth": True}


_PROMETHEUS_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
)
_RELEVANT_METRIC_PREFIXES = (
    "http_server_requests_", "jvm_memory_used_bytes", "jvm_threads_live_threads",
    "process_cpu_usage", "process_uptime_seconds", "system_cpu_usage",
)


def collect_real_metrics() -> list[dict[str, Any]]:
    """Pulls already existing Micrometer series; no synthetic values are created."""
    from multimodal_v1.contracts import UnifiedEvent

    events = []
    timestamp = utc_now()
    for service, port in (("order-service", 8081), ("payment-service", 8082)):
        status, _, body = request(
            f"http://localhost:{port}/actuator/prometheus", timeout=10,
            headers={"Accept": "text/plain;version=0.0.4"},
        )
        if status != 200 or not isinstance(body, str):
            continue
        selected = 0
        for line in body.splitlines():
            if line.startswith("#"):
                continue
            match = _PROMETHEUS_SAMPLE.fullmatch(line.strip())
            if not match or not match.group("name").startswith(_RELEVANT_METRIC_PREFIXES):
                continue
            value = float(match.group("value"))
            name = match.group("name")
            events.append(UnifiedEvent(
                event_id=f"clv3-metric-{service}-{uuid.uuid4().hex}",
                event_name=name, event_type="metric", signal_type="metric",
                event_timestamp=timestamp, service_name=service,
                metric_name=name, metric_value=value,
                attributes={"prometheus_labels": match.group("labels") or ""},
                resource_attributes={"service.name": service},
                source_reference=f"http://{service}:{port}/actuator/prometheus",
                source_dataset=DATASET_ID,
            ).to_dict())
            selected += 1
            if selected >= 64:
                break
    if not events:
        raise RuntimeError("Nenhuma série métrica operacional real disponível no Actuator.")
    return events


class LiveExecutor:
    def __call__(self, scenario: Scenario, seed: int, run_id: str,
                 correlation_id: str) -> Mapping[str, Any]:
        offsets_before = kafka_topic_offsets()
        fault_started, configured = configure_fault(scenario, seed)
        observed = ObservedFaultControllerState.from_mapping(configured)
        requested = requested_fault_configuration(scenario, seed)
        binding = validate_fault_controller_binding(scenario, seed, observed)
        if binding["status"] != "PASS":
            restore_faults(seed)
            raise RuntimeError(
                f"FAULT_CONTROLLER_BINDING=FAIL para {run_id}: {binding['violations']}"
            )
        # The scientific window starts only after the configured service
        # generations are ready. This excludes telemetry emitted by the
        # preceding run's healthy-reset containers.
        experiment_started = utc_now()
        request_id = f"req-{uuid.uuid4().hex}"
        entity_id = f"order-{uuid.uuid4().hex[:20]}"
        request_started = utc_now()
        product = f"controlled-{uuid.uuid4().hex[:16]}"
        publish_business_event("order.created", correlation_id, entity_id,
                               "order-service", "requested")
        if scenario.name != "order_error":
            publish_business_event("payment.requested", correlation_id, entity_id,
                                   "payment-service", "requested")
        monotonic = time.monotonic()
        status, headers, body = request(
            "http://localhost:8081/orders", payload={"product": product, "quantity": 1},
            headers={"X-Correlation-Id": correlation_id, "X-Request-Id": request_id},
            timeout=30, allow_error=True)
        request_finished = utc_now()
        duration = time.monotonic() - monotonic
        success = status // 100 == 2
        if scenario.name != "order_error":
            publish_business_event("payment.approved" if success else "payment.failed",
                                   correlation_id, entity_id, "payment-service",
                                   "approved" if success else "failed")
        publish_business_event("order.approved" if success else "order.rejected",
                               correlation_id, entity_id, "order-service",
                               "approved" if success else "rejected")
        fault_stopped = None
        try:
            events = wait_until(
                lambda: (rows if all(any(item.get("signal_type") == modality
                                             for item in rows)
                                     for modality in ("trace", "log", "metric",
                                                      "business_event"))
                         else None)
                if (rows := _search_events(correlation_id, experiment_started, utc_now())) else None,
                timeout=SCIENTIFIC_COLLECTION_TIMEOUT_SECONDS,
                description=f"quatro modalidades de {run_id}")
            time.sleep(10)
            events = _search_events(correlation_id, experiment_started, utc_now())
            collection_finished = utc_now()
            # Reset occurs strictly after the scientific window closes, so
            # metric evidence cannot come from the following healthy generation.
            fault_stopped = restore_faults(seed)
        except Exception:
            if fault_stopped is None:
                restore_faults(seed)
            raise
        offsets_after = kafka_topic_offsets()
        dlq_before = offsets_before.get("multimodal-dead-letter-v1", {})
        dlq_after = offsets_after.get("multimodal-dead-letter-v1", {})
        dlq_count = sum(max(0, dlq_after.get(partition, 0) - offset)
                        for partition, offset in dlq_before.items())
        pipeline = execute_local_pipeline(events, correlation_id)
        response_correlation = next((value for key, value in headers.items()
                                     if key.lower() == "x-correlation-id"), None)
        return {
            "events": events, "experiment_started_at": experiment_started,
            "request_started_at": request_started, "request_finished_at": request_finished,
            "collection_finished_at": collection_finished,
            "fault_started_at": fault_started, "fault_stopped_at": fault_stopped,
            "http_status": status, "dlq_count": dlq_count,
            "request_metadata": {"experiment_run_id": run_id, "request_id": request_id,
                                 "business.correlation.id": correlation_id,
                                 "request_payload": {"product": product, "quantity": 1},
                                 "response_correlation_id": response_correlation,
                                 "response_body_type": type(body).__name__},
            "timing": {"duration_seconds": duration,
                       "experiment_started_at": experiment_started,
                       "request_started_at": request_started,
                       "request_finished_at": request_finished,
                       "collection_finished_at": collection_finished},
            "fault_controller_state": configured,
            "requested_fault_configuration": requested,
            "observed_fault_controller_state": observed.to_dict(),
            "fault_controller_binding_audit": binding,
            "service_state": configured,
            "kafka_offsets_before": offsets_before,
            "kafka_offsets_after": offsets_after,
            "pipeline": pipeline,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="causalods-local-v3 controlled materializer")
    sub = parser.add_subparsers(dest="command", required=True)
    infra = sub.add_parser("infra")
    infra.add_argument("--start", action="store_true")
    infra.add_argument("--report", type=Path,
                       default=DEFAULT_OUTPUT / "audits" / "infrastructure.json")
    run = sub.add_parser("run")
    run.add_argument("--mode", choices=("smoke", "final"), required=True)
    run.add_argument("--output", type=Path)
    run.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--base-seed", type=int, default=20260814)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "infra":
        report = infrastructure(start=args.start)
        atomic_json(args.report, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "audit":
        from .audit import build_final_audit
        report = build_final_audit(args.output)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    args.output = validate_run_mode(args.mode, args.scenario, args.repeat, args.output)
    selected = tuple(SCENARIOS) if args.scenario == "all" else (args.scenario,)
    if not args.resume:
        atomic_json(args.output / "audits" / "resource-before.json",
                    persistent_resource_snapshot(args.output))
    report = Campaign(args.output, base_seed=args.base_seed, mode=args.mode,
                      executor=LiveExecutor()).run(selected, repeat=args.repeat,
                                                   resume=args.resume)
    if not args.resume or report["new_executions"] > 0:
        atomic_json(args.output / "audits" / "resource-after.json",
                    persistent_resource_snapshot(args.output))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0
