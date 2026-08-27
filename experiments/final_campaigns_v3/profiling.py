"""Reproducible, output-neutral profiling for scientific preprocessing v3."""

from __future__ import annotations

from collections import Counter
import gc
import json
from pathlib import Path
import resource
import shutil
import tempfile
import time
from typing import Any

from experiments.multimodal_v3.core import file_sha256
from multimodal_v1.event_graph import RelationType
from multimodal_v1.features_v3 import IncidentDatasetGeneratorV3, RCAFeatureExtractorV3
from multimodal_v1.features_v3.baselines import TrainingBaselineEstimatorV3

from .core import FrozenDatasetRegistry
from .preprocessing import FrozenEvidenceLoader, _atomic_json, _read_json


def standard_rcaeval_incidents(registry: FrozenDatasetRegistry) -> dict[str, dict[str, Any]]:
    frozen = registry.datasets["rcaeval"]
    values = []
    for manifest_path in (frozen.root / "manifests").glob("*.json"):
        manifest = _read_json(manifest_path)
        values.append((manifest["total_event_count"], manifest["incident_id"]))
    values.sort()
    requested = "rcaeval:re1ob_adservice_cpu_1"
    return {
        "small": {"incident_id": values[0][1], "events": values[0][0]},
        "medium": {"incident_id": values[len(values) // 2][1],
                   "events": values[len(values) // 2][0]},
        "required_large": {"incident_id": requested,
                           "events": next(count for count, item in values if item == requested)},
        "largest": {"incident_id": values[-1][1], "events": values[-1][0]},
    }


def profile_incident(*, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path,
                     incident_id: str, output: str | Path | None = None) -> dict[str, Any]:
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources[incident_id]
    total_started = time.perf_counter()
    events, load_profile = loader.events_profiled(source)
    feature_input, feature_profile = loader.feature_input_profiled(
        source, events, event_fingerprints=loader.event_fingerprints_aligned(events),
    )
    graph = feature_input.graph

    stage = time.perf_counter()
    baseline = TrainingBaselineEstimatorV3().fit(
        (feature_input.bundle,), training_incident_ids=(incident_id,),
    )
    baseline_seconds = time.perf_counter() - stage

    stage = time.perf_counter()
    vectors = RCAFeatureExtractorV3().extract(
        feature_input.bundle, graph, baselines=baseline,
    )
    extractor_seconds = time.perf_counter() - stage

    stage = time.perf_counter()
    dataset = IncidentDatasetGeneratorV3(seed=42).generate((feature_input,))
    generator_seconds = time.perf_counter() - stage

    relation_counts = Counter(edge.relation_type.value for edge in graph.edges)
    report = {
        "schema_version": "scientific-preprocessing-profile-v1",
        "incident_id": incident_id,
        "events": len(events),
        "events_load_seconds": load_profile["total_seconds"],
        "events_load_stages": load_profile,
        "bundle_aggregation_seconds": feature_profile["bundle_aggregation_seconds"],
        "boundary_mode": feature_profile["boundary_mode"],
        "graph_build_seconds": feature_profile["graph"]["total_seconds"],
        "graph_build_stages": feature_profile["graph"],
        "feature_extract_seconds": extractor_seconds,
        "training_baseline_seconds": baseline_seconds,
        "dataset_generate_seconds": generator_seconds,
        "total_seconds": time.perf_counter() - total_started,
        "graph_nodes": len(graph.nodes),
        "graph_edges": len(graph.edges),
        "edge_count_by_relation_type": {
            relation.value: relation_counts.get(relation.value, 0)
            for relation in sorted(RelationType, key=lambda item: item.value)
        },
        "service_nodes": len(graph.service_nodes),
        "identity_evidence": len(feature_input.bundle.identity_evidence),
        "feature_vectors": len(vectors),
        "dataset_rows": len(dataset.rows),
        "graph_fingerprint": graph.graph_fingerprint,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }
    if output is not None:
        target = Path(output).resolve()
        if target.suffix != ".json":
            target = target / f"{incident_id.replace(':', '__')}.json"
        _atomic_json(target, report)
    return report


def diagnose_remote_cache(*, registry: FrozenDatasetRegistry,
                          rcaeval_cache: str | Path, incident_id: str,
                          output: str | Path | None = None) -> dict[str, Any]:
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources[incident_id]
    manifest = _read_json(source.manifest_path)
    remote_parquet = loader.rcaeval_cache.target(manifest["cache_key"])
    remote_metadata = loader.rcaeval_cache.metadata_target(manifest["cache_key"])
    with tempfile.TemporaryDirectory(prefix="causalods-rcaeval-stage-") as temporary:
        stage_root = Path(temporary)
        stage = time.perf_counter()
        source_sha256 = file_sha256(remote_parquet)
        source_hash_seconds = time.perf_counter() - stage
        stage = time.perf_counter()
        local_parquet = stage_root / remote_parquet.name
        local_metadata = stage_root / remote_metadata.name
        shutil.copyfile(remote_parquet, local_parquet)
        shutil.copyfile(remote_metadata, local_metadata)
        copy_seconds = time.perf_counter() - stage
        stage = time.perf_counter()
        staged_sha256 = file_sha256(local_parquet)
        staged_hash_seconds = time.perf_counter() - stage
        local_parquet.chmod(0o444)
        local_metadata.chmod(0o444)
        if source_sha256 != staged_sha256:
            raise RuntimeError("RCAEVAL_STAGING_HASH_MISMATCH")

        direct_events, direct_profile = loader.events_profiled(source)
        direct_count = len(direct_events)
        del direct_events
        gc.collect()
        staged_loader = FrozenEvidenceLoader(registry, rcaeval_cache=stage_root)
        staged_events, staged_profile = staged_loader.events_profiled(source)
        staged_count = len(staged_events)
        del staged_events
        gc.collect()
        if direct_count != staged_count:
            raise RuntimeError("RCAEVAL_STAGING_EVENT_COUNT_MISMATCH")
        report = {
            "schema_version": "rcaeval-cache-staging-diagnostic-v1",
            "incident_id": incident_id, "event_count": direct_count,
            "source_path": str(remote_parquet), "source_sha256": source_sha256,
            "staged_sha256": staged_sha256, "checksum_match": True,
            "source_hash_seconds": source_hash_seconds,
            "copy_seconds": copy_seconds, "staged_hash_seconds": staged_hash_seconds,
            "mounted_load_seconds": direct_profile["total_seconds"],
            "local_load_seconds": staged_profile["total_seconds"],
            "mounted_load_profile": direct_profile,
            "local_load_profile": staged_profile,
            "staging_permissions": "0444",
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        }
    if output is not None:
        _atomic_json(Path(output).resolve(), report)
    return report


def print_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, sort_keys=True, separators=(",", ":")), flush=True)
