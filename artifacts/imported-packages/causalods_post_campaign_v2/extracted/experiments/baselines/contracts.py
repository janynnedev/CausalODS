from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class BaselineSample:
    source_version: str
    scenario: str
    iteration: int
    run_id: str
    trace_id: str
    rca_result_id: str
    ground_truth: str | None
    campaign_row: dict[str, Any]
    rca_document: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BaselineSample":
        required = (
            "source_version",
            "scenario",
            "iteration",
            "run_id",
            "trace_id",
            "rca_result_id",
            "campaign_row",
            "rca_document",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"BaselineSample incompleto: {missing}")

        return cls(
            source_version=str(payload["source_version"]),
            scenario=str(payload["scenario"]),
            iteration=int(payload["iteration"]),
            run_id=str(payload["run_id"]),
            trace_id=str(payload["trace_id"]),
            rca_result_id=str(payload["rca_result_id"]),
            ground_truth=(
                str(payload["ground_truth"])
                if payload.get("ground_truth")
                else None
            ),
            campaign_row=dict(payload["campaign_row"]),
            rca_document=dict(payload["rca_document"]),
        )

    def evidence(self) -> list[dict[str, Any]]:
        payload = self.rca_document.get("payload", self.rca_document)
        evidence = payload.get("evidence", []) if isinstance(payload, dict) else []
        return [item for item in evidence if isinstance(item, dict)]


@dataclass(frozen=True)
class BaselinePrediction:
    method: str
    source_version: str
    scenario: str
    iteration: int
    run_id: str
    trace_id: str
    rca_result_id: str
    ground_truth: str | None
    predicted_service: str | None
    score: float | None
    correct: bool | None
    abstained: bool
    execution_time_ms: float
    decision_reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaselineMethod(Protocol):
    name: str

    def predict(self, sample: BaselineSample) -> BaselinePrediction:
        ...
