from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from explanations_v2.client import LLMResponse
from .h3_openai_freeze import (
    API_FAMILY, ENDPOINT_BASE, ENDPOINT_PATH, MODEL_ID, PROVIDER,
    ChatMessageTokenEstimator, H3InputTokenBudgetExceeded, H3LLMFreezeError,
    load_manifest, request_payload,
)


class LLMBindingNotConfigured(RuntimeError):
    pass


@dataclass
class EnvironmentOpenAICompatibleClient:
    """Frozen single-request OpenAI Chat Completions transport for H3."""

    requested_model: str
    endpoint: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    max_output_tokens: int = 1024
    is_live_provider: bool = True
    fail_closed: bool = True
    manifest: dict | None = None

    @classmethod
    def from_environment(cls, *, model: str | None = None,
                         endpoint: str | None = None, timeout_seconds: float = 60.0,
                         max_output_tokens: int = 1024) -> "EnvironmentOpenAICompatibleClient":
        manifest = load_manifest()
        resolved_model = model or manifest["model_id"]
        resolved_endpoint = endpoint or manifest["endpoint_base"]
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMBindingNotConfigured("LLM_BINDING_NOT_CONFIGURED")
        if (resolved_model != MODEL_ID or resolved_endpoint.rstrip("/") != ENDPOINT_BASE
                or max_output_tokens != manifest["max_completion_tokens"]):
            raise H3LLMFreezeError("H3_LLM_RUNTIME_BINDING_MISMATCH")
        return cls(resolved_model, resolved_endpoint.rstrip("/"), api_key,
                   timeout_seconds, max_output_tokens, True, True, manifest)

    def build_request_payload(self, prompt: str) -> dict:
        return request_payload(prompt)

    def generate(self, prompt: str, *, temperature: float) -> LLMResponse:
        manifest = self.manifest or load_manifest()
        if temperature != manifest["temperature"]:
            raise H3LLMFreezeError("H3_LLM_TEMPERATURE_MISMATCH")
        payload = self.build_request_payload(prompt)
        estimated = ChatMessageTokenEstimator().count(payload["messages"])
        context = json.loads(prompt.splitlines()[-1])
        if estimated > manifest["input_token_budget"]:
            raise H3InputTokenBudgetExceeded(
                incident_id=str(context.get("incident_id") or context.get("rca_result_id")),
                mode=str(context.get("mode")), estimated_tokens=estimated,
                configured_budget=manifest["input_token_budget"])
        self.last_estimated_input_tokens = estimated
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}{ENDPOINT_PATH}", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                request_id = response.headers.get("x-request-id")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM_PROVIDER_ERROR:{type(exc).__name__}") from exc
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
            usage = payload.get("usage", {})
            model_name = payload["model"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM_PROVIDER_INVALID_RESPONSE") from exc
        if model_name != MODEL_ID:
            raise RuntimeError("LLM_PROVIDER_RETURNED_MODEL_MISMATCH")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("LLM_PROVIDER_EMPTY_RESPONSE")
        if finish_reason != "stop":
            raise RuntimeError(f"LLM_PROVIDER_UNACCEPTABLE_FINISH_REASON:{finish_reason}")
        return LLMResponse(
            text=text, model_name=model_name,
            tokens_prompt=usage.get("prompt_tokens"),
            tokens_completion=usage.get("completion_tokens"),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=finish_reason, response_id=payload.get("id"),
            total_tokens=usage.get("total_tokens"),
            system_fingerprint=payload.get("system_fingerprint"),
            request_id=request_id,
        )
