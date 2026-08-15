from __future__ import annotations

import json
import shutil
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import (EXPECTED_MODALITIES, SCENARIOS, audit_run_isolation, atomic_json,
                   file_sha256, parse_time, utc_now)


def build_final_audit(root: Path) -> dict[str, Any]:
    root = Path(root)
    checkpoints = [json.loads(path.read_text(encoding="utf-8"))
                   for path in sorted((root / "checkpoints").glob("*.json"))]
    rows = []
    ground_truth_rows = []
    for checkpoint in checkpoints:
        safe = checkpoint["run_id"].replace("/", "__")
        ground = json.loads((root / "incidents" / safe / "ground-truth.json")
                            .read_text(encoding="utf-8"))
        observed_path = root / "incidents" / safe / "observed-fault-controller-state.json"
        observed = json.loads(observed_path.read_text(encoding="utf-8"))
        binding = checkpoint.get("fault_controller_binding_audit", {})
        active = [item for item in observed["services"].values()
                  if item["mode"] != "none"]
        scenario = SCENARIOS[checkpoint["scenario"]]
        expected_service = active[0]["service_name"] if len(active) == 1 else None
        expected_type = active[0]["mode"] if len(active) == 1 else "healthy"
        ground_match = bool(
            binding.get("status") == "PASS"
            and len(active) <= 1
            and ground["fault_injected"] == bool(active)
            and ground["fault_target_service"] == expected_service
            and ground["root_cause_service"] == expected_service
            and ground["root_cause_type"] == expected_type
            and ground["observed_fault_controller_state_sha256"]
                == file_sha256(observed_path)
        )
        ground_truth_rows.append({
            "scenario": scenario.name,
            "requested": binding.get("requested"),
            "observed": observed,
            "recorded": {key: ground[key] for key in (
                "fault_injected", "root_cause_service", "root_cause_type")},
            "fault_controller_binding": binding.get("status"),
            "match": ground_match,
        })
        modality_bytes = {
            modality: (root / "incidents" / safe / f"{modality}.json.gz").stat().st_size
            for modality in EXPECTED_MODALITIES
        }
        artifact_bytes = sum((root / relative).stat().st_size
                             for relative in checkpoint["artifact_hashes"])
        checkpoint_path = root / "checkpoints" / f"{safe}.json"
        window_seconds = (
            parse_time(checkpoint["timing"]["collection_finished_at"])
            - parse_time(checkpoint["timing"]["experiment_started_at"])
        ).total_seconds()
        rows.append({
            "scenario": checkpoint["scenario"], "run_id": checkpoint["run_id"],
            "http_status": checkpoint["http_status"],
            "integrity_status": checkpoint["integrity_status"],
            "usable_for_benchmark": checkpoint["usable_for_benchmark"],
            "counts": checkpoint["audit"]["counts"],
            "identity": {key: checkpoint["audit"].get(key) for key in (
                "identity_status", "expected_services", "trace_services", "log_services",
                "correlation_ids")},
            "anti_leakage": checkpoint["audit"]["anti_leakage"],
            "metric_pipeline_provenance": checkpoint["audit"]["metric_pipeline_provenance"],
            "transport_audit": checkpoint["transport_audit"],
            "service_state_reset_audit": checkpoint["service_state_reset_audit"],
            "fault_controller_binding_audit": binding,
            "dlq_count": checkpoint["dlq_count"], "pipeline": checkpoint["pipeline"],
            "request_duration_seconds": checkpoint["timing"]["duration_seconds"],
            "window_duration_seconds": window_seconds,
            "bytes": artifact_bytes + checkpoint_path.stat().st_size,
            "modality_bytes": modality_bytes,
            "coordinator_peak_rss_kib": checkpoint["resource_after"]["coordinator_peak_rss_kib"],
        })
    free = shutil.disk_usage(root).free
    max_bytes = max((row["bytes"] for row in rows), default=0)
    by_scenario_estimate = sum(row["bytes"] * 100 for row in rows)
    pessimistic = max_bytes * 500
    isolation = audit_run_isolation(checkpoints, root)
    resume_path = root / "audits" / "resume-report.json"
    resume = json.loads(resume_path.read_text(encoding="utf-8")) if resume_path.is_file() else {
        "attempted": False, "completed": 0, "skipped": 0,
        "new_executions": None, "duration_seconds": None, "executed_at": None,
    }
    resume_pass = (resume.get("attempted") == len(rows)
                   and resume.get("completed") == len(rows)
                   and resume.get("skipped") == len(rows)
                   and resume.get("new_executions") == 0
                   and isinstance(resume.get("duration_seconds"), (int, float))
                   and resume.get("duration_seconds") >= 0
                   and bool(resume.get("executed_at")))
    before_path = root / "audits" / "resource-before.json"
    after_path = root / "audits" / "resource-after.json"
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.is_file() else {}
    after = json.loads(after_path.read_text(encoding="utf-8")) if after_path.is_file() else {}
    def delta(section: str, key: str = "total_bytes"):
        left = before.get(section, {}).get(key)
        right = after.get(section, {}).get(key)
        return right - left if isinstance(left, int) and isinstance(right, int) else None
    filesystem_delta = (after.get("filesystem_used_bytes", 0)
                        - before.get("filesystem_used_bytes", 0))
    artifact_delta = (after.get("dataset_output_bytes", 0)
                      - before.get("dataset_output_bytes", 0))
    infrastructure_projection = max(0, filesystem_delta) * 100
    required_projection = max(by_scenario_estimate, pessimistic, infrastructure_projection)
    disk_pass = free > required_projection + 5 * 1024**3
    audit = {
        "generated_at": utc_now(), "incident_count": len(rows), "rows": rows,
        "integrity": dict(Counter(row["integrity_status"] for row in rows)),
        "full_modality_audit": "PASS" if all(
            all(row["counts"].get(item, 0) > 0 for item in EXPECTED_MODALITIES)
            for row in rows) else "FAIL",
        "identity_audit": "PASS" if all(
            row["identity"]["identity_status"] == "PASS" for row in rows) else "FAIL",
        "ground_truth_audit": "PASS" if all(
            row["match"] for row in ground_truth_rows) else "FAIL",
        "ground_truth_rows": ground_truth_rows,
        "fault_controller_binding": "PASS" if all(
            row["fault_controller_binding_audit"].get("status") == "PASS"
            for row in rows) else "FAIL",
        "anti_leakage_audit": "PASS" if all(
            row["anti_leakage"]["status"] == "PASS" for row in rows) else "FAIL",
        "metric_pipeline_provenance": "PASS" if all(
            row["metric_pipeline_provenance"]["status"] == "PASS" for row in rows)
            else "FAIL",
        "telemetry_transport_audit": "PASS" if all(
            row["transport_audit"]["status"] == "PASS" for row in rows) else "FAIL",
        "service_state_reset_audit": "PASS" if all(
            row["service_state_reset_audit"]["status"] == "PASS" for row in rows)
            else "FAIL",
        "run_isolation_audit": isolation,
        "resume_audit": {"status": "PASS" if resume_pass else "FAIL",
                         "resume_validation": resume},
        "resource_report": {
            "observed_smoke_bytes": sum(row["bytes"] for row in rows),
            "bytes_per_scenario": {row["scenario"]: row["bytes"] for row in rows},
            "bytes_per_modality_by_scenario": {
                row["scenario"]: row["modality_bytes"] for row in rows},
            "estimated_500_run_bytes_by_scenario": by_scenario_estimate,
            "max_bytes_per_run": max_bytes,
            "pessimistic_500_run_bytes": pessimistic,
            "resource_before": before, "resource_after": after,
            "dataset_artifact_delta": artifact_delta,
            "opensearch_delta": delta("opensearch"),
            "kafka_delta": delta("kafka"),
            "docker_log_delta": delta("docker_logs"),
            "filesystem_used_delta": filesystem_delta,
            "infrastructure_projection_500": infrastructure_projection,
            "required_projection_500": required_projection,
            "projection_policy": (
                "max(scenario_artifact_projection,pessimistic_artifact_projection,"
                "observed_filesystem_delta_per_5_runs*100)"),
            "free_disk_bytes": free,
            "safety_margin_bytes": free - required_projection,
            "disk_capacity_for_500": "PASS" if disk_pass else "FAIL",
            "coordinator_peak_rss_kib": max(
                (row["coordinator_peak_rss_kib"] for row in rows), default=0),
            "mean_window_duration_seconds": statistics.mean(
                row["window_duration_seconds"] for row in rows) if rows else 0,
            "max_window_duration_seconds": max(
                (row["window_duration_seconds"] for row in rows), default=0),
            "estimated_serial_500_seconds": sum(
                row["window_duration_seconds"] * 100 for row in rows),
        },
    }
    atomic_json(root / "audits" / "final-audit.json", audit)
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{file_sha256(path)}  {path.relative_to(root)}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return audit
