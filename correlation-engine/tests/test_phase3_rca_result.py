from models.rca_context import (
    RCAContext,
)

from pipeline.rca_result_factory import (
    RCAResultFactory,
)


def build_context(
    *,
    low_confidence: bool = False,
) -> RCAContext:

    if low_confidence:

        predictions = {
            "order-service": {
                "probability": 0.20,
            },
            "payment-service": {
                "probability": 0.15,
            },
        }

        shap_results = {
            "order-service": {
                "support_score": 0.10,
                "contributions": [],
            },
            "payment-service": {
                "support_score": 0.05,
                "contributions": [],
            },
        }

        temporal_results = {
            "order-service": {
                "score": 0.0,
                "is_first_anomaly": False,
            },
            "payment-service": {
                "score": 0.0,
                "is_first_anomaly": False,
            },
        }

    else:

        predictions = {
            "order-service": {
                "probability": 0.18,
            },
            "payment-service": {
                "probability": 0.92,
            },
        }

        shap_results = {
            "order-service": {
                "support_score": 0.10,
                "contributions": [
                    {
                        "feature_name": (
                            "error_rate"
                        ),
                        "feature_value": 0.0,
                        "shap_value": 0.02,
                        "direction": (
                            "supporting"
                        ),
                    }
                ],
            },
            "payment-service": {
                "support_score": 0.85,
                "contributions": [
                    {
                        "feature_name": (
                            "latency_zscore"
                        ),
                        "feature_value": 4.8,
                        "shap_value": 0.62,
                        "direction": (
                            "supporting"
                        ),
                    },
                    {
                        "feature_name": (
                            "error_rate"
                        ),
                        "feature_value": 0.7,
                        "shap_value": 0.31,
                        "direction": (
                            "supporting"
                        ),
                    },
                ],
            },
        }

        temporal_results = {
            "order-service": {
                "score": 0.20,
                "is_first_anomaly": False,
            },
            "payment-service": {
                "score": 0.90,
                "is_first_anomaly": True,
            },
        }

    return RCAContext(
        trace_id="trace-phase3-001",
        services=(
            "order-service",
            "payment-service",
        ),
        feature_vectors={
            "order-service": {
                "latency_zscore": 0.4,
                "error_rate": 0.0,
            },
            "payment-service": {
                "latency_zscore": 4.8,
                "error_rate": 0.7,
            },
        },
        temporal_results=(
            temporal_results
        ),
        graph_results={
            "order-service": {
                "score": 0.30,
            },
            "payment-service": {
                "score": 0.50,
            },
        },
        anomaly_results={
            "order-service": {
                "score": 0.10,
                "zscore": 0.4,
            },
            "payment-service": {
                "score": 1.0,
                "zscore": 4.8,
            },
        },
        predictions=predictions,
        shap_results=shap_results,
        model_version="xgboost-rca-v1",
        pipeline_version=(
            "causalods-rca-v1"
        ),
    )


def test_builds_typed_rca_result():

    factory = RCAResultFactory()

    result = factory.build(
        build_context()
    )

    assert (
        result.root_cause_service
        == "payment-service"
    )

    assert result.ranking[0].position == 1

    assert (
        result.confidence
        == result.ranking[
            0
        ].scores.final_score
    )

    assert (
        result.reliability.confidence
        == result.confidence
    )

    assert (
        result.reliability.is_reliable
        is True
    )

    assert len(result.evidence) > 0

    known_evidence_ids = {
        item.evidence_id
        for item in result.evidence
    }

    for ranked_service in result.ranking:

        assert set(
            ranked_service.evidence_ids
        ).issubset(
            known_evidence_ids
        )


def test_low_confidence_result_is_preserved():

    factory = RCAResultFactory()

    result = factory.build(
        build_context(
            low_confidence=True
        )
    )

    assert result.ranking

    assert (
        result.reliability.is_reliable
        is False
    )

    assert (
        "minimum_confidence"
        in result.reliability.failed_rules
    )


def test_result_is_json_safe():

    factory = RCAResultFactory()

    result = factory.build(
        build_context()
    )

    allowed_evidence_types = {
        "trace",
        "temporal",
        "graph",
        "anomaly",
        "model",
        "shap",
        "ranking",
        "metric",
        "log",
        "business_event",
    }

    for evidence_item in result.evidence:

        assert (
            evidence_item.evidence_type.value
            in allowed_evidence_types
        )

    payload = result.to_dict()

    assert (
        payload[
            "root_cause_service"
        ]
        == "payment-service"
    )

    assert isinstance(
        payload["ranking"],
        list,
    )

    assert isinstance(
        payload["evidence"],
        list,
    )

    assert isinstance(
        payload["reliability"],
        dict,
    )