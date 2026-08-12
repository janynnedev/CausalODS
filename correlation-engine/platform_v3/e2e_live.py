from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from platform_v3.constants import ARTIFACT_ROOT, INDEX_NAMES, PLATFORM_VERSION, ROOT
from platform_v3.fault_e2e import (
    FAULT_E2E_CHECKSUM,
    FAULT_E2E_REPORT,
    FAULT_E2E_SCHEMA,
    STAGE_NAMES,
)
from platform_v3.operational_model import DEFAULT_MODELS_ROOT, operational_model_status
from platform_v3.serialization import canonical_json


COMPOSE = (
    "docker", "compose", "-f", str(ROOT / "docker-compose.yml"),
    "-f", str(ROOT / "docker-compose.platform-v3.yml"),
)
FORBIDDEN_GROUND_TRUTH = {
    "ground_truth", "fault_service", "fault_location", "is_root_cause",
    "actual_services", "root_cause_label", "target",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_commit() -> str:
    return subprocess.run(("git", "rev-parse", "HEAD"), cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def _request(
    url: str,
    *, payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    allow_error: bool = False,
    timeout: float = 15,
) -> tuple[int, dict[str, str], Any]:
    data = canonical_json(payload).encode() if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=request_headers,
                      method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw.strip() else None
            return response.status, dict(response.headers.items()), body
    except HTTPError as exception:
        raw = exception.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise
        try:
            body = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            body = raw
        return exception.code, dict(exception.headers.items()), body


def _search(index: str, field: str, value: str) -> list[dict[str, Any]]:
    payload = {"size": 100, "query": {"term": {field: value}}}
    _, _, body = _request(f"http://localhost:9200/{index}/_search", payload=payload)
    return [item["_source"] for item in body.get("hits", {}).get("hits", [])]


def _wait_until(function, *, timeout: float, description: str, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = function()
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(f"Timeout aguardando {description}; último valor={last!r}")


def _configure_fault(enabled: bool) -> None:
    environment = os.environ.copy()
    environment.update({
        "CAUSALODS_ORDER_FAULT_MODE": "none",
        "CAUSALODS_ORDER_FAULT_RATE": "0.0",
        "CAUSALODS_ORDER_FAULT_DELAY_MS": "0",
        "CAUSALODS_ORDER_FAULT_SEED": "42",
        "CAUSALODS_PAYMENT_FAULT_MODE": "error" if enabled else "none",
        "CAUSALODS_PAYMENT_FAULT_RATE": "1.0" if enabled else "0.0",
        "CAUSALODS_PAYMENT_FAULT_DELAY_MS": "0",
        "CAUSALODS_PAYMENT_FAULT_SEED": "42",
    })
    for service in ("payment-service", "order-service"):
        completed = subprocess.run(
            (*COMPOSE, "up", "-d", "--force-recreate", "--no-deps", service),
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise RuntimeError(f"Falha ao configurar {service}: {completed.stderr.strip()}")
        port = 8082 if service == "payment-service" else 8081
        _wait_until(
            lambda: _service_ready(port), timeout=90,
            description=f"{service} pronto após fault config",
        )


def _service_ready(port: int) -> bool:
    try:
        status, _, _ = _request(f"http://localhost:{port}/actuator/health", timeout=2)
        return status == 200
    except (OSError, URLError, HTTPError, json.JSONDecodeError):
        return False


def _runtime_health() -> dict[str, Any] | None:
    try:
        status, _, body = _request("http://localhost:8090/health", timeout=3)
        return body if status == 200 and isinstance(body, dict) else None
    except (OSError, URLError, HTTPError, json.JSONDecodeError):
        return None


def _find_trace_id(correlation_id: str) -> tuple[str, list[dict[str, Any]]] | None:
    events = _search(INDEX_NAMES["unified_events"], "correlation_id", correlation_id)
    trace_ids = sorted({item.get("trace_id") for item in events if item.get("trace_id")})
    return (trace_ids[0], events) if trace_ids else None


def _tempo(trace_id: str) -> tuple[bool, bool]:
    try:
        direct = _request(f"http://localhost:3200/api/traces/{trace_id}", timeout=10)[0] == 200
        proxy = _request(
            f"http://localhost:3000/api/datasources/proxy/uid/causalods-tempo/api/traces/{trace_id}",
            timeout=10,
        )[0] == 200
        return direct, proxy
    except (OSError, URLError, HTTPError, json.JSONDecodeError):
        return False, False


def _grafana_query(incident_id: str) -> bool:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload = {
        "from": str(now_ms - 3_600_000), "to": str(now_ms + 60_000),
        "queries": [{
            "refId": "A",
            "datasource": {"uid": "causalods-v3-incidents", "type": "grafana-opensearch-datasource"},
            "query": f'incident_id:"{incident_id}"',
            "queryType": "lucene", "luceneQueryType": "Metric",
            "timeField": "analysis_window_start",
            "metrics": [{"id": "1", "type": "count"}],
            "bucketAggs": [{"id": "2", "type": "date_histogram", "field": "analysis_window_start",
                            "settings": {"interval": "auto", "min_doc_count": "0"}}],
            "intervalMs": 1000, "maxDataPoints": 1000,
        }],
    }
    try:
        status, _, body = _request("http://localhost:3000/api/ds/query", payload=payload)
        result = body.get("results", {}).get("A", {}) if isinstance(body, dict) else {}
        return status == 200 and not result.get("error") and bool(result.get("frames"))
    except (OSError, URLError, HTTPError, json.JSONDecodeError):
        return False


def _forbidden_keys(value: Any, path: str = "$") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_GROUND_TRUTH:
                found.append(current)
            found.extend(_forbidden_keys(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_keys(item, f"{path}[{index}]"))
    return found


class Stages:
    def __init__(self) -> None:
        self.values: list[dict[str, Any]] = []

    def pass_(self, number: int, identifiers: dict[str, Any]) -> None:
        if number != len(self.values) + 1:
            raise RuntimeError("Estágios E2E devem ser registrados sequencialmente.")
        self.values.append({
            "stage": number,
            "name": STAGE_NAMES[number - 1],
            "status": "PASS",
            "timestamp": _now(),
            "identifiers": identifiers,
            "reason": None,
        })

    def fail(self, number: int, reason: str, identifiers: dict[str, Any] | None = None) -> None:
        while len(self.values) + 1 < number:
            missing = len(self.values) + 1
            self.values.append({
                "stage": missing, "name": STAGE_NAMES[missing - 1], "status": "FAIL",
                "timestamp": _now(), "identifiers": {}, "reason": "blocked_by_prior_stage",
            })
        self.values.append({
            "stage": number, "name": STAGE_NAMES[number - 1], "status": "FAIL",
            "timestamp": _now(), "identifiers": identifiers or {}, "reason": reason,
        })


def run_fault_e2e(
    output: Path = ARTIFACT_ROOT,
    *, scenario: str = "payment_failure",
    timeout: float = 120,
) -> dict[str, Any]:
    if scenario != "payment_failure":
        raise ValueError("Prompt 12 aprova somente o fault E2E payment_failure.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    model_status = operational_model_status(DEFAULT_MODELS_ROOT)
    if not model_status["valid"]:
        raise RuntimeError(f"Modelo operacional inválido: {model_status['reason']}")
    manifest = model_status["manifest"]
    health_before = _wait_until(
        lambda: (value if (value := _runtime_health()) and value.get("ready") else None),
        timeout=60,
        description="runtime v3 READY",
    )
    correlation_id = f"platform-v3-e2e-{uuid.uuid4().hex[:20]}"
    stages = Stages()
    trace_id = incident_id = rca_result_id = explanation_id = None
    events: list[dict[str, Any]] = []
    incident_docs: list[dict[str, Any]] = []
    rca_docs: list[dict[str, Any]] = []
    explanation_docs: list[dict[str, Any]] = []
    grafana_ok = tempo_direct = tempo_proxy = restored = False
    error: str | None = None
    try:
        _configure_fault(True)
        stages.pass_(1, {"service": "payment-service", "mode": "error", "rate": 1.0})
        status, response_headers, response = _request(
            "http://localhost:8081/orders",
            payload={"product": f"e2e-payment-failure-{correlation_id}", "quantity": 2},
            headers={"X-Correlation-Id": correlation_id},
            allow_error=True,
            timeout=20,
        )
        if status // 100 != 5:
            raise RuntimeError(f"fault payment_failure não produziu HTTP 5xx: {status}")
        stages.pass_(2, {"http_status": status, "response_type": type(response).__name__})
        observed_correlation = next(
            (value for key, value in response_headers.items() if key.lower() == "x-correlation-id"),
            None,
        )
        if observed_correlation != correlation_id:
            raise RuntimeError("correlation_id da resposta diverge do request")
        stages.pass_(3, {"correlation_id": correlation_id})
        trace_id, events = _wait_until(
            lambda: _find_trace_id(correlation_id), timeout=timeout,
            description="telemetria correlacionada e trace_id no runtime",
        )
        stages.pass_(4, {"trace_id": trace_id, "correlation_id": correlation_id})
        modalities = sorted({item.get("signal_type") for item in events if item.get("signal_type")})
        stages.pass_(5, {"event_count": len(events), "modalities": modalities,
                         "collector_topics": ["traces-v3", "logs-v1", "metrics-v1"]})
        health_after = _runtime_health() or {}
        if health_after.get("consumed_events", 0) <= health_before.get("consumed_events", 0):
            raise RuntimeError("contador de eventos consumidos não avançou")
        stages.pass_(6, {"consumed_before": health_before.get("consumed_events"),
                         "consumed_after": health_after.get("consumed_events")})
        incident_docs = _wait_until(
            lambda: _search(INDEX_NAMES["incidents"], "correlation_ids", correlation_id),
            timeout=timeout,
            description="IncidentEvidenceBundle persistido",
        )
        incident = incident_docs[0]
        incident_id = incident["incident_id"]
        stages.pass_(7, {"incident_id": incident_id, "event_count": incident["event_count"]})
        if not incident.get("graph_node_count") or not incident.get("graph_edge_count"):
            raise RuntimeError("EventEvidenceGraph persistido sem nós/arestas")
        stages.pass_(8, {"graph_node_count": incident["graph_node_count"],
                         "graph_edge_count": incident["graph_edge_count"]})
        rca_docs = _wait_until(
            lambda: _search(INDEX_NAMES["rca_results"], "incident_id", incident_id),
            timeout=timeout,
            description="RCAResult persistido",
        )
        rca = rca_docs[0]
        rca_result_id = rca["rca_result_id"]
        if rca.get("feature_registry_hash") != manifest["feature_registry_sha256"]:
            raise RuntimeError("feature registry do RCA diverge do modelo operacional")
        stages.pass_(9, {"feature_registry_sha256": rca["feature_registry_hash"],
                         "feature_view_sha256": manifest["feature_view_sha256"]})
        if rca.get("model_hash") != manifest["artifact_sha256"]:
            raise RuntimeError("model hash do RCA diverge do artefato")
        stages.pass_(10, {"model_name": manifest["model_name"],
                          "model_version": rca["model_version"], "model_sha256": rca["model_hash"]})
        stages.pass_(11, {"rca_result_id": rca_result_id,
                          "root_cause_service": rca["root_cause_service"],
                          "confidence": rca["confidence"], "reliable": rca["reliable"]})
        explanation_docs = _wait_until(
            lambda: _search(INDEX_NAMES["explanations"], "incident_id", incident_id),
            timeout=timeout,
            description="explicação persistida",
        )
        explanation = explanation_docs[0]
        explanation_id = explanation["explanation_id"]
        stages.pass_(12, {"explanation_id": explanation_id, "fallback": explanation["fallback"],
                          "evidence_reference_count": len(explanation.get("evidence_references", []))})
        linked_events = _search(INDEX_NAMES["unified_events"], "incident_id", incident_id)
        if not linked_events or not incident_docs or not rca_docs or not explanation_docs:
            raise RuntimeError("documentos operacionais não estão integralmente vinculados")
        stages.pass_(13, {"events": len(linked_events), "incidents": len(incident_docs),
                          "rca_results": len(rca_docs), "explanations": len(explanation_docs)})
        stages.pass_(14, {"incident_id": incident_id, "query_hits": len(incident_docs)})
        stages.pass_(15, {"rca_result_id": rca_result_id, "query_hits": len(rca_docs)})
        stages.pass_(16, {"explanation_id": explanation_id, "query_hits": len(explanation_docs)})
        grafana_ok = _grafana_query(incident_id)
        if not grafana_ok:
            raise RuntimeError("query Grafana backend por incident_id falhou")
        stages.pass_(17, {"incident_id": incident_id, "datasource_uid": "causalods-v3-incidents"})
        tempo_direct, tempo_proxy = _wait_until(
            lambda: (value if all(value := _tempo(trace_id)) else None),
            timeout=timeout,
            description="trace direto no Tempo e via proxy Grafana",
        )
        stages.pass_(18, {"trace_id": trace_id, "tempo_direct": tempo_direct,
                          "grafana_tempo_proxy": tempo_proxy})
    except Exception as exception:
        error = f"{type(exception).__name__}: {exception}"
        next_stage = min(len(stages.values) + 1, 18)
        if next_stage <= 18:
            stages.fail(next_stage, error)
    finally:
        try:
            _configure_fault(False)
            restored = True
        except Exception as exception:
            error = error or f"{type(exception).__name__}: {exception}"
        if len(stages.values) < 19:
            if len(stages.values) < 18:
                stages.fail(18, "blocked_by_prior_stage")
            if restored:
                stages.pass_(19, {"payment_service_mode": "none", "order_service_mode": "none"})
            else:
                stages.fail(19, "fault restoration failed")

    operational_documents = [*events, *incident_docs, *rca_docs, *explanation_docs]
    forbidden = _forbidden_keys(operational_documents)
    predicted = rca_docs[0].get("root_cause_service") if rca_docs else None
    top1 = predicted == "payment-service"
    success = (
        len(stages.values) == 19
        and all(item["status"] == "PASS" for item in stages.values)
        and top1 and not forbidden and restored
    )
    report = {
        "schema_version": FAULT_E2E_SCHEMA,
        "platform_version": PLATFORM_VERSION,
        "generated_at": _now(),
        "repository_commit": _git_commit(),
        "execution_mode": "live",
        "scenario": scenario,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
        "incident_id": incident_id,
        "rca_result_id": rca_result_id,
        "explanation_id": explanation_id,
        "model": {
            "model_name": manifest["model_name"],
            "model_version": manifest["model_version"],
            "model_sha256": manifest["artifact_sha256"],
            "feature_registry_sha256": manifest["feature_registry_sha256"],
            "feature_view_sha256": manifest["feature_view_sha256"],
        },
        "stages": stages.values,
        "runtime_evidence": {
            "runtime_ready": bool((_runtime_health() or {}).get("ready")),
            "telemetry_real": bool(events),
            "model_inference_real": bool(rca_docs and rca_docs[0].get("model_hash") == manifest["artifact_sha256"]),
            "opensearch_links_verified": bool(events and incident_docs and rca_docs and explanation_docs),
            "grafana_query_passed": grafana_ok,
            "tempo_direct_passed": tempo_direct,
            "tempo_grafana_proxy_passed": tempo_proxy,
            "fault_restored": restored,
            "observed_modalities": sorted({item.get("signal_type") for item in events if item.get("signal_type")}),
            "business_event_live_required": False,
        },
        "evaluation": {
            "predicted_root_cause": predicted,
            "expected_root_cause": "payment-service",
            "top1_correct": top1,
        },
        "leakage_check": {
            "passed": not forbidden,
            "forbidden_fields_found": forbidden,
            "scope": "operational OpenSearch projections only; expected label remains in evaluation",
        },
        "error": error,
        "status": "SUCCESS" if success else "FAILED",
    }
    report_path = output / FAULT_E2E_REPORT
    checksum_path = output / FAULT_E2E_CHECKSUM
    report_path.write_text(canonical_json(report) + "\n", encoding="utf-8")
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    checksum_path.write_text(f"{digest}  {FAULT_E2E_REPORT}\n", encoding="utf-8")
    return report
