from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from explanations_v2.contracts import EvidenceStatementV1, StructuredExplanation
from models.evidence import EvidenceDirection, EvidenceItem, EvidenceType
from models.rca_result import RCAResult, RankedService, ReliabilityDecision, ScoreBreakdown
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.event_graph import EventGraphBuilder
from multimodal_v1.features_v3 import FeatureRegistryV3, RCAFeatureExtractorV3
from platform_v3.serialization import sha256_value


BASE = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
TRACE_ID = "a" * 32
CORRELATION_ID = "order-platform-v3-demo"


def _event(event_id: str, signal: str, seconds: int, service: str, **values: Any) -> UnifiedEvent:
    defaults: dict[str, Any] = {
        "event_id": event_id,
        "event_name": signal,
        "event_type": signal,
        "signal_type": signal,
        "event_timestamp": BASE + timedelta(seconds=seconds),
        "correlation_id": CORRELATION_ID,
        "business_entity_type": "order",
        "business_entity_id": "order-demo-1",
        "service_name": service,
        "source_reference": f"fixture:{event_id}",
        "source_dataset": "platform-v3-validation-fixture",
    }
    if signal == "trace":
        defaults.update(trace_id=TRACE_ID, span_id=(event_id[-1] * 16))
    elif signal == "metric":
        defaults.update(metric_name="http.server.duration", metric_unit="ms", metric_value=20.0)
    elif signal == "business_event":
        defaults.update(business_operation="payment", business_outcome="approved")
    defaults.update(values)
    return UnifiedEvent(**defaults)


def scenario_events(name: str) -> tuple[UnifiedEvent, ...]:
    if name not in {"healthy", "payment_failure", "payment_latency", "missing_signal"}:
        raise KeyError(name)
    payment_error = name == "payment_failure"
    payment_latency = name == "payment_latency"
    suffix = {
        "healthy": "healthy",
        "payment_failure": "failure",
        "payment_latency": "latency",
        "missing_signal": "missing",
    }[name]
    events = (
        _event(f"span-root-{suffix}-1", "trace", 0, "order-service", event_name="POST /orders"),
        _event(
            f"span-pay-{suffix}-2", "trace", 1, "payment-service", event_name="POST /payments",
            parent_span_id="1" * 16,
            severity="ERROR" if payment_error else None,
            error_type="PaymentDeclined" if payment_error else None,
            status_code="ERROR" if payment_error else "OK",
            attributes={"duration_ms": 1500.0 if payment_latency else 25.0},
        ),
        _event(
            f"log-pay-{suffix}-3", "log", 2, "payment-service", event_name="payment log",
            severity="ERROR" if payment_error else "INFO",
            error_type="PaymentDeclined" if payment_error else None,
            attributes={"body": "payment declined" if payment_error else "payment approved"},
        ),
        _event(
            f"metric-pay-{suffix}-4", "metric", 3, "payment-service", event_name="payment latency",
            metric_value=1500.0 if payment_latency else (900.0 if payment_error else 20.0),
            attributes={"zscore": 5.0 if payment_latency else (3.5 if payment_error else 0.1)},
        ),
        _event(
            f"business-pay-{suffix}-5", "business_event", 4, "payment-service",
            event_name="payment.failed" if payment_error else "payment.approved",
            business_outcome="failed" if payment_error else "approved",
        ),
    )
    if name == "missing_signal":
        # Fixture controlada no limite de observação: remove log e métrica, sem alterar eventos.
        return tuple(item for item in events if item.signal_type.value in {"trace", "business_event"})
    return events


@dataclass(frozen=True)
class DemoScenario:
    name: str
    events: tuple[UnifiedEvent, ...]
    bundle: Any
    graph: Any
    feature_vectors: tuple[Any, ...]
    rca_result: RCAResult | None
    explanation: StructuredExplanation | None


def _rca_result(bundle: Any, *, reliable: bool) -> RCAResult:
    payment_trace = next(
        item for item in bundle.events
        if item.service_name == "payment-service" and item.signal_type.value == "trace"
    )
    evidence = EvidenceItem(
        evidence_id="e-payment-root",
        evidence_type=EvidenceType.GRAPH,
        source_component="EventGraphBuilder",
        description="Payment-service concentra a primeira falha observada no caminho estrutural.",
        service_name="payment-service",
        normalized_value=0.9,
        direction=EvidenceDirection.SUPPORTING,
        observed_at=BASE + timedelta(seconds=1),
        attributes={"trace_id": TRACE_ID, "event_id": payment_trace.event_id},
    )
    top = RankedService(
        position=1,
        service_name="payment-service",
        scores=ScoreBreakdown(0.9, 0.8, 0.9, 0.9, 0.8, 0.9),
        evidence_ids=(evidence.evidence_id,),
    )
    second = RankedService(
        position=2,
        service_name="order-service",
        scores=ScoreBreakdown(0.1, 0.2, 0.1, 0.2, 0.1, 0.1),
    )
    reliability = ReliabilityDecision(
        is_reliable=reliable,
        confidence=0.9,
        confidence_method="fixture-contract-validation",
        minimum_confidence=0.7,
        margin_to_second=0.8,
        minimum_margin=0.2,
        passed_rules=("minimum_confidence", "minimum_margin") if reliable else (),
        failed_rules=() if reliable else ("observability_quality",),
    )
    return RCAResult.create(
        trace_id=TRACE_ID,
        ranking=[top, second],
        evidence=[evidence],
        reliability=reliability,
        model_version="xgboost-rca-v3",
        pipeline_version="causalods-rca-v3",
        metadata={
            "incident_id": bundle.incident_id,
            "analysis_window": {
                "start": bundle.analysis_window.start.isoformat(),
                "end": bundle.analysis_window.end.isoformat(),
            },
            "feature_registry_hash": FeatureRegistryV3().fingerprint,
            "fixture_stage": "model-output-contract",
        },
    )


def _explanation(result: RCAResult) -> StructuredExplanation:
    evidence_id = result.ranking[0].evidence_ids[0]
    return StructuredExplanation(
        schema_version="structured-explanation-v1",
        root_cause=result.root_cause_service,
        summary="O payment-service é a causa raiz prevista com evidência rastreável.",
        evidence_statements=(EvidenceStatementV1(
            text="A primeira falha observada pertence ao payment-service.",
            evidence_ids=(evidence_id,),
        ),),
        propagation_path=("payment-service", "order-service"),
        uncertainty="low",
        missing_evidence=(),
        limitations=("fixture_model_output_not_live_inference",),
        recommendations=("Inspecionar o fluxo de pagamento.",),
        cited_evidence_ids=(evidence_id,),
        mode="evidence_grounded",
        model_name="deterministic-fixture",
        model_version=result.model_version,
        model_hash="0" * 64,
        prompt_hash=sha256_value({"result": result.rca_result_id, "evidence": evidence_id}),
        confidence=result.confidence,
        reliability=result.reliability.is_reliable,
        latency_ms=0,
    )


def build_demo_scenario(name: str) -> DemoScenario:
    events = scenario_events(name)
    bundles = IncidentEvidenceAssembler(
        topology={"order-service": ("payment-service",)}
    ).assemble(events)
    if len(bundles) != 1:
        raise RuntimeError(f"Fixture {name} não formou um único incidente.")
    bundle = bundles[0]
    graph = EventGraphBuilder().build(bundle)
    vectors = RCAFeatureExtractorV3().extract(
        bundle, graph, topology={"order-service": ("payment-service",)}
    )
    if name == "healthy":
        result = explanation = None
    else:
        result = _rca_result(bundle, reliable=name != "missing_signal")
        explanation = _explanation(result)
    return DemoScenario(name, events, bundle, graph, vectors, result, explanation)
