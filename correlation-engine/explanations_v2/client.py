from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in '0123456789abcdef' for ch in value)


def _validate_int(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f'{field_name} deve ser int não booleano ou None')
    if value < 0:
        raise ValueError(f'{field_name} não pode ser negativo')
    return value


def _validate_float(value: float | int | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f'{field_name} deve ser número finito ou None')
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f'{field_name} inválido')
    return converted


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_name: str
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    response_hash: str | None = None
    finish_reason: str | None = None
    response_id: str | None = None
    total_tokens: int | None = None
    system_fingerprint: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError('text deve ser str')
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError('model_name inválido')
        object.__setattr__(self, 'model_name', self.model_name.strip())
        object.__setattr__(self, 'tokens_prompt', _validate_int(self.tokens_prompt, 'tokens_prompt'))
        object.__setattr__(self, 'tokens_completion', _validate_int(self.tokens_completion, 'tokens_completion'))
        object.__setattr__(self, 'latency_ms', _validate_float(self.latency_ms, 'latency_ms'))
        object.__setattr__(self, 'cost_usd', _validate_float(self.cost_usd, 'cost_usd'))
        object.__setattr__(self, 'total_tokens', _validate_int(self.total_tokens, 'total_tokens'))
        computed = hashlib.sha256(self.text.encode('utf-8')).hexdigest()
        if self.response_hash is None:
            object.__setattr__(self, 'response_hash', computed)
        else:
            if not isinstance(self.response_hash, str) or not _is_sha256(self.response_hash):
                raise ValueError('response_hash inválido')
            if self.response_hash != computed:
                raise ValueError('response_hash não corresponde ao texto')


class LLMClient(Protocol):
    def generate(self, prompt: str, *, temperature: float) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, *, requested_model: str, endpoint: str | None = None, api_key: str | None = None) -> None:
        self.requested_model = requested_model
        self.endpoint = endpoint
        self.api_key = api_key

    def generate(self, prompt: str, *, temperature: float) -> LLMResponse:
        raise NotImplementedError('OpenAI-compatible client requires runtime integration')
