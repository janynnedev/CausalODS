from datetime import datetime, timedelta, timezone

import pytest

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import (
    EventAssociationScorer, EventReference, IncidentEvidenceAssembler,
    IncidentWindowPolicy, TrustedAssociationContext, TrustedIdentityClaim,
)


BASE = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def event(event_id, signal="trace", seconds=0, **values):
    defaults = dict(
        event_id=event_id,
        event_name={"trace": "span", "log": "log", "metric": "latency",
                    "business_event": "order.created"}[signal],
        event_type=signal,
        signal_type=signal,
        event_timestamp=BASE + timedelta(seconds=seconds),
        service_name="order-service",
        source_reference=f"kafka:{event_id}",
    )
    if signal == "trace":
        defaults.update(trace_id="a" * 32, span_id=(event_id[-1] * 16))
    elif signal == "metric":
        defaults.update(metric_name="http.server.duration", metric_value=10)
    elif signal == "business_event":
        defaults.update(business_operation="order.created", business_outcome="created")
    defaults.update(values)
    return UnifiedEvent(**defaults)


def test_single_trace_is_assembled_and_aggregated_by_service():
    assembler = IncidentEvidenceAssembler()
    bundle = assembler.assemble([
        event("span-1", span_id="1" * 16),
        event("span-2", seconds=1, span_id="2" * 16, parent_span_id="1" * 16,
              service_name="payment-service", severity="ERROR", error_type="TimeoutError"),
    ])[0]
    assert len(bundle.events) == 2
    assert bundle.traces == ("a" * 32,)
    assert {item.identity.service_name for item in bundle.services} == {"order-service", "payment-service"}
    assert next(item for item in bundle.services if item.identity.service_name == "payment-service").error_rate == 1


def test_multiple_traces_share_workflow_but_trace_id_remains_distinct():
    assembler = IncidentEvidenceAssembler()
    bundles = assembler.assemble([
        event("one-1", correlation_id="workflow-42", trace_id="a" * 32),
        event("two-2", seconds=20, correlation_id="workflow-42", trace_id="b" * 32),
    ])
    assert len(bundles) == 1
    assert bundles[0].traces == ("a" * 32, "b" * 32)
    assert bundles[0].association_reasons[1].reasons[0] == "same_correlation_id"


def test_log_and_metric_without_trace_associate_by_topology_and_time():
    assembler = IncidentEvidenceAssembler(topology={"order-service": ["payment-service"]})
    bundles = assembler.assemble([
        event("log-1", "log", service_name="order-service", attributes={"body": "timeout"}),
        event("metric-1", "metric", seconds=2, service_name="payment-service", metric_value=50),
    ])
    assert len(bundles) == 1
    bundle = bundles[0]
    assert "topology_and_temporal_proximity" in bundle.association_reasons[1].reasons
    assert bundle.services[1].metrics[0].average == 50


def test_late_event_closes_by_watermark_and_reopens_only_with_strong_identity():
    policy = IncidentWindowPolicy(allowed_lateness=timedelta(seconds=5), reopen_window=timedelta(minutes=2))
    assembler = IncidentEvidenceAssembler(policy=policy)
    first = assembler.ingest(event("span-1", correlation_id="saga-1"))
    closed = assembler.advance_watermark(BASE + timedelta(seconds=10))
    assert closed[0].closed
    late = assembler.ingest(event("late-2", "log", seconds=2, correlation_id="saga-1"))
    assert late.incident_id == first.incident_id and late.reopened
    bundle = assembler.active_bundles()[0]
    assert bundle.reopened_count == 1
    assert bundle.late_events == (EventReference("late-2", "kafka:late-2"),)


def test_weak_late_event_does_not_reopen_closed_incident():
    assembler = IncidentEvidenceAssembler()
    original = assembler.ingest(event("log-1", "log"))
    assembler.advance_watermark(BASE + timedelta(minutes=1))
    unrelated = assembler.ingest(event("log-2", "log", seconds=1, service_name="other"))
    assert unrelated.incident_id != original.incident_id
    assert not unrelated.reopened


def test_two_simultaneous_incidents_and_conflicting_correlation_ids_do_not_mix():
    assembler = IncidentEvidenceAssembler()
    bundles = assembler.assemble([
        event("a-1", correlation_id="workflow-a"),
        event("b-2", correlation_id="workflow-b", trace_id="b" * 32),
        event("a-3", "log", seconds=1, correlation_id="workflow-a"),
    ])
    assert len(bundles) == 2
    assert sorted(len(bundle.events) for bundle in bundles) == [1, 2]


def test_event_id_collision_is_disambiguated_by_source_reference():
    assembler = IncidentEvidenceAssembler()
    first = event("same-id", "log", correlation_id="workflow", source_reference="topic-a:1")
    second = event("same-id", "log", seconds=1, correlation_id="workflow", source_reference="topic-b:1")
    bundle = assembler.assemble([first, second])[0]
    assert len(bundle.events) == 2
    assert set(bundle.association_scores) == {
        EventReference("same-id", "topic-a:1"), EventReference("same-id", "topic-b:1")
    }
    duplicate = assembler.ingest(first)
    assert duplicate.duplicate


def test_missing_modalities_entities_quality_and_business_states_are_reported():
    assembler = IncidentEvidenceAssembler()
    bundle = assembler.assemble([
        event("business-1", "business_event", correlation_id="workflow",
              business_entity_type="order", business_entity_id="order-7",
              missing_fields=("service_version",), data_quality_flags=("legacy_source",)),
    ])[0]
    assert bundle.missing_modalities == ("trace", "log", "metric")
    assert bundle.business_entities == (("order", "order-7"),)
    assert bundle.services[0].business_states[0]["outcome"] == "created"
    assert bundle.services[0].data_quality_flags == ("legacy_source",)


def test_expiration_removes_closed_bundle_and_reopen_after_expiration_creates_new_incident():
    policy = IncidentWindowPolicy(allowed_lateness=timedelta(0), expiration=timedelta(minutes=1))
    assembler = IncidentEvidenceAssembler(policy=policy)
    first = assembler.ingest(event("one-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE)
    expired = assembler.expire(BASE + timedelta(minutes=1))
    assert expired[0].incident_id == first.incident_id
    assert not assembler.closed_bundles()
    new = assembler.ingest(event("two-2", "log", seconds=2, correlation_id="workflow"))
    assert new.incident_id != first.incident_id
    assert not new.reopened


def test_expiration_also_bounds_abandoned_active_incidents():
    policy = IncidentWindowPolicy(expiration=timedelta(minutes=1))
    assembler = IncidentEvidenceAssembler(policy=policy)
    assembler.ingest(event("abandoned-1", "log"))
    expired = assembler.expire(BASE + timedelta(minutes=1))
    assert len(expired) == 1 and expired[0].closed
    assert not assembler.active_bundles()


def test_memory_limits_reject_without_growing_state_and_emit_telemetry():
    policy = IncidentWindowPolicy(max_active_incidents=1, max_events_per_incident=1, max_total_events=1)
    assembler = IncidentEvidenceAssembler(policy=policy)
    assert assembler.ingest(event("one-1", correlation_id="one")).associated
    rejected = assembler.ingest(event("two-2", correlation_id="two", trace_id="b" * 32))
    assert not rejected.associated and rejected.reasons == ("memory_limit",)
    telemetry = assembler.telemetry.snapshot()
    assert telemetry["input_events"] == 2
    assert telemetry["associated_events"] == 1
    assert telemetry["unassociated_events"] == 1
    assert telemetry["bundle_size"] == 1
    assert telemetry["association_latency_samples"] == 2


def test_watermark_and_contract_reject_naive_time_and_wrong_input():
    assembler = IncidentEvidenceAssembler()
    with pytest.raises(ValueError, match="timezone-aware"):
        assembler.advance_watermark(datetime(2026, 8, 4))
    with pytest.raises(TypeError, match="UnifiedEvent"):
        assembler.ingest({})


def test_very_old_event_does_not_reopen_and_new_cycle_preserves_closed_bundle():
    policy = IncidentWindowPolicy(allowed_lateness=timedelta(seconds=5), reopen_window=timedelta(minutes=2))
    assembler = IncidentEvidenceAssembler(policy=policy)
    first = assembler.ingest(event("seed-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE + timedelta(seconds=10))
    old = assembler.ingest(event("old-2", "log", seconds=-10, correlation_id="workflow"))
    assert not old.reopened
    assert old.incident_id != first.incident_id
    assert {bundle.incident_id for bundle in assembler.closed_bundles()} == {first.incident_id}


def test_new_lifecycle_same_workflow_has_stable_workflow_key_and_unique_incident_id():
    assembler = IncidentEvidenceAssembler(
        policy=IncidentWindowPolicy(allowed_lateness=timedelta(0), reopen_window=timedelta(seconds=10))
    )
    first = assembler.ingest(event("seed-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE)
    second = assembler.ingest(event("seed-2", "log", seconds=60, correlation_id="workflow"))
    closed = assembler.closed_bundles()[0]
    active = assembler.active_bundles()[0]
    assert first.incident_id != second.incident_id
    assert closed.workflow_key == active.workflow_key
    assert closed.incident_id == first.incident_id


def test_reopen_rejected_by_memory_has_no_state_or_telemetry_mutation():
    policy = IncidentWindowPolicy(
        allowed_lateness=timedelta(0), max_events_per_incident=1, max_total_events=2
    )
    assembler = IncidentEvidenceAssembler(policy=policy)
    first = assembler.ingest(event("seed-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE)
    before = assembler.closed_bundles()[0]
    counters = assembler.telemetry.snapshot()
    rejected = assembler.ingest(event("late-2", "log", seconds=1, correlation_id="workflow"))
    after = assembler.closed_bundles()[0]
    assert not rejected.associated
    assert before.incident_id == after.incident_id == first.incident_id
    assert before.reopened_count == after.reopened_count == 0
    assert before.analysis_window == after.analysis_window
    assert not assembler.active_bundles()
    assert assembler.telemetry.snapshot()["reopened_bundles"] == counters["reopened_bundles"]


def test_parent_span_requires_same_trace_and_exact_pair():
    policy = IncidentWindowPolicy(association_threshold=0.80, reopen_threshold=0.80)
    scorer = EventAssociationScorer(policy)
    parent = event("parent-1", trace_id="a" * 32, span_id="1" * 16)
    different_trace = event("child-2", trace_id="b" * 32, span_id="2" * 16,
                            parent_span_id="1" * 16)
    same_trace = event("child-3", trace_id="a" * 32, span_id="3" * 16,
                       parent_span_id="1" * 16)
    assert "parent_span_or_span_link" not in scorer.score(different_trace, [parent]).reasons
    assert "parent_span_or_span_link" in scorer.score(same_trace, [parent]).reasons


def test_business_entity_identity_includes_type_and_requires_type():
    policy = IncidentWindowPolicy(association_threshold=0.70, reopen_threshold=0.70)
    scorer = EventAssociationScorer(policy)
    order = event("order-1", "business_event", business_entity_type="order", business_entity_id="42")
    payment = event("payment-2", "business_event", business_entity_type="payment", business_entity_id="42")
    same_order = event("order-3", "business_event", business_entity_type="order", business_entity_id="42")
    missing_type = event("legacy-4", "business_event", business_entity_id="42")
    assert "same_business_entity_id" not in scorer.score(payment, [order]).reasons
    assert "same_business_entity_id" in scorer.score(same_order, [order]).reasons
    assert "same_business_entity_id" not in scorer.score(missing_type, [order]).reasons


def test_trusted_context_rejects_divergence_and_audits_supplied_identity():
    assembler = IncidentEvidenceAssembler()
    divergent = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("other", "gateway", 0.9, "verified header")
    )
    with pytest.raises(ValueError, match="diverge"):
        assembler.ingest(event("observed-1", correlation_id="workflow"), context=divergent)

    context = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", "gateway", 0.9, "verified header")
    )
    assembler.ingest(event("log-1", "log"), context=context)
    decision = assembler.ingest(event("log-2", "log", seconds=1), context=context)
    assert decision.associated
    assert any(
        reason.startswith("same_correlation_id[current=external:gateway:verified header")
        for reason in decision.reasons
    )


def test_late_event_references_distinguish_same_id_from_different_sources():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("seed-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE + timedelta(seconds=30))
    assembler.ingest(event("same", "log", seconds=1, correlation_id="workflow", source_reference="a:1"))
    assembler.ingest(event("same", "log", seconds=2, correlation_id="workflow", source_reference="b:1"))
    assert set(assembler.active_bundles()[0].late_events) == {
        EventReference("same", "a:1"), EventReference("same", "b:1")
    }


@pytest.mark.parametrize("values", [
    {"association_threshold": -0.1},
    {"association_threshold": 1.1},
    {"reopen_threshold": 0.2, "association_threshold": 0.3},
    {"ambiguity_margin": 1.1},
    {"max_log_samples": 0},
])
def test_invalid_policy_thresholds_and_limits_are_rejected(values):
    with pytest.raises(ValueError):
        IncidentWindowPolicy(**values)


def test_score_ties_within_margin_are_unassociated_not_lexically_selected():
    policy = IncidentWindowPolicy(association_threshold=0.4, ambiguity_margin=0.02)
    assembler = IncidentEvidenceAssembler(policy=policy)
    assembler.ingest(event("one-1", correlation_id="one"))
    assembler.ingest(event("two-2", correlation_id="two", trace_id="b" * 32))
    decision = assembler.ingest(event("ambiguous-3", "log", seconds=1))
    assert not decision.associated
    assert decision.incident_id is None
    assert decision.reasons == ("ambiguous_weak_association",)


def test_event_reference_none_and_text_are_sorted_safely_for_late_events():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("seed-1", correlation_id="workflow"))
    assembler.advance_watermark(BASE + timedelta(seconds=30))
    assembler.ingest(event("same", "log", seconds=1, correlation_id="workflow", source_reference=None))
    assembler.ingest(event("same", "log", seconds=2, correlation_id="workflow", source_reference="topic:2"))
    bundle = assembler.active_bundles()[0]
    assert bundle.late_events == (
        EventReference("same", None), EventReference("same", "topic:2")
    )


def test_observed_correlation_score_is_not_limited_by_external_trace_confidence():
    context = TrustedAssociationContext(
        trace=TrustedIdentityClaim("b" * 32, "gateway", 0.5, "linked trace")
    )
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("log-1", "log", correlation_id="workflow"), context=context)
    decision = assembler.ingest(
        event("log-2", "log", seconds=1, correlation_id="workflow"), context=context
    )
    assert decision.score == 1.0
    assert "same_correlation_id" in decision.reasons
    bundle = assembler.active_bundles()[0]
    assert bundle.traces == ()
    assert {item.identity_type for item in bundle.external_identity_evidence} == {"trace_id"}


def test_observed_trace_score_is_not_limited_by_external_correlation_confidence():
    context = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", "gateway", 0.5, "workflow header")
    )
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("log-1", "log", trace_id="a" * 32), context=context)
    decision = assembler.ingest(
        event("log-2", "log", seconds=1, trace_id="a" * 32), context=context
    )
    assert decision.score == 0.92
    assert "same_trace_id" in decision.reasons


def test_external_context_on_seed_is_preserved_as_separate_evidence():
    context = TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", "gateway", 0.75, "signed header"),
        trace=TrustedIdentityClaim("b" * 32, "collector", 0.6, "transport link"),
    )
    assembler = IncidentEvidenceAssembler()
    decision = assembler.ingest(event("seed-1", "log"), context=context)
    bundle = assembler.active_bundles()[0]
    assert decision.reasons[0] == "incident_seed"
    assert len(bundle.external_identity_evidence) == 2
    assert {(item.identity_type, item.source, item.confidence, item.reason) for item in bundle.external_identity_evidence} == {
        ("correlation_id", "gateway", 0.75, "signed header"),
        ("trace_id", "collector", 0.6, "transport link"),
    }
    assert bundle.traces == ()


@pytest.mark.parametrize("context", [
    lambda: TrustedAssociationContext(correlation=TrustedIdentityClaim(" ", "gateway", 1, "header")),
    lambda: TrustedAssociationContext(correlation=TrustedIdentityClaim("bad\x00id", "gateway", 1, "header")),
    lambda: TrustedAssociationContext(correlation=TrustedIdentityClaim("x" * 129, "gateway", 1, "header")),
    lambda: TrustedAssociationContext(trace=TrustedIdentityClaim("legacy-trace", "gateway", 1, "header")),
])
def test_invalid_external_identity_is_rejected(context):
    with pytest.raises(ValueError):
        context()


def test_replay_is_deterministic_for_incident_id_and_lifecycle_generation():
    policy = IncidentWindowPolicy(allowed_lateness=timedelta(0), reopen_window=timedelta(seconds=5))

    def replay():
        assembler = IncidentEvidenceAssembler(policy=policy)
        first = assembler.ingest(event("seed-1", correlation_id="workflow"))
        assembler.advance_watermark(BASE)
        second = assembler.ingest(event("seed-2", "log", seconds=30, correlation_id="workflow"))
        bundles = (*assembler.closed_bundles(), *assembler.active_bundles())
        return (first.incident_id, second.incident_id), tuple(
            (bundle.incident_id, bundle.lifecycle_generation) for bundle in bundles
        )

    assert replay() == replay()


def correlation_context(confidence, source="gateway", reason="verified workflow"):
    return TrustedAssociationContext(
        correlation=TrustedIdentityClaim("workflow", source, confidence, reason)
    )


def trace_context(confidence, source="gateway", reason="verified trace"):
    return TrustedAssociationContext(
        trace=TrustedIdentityClaim("b" * 32, source, confidence, reason)
    )


def test_external_correlation_seed_then_observed_match_keeps_candidate_confidence():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("external-1", "log"), context=correlation_context(0.5))
    decision = assembler.ingest(event("observed-2", "log", seconds=1, correlation_id="workflow"))
    assert decision.score == 0.5
    assert "current=observed" in decision.reasons[0]
    assert "candidate=external:gateway:verified workflow" in decision.reasons[0]


def test_observed_correlation_seed_then_external_match_keeps_current_confidence():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("observed-1", "log", correlation_id="workflow"))
    decision = assembler.ingest(
        event("external-2", "log", seconds=1), context=correlation_context(0.5)
    )
    assert decision.score == 0.5
    assert "current=external:gateway:verified workflow" in decision.reasons[0]
    assert "candidate=observed" in decision.reasons[0]


def test_external_observed_order_is_semantically_symmetric():
    def run(external_first):
        assembler = IncidentEvidenceAssembler()
        external = (event("external", "log", seconds=0 if external_first else 1), correlation_context(0.5))
        observed = (event("observed", "log", seconds=1 if external_first else 0,
                          correlation_id="workflow"), None)
        first, second = (external, observed) if external_first else (observed, external)
        assembler.ingest(first[0], context=first[1])
        decision = assembler.ingest(second[0], context=second[1])
        origins = {item.origin for item in assembler.active_bundles()[0].identity_evidence}
        return decision.score, origins

    assert run(True) == run(False) == (0.5, {"observed", "external"})


def test_two_observed_correlations_match_at_one():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("observed-1", "log", correlation_id="workflow"))
    decision = assembler.ingest(event("observed-2", "log", seconds=1, correlation_id="workflow"))
    assert decision.score == 1.0
    assert decision.reasons[0] == "same_correlation_id"


def test_two_observed_traces_match_at_point_ninety_two():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("observed-1", "log", trace_id="b" * 32))
    decision = assembler.ingest(event("observed-2", "log", seconds=1, trace_id="b" * 32))
    assert decision.score == 0.92
    assert decision.reasons[0] == "same_trace_id"


def test_external_trace_seed_then_observed_trace_matches_at_point_five():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("external-1", "log"), context=trace_context(0.5))
    decision = assembler.ingest(event("observed-2", "log", seconds=1, trace_id="b" * 32))
    assert decision.score == 0.5


def test_two_external_claims_use_minimum_confidence():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("external-1", "log"), context=correlation_context(0.8, "one", "first"))
    decision = assembler.ingest(
        event("external-2", "log", seconds=1),
        context=correlation_context(0.6, "two", "second"),
    )
    assert decision.score == 0.6
    assert "current=external:two:second" in decision.reasons[0]
    assert "candidate=external:one:first" in decision.reasons[0]


def test_low_confidence_external_candidate_does_not_reopen_but_observed_pair_does():
    policy = IncidentWindowPolicy(allowed_lateness=timedelta(0), reopen_threshold=0.70)
    external = IncidentEvidenceAssembler(policy=policy)
    external.ingest(event("external-1", "log"), context=correlation_context(0.5))
    external.advance_watermark(BASE)
    rejected = external.ingest(event("observed-2", "log", seconds=1, correlation_id="workflow"))
    assert not rejected.reopened
    assert len(external.closed_bundles()) == 1

    observed = IncidentEvidenceAssembler(policy=policy)
    observed.ingest(event("observed-1", "log", correlation_id="workflow"))
    observed.advance_watermark(BASE)
    reopened = observed.ingest(event("observed-2", "log", seconds=1, correlation_id="workflow"))
    assert reopened.reopened and reopened.score == 1.0


def test_zero_confidence_trusted_claim_is_rejected():
    with pytest.raises(ValueError, match="0 < confidence"):
        TrustedIdentityClaim("workflow", "gateway", 0, "header")


def test_later_observation_does_not_rewrite_historical_association_score():
    assembler = IncidentEvidenceAssembler()
    assembler.ingest(event("external-1", "log"), context=correlation_context(0.5))
    second = assembler.ingest(event("observed-2", "log", seconds=1, correlation_id="workflow"))
    third = assembler.ingest(event("observed-3", "log", seconds=2, correlation_id="workflow"))
    bundle = assembler.active_bundles()[0]
    assert second.score == 0.5
    assert third.score == 1.0  # upgrade explícito vale somente para matches futuros
    assert bundle.association_scores[EventReference("observed-2", "kafka:observed-2")] == 0.5
