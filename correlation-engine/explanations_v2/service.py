from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any

from explanations_v2.client import LLMClient, LLMResponse
from explanations_v2.contracts import (
    EvidenceObjectV1,
    EvidenceReferenceV1,
    EvidenceStatementV1,
    ExplanationProvenanceRecordV1,
    PromptPayload,
    StructuredExplanation,
    sha256_canonical,
    H3_OUTPUT_KEYS,
    H3_EVIDENCE_STATEMENT_KEYS,
)
from explanations_v2.deterministic_explainer import DeterministicTemplateExplainer, UNCERTAINTY_POLICY_VERSION, compute_uncertainty
from explanations_v2.prompt_builder import PromptBuilder
from explanations_v2.validator import EvidenceValidator, ExplanationValidator


class RootCauseMismatchError(ValueError):
    pass


@dataclass(frozen=True)
class ExplanationMode:
    GENERIC = 'generic'
    RAW_TELEMETRY = 'raw_telemetry'
    EVIDENCE_GROUNDED = 'evidence_grounded'


@dataclass(frozen=True)
class ExplanationServiceResult:
    explanation: StructuredExplanation
    provenance: ExplanationProvenanceRecordV1
    prompt: PromptPayload
    evidence: EvidenceObjectV1


class ExplanationService:
    def __init__(self, *, llm_client: LLMClient | None = None, prompt_builder: PromptBuilder | None = None, evidence_validator: EvidenceValidator | None = None, explanation_validator: ExplanationValidator | None = None, deterministic_explainer: DeterministicTemplateExplainer | None = None, provenance_repository=None, temperature: float = 0.0) -> None:
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.evidence_validator = evidence_validator or EvidenceValidator()
        self.explanation_validator = explanation_validator or ExplanationValidator()
        self.deterministic_explainer = deterministic_explainer or DeterministicTemplateExplainer()
        self.provenance_repository = provenance_repository
        self.temperature = temperature

    def explain(self, rca_result, *, mode: str = ExplanationMode.EVIDENCE_GROUNDED,
                raw_telemetry=()) -> ExplanationServiceResult:
        evidence = self._build_evidence(rca_result)
        self.evidence_validator.validate(evidence)
        prompt = self.prompt_builder.build(
            evidence, mode=mode, temperature=self.temperature,
            raw_telemetry=raw_telemetry)
        fallback = self.deterministic_explainer.explain(
            evidence, prompt_hash=prompt.prompt_hash, mode=mode,
            fallback_reason='no_llm_client',
            allowed_evidence_ids=prompt.sanitized_context.get(
                'allowed_evidence_ids', ()))
        response = None
        if self.llm_client is None:
            return self._finalize(evidence, prompt, fallback, response, True, 'no_llm_client')
        try:
            response = self.llm_client.generate(prompt.prompt, temperature=self.temperature)
            if not response.text.strip():
                raise ValueError('empty_response')
            parsed = self._parse_response(response.text, evidence, prompt, response)
            return self._finalize(evidence, prompt, parsed, response, False, None)
        except Exception as exc:
            # Scientific H3 providers are fail-closed: deterministic fallbacks would
            # turn a failed treatment into a different treatment.
            if getattr(self.llm_client, 'fail_closed', False):
                raise
            if isinstance(exc, TimeoutError):
                return self._finalize(evidence, prompt, fallback, response, True, 'timeout')
            if isinstance(exc, RootCauseMismatchError):
                return self._finalize(evidence, prompt, fallback, response, True, 'root_cause_mismatch')
            if isinstance(exc, json.JSONDecodeError):
                return self._finalize(evidence, prompt, fallback, response, True, 'invalid_json')
            if isinstance(exc, ValueError):
                reason = str(exc)
                if reason not in {
                    'invalid_schema', 'empty_response', 'invalid_evidence_reference', 'missing_evidence_citation'
                }:
                    reason = 'invalid_schema'
                return self._finalize(evidence, prompt, fallback, response, True, reason)
            return self._finalize(evidence, prompt, fallback, response, True, 'provider_error')
    def _build_evidence(self, rca_result) -> EvidenceObjectV1:
        ranking = tuple(getattr(rca_result, 'ranking', ()) or ())
        evidence_items = tuple(getattr(rca_result, 'evidence', ()) or ())
        metadata = getattr(rca_result, 'metadata', {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        trusted_root = getattr(rca_result, 'root_cause_service', None)
        trusted_confidence = float(getattr(rca_result, 'confidence', 0.0))
        trusted_reliability = bool(getattr(getattr(rca_result, 'reliability', None), 'is_reliable', False))
        trusted_margin = getattr(getattr(rca_result, 'reliability', None), 'margin_to_second', None)
        if ranking:
            if trusted_root != ranking[0].service_name:
                raise ValueError('root cause mismatch')
            if abs(trusted_confidence - ranking[0].scores.final_score) >= 1e-6:
                raise ValueError('confidence mismatch')
            rel_conf = getattr(getattr(rca_result, 'reliability', None), 'confidence', None)
            if rel_conf is not None and abs(trusted_confidence - rel_conf) >= 1e-6:
                raise ValueError('confidence mismatch')
        analysis_window = metadata.get('analysis_window')
        missing = []
        if analysis_window is None:
            missing.append('analysis_window')
        source_catalog: list[EvidenceReferenceV1] = []
        feature_values: dict[str, Any] = {}
        feature_baselines: dict[str, Any] = {}
        shap_refs: list[EvidenceReferenceV1] = []
        event_paths: list[str] = []
        dependency_paths: list[str] = []
        temporal_order: list[str] = []
        for item in evidence_items:
            d = item.to_dict() if hasattr(item, 'to_dict') else dict(item)
            evidence_type = _coerce_text(d.get('evidence_type'))
            direction = d.get('direction')
            if hasattr(direction, 'value'):
                direction = direction.value
            attrs = d.get('attributes', {}) if isinstance(d.get('attributes', {}), dict) else {}
            ref = EvidenceReferenceV1(
                evidence_id=_coerce_text(d.get('evidence_id')),
                evidence_type=evidence_type,
                source_component=_coerce_text(d.get('source_component', 'unknown')),
                service_name=d.get('service_name'),
                description=d.get('description'),
                metric_name=d.get('metric_name'),
                raw_value=d.get('raw_value'),
                normalized_value=d.get('normalized_value'),
                direction=direction,
                observed_at=_coerce_observed_at(d.get('observed_at') or attrs.get('observed_at')),
                selected_for_explanation=bool(d.get('selected_for_explanation', False)),
                source_trace_ids=_extract_ids(d, attrs, 'trace_id'),
                source_span_ids=_extract_ids(d, attrs, 'span_id'),
                source_log_ids=_extract_ids(d, attrs, 'log_id'),
                source_metric_series_ids=_extract_ids(d, attrs, 'metric_series_id'),
                source_business_event_ids=_extract_ids(d, attrs, 'business_event_id'),
            )
            source_catalog.append(ref)
            if ref.evidence_type == 'shap' and ref.service_name == trusted_root:
                shap_refs.append(ref)
                if 'feature_value' in attrs:
                    feature_values[ref.metric_name or ref.evidence_id] = attrs['feature_value']
                else:
                    missing.append('feature_value')
                if 'baseline' in attrs:
                    feature_baselines[ref.metric_name or ref.evidence_id] = attrs['baseline']
                else:
                    missing.append('feature_baseline')
            if ref.evidence_type == 'temporal':
                temporal_order.append(ref.evidence_id)
                event_paths.append(ref.description or ref.evidence_id)
            if ref.evidence_type == 'graph':
                dependency_paths.append(ref.evidence_id)
                event_paths.append(ref.description or ref.evidence_id)
        root_evidence_ids = tuple(getattr(ranking[0], 'evidence_ids', ()) if ranking else ())
        if root_evidence_ids and not set(root_evidence_ids).issubset({item.evidence_id for item in source_catalog}):
            raise ValueError('root_cause_evidence_ids inválido')
        if root_evidence_ids:
            selected_ids = set(root_evidence_ids) | {item.evidence_id for item in shap_refs} | set(temporal_order) | set(dependency_paths)
        else:
            selected_ids = {item.evidence_id for item in shap_refs} | set(temporal_order) | set(dependency_paths)
        selected_catalog = tuple(item for item in source_catalog if item.evidence_id in selected_ids)
        top_shap_features = tuple(sorted(shap_refs, key=lambda ref: abs(float(ref.raw_value or 0.0)), reverse=True))
        if analysis_window is None:
            missing.append('analysis_window')
        if not root_evidence_ids and trusted_root is not None:
            missing.append('root_cause_without_citable_evidence')
        if trusted_margin is not None and ranking and len(ranking) > 1:
            calc_margin = ranking[0].scores.final_score - ranking[1].scores.final_score
            if abs(calc_margin - trusted_margin) >= 1e-6:
                raise ValueError('margin_to_second mismatch')
        return EvidenceObjectV1(
            rca_result_id=str(getattr(rca_result, 'rca_result_id')),
            trace_id=str(getattr(rca_result, 'trace_id')),
            incident_id=metadata.get('incident_id'),
            analysis_window=analysis_window,
            generated_at=getattr(rca_result, 'generated_at'),
            pipeline_version=str(getattr(rca_result, 'pipeline_version')),
            model_version=str(getattr(rca_result, 'model_version')),
            # Final H3 RCA artifacts bind the physical trained model under the
            # explicit materializer provenance key; older callers used model_hash.
            model_hash=str(metadata.get('model_hash') or
                           metadata.get('model_artifact_sha256') or ''),
            ranked_root_causes=tuple(item.service_name for item in ranking),
            model_probabilities={item.service_name: item.scores.model_probability for item in ranking},
            final_ranking_scores={item.service_name: item.scores.final_score for item in ranking},
            confidence=trusted_confidence,
            margin_to_second=trusted_margin,
            reliability=trusted_reliability,
            evidence_catalog=selected_catalog,
            root_cause_evidence_ids=root_evidence_ids,
            top_shap_features=top_shap_features,
            feature_values=feature_values,
            feature_baselines=feature_baselines,
            event_paths=tuple(event_paths),
            dependency_paths=tuple(dependency_paths),
            temporal_order=tuple(temporal_order),
            first_failure_event=temporal_order[0] if temporal_order else None,
            missing_evidence=tuple(dict.fromkeys(missing)),
            limitations=tuple(() if root_evidence_ids else ('root_cause_without_citable_evidence',)),
        )

    def _parse_response(self, text: str, evidence: EvidenceObjectV1, prompt: PromptPayload, response: LLMResponse) -> StructuredExplanation:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('invalid_schema')
        if set(data) != set(H3_OUTPUT_KEYS):
            raise ValueError('invalid_schema')
        trusted_root = evidence.ranked_root_causes[0] if evidence.ranked_root_causes else None
        returned_root = data['root_cause']
        if not isinstance(returned_root, str) or not returned_root.strip():
            raise ValueError('invalid_schema')
        if returned_root != trusted_root:
            raise RootCauseMismatchError()
        if not isinstance(data['summary'], str) or not data['summary'].strip():
            raise ValueError('invalid_schema')
        if not isinstance(data['propagation_path'], list) or not isinstance(data['recommendations'], list) or not isinstance(data['cited_evidence_ids'], list):
            raise ValueError('invalid_schema')
        statements = []
        for item in data['evidence_statements']:
            if not isinstance(item, dict) or set(item) != set(H3_EVIDENCE_STATEMENT_KEYS):
                raise ValueError('invalid_schema')
            statements.append(EvidenceStatementV1(text=item['text'], evidence_ids=item['evidence_ids']))
        cited = tuple(data['cited_evidence_ids'])
        if statements and not cited:
            raise ValueError('missing_evidence_citation')
        known = set(prompt.sanitized_context.get('allowed_evidence_ids', ()))
        if not set(cited).issubset(known):
            raise ValueError('invalid_evidence_reference')
        for stmt in statements:
            if not set(stmt.evidence_ids).issubset(known):
                raise ValueError('invalid_evidence_reference')
        explanation = StructuredExplanation(
            schema_version='structured-explanation-v1',
            root_cause=trusted_root,
            summary=data['summary'],
            evidence_statements=tuple(statements),
            propagation_path=tuple(data['propagation_path']),
            uncertainty=compute_uncertainty(evidence),
            missing_evidence=evidence.missing_evidence,
            limitations=evidence.limitations,
            recommendations=tuple(data['recommendations']),
            cited_evidence_ids=cited,
            mode=prompt.mode,
            model_name=response.model_name,
            model_version=evidence.model_version,
            model_hash=evidence.model_hash,
            prompt_hash=prompt.prompt_hash,
            confidence=evidence.confidence,
            reliability=evidence.reliability,
            used_fallback=False,
            fallback_reason=None,
        )
        self.explanation_validator.validate(explanation, evidence)
        return explanation

    def _finalize(self, evidence: EvidenceObjectV1, prompt: PromptPayload, explanation: StructuredExplanation, response: LLMResponse | None, used_fallback: bool, fallback_reason: str | None) -> ExplanationServiceResult:
        self._validate_citation_boundary(explanation, prompt)
        explanation = StructuredExplanation(
            schema_version='structured-explanation-v1',
            root_cause=explanation.root_cause,
            summary=explanation.summary,
            evidence_statements=explanation.evidence_statements,
            propagation_path=explanation.propagation_path,
            uncertainty=compute_uncertainty(evidence),
            missing_evidence=evidence.missing_evidence,
            limitations=evidence.limitations,
            recommendations=explanation.recommendations,
            cited_evidence_ids=explanation.cited_evidence_ids,
            mode=explanation.mode,
            model_name=(response.model_name if response is not None else self.deterministic_explainer.model_name),
            model_version=evidence.model_version,
            model_hash=evidence.model_hash,
            prompt_hash=prompt.prompt_hash,
            confidence=evidence.confidence,
            reliability=evidence.reliability,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            tokens_prompt=(response.tokens_prompt if response is not None else None),
            tokens_completion=(response.tokens_completion if response is not None else None),
            latency_ms=(response.latency_ms if response is not None and response.latency_ms is not None else 0),
            cost_usd=(response.cost_usd if response is not None else None),
        )
        self.explanation_validator.validate(explanation, evidence)
        provenance = ExplanationProvenanceRecordV1(
            provenance_id=sha256_canonical(
                {
                    'prompt_hash': prompt.prompt_hash,
                    'explanation_hash': sha256_canonical(explanation.to_dict()),
                    'response_hash': getattr(response, 'response_hash', None) if response is not None else None,
                    'rca_result_id': evidence.rca_result_id,
                    'trace_id': evidence.trace_id,
                }
            ),
            schema_version='explanation-provenance-v1',
            created_at=datetime.now(timezone.utc),
            rca_result_id=evidence.rca_result_id,
            trace_id=evidence.trace_id,
            root_cause_service=explanation.root_cause,
            evidence_hash=prompt.evidence_hash,
            prompt_builder_version=prompt.prompt_builder_version,
            prompt_schema_version=prompt.prompt_schema_version,
            prompt_hash=prompt.prompt_hash,
            response_hash=(getattr(response, 'response_hash', None) if response is not None else None),
            requested_model=getattr(self.llm_client, 'requested_model', None),
            actual_model=(response.model_name if response is not None else None),
            mode=prompt.mode,
            temperature=prompt.temperature,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            validation_status='passed',
            tokens_prompt=(response.tokens_prompt if response is not None else None),
            tokens_completion=(response.tokens_completion if response is not None else None),
            latency_ms=(response.latency_ms if response is not None else 0),
            cost_usd=(response.cost_usd if response is not None else None),
            explanation_hash=sha256_canonical(explanation.to_dict()),
            uncertainty_policy_version=UNCERTAINTY_POLICY_VERSION,
            evidence_object=evidence,
            prompt_payload=prompt,
            explanation=explanation,
        )
        if self.provenance_repository is not None:
            self.provenance_repository.save(provenance)
        return ExplanationServiceResult(explanation=explanation, provenance=provenance, prompt=prompt, evidence=evidence)

    @staticmethod
    def _validate_citation_boundary(explanation: StructuredExplanation,
                                    prompt: PromptPayload) -> None:
        allowed = set(prompt.sanitized_context.get('allowed_evidence_ids', ()))
        if not set(explanation.cited_evidence_ids).issubset(allowed):
            raise ValueError('invalid_evidence_reference')
        if any(not set(statement.evidence_ids).issubset(allowed)
               for statement in explanation.evidence_statements):
            raise ValueError('invalid_evidence_reference')


def _coerce_text(value: Any) -> str:
    if hasattr(value, 'value'):
        value = value.value
    if not isinstance(value, str):
        raise TypeError('valor textual inválido')
    text = value.strip()
    if not text:
        raise ValueError('valor textual vazio')
    return text


def _coerce_observed_at(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError('observed_at deve ser datetime, str ISO ou None')
    candidate = value.strip().replace('Z', '+00:00')
    if not candidate:
        raise ValueError('observed_at não pode ser vazio')
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('observed_at deve conter timezone')
    return parsed.astimezone(timezone.utc)


def _extract_ids(payload: dict[str, Any], attrs: dict[str, Any], key: str) -> tuple[str, ...]:
    values = []
    direct = payload.get(key)
    if isinstance(direct, str) and direct.strip():
        values.append(direct.strip())
    elif isinstance(direct, (list, tuple)):
        values.extend(item.strip() for item in direct if isinstance(item, str) and item.strip())
    attr_value = attrs.get(key)
    if isinstance(attr_value, str) and attr_value.strip():
        values.append(attr_value.strip())
    elif isinstance(attr_value, (list, tuple)):
        values.extend(item.strip() for item in attr_value if isinstance(item, str) and item.strip())
    return tuple(dict.fromkeys(values))
