from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from experiments.final_campaigns_v3.preprocessing import (
    FrozenEvidenceLoader, IncidentSource, _atomic_json, _baseline_contribution,
    _baseline_finalizer_payload, _baseline_from_training_incidents,
    _checkpoint_name, _source_cache_identity, audit_existing_artifacts,
)
from experiments.rcaeval_official_v3 import CompressedNormalizedEventCache
from experiments.multimodal_v3.core import sha256_value
from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.evidence import EventReference
from multimodal_v1.event_graph import EdgeRuleRegistry, EventGraphBuilder, RelationType
from multimodal_v1.event_graph.rules import (
    _edge, _ref, _topology_supports, business_transition_rule, co_anomaly_rule,
    same_correlation_rule, same_entity_rule, service_call_rule, span_link_rule,
    span_parent_rule, temporal_rule,
)
from multimodal_v1.features_v3 import FeatureRegistryV3, RCAFeatureExtractorV3


BASE = datetime(2026, 8, 18, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def event(number, *, seconds=0, service="source", severity=None,
          correlation_id=None, trace_id=None, span_id=None, parent_span_id=None):
    return UnifiedEvent(
        event_id=f"event-{number:05d}", event_name="operation", event_type="log",
        signal_type="log", event_timestamp=BASE + timedelta(seconds=seconds),
        service_name=service, severity=severity, correlation_id=correlation_id,
        trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id,
        source_reference=f"fixture:{number:05d}", source_dataset="fixture",
        attributes={"message": "fixture"},
    )


def bundle(events):
    result = IncidentEvidenceAssembler().assemble(tuple(events))
    assert len(result) == 1
    return result[0]


def legacy_topology_rule(context):
    by_service = {}
    for item in context.events:
        if item.service_name:
            by_service.setdefault(item.service_name, []).append(item)
    for source_service, targets in sorted(context.topology.items()):
        for target_service in targets:
            for source in by_service.get(source_service, ()):
                choices = [item for item in by_service.get(target_service, ())
                           if timedelta(0) <= item.event_timestamp - source.event_timestamp
                           <= context.temporal_window]
                supported = [(target, _topology_supports(source, target, context))
                             for target in choices]
                supported = [(target, supports) for target, supports in supported if supports]
                if supported:
                    target, supports = min(supported, key=lambda item: (
                        item[0].event_timestamp, item[0].event_id,
                        item[0].source_reference or ""))
                    strongest = max(support.confidence for support in supports)
                    confidence = min(context.topology_policy.configured_topology_weight,
                                     strongest)
                    evidence = (
                        _ref(source, context), _ref(target, context),
                        *(reference for support in supports
                          for reference in support.evidence_references),
                    )
                    support_text = "; ".join(
                        f"{support.support_type}[{support.confidence:.3f}]:{support.explanation}"
                        for support in supports
                    )
                    yield _edge(
                        source, target, RelationType.TOPOLOGY_DEPENDENCY, context,
                        confidence,
                        f"Topologia {context.topology_policy.topology_version}, direção "
                        f"{source_service}->{target_service}, suportes={support_text}; "
                        f"confidence=min({context.topology_policy.configured_topology_weight:.3f},"
                        f"{strongest:.3f})={confidence:.3f}; "
                        "é candidato causal, não causa comprovada.",
                        context.topology_policy.topology_version, evidence,
                    )


# Keep policy provenance identical; only the implementation strategy differs.
legacy_topology_rule.__name__ = "topology_rule"
legacy_topology_rule.__qualname__ = "topology_rule"
legacy_topology_rule.__module__ = "multimodal_v1.event_graph.rules"


def legacy_registry():
    registry = EdgeRuleRegistry()
    for kind, rule in (
        (RelationType.SPAN_PARENT, span_parent_rule),
        (RelationType.SPAN_LINK, span_link_rule),
        (RelationType.SERVICE_CALL, service_call_rule),
        (RelationType.TEMPORAL_PRECEDENCE, temporal_rule),
        (RelationType.SAME_CORRELATION, same_correlation_rule),
        (RelationType.SAME_BUSINESS_ENTITY, same_entity_rule),
        (RelationType.BUSINESS_TRANSITION, business_transition_rule),
        (RelationType.CO_ANOMALY, co_anomaly_rule),
        (RelationType.TOPOLOGY_DEPENDENCY, legacy_topology_rule),
    ):
        registry.register(kind, rule)
    return registry


def test_indexed_topology_is_byte_equivalent_to_exhaustive_rule():
    trace = "a" * 32
    values = (
        event(1, service="source", correlation_id="workflow", trace_id=trace,
              span_id="1" * 16),
        event(2, seconds=1, service="target", correlation_id="workflow"),
        event(3, seconds=1, service="target", severity="ERROR"),
        event(4, seconds=299, service="target", trace_id=trace, span_id="2" * 16,
              parent_span_id="1" * 16),
        event(5, seconds=400, service="target", severity="ERROR"),
    )
    evidence = bundle(values)
    topology = {"source": ["target"]}
    optimized = EventGraphBuilder().build(evidence, topology=topology)
    exhaustive = EventGraphBuilder(legacy_registry()).build(evidence, topology=topology)
    assert optimized.to_json() == exhaustive.to_json()
    assert optimized.nodes == exhaustive.nodes
    assert optimized.edges == exhaustive.edges
    assert optimized.service_nodes == exhaustive.service_nodes
    assert optimized.rule_versions == exhaustive.rule_versions
    assert optimized.graph_fingerprint == exhaustive.graph_fingerprint


def test_streaming_graph_fingerprint_equals_public_to_json_contract():
    graph = EventGraphBuilder().build(bundle((event(1), event(2, seconds=1))))
    expected = hashlib.sha256(graph.to_json().encode("utf-8")).hexdigest()
    assert graph.graph_fingerprint == expected
    assert graph.graph_fingerprint == expected  # cached path
    graph._bind_event_fingerprints({
        node.reference: node.event.contract_fingerprint for node in graph.nodes
    })
    assert graph.graph_fingerprint == expected


def test_large_topology_uses_index_probes_not_source_times_target_comparisons():
    count = 600
    values = tuple(event(index, seconds=index, service="source")
                   for index in range(count)) + tuple(
        event(count + index, seconds=index, service="target") for index in range(count)
    )
    evidence = bundle(values)
    operations = {}
    graph, profile = EventGraphBuilder().build_profiled(
        evidence, topology={"source": ["target"]}, operation_counts=operations)
    assert not [edge for edge in graph.edges
                if edge.relation_type is RelationType.TOPOLOGY_DEPENDENCY]
    assert operations.get("topology_candidate_comparisons", 0) == 0
    assert operations["topology_index_probes"] <= count * 4
    assert profile["edge_rules_edges"]["topology_rule"] == 0


def test_indexed_identity_free_boundary_matches_public_assembler_exactly():
    values = tuple(event(index, seconds=index // 4,
                         service="source" if index % 3 else "target")
                   for index in range(1, 101))
    source = IncidentSource(
        "rcaeval:fixture", "rcaeval", "fixture", Path("manifest.json"),
        "a" * 64, "source", ("log",),
    )
    assembler = IncidentEvidenceAssembler()
    public = assembler.assemble(values)
    assert len(public) == 1 and len(public[0].events) == len(values)
    indexed = FrozenEvidenceLoader._identity_free_indexed_bundle(
        source, values, IncidentEvidenceAssembler())
    assert indexed == replace(public[0], incident_id=source.incident_id)


def test_compact_authoritative_boundary_preserves_graph_and_features_exactly():
    values = (
        event(1, seconds=0, service="source"),
        event(2, seconds=1_000, service="target", severity="ERROR"),
    )
    source = IncidentSource(
        "fixture:compact", "fixture", "fixture", Path("manifest.json"),
        "a" * 64, "target", ("log",),
    )
    compact, mode = FrozenEvidenceLoader._bundle(source, values)
    assert mode == "authoritative-frozen-boundary"
    materialized = replace(
        compact,
        association_reasons=tuple(compact.association_reasons),
        association_scores=dict(compact.association_scores),
        provenance=tuple(compact.provenance),
    )
    assert tuple(compact.association_reasons) == materialized.association_reasons
    assert dict(compact.association_scores) == dict(materialized.association_scores)
    assert tuple(compact.provenance) == materialized.provenance

    compact_graph = EventGraphBuilder().build(compact)
    materialized_graph = EventGraphBuilder().build(materialized)
    assert compact_graph.to_json() == materialized_graph.to_json()
    assert compact_graph.graph_fingerprint == materialized_graph.graph_fingerprint
    extractor = RCAFeatureExtractorV3()
    assert extractor.extract(compact, compact_graph) == extractor.extract(
        materialized, materialized_graph)


def test_optimized_parquet_loader_is_identical_to_frozen_cache_loader(tmp_path):
    cache = CompressedNormalizedEventCache(tmp_path)
    values = (event(1), event(2, seconds=1, severity="ERROR"))
    source_hashes = {"fixture.parquet": "a" * 64}
    key = "b" * 64
    cache.store(key=key, adapter_version="fixture-v1",
                configuration_sha256="c" * 64,
                source_files_sha256=source_hashes, events=values)
    manifest = {"cache_key": key, "adapter_version": "fixture-v1",
                "adapter_configuration_sha256": "c" * 64,
                "source_files_sha256": source_hashes}
    loader = object.__new__(FrozenEvidenceLoader)
    loader.rcaeval_cache = cache
    optimized = loader._rcaeval_events(manifest, profile=None)
    frozen = cache.load(key=key, adapter_version="fixture-v1",
                        configuration_sha256="c" * 64,
                        source_files_sha256=source_hashes)
    assert optimized == frozen == values
    fingerprints = loader.event_fingerprints(optimized)
    assert fingerprints == {
        EventReference(item.event_id, item.source_reference): item.contract_fingerprint
        for item in values
    }


def test_existing_seven_golden_artifacts_are_valid_and_untouched():
    root = ROOT / "artifacts/final-experiments-v3/scientific-inputs"
    report = audit_existing_artifacts(root)
    assert report["valid"] == 7
    assert report["invalid"] == 0


def test_p3_checkpoint_finalizer_matches_p2_baseline_exactly_multi_incident(tmp_path):
    """Event order is deliberately non-lexical; P3 must preserve P2 semantics."""
    sources = {
        name: IncidentSource(name, "fixture", "system", tmp_path / f"{name}.json",
                             hashlib.sha256(name.encode()).hexdigest(), "source", ("log",))
        for name in ("incident-z", "incident-a")
    }
    for source in sources.values():
        source.manifest_path.write_text(json.dumps({"source_files_sha256": {"events": source.manifest_sha256}}))
    values = {
        # Raw order differs from temporal order.  P2 observes bundle.events,
        # whereas P3 receives these event tuples directly.
        "incident-z": (
            replace(event(9, seconds=4, service="z"), event_type="metric", signal_type=SignalType.METRIC,
                    metric_name="system.cpu.utilization", metric_value=91.0),
            replace(event(1, seconds=1, service="a"), event_type="trace", signal_type=SignalType.TRACE,
                    attributes={"duration_ms": 11.0}),
            event(3, seconds=2, service="a"),
        ),
        "incident-a": (
            replace(event(8, seconds=3, service="z"), event_type="metric", signal_type=SignalType.METRIC,
                    metric_name="system.cpu.utilization", metric_value=83.0),
            replace(event(2, seconds=2, service="a"), event_type="trace", signal_type=SignalType.TRACE,
                    attributes={"duration_ms": 22.0}),
            event(4, seconds=1, service="a"),
        ),
    }

    class Loader:
        _last_event_fingerprints = []
        def events(self, source):
            return values[source.incident_id]

    extractor = RCAFeatureExtractorV3()
    p2 = _baseline_from_training_incidents(Loader(), sources, sources, extractor)
    root = tmp_path / "contributions"
    for incident_id in sorted(sources):
        contribution = _baseline_contribution(values[incident_id], extractor)
        _atomic_json(root / f"{_checkpoint_name(incident_id)}.json", {
            "schema_version": "scientific-preprocessing-baseline-contribution-v1",
            "preprocessing_version": "scientific-input-preprocessing-v3", "base_key": "fixture",
            "incident_id": incident_id, "source_manifest_sha256": sources[incident_id].manifest_sha256,
            "source_cache_identity": _source_cache_identity(sources[incident_id]),
            "feature_registry_hash": FeatureRegistryV3().fingerprint,
            "status": "COMPLETED", "contribution": contribution,
            "contribution_sha256": sha256_value(contribution),
        })
    result = _baseline_finalizer_payload(
        contribution_root=root, sources=sources, train_incident_ids=sources, base_key="fixture",
        split_key="fixture-split", output=tmp_path / "snapshot.json")
    assert result["baseline_json"] == p2.to_json()
    assert result["baseline_sha256"] == sha256_value(json.loads(p2.to_json()))
    assert _baseline_from_training_incidents(Loader(), sources, sources, extractor).to_json() == p2.to_json()
    assert _source_cache_identity(sources["incident-a"]) != _source_cache_identity(sources["incident-z"])
