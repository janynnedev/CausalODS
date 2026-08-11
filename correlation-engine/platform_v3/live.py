from __future__ import annotations

from typing import Any
from pathlib import Path

from opensearchpy import OpenSearch

from multimodal_v1.contracts import UnifiedEvent
from platform_v3.constants import INDEX_NAMES
from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.experiments import ExperimentArtifactImporter
from platform_v3.fixtures import build_demo_scenario
from platform_v3.opensearch import OpenSearchProjectionRepository


def client(host: str = "localhost", port: int = 9200) -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": host, "port": port}], use_ssl=False, verify_certs=False,
        timeout=10,
    )


def seed_demo(
    *, host: str = "localhost", port: int = 9200,
    campaign_root: Path | None = None,
) -> dict[str, Any]:
    opensearch = client(host, port)
    if not opensearch.ping():
        raise RuntimeError("OpenSearch não está acessível.")
    repository = OpenSearchProjectionRepository(opensearch)
    provisioned = repository.provision()
    adapter = VisualizationDocumentAdapter()
    scenario_reports = {}
    for name in ("healthy", "payment_failure", "payment_latency", "missing_signal"):
        scenario = build_demo_scenario(name)
        for event in scenario.events:
            document = adapter.unified_event(event, incident_id=scenario.bundle.incident_id)
            repository.index("unified_events", document["event_id"], document)
        incident = adapter.incident(scenario.bundle, scenario.graph)
        repository.index("incidents", incident["incident_id"], incident)
        if scenario.rca_result is not None:
            rca = adapter.rca_result(
                scenario.rca_result,
                rca_result_id=f"fixture-rca-{name}",
                model_hash="0" * 64,
                feature_registry_hash=scenario.rca_result.metadata["feature_registry_hash"],
                graph_rule_versions=scenario.graph.rule_versions,
            )
            repository.index("rca_results", rca["rca_result_id"], rca)
            explanation = adapter.explanation(
                scenario.explanation,
                incident_id=scenario.bundle.incident_id,
                explanation_id=f"fixture-explanation-{name}",
                evidence_hash=rca["payload_hash"],
                created_at="2026-08-11T12:00:00Z",
            )
            repository.index("explanations", explanation["explanation_id"], explanation)
        scenario_reports[name] = {
            "incident_id": scenario.bundle.incident_id,
            "event_count": len(scenario.events),
            "rca_created": scenario.rca_result is not None,
        }

    # Stress real do mapping: 2.000 chaves permanecem dentro de attributes_json.
    stress = UnifiedEvent(
        event_id="platform-v3-mapping-stress",
        event_name="mapping stress",
        event_type="log",
        signal_type="log",
        event_timestamp="2026-08-11T12:00:00Z",
        service_name="validation-service",
        attributes={f"arbitrary_{number}": number for number in range(2000)},
        source_reference="fixture:mapping-stress",
    )
    stress_document = adapter.unified_event(stress)
    repository.index("unified_events", stress_document["event_id"], stress_document)
    mapping = opensearch.indices.get_mapping(index=INDEX_NAMES["unified_events"])
    properties = mapping[INDEX_NAMES["unified_events"]]["mappings"].get("properties", {})
    queries = {
        "incident_id": bool(repository.query(
            "incidents", "incident_id", scenario_reports["payment_failure"]["incident_id"], size=10,
        )),
        "trace_id": bool(repository.query("unified_events", "trace_id", "a" * 32, size=100)),
        "correlation_id": bool(repository.query(
            "unified_events", "correlation_id", "order-platform-v3-demo", size=100,
        )),
        "service": bool(repository.query(
            "unified_events", "service_name", "payment-service", size=100,
        )),
        "event_incident_link": bool(repository.query(
            "unified_events", "incident_id",
            scenario_reports["payment_failure"]["incident_id"], size=100,
        )),
    }
    experiment_import = None
    if campaign_root is not None:
        experiment_import = ExperimentArtifactImporter(repository).import_campaign(campaign_root)
        queries["campaign_id"] = bool(repository.query(
            "experiment_runs", "campaign_id", experiment_import["campaign_id"], size=100,
        ))
        queries["experiment_system"] = bool(repository.query(
            "experiment_runs", "system", "causalods", size=100,
        ))
    return {
        "status": "PASS" if all(queries.values()) and len(properties) < 1000 else "FAIL",
        "templates": list(provisioned),
        "scenarios": scenario_reports,
        "index_counts": {kind: repository.document_count(kind) for kind in INDEX_NAMES},
        "queries": queries,
        "experiment_import": experiment_import,
        "mapping_stress": {
            "arbitrary_attribute_count": 2000,
            "mapped_field_count": len(properties),
            "field_limit_not_exceeded": len(properties) < 1000,
        },
    }
