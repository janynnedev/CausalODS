from datetime import datetime, timezone

import pytest

from models.evidence import (
    EvidenceDirection,
    EvidenceItem,
    EvidenceType,
)
from models.rca_context import RCAContext
from models.rca_result import (
    RCAResult,
    RankedService,
    ReliabilityDecision,
    ScoreBreakdown,
)


def build_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id=(
            "temporal.payment-service.first-anomaly"
        ),
        evidence_type=EvidenceType.TEMPORAL,
        source_component="TemporalCorrelator",
        description=(
            "O payment-service foi o primeiro serviço "
            "a apresentar anomalia."
        ),
        service_name="payment-service",
        metric_name="first_anomaly",
        raw_value=True,
        normalized_value=1.0,
        direction=EvidenceDirection.SUPPORTING,
        observed_at=datetime.now(timezone.utc),
    )


def build_scores() -> ScoreBreakdown:
    return ScoreBreakdown(
        model_probability=0.90,
        shap_support_score=0.80,
        temporal_score=1.00,
        graph_score=0.70,
        anomaly_score=0.85,
        final_score=0.86,
        component_weights={
            "model": 0.40,
            "shap": 0.15,
            "temporal": 0.20,
            "graph": 0.10,
            "anomaly": 0.15,
        },
    )


def build_reliability() -> ReliabilityDecision:
    return ReliabilityDecision(
        is_reliable=True,
        confidence=0.86,
        confidence_method=(
            "normalized_weighted_ranking_v1"
        ),
        minimum_confidence=0.60,
        margin_to_second=0.25,
        minimum_margin=0.10,
        passed_rules=(
            "minimum_confidence",
            "minimum_margin",
            "minimum_evidence_types",
        ),
        failed_rules=(),
    )


def test_evidence_is_serializable():
    evidence = build_evidence()

    serialized = evidence.to_dict()

    assert serialized["normalized_value"] == 1.0
    assert serialized["direction"] == "supporting"
    assert serialized["observed_at"] is not None


def test_context_accepts_canonical_results():
    context = RCAContext(
        trace_id="trace-001",
        services=(
            "order-service",
            "payment-service",
        ),
        feature_vectors={
            "payment-service": {
                "latency_zscore": 4.2,
            }
        },
        predictions={
            "payment-service": {
                "probability": 0.90,
            }
        },
    )

    assert context.service_exists("payment-service")
    assert context.unknown_result_services() == {}


def test_rca_result_is_created():
    evidence = build_evidence()

    ranking = [
        RankedService(
            position=1,
            service_name="payment-service",
            scores=build_scores(),
            evidence_ids=(
                evidence.evidence_id,
            ),
        )
    ]

    result = RCAResult.create(
        trace_id="trace-001",
        ranking=ranking,
        evidence=[evidence],
        reliability=build_reliability(),
        model_version="xgboost-rca-v1",
        pipeline_version="causalods-rca-v1",
    )

    assert result.root_cause_service == (
        "payment-service"
    )
    assert result.confidence == 0.86
    assert result.to_dict()["schema_version"] == "1.0"


def test_rejects_invalid_score():
    with pytest.raises(ValueError):
        ScoreBreakdown(
            model_probability=1.50,
            shap_support_score=0.80,
            temporal_score=1.00,
            graph_score=0.70,
            anomaly_score=0.85,
            final_score=0.86,
        )


def test_rejects_unknown_evidence_reference():
    ranking = [
        RankedService(
            position=1,
            service_name="payment-service",
            scores=build_scores(),
            evidence_ids=(
                "unknown.evidence",
            ),
        )
    ]

    with pytest.raises(ValueError):
        RCAResult.create(
            trace_id="trace-001",
            ranking=ranking,
            evidence=[],
            reliability=build_reliability(),
            model_version="xgboost-rca-v1",
            pipeline_version="causalods-rca-v1",
        )