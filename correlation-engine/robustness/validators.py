from __future__ import annotations

from typing import Any

from robustness.catalog import scenario_catalog
from robustness.contracts import (
    LifecycleObservation,
    LiveObservation,
    RobustnessScenario,
    ValidationIssue,
    ValidationReport,
)


def _issue(
    issues: list[ValidationIssue],
    *,
    code: str,
    message: str,
    expected: Any,
    actual: Any,
) -> None:
    issues.append(
        ValidationIssue(
            code=code,
            message=message,
            expected=expected,
            actual=actual,
        )
    )


def validate_offline_observation(
    observation: LifecycleObservation,
    *,
    control_fingerprint: str,
    expected_root_cause: str = "payment-service",
) -> ValidationReport:
    spec = scenario_catalog(expected_root_cause)[observation.scenario]
    issues: list[ValidationIssue] = []

    if (
        spec.expected_trace_completed is not None
        and observation.trace_completed
        != spec.expected_trace_completed
    ):
        _issue(
            issues,
            code="trace_completion_mismatch",
            message="Estado de conclusão do trace divergente.",
            expected=spec.expected_trace_completed,
            actual=observation.trace_completed,
        )

    if (
        spec.expected_trace_timed_out is not None
        and observation.trace_timed_out
        != spec.expected_trace_timed_out
    ):
        _issue(
            issues,
            code="trace_timeout_mismatch",
            message="Estado de timeout divergente.",
            expected=spec.expected_trace_timed_out,
            actual=observation.trace_timed_out,
        )

    if observation.trace_finalization_count != 1:
        _issue(
            issues,
            code="multiple_trace_finalizations",
            message="Cada trace deve ser finalizado exatamente uma vez.",
            expected=1,
            actual=observation.trace_finalization_count,
        )

    if (
        not spec.reliable_decision_allowed
        and observation.decision_allowed
    ):
        _issue(
            issues,
            code="unsafe_decision_allowed",
            message=(
                "Uma decisão confiável não pode ser permitida "
                "para trace incompleto ou finalizado por timeout."
            ),
            expected=False,
            actual=True,
        )

    scenarios_matching_control = {
        RobustnessScenario.CONTROL,
        RobustnessScenario.DUPLICATE_SPAN,
        RobustnessScenario.OUT_OF_ORDER_SPANS,
        RobustnessScenario.DELAYED_SPAN_BEFORE_TIMEOUT,
    }
    if (
        observation.scenario in scenarios_matching_control
        and observation.semantic_fingerprint != control_fingerprint
    ):
        _issue(
            issues,
            code="semantic_fingerprint_mismatch",
            message=(
                "A mutação não deveria alterar o conjunto semântico "
                "de spans aceitos."
            ),
            expected=control_fingerprint,
            actual=observation.semantic_fingerprint,
        )

    if (
        observation.scenario
        == RobustnessScenario.DUPLICATE_SPAN
        and observation.duplicate_spans_detected < 1
    ):
        _issue(
            issues,
            code="duplicate_not_detected",
            message="O span duplicado não foi detectado.",
            expected=">=1",
            actual=observation.duplicate_spans_detected,
        )

    if (
        observation.scenario
        == RobustnessScenario.OUT_OF_ORDER_SPANS
        and observation.accepted_unique_spans
        != observation.expected_span_count
    ):
        _issue(
            issues,
            code="out_of_order_data_loss",
            message="Spans fora de ordem causaram perda semântica.",
            expected=observation.expected_span_count,
            actual=observation.accepted_unique_spans,
        )

    if (
        observation.scenario
        == RobustnessScenario.INCOMPLETE_TRACE
        and not observation.missing_span_ids
    ):
        _issue(
            issues,
            code="missing_span_not_reported",
            message="O trace incompleto deveria registrar spans ausentes.",
            expected="non-empty",
            actual=observation.missing_span_ids,
        )

    if (
        observation.scenario
        == RobustnessScenario.DELAYED_SPAN_AFTER_TIMEOUT
        and observation.late_spans_rejected < 1
    ):
        _issue(
            issues,
            code="late_span_not_rejected",
            message="O span após timeout deveria ser rejeitado ou isolado.",
            expected=">=1",
            actual=observation.late_spans_rejected,
        )

    metrics = {
        "received_envelopes": observation.received_envelopes,
        "accepted_unique_spans": observation.accepted_unique_spans,
        "duplicate_spans_detected": observation.duplicate_spans_detected,
        "late_spans_rejected": observation.late_spans_rejected,
        "trace_finalization_count": observation.trace_finalization_count,
        "trace_completed": observation.trace_completed,
        "trace_timed_out": observation.trace_timed_out,
        "decision_allowed": observation.decision_allowed,
        "missing_span_count": len(observation.missing_span_ids),
        "processing_latency_ms": observation.processing_latency_ms,
        "semantic_fingerprint": observation.semantic_fingerprint,
    }

    return ValidationReport(
        scenario=observation.scenario,
        valid=not issues,
        issues=tuple(issues),
        metrics=metrics,
    )


def validate_live_observation(
    observation: LiveObservation,
    *,
    expected_root_cause: str = "payment-service",
) -> ValidationReport:
    spec = scenario_catalog(expected_root_cause)[observation.scenario]
    issues: list[ValidationIssue] = []

    if not (
        spec.expected_rca_result_min
        <= observation.rca_result_count
        <= spec.expected_rca_result_max
    ):
        _issue(
            issues,
            code="rca_result_count_out_of_range",
            message="Quantidade de RCAResults fora do intervalo esperado.",
            expected=(
                spec.expected_rca_result_min,
                spec.expected_rca_result_max,
            ),
            actual=observation.rca_result_count,
        )

    if (
        spec.expected_incident_max is not None
        and observation.incident_count > spec.expected_incident_max
    ):
        _issue(
            issues,
            code="incident_count_exceeded",
            message="Quantidade de incidentes excedeu o máximo esperado.",
            expected=f"<= {spec.expected_incident_max}",
            actual=observation.incident_count,
        )

    if observation.rca_result_count > 1:
        _issue(
            issues,
            code="duplicate_rca_result",
            message="A mesma condição produziu RCAResult duplicado.",
            expected="<=1",
            actual=observation.rca_result_count,
        )

    if observation.incident_count > 1:
        _issue(
            issues,
            code="duplicate_incident",
            message="A mesma condição produziu incidente duplicado.",
            expected="<=1",
            actual=observation.incident_count,
        )

    if observation.data_loss_count > 0:
        _issue(
            issues,
            code="data_loss",
            message="Foi observada perda de dados.",
            expected=0,
            actual=observation.data_loss_count,
        )

    if (
        observation.is_reliable is True
        and not spec.reliable_decision_allowed
    ):
        _issue(
            issues,
            code="unsafe_reliable_decision",
            message="A decisão confiável não é permitida neste cenário.",
            expected=False,
            actual=True,
        )

    if (
        observation.is_reliable is True
        and spec.expected_top_service is not None
        and observation.top_service != spec.expected_top_service
    ):
        _issue(
            issues,
            code="wrong_root_cause",
            message="Causa Top-1 divergente.",
            expected=spec.expected_top_service,
            actual=observation.top_service,
        )

    if observation.provenance_valid is False:
        _issue(
            issues,
            code="invalid_provenance",
            message="A proveniência do resultado é inválida.",
            expected=True,
            actual=False,
        )

    metrics = {
        "rca_result_count": observation.rca_result_count,
        "incident_count": observation.incident_count,
        "top_service": observation.top_service,
        "is_reliable": observation.is_reliable,
        "data_loss_count": observation.data_loss_count,
        "recovery_time_ms": observation.recovery_time_ms,
        "processing_latency_ms": observation.processing_latency_ms,
        "provenance_valid": observation.provenance_valid,
    }

    return ValidationReport(
        scenario=observation.scenario,
        valid=not issues,
        issues=tuple(issues),
        metrics=metrics,
    )
