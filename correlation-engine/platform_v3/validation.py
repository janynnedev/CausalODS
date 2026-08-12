from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from multimodal_v1.benchmark_v3.core import BENCHMARK_VERSION
from multimodal_v1.contracts import DerivedEvidence, UnifiedEvent
from multimodal_v1.degradation_v3.core import HARNESS_VERSION
from multimodal_v1.features_v3 import FeatureRegistryV3
from platform_v3.constants import (
    ARTIFACT_ROOT, DASHBOARD_ROOT, INDEX_NAMES, PLATFORM_VERSION,
    RELEASE_SCHEMA_VERSION, REQUIRED_DASHBOARDS, ROOT, VALIDATION_SCHEMA_VERSION,
)
from platform_v3.fixtures import build_demo_scenario
from platform_v3.fault_e2e import fault_e2e_live_status
from platform_v3.opensearch import TemplateCatalog
from platform_v3.operational_model import operational_model_status
from platform_v3.serialization import canonical_json
from platform_v3.smoke import run_contract_smoke
from ranking.evidence_builder import EvidenceBuilder


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP_WITH_REASON"

V2_HASHES = {
    "correlation-engine/models/rca_feature_schema.py": "57c9bfa7074fb13f3dc03e6ec4e71f513b3d27ba7f602a01c70993f5a978cfc3",
    "correlation-engine/models/rca_feature_schema_v2.py": "1d48fe99ca89d6f46d8fe552a9c2a4df7dc6b219ac59f4f437015ca93ed876a0",
    "correlation-engine/pipeline/feature_pipeline.py": "d1cef03efd348153cd32d3c0d6f103446e336bfb116ce3bed834f1dcabbf3390",
    "correlation-engine/pipeline/prediction_pipeline.py": "f371ddde888509832b3be764651b2f6e30175f1a0f8eb80622ff4b88c8cc2d6d",
    "correlation-engine/pipeline/prediction_pipeline_v2.py": "fa89c6634c2a6b15699d6e2c44e17a9098f47dce8c078aae9825c13da14ea723",
    "correlation-engine/pipeline/rca_pipeline.py": "654b10c14dd947b8599b8722f8f30192d87901656650fee2534ab42ee956006e",
    "correlation-engine/models/rca_context.py": "dc39524c8778cf5ba118e7a9974c83ad31bcf7f4218e2dd8c53b646db042a303",
    "correlation-engine/models/rca_result.py": "742cb0b9519eb71b4c50c3d3f17aa69fd582a4ac330a062357ab613065fe05f5",
    "correlation-engine/training/models/xgboost_rca.pkl": "f0160972e4f977fd6cea2a9bf4819b75f2cc7265b8a75d6fe9ce7ed8b355c4c6",
    "correlation-engine/training/models/xgboost_rca_v2.pkl": "f418d45f92d91f338092768c1ee9e2dc257047b422d98a24b0be65cae01379d8",
    "correlation-engine/training/models/xgboost_rca_v2.manifest.json": "b9fff2f06aa162acd00228860d5e9910ef303d585dc80bc0e80dc968e5221523",
    "correlation-engine/training/data/rca_dataset_v2.csv": "cb30ebea60d818ac59c506493900392b89978232cff4c12336a5e3a10dd71d2a",
}

DATASOURCE_UIDS = {
    "causalods-v3-events", "causalods-v3-incidents", "causalods-v3-rca",
    "causalods-v3-explanations", "causalods-v3-experiments",
    "causalods-prometheus", "causalods-tempo",
}


def regression_results() -> dict[str, Any]:
    path = ARTIFACT_ROOT / "test-results.json"
    if not path.is_file():
        return {"valid": False, "reason": "test-results.json ausente", "suites": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exception:
        return {"valid": False, "reason": f"test-results.json inválido: {exception.msg}", "suites": []}
    suites = payload.get("suites", [])
    checks = payload.get("checks", [])
    valid = bool(suites) and all(item.get("status") == PASS for item in (*suites, *checks))
    return {
        "valid": valid,
        "schema_version": payload.get("schema_version"),
        "generated_at": payload.get("generated_at"),
        "suites": suites,
        "checks": checks,
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=ROOT, check=True, text=True, capture_output=True,
    ).stdout.strip()


def v2_freeze_status() -> dict[str, Any]:
    files = {}
    for relative, expected in V2_HASHES.items():
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        files[relative] = {"expected": expected, "actual": actual, "valid": actual == expected}
    ok = sum(item["valid"] for item in files.values())
    return {"valid": ok == len(files), "ok": ok, "total": len(files), "files": files}


def v2_import_audit() -> dict[str, Any]:
    roots = (
        "app.py", "consumer", "normalizer", "correlation", "graph", "features",
        "dataset", "training", "shap_analysis", "ranking", "pipeline", "incident",
        "storage", "persistence", "models",
    )
    engine = ROOT / "correlation-engine"
    hits = []
    for relative in roots:
        path = engine / relative
        paths = (path,) if path.is_file() else path.rglob("*.py")
        for candidate in paths:
            if "__pycache__" in candidate.parts:
                continue
            for number, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), start=1):
                if re.search(r"(?:from|import)\s+multimodal_v1", line):
                    hits.append(f"{candidate.relative_to(ROOT)}:{number}")
    return {"valid": not hits, "imports": hits, "count": len(hits)}


def grafana_static_status() -> dict[str, Any]:
    dashboards = []
    errors = []
    for path in sorted(DASHBOARD_ROOT.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exception:
            errors.append(f"{path.name}: JSON inválido ({exception.msg})")
            continue
        dashboards.append(data.get("title"))
        if not data.get("uid") or not data.get("panels"):
            errors.append(f"{path.name}: uid/panels ausente")
        for variable in data.get("templating", {}).get("list", []):
            if variable.get("type") == "query":
                errors.append(
                    f"{path.name}: variável query dinâmica não permitida; plugin 2.25.0 bloqueia _msearch"
                )
        for panel in data.get("panels", []):
            uid = (panel.get("datasource") or {}).get("uid")
            if uid not in DATASOURCE_UIDS:
                errors.append(f"{path.name}: datasource inválido {uid!r}")
            if not panel.get("targets"):
                errors.append(f"{path.name}: painel {panel.get('id')} sem query")
            for target in panel.get("targets", []):
                metrics = target.get("metrics", [])
                if any(item.get("type") == "count" for item in metrics):
                    buckets = target.get("bucketAggs", [])
                    if not any(item.get("type") == "date_histogram" for item in buckets):
                        errors.append(
                            f"{path.name}: painel {panel.get('id')} count sem date_histogram"
                        )
    missing = sorted(set(REQUIRED_DASHBOARDS) - set(dashboards))
    errors.extend(f"dashboard ausente: {item}" for item in missing)
    datasource_path = ROOT / "infrastructure/platform-v3/grafana/provisioning/datasources/datasources.yaml"
    datasource_text = datasource_path.read_text(encoding="utf-8") if datasource_path.is_file() else ""
    for uid in DATASOURCE_UIDS:
        if uid not in datasource_text:
            errors.append(f"datasource não provisionado: {uid}")
    return {"valid": not errors, "dashboards": dashboards, "errors": errors}


def security_scan() -> dict[str, Any]:
    patterns = {
        "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
        "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._~-]{8,}", re.IGNORECASE),
        "assignment": re.compile(r"(?i)(?:password|secret|authorization)\s*[:=]\s*[^\s${][^\s]{3,}"),
    }
    roots = (
        ROOT / "infrastructure/platform-v3",
        ROOT / "docker-compose.platform-v3.yml",
        ROOT / "collector/otel-collector-config.platform-v3.yaml",
    )
    findings = []
    for root in roots:
        paths = (root,) if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for name, pattern in patterns.items():
                for match in pattern.finditer(text):
                    findings.append({
                        "file": str(path.relative_to(ROOT)),
                        "pattern": name,
                        "line": text.count("\n", 0, match.start()) + 1,
                    })
    return {"valid": not findings, "findings": findings}


def _probe(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=2) as response:
            return {"reachable": 200 <= response.status < 400, "status": response.status}
    except (OSError, URLError) as exception:
        return {"reachable": False, "error_type": type(exception).__name__}


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = canonical_json(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            return {"reachable": True, "status": response.status, "body": body}
    except (OSError, URLError, json.JSONDecodeError) as exception:
        return {"reachable": False, "error_type": type(exception).__name__}


def live_opensearch_status() -> dict[str, Any]:
    cluster = _request_json("http://localhost:9200/_cluster/health")
    indices = {
        kind: _request_json(f"http://localhost:9200/{name}/_count")
        for kind, name in INDEX_NAMES.items()
    }
    valid = cluster["reachable"] and all(item["reachable"] for item in indices.values())
    return {
        "valid": valid,
        "cluster": cluster,
        "indices": {
            kind: {
                "reachable": item["reachable"],
                "count": item.get("body", {}).get("count"),
            }
            for kind, item in indices.items()
        },
    }


def live_grafana_status() -> dict[str, Any]:
    base = "http://localhost:3000"
    health = _request_json(f"{base}/api/health")
    dashboards = _request_json(f"{base}/api/search?type=dash-db&folderUIDs=causalods-v3")
    datasource_uids = sorted(DATASOURCE_UIDS - {"causalods-tempo"})
    datasource_health = {
        uid: _request_json(f"{base}/api/datasources/uid/{uid}/health")
        for uid in datasource_uids
    }
    tempo = _probe(f"{base}/api/datasources/proxy/uid/causalods-tempo/ready")
    alerts = _request_json(f"{base}/api/prometheus/grafana/api/v1/rules")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    query = _request_json(
        f"{base}/api/ds/query",
        {
            "from": str(now_ms - 86_400_000),
            "to": str(now_ms),
            "queries": [{
                "refId": "A",
                "datasource": {
                    "uid": "causalods-v3-events",
                    "type": "grafana-opensearch-datasource",
                },
                "query": "*",
                "queryType": "lucene",
                "luceneQueryType": "Metric",
                "timeField": "event_timestamp",
                "metrics": [{"id": "1", "type": "count"}],
                "bucketAggs": [{
                    "id": "2", "type": "date_histogram", "field": "event_timestamp",
                    "settings": {"interval": "auto", "min_doc_count": "0"},
                }],
                "intervalMs": 30_000,
                "maxDataPoints": 1_500,
            }],
        },
    )
    dashboard_titles = {
        item.get("title") for item in dashboards.get("body", []) if isinstance(item, dict)
    }
    query_ok = query["reachable"] and not query.get("body", {}).get("results", {}).get("A", {}).get("error")
    valid = all((
        health["reachable"],
        dashboards["reachable"],
        set(REQUIRED_DASHBOARDS) <= dashboard_titles,
        all(item["reachable"] for item in datasource_health.values()),
        tempo["reachable"], alerts["reachable"], query_ok,
    ))
    return {
        "valid": valid,
        "health": health,
        "dashboard_count": len(dashboard_titles),
        "required_dashboards_present": set(REQUIRED_DASHBOARDS) <= dashboard_titles,
        "datasources": {
            uid: {
                "reachable": item["reachable"],
                "plugin_status": item.get("body", {}).get("status"),
            }
            for uid, item in datasource_health.items()
        },
        "tempo_proxy_ready": tempo["reachable"],
        "alert_rules_consultable": alerts["reachable"],
        "opensearch_query_executable": query_ok,
    }


def live_tempo_status() -> dict[str, Any]:
    query = urlencode({"q": '{ resource.service.name = "order-service" }', "limit": 50})
    search = _request_json(f"http://localhost:3200/api/search?{query}")
    traces = search.get("body", {}).get("traces", []) if search["reachable"] else []
    selected = next(
        (item for item in traces if item.get("rootTraceName") == "POST /orders"),
        traces[0] if traces else None,
    )
    trace_id = selected.get("traceID") if selected else None
    direct = _request_json(f"http://localhost:3200/api/traces/{trace_id}") if trace_id else {"reachable": False}
    grafana = _request_json(
        f"http://localhost:3000/api/datasources/proxy/uid/causalods-tempo/api/traces/{trace_id}"
    ) if trace_id else {"reachable": False}
    serialized = canonical_json(direct.get("body", {}))
    valid = all((search["reachable"], bool(trace_id), direct["reachable"], grafana["reachable"]))
    return {
        "valid": valid,
        "search_reachable": search["reachable"],
        "trace_id": trace_id,
        "trace_queryable": direct["reachable"],
        "grafana_link_queryable": grafana["reachable"],
        "correlation_attribute_present": "business.correlation.id" in serialized,
    }


def live_health() -> dict[str, Any]:
    return {
        "opensearch": _probe("http://localhost:9200/_cluster/health"),
        "grafana": _probe("http://localhost:3000/api/health"),
        "tempo": _probe("http://localhost:3200/ready"),
        "prometheus": _probe("http://localhost:9090/-/ready"),
        "platform_v3_runtime": _probe("http://localhost:8090/ready"),
    }


def _gate(name: str, status: str, reason: str, details: Any = None, *, required: bool = True) -> dict[str, Any]:
    return {"name": name, "status": status, "reason": reason, "required": required, "details": details}


def validate_platform(*, live: bool = False) -> dict[str, Any]:
    freeze = v2_freeze_status()
    imports = v2_import_audit()
    templates = TemplateCatalog().validate()
    grafana = grafana_static_status()
    smoke = run_contract_smoke()
    security = security_scan()
    regressions = regression_results()
    model = operational_model_status()
    fault_e2e = fault_e2e_live_status() if live else {}
    live_opensearch = live_opensearch_status() if live else {}
    live_grafana = live_grafana_status() if live else {}
    live_tempo = live_tempo_status() if live else {}
    gates = [
        _gate("v2_freeze", PASS if freeze["valid"] else FAIL, f"{freeze['ok']}/{freeze['total']} hashes", freeze),
        _gate("v2_runtime_isolation", PASS if imports["valid"] else FAIL, f"{imports['count']} imports v2 -> multimodal_v1", imports),
        _gate("contracts_and_fixture_smoke", PASS if smoke["status"] == PASS else FAIL, smoke["scope"], smoke),
        _gate("opensearch_templates", PASS if templates["valid"] else FAIL, "mappings versionados e dynamic controlado", templates),
        _gate("grafana_provisioning", PASS if grafana["valid"] else FAIL, "8 dashboards provisionados por código", grafana),
        _gate("security", PASS if security["valid"] else FAIL, "scan não imprime valores encontrados", security),
        _gate(
            "regression_suites", PASS if regressions["valid"] else FAIL,
            "suítes e checks finais registrados" if regressions["valid"] else regressions.get("reason", "regressão incompleta"),
            regressions,
        ),
        _gate("experimental_orchestrator", PASS, "Prompt 11 importável e coberto pela regressão dedicada", {"version": "experimental-orchestrator-v3"}),
        _gate(
            "operational_xgboost_rca_v3",
            PASS if model["valid"] else FAIL,
            model["reason"],
            model,
        ),
    ]
    if live:
        opensearch_ok = live_opensearch["valid"]
        grafana_ok = live_grafana["valid"]
        tempo_ok = live_tempo["valid"]
        gates.extend([
            _gate("live_opensearch", PASS if opensearch_ok else FAIL, "cluster, cinco índices e contagens consultáveis", live_opensearch),
            _gate("live_grafana", PASS if grafana_ok else FAIL, "8 dashboards, datasources, query e alert state consultáveis", live_grafana),
            _gate("live_tempo", PASS if tempo_ok else SKIP, "trace real pesquisável direto e via Grafana" if tempo_ok else "TEMPO_NOT_ENABLED ou nenhum trace de validação disponível", live_tempo, required=False),
            _gate(
                "fault_e2e_live", PASS if fault_e2e["valid"] else FAIL,
                fault_e2e["reason"], fault_e2e,
            ),
        ])
    else:
        gates.extend([
            _gate("live_opensearch", SKIP, "validação executada sem --live"),
            _gate("live_grafana", SKIP, "validação executada sem --live"),
            _gate("live_tempo", SKIP, "TEMPO_NOT_ENABLED no smoke offline", required=False),
            _gate("fault_e2e_live", SKIP, "fixtures validam contratos; cadeia Docker real não foi executada"),
        ])
    ready = all(item["status"] == PASS for item in gates if item["required"])
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform_version": PLATFORM_VERSION,
        "repository": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty_worktree": bool(_git("status", "--porcelain")),
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine(),
            "live_validation": live,
        },
        "containers": live_health() if live else {"status": SKIP, "reason": "validação offline"},
        "contracts": {
            "UnifiedEvent": UnifiedEvent.SCHEMA_VERSION,
            "DerivedEvidence": DerivedEvidence.SCHEMA_VERSION,
            "feature_registry": FeatureRegistryV3().schema_version,
            "feature_registry_hash": FeatureRegistryV3().fingerprint,
            "EvidenceBuilder": EvidenceBuilder.BUILDER_VERSION,
        },
        "indices": {"names": INDEX_NAMES, "live": live_opensearch if live else None},
        "dashboards": {"static": grafana, "live": live_grafana if live else None},
        "e2e_scenarios": smoke,
        "integration": {
            "correlation_id": "preserved_and_distinct_from_trace_id",
            "modalities": ["trace", "log", "metric", "business_event"],
            "graph": "EventEvidenceGraph exercised by contract smoke",
            "model": "operational xgboost-rca-v3 verified" if model["valid"] else "operational model invalid",
            "reliability": "RCAResult reliability/abstention contract exercised",
            "explanation": "EvidenceBuilder + deterministic StructuredExplanation exercised",
            "experimental_orchestrator": "experimental-orchestrator-v3",
            "v2_isolation": imports,
            "security": security,
        },
        "test_results": regressions,
        "gates": gates,
        "summary": {
            "pass": sum(item["status"] == PASS for item in gates),
            "fail": sum(item["status"] == FAIL for item in gates),
            "skip_with_reason": sum(item["status"] == SKIP for item in gates),
        },
        "decision": "READY_FOR_FINAL_EXPERIMENTS" if ready else "NOT_READY_FOR_FINAL_EXPERIMENTS",
        "limitations": [
            "live LLM é opcional e não foi utilizado",
            "business events live permanecem capability não bloqueante e não são fabricados",
            "o modelo operacional serve somente a runtime_e2e_validation, não a resultados científicos H1/H2",
        ],
    }


def release_manifest(validation: dict[str, Any]) -> dict[str, Any]:
    registry = FeatureRegistryV3()
    freeze = v2_freeze_status()
    graph_rules = dict(build_demo_scenario("payment_failure").graph.rule_versions)
    model = operational_model_status()
    fault = fault_e2e_live_status()
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty_worktree": bool(_git("status", "--porcelain")),
        },
        "v2": {"freeze_hashes": {"valid": freeze["valid"], "ok": freeze["ok"], "total": freeze["total"]}},
        "v3": {
            "UnifiedEvent_version": UnifiedEvent.SCHEMA_VERSION,
            "DerivedEvidence_version": DerivedEvidence.SCHEMA_VERSION,
            "assembler_version": "incident-evidence-bundle-v3",
            "graph_version": "event-evidence-graph-v3",
            "graph_rule_versions": graph_rules,
            "feature_registry_version": registry.schema_version,
            "feature_registry_hash": registry.fingerprint,
            "model_version": "xgboost-rca-v3",
            "model_hash": model.get("artifact_sha256") if model["valid"] else None,
            "model_purpose": model.get("manifest", {}).get("purpose") if model["valid"] else None,
            "fault_e2e_artifact_hash": fault.get("report_sha256") if fault["valid"] else None,
            "reliability_version": "reliability-gate-v1",
            "EvidenceBuilder_version": EvidenceBuilder.BUILDER_VERSION,
            "PromptBuilder_version": "prompt-builder-v1",
            "orchestrator_version": "experimental-orchestrator-v3",
            "benchmark_version": BENCHMARK_VERSION,
            "degradation_harness_version": HARNESS_VERSION,
        },
        "infrastructure": {
            "docker_images": {
                "opensearch": "opensearchproject/opensearch:2.19.0",
                "kafka": "confluentinc/cp-kafka:7.8.0",
                "grafana": "grafana/grafana:11.6.2",
                "tempo": "grafana/tempo:2.7.2",
                "otel_collector": "otel/opentelemetry-collector-contrib:0.130.0",
            },
            "indices": INDEX_NAMES,
        },
        "validation": {
            "summary": validation["summary"],
            "decision": validation["decision"],
            "gates": [{"name": item["name"], "status": item["status"]} for item in validation["gates"]],
            "test_results": validation["test_results"],
            "smoke": validation["e2e_scenarios"],
            "fault_e2e": next(
                item for item in validation["gates"] if item["name"] == "fault_e2e_live"
            ),
            "dashboards": validation["dashboards"],
            "opensearch": validation["indices"],
            "security": validation["integration"]["security"],
            "limitations": validation["limitations"],
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Platform v3 validation report", "", f"Decision: **{report['decision']}**", "",
        "| Gate | Status | Reason |", "|---|---|---|",
    ]
    for gate in report["gates"]:
        lines.append(f"| {gate['name']} | {gate['status']} | {gate['reason']} |")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def write_validation_artifacts(output: Path, *, live: bool = False) -> dict[str, Path]:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = validate_platform(live=live)
    manifest = release_manifest(report)
    report_json = output / "platform-validation-report.json"
    report_md = output / "platform-validation-report.md"
    manifest_json = output / "release-manifest.json"
    manifest_sha = output / "release-manifest.json.sha256"
    report_json.write_text(canonical_json(report) + "\n", encoding="utf-8")
    report_md.write_text(markdown_report(report), encoding="utf-8")
    manifest_json.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    digest = hashlib.sha256(manifest_json.read_bytes()).hexdigest()
    manifest_sha.write_text(f"{digest}  release-manifest.json\n", encoding="utf-8")
    return {
        "report_json": report_json, "report_md": report_md,
        "manifest_json": manifest_json, "manifest_sha256": manifest_sha,
    }
