from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import pytest

from explanations_v2 import (
    DeterministicTemplateExplainer,
    EvidenceObjectV1,
    EvidenceReferenceV1,
    EvidenceStatementV1,
    EvidenceValidator,
    ExplanationMode,
    ExplanationService,
    ExplanationValidator,
    InMemoryExplanationProvenanceRepository,
    LLMResponse,
    PromptBuilder,
    StructuredExplanation,
)
from models.evidence import EvidenceDirection, EvidenceItem, EvidenceType
from models.rca_result import (
    RCAResult,
    RankedService,
    ReliabilityDecision,
    ScoreBreakdown,
)


@dataclass
class FakeScore:
    model_probability: float
    final_score: float


@dataclass
class FakeRankedService:
    service_name: str
    scores: FakeScore
    evidence_ids: tuple[str, ...] = ()


@dataclass
class FakeEvidence:
    evidence_id: str
    evidence_type: str
    source_component: str = 'component'
    service_name: str | None = 'payment-service'
    description: str | None = 'desc'
    metric_name: str | None = 'latency'
    raw_value: float = 0.9
    normalized_value: float | None = 0.9
    direction: str | None = 'supporting'
    observed_at: datetime | None = None
    selected_for_explanation: bool = True
    source_trace_ids: tuple[str, ...] = ()
    source_span_ids: tuple[str, ...] = ()
    source_log_ids: tuple[str, ...] = ()
    source_metric_series_ids: tuple[str, ...] = ()
    source_business_event_ids: tuple[str, ...] = ()
    attributes: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            'evidence_id': self.evidence_id,
            'evidence_type': self.evidence_type,
            'source_component': self.source_component,
            'service_name': self.service_name,
            'description': self.description,
            'metric_name': self.metric_name,
            'raw_value': self.raw_value,
            'normalized_value': self.normalized_value,
            'direction': self.direction,
            'observed_at': self.observed_at,
            'selected_for_explanation': self.selected_for_explanation,
            'source_trace_ids': self.source_trace_ids,
            'source_span_ids': self.source_span_ids,
            'source_log_ids': self.source_log_ids,
            'source_metric_series_ids': self.source_metric_series_ids,
            'source_business_event_ids': self.source_business_event_ids,
            'attributes': self.attributes,
        }


@dataclass
class FakeReliability:
    is_reliable: bool
    confidence: float
    margin_to_second: float = 0.2


@dataclass
class FakeRCA:
    rca_result_id: str = 'rca-1'
    trace_id: str = 'trace-1'
    confidence: float = 0.569
    root_cause_service: str = 'payment-service'
    ranking: tuple = field(default_factory=lambda: (
        FakeRankedService('payment-service', FakeScore(0.99, 0.569), ('e-root',)),
        FakeRankedService('order-service', FakeScore(0.40, 0.20), ('e-alt',)),
    ))
    evidence: tuple = field(default_factory=lambda: (
        FakeEvidence('e-root', 'temporal', description='payment-service first failure', source_trace_ids=('trace-1',)),
        FakeEvidence('e-shap', 'shap', raw_value=0.62, normalized_value=0.62, source_component='shap', service_name='payment-service', metric_name='latency_zscore', source_trace_ids=('trace-1',), attributes={'feature_value': 4.8, 'baseline': 1.0}),
        FakeEvidence('e-alt', 'graph', service_name='order-service', description='order->payment', source_trace_ids=('trace-1',)),
    ))
    reliability: FakeReliability = field(default_factory=lambda: FakeReliability(True, 0.569, 0.369))
    model_version: str = 'xgboost-rca-v2'
    pipeline_version: str = 'causalods-rca-v3'
    generated_at: datetime = field(default_factory=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc))
    metadata: dict = field(default_factory=lambda: {'model_hash': 'hash-1', 'analysis_window': {'start': '2026-08-10T00:00:00Z', 'end': '2026-08-10T01:00:00Z'}})


class FakeClient:
    def __init__(self, response_text='{"root_cause":"payment-service","summary":"ok","evidence_statements":[{"text":"root","evidence_ids":["e-root"]}],"propagation_path":["p"],"recommendations":["r"],"cited_evidence_ids":["e-root"]}'):
        self.response_text = response_text
        self.calls = 0
        self.requested_model = 'gpt-4.1'

    def generate(self, prompt: str, *, temperature: float):
        self.calls += 1
        return LLMResponse(text=self.response_text, model_name='actual-model', tokens_prompt=12, tokens_completion=8, latency_ms=3, cost_usd=0.0)


class InvalidResponseClient:
    def __init__(self, text='not-json'):
        self.text = text
        self.requested_model = 'actual-model'
    def generate(self, prompt: str, *, temperature: float):
        return LLMResponse(text=self.text, model_name='actual-model', tokens_prompt=7, tokens_completion=1, latency_ms=5, cost_usd=0.01)


class InvalidSchemaClient:
    def __init__(self):
        self.requested_model = 'actual-model'

    def generate(self, prompt: str, *, temperature: float):
        return LLMResponse(
            text='{"root_cause":"payment-service","summary":"ok"}',
            model_name='actual-model',
            tokens_prompt=9,
            tokens_completion=2,
            latency_ms=4,
            cost_usd=0.01,
        )


class TimeoutClient:
    def __init__(self):
        self.requested_model = 'actual-model'

    def generate(self, prompt: str, *, temperature: float):
        raise TimeoutError('timeout')


class ProviderErrorClient:
    requested_model = 'actual-model'

    def generate(self, prompt: str, *, temperature: float):
        raise RuntimeError('provider unavailable')


def valid_llm_payload(**changes):
    payload = {
        'root_cause': 'payment-service',
        'summary': 'ok',
        'evidence_statements': [{'text': 'root', 'evidence_ids': ['e-root']}],
        'propagation_path': ['payment failure'],
        'recommendations': ['inspect payment'],
        'cited_evidence_ids': ['e-root'],
    }
    payload.update(changes)
    return payload


def client_for_payload(payload):
    return FakeClient(json.dumps(payload, separators=(',', ':')))


def build_real_rca_result(
    *,
    analysis_window: dict | None = None,
    generated_at: datetime | None = None,
) -> RCAResult:
    temporal = EvidenceItem(
        evidence_id='real-temporal',
        evidence_type=EvidenceType.TEMPORAL,
        source_component='temporal-correlator',
        description='payment-service first failure',
        service_name='payment-service',
        raw_value={'nested': {'values': [1]}},
        normalized_value=0.9,
        direction=EvidenceDirection.SUPPORTING,
        observed_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
        attributes={'trace_id': 'trace-real-1'},
    )
    shap = EvidenceItem(
        evidence_id='real-shap',
        evidence_type=EvidenceType.SHAP,
        source_component='shap-service',
        description='latency contribution',
        service_name='payment-service',
        metric_name='latency_zscore',
        raw_value=0.62,
        normalized_value=0.62,
        direction=EvidenceDirection.SUPPORTING,
        observed_at=datetime(2026, 8, 10, 0, 1, tzinfo=timezone.utc),
        attributes={'feature_value': {'nested': [4.8]}, 'baseline': {'nested': [1.0]}, 'trace_id': 'trace-real-1'},
    )
    graph = EvidenceItem(
        evidence_id='real-graph',
        evidence_type=EvidenceType.GRAPH,
        source_component='graph-builder',
        description='order->payment',
        service_name='order-service',
        raw_value=0.4,
        normalized_value=0.4,
        direction=EvidenceDirection.SUPPORTING,
        observed_at=datetime(2026, 8, 10, 0, 2, tzinfo=timezone.utc),
        attributes={'trace_id': 'trace-real-1'},
    )
    ranking = [
        RankedService(
            position=1,
            service_name='payment-service',
            scores=ScoreBreakdown(
                model_probability=0.99,
                shap_support_score=0.62,
                temporal_score=0.90,
                graph_score=0.20,
                anomaly_score=0.10,
                final_score=0.569,
                component_weights={'model_probability': 0.4, 'shap_support_score': 0.3, 'temporal_score': 0.2, 'graph_score': 0.1},
            ),
            evidence_ids=('real-temporal', 'real-shap'),
        ),
        RankedService(
            position=2,
            service_name='order-service',
            scores=ScoreBreakdown(
                model_probability=0.40,
                shap_support_score=0.20,
                temporal_score=0.10,
                graph_score=0.40,
                anomaly_score=0.30,
                final_score=0.200,
                component_weights={'model_probability': 0.4, 'shap_support_score': 0.3, 'temporal_score': 0.2, 'graph_score': 0.1},
            ),
            evidence_ids=('real-graph',),
        ),
    ]
    reliability = ReliabilityDecision(
        is_reliable=True,
        confidence=0.569,
        confidence_method='manual',
        minimum_confidence=0.5,
        margin_to_second=0.369,
        minimum_margin=0.1,
        passed_rules=('minimum_confidence',),
        failed_rules=(),
    )
    return RCAResult(
        rca_result_id='real-rca-1',
        trace_id='trace-real-1',
        root_cause_service='payment-service',
        confidence=0.569,
        ranking=tuple(ranking),
        evidence=(temporal, shap, graph),
        reliability=reliability,
        model_version='xgboost-rca-v2',
        pipeline_version='causalods-rca-v3',
        generated_at=generated_at or datetime(2026, 8, 10, 0, 5, tzinfo=timezone.utc),
        metadata={
            'model_hash': 'real-model-hash',
            'analysis_window': analysis_window
            or {
                'start': '2026-08-09T21:00:00-03:00',
                'end': '2026-08-09T22:00:00-03:00',
            },
        },
    )


def build_service(client=None):
    return ExplanationService(
        llm_client=client,
        prompt_builder=PromptBuilder(),
        evidence_validator=EvidenceValidator(),
        explanation_validator=ExplanationValidator(),
        deterministic_explainer=DeterministicTemplateExplainer(),
        provenance_repository=InMemoryExplanationProvenanceRepository(),
        temperature=0.0,
    )


def test_baseline_fallback_without_client():
    result = build_service().explain(FakeRCA(), mode=ExplanationMode.EVIDENCE_GROUNDED)
    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.confidence == 0.569
    assert result.explanation.reliability is True
    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'no_llm_client'


def test_llm_valid_preserves_root():
    result = build_service(FakeClient()).explain(FakeRCA())
    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.used_fallback is False
    assert result.explanation.confidence == 0.569
    assert result.explanation.reliability is True
    assert result.provenance.actual_model == 'actual-model'


def test_llm_root_mismatch_forces_fallback():
    bad = FakeClient('{"root_cause":"order-service","summary":"x","evidence_statements":[{"text":"bad","evidence_ids":["e-root"]}],"propagation_path":[],"recommendations":[],"cited_evidence_ids":["e-root"]}')
    result = build_service(bad).explain(FakeRCA())
    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'root_cause_mismatch'


def test_json_invalid_fallback_preserves_provenance():
    result = build_service(InvalidResponseClient()).explain(FakeRCA())
    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_json'
    assert result.provenance.actual_model == 'actual-model'
    assert result.provenance.tokens_prompt == 7


def test_invalid_evidence_id_rejected():
    svc = build_service()
    with pytest.raises(ValueError):
        svc.evidence_validator.validate(EvidenceObjectV1(
            rca_result_id='x', trace_id='y', incident_id=None, analysis_window=None, generated_at=datetime.now(timezone.utc), pipeline_version='p', model_version='m', model_hash='h', ranked_root_causes=('payment-service',), model_probabilities={'payment-service': 0.5}, final_ranking_scores={'payment-service': 0.5}, confidence=0.5, margin_to_second=0.2, reliability=True, evidence_catalog=(EvidenceReferenceV1(evidence_id='bad id', evidence_type='temporal', source_component='c'),), root_cause_evidence_ids=('bad id',), top_shap_features=(), feature_values={}, feature_baselines={}, event_paths=(), dependency_paths=(), temporal_order=(), first_failure_event=None, missing_evidence=(), limitations=(),
        ))


def test_immutability_deep_freeze():
    result = build_service().explain(FakeRCA())
    with pytest.raises(TypeError):
        result.evidence.model_probabilities['x'] = 1.0  # type: ignore[index]


def test_analysis_window_absent_is_missing_evidence():
    rca = replace(FakeRCA(), metadata={'model_hash': 'hash-1'})

    evidence = build_service()._build_evidence(rca)

    assert evidence.analysis_window is None
    assert 'analysis_window' in evidence.missing_evidence


@pytest.mark.parametrize(
    'window, expected_start, expected_end',
    [
        (
            {'start': '2026-08-10T00:00:00Z', 'end': '2026-08-10T01:00:00Z'},
            '2026-08-10T00:00:00Z',
            '2026-08-10T01:00:00Z',
        ),
        (
            {'start': '2026-08-09T21:00:00-03:00', 'end': '2026-08-09T22:00:00-03:00'},
            '2026-08-10T00:00:00Z',
            '2026-08-10T01:00:00Z',
        ),
    ],
)
def test_analysis_window_valid_windows_are_normalized(window, expected_start, expected_end):
    rca = replace(
        FakeRCA(),
        metadata={'model_hash': 'hash-1', 'analysis_window': window},
    )

    evidence = build_service()._build_evidence(rca)

    assert evidence.analysis_window['start'] == expected_start
    assert evidence.analysis_window['end'] == expected_end


@pytest.mark.parametrize(
    'window',
    [
        {'start': 'bad', 'end': 'bad'},
        {'start': '2026-08-10T00:00:00', 'end': '2026-08-10T01:00:00'},
        {'start': '2026-08-10T02:00:00Z', 'end': '2026-08-10T01:00:00Z'},
        {'from': '2026-08-10T00:00:00Z', 'to': '2026-08-10T01:00:00Z'},
        {'start': '2026-08-10T00:00:00Z', 'end': '2026-08-10T01:00:00Z', 'extra': 'x'},
    ],
)
def test_analysis_window_invalid_windows_fail(window):
    rca = replace(
        FakeRCA(),
        metadata={'model_hash': 'hash-1', 'analysis_window': window},
    )

    with pytest.raises((TypeError, ValueError)):
        build_service()._build_evidence(rca)


def test_analysis_window_deep_freeze_and_source_isolated():
    window = {'start': '2026-08-09T21:00:00-03:00', 'end': '2026-08-09T22:00:00-03:00'}
    rca = replace(
        FakeRCA(),
        metadata={'model_hash': 'hash-1', 'analysis_window': window},
    )

    evidence = build_service()._build_evidence(rca)

    window['start'] = 'tampered'
    assert evidence.analysis_window['start'] == '2026-08-10T00:00:00Z'

    with pytest.raises(TypeError):
        evidence.analysis_window['start'] = 'tampered'  # type: ignore[index]


def test_real_rca_result_is_supported_with_offset_window():
    client = FakeClient('{"root_cause":"payment-service","summary":"ok","evidence_statements":[{"text":"root","evidence_ids":["real-temporal"]}],"propagation_path":["p"],"recommendations":["r"],"cited_evidence_ids":["real-temporal"]}')
    result = build_service(client).explain(
        build_real_rca_result(),
    )

    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.confidence == 0.569
    assert result.explanation.reliability is True
    assert result.explanation.used_fallback is False
    assert result.evidence.model_probabilities['payment-service'] == 0.99
    assert result.evidence.final_ranking_scores['payment-service'] == 0.569
    assert result.evidence.confidence == 0.569
    assert result.evidence.root_cause_evidence_ids == ('real-temporal', 'real-shap')
    assert result.evidence.analysis_window['start'] == '2026-08-10T00:00:00Z'
    assert result.evidence.analysis_window['end'] == '2026-08-10T01:00:00Z'
    assert result.provenance.actual_model == 'actual-model'
    assert result.provenance.validation_status == 'passed'


def test_prompt_and_evidence_payloads_are_deep_frozen():
    nested_feature_value = {'nested': {'values': [1]}}
    nested_baseline = {'nested': {'values': [2]}}
    nested_window = {'start': '2026-08-09T21:00:00-03:00', 'end': '2026-08-09T22:00:00-03:00'}
    rca = replace(
        FakeRCA(
            evidence=(
                FakeEvidence(
                    'e-root',
                    'temporal',
                    description='payment-service first failure',
                    raw_value={'nested': {'values': [1]}},
                    source_trace_ids=('trace-1',),
                ),
                FakeEvidence(
                    'e-shap',
                    'shap',
                    raw_value=0.62,
                    normalized_value=0.62,
                    source_component='shap',
                    service_name='payment-service',
                    metric_name='latency_zscore',
                    source_trace_ids=('trace-1',),
                    attributes={'feature_value': nested_feature_value, 'baseline': nested_baseline},
                ),
                FakeEvidence(
                    'e-alt',
                    'graph',
                    service_name='order-service',
                    description='order->payment',
                    source_trace_ids=('trace-1',),
                ),
            ),
        ),
        metadata={'model_hash': 'hash-1', 'analysis_window': nested_window},
    )

    result = build_service().explain(rca)

    nested_feature_value['nested']['values'].append(2)
    nested_baseline['nested']['values'].append(3)
    nested_window['start'] = 'tampered'

    assert result.evidence.feature_values['latency_zscore']['nested']['values'] == (1,)
    assert result.evidence.feature_baselines['latency_zscore']['nested']['values'] == (2,)
    assert result.prompt.sanitized_context['evidence_catalog'][0]['description'] == 'payment-service first failure'
    assert result.prompt.sanitized_context['evidence_catalog'][0]['raw_value']['nested']['values'] == (1,)

    with pytest.raises(TypeError):
        result.prompt.sanitized_context['evidence_catalog'][0]['raw_value']['nested']['values'][0] = 3  # type: ignore[index]

    with pytest.raises(TypeError):
        result.evidence.feature_values['latency_zscore']['nested']['values'][0] = 3  # type: ignore[index]

    with pytest.raises(TypeError):
        result.evidence.feature_baselines['latency_zscore']['nested']['values'][0] = 3  # type: ignore[index]


@pytest.mark.parametrize(
    'mutation',
    [
        {'provenance_id': '0' * 64},
        {'rca_result_id': 'different'},
        {'trace_id': 'different'},
        {'root_cause_service': 'order-service'},
        {'evidence_hash': '0' * 64},
        {'response_hash': '0' * 64},
        {'prompt_builder_version': 'different'},
        {'prompt_schema_version': 'different'},
        {'prompt_hash': '0' * 64},
        {'mode': 'generic'},
        {'validation_status': 'evil'},
        {'uncertainty_policy_version': 'evil'},
        {'explanation_hash': '0' * 64},
        {'fallback_reason': 'timeout'},
    ],
)
def test_provenance_reconciliation_rejects_mutations(mutation):
    result = build_service(InvalidResponseClient()).explain(FakeRCA())
    original = result.provenance

    with pytest.raises(ValueError):
        replace(original, **mutation)


def test_low_confidence_uses_high_uncertainty():
    low_confidence_rca = replace(
        FakeRCA(
            confidence=0.2,
            ranking=(
                FakeRankedService('payment-service', FakeScore(0.50, 0.20), ('e-root',)),
                FakeRankedService('order-service', FakeScore(0.40, 0.10), ('e-alt',)),
            ),
            reliability=FakeReliability(False, 0.2, 0.1),
        ),
        metadata={'model_hash': 'hash-1', 'analysis_window': {'start': '2026-08-10T00:00:00Z', 'end': '2026-08-10T01:00:00Z'}},
    )

    result = build_service(FakeClient()).explain(low_confidence_rca)

    assert result.explanation.uncertainty == 'high'
    assert result.explanation.used_fallback is False


def test_timeout_falls_back_deterministically():
    result = build_service(TimeoutClient()).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'timeout'
    assert result.provenance.validation_status == 'passed'


def test_invalid_schema_falls_back_deterministically():
    result = build_service(InvalidSchemaClient()).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_schema'
    assert result.provenance.validation_status == 'passed'

def test_uncertainty_policy_version_is_fixed():
    result = build_service().explain(FakeRCA())

    assert result.explanation.uncertainty_policy_version == 'uncertainty-policy-v1'
    assert result.provenance.uncertainty_policy_version == 'uncertainty-policy-v1'
    assert (
        result.provenance.uncertainty_policy_version
        == result.explanation.uncertainty_policy_version
    )

    with pytest.raises(ValueError, match="uncertainty_policy_version"):
        replace(
            result.explanation,
            uncertainty_policy_version="evil",
        )

    with pytest.raises(ValueError, match="uncertainty_policy_version"):
        replace(
            result.provenance,
            uncertainty_policy_version="evil",
        )


def test_uncertainty_policy_coordinated_tampering_is_impossible():
    result = build_service().explain(FakeRCA())

    with pytest.raises(ValueError, match='uncertainty_policy_version'):
        replace(result.explanation, uncertainty_policy_version='evil')

    with pytest.raises(ValueError, match='uncertainty_policy_version'):
        replace(
            result.provenance,
            explanation=None,
            uncertainty_policy_version='evil',
        )


def test_provider_exception_uses_exact_fallback_reason():
    result = build_service(ProviderErrorClient()).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'provider_error'


@pytest.mark.parametrize('response_text', ['', '   '])
def test_empty_provider_response_uses_exact_fallback_reason(response_text):
    result = build_service(InvalidResponseClient(response_text)).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'empty_response'


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'summary': 'ok'},
        [],
        valid_llm_payload(unknown=True),
    ],
    ids=['empty-object', 'summary-only', 'top-level-list', 'unknown-field'],
)
def test_closed_llm_schema_rejects_invalid_payloads(payload):
    result = build_service(client_for_payload(payload)).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_schema'


@pytest.mark.parametrize(
    'missing_field',
    [
        'root_cause',
        'summary',
        'evidence_statements',
        'propagation_path',
        'recommendations',
        'cited_evidence_ids',
    ],
)
def test_closed_llm_schema_rejects_each_missing_required_field(missing_field):
    payload = valid_llm_payload()
    del payload[missing_field]

    result = build_service(client_for_payload(payload)).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_schema'


@pytest.mark.parametrize(
    'returned_root, expected_fallback_reason',
    [
        ('payment-service', None),
        ('order-service', 'root_cause_mismatch'),
        ('unknown-service', 'root_cause_mismatch'),
    ],
)
def test_llm_cannot_change_trusted_root(returned_root, expected_fallback_reason):
    rca = FakeRCA()
    result = build_service(
        client_for_payload(valid_llm_payload(root_cause=returned_root))
    ).explain(rca)

    assert result.explanation.root_cause == rca.root_cause_service
    assert result.explanation.used_fallback is (expected_fallback_reason is not None)
    assert result.provenance.fallback_reason == expected_fallback_reason


def test_statement_as_string_is_invalid_schema():
    payload = valid_llm_payload(evidence_statements=['foo'])

    result = build_service(client_for_payload(payload)).explain(FakeRCA())

    assert result.provenance.fallback_reason == 'invalid_schema'


def test_statement_without_evidence_ids_is_rejected_by_rigid_contract():
    payload = valid_llm_payload(
        evidence_statements=[{'text': 'root', 'evidence_ids': []}],
        cited_evidence_ids=[],
    )

    result = build_service(client_for_payload(payload)).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_schema'


@pytest.mark.parametrize('invalid_id', ['made-up-evidence', 'trace-1'])
def test_only_canonical_evidence_ids_are_citable(invalid_id):
    payload = valid_llm_payload(
        evidence_statements=[{'text': 'root', 'evidence_ids': [invalid_id]}],
        cited_evidence_ids=[invalid_id],
    )

    result = build_service(client_for_payload(payload)).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_evidence_reference'


@pytest.mark.parametrize(
    'trusted_field, provider_value',
    [
        ('uncertainty', 'low'),
        ('missing_evidence', []),
        ('limitations', []),
        ('confidence', 1.0),
        ('reliability', False),
    ],
)
def test_llm_cannot_supply_trusted_fields(trusted_field, provider_value):
    result = build_service(
        client_for_payload(valid_llm_payload(**{trusted_field: provider_value}))
    ).explain(FakeRCA())

    assert result.explanation.used_fallback is True
    assert result.provenance.fallback_reason == 'invalid_schema'


def test_temporal_only_fallback_uses_canonical_evidence():
    rca = FakeRCA(
        ranking=(
            FakeRankedService('payment-service', FakeScore(0.99, 0.569), ('e-temporal',)),
            FakeRankedService('order-service', FakeScore(0.40, 0.20)),
        ),
        evidence=(FakeEvidence('e-temporal', 'temporal'),),
    )

    result = build_service().explain(rca)

    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.used_fallback is True
    assert any('temporal' in item.text for item in result.explanation.evidence_statements)
    assert result.explanation.cited_evidence_ids == ('e-temporal',)


def test_graph_only_fallback_uses_canonical_evidence():
    rca = FakeRCA(
        ranking=(
            FakeRankedService('payment-service', FakeScore(0.99, 0.569), ('e-graph',)),
            FakeRankedService('order-service', FakeScore(0.40, 0.20)),
        ),
        evidence=(FakeEvidence('e-graph', 'graph'),),
    )

    result = build_service().explain(rca)

    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.used_fallback is True
    assert any('estrutural' in item.text for item in result.explanation.evidence_statements)
    assert result.explanation.cited_evidence_ids == ('e-graph',)


def test_no_shap_fallback_remains_valid():
    rca = replace(
        FakeRCA(),
        evidence=(
            FakeEvidence('e-root', 'temporal'),
            FakeEvidence('e-alt', 'graph', service_name='order-service'),
        ),
    )

    result = build_service().explain(rca)

    assert result.explanation.root_cause == 'payment-service'
    assert result.explanation.used_fallback is True
    assert result.evidence.top_shap_features == ()


def test_root_without_evidence_ids_does_not_invent_citations():
    rca = FakeRCA(
        ranking=(
            FakeRankedService('payment-service', FakeScore(0.99, 0.569)),
            FakeRankedService('order-service', FakeScore(0.40, 0.20)),
        ),
        evidence=(),
    )

    result = build_service().explain(rca)

    assert result.explanation.root_cause == 'payment-service'
    assert 'root_cause_without_citable_evidence' in result.evidence.limitations
    assert result.explanation.evidence_statements == ()
    assert result.explanation.cited_evidence_ids == ()


@pytest.mark.parametrize('client', [None, FakeClient()], ids=['fallback', 'valid-llm'])
def test_trusted_rca_values_are_preserved_in_all_paths(client):
    rca = FakeRCA()

    result = build_service(client).explain(rca)

    assert result.explanation.root_cause == rca.root_cause_service
    assert result.explanation.confidence == 0.569
    assert result.explanation.reliability is True


@pytest.mark.parametrize('invalid_score', [float('nan'), float('inf'), -0.1, 1.1, True])
@pytest.mark.parametrize('field_name', ['model_probabilities', 'final_ranking_scores', 'confidence'])
def test_evidence_object_rejects_invalid_scores(field_name, invalid_score):
    evidence = build_service()._build_evidence(FakeRCA())
    mutation = {field_name: invalid_score}
    if field_name != 'confidence':
        mutation[field_name] = {'payment-service': invalid_score}

    with pytest.raises((TypeError, ValueError)):
        replace(evidence, **mutation)


def test_shap_semantics_are_preserved():
    rca = FakeRCA(
        ranking=(
            FakeRankedService('payment-service', FakeScore(0.99, 0.569), ('shap-low',)),
            FakeRankedService('order-service', FakeScore(0.40, 0.20)),
        ),
        evidence=(
            FakeEvidence('shap-low', 'shap', source_component='shap', metric_name='f-low', raw_value=0.2, attributes={'feature_value': 1.2, 'baseline': 0.1}),
            FakeEvidence('shap-high', 'shap', source_component='shap', metric_name='f-high', raw_value=-0.8, attributes={'feature_value': 2.3}),
            FakeEvidence('shap-mid', 'shap', source_component='shap', metric_name='latency_zscore', raw_value=0.5, attributes={'feature_value': 4.8}),
            FakeEvidence('shap-other', 'shap', source_component='shap', service_name='order-service', metric_name='other', raw_value=-99.0, attributes={'feature_value': 99.0}),
        ),
    )

    evidence = build_service()._build_evidence(rca)

    assert [item.raw_value for item in evidence.top_shap_features] == [-0.8, 0.5, 0.2]
    assert all(item.service_name == 'payment-service' for item in evidence.top_shap_features)
    assert evidence.feature_values['latency_zscore'] == 4.8
    assert 'f-high' not in evidence.feature_baselines
    assert 'latency_zscore' not in evidence.feature_baselines
    assert 'feature_baseline' in evidence.missing_evidence


@pytest.mark.parametrize(
    'text, evidence_ids',
    [
        ('', ('e1',)),
        ('   ', ('e1',)),
        ('texto', ()),
        ('texto', ('bad id',)),
        ('texto', 'e1'),
    ],
)
def test_evidence_statement_rejects_invalid_values(text, evidence_ids):
    with pytest.raises((TypeError, ValueError)):
        EvidenceStatementV1(text, evidence_ids)


@pytest.mark.parametrize(
    'mutation',
    [
        {'tokens_prompt': True},
        {'tokens_prompt': 1.5},
        {'tokens_completion': -1},
        {'latency_ms': True},
        {'latency_ms': float('nan')},
        {'cost_usd': float('nan')},
        {'cost_usd': -1},
        {'response_hash': 'bad'},
        {'response_hash': '0' * 64},
    ],
)
def test_llm_response_rejects_invalid_values(mutation):
    with pytest.raises((TypeError, ValueError)):
        LLMResponse(text='abc', model_name='model', **mutation)


def test_llm_response_computes_hash_from_text():
    response = LLMResponse(text='abc', model_name='model', response_hash=None)

    assert response.response_hash == hashlib.sha256(b'abc').hexdigest()


@pytest.mark.parametrize(
    'mutation',
    [
        {'schema_version': 'different'},
        {'uncertainty': 'unknown'},
        {'uncertainty_policy_version': 'evil'},
        {'mode': 'unknown'},
        {'confidence': 1.1},
        {'confidence': float('nan')},
        {'reliability': 1},
        {'tokens_prompt': -1},
        {'tokens_prompt': True},
        {'latency_ms': float('nan')},
        {'cost_usd': float('nan')},
        {'prompt_hash': 'bad'},
        {'used_fallback': True, 'fallback_reason': None},
        {'used_fallback': False, 'fallback_reason': 'timeout'},
    ],
)
def test_structured_explanation_rejects_invalid_values(mutation):
    explanation = build_service(FakeClient()).explain(FakeRCA()).explanation

    with pytest.raises((TypeError, ValueError)):
        replace(explanation, **mutation)


def test_evidence_reference_raw_value_is_deep_frozen_and_source_isolated():
    source = {'nested': {'values': [1]}}
    reference = EvidenceReferenceV1(
        evidence_id='e1',
        evidence_type='temporal',
        source_component='test',
        raw_value=source,
    )

    source['nested']['values'].append(2)
    assert reference.raw_value['nested']['values'] == (1,)

    with pytest.raises(TypeError):
        reference.raw_value['nested']['values'][0] = 3  # type: ignore[index]


def test_invalid_json_preserves_complete_provider_provenance():
    result = build_service(InvalidResponseClient()).explain(FakeRCA())

    assert result.provenance.response_hash == hashlib.sha256(b'not-json').hexdigest()
    assert result.provenance.actual_model == 'actual-model'
    assert result.provenance.tokens_prompt == 7
    assert result.provenance.tokens_completion == 1
    assert result.provenance.latency_ms == 5.0
    assert result.provenance.cost_usd == 0.01
