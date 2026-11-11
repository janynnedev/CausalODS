from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from platform_v3 import presentation_demo as demo
from platform_v3.cli import parser
from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.explanation_provider import build_explanation_service
from platform_v3.fixtures import build_demo_scenario
from multimodal_v1.event_graph import CandidatePruner
from multimodal_v1.evidence.contracts import EventReference


@pytest.fixture(autouse=True)
def no_paid_network(monkeypatch):
    # Even a developer with a real key cannot trigger a paid request in this file.
    def forbidden(*args, **kwargs):
        raise AssertionError("Network is forbidden in offline demo tests")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.fixture
def projections():
    scenario = build_demo_scenario("payment_failure")
    refs = tuple(EventReference(event.event_id, event.source_reference) for event in scenario.events
                 if event.service_name == "payment-service")
    pruning = CandidatePruner().prune(scenario.graph, ("order-service", "payment-service"), refs)
    adapter = VisualizationDocumentAdapter()
    incident = adapter.incident(scenario.bundle, scenario.graph, pruning=pruning)
    rca = adapter.rca_result(scenario.rca_result)
    explanation = adapter.explanation(scenario.explanation, incident_id=incident["incident_id"],
                                      explanation_id="explanation-test", provider="deterministic-fallback")
    # Fixtures are offline test inputs, never published by the live runner.
    explanation["fallback"] = True
    report = {"correlation_id": incident["correlation_ids"][0], "trace_id": rca["trace_id"],
              "incident_id": incident["incident_id"], "rca_result_id": rca["rca_result_id"],
              "explanation_id": explanation["explanation_id"], "status": "SUCCESS"}
    return report, incident, rca, explanation


def test_cli_defaults_and_explicit_options():
    args = parser().parse_args(["presentation-demo"])
    assert (args.runs, args.scenario, args.live_llm, args.export_graphs) == (10, "payment_failure", False, True)
    args = parser().parse_args(["presentation-demo", "--runs", "1", "--scenario", "payment_failure",
                               "--live-llm", "--export-graphs", "--output", "artifacts/presentation-demo"])
    assert args.runs == 1 and args.live_llm and args.export_graphs
    assert args.output == Path("artifacts/presentation-demo")


def test_default_provider_is_deterministic(monkeypatch):
    monkeypatch.delenv("PLATFORM_V3_EXPLANATION_PROVIDER", raising=False)
    assert build_explanation_service().llm_client is None
    with pytest.raises(ValueError):
        build_explanation_service("unknown")


def test_openai_injects_existing_frozen_client(monkeypatch):
    from experiments.final_campaigns_v3 import live_client, h3_openai_freeze as freeze
    sentinel = Mock(fail_closed=True)
    factory = Mock(return_value=sentinel)
    monkeypatch.setattr(live_client.EnvironmentOpenAICompatibleClient, "from_environment", factory)
    manifest = freeze.load_manifest()
    monkeypatch.setattr(freeze, "ChatMessageTokenEstimator", lambda: SimpleNamespace(
        library_version=manifest["tokenizer"]["version"]))
    service = build_explanation_service("openai")
    assert service.llm_client is sentinel
    factory.assert_called_once_with(max_output_tokens=manifest["max_completion_tokens"])


def test_tokenizer_version_drift_fails_closed(monkeypatch):
    from experiments.final_campaigns_v3 import h3_openai_freeze as freeze
    monkeypatch.setattr(freeze, "ChatMessageTokenEstimator", lambda: SimpleNamespace(library_version="wrong"))
    with pytest.raises(RuntimeError, match="VERSION_MISMATCH"):
        build_explanation_service("openai")


def test_projection_additions_preserve_contract_and_legacy_calls(projections):
    _, incident, _, explanation = projections
    pruning = json.loads(incident["pruning_json"])
    edges = json.loads(incident["graph_edges_json"])
    assert edges
    assert all("source" in edge and "target" in edge for edge in edges)
    assert incident["pruning_before_count"] == pruning["before_count"]
    assert incident["pruning_after_count"] == pruning["after_count"]
    assert incident["pruning_candidates"] == pruning["candidates"]
    assert isinstance(json.loads(explanation["propagation_path_json"]), list)
    assert isinstance(json.loads(explanation["recommendations_json"]), list)
    scenario = build_demo_scenario("payment_failure")
    legacy = VisualizationDocumentAdapter().incident(scenario.bundle, scenario.graph)
    assert json.loads(legacy["pruning_json"]) == {}


def test_existing_indexes_receive_additive_mapping_without_deletion():
    from platform_v3.opensearch import OpenSearchProjectionRepository, TemplateCatalog
    from platform_v3.constants import INDEX_NAMES
    client = Mock()
    client.indices.exists.return_value = True
    OpenSearchProjectionRepository(client).provision()
    assert client.indices.put_mapping.call_count == len(INDEX_NAMES)
    client.indices.delete.assert_not_called()
    mappings = TemplateCatalog().load()
    assert {"propagation_path_json", "recommendations_json"} <= set(
        mappings["explanations"]["template"]["mappings"]["properties"])
    assert "pruning_json" in mappings["incidents"]["template"]["mappings"]["properties"]


@pytest.mark.parametrize("field,value", [
    ("provider", "deterministic-fallback"), ("fallback", True), ("valid", False),
    ("model_name", "wrong-model"),
])
def test_live_fallback_or_invalid_model_is_failure(projections, field, value):
    from experiments.final_campaigns_v3.h3_openai_freeze import MODEL_ID
    report, incident, rca, explanation = projections
    explanation.update(provider="llm", fallback=False, valid=True, model_name=MODEL_ID)
    explanation[field] = value
    with pytest.raises(ValueError, match="Live LLM"):
        demo.validate_documents(report, incident, rca, explanation, live_llm=True)


@pytest.mark.parametrize("secret_content", [
    {"OPENAI_API_KEY": "redacted"}, {"Authorization": "redacted"},
    {"summary": "Bearer test-key"}, {"summary": "unit-test-secret"},
    {"graph_nodes_json": '{"value":"unit-test-secret"}'},
])
def test_secrets_never_reach_artifact(tmp_path, monkeypatch, secret_content):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-secret")
    path = tmp_path / "must-not-exist.json"
    with pytest.raises(ValueError, match="Sensitive"):
        demo.write_json(path, secret_content)
    assert not path.exists()


def test_seven_headless_pngs_share_layout_and_real_inputs(tmp_path, projections):
    from platform_v3.presentation_graph import STAGES, export_graphs
    _, incident, rca, _ = projections
    paths = export_graphs(incident, rca, tmp_path)
    assert [path.stem for path in paths] == list(STAGES)
    for path in paths:
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    layout = json.loads((tmp_path / "layout.json").read_text())
    refs = {json.dumps(node["reference"], sort_keys=True) for node in layout["nodes"]}
    original = json.loads(incident["graph_nodes_json"])
    assert refs == {json.dumps(node["reference"], sort_keys=True) for node in original}
    export_graphs(incident, rca, tmp_path / "repeat")
    assert layout == json.loads((tmp_path / "repeat/layout.json").read_text())


def _mock_campaign(monkeypatch, tmp_path, projections):
    report, incident, rca, explanation = projections
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fix/codex\n", returncode=0))
    monkeypatch.setattr(demo.live, "_git_commit", lambda: demo.BASE_COMMIT)
    monkeypatch.setattr(demo.live, "_now", lambda: "2026-11-11T12:00:00Z")
    providers, faults = [], []
    monkeypatch.setattr(demo, "_runtime_provider", providers.append)
    monkeypatch.setattr(demo.live, "_configure_fault", faults.append)
    monkeypatch.setattr(demo, "_verify_fault_mode", lambda mode: True)
    monkeypatch.setattr(demo, "_document", lambda kind, identifier: {
        "incidents": incident, "rca_results": rca, "explanations": explanation}[kind])
    def execute(folder, **kwargs):
        correlation = kwargs["correlation_id"]
        incident_id = f"incident-{correlation}"
        trace_id = f"trace-{correlation}"
        incident["incident_id"] = rca["incident_id"] = explanation["incident_id"] = incident_id
        incident["trace_ids"] = [trace_id]
        rca["trace_id"] = trace_id
        rca["rca_result_id"] = f"rca-{correlation}"
        explanation["explanation_id"] = f"explanation-{correlation}"
        incident["correlation_ids"] = [correlation]
        return {**report, "correlation_id": correlation, "trace_id": trace_id,
                "incident_id": incident_id, "rca_result_id": rca["rca_result_id"],
                "explanation_id": explanation["explanation_id"]}
    monkeypatch.setattr(demo.live, "execute_live_incident", execute)
    return tmp_path / "artifacts/presentation-demo", providers, faults


def test_multiple_runs_fault_setup_once_and_restoration(monkeypatch, tmp_path, projections):
    output, providers, faults = _mock_campaign(monkeypatch, tmp_path, projections)
    manifest = demo.run_presentation_demo(output, runs=10, export_graphs=False)
    assert manifest["campaign_status"] == "SUCCESS"
    assert faults == [True, False]
    assert providers == [False, False]
    assert len({run["correlation_id"] for run in manifest["runs"]}) == 10
    assert all(run["correlation_id"].startswith("presentation-demo-") for run in manifest["runs"])
    assert manifest["runs_successful"] == 10 and manifest["scientific_result"] is False
    assert manifest["featured_run"] == 1
    assert manifest["fault_restored"]


@pytest.mark.parametrize("failure", ["provider", "fault_setup", "incident", "restore"])
def test_campaign_always_restores_after_failure(monkeypatch, tmp_path, projections, failure):
    output, providers, faults = _mock_campaign(monkeypatch, tmp_path, projections)
    def fail(*args, **kwargs):
        raise TimeoutError("unit-test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-secret")
    if failure == "provider":
        def provider(live_llm):
            providers.append(live_llm)
            if len(providers) == 1:
                fail()
        monkeypatch.setattr(demo, "_runtime_provider", provider)
    elif failure in {"fault_setup", "restore"}:
        def fault(enabled):
            faults.append(enabled)
            if enabled == (failure == "fault_setup"):
                fail()
        monkeypatch.setattr(demo.live, "_configure_fault", fault)
    else:
        monkeypatch.setattr(demo.live, "execute_live_incident", fail)
    manifest = demo.run_presentation_demo(output, runs=1, export_graphs=False)
    assert manifest["campaign_status"] == "FAILED"
    if failure != "provider":
        assert faults[-1] is False
    assert providers[-1] is False
    if failure == "restore":
        assert manifest["fault_restored"] is False
    assert "unit-test-secret" not in (output / manifest["campaign_id"] / "manifest.json").read_text()


def test_live_missing_binding_is_recorded_without_network(monkeypatch, tmp_path, projections):
    output, _, _ = _mock_campaign(monkeypatch, tmp_path, projections)
    monkeypatch.setattr("platform_v3.explanation_provider.build_explanation_service",
                        Mock(side_effect=RuntimeError("binding absent")))
    result = demo.run_presentation_demo(output, runs=1, live_llm=True)
    assert result["campaign_status"] == "FAILED" and result["runs_successful"] == 0


def test_live_campaign_rejects_deterministic_success(monkeypatch, tmp_path, projections):
    output, _, faults = _mock_campaign(monkeypatch, tmp_path, projections)
    monkeypatch.setattr("platform_v3.explanation_provider.build_explanation_service", lambda *a: Mock())
    result = demo.run_presentation_demo(output, runs=1, live_llm=True, export_graphs=False)
    assert result["campaign_status"] == "FAILED"
    assert result["runs"][0]["status"] == "FAILED"
    assert result["fault_restored"] and faults == [True, False]


def test_provider_error_preserves_rca_without_fabricating_explanation(monkeypatch):
    from platform_v3.runtime import RuntimeProcessor
    from platform_v3 import fixtures
    monkeypatch.setattr(fixtures, "CORRELATION_ID", "presentation-demo-offline-test")
    scenario = fixtures.build_demo_scenario("payment_failure")
    model = SimpleNamespace(
        model_sha256="a" * 64,
        manifest={"feature_view_id": "test-view", "feature_view_sha256": "b" * 64},
        predict=lambda vectors: [{"service_name": vector.service_name,
                                  "probability": .99 if vector.service_name == "payment-service" else .01}
                                 for vector in vectors],
    )
    monkeypatch.delenv("PLATFORM_V3_EXPLANATION_PROVIDER", raising=False)
    repository = Mock()
    processor = RuntimeProcessor(model, repository)
    monkeypatch.setattr(processor, "_shap", lambda vectors: {
        vector.service_name: {"support_score": .5, "contributions": []} for vector in vectors})
    processor.explanations = Mock(llm_client=Mock())
    processor.explanations.explain.side_effect = TimeoutError("provider unavailable")
    with pytest.raises(TimeoutError):
        processor.process(scenario.bundle)
    kinds = [call.args[0] for call in repository.index.call_args_list]
    assert kinds == ["incidents", "rca_results", "incidents"]
    failed = repository.index.call_args_list[-1].args[2]
    assert failed["explanation_status"] == "FAILED"
    assert failed["explanation_error_code"] == "TimeoutError"
    assert failed["explanation_provider"] == "openai"
    assert "provider unavailable" not in str(failed)


def test_output_cannot_overwrite_scientific_artifacts(tmp_path):
    with pytest.raises(ValueError, match="output"):
        demo.run_presentation_demo(tmp_path / "final-experiments", runs=1)


def test_featured_prefers_reliable_then_first():
    runs = [{"run_number": 1, "status": "SUCCESS", "reliable": False},
            {"run_number": 2, "status": "FAILED", "reliable": True},
            {"run_number": 3, "status": "SUCCESS", "reliable": True},
            {"run_number": 4, "status": "SUCCESS", "reliable": True}]
    assert demo.select_featured(runs)["run_number"] == 3


@pytest.mark.parametrize("managed,count", [(True, 19), (False, 18)])
def test_shared_e2e_preserves_public_nineteen_stages(monkeypatch, tmp_path, projections, managed, count):
    live = demo.live
    report, incident, rca, explanation = projections
    from platform_v3.constants import INDEX_NAMES
    manifest = {"feature_registry_sha256": "features", "artifact_sha256": "model",
                "feature_view_sha256": "view", "model_name": "xgboost", "model_version": "v3"}
    rca.update(feature_registry_hash="features", model_hash="model", model_version="v3")
    monkeypatch.setattr(live, "operational_model_status", lambda *a: {"valid": True, "manifest": manifest})
    health_counter = iter(range(100))
    monkeypatch.setattr(live, "_runtime_health", lambda: {"ready": True, "consumed_events": next(health_counter)})
    faults = []
    monkeypatch.setattr(live, "_configure_fault", faults.append)
    monkeypatch.setattr(live, "_request", lambda *a, **k: (500, {"X-Correlation-Id": report["correlation_id"]}, {}))
    monkeypatch.setattr(live, "_find_trace_id", lambda cid: (report["trace_id"], [{"signal_type": "trace"}]))
    def search(index, field, value):
        return {INDEX_NAMES["incidents"]: [incident], INDEX_NAMES["rca_results"]: [rca],
                INDEX_NAMES["explanations"]: [explanation], INDEX_NAMES["unified_events"]: [{"signal_type": "trace"}]}[index]
    monkeypatch.setattr(live, "_search", search)
    monkeypatch.setattr(live, "_grafana_query", lambda *a: True)
    monkeypatch.setattr(live, "_tempo", lambda *a: (True, True))
    monkeypatch.setattr(live, "_git_commit", lambda: demo.BASE_COMMIT)
    monkeypatch.setattr(live, "source_snapshot_sha256", lambda: "snapshot")
    if managed:
        # Keep public arguments and generated correlation semantics unchanged.
        monkeypatch.setattr(live.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
        report["correlation_id"] = "platform-v3-e2e-fixed"
        result = live.run_fault_e2e(tmp_path)
        assert faults == [True, False]
    else:
        result = live.execute_live_incident(tmp_path, correlation_id=report["correlation_id"],
                                            manage_fault=False, write_report=False)
        assert faults == []
    assert result["status"] == "SUCCESS"
    assert len(result["stages"]) == count
    assert all(stage["status"] == "PASS" for stage in result["stages"])


def test_runtime_sink_batches_refresh_and_preserves_observed_identity():
    from platform_v3.runtime import RuntimeSink, RuntimeHealth
    from platform_v3.opensearch import OpenSearchProjectionRepository
    scenario = build_demo_scenario("payment_failure")
    client = Mock()
    client.bulk.return_value = {"errors": False}
    health = RuntimeHealth()
    sink = RuntimeSink(OpenSearchProjectionRepository(client), health)
    sink.deliver(scenario.events)
    client.index.assert_not_called()
    client.bulk.assert_called_once()
    body = client.bulk.call_args.kwargs["body"]
    documents = body[1::2]
    assert len(documents) == len(scenario.events)
    assert client.bulk.call_args.kwargs["refresh"] is False
    assert {d["correlation_id"] for d in documents} == {e.correlation_id for e in scenario.events}
    assert {d["trace_id"] for d in documents} == {e.trace_id for e in scenario.events}
    assert health.consumed_events == len(documents)
    assert sink.ready_bundles(idle_seconds=0)


def test_bulk_failure_does_not_confirm_ingestion():
    from platform_v3.runtime import RuntimeSink, RuntimeHealth
    from platform_v3.opensearch import OpenSearchProjectionRepository
    client = Mock()
    client.bulk.return_value = {"errors": True}
    health = RuntimeHealth()
    sink = RuntimeSink(OpenSearchProjectionRepository(client), health)
    events = build_demo_scenario("payment_failure").events
    with pytest.raises(RuntimeError, match="failed items"):
        sink.deliver(events)
    assert health.consumed_events == 0
    client.bulk.return_value = {"errors": False}
    sink.deliver(events)
    assert health.consumed_events == len(events)
    assert len(sink.assembler.active_bundles()[0].events) <= len(events)


@pytest.mark.parametrize("live_llm", [False, True])
def test_provider_recreation_always_uses_presentation_group_overlay(monkeypatch, live_llm):
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(demo.subprocess, "run", run)
    monkeypatch.setattr(demo.live, "_wait_until", Mock())
    demo._runtime_provider(live_llm)
    command = run.call_args.args[0]
    assert str(demo.ROOT / "docker-compose.presentation-demo.yml") in command
    assert run.call_args.kwargs["env"]["PLATFORM_V3_DEMO_PROVIDER"] == (
        "openai" if live_llm else "deterministic")


def test_provider_readiness_rejects_wrong_consumer_group(monkeypatch):
    monkeypatch.setattr(demo.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0)))
    health = {"ready": True, "explanation_provider": "deterministic",
              "consumer_group": "causalods-platform-v3-runtime"}
    monkeypatch.setattr(demo.live, "_runtime_health", lambda: health)
    def wait(predicate, **kwargs):
        assert predicate() is None
        health["consumer_group"] = "causalods-platform-v3-presentation-demo"
        assert predicate() == health
    monkeypatch.setattr(demo.live, "_wait_until", wait)
    demo._runtime_provider(False)


def test_validated_live_manifest_records_openai_binding(monkeypatch, tmp_path, projections):
    from experiments.final_campaigns_v3.h3_openai_freeze import MODEL_ID
    projections[3].update(provider="llm", fallback=False, valid=True, model_name=MODEL_ID)
    output, _, _ = _mock_campaign(monkeypatch, tmp_path, projections)
    monkeypatch.setattr("platform_v3.explanation_provider.build_explanation_service", lambda *args: Mock())
    result = demo.run_presentation_demo(output, runs=1, live_llm=True, export_graphs=False)
    assert result["campaign_status"] == "SUCCESS"
    assert result["runs"][0]["provider"] == "openai"
    assert result["runs"][0]["fallback"] is False


def test_explanation_failure_is_reported_without_timeout_or_secret(monkeypatch):
    from platform_v3.explanation_status import OperationalExplanationError
    def search(index, field, identifier):
        if index.endswith('explanations'):
            return []
        return [{'explanation_status': 'FAILED',
                 'explanation_error_code': 'LLM_PROVIDER_UNACCEPTABLE_FINISH_REASON:length'}]
    monkeypatch.setattr(demo.live, '_search', search)
    with pytest.raises(OperationalExplanationError, match='FINISH_REASON:length'):
        demo.live._find_explanation('real-incident')


def test_failure_code_does_not_export_provider_content():
    from platform_v3.explanation_status import explanation_error_code
    assert explanation_error_code(RuntimeError('Bearer secret response')) == 'EXPLANATION_PROCESSING_ERROR'
    assert explanation_error_code(ValueError('unit-test-secret')) == 'ValueError'
    assert explanation_error_code(RuntimeError('LLM_PROVIDER_EMPTY_RESPONSE')) == 'LLM_PROVIDER_EMPTY_RESPONSE'


def test_failed_explanation_retains_confirmed_rca_in_demo_summary(monkeypatch, tmp_path, projections):
    output, _, _ = _mock_campaign(monkeypatch, tmp_path, projections)
    def execute(folder, **kwargs):
        return {'status': 'FAILED', 'error': 'LLM_PROVIDER_EMPTY_RESPONSE',
                'operational_progress': {'root_cause_service': 'payment-service',
                                         'confidence': .768, 'event_count': 10,
                                         'provider': 'openai'},
                'rca_result_id': 'rca-real', 'incident_id': 'incident-real',
                'trace_id': 'trace-real', 'correlation_id': kwargs['correlation_id']}
    monkeypatch.setattr(demo.live, 'execute_live_incident', execute)
    result = demo.run_presentation_demo(output, runs=1, export_graphs=False)
    run = result['runs'][0]
    assert result['campaign_status'] == 'FAILED'
    assert run['root_cause_service'] == 'payment-service'
    assert run['event_count'] == 10 and run['confidence'] == .768
    assert run['provider'] == 'openai'
    assert run['error'] == 'LLM_PROVIDER_EMPTY_RESPONSE'
    assert run['explanation_id'] is None and run['fallback'] is None


def test_post_rca_context_parsing_and_projection_with_offline_response():
    from explanations_v2 import ExplanationService
    from explanations_v2.client import LLMResponse
    from experiments.final_campaigns_v3.h3_openai_freeze import MODEL_ID
    scenario = build_demo_scenario('payment_failure')
    client = Mock(spec=["generate", "fail_closed", "requested_model"],
                  fail_closed=True, requested_model=MODEL_ID)
    def generate(prompt, **kwargs):
        context = json.loads(prompt.splitlines()[-1])
        identifiers = context['allowed_evidence_ids'][:1]
        return LLMResponse(model_name=MODEL_ID, finish_reason='stop', text=json.dumps({
            'root_cause': context['root_cause'], 'summary': 'Evidence supports the supplied RCA.',
            'evidence_statements': [{'text': 'Observed evidence supports the ranking.',
                                     'evidence_ids': identifiers}],
            'propagation_path': [], 'recommendations': [], 'cited_evidence_ids': identifiers,
        }))
    client.generate.side_effect = generate
    from dataclasses import replace
    rca = replace(scenario.rca_result,
                  metadata={**scenario.rca_result.metadata, 'model_hash': 'a' * 64})
    result = ExplanationService(llm_client=client).explain(rca)
    document = VisualizationDocumentAdapter().explanation(
        result.explanation, incident_id=scenario.bundle.incident_id, provider='llm')
    assert document['fallback'] is False
    assert document['root_cause_service'] == scenario.rca_result.root_cause_service
    assert document['model_name'] == MODEL_ID
    client.generate.assert_called_once()
