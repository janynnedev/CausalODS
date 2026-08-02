from __future__ import annotations

import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from robustness import (
    LiveObservation,
    RobustnessScenario,
    validate_live_observation,
)


def test_live_validator_accepts_single_recovered_result():
    observation = LiveObservation(
        scenario=RobustnessScenario.KAFKA_BACKLOG,
        trace_id="trace-1",
        rca_result_count=1,
        incident_count=1,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=0,
        recovery_time_ms=1_500,
        processing_latency_ms=2_000,
        provenance_valid=True,
    )

    report = validate_live_observation(observation)

    assert report.valid is True
    assert report.issues == ()


def test_live_validator_rejects_duplicates_and_data_loss():
    observation = LiveObservation(
        scenario=RobustnessScenario.CORRELATION_ENGINE_RESTART,
        trace_id="trace-2",
        rca_result_count=2,
        incident_count=2,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=1,
        restart_count_before=0,
        restart_count_after=1,
        provenance_valid=True,
    )

    report = validate_live_observation(observation)
    codes = {
        issue.code
        for issue in report.issues
    }

    assert report.valid is False
    assert "duplicate_rca_result" in codes
    assert "duplicate_incident" in codes
    assert "data_loss" in codes


def test_incomplete_trace_cannot_be_reliable():
    observation = LiveObservation(
        scenario=RobustnessScenario.INCOMPLETE_TRACE,
        trace_id="trace-3",
        rca_result_count=1,
        incident_count=1,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=0,
        provenance_valid=True,
    )

    report = validate_live_observation(observation)
    codes = {
        issue.code
        for issue in report.issues
    }

    assert report.valid is False
    assert "unsafe_reliable_decision" in codes
    assert "incident_count_exceeded" in codes


def test_restart_requires_started_at_change():
    observation = LiveObservation(
        scenario=RobustnessScenario.CORRELATION_ENGINE_RESTART,
        trace_id="trace-restart-no-change",
        rca_result_count=1,
        incident_count=0,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=0,
        restart_count_before=0,
        restart_count_after=0,
        provenance_valid=True,
        metadata={
            "restart_observed": False,
            "started_at_before": "2026-08-01T10:00:00Z",
            "started_at_after": "2026-08-01T10:00:00Z",
            "lag_after": 0,
        },
    )

    report = validate_live_observation(observation)
    codes = {issue.code for issue in report.issues}

    assert report.valid is False
    assert "restart_not_detected" in codes


def test_restart_rejects_backlog_not_drained():
    observation = LiveObservation(
        scenario=RobustnessScenario.CORRELATION_ENGINE_RESTART,
        trace_id="trace-restart-lag",
        rca_result_count=1,
        incident_count=0,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=0,
        restart_count_before=0,
        restart_count_after=0,
        provenance_valid=True,
        metadata={
            "restart_observed": True,
            "started_at_before": "2026-08-01T10:00:00Z",
            "started_at_after": "2026-08-01T10:00:10Z",
            "lag_after": 1,
        },
    )

    report = validate_live_observation(observation)
    codes = {issue.code for issue in report.issues}

    assert report.valid is False
    assert "consumer_not_recovered" in codes


def test_restart_accepts_observed_recovery():
    observation = LiveObservation(
        scenario=RobustnessScenario.CORRELATION_ENGINE_RESTART,
        trace_id="trace-restart-ok",
        rca_result_count=1,
        incident_count=0,
        top_service="payment-service",
        is_reliable=True,
        data_loss_count=0,
        restart_count_before=0,
        restart_count_after=0,
        provenance_valid=True,
        metadata={
            "restart_observed": True,
            "started_at_before": "2026-08-01T10:00:00Z",
            "started_at_after": "2026-08-01T10:00:10Z",
            "lag_after": 0,
        },
    )

    report = validate_live_observation(observation)

    assert report.valid is True
    assert report.issues == ()
