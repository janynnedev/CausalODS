from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from explanations_v2.contracts import EvidenceObjectV1, PromptPayload


@dataclass(frozen=True)
class PromptBuilder:
    prompt_builder_version: str = 'prompt-builder-v1'
    prompt_schema_version: str = 'prompt-schema-v1'

    def build(self, evidence: EvidenceObjectV1, *, mode: str, temperature: float) -> PromptPayload:
        selected = self._selected_catalog(evidence)
        allowed_ids = [item['evidence_id'] for item in selected]
        sanitized = {
            'rca_result_id': evidence.rca_result_id,
            'trace_id': evidence.trace_id,
            'root_cause': evidence.ranked_root_causes[0] if evidence.ranked_root_causes else None,
            'confidence': evidence.confidence,
            'reliability': evidence.reliability,
            'ranked_root_causes': list(evidence.ranked_root_causes),
            'root_cause_evidence_ids': list(evidence.root_cause_evidence_ids),
            'evidence_catalog': selected,
            'allowed_evidence_ids': allowed_ids,
            'top_shap_features': [item.to_dict() for item in evidence.top_shap_features],
            'temporal_evidence': [item.to_dict() for item in evidence.evidence_catalog if item.evidence_type == 'temporal'],
            'graph_evidence': [item.to_dict() for item in evidence.evidence_catalog if item.evidence_type == 'graph'],
            'missing_evidence': list(evidence.missing_evidence),
            'limitations': list(evidence.limitations),
            'mode': mode,
        }
        prompt = self._render(sanitized)
        evidence_hash = hashlib.sha256(json.dumps(evidence.to_dict(), sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
        prompt_hash = hashlib.sha256((self.prompt_builder_version + self.prompt_schema_version + prompt + evidence_hash).encode('utf-8')).hexdigest()
        return PromptPayload(
            prompt_builder_version=self.prompt_builder_version,
            prompt_schema_version=self.prompt_schema_version,
            mode=mode,
            temperature=temperature,
            prompt=prompt,
            prompt_hash=prompt_hash,
            evidence_hash=evidence_hash,
            evidence_object=evidence,
            sanitized_context=sanitized,
        )

    def _selected_catalog(self, evidence: EvidenceObjectV1) -> list[dict[str, Any]]:
        wanted = set(evidence.root_cause_evidence_ids)
        wanted.update(item.evidence_id for item in evidence.top_shap_features)
        wanted.update(item.evidence_id for item in evidence.evidence_catalog if item.selected_for_explanation)
        wanted.update(item.evidence_id for item in evidence.evidence_catalog if item.evidence_type in {'temporal', 'graph'})
        return [item.to_dict() for item in evidence.evidence_catalog if item.evidence_id in wanted]

    def _render(self, sanitized: dict[str, Any]) -> str:
        return '\n'.join([
            'Return JSON only.',
            'Do not change root cause.',
            f"ALLOWED_EVIDENCE_IDS={json.dumps(sanitized['allowed_evidence_ids'], ensure_ascii=True, sort_keys=True)}",
            json.dumps(sanitized, ensure_ascii=True, sort_keys=True),
        ])
