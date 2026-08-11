from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from explanations_v2.contracts import EvidenceObjectV1, EvidenceStatementV1, StructuredExplanation

UNCERTAINTY_POLICY_VERSION = 'uncertainty-policy-v1'


def compute_uncertainty(evidence: EvidenceObjectV1) -> str:
    if not evidence.reliability:
        return 'high'
    if evidence.missing_evidence:
        return 'medium'
    if evidence.margin_to_second is not None and evidence.margin_to_second < 0.1:
        return 'medium'
    return 'low'


@dataclass(frozen=True)
class DeterministicTemplateExplainer:
    model_name: str = 'deterministic-template'

    def explain(self, evidence: EvidenceObjectV1, *, prompt_hash: str, mode: str,
                fallback_reason: str | None = None,
                allowed_evidence_ids: Iterable[str] | None = None) -> StructuredExplanation:
        root = evidence.ranked_root_causes[0] if evidence.ranked_root_causes else None
        allowed = None if allowed_evidence_ids is None else set(allowed_evidence_ids)

        def permitted(values: Iterable[str]) -> tuple[str, ...]:
            identifiers = tuple(values)
            return identifiers if allowed is None else tuple(
                item for item in identifiers if item in allowed)

        statements = []
        root_ids = permitted(evidence.root_cause_evidence_ids)
        if root and root_ids:
            statements.append(EvidenceStatementV1(
                text='A causa raiz permaneceu inalterada porque o RCAResult já a escolheu como primeiro colocado.',
                evidence_ids=root_ids,
            ))
        shap_ids = permitted(item.evidence_id for item in evidence.top_shap_features)
        if shap_ids:
            statements.append(EvidenceStatementV1(
                text='As evidências SHAP da causa raiz sustentam a classificação.',
                evidence_ids=shap_ids,
            ))
        temporal_ids = permitted(
            item.evidence_id for item in evidence.evidence_catalog
            if item.evidence_type == 'temporal')
        if temporal_ids:
            statements.append(EvidenceStatementV1(
                text='A ordem temporal observada é compatível com a propagação do incidente.',
                evidence_ids=temporal_ids,
            ))
        graph_ids = permitted(
            item.evidence_id for item in evidence.evidence_catalog
            if item.evidence_type == 'graph')
        if graph_ids:
            statements.append(EvidenceStatementV1(
                text='A dependência estrutural observada sustenta a propagação do incidente.',
                evidence_ids=graph_ids,
            ))
        if not statements and root_ids:
            statements.append(EvidenceStatementV1(
                text='A explicação usa apenas as evidências citáveis da causa raiz.',
                evidence_ids=root_ids,
            ))
        cited = tuple(dict.fromkeys(item for stmt in statements for item in stmt.evidence_ids))
        return StructuredExplanation(
            schema_version='structured-explanation-v1',
            root_cause=root,
            summary=f'Causa raiz indicada: {root}' if root else 'Nenhuma causa raiz disponível.',
            evidence_statements=tuple(statements),
            propagation_path=(evidence.event_paths if allowed is None or allowed else ()),
            uncertainty=compute_uncertainty(evidence),
            missing_evidence=evidence.missing_evidence,
            limitations=evidence.limitations,
            recommendations=('Coletar mais evidências canônicas antes de elevar confiança operacional.',),
            cited_evidence_ids=cited,
            mode=mode,
            model_name=self.model_name,
            model_version=evidence.model_version,
            model_hash=evidence.model_hash,
            prompt_hash=prompt_hash,
            confidence=evidence.confidence,
            reliability=evidence.reliability,
            used_fallback=True,
            fallback_reason=fallback_reason or 'no_llm_client',
        )
