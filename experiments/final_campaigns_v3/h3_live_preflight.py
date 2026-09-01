"""Explicit three-call synthetic preflight; excluded from H3 science."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

from explanations_v2 import ExplanationService
from experiments.multimodal_v3.executors import H3ExperimentExecutor
from experiments.multimodal_v3.fixtures import smoke_observation, smoke_rca

from .h3_openai_freeze import CONDITIONS
from .live_client import EnvironmentOpenAICompatibleClient


class RecordingClient:
    is_live_provider = True
    fail_closed = True

    def __init__(self, delegate):
        self.delegate, self.requested_model, self.responses = delegate, delegate.requested_model, []

    def generate(self, prompt, *, temperature):
        response = self.delegate.generate(prompt, temperature=temperature)
        self.responses.append(response)
        return response


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        for mode in CONDITIONS:
            print(f"H3_LLM_LIVE_PREFLIGHT_{mode.upper()} = BLOCKED_NO_API_KEY")
        print("H3_GPT54_MINI_COMPATIBILITY = BLOCKED_NO_API_KEY")
        print("H3_LLM_LIVE_PREFLIGHT = BLOCKED_NO_API_KEY")
        return 2
    delegate = EnvironmentOpenAICompatibleClient.from_environment()
    client = RecordingClient(delegate)
    rca = smoke_rca()
    raw = tuple(event.to_dict() for event in smoke_observation().events)
    executor = H3ExperimentExecutor(
        ExplanationService(llm_client=client, temperature=0.0),
        {"synthetic-h3-preflight": rca},
        raw_telemetry={"synthetic-h3-preflight": raw}, allow_live_llm=True)
    hashes, records = set(), []
    for mode in CONDITIONS:
        run = SimpleNamespace(incident_id="synthetic-h3-preflight", explanation_mode=mode)
        _, provenance, _ = executor.execute(run)
        response = client.responses[-1]
        hashes.add(provenance["rca_result_hash"])
        records.append({"mode": mode, "requested_model": client.requested_model,
                        "returned_model": response.model_name, "temperature": 0.0,
                        "reasoning_effort": "none",
                        "estimated_input_tokens": delegate.last_estimated_input_tokens,
                        "provider_prompt_tokens": response.tokens_prompt,
                        "provider_completion_tokens": response.tokens_completion,
                        "finish_reason": response.finish_reason,
                        "rca_result_hash": provenance["rca_result_hash"], "status": "PASS"})
    if len(hashes) != 1 or len(client.responses) != 3:
        raise RuntimeError("H3_SYNTHETIC_PREFLIGHT_INVARIANT_FAILED")
    print(json.dumps(records, sort_keys=True))
    for mode in CONDITIONS:
        print(f"H3_LLM_LIVE_PREFLIGHT_{mode.upper()} = PASS")
    print("H3_GPT54_MINI_COMPATIBILITY = PASS")
    print("H3_LLM_LIVE_PREFLIGHT = PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
