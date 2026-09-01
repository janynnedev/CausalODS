"""Fail-closed OpenAI contract and offline token accounting for final H3."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROVIDER = "openai"
MODEL_FAMILY = "GPT-5.4 Mini"
MODEL_ID = "gpt-5.4-mini-2026-03-17"
API_FAMILY = "chat-completions"
ENDPOINT_BASE = "https://api.openai.com/v1"
ENDPOINT_PATH = "/chat/completions"
REASONING_EFFORT = "none"
TEMPERATURE = 0.0
MAX_COMPLETION_TOKENS = 1024
CONDITIONS = ("generic", "raw_telemetry", "evidence_grounded")
from explanations_v2.contracts import H3_OUTPUT_KEYS, H3_EVIDENCE_STATEMENT_KEYS

DEVELOPER_MESSAGE = """Return exactly one JSON object.
Do not return Markdown.
Do not return code fences.
Do not return text before or after the JSON object.
Do not add fields.

The object must contain exactly these keys:
root_cause
summary
evidence_statements
propagation_path
recommendations
cited_evidence_ids

Expected types:
root_cause: string
summary: non-empty string
evidence_statements: array of objects; each object has exactly:
  text: string
  evidence_ids: array of strings
propagation_path: array of strings
recommendations: array of strings
cited_evidence_ids: array of strings

root_cause MUST exactly equal the supplied root cause.
The model MUST NOT replace, reinterpret or recompute it.

Every evidence ID in evidence_statements[*].evidence_ids and cited_evidence_ids MUST belong to ALLOWED_EVIDENCE_IDS.
When ALLOWED_EVIDENCE_IDS = [], evidence_statements MUST equal [] and cited_evidence_ids MUST equal [].
Do not invent evidence IDs."""
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "configs/final-v3/h3-llm-freeze.json"


class H3LLMFreezeError(RuntimeError):
    pass


class H3InputTokenBudgetExceeded(H3LLMFreezeError):
    def __init__(self, *, incident_id: str, mode: str, estimated_tokens: int,
                 configured_budget: int) -> None:
        self.incident_id, self.mode = incident_id, mode
        self.estimated_tokens, self.configured_budget = estimated_tokens, configured_budget
        super().__init__(
            f"H3_INPUT_TOKEN_BUDGET_EXCEEDED:incident_id={incident_id}:mode={mode}:"
            f"estimated_tokens={estimated_tokens}:configured_budget={configured_budget}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(value: Any) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def messages_for_prompt(prompt: str) -> list[dict[str, str]]:
    return [{"role": "developer", "content": DEVELOPER_MESSAGE},
            {"role": "user", "content": prompt}]


def rendered_input_hash(prompt: str) -> str:
    return sha256(messages_for_prompt(prompt))


def prompt_provenance_fields(manifest: Mapping[str, Any], *, mode: str,
                             prompt: str, prompt_hash: str) -> dict[str, str]:
    if mode not in CONDITIONS or mode not in manifest.get("prompt_hashes", {}):
        raise H3LLMFreezeError("H3_PROMPT_PROVENANCE_MODE_INVALID")
    if not isinstance(prompt_hash, str) or len(prompt_hash) != 64:
        raise H3LLMFreezeError("H3_DYNAMIC_PROMPT_HASH_INVALID")
    return {
        "common_prompt_hash": manifest["prompt_hashes"]["common"],
        "prompt_template_hash": manifest["prompt_hashes"][mode],
        "prompt_hash": prompt_hash,
        "rendered_input_hash": rendered_input_hash(prompt),
    }


class ChatMessageTokenEstimator:
    """Deterministic tiktoken estimate including Chat Completions framing."""

    method = "tiktoken_chat_messages_estimate_v1"
    encoding_name = "o200k_base"

    def __init__(self) -> None:
        try:
            import tiktoken
        except ImportError as exc:
            raise H3LLMFreezeError("H3_TOKENIZER_NOT_AVAILABLE:tiktoken") from exc
        encoding = tiktoken.encoding_for_model(MODEL_ID)
        if encoding.name != self.encoding_name:
            raise H3LLMFreezeError(
                f"H3_TOKENIZER_MODEL_MAPPING_MISMATCH:{encoding.name}")
        self._encoding = encoding
        self.library_version = importlib.metadata.version("tiktoken")

    def count(self, messages: Sequence[Mapping[str, str]]) -> int:
        # Deterministic estimate: 3 framing tokens/message and 3 reply priming tokens.
        total = 3
        for message in messages:
            total += 3
            total += len(self._encoding.encode(message["role"]))
            total += len(self._encoding.encode(message["content"]))
        return total


def prompt_contract_hashes() -> dict[str, str]:
    # Static artifacts only; dynamic incident content is intentionally excluded.
    from explanations_v2.prompt_builder import PromptBuilder
    common = sha256(DEVELOPER_MESSAGE)
    render = inspect.getsource(PromptBuilder._render)
    build = inspect.getsource(PromptBuilder.build)
    raw_selection = inspect.getsource(PromptBuilder._sanitize_raw)
    return {
        "common": common,
        "generic": sha256({"render": render, "build": build, "mode": "generic",
                            "fields": ["incident_description", "allowed_evidence_ids"]}),
        "raw_telemetry": sha256({"render": render, "build": build,
                                  "raw_selection": raw_selection, "mode": "raw_telemetry",
                                  "selection": "deterministic-raw-selection-v1", "limit": 50}),
        "evidence_grounded": sha256({"render": render, "build": build,
                                      "mode": "evidence_grounded",
                                      "catalog": "selected-evidence-v1"}),
    }


def output_contract_hash() -> str:
    from explanations_v2.contracts import StructuredExplanation
    from explanations_v2.service import ExplanationService
    return sha256({"version": "structured-explanation-v1",
        "required": sorted(H3_OUTPUT_KEYS),
        "evidence_statement_keys": sorted(H3_EVIDENCE_STATEMENT_KEYS),
        "additionalProperties": False,
        "parser_source": inspect.getsource(ExplanationService._parse_response),
        "contract_source": inspect.getsource(StructuredExplanation)})


def output_instruction_binding_valid() -> bool:
    expected = (
        "root_cause", "summary", "evidence_statements", "propagation_path",
        "recommendations", "cited_evidence_ids")
    return (H3_OUTPUT_KEYS == expected
            and H3_EVIDENCE_STATEMENT_KEYS == ("text", "evidence_ids")
            and all(f"\n{key}\n" in f"\n{DEVELOPER_MESSAGE}\n" for key in H3_OUTPUT_KEYS)
            and "ALLOWED_EVIDENCE_IDS = []" in DEVELOPER_MESSAGE
            and "evidence_statements MUST equal []" in DEVELOPER_MESSAGE
            and "cited_evidence_ids MUST equal []" in DEVELOPER_MESSAGE)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise H3LLMFreezeError("H3_LLM_FREEZE_MANIFEST_INVALID") from exc
    validate_manifest(payload)
    return payload


def validate_manifest(value: Mapping[str, Any]) -> None:
    exact = {
        "experiment": "H3", "provider": PROVIDER, "model_family": MODEL_FAMILY,
        "model_id": MODEL_ID, "api_family": API_FAMILY,
        "endpoint_base": ENDPOINT_BASE, "endpoint_path": ENDPOINT_PATH,
        "reasoning_effort": REASONING_EFFORT, "temperature": TEMPERATURE,
        "top_p_policy": "provider_default_omitted", "api_seed_policy": "omitted",
        "max_completion_tokens": MAX_COMPLETION_TOKENS, "stream": False,
        "conditions": list(CONDITIONS), "final_incident_count": 40,
    }
    for key, expected in exact.items():
        if value.get(key) != expected:
            raise H3LLMFreezeError(f"H3_LLM_FREEZE_BINDING_MISMATCH:{key}")
    budget = value.get("input_token_budget")
    if budget not in {8192, 16384, 32768, 65536}:
        raise H3LLMFreezeError("H3_INPUT_TOKEN_BUDGET_NOT_FROZEN")
    if value.get("prompt_hashes") != prompt_contract_hashes():
        raise H3LLMFreezeError("H3_PROMPT_DRIFT")
    if value.get("output_contract_hash") != output_contract_hash():
        raise H3LLMFreezeError("H3_OUTPUT_CONTRACT_DRIFT")


def request_payload(prompt: str) -> dict[str, Any]:
    return {"model": MODEL_ID, "messages": messages_for_prompt(prompt),
            "reasoning_effort": REASONING_EFFORT, "temperature": TEMPERATURE,
            "max_completion_tokens": MAX_COMPLETION_TOKENS, "stream": False}


def contract_fingerprint(manifest: Mapping[str, Any]) -> str:
    return sha256({key: manifest[key] for key in (
        "provider", "model_id", "api_family", "reasoning_effort", "temperature",
        "top_p_policy", "api_seed_policy", "max_completion_tokens", "stream",
        "input_token_budget", "output_contract_hash")})
