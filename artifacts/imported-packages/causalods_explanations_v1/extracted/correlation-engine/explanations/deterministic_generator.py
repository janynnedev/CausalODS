from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from explanations.contracts import (
    ExplanationEvidence,
    RCAExplanation,
)
from explanations.evidence_adapter import (
    NormalizedRCA,
    normalize_rca_document,
    ranking_score,
    ranking_service,
)


GENERATOR_VERSION = "causalods-explanation-v1"


def _compact_text(value: Any) -> str:
    return " ".join(str(value).split())


def _evidence_service(item: Mapping[str, Any]) -> str | None:
    for key in (
        "service_name",
        "service",
        "target_service",
    ):
        value = item.get(key)
        if value is not None:
            return str(value)
    return None


def _evidence_source(item: Mapping[str, Any]) -> str:
    for key in (
        "source",
        "type",
        "evidence_type",
        "category",
    ):
        value = item.get(key)
        if value:
            return str(value)
    return "rca_evidence"


def _evidence_score(item: Mapping[str, Any]) -> float | None:
    for key in (
        "score",
        "value",
        "weight",
        "contribution",
    ):
        value = item.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _evidence_statement(item: Mapping[str, Any]) -> str:
    for key in (
        "description",
        "message",
        "statement",
        "reason",
        "explanation",
    ):
        value = item.get(key)
        if value:
            return _compact_text(value)

    ignored = {
        "metadata",
        "service",
        "service_name",
        "score",
        "value",
        "weight",
        "source",
        "type",
        "category",
    }
    fragments = []
    for key, value in item.items():
        if key in ignored or value in (None, "", [], {}):
            continue
        fragments.append(f"{key}={value}")

    return (
        "; ".join(fragments[:5])
        or "Evidência RCA registrada sem descrição textual."
    )


def _score_breakdown(
    normalized: NormalizedRCA,
) -> list[ExplanationEvidence]:
    if not normalized.ranking:
        return []

    top = normalized.ranking[0]
    scores = top.get("scores")
    if not isinstance(scores, Mapping):
        return []

    evidence: list[ExplanationEvidence] = []
    for key in sorted(scores):
        if key == "final_score":
            continue
        value = scores[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue

        evidence.append(
            ExplanationEvidence(
                source="score_breakdown",
                statement=(
                    f"Componente {key} contribuiu com "
                    f"{number:.4f} para o serviço classificado."
                ),
                service_name=normalized.root_cause_service,
                score=number,
                metadata={"component": key},
            )
        )

    return evidence


def _normalize_evidence(
    normalized: NormalizedRCA,
) -> list[ExplanationEvidence]:
    selected: list[ExplanationEvidence] = []
    target = normalized.root_cause_service

    for item in normalized.evidence:
        service = _evidence_service(item)
        if target and service and service != target:
            continue

        selected.append(
            ExplanationEvidence(
                source=_evidence_source(item),
                statement=_evidence_statement(item),
                service_name=service,
                score=_evidence_score(item),
                metadata={
                    key: value
                    for key, value in item.items()
                    if key not in {
                        "description",
                        "message",
                        "statement",
                        "reason",
                        "explanation",
                    }
                },
            )
        )

    selected.extend(_score_breakdown(normalized))

    selected.sort(
        key=lambda item: (
            item.score is None,
            -(item.score or 0.0),
            item.source,
            item.statement,
        )
    )
    return selected[:8]


def _alternatives(
    normalized: NormalizedRCA,
) -> tuple[str, ...]:
    alternatives: list[str] = []
    for item in normalized.ranking[1:4]:
        service = ranking_service(item)
        score = ranking_score(item)
        if not service:
            continue

        if score is None:
            alternatives.append(service)
        else:
            alternatives.append(
                f"{service} (score={score:.4f})"
            )

    return tuple(alternatives)


def _reliability_failures(
    normalized: NormalizedRCA,
) -> list[str]:
    reliability = normalized.reliability
    values = reliability.get("failed_rules")
    if isinstance(values, list):
        return [
            _compact_text(value)
            for value in values
        ]

    values = reliability.get("failures")
    if isinstance(values, list):
        return [
            _compact_text(value)
            for value in values
        ]

    return []


def _summary(normalized: NormalizedRCA) -> str:
    service = (
        normalized.root_cause_service
        or "nenhum serviço"
    )
    confidence = (
        f"{normalized.confidence:.4f}"
        if normalized.confidence is not None
        else "indisponível"
    )

    if normalized.is_reliable:
        return (
            f"O CausalODS classificou {service} como causa raiz "
            f"com confiança {confidence} e considerou a decisão confiável."
        )

    return (
        f"O CausalODS classificou {service} como principal candidato "
        f"com confiança {confidence}, mas a decisão não ultrapassou "
        "os critérios de confiabilidade."
    )


def _decision(normalized: NormalizedRCA) -> str:
    if normalized.is_reliable:
        return (
            "A decisão pode acionar o fluxo operacional de incidente, "
            "preservando a proveniência do RCAResult."
        )

    failures = _reliability_failures(normalized)
    if failures:
        return (
            "A decisão foi mantida como hipótese e não como incidente. "
            "Critérios não atendidos: "
            + "; ".join(failures)
            + "."
        )

    return (
        "A decisão foi mantida como hipótese e não deve acionar "
        "automaticamente um incidente."
    )


def _limitations(
    normalized: NormalizedRCA,
    evidence: list[ExplanationEvidence],
) -> tuple[str, ...]:
    limitations: list[str] = []

    if not normalized.is_reliable:
        limitations.append(
            "O resultado não ultrapassou o gate de confiabilidade."
        )

    if not evidence:
        limitations.append(
            "O RCAResult não contém evidências textuais suficientes "
            "para detalhar a decisão."
        )

    if normalized.model_version == "unknown":
        limitations.append(
            "A versão do modelo não foi registrada."
        )

    if normalized.pipeline_version == "unknown":
        limitations.append(
            "A versão da pipeline não foi registrada."
        )

    if not normalized.ranking:
        limitations.append(
            "O ranking completo de serviços não está disponível."
        )

    return tuple(limitations)


def generate_explanation(
    document: Mapping[str, Any],
) -> RCAExplanation:
    normalized = normalize_rca_document(document)
    evidence = _normalize_evidence(normalized)

    digest = hashlib.sha256(
        (
            normalized.rca_result_id
            + "|"
            + GENERATOR_VERSION
        ).encode("utf-8")
    ).hexdigest()[:24]

    return RCAExplanation(
        explanation_id=f"exp-{digest}",
        rca_result_id=normalized.rca_result_id,
        trace_id=normalized.trace_id,
        root_cause_service=normalized.root_cause_service,
        confidence=normalized.confidence,
        is_reliable=normalized.is_reliable,
        summary=_summary(normalized),
        decision=_decision(normalized),
        evidence=tuple(evidence),
        alternatives=_alternatives(normalized),
        limitations=_limitations(
            normalized,
            evidence,
        ),
        model_version=normalized.model_version,
        pipeline_version=normalized.pipeline_version,
        generator_version=GENERATOR_VERSION,
    )
