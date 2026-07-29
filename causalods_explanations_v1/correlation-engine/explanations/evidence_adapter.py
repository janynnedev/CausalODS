from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedRCA:
    rca_result_id: str
    trace_id: str
    root_cause_service: str | None
    confidence: float | None
    is_reliable: bool
    ranking: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    reliability: dict[str, Any]
    model_version: str
    pipeline_version: str


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return list(value)
    return []


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _payload(document: Mapping[str, Any]) -> dict[str, Any]:
    # Export wrapper:
    # {"rca_document": {"payload": {...}}}
    rca_document = _mapping(document.get("rca_document"))
    if rca_document:
        payload = _mapping(rca_document.get("payload"))
        return payload or rca_document

    # OpenSearch source:
    # {"payload": {...}}
    payload = _mapping(document.get("payload"))
    return payload or dict(document)


def _source(document: Mapping[str, Any]) -> dict[str, Any]:
    rca_document = _mapping(document.get("rca_document"))
    return rca_document or dict(document)


def _ranking_item_service(item: Mapping[str, Any]) -> str | None:
    value = _first(
        item,
        "service_name",
        "service",
        "name",
    )
    return str(value) if value is not None else None


def _ranking_item_score(item: Mapping[str, Any]) -> float | None:
    scores = _mapping(item.get("scores"))
    value = _first(
        scores,
        "final_score",
        "score",
        "confidence",
    )
    if value is None:
        value = _first(
            item,
            "confidence",
            "score",
            "final_score",
        )
    return _float_or_none(value)


def normalize_rca_document(
    document: Mapping[str, Any],
) -> NormalizedRCA:
    source = _source(document)
    payload = _payload(document)

    raw_ranking = _sequence(payload.get("ranking"))
    ranking = tuple(
        dict(item)
        for item in raw_ranking
        if isinstance(item, Mapping)
    )

    raw_evidence = _sequence(payload.get("evidence"))
    evidence = tuple(
        dict(item)
        for item in raw_evidence
        if isinstance(item, Mapping)
    )

    reliability = _mapping(payload.get("reliability"))

    top_item = ranking[0] if ranking else {}
    root_cause = _ranking_item_service(top_item)
    if root_cause is None:
        value = _first(
            source,
            "top_service",
            "root_cause_service",
        )
        root_cause = (
            str(value)
            if value is not None
            else None
        )

    confidence = _ranking_item_score(top_item)
    if confidence is None:
        confidence = _float_or_none(
            _first(
                payload,
                "confidence",
                "final_score",
            )
        )
    if confidence is None:
        confidence = _float_or_none(
            _first(
                source,
                "confidence",
                "final_score",
            )
        )

    reliable_value = _first(
        reliability,
        "is_reliable",
        "reliable",
    )
    if reliable_value is None:
        reliable_value = _first(
            payload,
            "is_reliable",
            "reliable",
        )
    if reliable_value is None:
        reliable_value = _first(
            source,
            "is_reliable",
            "reliable",
        )

    rca_result_id = _first(
        source,
        "rca_result_id",
        "_id",
    )
    if rca_result_id is None:
        rca_result_id = _first(
            payload,
            "rca_result_id",
        )

    trace_id = _first(
        source,
        "trace_id",
    )
    if trace_id is None:
        trace_id = _first(
            payload,
            "trace_id",
        )

    if not rca_result_id:
        raise ValueError("rca_result_id ausente")
    if not trace_id:
        raise ValueError("trace_id ausente")

    model_version = str(
        _first(
            source,
            "model_version",
        )
        or _first(
            payload,
            "model_version",
        )
        or "unknown"
    )
    pipeline_version = str(
        _first(
            source,
            "pipeline_version",
        )
        or _first(
            payload,
            "pipeline_version",
        )
        or "unknown"
    )

    return NormalizedRCA(
        rca_result_id=str(rca_result_id),
        trace_id=str(trace_id),
        root_cause_service=root_cause,
        confidence=confidence,
        is_reliable=bool(reliable_value),
        ranking=ranking,
        evidence=evidence,
        reliability=reliability,
        model_version=model_version,
        pipeline_version=pipeline_version,
    )


def ranking_service(item: Mapping[str, Any]) -> str | None:
    return _ranking_item_service(item)


def ranking_score(item: Mapping[str, Any]) -> float | None:
    return _ranking_item_score(item)
