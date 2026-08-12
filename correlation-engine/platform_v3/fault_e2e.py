from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from platform_v3.constants import ARTIFACT_ROOT, PLATFORM_VERSION
from platform_v3.operational_model import DEFAULT_MODELS_ROOT, operational_model_status
from platform_v3.serialization import source_snapshot_sha256


FAULT_E2E_SCHEMA = "fault-e2e-live-v1"
FAULT_E2E_REPORT = "fault-e2e-live.json"
FAULT_E2E_CHECKSUM = "fault-e2e-live.json.sha256"
STAGE_NAMES = (
    "fault_configured",
    "real_request_sent",
    "correlation_id_obtained",
    "trace_id_obtained",
    "telemetry_reached_collector_kafka",
    "multimodal_runtime_consumed_events",
    "incident_evidence_bundle_created",
    "event_evidence_graph_created",
    "features_v3_extracted",
    "operational_xgboost_executed",
    "real_rca_result_created",
    "evidence_and_explanation_created",
    "documents_persisted_opensearch",
    "incident_queryable",
    "rca_queryable",
    "explanation_queryable",
    "grafana_backend_query_executable",
    "tempo_trace_queryable",
    "fault_restored",
)


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def fault_e2e_live_status(
    artifact_root: Path = ARTIFACT_ROOT,
    models_root: Path = DEFAULT_MODELS_ROOT,
    *,
    expected_source_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    artifact_root = Path(artifact_root)
    current_source_snapshot = (
        expected_source_snapshot_sha256
        if expected_source_snapshot_sha256 is not None
        else source_snapshot_sha256()
    )
    report_path = artifact_root / FAULT_E2E_REPORT
    checksum_path = artifact_root / FAULT_E2E_CHECKSUM
    try:
        if not report_path.is_file():
            raise ValueError("relatório fault E2E live ausente")
        if not checksum_path.is_file():
            raise ValueError("checksum fault E2E live ausente")
        parts = checksum_path.read_text(encoding="utf-8").strip().split()
        if len(parts) != 2 or parts[1].lstrip("*") != FAULT_E2E_REPORT:
            raise ValueError("arquivo de checksum fault E2E inválido")
        actual = hashlib.sha256(report_path.read_bytes()).hexdigest()
        if parts[0] != actual:
            raise ValueError("checksum fault E2E divergente")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("relatório fault E2E deve ser objeto")
        required = {
            "schema_version", "platform_version", "generated_at", "repository_commit",
            "source_snapshot_sha256",
            "execution_mode", "scenario", "correlation_id", "trace_id", "incident_id",
            "rca_result_id", "explanation_id", "model", "stages", "runtime_evidence",
            "evaluation", "leakage_check", "status",
        }
        missing = sorted(required - set(report))
        if missing:
            raise ValueError(f"campos E2E ausentes: {missing}")
        if report["schema_version"] != FAULT_E2E_SCHEMA:
            raise ValueError("schema E2E incompatível")
        if report["platform_version"] != PLATFORM_VERSION:
            raise ValueError("platform_version E2E diverge da plataforma atual")
        if report["source_snapshot_sha256"] != current_source_snapshot:
            raise ValueError("source snapshot E2E diverge do código atual")
        if report["execution_mode"] != "live":
            raise ValueError("relatório E2E não é live")
        if report["scenario"] != "payment_failure":
            raise ValueError("gate exige payment_failure real")
        if report["status"] != "SUCCESS":
            raise ValueError("status E2E não é SUCCESS")
        if not _valid_timestamp(report["generated_at"]):
            raise ValueError("generated_at E2E inválido")
        for field in ("correlation_id", "trace_id", "incident_id", "rca_result_id", "explanation_id"):
            if not isinstance(report[field], str) or not report[field].strip():
                raise ValueError(f"identificador E2E ausente: {field}")
        if len(report["trace_id"]) != 32:
            raise ValueError("trace_id E2E inválido")

        stages = report["stages"]
        if not isinstance(stages, list) or len(stages) != len(STAGE_NAMES):
            raise ValueError("relatório E2E não contém os 19 estágios")
        timestamps = []
        for number, (stage, expected_name) in enumerate(zip(stages, STAGE_NAMES, strict=True), start=1):
            if not isinstance(stage, dict):
                raise ValueError(f"estágio {number} inválido")
            if stage.get("stage") != number or stage.get("name") != expected_name:
                raise ValueError(f"estágio {number} ausente ou fora de ordem")
            if stage.get("status") != "PASS":
                raise ValueError(f"estágio {number} não passou")
            if not _valid_timestamp(stage.get("timestamp")):
                raise ValueError(f"timestamp inválido no estágio {number}")
            if not isinstance(stage.get("identifiers"), dict):
                raise ValueError(f"identifiers ausentes no estágio {number}")
            timestamps.append(datetime.fromisoformat(stage["timestamp"].replace("Z", "+00:00")))
        if timestamps != sorted(timestamps):
            raise ValueError("timestamps dos estágios não são monotônicos")

        model = operational_model_status(models_root)
        if not model["valid"]:
            raise ValueError(f"modelo operacional inválido: {model['reason']}")
        evidence = report["model"]
        manifest = model["manifest"]
        expected_model = {
            "model_name": manifest["model_name"],
            "model_version": manifest["model_version"],
            "model_sha256": manifest["artifact_sha256"],
            "feature_registry_sha256": manifest["feature_registry_sha256"],
            "feature_view_sha256": manifest["feature_view_sha256"],
        }
        if any(evidence.get(key) != value for key, value in expected_model.items()):
            raise ValueError("evidência do modelo E2E diverge do artefato aprovado")

        runtime = report["runtime_evidence"]
        required_runtime = (
            "runtime_ready", "telemetry_real", "model_inference_real",
            "opensearch_links_verified", "grafana_query_passed", "tempo_direct_passed",
            "tempo_grafana_proxy_passed", "fault_restored",
        )
        if not isinstance(runtime, dict) or any(runtime.get(key) is not True for key in required_runtime):
            raise ValueError("evidência runtime E2E incompleta")
        forbidden_markers = ("model_output_contract", "telemetry_fixture", "fixture_boundaries")
        runtime_text = json.dumps(runtime, sort_keys=True).lower()
        if any(marker in runtime_text for marker in forbidden_markers):
            raise ValueError("fixture detectada no path live")

        evaluation = report["evaluation"]
        if not isinstance(evaluation, dict):
            raise ValueError("evaluation E2E inválida")
        if evaluation.get("predicted_root_cause") != "payment-service":
            raise ValueError("modelo não classificou payment-service como top-1")
        if evaluation.get("expected_root_cause") != "payment-service" or evaluation.get("top1_correct") is not True:
            raise ValueError("avaliação top-1 do fault E2E falhou")
        leakage = report["leakage_check"]
        if not isinstance(leakage, dict) or leakage.get("passed") is not True:
            raise ValueError("check de ground-truth leakage falhou")
        if leakage.get("forbidden_fields_found") not in ([], ()):
            raise ValueError("ground truth encontrado no path operacional")
        return {
            "valid": True,
            "reason": "checksum, modelo, 19 estágios, integrações e leakage válidos",
            "report_path": str(report_path),
            "report_sha256": actual,
            "report": report,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exception:
        return {"valid": False, "reason": str(exception), "report_path": str(report_path)}
