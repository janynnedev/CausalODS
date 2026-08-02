from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from explanations import (
    GENERATOR_VERSION,
    generate_explanation,
)


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
                            "shap_support_score": 0.60,
                            "graph_score": 0.20,
                            "anomaly_score": 0.50,
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
                        "evidence_type": "temporal",
                        "description": (
                            "Foi a primeira anomalia observada."
                        ),
                        "normalized_value": 0.90,
                    },
                    {
                        "service_name": "payment-service",
                        "evidence_type": "shap",
                        "description": (
                            "SHAP sustentou a classificação."
                        ),
                        "normalized_value": 0.60,
                    },
                    {
                        "service_name": "payment-service",
                        "evidence_type": "model",
                        "description": "O modelo sustentou a hipótese.",
                        "normalized_value": 0.70,
                    },
                    {
                        "service_name": "payment-service",
                        "evidence_type": "anomaly",
                        "description": "Anomalia observada.",
                        "normalized_value": 0.50,
                    },
                    {
                        "service_name": "payment-service",
                        "evidence_type": "graph",
                        "description": "Relação estrutural observada.",
                        "normalized_value": 0.20,
                    },
                    {
                        "service_name": "order-service",
                        "evidence_type": "graph",
                        "description": "Serviço upstream.",
                        "normalized_value": 0.10,
                    },
                ],
            },
        }
    }


def test_generates_reliable_explanation():
    explanation = generate_explanation(reliable_document())

    assert explanation.root_cause_service == "payment-service"
    assert explanation.is_reliable is True
    assert explanation.confidence == 0.82
    assert explanation.model_version == "xgboost-rca-v2"
    assert explanation.pipeline_version == "causalods-rca-v2"
    assert explanation.generator_version == GENERATOR_VERSION
    assert explanation.evidence
    assert "payment-service" in explanation.summary
    assert explanation.alternatives == (
        "order-service (score=0.1200)",
    )


def test_preserves_temporal_and_shap_sources():
    explanation = generate_explanation(reliable_document())
    sources = {
        item.source
        for item in explanation.evidence
    }

    assert "temporal" in sources
    assert "shap" in sources
    assert "model" in sources
    assert "anomaly" in sources
    assert "graph" in sources
    assert "score_breakdown" in sources


def test_explanation_id_is_deterministic():
    first = generate_explanation(reliable_document())
    second = generate_explanation(reliable_document())

    assert first.explanation_id == second.explanation_id
    assert first.to_dict() == second.to_dict()


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
