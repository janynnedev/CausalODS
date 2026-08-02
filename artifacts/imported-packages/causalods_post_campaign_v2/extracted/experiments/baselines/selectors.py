from __future__ import annotations

import math
import time

from datetime import datetime
from typing import Any

from analysis_utils import as_float
from baselines.contracts import BaselinePrediction, BaselineSample


def evidence_type(item: dict[str, Any]) -> str:
    return str(item.get("evidence_type", "")).lower()


def service_name(item: dict[str, Any]) -> str | None:
    value = item.get("service_name", item.get("service"))
    return str(value) if value else None


def normalized_score(item: dict[str, Any]) -> float | None:
    value = as_float(item.get("normalized_value"))
    if value is not None:
        return value

    raw = item.get("raw_value")
    if isinstance(raw, dict):
        return as_float(raw.get("score", raw.get("raw_score")))
    return as_float(raw)


def raw_mapping(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw_value")
    return raw if isinstance(raw, dict) else {}


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def finish_prediction(
    *,
    method: str,
    sample: BaselineSample,
    predicted_service: str | None,
    score: float | None,
    started: float,
    reason: str,
    evidence: dict[str, Any],
) -> BaselinePrediction:
    correct = (
        predicted_service == sample.ground_truth
        if sample.ground_truth is not None
        else predicted_service is None
    )

    return BaselinePrediction(
        method=method,
        source_version=sample.source_version,
        scenario=sample.scenario,
        iteration=sample.iteration,
        run_id=sample.run_id,
        trace_id=sample.trace_id,
        rca_result_id=sample.rca_result_id,
        ground_truth=sample.ground_truth,
        predicted_service=predicted_service,
        score=score,
        correct=correct,
        abstained=predicted_service is None,
        execution_time_ms=(time.perf_counter() - started) * 1000.0,
        decision_reason=reason,
        evidence=evidence,
    )


class MaxTemporalScoreBaseline:
    name = "max_temporal_score"

    def __init__(self, minimum_score: float = 0.0) -> None:
        self.minimum_score = float(minimum_score)

    def predict(self, sample: BaselineSample) -> BaselinePrediction:
        started = time.perf_counter()
        candidates: list[tuple[float, int, str, dict[str, Any]]] = []

        for item in sample.evidence():
            if evidence_type(item) != "temporal":
                continue
            service = service_name(item)
            score = normalized_score(item)
            if service is None or score is None:
                continue
            attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            rank_value = attributes.get("rank")
            try:
                rank = int(rank_value)
            except (TypeError, ValueError):
                rank = 10**9
            candidates.append((score, rank, service, item))

        if not candidates:
            return finish_prediction(
                method=self.name,
                sample=sample,
                predicted_service=None,
                score=None,
                started=started,
                reason="no_temporal_evidence",
                evidence={},
            )

        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        score, rank, service, item = candidates[0]

        if score <= self.minimum_score:
            return finish_prediction(
                method=self.name,
                sample=sample,
                predicted_service=None,
                score=score,
                started=started,
                reason="score_not_above_threshold",
                evidence={"threshold": self.minimum_score, "candidate": item},
            )

        return finish_prediction(
            method=self.name,
            sample=sample,
            predicted_service=service,
            score=score,
            started=started,
            reason="maximum_temporal_score",
            evidence={"rank": rank, "candidate": item},
        )


class MaxAnomalyZScoreBaseline:
    name = "max_anomaly_zscore"

    def __init__(self, minimum_zscore: float = 0.0) -> None:
        self.minimum_zscore = float(minimum_zscore)

    def predict(self, sample: BaselineSample) -> BaselinePrediction:
        started = time.perf_counter()
        candidates: list[tuple[float, str, dict[str, Any]]] = []

        for item in sample.evidence():
            if evidence_type(item) != "anomaly":
                continue
            service = service_name(item)
            zscore = as_float(raw_mapping(item).get("zscore"))
            if service is None or zscore is None:
                continue
            candidates.append((zscore, service, item))

        if not candidates:
            return finish_prediction(
                method=self.name,
                sample=sample,
                predicted_service=None,
                score=None,
                started=started,
                reason="no_anomaly_evidence",
                evidence={},
            )

        candidates.sort(key=lambda item: (-item[0], item[1]))
        score, service, item = candidates[0]

        if score <= self.minimum_zscore:
            return finish_prediction(
                method=self.name,
                sample=sample,
                predicted_service=None,
                score=score,
                started=started,
                reason="zscore_not_above_threshold",
                evidence={"threshold": self.minimum_zscore, "candidate": item},
            )

        return finish_prediction(
            method=self.name,
            sample=sample,
            predicted_service=service,
            score=score,
            started=started,
            reason="maximum_anomaly_zscore",
            evidence={"candidate": item},
        )


class FirstAnomalyBaseline:
    name = "first_anomaly_in_time"

    def predict(self, sample: BaselineSample) -> BaselinePrediction:
        started = time.perf_counter()
        candidates: list[tuple[float, int, str, float | None, dict[str, Any]]] = []

        for item in sample.evidence():
            if evidence_type(item) != "temporal":
                continue

            service = service_name(item)
            if service is None:
                continue

            raw = raw_mapping(item)
            attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            is_first = bool(raw.get("is_first_anomaly", attributes.get("is_first_anomaly", False)))
            observed = (
                parse_timestamp(raw.get("anomaly_started_at"))
                or parse_timestamp(item.get("observed_at"))
            )

            if not is_first and observed is None:
                continue

            try:
                rank = int(attributes.get("rank", 10**9))
            except (TypeError, ValueError):
                rank = 10**9

            effective_time = observed.timestamp() if observed is not None else float('-inf')
            candidates.append((effective_time, rank, service, normalized_score(item), item))

        if not candidates:
            return finish_prediction(
                method=self.name,
                sample=sample,
                predicted_service=None,
                score=None,
                started=started,
                reason="no_timestamped_anomaly",
                evidence={},
            )

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        observed_timestamp, rank, service, score, item = candidates[0]

        return finish_prediction(
            method=self.name,
            sample=sample,
            predicted_service=service,
            score=score,
            started=started,
            reason="earliest_anomaly_timestamp",
            evidence={
                "observed_at": (
                    datetime.fromtimestamp(observed_timestamp).isoformat()
                    if math.isfinite(observed_timestamp)
                    else None
                ),
                "rank": rank,
                "candidate": item,
            },
        )
