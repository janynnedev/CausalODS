from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from explanations_v2.contracts import EvidenceObjectV1, EvidenceReferenceV1, EvidenceStatementV1, StructuredExplanation


@dataclass(frozen=True)
class EvidenceValidator:
    def validate(self, evidence: EvidenceObjectV1) -> None:
        if not evidence.rca_result_id or not evidence.trace_id:
            raise ValueError('rca_result_id e trace_id são obrigatórios')
        self._validate_score_mapping(evidence.model_probabilities, 'model_probabilities')
        self._validate_score_mapping(evidence.final_ranking_scores, 'final_ranking_scores')
        if set(evidence.model_probabilities) != set(evidence.final_ranking_scores):
            raise ValueError('model_probabilities e final_ranking_scores devem ter as mesmas chaves')
        if evidence.ranked_root_causes:
            root = evidence.ranked_root_causes[0]
            if abs(evidence.confidence - evidence.final_ranking_scores.get(root, evidence.confidence)) >= 1e-6:
                raise ValueError('confidence deve corresponder ao primeiro score final')
            if evidence.root_cause_evidence_ids:
                known = {item.evidence_id for item in evidence.evidence_catalog}
                if not set(evidence.root_cause_evidence_ids).issubset(known):
                    raise ValueError('root_cause_evidence_ids inválido')
        for item in evidence.evidence_catalog:
            self._validate_reference(item)
        for ref in evidence.top_shap_features:
            if ref.evidence_id not in {item.evidence_id for item in evidence.evidence_catalog}:
                raise ValueError('top_shap_features contém evidence_id inválido')
        if evidence.analysis_window is None:
            if 'analysis_window' not in evidence.missing_evidence:
                raise ValueError('analysis_window ausente deve ser declarado como missing_evidence')
        for item in evidence.missing_evidence + evidence.limitations + evidence.event_paths + evidence.dependency_paths + evidence.temporal_order:
            if not isinstance(item, str) or not item.strip():
                raise ValueError('campos textuais inválidos')

    def _validate_score_mapping(self, mapping: Any, field_name: str) -> None:
        if not hasattr(mapping, 'items'):
            raise TypeError(f'{field_name} deve ser mapeamento')
        for key, value in mapping.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f'chave inválida em {field_name}')
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f'{field_name}.{key} deve ser número')
            converted = float(value)
            if not math.isfinite(converted) or not 0.0 <= converted <= 1.0:
                raise ValueError(f'{field_name}.{key} fora de [0,1]')

    def _validate_reference(self, ref: EvidenceReferenceV1) -> None:
        if not isinstance(ref, EvidenceReferenceV1):
            raise TypeError('evidence_catalog deve conter EvidenceReferenceV1')
        if ref.normalized_value is not None and not 0.0 <= ref.normalized_value <= 1.0:
            raise ValueError('normalized_value inválido')
        if type(ref.selected_for_explanation) is not bool:
            raise TypeError('selected_for_explanation deve ser bool exato')


@dataclass(frozen=True)
class ExplanationValidator:
    def validate(self, explanation: StructuredExplanation, evidence: EvidenceObjectV1) -> None:
        trusted_root = evidence.ranked_root_causes[0] if evidence.ranked_root_causes else None
        if explanation.root_cause != trusted_root:
            raise ValueError('root_cause divergente')
        if explanation.confidence != evidence.confidence:
            raise ValueError('confidence imutável')
        if explanation.reliability != evidence.reliability:
            raise ValueError('reliability imutável')
        if explanation.uncertainty not in {'low', 'medium', 'high'}:
            raise ValueError('uncertainty inválida')
        if explanation.mode not in {'generic', 'raw_telemetry', 'evidence_grounded'}:
            raise ValueError('mode inválido')
        if explanation.used_fallback and explanation.fallback_reason is None:
            raise ValueError('fallback_reason obrigatório')
        if not explanation.used_fallback and explanation.fallback_reason is not None:
            raise ValueError('fallback_reason deve ser None')
        known = {item.evidence_id for item in evidence.evidence_catalog}
        if not set(explanation.cited_evidence_ids).issubset(known):
            raise ValueError('cited_evidence_ids inválidos')
        for stmt in explanation.evidence_statements:
            if not isinstance(stmt, EvidenceStatementV1):
                raise TypeError('evidence_statements deve conter EvidenceStatementV1')
            if not stmt.evidence_ids or not set(stmt.evidence_ids).issubset(known):
                raise ValueError('evidence citation inválida')
