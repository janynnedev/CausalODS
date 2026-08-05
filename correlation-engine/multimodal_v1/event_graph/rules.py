from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Mapping

from multimodal_v1.anomaly import AnomalyPolicyV1
from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence.contracts import EventReference, IncidentIdentityEvidence
from multimodal_v1.event_graph.contracts import (
    EDGE_TAXONOMY, EventGraphEdge, RelationType, reference_key,
)


@dataclass(frozen=True)
class BusinessTransitionPolicy:
    version: str = "business-transition-v1"
    transitions: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: {
        "order.created": ("order.approved", "order.rejected"),
        "payment.requested": ("payment.approved", "payment.failed"),
    })

    def allows(self, source: str, target: str) -> bool:
        return target in self.transitions.get(source, ())

    def to_dict(self):
        return {"version": self.version,
                "transitions": {key: list(self.transitions[key]) for key in sorted(self.transitions)}}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class TopologyDependencyPolicy:
    topology_version: str = "topology-v1"
    allow_healthy_prior: bool = False
    configured_topology_weight: float = 0.55
    healthy_prior_confidence: float = 0.25

    def __post_init__(self):
        for name in ("configured_topology_weight", "healthy_prior_confidence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"{name} deve estar entre 0 e 1.")

    def to_dict(self):
        return {"topology_version": self.topology_version,
                "allow_healthy_prior": self.allow_healthy_prior,
                "configured_topology_weight": self.configured_topology_weight,
                "healthy_prior_confidence": self.healthy_prior_confidence}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class TopologySupport:
    support_type: str
    confidence: float
    evidence_references: tuple[EventReference, ...]
    explanation: str


@dataclass(frozen=True)
class RuleContext:
    events: tuple[UnifiedEvent, ...]
    references: Mapping[EventReference, UnifiedEvent]
    derived_at: datetime
    temporal_window: timedelta
    topology: Mapping[str, tuple[str, ...]]
    identity_evidence: tuple[IncidentIdentityEvidence, ...]
    business_policy: BusinessTransitionPolicy
    topology_policy: TopologyDependencyPolicy
    anomaly_policy: AnomalyPolicyV1


EdgeRule = Callable[[RuleContext], Iterable[EventGraphEdge]]


def _ref(event):
    return EventReference(event.event_id, event.source_reference)


def _edge(source, target, relation, context, confidence, explanation, version="v1", evidence_references=None):
    classification, direction = EDGE_TAXONOMY[relation]
    source_ref, target_ref = _ref(source), _ref(target)
    return EventGraphEdge(
        source_ref, target_ref, relation, confidence, confidence,
        tuple(evidence_references or (source_ref, target_ref)), version, direction, context.derived_at,
        classification, explanation,
    )


def _groups(events, key):
    grouped = defaultdict(list)
    for event in events:
        value = key(event)
        if value is not None:
            grouped[value].append(event)
    return grouped.values()


def _chains(groups):
    for group in groups:
        ordered = sorted(group, key=lambda event: (event.event_timestamp, *reference_key(_ref(event))))
        yield from zip(ordered, ordered[1:])


def _span_index(events):
    spans = defaultdict(list)
    for event in events:
        if event.trace_id and event.span_id:
            spans[(event.trace_id, event.span_id)].append(event)
    for values in spans.values():
        values.sort(key=lambda event: (event.event_timestamp, *reference_key(_ref(event))))
    return spans


def _preferred_observations(candidates, target):
    same_source = [event for event in candidates if target.source_reference and event.source_reference == target.source_reference]
    if same_source:
        return same_source, "same_source_reference"
    same_dataset = [event for event in candidates if target.source_dataset and event.source_dataset == target.source_dataset]
    if same_dataset:
        return same_dataset, "same_source_dataset"
    return list(candidates), "all_observations_no_source_match"


def span_parent_rule(context):
    spans = _span_index(context.events)
    for child in context.events:
        candidates = spans.get((child.trace_id, child.parent_span_id), ())
        parents, policy = _preferred_observations(candidates, child)
        for parent in parents:
            yield _edge(parent, child, RelationType.SPAN_PARENT, context, 1.0,
                        f"Destino declara parent no mesmo trace; matching={policy}; "
                        f"observações_candidatas={len(candidates)}.")


def span_link_rule(context):
    spans = _span_index(context.events)
    for target in context.events:
        for link in target.span_links:
            candidates = spans.get((link.get("trace_id"), link.get("span_id")), ())
            sources, policy = _preferred_observations(candidates, target)
            for source in sources:
                yield _edge(source, target, RelationType.SPAN_LINK, context, 0.90,
                            f"Span Link explícito; matching={policy}; "
                            f"observações_candidatas={len(candidates)}.")


def service_call_rule(context):
    events = {_ref(event): event for event in context.events}
    for parent in span_parent_rule(context):
        source, target = events[parent.source], events[parent.target]
        if source.service_name and target.service_name and source.service_name != target.service_name:
            yield _edge(source, target, RelationType.SERVICE_CALL, context, 0.95,
                        "Spans parent/child no mesmo trace atravessam serviços distintos.")


def temporal_rule(context):
    for source, target in _chains((context.events,)):
        distance = target.event_timestamp - source.event_timestamp
        if timedelta(0) <= distance <= context.temporal_window:
            confidence = max(0.05, 1 - distance.total_seconds() / max(1, context.temporal_window.total_seconds()))
            yield _edge(source, target, RelationType.TEMPORAL_PRECEDENCE, context, confidence,
                        "Source precede target; precedência temporal não prova causalidade.")


def same_correlation_rule(context):
    by_value = defaultdict(lambda: defaultdict(list))
    for identity in context.identity_evidence:
        if identity.identity_type == "correlation_id" and identity.event_reference in context.references:
            by_value[identity.identity_value][identity.event_reference].append(identity)
    for value, by_reference in sorted(by_value.items()):
        ordered = sorted(by_reference, key=lambda ref: (
            context.references[ref].event_timestamp, *reference_key(ref)
        ))
        for source_ref, target_ref in zip(ordered, ordered[1:]):
            source_evidence = max(by_reference[source_ref], key=lambda item: item.confidence)
            target_evidence = max(by_reference[target_ref], key=lambda item: item.confidence)
            confidence = min(source_evidence.confidence, target_evidence.confidence)
            explanation = (
                f"correlation_id compartilhado; source={source_evidence.origin}"
                f"({source_evidence.source or 'event'}), target={target_evidence.origin}"
                f"({target_evidence.source or 'event'}), confidence=min={confidence:.3f}."
            )
            yield _edge(context.references[source_ref], context.references[target_ref],
                        RelationType.SAME_CORRELATION, context, confidence, explanation)


def same_entity_rule(context):
    key = lambda event: ((event.business_entity_type, event.business_entity_id)
                         if event.business_entity_type and event.business_entity_id else None)
    for source, target in _chains(_groups(context.events, key)):
        yield _edge(source, target, RelationType.SAME_BUSINESS_ENTITY, context, 0.90,
                    "Eventos compartilham tipo e ID da entidade de negócio.")


def business_transition_rule(context):
    key = lambda event: ((event.business_entity_type, event.business_entity_id)
                         if event.signal_type is SignalType.BUSINESS_EVENT
                         and event.business_entity_type and event.business_entity_id else None)
    for source, target in _chains(_groups(context.events, key)):
        if context.business_policy.allows(source.event_name, target.event_name):
            yield _edge(source, target, RelationType.BUSINESS_TRANSITION, context, 0.95,
                        f"Transição validada por {context.business_policy.version}: "
                        f"{source.event_name} -> {target.event_name}.", context.business_policy.version)


def co_anomaly_rule(context):
    anomalies = tuple(event for event in context.events
                      if context.anomaly_policy.is_anomalous(event))
    for source, target in _chains((anomalies,)):
        if target.event_timestamp - source.event_timestamp <= context.temporal_window:
            yield _edge(source, target, RelationType.CO_ANOMALY, context, 0.60,
                        "Anomalias coocorrem na janela; coocorrência não estabelece causalidade.")


def _semantic_supports(source, target, context):
    source_identities = [item for item in context.identity_evidence
                         if item.event_reference == _ref(source) and item.identity_type == "correlation_id"]
    target_identities = [item for item in context.identity_evidence
                         if item.event_reference == _ref(target) and item.identity_type == "correlation_id"]
    matches = [
        (left, right, min(left.confidence, right.confidence))
        for left in source_identities for right in target_identities
        if left.identity_value == right.identity_value
    ]
    supports = []
    if matches:
        left, right, confidence = max(matches, key=lambda item: item[2])
        supports.append(TopologySupport(
            "semantic_identity", confidence, (_ref(source), _ref(target)),
            f"correlation compartilhada; source={left.origin}:{left.source or 'event'}; "
            f"target={right.origin}:{right.source or 'event'}; confidence=min={confidence:.3f}",
        ))
    same_entity = bool(source.business_entity_type and source.business_entity_id
                       and (source.business_entity_type, source.business_entity_id)
                       == (target.business_entity_type, target.business_entity_id))
    if same_entity:
        supports.append(TopologySupport(
            "semantic_entity", 0.90, (_ref(source), _ref(target)),
            "tipo e ID da entidade de negócio coincidem",
        ))
    return tuple(supports)


def _structural_support(source, target):
    shared = bool(source.trace_id and source.trace_id == target.trace_id and (
        target.parent_span_id == source.span_id or source.parent_span_id == target.span_id
    ))
    if not shared:
        return ()
    return (TopologySupport(
        "structural_relation", 1.0, (_ref(source), _ref(target)),
        "relação parent/span observada no mesmo trace",
    ),)


def _topology_supports(source, target, context):
    supports = []
    if context.anomaly_policy.is_anomalous(target):
        supports.append(TopologySupport(
            "target_anomalous", 1.0, (_ref(target),),
            "destino classificado como anômalo por multimodal-anomaly-v1",
        ))
    supports.extend(_semantic_supports(source, target, context))
    supports.extend(_structural_support(source, target))
    if context.topology_policy.allow_healthy_prior:
        supports.append(TopologySupport(
            "configured_healthy_prior", context.topology_policy.healthy_prior_confidence,
            (_ref(source), _ref(target)), "prior saudável habilitado explicitamente",
        ))
    return tuple(supports)


def topology_rule(context):
    by_service = defaultdict(list)
    for event in context.events:
        if event.service_name:
            by_service[event.service_name].append(event)
    for source_service, targets in sorted(context.topology.items()):
        for target_service in targets:
            for source in by_service[source_service]:
                choices = [event for event in by_service[target_service]
                           if timedelta(0) <= event.event_timestamp - source.event_timestamp <= context.temporal_window]
                if not choices:
                    continue
                supported = [(target, _topology_supports(source, target, context)) for target in choices]
                supported = [(target, supports) for target, supports in supported if supports]
                if supported:
                    target, supports = min(
                        supported, key=lambda item: (item[0].event_timestamp, *reference_key(_ref(item[0])))
                    )
                    strongest = max(support.confidence for support in supports)
                    confidence = min(context.topology_policy.configured_topology_weight, strongest)
                    evidence = (
                        _ref(source), _ref(target),
                        *(reference for support in supports for reference in support.evidence_references),
                    )
                    support_text = "; ".join(
                        f"{support.support_type}[{support.confidence:.3f}]:{support.explanation}"
                        for support in supports
                    )
                    yield _edge(source, target, RelationType.TOPOLOGY_DEPENDENCY, context, confidence,
                                f"Topologia {context.topology_policy.topology_version}, direção "
                                f"{source_service}->{target_service}, suportes={support_text}; "
                                f"confidence=min({context.topology_policy.configured_topology_weight:.3f},"
                                f"{strongest:.3f})={confidence:.3f}; "
                                "é candidato causal, não causa comprovada.",
                                context.topology_policy.topology_version, evidence)


class EdgeRuleRegistry:
    def __init__(self):
        self._rules: dict[RelationType, tuple[str, EdgeRule]] = {}

    def register(self, relation_type: RelationType, rule: EdgeRule, version: str = "v1") -> None:
        if relation_type in self._rules:
            raise ValueError(f"Regra já registrada: {relation_type.value}.")
        self._rules[relation_type] = (version, rule)

    def rules(self):
        return tuple((kind, *self._rules[kind]) for kind in sorted(self._rules, key=lambda item: item.value))

    def to_dict(self):
        return {kind.value: {"version": version,
                             "rule": f"{rule.__module__}.{rule.__qualname__}"}
                for kind, version, rule in self.rules()}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def defaults(cls):
        registry = cls()
        for kind, rule in (
            (RelationType.SPAN_PARENT, span_parent_rule), (RelationType.SPAN_LINK, span_link_rule),
            (RelationType.SERVICE_CALL, service_call_rule), (RelationType.TEMPORAL_PRECEDENCE, temporal_rule),
            (RelationType.SAME_CORRELATION, same_correlation_rule),
            (RelationType.SAME_BUSINESS_ENTITY, same_entity_rule),
            (RelationType.BUSINESS_TRANSITION, business_transition_rule),
            (RelationType.CO_ANOMALY, co_anomaly_rule),
            (RelationType.TOPOLOGY_DEPENDENCY, topology_rule),
        ):
            registry.register(kind, rule)
        return registry
