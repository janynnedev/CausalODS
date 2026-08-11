from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from platform_v3.constants import PROJECTION_VERSION
from platform_v3.serialization import arbitrary_json, plain, sanitize, sha256_value


def _mapping(value: Any) -> dict[str, Any]:
    converted = plain(value)
    if not isinstance(converted, dict):
        raise TypeError("O contrato projetado deve serializar para um objeto JSON.")
    return converted


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_reference(value: Any) -> dict[str, Any]:
    data = _mapping(value)
    return {
        "event_id": data.get("event_id"),
        "source_reference": data.get("source_reference"),
    }


class VisualizationDocumentAdapter:
    """Adapta contratos existentes para projeções pesquisáveis, sem os redefinir."""

    def unified_event(self, event: Any, *, incident_id: str | None = None) -> dict[str, Any]:
        data = sanitize(_mapping(event))
        event_id = data.get("event_id")
        if not event_id:
            raise ValueError("UnifiedEvent projetado requer event_id.")
        return {
            "projection_version": PROJECTION_VERSION,
            "event_id": event_id,
            "incident_id": incident_id,
            "event_name": data.get("event_name"),
            "event_type": data.get("event_type"),
            "signal_type": data.get("signal_type"),
            "event_timestamp": data.get("event_timestamp"),
            "observed_timestamp": data.get("observed_timestamp"),
            "ingested_timestamp": data.get("ingested_timestamp"),
            "trace_id": data.get("trace_id"),
            "span_id": data.get("span_id"),
            "parent_span_id": data.get("parent_span_id"),
            "correlation_id": data.get("correlation_id"),
            "business_entity_type": data.get("business_entity_type"),
            "business_entity_id": data.get("business_entity_id"),
            "business_operation": data.get("business_operation"),
            "business_outcome": data.get("business_outcome"),
            "service_name": data.get("service_name"),
            "severity": data.get("severity"),
            "error_type": data.get("error_type"),
            "status_code": data.get("status_code"),
            "metric_name": data.get("metric_name"),
            "metric_unit": data.get("metric_unit"),
            "metric_value": data.get("metric_value"),
            "source_reference": data.get("source_reference"),
            "source_dataset": data.get("source_dataset"),
            "missing_fields": data.get("missing_fields", []),
            "data_quality_flags": data.get("data_quality_flags", []),
            "late_arrival_ms": data.get("late_arrival_ms"),
            "attributes_json": arbitrary_json(data.get("attributes", {})),
            "resource_attributes_json": arbitrary_json(data.get("resource_attributes", {})),
            "scope_json": arbitrary_json(data.get("instrumentation_scope", {})),
            "payload_hash": sha256_value(data),
        }

    def incident(self, bundle: Any, graph: Any | None = None) -> dict[str, Any]:
        data = sanitize(_mapping(bundle))
        incident_id = data.get("incident_id")
        if not incident_id:
            raise ValueError("IncidentEvidenceBundle projetado requer incident_id.")
        events = data.get("events", [])
        services = data.get("services", [])
        correlation_ids = sorted({
            item.get("correlation_id") for item in events
            if isinstance(item, dict) and item.get("correlation_id")
        })
        modalities = sorted({
            item.get("signal_type") for item in events
            if isinstance(item, dict) and item.get("signal_type")
        })
        association_scores = []
        raw_scores = data.get("association_scores", {})
        if isinstance(raw_scores, dict):
            association_scores = [float(value) for value in raw_scores.values()]
        elif isinstance(raw_scores, list):
            association_scores = [
                float(item.get("score", 0.0)) for item in raw_scores if isinstance(item, dict)
            ]
        graph_data = sanitize(_mapping(graph)) if graph is not None else {}
        return {
            "projection_version": PROJECTION_VERSION,
            "incident_id": incident_id,
            "workflow_key": data.get("workflow_key"),
            "analysis_window_start": (data.get("analysis_window") or {}).get("start"),
            "analysis_window_end": (data.get("analysis_window") or {}).get("end"),
            "services": sorted({
                str((item.get("identity") or {}).get("service_name") or item.get("service_name"))
                for item in services if isinstance(item, dict)
                and ((item.get("identity") or {}).get("service_name") or item.get("service_name"))
            }),
            "modalities": modalities,
            "missing_modalities": data.get("missing_modalities", []),
            "event_count": len(events),
            "late_event_count": len(data.get("late_events", [])),
            "association_quality": (
                sum(association_scores) / len(association_scores) if association_scores else None
            ),
            "correlation_ids": correlation_ids,
            "trace_ids": data.get("traces", []),
            "business_entities_json": arbitrary_json(data.get("business_entities", [])),
            "provenance_json": arbitrary_json(data.get("provenance", [])),
            "quality_json": arbitrary_json({
                "late_events": data.get("late_events", []),
                "missing_modalities": data.get("missing_modalities", []),
                "association_reasons": data.get("association_reasons", []),
            }),
            "graph_node_count": len(graph_data.get("nodes", [])),
            "graph_edge_count": len(graph_data.get("edges", [])),
            "graph_rule_versions_json": arbitrary_json(graph_data.get("rule_versions", {})),
            "graph_nodes_json": arbitrary_json(graph_data.get("nodes", [])),
            "graph_edges_json": arbitrary_json(graph_data.get("edges", [])),
            "payload_hash": sha256_value(data),
        }

    def rca_result(
        self,
        result: Any,
        *,
        rca_result_id: str | None = None,
        incident_id: str | None = None,
        model_hash: str | None = None,
        feature_registry_hash: str | None = None,
        graph_rule_versions: Mapping[str, Any] | None = None,
        shap_values: Any = (),
    ) -> dict[str, Any]:
        data = sanitize(_mapping(result))
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        ranking = data.get("ranking", [])
        reliability = data.get("reliability") or {}
        top = ranking[0] if ranking else {}
        scores = top.get("scores", {}) if isinstance(top, dict) else {}
        resolved_incident = incident_id or metadata.get("incident_id")
        return {
            "projection_version": PROJECTION_VERSION,
            "rca_result_id": rca_result_id or data.get("rca_result_id"),
            "incident_id": resolved_incident,
            "trace_id": data.get("trace_id"),
            "root_cause_service": data.get("root_cause_service"),
            "ranked_root_causes": [item.get("service_name") for item in ranking],
            "predicted_probabilities": [
                (item.get("scores") or {}).get("model_probability") for item in ranking
            ],
            "ranking_json": arbitrary_json(ranking),
            "confidence": data.get("confidence"),
            "margin_to_second": reliability.get("margin_to_second"),
            "reliable": reliability.get("is_reliable"),
            "abstained": not bool(reliability.get("is_reliable")),
            "failed_reliability_rules": reliability.get("failed_rules", []),
            "model_version": data.get("model_version"),
            "model_hash": model_hash or metadata.get("model_hash"),
            "pipeline_version": data.get("pipeline_version"),
            "feature_registry_hash": feature_registry_hash or metadata.get("feature_registry_hash"),
            "graph_rule_versions_json": arbitrary_json(graph_rule_versions or metadata.get("graph_rule_versions", {})),
            "generated_at": data.get("generated_at"),
            "model_top_probability": scores.get("model_probability"),
            "shap_json": arbitrary_json(shap_values),
            "evidence_ids": [item.get("evidence_id") for item in data.get("evidence", [])],
            "evidence_json": arbitrary_json(data.get("evidence", [])),
            "payload_hash": sha256_value(data),
        }

    def explanation(
        self,
        explanation: Any,
        *,
        incident_id: str,
        explanation_id: str | None = None,
        evidence_hash: str | None = None,
        provider: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        data = sanitize(_mapping(explanation))
        if not incident_id:
            raise ValueError("Explanation projetada requer incident_id.")
        identifier = explanation_id or sha256_value(
            {"incident_id": incident_id, "prompt_hash": data.get("prompt_hash"), "summary": data.get("summary")}
        )
        return {
            "projection_version": PROJECTION_VERSION,
            "incident_id": incident_id,
            "explanation_id": identifier,
            "created_at": created_at or _now(),
            "mode": data.get("mode"),
            "root_cause_service": data.get("root_cause"),
            "summary": data.get("summary"),
            "evidence_references": data.get("cited_evidence_ids", []),
            "evidence_statements_json": arbitrary_json(data.get("evidence_statements", [])),
            "missing_evidence": data.get("missing_evidence", []),
            "limitations": data.get("limitations", []),
            "valid": not data.get("used_fallback", False),
            "fallback": bool(data.get("used_fallback", False)),
            "fallback_reason": data.get("fallback_reason"),
            "provider": provider,
            "model_name": data.get("model_name"),
            "model_version": data.get("model_version"),
            "model_hash": data.get("model_hash"),
            "prompt_hash": data.get("prompt_hash"),
            "evidence_hash": evidence_hash,
            "latency_ms": data.get("latency_ms"),
            "tokens_prompt": data.get("tokens_prompt"),
            "tokens_completion": data.get("tokens_completion"),
            "reliable": data.get("reliability"),
            "confidence": data.get("confidence"),
            "payload_hash": sha256_value(data),
        }

    def experiment(self, run: Any, *, campaign_id: str) -> dict[str, Any]:
        data = sanitize(_mapping(run), evaluation=True)
        run_id = data.get("run_id")
        if not campaign_id or not run_id:
            raise ValueError("Resultado experimental requer campaign_id e run_id.")
        factors = data.get("factors") if isinstance(data.get("factors"), dict) else {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        return {
            "projection_version": PROJECTION_VERSION,
            "evaluation": True,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "hypothesis": data.get("hypothesis") or factors.get("hypothesis"),
            "experiment_family": data.get("experiment_family") or factors.get("experiment_family"),
            "dataset": data.get("dataset") or factors.get("dataset"),
            "system": data.get("system") or factors.get("system"),
            "protocol": data.get("protocol") or factors.get("protocol"),
            "model": data.get("model") or factors.get("model"),
            "feature_view": data.get("feature_view") or factors.get("feature_view"),
            "degradation": data.get("degradation") or factors.get("degradation"),
            "explanation_mode": data.get("explanation_mode") or factors.get("explanation_mode"),
            "status": data.get("status"),
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at"),
            "metrics_json": arbitrary_json(metrics, evaluation=True),
            "provenance_json": arbitrary_json(data.get("provenance", {}), evaluation=True),
            "payload_hash": sha256_value(data),
        }
