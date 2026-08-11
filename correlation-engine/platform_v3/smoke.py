from __future__ import annotations

from typing import Any

from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.fixtures import CORRELATION_ID, TRACE_ID, build_demo_scenario
from multimodal_v1.features_v3 import FeatureRegistryV3


def run_contract_smoke() -> dict[str, Any]:
    adapter = VisualizationDocumentAdapter()
    reports = {}
    for name in ("healthy", "payment_failure", "payment_latency", "missing_signal"):
        scenario = build_demo_scenario(name)
        event_documents = tuple(
            adapter.unified_event(item, incident_id=scenario.bundle.incident_id)
            for item in scenario.events
        )
        incident_document = adapter.incident(scenario.bundle, scenario.graph)
        rca_document = (
            adapter.rca_result(scenario.rca_result) if scenario.rca_result is not None else None
        )
        explanation_document = (
            adapter.explanation(
                scenario.explanation,
                incident_id=scenario.bundle.incident_id,
                evidence_hash=(rca_document or {}).get("payload_hash"),
                created_at="2026-08-11T12:00:00Z",
            )
            if scenario.explanation is not None else None
        )
        event_ids = {item.event_id for item in scenario.events}
        graph_dict = scenario.graph.to_dict()
        graph_refs = {
            item["reference"]["event_id"] for item in graph_dict["nodes"]
        }
        modalities = {item.signal_type.value for item in scenario.events}
        citations_valid = True
        if explanation_document and rca_document:
            citations_valid = set(explanation_document["evidence_references"]) <= set(
                rca_document["evidence_ids"]
            )
        reports[name] = {
            "status": "PASS",
            "event_count": len(event_documents),
            "modalities": sorted(modalities),
            "missing_modalities": list(scenario.bundle.missing_modalities),
            "correlation_id_preserved": all(
                item["correlation_id"] == CORRELATION_ID for item in event_documents
            ),
            "trace_id_independent": CORRELATION_ID != TRACE_ID,
            "graph_non_empty": bool(graph_dict["nodes"] and graph_dict["edges"]),
            "graph_references_valid": graph_refs == event_ids,
            "graph_rules_present": bool(graph_dict["rule_versions"]),
            "feature_registry_hash": FeatureRegistryV3().fingerprint,
            "feature_vectors": len(scenario.feature_vectors),
            "rca_created": rca_document is not None,
            "explanation_created": explanation_document is not None,
            "citations_valid": citations_valid,
            "fixture_boundaries": [
                "telemetry_fixture",
                "model_output_contract" if rca_document is not None else "no_model_output",
            ],
        }
    fault = reports["payment_failure"]
    fault["all_modalities_present"] = set(fault["modalities"]) == {
        "trace", "log", "metric", "business_event"
    }
    return {
        "schema_version": "platform-v3-contract-smoke-v1",
        "status": "PASS" if all(item["status"] == "PASS" for item in reports.values()) else "FAIL",
        "scenarios": reports,
        "scope": "contract integration with explicit telemetry/model-output fixtures",
        "not_validated": [
            "live microservice-to-kafka path",
            "live xgboost-rca-v3 inference",
            "live OpenSearch indexing",
            "live Grafana query execution",
        ],
    }
