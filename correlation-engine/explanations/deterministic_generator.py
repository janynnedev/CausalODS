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


GENERATOR_VERSION = "causalods-explanation-v1.1"
MAX_DOMAIN_EVIDENCE = 8
MAX_SCORE_EVIDENCE = 5

SOURCE_PRIORITY = {
    "temporal": 0,
    "shap": 1,
    "model": 2,
    "anomaly": 3,
    "graph": 4,
    "reliability": 5,
    "rca_evidence": 6,
    "score_breakdown": 10,
}


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


def _canonical_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "temporal_correlation": "temporal",
        "temporalcorrelator": "temporal",
        "shap_value": "shap",
        "shap_support": "shap",
        "prediction": "model",
        "model_prediction": "model",
        "anomaly_detection": "anomaly",
        "graph_structure": "graph",
        "reliability_gate": "reliability",
    }
    return aliases.get(text, text or "rca_evidence")


def _evidence_source(item: Mapping[str, Any]) -> str:
    for key in (
        "source",
        "type",
        "evidence_type",
        "category",
    ):
        value = item.get(key)
        if value:
            return _canonical_source(value)
    return "rca_evidence"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _evidence_score(item: Mapping[str, Any]) -> float | None:
    for key in (
        "score",
        "normalized_value",
        "value",
        "weight",
        "contribution",
    ):
        value = item.get(key)
        number = _number(value)
        if number is not None:
            return number
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

    metric_name = item.get("metric_name")
    normalized = _number(item.get("normalized_value"))
    service = _evidence_service(item)
    source = _evidence_source(item)

    if metric_name and normalized is not None:
        target = service or "serviço avaliado"
        return (
            f"A evidência {source} registrou {metric_name}="
            f"{normalized:.6f} para {target}."
        )

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
        "attributes",
        "tags",
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
        number = _number(scores[key])
        if number is None:
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

    evidence.sort(
        key=lambda item: (
            -(abs(item.score or 0.0)),
            item.statement,
        )
    )
    return evidence[:MAX_SCORE_EVIDENCE]


def _evidence_sort_key(
    item: ExplanationEvidence,
) -> tuple[Any, ...]:
    return (
        SOURCE_PRIORITY.get(item.source, 7),
        item.score is None,
        -(abs(item.score or 0.0)),
        item.source,
        item.statement,
    )


def _deduplicate(
    items: list[ExplanationEvidence],
) -> list[ExplanationEvidence]:
    result: list[ExplanationEvidence] = []
    seen: set[tuple[Any, ...]] = set()

    for item in items:
        key = (
            item.source,
            item.service_name,
            item.statement,
            item.score,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)

    return result


def _domain_evidence(
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

    selected = _deduplicate(selected)
    selected.sort(key=_evidence_sort_key)

    # Preserve diversity before filling remaining slots.
    diverse: list[ExplanationEvidence] = []
    seen_sources: set[str] = set()

    for item in selected:
        if item.source in seen_sources:
            continue
        diverse.append(item)
        seen_sources.add(item.source)
        if len(diverse) >= MAX_DOMAIN_EVIDENCE:
            return diverse

    for item in selected:
        if item in diverse:
            continue
        diverse.append(item)
        if len(diverse) >= MAX_DOMAIN_EVIDENCE:
            break

    return diverse


def _normalize_evidence(
    normalized: NormalizedRCA,
) -> list[ExplanationEvidence]:
    domain = _domain_evidence(normalized)
    score = _score_breakdown(normalized)
    return _deduplicate(domain + score)


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
