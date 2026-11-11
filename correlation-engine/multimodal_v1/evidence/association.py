from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence.contracts import EventReference, IncidentIdentityEvidence
from multimodal_v1.evidence.policy import IncidentWindowPolicy, KnownTopology, ServiceIdentityResolver


@dataclass(frozen=True)
class AssociationScore:
    score: float
    reasons: tuple[str, ...]


class EventAssociationScorer:
    """Pontua evidência observada; não cria relações causais derivadas."""

    def __init__(self, policy: IncidentWindowPolicy, topology: KnownTopology | None = None,
                 resolver: ServiceIdentityResolver | None = None):
        self.policy = policy
        self.topology = topology or KnownTopology()
        self.resolver = resolver or ServiceIdentityResolver()

    def score(self, event: UnifiedEvent, candidates: Iterable[UnifiedEvent], *,
              current_identities: Iterable[IncidentIdentityEvidence] = (),
              candidate_identities: Iterable[IncidentIdentityEvidence] = ()) -> AssociationScore:
        items = tuple(candidates)
        if not items:
            return AssociationScore(0.0, ())
        current = tuple(current_identities) or self._observed_identities((event,))
        stored = tuple(candidate_identities) or self._observed_identities(items)
        current_correlations = {item.identity_value for item in current if item.identity_type == "correlation_id"}
        stored_correlations = {item.identity_value for item in stored if item.identity_type == "correlation_id"}
        if current_correlations and stored_correlations and current_correlations.isdisjoint(stored_correlations):
            return AssociationScore(0.0, ("conflicting_correlation_id",))

        reasons: list[str] = []
        strengths: list[float] = []
        identity_scores: dict[str, float] = {}
        for identity_type, reason in (
            ("correlation_id", "same_correlation_id"), ("trace_id", "same_trace_id")
        ):
            matches = [
                (left, right, min(left.confidence, right.confidence))
                for left in current if left.identity_type == identity_type
                for right in stored
                if right.identity_type == identity_type and left.identity_value == right.identity_value
            ]
            if matches:
                left, right, match_score = max(matches, key=lambda item: item[2])
                reasons.append(self._identity_reason(reason, left, right))
                strengths.append(match_score)
                identity_scores[identity_type] = match_score
        if self._span_related(event, items):
            reasons.append("parent_span_or_span_link")
            strengths.append(0.85)
        entities = {(item.business_entity_type, item.business_entity_id) for item in items
                    if item.business_entity_type and item.business_entity_id}
        entity = (event.business_entity_type, event.business_entity_id)
        if event.business_entity_type and event.business_entity_id and entity in entities:
            reasons.append("same_business_entity_id")
            strengths.append(0.75)

        current_traces = {item.identity_value for item in current if item.identity_type == "trace_id"}
        stored_traces = {item.identity_value for item in stored if item.identity_type == "trace_id"}
        if (current_traces and stored_traces and current_traces.isdisjoint(stored_traces)
                and not strengths):
            # Time/topology alone must not attach an identified request to an
            # unrelated probe/workflow before its correlation-bearing span arrives.
            return AssociationScore(0.0, ("distinct_trace_identity_without_link",))

        temporal = self.policy.temporal_score(event.event_timestamp, (item.event_timestamp for item in items))
        service = self.resolver.resolve(event).service_name
        related = any(self.topology.related(service, self.resolver.resolve(item).service_name) for item in items)
        if temporal > 0 and related:
            reasons.append("topology_and_temporal_proximity")
            strengths.append(0.45 + 0.20 * temporal)
        elif temporal > 0:
            reasons.append("temporal_proximity_only")
            strengths.append(0.10 + 0.25 * temporal)
        if identity_scores:
            final_score = max(identity_scores.values())
        else:
            final_score = max(strengths, default=0.0)
        return AssociationScore(final_score, tuple(reasons))

    @staticmethod
    def _identity_reason(
        base: str, current: IncidentIdentityEvidence, candidate: IncidentIdentityEvidence,
    ) -> str:
        if current.origin == candidate.origin == "observed":
            return base
        current_detail = current.origin
        candidate_detail = candidate.origin
        if current.origin == "external":
            current_detail += f":{current.source}:{current.reason}"
        if candidate.origin == "external":
            candidate_detail += f":{candidate.source}:{candidate.reason}"
        return f"{base}[current={current_detail};candidate={candidate_detail}]"

    @staticmethod
    def _observed_identities(events: Iterable[UnifiedEvent]) -> tuple[IncidentIdentityEvidence, ...]:
        identities = []
        for item in events:
            reference = EventReference(item.event_id, item.source_reference)
            if item.correlation_id:
                identities.append(IncidentIdentityEvidence(
                    reference, "correlation_id", item.correlation_id, "observed", 1.0
                ))
            if item.trace_id:
                identities.append(IncidentIdentityEvidence(
                    reference, "trace_id", item.trace_id, "observed", 0.92
                ))
        return tuple(identities)

    @staticmethod
    def _span_related(event: UnifiedEvent, items: tuple[UnifiedEvent, ...]) -> bool:
        if not event.trace_id:
            return False
        spans = {(item.trace_id, item.span_id) for item in items if item.trace_id and item.span_id}
        parents = {(item.trace_id, item.parent_span_id) for item in items if item.trace_id and item.parent_span_id}
        if event.parent_span_id and (event.trace_id, event.parent_span_id) in spans:
            return True
        if event.span_id and (event.trace_id, event.span_id) in parents:
            return True
        for link in event.span_links:
            if link.get("trace_id") and link.get("span_id") and (link["trace_id"], link["span_id"]) in spans:
                return True
        for item in items:
            if not item.trace_id:
                continue
            for link in item.span_links:
                if link.get("trace_id") == event.trace_id and link.get("span_id") == event.span_id:
                    return True
        return False
