import pytest

from ranking.evidence import RCAEvidence
from ranking.ranking_service import (
    RCARankingService,
)


def build_ranking() -> list[RCAEvidence]:

    service = RCARankingService()

    predictions = {
        "order-service": 0.25,
        "payment-service": 0.90,
    }

    shap_scores = {
        "order-service": 0.15,
        "payment-service": 0.85,
    }

    temporal = {
        "order-service": False,
        "payment-service": True,
    }

    graph = {
        "order-service": 0.30,
        "payment-service": 0.70,
    }

    return service.rank(
        predictions,
        shap_scores,
        temporal,
        graph,
    )


def test_root_cause_ranking_returns_all_services():

    ranking = build_ranking()

    assert isinstance(
        ranking,
        list,
    )

    assert len(ranking) == 2

    assert all(
        isinstance(
            item,
            RCAEvidence,
        )
        for item in ranking
    )


def test_root_cause_ranking_places_strongest_candidate_first():

    ranking = build_ranking()

    first = ranking[0]
    second = ranking[1]

    assert (
        first.service_name
        == "payment-service"
    )

    assert (
        second.service_name
        == "order-service"
    )


def test_root_cause_ranking_is_sorted_by_final_score():

    ranking = build_ranking()

    final_scores = [
        item.final_score
        for item in ranking
    ]

    assert final_scores == sorted(
        final_scores,
        reverse=True,
    )


def test_root_cause_ranking_preserves_component_scores():

    ranking = build_ranking()

    payment = ranking[0]

    assert payment.xgboost_score == 0.90
    assert payment.shap_score == 0.85
    assert payment.temporal_score == 1
    assert payment.graph_score == 0.70


def test_root_cause_ranking_calculates_expected_scores():

    ranking = build_ranking()

    payment = ranking[0]
    order = ranking[1]

    assert payment.final_score == pytest.approx(
        0.8775
    )

    assert order.final_score == pytest.approx(
        0.1825
    )