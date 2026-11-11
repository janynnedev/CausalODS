"""Operational binding to the existing frozen H3 client (no contract copy)."""
from __future__ import annotations

import os

from explanations_v2 import ExplanationService


def build_explanation_service(provider: str | None = None) -> ExplanationService:
    selected = (provider or os.getenv("PLATFORM_V3_EXPLANATION_PROVIDER", "deterministic")).strip().lower()
    if selected == "deterministic":
        return ExplanationService()
    if selected != "openai":
        raise ValueError("Unknown explanation provider")
    # Imported only on explicit opt-in. from_environment validates the H3 freeze.
    from experiments.final_campaigns_v3.live_client import EnvironmentOpenAICompatibleClient
    from experiments.final_campaigns_v3.h3_openai_freeze import ChatMessageTokenEstimator, load_manifest
    manifest = load_manifest()
    estimator = ChatMessageTokenEstimator()
    if estimator.library_version != manifest["tokenizer"]["version"]:
        raise RuntimeError("H3_TOKENIZER_VERSION_MISMATCH")
    client = EnvironmentOpenAICompatibleClient.from_environment(
        max_output_tokens=manifest["max_completion_tokens"],
    )
    return ExplanationService(llm_client=client, temperature=manifest["temperature"])
