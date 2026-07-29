from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExplanationEvidence:
    source: str
    statement: str
    service_name: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RCAExplanation:
    explanation_id: str
    rca_result_id: str
    trace_id: str
    root_cause_service: str | None
    confidence: float | None
    is_reliable: bool
    summary: str
    decision: str
    evidence: tuple[ExplanationEvidence, ...]
    alternatives: tuple[str, ...]
    limitations: tuple[str, ...]
    model_version: str
    pipeline_version: str
    generator_version: str = "causalods-explanation-v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [
            item.to_dict()
            for item in self.evidence
        ]
        payload["alternatives"] = list(self.alternatives)
        payload["limitations"] = list(self.limitations)
        return payload
