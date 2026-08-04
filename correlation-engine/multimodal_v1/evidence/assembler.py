from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping

from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence.aggregation import EvidenceAggregator
from multimodal_v1.evidence.association import AssociationScore, EventAssociationScorer
from multimodal_v1.evidence.contracts import (
    AnalysisWindow, AssociationDecision, EventAssociation, EventReference,
    ExternalIdentityEvidence, IncidentEvidenceBundle, ProvenanceRecord,
    IncidentIdentityEvidence, TrustedAssociationContext, utc,
)
from multimodal_v1.evidence.policy import IncidentWindowPolicy, KnownTopology, ServiceIdentityResolver
from multimodal_v1.evidence.telemetry import AssemblerTelemetry


def _event_key(event: UnifiedEvent) -> EventReference:
    return EventReference(event.event_id, event.source_reference)


@dataclass
class _IncidentState:
    incident_id: str
    workflow_key: str
    lifecycle_generation: int
    events: list[UnifiedEvent] = field(default_factory=list)
    associations: dict[EventReference, EventAssociation] = field(default_factory=dict)
    late_keys: set[EventReference] = field(default_factory=set)
    identity_evidence: list[IncidentIdentityEvidence] = field(default_factory=list)
    external_identity_evidence: list[ExternalIdentityEvidence] = field(default_factory=list)
    closed_at: datetime | None = None
    reopened_count: int = 0

    @property
    def first_at(self) -> datetime:
        return min(item.event_timestamp for item in self.events)

    @property
    def last_at(self) -> datetime:
        return max(item.event_timestamp for item in self.events)


class IncidentEvidenceAssembler:
    """Assembler v3 single-threaded, isolado do consumer e pipeline v2."""

    def __init__(self, *, policy: IncidentWindowPolicy | None = None,
                 topology: Mapping[str, Iterable[str]] | Iterable[tuple[str, str]] | None = None,
                 resolver: ServiceIdentityResolver | None = None,
                 telemetry: AssemblerTelemetry | None = None):
        self.policy = policy or IncidentWindowPolicy()
        self.resolver = resolver or ServiceIdentityResolver()
        self.topology = KnownTopology(topology)
        self.scorer = EventAssociationScorer(self.policy, self.topology, self.resolver)
        self.aggregator = EvidenceAggregator(self.resolver, self.policy.max_log_samples)
        self.telemetry = telemetry or AssemblerTelemetry()
        self._active: dict[str, _IncidentState] = {}
        self._closed: dict[str, _IncidentState] = {}
        self._watermark: datetime | None = None
        self._workflow_generations: dict[str, int] = {}

    def ingest(self, event: UnifiedEvent, *,
               context: TrustedAssociationContext | None = None) -> AssociationDecision:
        if not isinstance(event, UnifiedEvent):
            raise TypeError("IncidentEvidenceAssembler aceita apenas UnifiedEvent.")
        started = time.perf_counter()
        self.telemetry.increment("input_events")
        try:
            correlation_id, trace_id, external_evidence, current_identities = (
                self._association_identity(event, context)
            )
            duplicate = self._find_duplicate(event)
            if duplicate:
                self.telemetry.increment("duplicate_events")
                return AssociationDecision(_event_key(event), duplicate.incident_id, 1.0,
                                           ("duplicate_event_id_and_source_reference",), True, True)
            state, score, reopened, ambiguous = self._select(
                event, current_identities
            )
            if ambiguous:
                self.telemetry.increment("unassociated_events")
                return AssociationDecision(_event_key(event), None, 0.0, score.reasons, False)
            if state is None:
                if not self._can_seed():
                    self.telemetry.increment("unassociated_events")
                    self.telemetry.increment("memory_limit_rejections")
                    return AssociationDecision(_event_key(event), None, 0.0, ("memory_limit",), False)
                workflow_key = self._workflow_key(event, correlation_id, trace_id)
                generation = self._next_generation(workflow_key)
                state = _IncidentState(
                    self._incident_id(workflow_key, event, generation), workflow_key, generation
                )
                self._active[state.incident_id] = state
                context_reasons = tuple(
                    f"trusted_context:{item.identity_type}:{item.source}:{item.reason}"
                    for item in external_evidence
                )
                score = AssociationScore(1.0, ("incident_seed", *context_reasons))
            elif not self._has_capacity(state, reopening=reopened):
                self.telemetry.increment("unassociated_events")
                self.telemetry.increment("memory_limit_rejections")
                return AssociationDecision(_event_key(event), None, 0.0, ("memory_limit",), False)
            elif reopened:
                state.closed_at = None
                state.reopened_count += 1
                self._closed.pop(state.incident_id, None)
                self._active[state.incident_id] = state
                self.telemetry.increment("reopened_bundles")
            key = _event_key(event)
            state.events.append(event)
            state.identity_evidence.extend(current_identities)
            state.external_identity_evidence.extend(external_evidence)
            state.associations[key] = EventAssociation(key, score.score, score.reasons)
            if (self._watermark and event.event_timestamp <= self._watermark) or event.late_arrival_ms:
                state.late_keys.add(key)
            self.telemetry.increment("associated_events")
            self._update_gauges()
            return AssociationDecision(key, state.incident_id, score.score, score.reasons, True,
                                       reopened=reopened)
        finally:
            self.telemetry.observe_latency((time.perf_counter() - started) * 1000)

    def assemble(self, events: Iterable[UnifiedEvent], *,
                 context: TrustedAssociationContext | None = None,
                 watermark: datetime | None = None) -> tuple[IncidentEvidenceBundle, ...]:
        touched: set[str] = set()
        for event in events:
            decision = self.ingest(event, context=context)
            if decision.incident_id:
                touched.add(decision.incident_id)
        if watermark is not None:
            self.advance_watermark(watermark)
        states = {**self._closed, **self._active}
        return tuple(self._bundle(states[item]) for item in sorted(touched) if item in states)

    def advance_watermark(self, watermark: datetime) -> tuple[IncidentEvidenceBundle, ...]:
        value = utc(watermark, "watermark")
        if self._watermark and value < self._watermark:
            raise ValueError("watermark não pode retroceder.")
        self._watermark = value
        closed = []
        for incident_id, state in list(self._active.items()):
            if value >= state.last_at + self.policy.allowed_lateness:
                state.closed_at = value
                self._closed[incident_id] = self._active.pop(incident_id)
                closed.append(self._bundle(state))
                self.telemetry.increment("closed_bundles")
        self._update_gauges()
        return tuple(closed)

    def expire(self, now: datetime | None = None) -> tuple[IncidentEvidenceBundle, ...]:
        value = utc(now or datetime.now(timezone.utc), "now")
        expired = []
        for incident_id, state in list(self._active.items()):
            if value >= state.last_at + self.policy.expiration:
                state.closed_at = value
                expired.append(self._bundle(state))
                del self._active[incident_id]
                self.telemetry.increment("expired_bundles")
        for incident_id, state in list(self._closed.items()):
            if state.closed_at and value >= state.closed_at + self.policy.expiration:
                expired.append(self._bundle(state))
                del self._closed[incident_id]
                self.telemetry.increment("expired_bundles")
        self._update_gauges()
        return tuple(expired)

    def active_bundles(self) -> tuple[IncidentEvidenceBundle, ...]:
        return tuple(self._bundle(state) for _, state in sorted(self._active.items()))

    def closed_bundles(self) -> tuple[IncidentEvidenceBundle, ...]:
        return tuple(self._bundle(state) for _, state in sorted(self._closed.items()))

    def _select(self, event: UnifiedEvent,
                current_identities: tuple[IncidentIdentityEvidence, ...],
                ) -> tuple[_IncidentState | None, AssociationScore, bool, bool]:
        candidates: list[tuple[float, str, _IncidentState, AssociationScore, bool]] = []
        for state in self._active.values():
            result = self.scorer.score(
                event, state.events, current_identities=current_identities,
                candidate_identities=state.identity_evidence,
            )
            if result.score >= self.policy.association_threshold:
                candidates.append((result.score, state.incident_id, state, result, False))
        for state in self._closed.values():
            result = self.scorer.score(
                event, state.events, current_identities=current_identities,
                candidate_identities=state.identity_evidence,
            )
            within = bool(
                state.closed_at
                and state.first_at - self.policy.allowed_lateness
                <= event.event_timestamp
                <= state.closed_at + self.policy.reopen_window
            )
            if within and result.score >= self.policy.reopen_threshold:
                candidates.append((result.score, state.incident_id, state, result, True))
        if not candidates:
            return None, AssociationScore(0.0, ()), False, False
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) <= self.policy.ambiguity_margin:
            strength = "strong" if candidates[0][0] >= self.policy.reopen_threshold else "weak"
            return None, AssociationScore(0.0, (f"ambiguous_{strength}_association",)), False, True
        _, _, state, result, reopened = candidates[0]
        return state, result, reopened, False

    def _bundle(self, state: _IncidentState) -> IncidentEvidenceBundle:
        events = tuple(sorted(state.events, key=lambda item: (item.event_timestamp, item.event_id, item.source_reference or "")))
        signals = {event.signal_type.value for event in events}
        all_signals = tuple(item.value for item in SignalType)
        traces = tuple(sorted({event.trace_id for event in events if event.trace_id}))
        entities = tuple(sorted({(event.business_entity_type, event.business_entity_id)
                                 for event in events if event.business_entity_id}, key=lambda item: ((item[0] or ""), item[1])))
        scores = {_event_key(event): state.associations[_event_key(event)].score for event in events}
        return IncidentEvidenceBundle(
            incident_id=state.incident_id,
            workflow_key=state.workflow_key,
            analysis_window=AnalysisWindow(state.first_at, state.last_at),
            events=events,
            services=self.aggregator.aggregate(events),
            traces=traces,
            business_entities=entities,
            missing_modalities=tuple(signal for signal in all_signals if signal not in signals),
            late_events=tuple(sorted(
                state.late_keys, key=lambda item: (item.event_id, item.source_reference or "")
            )),
            association_reasons=tuple(state.associations[_event_key(event)] for event in events),
            association_scores=scores,
            provenance=tuple(ProvenanceRecord(_event_key(event), event.source_dataset) for event in events),
            external_identity_evidence=tuple(state.external_identity_evidence),
            identity_evidence=tuple(state.identity_evidence),
            lifecycle_generation=state.lifecycle_generation,
            reopened_count=state.reopened_count,
            closed=state.closed_at is not None,
        )

    def _find_duplicate(self, event: UnifiedEvent) -> _IncidentState | None:
        key = _event_key(event)
        return next((state for state in (*self._active.values(), *self._closed.values())
                     if key in state.associations), None)

    def _can_seed(self) -> bool:
        return len(self._active) < self.policy.max_active_incidents and self._total_events() < self.policy.max_total_events

    def _has_capacity(self, state: _IncidentState, *, reopening: bool) -> bool:
        if len(state.events) >= self.policy.max_events_per_incident:
            return False
        if self._total_events() >= self.policy.max_total_events:
            return False
        if reopening and len(self._active) >= self.policy.max_active_incidents:
            return False
        return True

    def _total_events(self) -> int:
        return sum(len(state.events) for state in (*self._active.values(), *self._closed.values()))

    def _update_gauges(self) -> None:
        self.telemetry.gauges(bundle_size=self._total_events(), active_bundles=len(self._active))

    @staticmethod
    def _workflow_key(event: UnifiedEvent, correlation_id: str | None, trace_id: str | None) -> str:
        identity = correlation_id or trace_id or f"{event.event_id}|{event.source_reference or ''}"
        kind = "correlation" if correlation_id else "trace" if trace_id else "event"
        digest = hashlib.sha256(f"{kind}:{identity}".encode("utf-8")).hexdigest()[:24]
        return f"workflow-{kind}-{digest}"

    @staticmethod
    def _incident_id(workflow_key: str, event: UnifiedEvent, generation: int) -> str:
        timestamp = event.event_timestamp.isoformat()
        material = (
            f"{workflow_key}|{event.event_id}|{timestamp}|{event.source_reference or ''}|"
            f"{generation}"
        ).encode("utf-8")
        return f"inc-v3-{hashlib.sha256(material).hexdigest()[:24]}"

    def _next_generation(self, workflow_key: str) -> int:
        generation = self._workflow_generations.get(workflow_key, -1) + 1
        self._workflow_generations[workflow_key] = generation
        return generation

    @staticmethod
    def _association_identity(
        event: UnifiedEvent, context: TrustedAssociationContext | None,
    ) -> tuple[
        str | None, str | None, tuple[ExternalIdentityEvidence, ...],
        tuple[IncidentIdentityEvidence, ...],
    ]:
        reference = _event_key(event)
        identities: list[IncidentIdentityEvidence] = []
        if event.correlation_id:
            identities.append(IncidentIdentityEvidence(
                reference, "correlation_id", event.correlation_id, "observed", 1.0
            ))
        if event.trace_id:
            identities.append(IncidentIdentityEvidence(
                reference, "trace_id", event.trace_id, "observed", 0.92
            ))
        if context is None:
            return event.correlation_id, event.trace_id, (), tuple(identities)
        correlation_id, trace_id, evidence = context.identities_for(event)
        for item in evidence:
            maximum = 1.0 if item.identity_type == "correlation_id" else 0.92
            identities.append(IncidentIdentityEvidence(
                item.event_reference, item.identity_type, item.identity_value, "external",
                min(maximum, item.confidence), item.source, item.reason,
            ))
        return correlation_id, trace_id, evidence, tuple(identities)
