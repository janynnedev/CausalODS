from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from explanations import generate_explanation


def reliable_document():
    return {
        "rca_document": {
            "rca_result_id": "rca-1",
            "trace_id": "trace-1",
            "model_version": "xgboost-rca-v2",
            "pipeline_version": "causalods-rca-v2",
            "payload": {
                "ranking": [
                    {
                        "service_name": "payment-service",
                        "scores": {
                            "final_score": 0.82,
                            "model_score": 0.70,
                            "temporal_score": 0.90,
                        },
                    },
                    {
                        "service_name": "order-service",
                        "scores": {
                            "final_score": 0.12,
                        },
                    },
                ],
                "reliability": {
                    "is_reliable": True,
                    "failed_rules": [],
                },
                "evidence": [
                    {
                        "service_name": "payment-service",
                        "source": "temporal",
                        "description": (
                            "Foi a primeira anomalia observada."
                        ),
                        "score": 0.9,
                    },
                    {
                        "service_name": "order-service",
                        "source": "graph",
                        "description": "Serviço upstream.",
                        "score": 0.2,
                    },
                ],
            },
        }
    }


def test_generates_reliable_explanation():
    explanation = generate_explanation(
        reliable_document()
    )

    assert explanation.root_cause_service == "payment-service"
    assert explanation.is_reliable is True
    assert explanation.confidence == 0.82
    assert explanation.model_version == "xgboost-rca-v2"
    assert explanation.pipeline_version == "causalods-rca-v2"
    assert explanation.evidence
    assert "payment-service" in explanation.summary
    assert explanation.alternatives == (
        "order-service (score=0.1200)",
    )


def test_explanation_id_is_deterministic():
    first = generate_explanation(
        reliable_document()
    )
    second = generate_explanation(
        reliable_document()
    )

    assert first.explanation_id == second.explanation_id


def test_non_reliable_result_reports_gate():
    document = reliable_document()
    payload = document["rca_document"]["payload"]
    payload["reliability"] = {
        "is_reliable": False,
        "failed_rules": [
            "minimum_confidence",
        ],
    }
    payload["ranking"][0]["scores"]["final_score"] = 0.19

    explanation = generate_explanation(document)

    assert explanation.is_reliable is False
    assert "não ultrapassou" in explanation.summary
    assert "minimum_confidence" in explanation.decision
    assert explanation.limitations


def test_fallback_to_top_level_fields():
    document = {
        "rca_result_id": "rca-2",
        "trace_id": "trace-2",
        "top_service": "order-service",
        "confidence": 0.03,
        "is_reliable": False,
        "model_version": "xgboost-rca-v2",
        "pipeline_version": "causalods-rca-v2",
    }

    explanation = generate_explanation(document)

    assert explanation.root_cause_service == "order-service"
    assert explanation.confidence == 0.03
    assert explanation.is_reliable is False
