from __future__ import annotations

import json
from pathlib import Path

import pytest
from types import SimpleNamespace

from experiments.final_campaigns_v3 import h3_openai_freeze as freeze
from experiments.final_campaigns_v3.live_client import EnvironmentOpenAICompatibleClient
from experiments.causalods_campaign_bindings import _AuditedLLMClient, _H3ProvenanceExecutor
from experiments.multimodal_v3.executors import H3ExperimentExecutor, StageFailureV3
from experiments.multimodal_v3.fixtures import smoke_observation, smoke_rca
from explanations_v2 import ExplanationService, LLMResponse
from explanations_v2.contracts import H3_OUTPUT_KEYS, H3_EVIDENCE_STATEMENT_KEYS


def manifest(**changes):
    value = {
        "experiment": "H3", "provider": freeze.PROVIDER,
        "model_family": freeze.MODEL_FAMILY, "model_id": freeze.MODEL_ID,
        "api_family": freeze.API_FAMILY, "endpoint_base": freeze.ENDPOINT_BASE,
        "endpoint_path": freeze.ENDPOINT_PATH, "reasoning_effort": "none",
        "temperature": 0.0, "top_p_policy": "provider_default_omitted",
        "api_seed_policy": "omitted", "max_completion_tokens": 1024,
        "stream": False, "input_token_budget": 8192,
        "conditions": list(freeze.CONDITIONS), "final_incident_count": 40,
        "prompt_hashes": freeze.prompt_contract_hashes(),
        "output_contract_hash": freeze.output_contract_hash(),
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("invalid", [
    "gpt-5.4-mini", "gpt-5.4", "gpt-5.4-nano", "gpt-5.4-pro", "arbitrary-model"])
def test_exact_snapshot_rejects_every_alternative(invalid):
    with pytest.raises(freeze.H3LLMFreezeError, match="model_id"):
        freeze.validate_manifest(manifest(model_id=invalid))


def test_exact_snapshot_and_request_boundary_contract():
    freeze.validate_manifest(manifest())
    payload = freeze.request_payload("probe")
    assert payload == {
        "model": "gpt-5.4-mini-2026-03-17",
        "messages": [{"role": "developer", "content": freeze.DEVELOPER_MESSAGE},
                     {"role": "user", "content": "probe"}],
        "reasoning_effort": "none", "temperature": 0.0,
        "max_completion_tokens": 1024, "stream": False,
    }
    assert "top_p" not in payload and "seed" not in payload


def test_developer_instruction_and_parser_share_exact_output_contract():
    assert H3_OUTPUT_KEYS == (
        "root_cause", "summary", "evidence_statements", "propagation_path",
        "recommendations", "cited_evidence_ids")
    assert H3_EVIDENCE_STATEMENT_KEYS == ("text", "evidence_ids")
    assert freeze.output_instruction_binding_valid()
    assert freeze.DEVELOPER_MESSAGE == freeze.messages_for_prompt("user")[0]["content"]
    assert "ALLOWED_EVIDENCE_IDS = []" in freeze.DEVELOPER_MESSAGE


@pytest.mark.parametrize("field,value", [
    ("provider", "other"), ("api_family", "responses"),
    ("reasoning_effort", "low"), ("temperature", 0.1),
    ("max_completion_tokens", 2048), ("stream", True),
    ("top_p_policy", "explicit"), ("api_seed_policy", "42")])
def test_binding_mismatch_fails_closed(field, value):
    with pytest.raises(freeze.H3LLMFreezeError, match=field):
        freeze.validate_manifest(manifest(**{field: value}))


def test_prompt_drift_fails_without_refreeze():
    changed = manifest(prompt_hashes={**freeze.prompt_contract_hashes(), "generic": "0" * 64})
    with pytest.raises(freeze.H3LLMFreezeError, match="PROMPT_DRIFT"):
        freeze.validate_manifest(changed)


def client():
    return EnvironmentOpenAICompatibleClient(
        freeze.MODEL_ID, freeze.ENDPOINT_BASE, "test-secret", manifest=manifest())


@pytest.mark.parametrize("estimated,raises", [(8191, False), (8192, False), (8193, True)])
def test_input_budget_boundary_has_zero_network_calls(monkeypatch, estimated, raises):
    calls = []
    monkeypatch.setattr(freeze.ChatMessageTokenEstimator, "count", lambda self, messages: estimated)
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.ChatMessageTokenEstimator.count",
                        lambda self, messages: estimated)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(TimeoutError()))
    prompt = 'Return JSON only.\nDo not change root cause.\nALLOWED_EVIDENCE_IDS=[]\n{"incident_id":"i","mode":"generic"}'
    if raises:
        with pytest.raises(freeze.H3InputTokenBudgetExceeded):
            client().generate(prompt, temperature=0.0)
        assert calls == []
    else:
        with pytest.raises(RuntimeError, match="LLM_PROVIDER_ERROR"):
            client().generate(prompt, temperature=0.0)
        assert len(calls) == 1


def test_provider_failure_is_one_request_no_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.ChatMessageTokenEstimator.count",
                        lambda self, messages: 100)
    def fail(*args, **kwargs):
        calls.append(args[0].full_url)
        raise TimeoutError()
    monkeypatch.setattr("urllib.request.urlopen", fail)
    prompt = 'x\n{"incident_id":"i","mode":"generic"}'
    with pytest.raises(RuntimeError, match="LLM_PROVIDER_ERROR"):
        client().generate(prompt, temperature=0.0)
    assert calls == ["https://api.openai.com/v1/chat/completions"]


class FakeHTTPResponse:
    headers = {"x-request-id": "req-test"}
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


@pytest.mark.parametrize("payload,error", [
    ({"model": freeze.MODEL_ID, "choices": []}, "INVALID_RESPONSE"),
    ({"model": freeze.MODEL_ID, "choices": [{"message": {"content": ""},
       "finish_reason": "stop"}]}, "EMPTY_RESPONSE"),
    ({"model": freeze.MODEL_ID, "choices": [{"message": {"content": "{}"},
       "finish_reason": "length"}]}, "UNACCEPTABLE_FINISH_REASON:length"),
    ({"model": "gpt-5.4-mini", "choices": [{"message": {"content": "{}"},
       "finish_reason": "stop"}]}, "RETURNED_MODEL_MISMATCH"),
])
def test_response_validation_fails_closed(monkeypatch, payload, error):
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.ChatMessageTokenEstimator.count",
                        lambda self, messages: 100)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeHTTPResponse(payload))
    with pytest.raises(RuntimeError, match=error):
        client().generate('x\n{"incident_id":"i","mode":"generic"}', temperature=0.0)


def test_valid_response_preserves_provider_provenance(monkeypatch):
    payload = {"id": "chatcmpl-test", "model": freeze.MODEL_ID,
               "system_fingerprint": "fp_test",
               "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.ChatMessageTokenEstimator.count",
                        lambda self, messages: 100)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeHTTPResponse(payload))
    response = client().generate('x\n{"incident_id":"i","mode":"generic"}', temperature=0.0)
    assert (response.finish_reason, response.response_id, response.total_tokens,
            response.system_fingerprint, response.request_id) == (
                "stop", "chatcmpl-test", 12, "fp_test", "req-test")


def test_api_key_only_uses_openai_variable(monkeypatch, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()))
    monkeypatch.setattr("experiments.final_campaigns_v3.live_client.load_manifest",
                        lambda: manifest())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CAUSALODS_LLM_API_KEY", "legacy-must-not-work")
    with pytest.raises(RuntimeError, match="LLM_BINDING_NOT_CONFIGURED"):
        EnvironmentOpenAICompatibleClient.from_environment()


def test_prompt_hashes_and_contract_fingerprint_are_deterministic():
    assert freeze.prompt_contract_hashes() == freeze.prompt_contract_hashes()
    assert freeze.contract_fingerprint(manifest()) == freeze.contract_fingerprint(manifest())
    assert len(freeze.output_contract_hash()) == 64


class FrozenMockH3Client:
    requested_model = freeze.MODEL_ID
    is_live_provider = False
    fail_closed = True
    manifest = manifest()
    last_estimated_input_tokens = 100

    def __init__(self, override=None):
        self.override = override

    def generate(self, prompt, *, temperature):
        mode = json.loads(prompt.splitlines()[-1])["mode"]
        grounded = mode == "evidence_grounded"
        payload = {
            "root_cause": "payment-service", "summary": "Explicação controlada.",
            "evidence_statements": ([{"text": "Evidência temporal.",
                                      "evidence_ids": ["e-root"]}] if grounded else []),
            "propagation_path": [], "recommendations": ["Inspecionar serviço."],
            "cited_evidence_ids": (["e-root"] if grounded else []),
        }
        text = self.override(payload, mode) if self.override else json.dumps(payload)
        return LLMResponse(text, freeze.MODEL_ID, 10, 5, 1.0, 0.0,
                           finish_reason="stop", response_id="mock-response", total_tokens=15)


def h3_runtime(client=None):
    raw_client = client or FrozenMockH3Client()
    audited = _AuditedLLMClient(raw_client)
    rca = smoke_rca()
    raw = tuple(event.to_dict() for event in smoke_observation().events)
    delegate = H3ExperimentExecutor(
        ExplanationService(llm_client=audited, temperature=0.0), {"incident": rca},
        raw_telemetry={"incident": raw}, allow_live_llm=True)
    return _H3ProvenanceExecutor(delegate, audited, temperature=0.0)


@pytest.mark.parametrize("mode", freeze.CONDITIONS)
def test_mocked_h3_end_to_end_and_static_dynamic_provenance(mode):
    _, provenance, _ = h3_runtime().execute(
        SimpleNamespace(incident_id="incident", explanation_mode=mode))
    frozen = freeze.load_manifest()
    assert provenance["common_prompt_hash"] == frozen["prompt_hashes"]["common"]
    assert provenance["prompt_template_hash"] == frozen["prompt_hashes"][mode]
    assert len(provenance["prompt_hash"]) == 64
    assert provenance["rendered_input_hash"] == freeze.rendered_input_hash(
        provenance["prompt_text"])


@pytest.mark.parametrize("override", [
    lambda payload, mode: json.dumps({**payload, "additional": True}),
    lambda payload, mode: json.dumps({key: value for key, value in payload.items()
                                      if key != "summary"}),
    lambda payload, mode: json.dumps({**payload,
        "evidence_statements": [{"text": "invented", "evidence_ids": ["invented"]}],
        "cited_evidence_ids": ["invented"]}),
    lambda payload, mode: json.dumps({**payload, "root_cause": "order-service"}),
    lambda payload, mode: "```json\n" + json.dumps(payload) + "\n```",
    lambda payload, mode: "not JSON",
])
def test_mocked_h3_invalid_output_fails_closed_without_retry(override):
    client = FrozenMockH3Client(override)
    with pytest.raises(StageFailureV3):
        h3_runtime(client).execute(
            SimpleNamespace(incident_id="incident", explanation_mode="evidence_grounded"))
