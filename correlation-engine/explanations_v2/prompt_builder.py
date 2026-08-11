from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from explanations_v2.contracts import EvidenceObjectV1, PromptPayload


@dataclass(frozen=True)
class PromptBuilder:
    prompt_builder_version: str = 'prompt-builder-v1'
    prompt_schema_version: str = 'prompt-schema-v1'

    def build(self, evidence: EvidenceObjectV1, *, mode: str, temperature: float,
              raw_telemetry: Iterable[Mapping[str, Any]] = ()) -> PromptPayload:
        selected = self._selected_catalog(evidence)
        trusted = {
            'rca_result_id': evidence.rca_result_id,
            'trace_id': evidence.trace_id,
            'root_cause': evidence.ranked_root_causes[0] if evidence.ranked_root_causes else None,
            'confidence': evidence.confidence,
            'reliability': evidence.reliability,
            'mode': mode,
        }
        if mode == 'generic':
            sanitized = {**trusted, 'incident_description': evidence.incident_id,
                         'allowed_evidence_ids': []}
        elif mode == 'raw_telemetry':
            raw = self._sanitize_raw(raw_telemetry)
            sanitized = {
                **trusted,
                'raw_selection_policy_version': 'deterministic-raw-selection-v1',
                'selected_raw_telemetry': raw,
                'selected_raw_telemetry_ids': [item['selection_id'] for item in raw],
                'raw_telemetry_count_by_modality': self._counts(raw),
                'allowed_evidence_ids': [],
            }
        else:
            allowed_ids = [item['evidence_id'] for item in selected]
            sanitized = {**trusted,
            'ranked_root_causes': list(evidence.ranked_root_causes),
            'root_cause_evidence_ids': list(evidence.root_cause_evidence_ids),
            'evidence_catalog': selected,
            'allowed_evidence_ids': allowed_ids,
            'top_shap_features': [item.to_dict() for item in evidence.top_shap_features],
            'temporal_evidence': [item.to_dict() for item in evidence.evidence_catalog if item.evidence_type == 'temporal'],
            'graph_evidence': [item.to_dict() for item in evidence.evidence_catalog if item.evidence_type == 'graph'],
            'missing_evidence': list(evidence.missing_evidence),
            'limitations': list(evidence.limitations),
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

    def _sanitize_raw(self, values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        forbidden = {'ground_truth', 'fault_service', 'fault_location', 'is_root_cause'}
        def scrub(value):
            if hasattr(value, 'value'):
                return scrub(value.value)
            if isinstance(value, Mapping):
                return {str(key): scrub(item) for key, item in sorted(value.items())
                        if str(key).lower() not in forbidden}
            if isinstance(value, (list, tuple)):
                return [scrub(item) for item in value]
            return value
        normalized = []
        for item in values:
            payload = scrub(item)
            selection_id = str(payload.get('event_id') or payload.get('source_reference') or
                               hashlib.sha256(json.dumps(payload, sort_keys=True,
                                                        default=str).encode()).hexdigest()[:16])
            normalized.append({'selection_id': selection_id, 'payload': payload,
                               'modality': payload.get('signal_type', 'unknown')})
        normalized.sort(key=lambda item: item['selection_id'])
        return normalized[:50]

    @staticmethod
    def _counts(values: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        result = {}
        for item in values:
            modality = str(item.get('modality', 'unknown'))
            result[modality] = result.get(modality, 0) + 1
        return dict(sorted(result.items()))

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
