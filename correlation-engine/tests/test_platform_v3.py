from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from multimodal_v1.contracts import UnifiedEvent
from platform_v3.cli import main as cli_main
from platform_v3.constants import INDEX_NAMES, REQUIRED_DASHBOARDS, ROOT
from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.experiments import ExperimentArtifactImporter
from platform_v3.fixtures import CORRELATION_ID, TRACE_ID, build_demo_scenario
from platform_v3.opensearch import OpenSearchProjectionRepository, TemplateCatalog
from platform_v3.serialization import canonical_json
from platform_v3.smoke import run_contract_smoke
from platform_v3.validation import (
    FAIL, PASS, grafana_static_status, release_manifest, security_scan,
    v2_import_audit, validate_platform, write_validation_artifacts,
)


class FakeIndices:
    def __init__(self):
        self.templates = {}

    def put_index_template(self, *, name, body):
        self.templates[name] = body
        return {"acknowledged": True}


class FakeClient:
    def __init__(self):
        self.indices = FakeIndices()
        self.documents = {}
        self.search_body = None

    def index(self, *, index, id, body, refresh):
        self.documents[(index, id)] = dict(body)
        return {"result": "created"}

    def search(self, *, index, body):
        self.search_body = (index, body)
        hits = [
            {"_source": value} for (stored_index, _), value in self.documents.items()
            if stored_index == index
        ]
        return {"hits": {"hits": hits}}

    def count(self, *, index):
        return {"count": sum(key[0] == index for key in self.documents)}


def test_templates_are_explicit_versioned_and_bounded():
    catalog = TemplateCatalog()
    report = catalog.validate()
    assert report["valid"]
    assert set(report["field_counts"]) == set(INDEX_NAMES)
    assert max(report["field_counts"].values()) < 100
    for template in catalog.load().values():
        mappings = template["template"]["mappings"]
        assert mappings["dynamic"] is False
        assert template["template"]["settings"]["index.mapping.total_fields.limit"] == 300


def test_2000_arbitrary_attributes_do_not_create_mapping_fields():
    event = UnifiedEvent(
        event_id="stress-event", event_name="stress", event_type="log", signal_type="log",
        event_timestamp="2026-08-11T12:00:00Z", service_name="payment-service",
        attributes={f"arbitrary_{number}": number for number in range(2000)},
        resource_attributes={f"resource_{number}": number for number in range(2000)},
    )
    document = VisualizationDocumentAdapter().unified_event(event)
    assert len(json.loads(document["attributes_json"])) == 2000
    assert len(json.loads(document["resource_attributes_json"])) == 2000
    assert not any(key.startswith("arbitrary_") for key in document)
    assert TemplateCatalog().validate()["field_counts"]["unified_events"] < 100


def test_operational_projection_removes_ground_truth_and_secrets_recursively():
    event = UnifiedEvent(
        event_id="secure-event", event_name="secure", event_type="log", signal_type="log",
        event_timestamp="2026-08-11T12:00:00Z", service_name="payment-service",
        attributes={
            "ground_truth": "payment-service",
            "nested": {"fault_service": "payment-service", "authorization": "redacted-value"},
            "safe": "visible",
        },
    )
    attributes = json.loads(VisualizationDocumentAdapter().unified_event(event)["attributes_json"])
    assert attributes == {"nested": {}, "safe": "visible"}


def test_fixture_preserves_trace_correlation_multimodality_graph_and_features():
    scenario = build_demo_scenario("payment_failure")
    assert {item.signal_type.value for item in scenario.events} == {
        "trace", "log", "metric", "business_event",
    }
    assert {item.correlation_id for item in scenario.events} == {CORRELATION_ID}
    assert {item.trace_id for item in scenario.events if item.trace_id} == {TRACE_ID}
    assert TRACE_ID != CORRELATION_ID
    graph = scenario.graph.to_dict()
    assert graph["nodes"] and graph["edges"] and graph["rule_versions"]
    node_ids = {item["reference"]["event_id"] for item in graph["nodes"]}
    assert node_ids == {item.event_id for item in scenario.events}
    assert len(scenario.feature_vectors) == 2


def test_projection_links_incident_rca_evidence_explanation_and_model():
    scenario = build_demo_scenario("payment_failure")
    adapter = VisualizationDocumentAdapter()
    incident = adapter.incident(scenario.bundle, scenario.graph)
    rca = adapter.rca_result(
        scenario.rca_result, model_hash="a" * 64,
        feature_registry_hash="b" * 64,
    )
    explanation = adapter.explanation(
        scenario.explanation, incident_id=scenario.bundle.incident_id,
        evidence_hash=rca["payload_hash"], created_at="2026-08-11T12:00:00Z",
    )
    assert incident["incident_id"] == rca["incident_id"] == explanation["incident_id"]
    assert rca["trace_id"] in incident["trace_ids"]
    assert rca["model_version"] == "xgboost-rca-v3"
    assert rca["model_hash"] == "a" * 64
    assert set(explanation["evidence_references"]) <= set(rca["evidence_ids"])
    assert explanation["root_cause_service"] == rca["root_cause_service"]


def test_repository_provisions_indexes_idempotently_and_queries_canonical_field():
    client = FakeClient()
    repository = OpenSearchProjectionRepository(client)
    assert set(repository.provision()) == set(INDEX_NAMES)
    document = {"incident_id": "incident-1", "projection_version": "v3"}
    repository.index("incidents", "incident-1", document)
    repository.index("incidents", "incident-1", document)
    assert repository.document_count("incidents") == 1
    assert repository.query("incidents", "incident_id", "incident-1") == [document]
    assert client.search_body[1]["query"] == {"term": {"incident_id": "incident-1"}}


def _campaign(root: Path, *, corrupt: bool = False) -> Path:
    root.mkdir()
    (root / "runs").mkdir()
    campaign_id = "prompt-11-smoke"
    manifest = {
        "identity": {"schema_version": "campaign-manifest-v3", "campaign_id": campaign_id},
    }
    run_id = "a" * 64
    run = {
        "schema_version": "experiment-run-v3",
        "identity": {
            "campaign_id": campaign_id, "run_id": run_id, "hypothesis": "H4",
            "experiment_family": "H4-DEGRADATION", "protocol": "within-dataset",
        },
        "configuration": {
            "method": "xgboost-rca-v3", "graph_mode": "FULL",
            "degradation_operator": "span_loss", "degradation_severity": 0.3,
            "explanation_mode": None,
        },
        "input": {"dataset": "local"},
        "evaluation": {"top_1": 1.0, "mrr": 1.0, "ground_truth_services": ["payment-service"]},
        "provenance": {"orchestrator_version": "experimental-orchestrator-v3"},
        "status": {"state": "SUCCESS", "started_at": "2026-08-11T12:00:00Z", "finished_at": "2026-08-11T12:00:01Z"},
    }
    files = {
        "campaign-manifest.json": canonical_json(manifest) + "\n",
        f"runs/{run_id}.json": canonical_json(run) + "\n",
    }
    for relative, text in files.items():
        (root / relative).write_text(text, encoding="utf-8")
    sums = []
    for relative in sorted(files):
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        sums.append(f"{digest}  {relative}")
    (root / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    if corrupt:
        (root / f"runs/{run_id}.json").write_text("{}\n", encoding="utf-8")
    return root


def test_prompt11_import_validates_checksum_schema_ids_and_preserves_evaluation(tmp_path):
    documents = ExperimentArtifactImporter().load(_campaign(tmp_path / "campaign"))
    assert len(documents) == 1
    assert documents[0]["campaign_id"] == "prompt-11-smoke"
    assert documents[0]["hypothesis"] == "H4"
    assert documents[0]["evaluation"] is True
    metrics = json.loads(documents[0]["metrics_json"])
    assert metrics["ground_truth_services"] == ["payment-service"]


def test_prompt11_import_rejects_corrupted_artifact(tmp_path):
    with pytest.raises(ValueError, match="Checksum divergente"):
        ExperimentArtifactImporter().load(_campaign(tmp_path / "campaign", corrupt=True))


def test_grafana_provisioning_has_all_dashboards_datasources_and_queries():
    report = grafana_static_status()
    assert report["valid"], report["errors"]
    assert set(report["dashboards"]) == set(REQUIRED_DASHBOARDS)


def test_alerts_are_provisioned_without_external_receiver_or_secret():
    rules = (ROOT / "infrastructure/platform-v3/grafana/provisioning/alerting/rules.yaml").read_text()
    assert "causalods-v3-service-down" in rules
    assert "contactPoints" not in rules
    assert security_scan()["valid"]


def test_smoke_covers_four_scenarios_and_declares_fixture_boundaries():
    report = run_contract_smoke()
    assert report["status"] == PASS
    assert set(report["scenarios"]) == {
        "healthy", "payment_failure", "payment_latency", "missing_signal",
    }
    assert report["scenarios"]["payment_failure"]["all_modalities_present"]
    assert report["scenarios"]["healthy"]["rca_created"] is False
    assert report["scenarios"]["missing_signal"]["missing_modalities"] == ["log", "metric"]
    assert report["not_validated"]


def test_release_validation_is_honest_about_missing_operational_model():
    report = validate_platform(live=False)
    gates = {item["name"]: item for item in report["gates"]}
    assert gates["v2_runtime_isolation"]["status"] == PASS
    assert gates["operational_xgboost_rca_v3"]["status"] == FAIL
    assert report["decision"] == "NOT_READY_FOR_FINAL_EXPERIMENTS"


def test_release_artifacts_include_manifest_hash_and_machine_readable_gates(tmp_path):
    paths = write_validation_artifacts(tmp_path)
    report = json.loads(paths["report_json"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest_json"].read_text(encoding="utf-8"))
    checksum = paths["manifest_sha256"].read_text(encoding="utf-8").split()[0]
    assert report["decision"] == "NOT_READY_FOR_FINAL_EXPERIMENTS"
    assert manifest["validation"]["decision"] == report["decision"]
    assert checksum == hashlib.sha256(paths["manifest_json"].read_bytes()).hexdigest()
    assert "release-manifest.json" in paths["manifest_sha256"].read_text(encoding="utf-8")


def test_cli_smoke_and_validate_return_semantic_status(tmp_path, capsys):
    assert cli_main(["smoke"]) == 0
    assert cli_main(["validate", "--output", str(tmp_path)]) == 2
    assert (tmp_path / "platform-validation-report.json").is_file()
    assert "NOT_READY_FOR_FINAL_EXPERIMENTS" in capsys.readouterr().out


def test_v2_import_audit_remains_green():
    # O módulo legado test_training.py regrava xgboost_rca.pkl na coleta global.
    # O hash congelado é restaurado e validado pelo release gate após a suíte.
    assert v2_import_audit() == {"valid": True, "imports": [], "count": 0}
