"""Presentation-only live campaign. Never runs a scientific experiment."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import uuid

from platform_v3 import e2e_live as live
from platform_v3.constants import INDEX_NAMES, ROOT
from platform_v3.presentation_safety import checked_json

BASE_COMMIT = "a1d026e2a5ec94194e99ae58937666bc19307caf"
ID_FIELDS = ("correlation_id", "trace_id", "incident_id", "rca_result_id", "explanation_id")


def write_json(path: Path, value) -> None:
    """Reject, rather than export, potentially sensitive remote content."""
    text = checked_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def _runtime_provider(live_llm: bool) -> None:
    command = (*live.COMPOSE, "-f", str(ROOT / "docker-compose.presentation-demo.yml"))
    environment = os.environ.copy()
    environment["PLATFORM_V3_DEMO_PROVIDER"] = "openai" if live_llm else "deterministic"
    result = subprocess.run(
        (*command, "up", "-d", "--force-recreate", "--no-deps", "platform-v3-runtime"),
        cwd=ROOT, capture_output=True, text=True, env=environment,
    )
    if result.returncode:
        # Never expose compose diagnostics: interpolation may include credentials.
        raise RuntimeError("Runtime provider configuration failed")
    expected = "openai" if live_llm else "deterministic"
    live._wait_until(
        lambda: (value if (value := live._runtime_health()) and value.get("ready")
                 and value.get("explanation_provider") == expected
                 and value.get("consumer_group") == "causalods-platform-v3-presentation-demo"
                 else None),
        timeout=90, description="runtime presentation provider READY",
    )


def _verify_fault_mode(mode: str) -> bool:
    result = subprocess.run(
        (*live.COMPOSE, "exec", "-T", "payment-service", "printenv", "CAUSALODS_FAULT_MODE"),
        cwd=ROOT, capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == mode and live._service_ready(8082)


def _document(kind: str, identifier: str) -> dict:
    field = {"incidents": "incident_id", "rca_results": "rca_result_id",
             "explanations": "explanation_id"}[kind]
    docs = live._search(INDEX_NAMES[kind], field, identifier)
    if len(docs) != 1 or docs[0].get(field) != identifier:
        raise ValueError("Operational document identity mismatch")
    return docs[0]


def validate_documents(report: dict, incident: dict, rca: dict, explanation: dict,
                       *, live_llm: bool) -> None:
    if not all(report.get(key) for key in ID_FIELDS):
        raise ValueError("Missing run identifiers")
    if not (incident["incident_id"] == rca["incident_id"] == explanation["incident_id"]
            == report["incident_id"]):
        raise ValueError("Unlinked operational documents")
    if (report["correlation_id"] not in incident["correlation_ids"]
            or report["trace_id"] not in incident["trace_ids"]
            or rca["trace_id"] != report["trace_id"]):
        raise ValueError("Run correlation mismatch")
    if explanation["root_cause_service"] != rca["root_cause_service"]:
        raise ValueError("Explanation changed RCA")
    if not set(explanation.get("evidence_references", [])) <= set(rca.get("evidence_ids", [])):
        raise ValueError("Unknown explanation citation")
    if not incident.get("graph_node_count") or not incident.get("graph_edge_count"):
        raise ValueError("Empty operational graph")
    if not json.loads(incident.get("pruning_json", "{}")):
        raise ValueError("Missing persisted pruning")
    for field in ("evidence_statements_json", "propagation_path_json", "recommendations_json"):
        if not isinstance(json.loads(explanation[field]), list):
            raise ValueError("Missing explanation contract projection")
    if live._forbidden_keys([incident, rca, explanation]):
        raise ValueError("Operational ground truth leakage")
    if live_llm:
        from experiments.final_campaigns_v3.h3_openai_freeze import MODEL_ID
        if (explanation.get("provider") != "llm" or explanation.get("fallback") is not False
                or explanation.get("valid") is not True or explanation.get("model_name") != MODEL_ID):
            raise ValueError("Live LLM validation failed")
    elif explanation.get("provider") != "deterministic-fallback" or explanation.get("fallback") is not True:
        raise ValueError("Deterministic provider validation failed")


def select_featured(runs: list[dict]) -> dict | None:
    successful = [run for run in runs if run["status"] == "SUCCESS"]
    # Stable ordering: prefer reliable evidence, then retain original run order.
    return next(iter(sorted(successful, key=lambda run: not run.get("reliable", False))), None)


def run_presentation_demo(output: Path, *, runs: int = 10, scenario: str = "payment_failure",
                          live_llm: bool = False, export_graphs: bool = True) -> dict:
    if runs < 1 or scenario != "payment_failure":
        raise ValueError("Demo requires runs >= 1 and payment_failure")
    output = Path(output).resolve()
    # Restrict all writes to the dedicated namespace, including custom subfolders.
    allowed = (ROOT / "artifacts/presentation-demo").resolve()
    if output != allowed and allowed not in output.parents:
        raise ValueError("Demo output must be under artifacts/presentation-demo")
    branch = subprocess.run(("git", "branch", "--show-current"), cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    if branch != "fix/codex":
        raise ValueError("Presentation demo requires fix/codex")
    campaign_id = uuid.uuid4().hex
    campaign = output / campaign_id
    campaign.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "presentation-demo-v1", "campaign_id": campaign_id,
        "purpose": "presentation_demo", "scientific_result": False,
        "runs_requested": runs, "runs_completed": 0, "runs_successful": 0, "runs_failed": 0,
        "started_at": live._now(), "completed_at": None,
        "repository_commit": live._git_commit(), "base_branch": "fix/codex",
        "base_commit": BASE_COMMIT, "working_branch": branch, "scenario": scenario,
        "live_llm_requested": live_llm, "graph_export_requested": export_graphs,
        "fault_restored": False, "runtime_provider_restored": False,
        "featured_run": None, "runs": [], "campaign_status": "RUNNING", "error": None,
    }
    write_json(campaign / "manifest.json", manifest)
    documents = {}
    runtime_touched = fault_touched = False
    try:
        if live_llm:
            # Validation/tokenizer only: no paid request is made here.
            from platform_v3.explanation_provider import build_explanation_service
            build_explanation_service("openai")
        runtime_touched = True
        _runtime_provider(live_llm)
        fault_touched = True
        live._configure_fault(True)
        if not _verify_fault_mode("error"):
            raise RuntimeError("Fault setup verification failed")
        for number in range(1, runs + 1):
            folder = campaign / "runs" / f"run-{number:02d}"
            folder.mkdir(parents=True)
            correlation_id = f"presentation-demo-{campaign_id}-run-{number:02d}"
            item = {key: None for key in ID_FIELDS}
            item.update({"run_number": number, "correlation_id": correlation_id,
                         "status": "FAILED", "root_cause_service": None, "confidence": None,
                         "reliable": False, "provider": None, "model_name": None, "fallback": None,
                         "modalities": [], "event_count": 0,
                         "report_path": str((folder / "report.json").relative_to(campaign))})
            report = {"scientific_result": False, "correlation_id": correlation_id, "status": "FAILED"}
            try:
                report = live.execute_live_incident(
                    folder, scenario=scenario, correlation_id=correlation_id, manage_fault=False,
                    write_report=False,
                )
                item.update({key: report.get(key) for key in ID_FIELDS})
                item.update({key: value for key, value in report.get("operational_progress", {}).items()
                             if key in item and value is not None})
                if report["status"] != "SUCCESS":
                    raise RuntimeError("Live incident stages failed")
                for previous in manifest["runs"]:
                    if any(report.get(key) and report[key] == previous.get(key) for key in ID_FIELDS):
                        raise ValueError("Incident identifiers reused across demo runs")
                incident = _document("incidents", report["incident_id"])
                rca = _document("rca_results", report["rca_result_id"])
                explanation = _document("explanations", report["explanation_id"])
                validate_documents(report, incident, rca, explanation, live_llm=live_llm)
                for name, value in (("incident", incident), ("rca", rca), ("explanation", explanation)):
                    write_json(folder / f"{name}.json", value)
                item.update({key: rca.get(key) for key in ("root_cause_service", "confidence", "reliable")})
                item.update({key: explanation.get(key) for key in ("provider", "model_name", "fallback", "valid")})
                if live_llm:
                    # The persisted v3 projection classifies providers as llm;
                    # the campaign records the explicitly validated binding.
                    item["provider"] = "openai"
                item.update({key: incident.get(key) for key in
                             ("modalities", "event_count", "graph_node_count", "graph_edge_count")})
                item["status"] = "SUCCESS"
                documents[number] = (incident, rca)
            except Exception as exc:
                # Do not serialize arbitrary exception strings or provider responses.
                item["error"] = report.get("error") or type(exc).__name__
            report.update({"scientific_result": False, "status": item["status"], "demo_run": item})
            try:
                write_json(folder / "report.json", report)
            except ValueError:
                item["status"] = "FAILED"
                report = {"scientific_result": False, "status": "FAILED", "error": "Sensitive content rejected"}
                write_json(folder / "report.json", report)
            manifest["runs"].append(item)
            manifest["runs_completed"] += 1
            write_json(campaign / "manifest.json", manifest)
        featured = select_featured(manifest["runs"])
        if featured:
            manifest["featured_run"] = featured["run_number"]
            write_json(campaign / "featured/identifiers.json", {key: featured[key] for key in ID_FIELDS})
            if export_graphs:
                from platform_v3.presentation_graph import export_graphs as render
                incident, rca = documents[featured["run_number"]]
                paths = render(incident, rca, campaign / "featured/graph")
                manifest["graph_images"] = [str(path.relative_to(campaign)) for path in paths]
    except (Exception, KeyboardInterrupt) as exc:
        manifest["error"] = type(exc).__name__
    finally:
        try:
            if fault_touched:
                live._configure_fault(False)
            manifest["fault_restored"] = _verify_fault_mode("none")
        except Exception:
            manifest["fault_restored"] = False
        try:
            if runtime_touched:
                _runtime_provider(False)
            manifest["runtime_provider_restored"] = True
        except Exception:
            manifest["runtime_provider_restored"] = False
        manifest["runs_successful"] = sum(run["status"] == "SUCCESS" for run in manifest["runs"])
        manifest["runs_failed"] = sum(run["status"] == "FAILED" for run in manifest["runs"])
        manifest["completed_at"] = live._now()
        success = (manifest["runs_successful"] == runs and manifest["fault_restored"]
                   and manifest["runtime_provider_restored"] and not manifest["error"])
        manifest["campaign_status"] = "SUCCESS" if success else "FAILED"
        write_json(campaign / "manifest.json", manifest)
        write_json(campaign / "summary.json", manifest)
        _readme(campaign, manifest)
    return manifest


def _readme(campaign: Path, manifest: dict) -> None:
    featured = next((run for run in manifest["runs"]
                     if run["run_number"] == manifest["featured_run"]), {})
    text = (
        "# CausalODS presentation demo\n\nscientific_result = false\n\n"
        f"Status: {manifest['campaign_status']}\n\n"
        f"Fault restored: {manifest['fault_restored']}\n\n"
        f"Featured incident_id: {featured.get('incident_id')}\n\n"
        f"Featured trace_id: {featured.get('trace_id')}\n\n"
        "Grafana: http://localhost:3000\n\nPrometheus: http://localhost:9090\n\n"
        "Tempo: http://localhost:3200\n\nRuntime health: http://localhost:8090/health\n\n"
        "Open these Grafana dashboards and filter by the featured incident_id:\n\n"
        "- Platform Health\n- Incident Overview\n- Multimodal Evidence\n"
        "- Event Evidence Graph\n- Root Cause Detail\n- Explanation Audit\n\n"
        "Use the trace_id in Grafana Explore / Tempo. Progressive images are in featured/graph.\n"
        "Reports defer fault restoration to this campaign manifest. Operational JSON exports are copies.\n"
        "This campaign does not replace or extend any frozen H1–H4 scientific result.\n"
    )
    (campaign / "README.md").write_text(text, encoding="utf-8")
