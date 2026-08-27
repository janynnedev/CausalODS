from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from explanations_v2.client import LLMResponse


class LLMBindingNotConfigured(RuntimeError):
    pass


@dataclass
class EnvironmentOpenAICompatibleClient:
    """Small OpenAI-compatible binding configured only through environment variables."""

    requested_model: str
    endpoint: str
    api_key: str
    timeout_seconds: float = 60.0
    max_output_tokens: int = 1024
    is_live_provider: bool = True

    @classmethod
    def from_environment(cls, *, model: str | None = None,
                         endpoint: str | None = None, timeout_seconds: float = 60.0,
                         max_output_tokens: int = 1024) -> "EnvironmentOpenAICompatibleClient":
        resolved_model = model or os.environ.get("CAUSALODS_LLM_MODEL")
        resolved_endpoint = endpoint or os.environ.get("CAUSALODS_LLM_ENDPOINT")
        api_key = os.environ.get("CAUSALODS_LLM_API_KEY")
        if not resolved_model or not resolved_endpoint or not api_key:
            raise LLMBindingNotConfigured("LLM_BINDING_NOT_CONFIGURED")
        return cls(resolved_model, resolved_endpoint.rstrip("/"), api_key,
                   timeout_seconds, max_output_tokens)

    def generate(self, prompt: str, *, temperature: float) -> LLMResponse:
        body = json.dumps({
            "model": self.requested_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": self.max_output_tokens,
        }, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"LLM_PROVIDER_ERROR:{type(exc).__name__}") from exc
        try:
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
            model_name = payload.get("model") or self.requested_model
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM_PROVIDER_INVALID_RESPONSE") from exc
        return LLMResponse(
            text=text, model_name=model_name,
            tokens_prompt=usage.get("prompt_tokens"),
            tokens_completion=usage.get("completion_tokens"),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
