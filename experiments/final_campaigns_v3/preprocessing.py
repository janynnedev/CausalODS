"""Planner-bound scientific input materialization for the final v3 campaigns.

This is an additive experimental boundary.  It reads frozen manifests/events,
delegates feature construction to rca-features-v3, and never writes below a
frozen dataset root.  RCAEval is deliberately cache-only: rebuilding its
archived normalized cache is outside Prompt 13C.1.
"""

from __future__ import annotations

import gzip
import gc
import hashlib
from collections import OrderedDict
from bisect import bisect_left, insort
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, fields, replace
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from experiments.multimodal_v3.core import canonical_json, file_sha256, sha256_value
from experiments.rcaeval_official_v3 import CompressedNormalizedEventCache
from multimodal_v1.benchmark_v3 import BenchmarkDataset
from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence import (
    AnalysisWindow, EventAssociation, EventReference, IncidentEvidenceAssembler,
    IncidentEvidenceBundle, ProvenanceRecord,
)
from multimodal_v1.event_graph import EventGraphBuilder
from multimodal_v1.features_v3 import (
    BaselineSnapshotV3, BaselineStatistic,
    DatasetRowV3, FeatureGroup, FeatureInputV3, FeatureRegistryV3,
    IncidentDatasetGeneratorV3, LabelStatus, MetricIdentityV3, MetricSelectionV3,
    PolicyProvenanceV3, RCAFeatureDatasetV3, RCAFeatureExtractorV3,
    RCAFeatureVectorViewV3, RCAServiceTargetV3,
)
from multimodal_v1.features_v3.dataset import baseline_binding_sha256
from multimodal_v1.features_v3.baselines import event_latency_ms, log_template, metric_kind

from .core import (
    FORBIDDEN_SCIENTIFIC_INPUT_KEYS, H1B_VIEWS, H1C_VIEWS,
    FrozenDataset, FrozenDatasetRegistry, build_experiment_config, load_campaign_config,
)


PREPROCESSING_VERSION = "scientific-input-preprocessing-v3"
WORKER_DIAGNOSTIC_TAIL_BYTES = 8192
HEAVY_RESOURCE_GATE_VERSION = "heavy-resource-gate-v1"
CATALOG_VERSION = "scientific-benchmark-catalog-v3"
VIEW_GROUPS = {
    "FULL": None,
    "NO_GRAPH": frozenset({FeatureGroup.GRAPH}),
    "GRAPH": frozenset({FeatureGroup.BUSINESS}),
    "GRAPH_BUSINESS": frozenset(),
    "METRIC_ONLY": frozenset(set(FeatureGroup) - {FeatureGroup.METRIC, FeatureGroup.QUALITY}),
    "TRACE_ONLY": frozenset(set(FeatureGroup) - {FeatureGroup.TRACE, FeatureGroup.QUALITY}),
    "METRIC_TRACE": frozenset(set(FeatureGroup) - {
        FeatureGroup.METRIC, FeatureGroup.TRACE, FeatureGroup.QUALITY}),
    "METRIC_TRACE_LOG": frozenset(set(FeatureGroup) - {
        FeatureGroup.METRIC, FeatureGroup.TRACE, FeatureGroup.LOG, FeatureGroup.QUALITY}),
    "METRIC_TRACE_LOG_BUSINESS": frozenset(set(FeatureGroup) - {
        FeatureGroup.METRIC, FeatureGroup.TRACE, FeatureGroup.LOG,
        FeatureGroup.BUSINESS, FeatureGroup.QUALITY}),
}


@dataclass(frozen=True)
class PreprocessingBinding:
    """One frozen planner fold resolved for an incident in any split."""

    assignment: Any
    splits: Mapping[str, tuple[str, ...]]
    split_key: str
    base_key: str
    incident_split: str
    incident_ids: tuple[str, ...]


class WorkerExecutionError(RuntimeError):
    """A subprocess failure whose complete bounded diagnostic must survive."""

    def __init__(self, diagnostic: Mapping[str, Any]):
        self.diagnostic = dict(diagnostic)
        kind = self.diagnostic.get("worker_kind", "UNKNOWN")
        incident = self.diagnostic.get("incident_id") or "none"
        location = self.diagnostic.get("diagnostic_state") or "state.json"
        super().__init__(f"{kind}_WORKER_FAILED:{incident}:diagnostic={location}")


def _diagnostic_tail(value: str | None) -> str:
    return (value or "")[-WORKER_DIAGNOSTIC_TAIL_BYTES:]


def _failed_worker_state(*, phase: str, worker_kind: str,
                         base_key: str, split_key: str,
                         diagnostic: Mapping[str, Any],
                         state_path: Path) -> dict[str, Any]:
    payload = {
        "schema_version": "scientific-preprocessing-work-state-v2",
        "status": "FAILED_WORKER", "phase": phase,
        "worker_kind": worker_kind,
        "incident_id": diagnostic.get("incident_id"),
        "worker_returncode": diagnostic.get("worker_returncode"),
        "signal": diagnostic.get("signal"),
        "stdout_tail": _diagnostic_tail(diagnostic.get("stdout") or diagnostic.get("stdout_tail")),
        "stderr_tail": _diagnostic_tail(diagnostic.get("stderr") or diagnostic.get("stderr_tail")),
        "command": diagnostic.get("command"),
        "worker_spec": diagnostic.get("worker_spec"),
        "base_key": base_key, "split_key": split_key,
        "parent_pid": os.getpid(),
    }
    _atomic_json(state_path, payload)
    return payload


def resolve_preprocessing_binding(*, registry: FrozenDatasetRegistry,
                                  experiment_planner: Any,
                                  sources: Mapping[str, Any], protocol: str,
                                  seed: int, incident_id: str,
                                  held_out: str | None = None) -> PreprocessingBinding:
    """Resolve the planner-bound fold without requiring the incident to be test.

    ``protocol_assignments`` intentionally emits one assignment per test
    incident.  The preprocessing binding, however, belongs to the complete
    train/validation/test fold.  Deduplication by that frozen fold signature
    preserves planner semantics while allowing workers for every split.
    """
    assignments, _ = experiment_planner.protocol_assignments(protocol, seed)
    unique: dict[tuple[Any, ...], Any] = {}
    for item in assignments:
        if not item.applicable:
            continue
        split_ids = (tuple(item.train_incident_ids),
                     tuple(item.validation_incident_ids),
                     tuple(item.test_incident_ids))
        if incident_id not in {value for values in split_ids for value in values}:
            continue
        if held_out is not None and item.held_out != held_out:
            continue
        signature = (*split_ids, item.held_out)
        unique.setdefault(signature, item)

    matches = list(unique.values())
    if not matches:
        raise ValueError(
            f"PREPROCESSING_BINDING_NOT_FOUND:{incident_id}:{protocol}:{seed}"
        )
    if len(matches) > 1:
        options = sorted({str(item.held_out) for item in matches})
        raise ValueError(
            f"PREPROCESSING_BINDING_AMBIGUOUS:{incident_id}:{protocol}:{seed}:"
            f"held_out_options={','.join(options)}"
        )

    assignment = matches[0]
    splits = {
        "train": tuple(assignment.train_incident_ids),
        "validation": tuple(assignment.validation_incident_ids),
        "test": tuple(assignment.test_incident_ids),
    }
    incident_split = next(
        name for name, values in splits.items() if incident_id in values
    )
    split_key, base_key, incident_ids = build_preprocessing_keys(
        registry=registry, sources=sources, splits=splits, protocol=protocol,
        held_out=assignment.held_out, seed=seed,
    )
    return PreprocessingBinding(
        assignment=assignment, splits=splits, split_key=split_key,
        base_key=base_key, incident_split=incident_split,
        incident_ids=incident_ids,
    )


def preprocessing_key_preimage(*, registry: FrozenDatasetRegistry,
                               sources: Mapping[str, Any],
                               splits: Mapping[str, Iterable[str]],
                               protocol: str, held_out: str | None, seed: int,
                               registry_sha256: str | None = None,
                               ) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return the complete deterministic base-key preimage for forensic use."""
    split_key = sha256_value({name: sorted(values) for name, values in splits.items()})
    incident_ids = tuple(sorted({value for values in splits.values() for value in values}))
    manifest_hashes = sorted(sources[value].manifest_sha256 for value in incident_ids)
    return ({
        "dataset_fingerprints": (registry_sha256 if registry_sha256 is not None
                                 else registry.snapshot()["registry_sha256"]),
        "manifest_hashes": manifest_hashes,
        "protocol": protocol,
        "held_out": held_out,
        "seed": seed,
        "split_hash": split_key,
        "feature_registry_hash": FeatureRegistryV3().fingerprint,
        "preprocessing_version": PREPROCESSING_VERSION,
    }, incident_ids)


def build_preprocessing_keys(*, registry: FrozenDatasetRegistry,
                             sources: Mapping[str, Any],
                             splits: Mapping[str, Iterable[str]],
                             protocol: str, held_out: str | None,
                             seed: int, registry_sha256: str | None = None,
                             ) -> tuple[str, str, tuple[str, ...]]:
    """Canonical frozen split/base key formula shared by CLI and materialize."""
    preimage, incident_ids = preprocessing_key_preimage(
        registry=registry, sources=sources, splits=splits, protocol=protocol,
        held_out=held_out, seed=seed, registry_sha256=registry_sha256)
    split_key = preimage["split_hash"]
    base_key = sha256_value(preimage)
    return split_key, base_key, incident_ids


def effective_split_sha256(splits: Mapping[str, Iterable[str]]) -> str:
    """Canonical identity of actual train/validation/test membership.

    This is intentionally distinct from the parent planner split and from a
    scientific tranche selection-manifest fingerprint.  Operational tranche
    checkpoints retain their historical selection-SHA binding separately.
    """
    return sha256_value({name: sorted(splits[name])
                         for name in ("train", "validation", "test")})


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON científico inválido: {path}") from exc


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _record_finalization_phase(path: Path, phase: str, *, started: float,
                               view: str | None = None,
                               cache_key: str | None = None) -> None:
    """Persist bounded parent-process telemetry outside scientific hashes."""
    current_rss_kb = None
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                current_rss_kb = int(line.split()[1])
                break
    except (OSError, ValueError):
        pass
    events = []
    if path.is_file():
        try:
            prior = _read_json(path)
            if isinstance(prior, dict) and isinstance(prior.get("events"), list):
                events = prior["events"][-255:]
        except ValueError:
            events = []
    events.append({
        "phase": phase, "elapsed_seconds": time.perf_counter() - started,
        "rss_kb": current_rss_kb,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "view": view, "cache_key": cache_key,
    })
    _atomic_json(path, {
        "schema_version": "tranche-finalization-telemetry-v1",
        "status": "IN_PROGRESS", "events": events,
    })


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _sha_ids(values: Iterable[str]) -> str:
    return sha256_value(sorted(values))


def _feature_view(view: str):
    if view not in VIEW_GROUPS:
        raise ValueError(f"Feature view desconhecida: {view}")
    registry = FeatureRegistryV3()
    excluded = VIEW_GROUPS[view]
    return registry.select(exclude_groups=excluded or ())


def model_specification_hash(path: str | Path) -> str:
    value = Path(path)
    payload = _read_json(value)
    required = {
        "version", "benchmark_version", "feature_registry_version",
        "feature_registry_sha256", "method_catalogue", "tuning_policy",
        "tuning_budget", "bootstrap_iterations", "split_policy", "seed_policy",
        "preprocessing_version",
    }
    if not required <= set(payload):
        raise ValueError("Scientific model specification incompleta.")
    if payload["feature_registry_sha256"] != FeatureRegistryV3().fingerprint:
        raise ValueError("Scientific model specification diverge do FeatureRegistryV3.")
    if payload["preprocessing_version"] != PREPROCESSING_VERSION:
        raise ValueError("Scientific model specification diverge do preprocessing.")
    return file_sha256(value)


class _PlannerBoundGenerator(IncidentDatasetGeneratorV3):
    """Frozen generator with its split delegated to the frozen planner."""

    def __init__(self, splits: Mapping[str, Iterable[str]], *, seed: int):
        super().__init__(seed=seed)
        self._splits = {name: tuple(sorted(values)) for name, values in splits.items()}

    def split(self, incident_ids, **_ignored):
        observed = set(incident_ids)
        planned = {item for values in self._splits.values() for item in values}
        if observed != planned:
            raise ValueError("SCIENTIFIC_SPLIT_BINDING_FAIL: incident set diverge do planner.")
        return self._splits


@dataclass
class FeatureInputLifecycleCounter:
    live: int = 0
    max_live: int = 0
    created: int = 0

    def enter(self) -> None:
        self.live += 1
        self.created += 1
        self.max_live = max(self.max_live, self.live)

    def exit(self) -> None:
        self.live -= 1
        if self.live < 0:
            raise RuntimeError("FEATURE_INPUT_LIFECYCLE_UNDERFLOW")


@dataclass
class _ScientificDatasetBuild:
    dataset: RCAFeatureDatasetV3
    lifecycle: FeatureInputLifecycleCounter
    checkpoint_report: Mapping[str, Any] | None = None


def _full_view_vector(vector, view) -> RCAFeatureVectorViewV3:
    return RCAFeatureVectorViewV3(
        vector.incident_id, vector.service_name,
        {name: vector.values[name] for name in view.names}, view.names,
        view.fingerprint, view.ablation_id,
    )


def _baseline_from_json(value: str | Mapping[str, Any]) -> BaselineSnapshotV3:
    payload = json.loads(value) if isinstance(value, str) else dict(value)
    statistics = {
        key: BaselineStatistic(
            item["mean"], item["stddev"], item["count"],
            item.get("zero_variance_saturation", 12.0),
            item.get("policy_version", "zscore-v3-zero-variance-isclose-1e-12-saturated-12"),
        )
        for key, item in payload["statistics"].items()
    }
    selections = {}
    for key, item in payload.get("metric_selections", {}).items():
        identity = MetricIdentityV3(
            item["service_name"], item["metric_kind"], item["metric_name"],
            item.get("metric_unit"), item.get("metric_aggregation"),
        )
        selections[key] = MetricSelectionV3(
            identity, item["sample_count"],
            item.get("policy", "max-normal-training-samples-then-lexicographic-v1"),
        )
    return BaselineSnapshotV3(
        statistics,
        {key: tuple(items) for key, items in payload["log_templates"].items()},
        tuple(payload["training_incident_ids"]),
        selections,
        payload.get("anomaly_policy_version", "multimodal-anomaly-v1"),
        payload.get("schema_version", "rca-feature-baselines-v3"),
    )


def _baseline_contribution(events: tuple[UnifiedEvent, ...],
                           extractor: RCAFeatureExtractorV3) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    metric_values: dict[str, dict[str, Any]] = {}
    templates: dict[str, set[str]] = {}
    for event in events:
        service = event.service_name
        if not service or extractor.anomaly_policy.is_anomalous(event):
            continue
        if event.signal_type is SignalType.TRACE:
            latency = event_latency_ms(event)
            if latency is not None:
                values.setdefault(f"service:{service}:latency", []).append(latency)
        elif event.signal_type is SignalType.METRIC and event.metric_value is not None:
            kind = metric_kind(event.metric_name)
            if kind:
                identity = MetricIdentityV3(
                    service, kind, event.metric_name, event.metric_unit,
                    event.metric_aggregation,
                )
                metric_values.setdefault(identity.key, {
                    "identity": {
                        "service_name": identity.service_name,
                        "metric_kind": identity.metric_kind,
                        "metric_name": identity.metric_name,
                        "metric_unit": identity.metric_unit,
                        "metric_aggregation": identity.metric_aggregation,
                    },
                    "values": [],
                })["values"].append(float(event.metric_value))
        elif event.signal_type is SignalType.LOG:
            template = log_template(event)
            if template:
                templates.setdefault(service, set()).add(template)
    return {
        "values": values,
        "metric_values": metric_values,
        "log_templates": {key: sorted(items) for key, items in templates.items()},
    }


def _baseline_from_contributions(
    contributions: Mapping[str, Mapping[str, Any]], train_incident_ids: Iterable[str],
    extractor: RCAFeatureExtractorV3,
) -> BaselineSnapshotV3:
    values: dict[str, list[float]] = {}
    metric_values: dict[MetricIdentityV3, list[float]] = {}
    templates: dict[str, set[str]] = {}
    for incident_id in sorted(train_incident_ids):
        contribution = contributions[incident_id]
        for key, items in contribution["values"].items():
            values.setdefault(key, []).extend(items)
        for item in contribution["metric_values"].values():
            raw = item["identity"]
            identity = MetricIdentityV3(
                raw["service_name"], raw["metric_kind"], raw["metric_name"],
                raw.get("metric_unit"), raw.get("metric_aggregation"),
            )
            metric_values.setdefault(identity, []).extend(item["values"])
        for service, items in contribution["log_templates"].items():
            templates.setdefault(service, set()).update(items)
    selections = {}
    identities_by_service_kind: dict[tuple[str, str], list[MetricIdentityV3]] = {}
    for identity in metric_values:
        identities_by_service_kind.setdefault((identity.service_name, identity.metric_kind), []).append(identity)
    for (service, kind), identities in identities_by_service_kind.items():
        selected = min(identities, key=lambda item: (-len(metric_values[item]), item.key))
        selections[f"{service}:{kind}"] = MetricSelectionV3(selected, len(metric_values[selected]))
        values[selected.key] = metric_values[selected]
    from statistics import fmean, pstdev
    statistics = {
        key: BaselineStatistic(fmean(items), pstdev(items), len(items))
        for key, items in values.items()
    }
    return BaselineSnapshotV3(
        statistics, {key: tuple(sorted(items)) for key, items in templates.items()},
        tuple(sorted(train_incident_ids)), selections, extractor.anomaly_policy.version,
    )


def _checkpoint_name(value: str) -> str:
    return sha256_value(value)


def _source_cache_identity(source: "IncidentSource") -> str:
    """Frozen identity of the normalized input, independent of its path."""
    manifest = _read_json(source.manifest_path)
    # RCAEval identifies its immutable normalized-cache entry explicitly.  Local
    # datasets use the same manifest/source-file hash contract.
    identity = {
        "dataset_id": source.dataset_id,
        "manifest_sha256": source.manifest_sha256,
        "cache_key": manifest.get("cache_key"),
        "adapter_version": manifest.get("adapter_version"),
        "adapter_configuration_sha256": manifest.get("adapter_configuration_sha256"),
        "source_files_sha256": manifest.get("source_files_sha256"),
        "normalized_source_files": manifest.get("normalized_source_files"),
    }
    return sha256_value(identity)


def _registry_from_snapshot(payload: Mapping[str, Any]) -> FrozenDatasetRegistry:
    datasets = []
    for item in payload["datasets"]:
        datasets.append(FrozenDataset(
            item["dataset_id"], item["kind"], Path(item["root"]),
            int(item["expected_incidents"]), int(item["expected_usable"]),
            Path(item["checksum_manifest"]), item["checksum_manifest_sha256"],
            Path(item["index"]), Path(item["capabilities"]),
            Path(item["materialization_report"]), bool(item.get("frozen", True)),
        ))
    roots = {Path(item["root"]).resolve() for item in payload["datasets"]}
    repository_root = Path.cwd().resolve()
    for root in roots:
        if repository_root in root.parents:
            continue
    return FrozenDatasetRegistry(repository_root, datasets)


def _checkpoint_bindings(*, base_key: str, incident_id: str, source: IncidentSource,
                         baseline_sha256: str, split_key: str) -> dict[str, Any]:
    return {
        "schema_version": "scientific-preprocessing-incident-checkpoint-v1",
        "preprocessing_version": PREPROCESSING_VERSION,
        "base_key": base_key,
        "incident_id": incident_id,
        "source_manifest_sha256": source.manifest_sha256,
        "source_cache_identity": _source_cache_identity(source),
        "source_dataset": source.dataset_id,
        "feature_registry_hash": FeatureRegistryV3().fingerprint,
        "baseline_sha256": baseline_sha256,
        "split_sha256": split_key,
    }


def _baseline_snapshot_bindings(*, base_key: str, split_key: str,
                                train_incident_ids: Iterable[str]) -> dict[str, Any]:
    return {
        "schema_version": "scientific-preprocessing-baseline-snapshot-v1",
        "preprocessing_version": PREPROCESSING_VERSION,
        "base_key": base_key,
        "split_sha256": split_key,
        "training_incident_ids": list(sorted(train_incident_ids)),
    }


def _load_valid_baseline_snapshot(path: Path, bindings: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        payload = _read_json(path)
        if any(payload.get(key) != value for key, value in bindings.items()):
            return None
        if payload.get("status") != "COMPLETED":
            return None
        baseline = _baseline_from_json(payload["baseline_json"])
        if sha256_value(json.loads(baseline.to_json())) != payload.get("baseline_sha256"):
            return None
        if list(baseline.training_incident_ids) != bindings["training_incident_ids"]:
            return None
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _load_worker_baseline(*, snapshot_path: str | Path, expected_sha256: str,
                          bindings: Mapping[str, Any]) -> BaselineSnapshotV3:
    """Load an immutable planner-bound baseline reference for one worker."""
    path = Path(snapshot_path)
    payload = _load_valid_baseline_snapshot(path, bindings)
    if payload is None:
        raise RuntimeError(f"INCIDENT_WORKER_BASELINE_REFERENCE_INVALID:{path}")
    if payload.get("baseline_sha256") != expected_sha256:
        raise RuntimeError("INCIDENT_WORKER_BASELINE_SHA256_MISMATCH")
    baseline_json = payload["baseline_json"]
    baseline = _baseline_from_json(baseline_json)
    # Release both the parsed snapshot mapping and its large JSON string before
    # the normalized event cache is opened.
    del baseline_json, payload
    gc.collect()
    return baseline


def _incident_scientific_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("schema_version", "preprocessing_version", "base_key", "incident_id",
            "source_manifest_sha256", "source_cache_identity", "source_dataset",
            "feature_registry_hash", "baseline_sha256", "split_sha256", "rows", "row_sha256",
            "graph_fingerprint", "topology_fingerprint", "relation_rule_versions",
            "graph_policy_provenance", "pruning_policy_version", "pruning_fingerprint")
    return {key: payload.get(key) for key in keys}


def _rows_from_payload(rows: Iterable[Mapping[str, Any]]) -> tuple[DatasetRowV3, ...]:
    result = []
    for row in rows:
        vector = row["vector"]
        target = row.get("target")
        feature_names = tuple(vector["feature_names"])
        # Canonical checkpoint JSON sorts mapping keys, while the feature-view
        # contract requires the declared registry order on reconstruction.
        features = {name: vector["features"][name] for name in feature_names}
        result.append(DatasetRowV3(
            row["split"],
            RCAFeatureVectorViewV3(
                vector["incident_id"], vector["service_name"], features,
                feature_names, vector["feature_fingerprint"],
                vector["ablation_id"],
            ),
            RCAServiceTargetV3(
                target["incident_id"], target["service_name"], target["is_root_cause"],
                target["label_status"], target["source"], target.get("root_cause_rank"),
                target.get("confidence"),
            ) if target else None,
        ))
    return tuple(result)


def _validate_incident_checkpoint(payload: Mapping[str, Any] | None,
                                  bindings: Mapping[str, Any]) -> tuple[bool, str | None]:
    if payload is None:
        return False, "MISSING_FILE"
    for key, expected in bindings.items():
        if payload.get(key) != expected:
            return False, f"BINDING_MISMATCH:{key}"
    if payload.get("status") != "COMPLETED":
        return False, "STATUS_NOT_COMPLETED"
    if payload.get("row_sha256") != sha256_value(payload.get("rows", [])):
        return False, "ROW_SHA256_MISMATCH"
    if payload.get("scientific_payload_sha256") != sha256_value(_incident_scientific_payload(payload)):
        return False, "SCIENTIFIC_PAYLOAD_SHA256_MISMATCH"
    try:
        _rows_from_payload(payload["rows"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"ROW_DESERIALIZATION_ERROR:{type(exc).__name__}:{exc}"
    return True, None


def _load_valid_incident_checkpoint(path: Path, bindings: Mapping[str, Any]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = _read_json(path)
        valid, _ = _validate_incident_checkpoint(payload, bindings)
        return payload if valid else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _incident_worker_payload(
    *, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path | None,
    incident_id: str, split: str, base_key: str, split_key: str,
    baseline_snapshot_path: str | Path, baseline_sha256: str,
    baseline_snapshot_bindings: Mapping[str, Any], source: IncidentSource, target: Path,
    phase_diagnostic_path: str | Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("CAUSALODS_TEST_FAIL_INCIDENT_WORKER") == incident_id:
        raise RuntimeError(f"TEST_INCIDENT_WORKER_FAILURE:{incident_id}")
    started = time.perf_counter()
    phase_started = started
    phase_telemetry = []
    diagnostic = Path(phase_diagnostic_path or target.with_suffix(".phases.json"))

    def record_phase(name: str) -> None:
        nonlocal phase_started
        now = time.perf_counter()
        current_rss_kb = None
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    current_rss_kb = int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):  # pragma: no cover - non-Linux host
            pass
        phase_telemetry.append({
            "phase": name,
            "elapsed_seconds": now - phase_started,
            "rss_kb": current_rss_kb,
            "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        })
        _atomic_json(diagnostic, {
            "schema_version": "incident-worker-phase-diagnostic-v1",
            "incident_id": incident_id, "status": "IN_PROGRESS",
            "current_phase": name, "phases": phase_telemetry,
        })
        phase_started = now

    baseline = _load_worker_baseline(
        snapshot_path=baseline_snapshot_path, expected_sha256=baseline_sha256,
        bindings=baseline_snapshot_bindings)
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources[incident_id]
    record_phase("CACHE_START")
    events = loader.events(source)
    record_phase("CACHE_DONE")
    event_fingerprints = loader.event_fingerprints_aligned(events)
    record_phase("FINGERPRINTS_DONE")
    bundle, _ = FrozenEvidenceLoader._bundle(source, events)
    record_phase("BUNDLE_DONE")
    graph = EventGraphBuilder().build(bundle)
    # IncidentGraphContext defensively copies arbitrary public mappings.  The
    # private frozen boundary is already immutable and is also retained by the
    # bundle, so discard that duplicate operational index after validation.
    if isinstance(bundle.association_scores, _FrozenBoundaryScores):
        object.__setattr__(graph.context, "association_scores",
                           bundle.association_scores)
        gc.collect()
    if event_fingerprints is not None:
        if any(node.event is not event for node, event in zip(graph.nodes, events)):
            event_fingerprints = {
                EventReference(event.event_id, event.source_reference): fingerprint.hex()
                for event, fingerprint in zip(events, event_fingerprints)
            }
        _bind_event_fingerprints(graph, event_fingerprints)
        del event_fingerprints
    record_phase("GRAPH_DONE")
    extractor = RCAFeatureExtractorV3()
    full_view = FeatureRegistryV3().select()
    rows = []
    positives = 0
    if source.root_cause_service is None:
        raise ValueError(f"HEALTHY_TOP1_TARGET_NOT_APPLICABLE:{incident_id}")
    for vector in extractor.extract(bundle, graph, baselines=baseline):
        target_row = RCAServiceTargetV3(
            vector.incident_id, vector.service_name,
            vector.service_name == source.root_cause_service, LabelStatus.LABELED,
            "frozen-ground-truth-evaluation-channel",
            root_cause_rank=1 if vector.service_name == source.root_cause_service else None,
        )
        positives += 1 if target_row.is_root_cause else 0
        rows.append(DatasetRowV3(split, _full_view_vector(vector, full_view), target_row))
    record_phase("FEATURES_DONE")
    if not positives:
        raise ValueError(f"GROUND_TRUTH_SERVICE_ABSENT_FROM_EVIDENCE:{incident_id}")
    rows_payload = [row.to_dict() for row in sorted(
        rows, key=lambda row: (row.split, row.vector.incident_id, row.vector.service_name))]
    bindings = _checkpoint_bindings(
        base_key=base_key, incident_id=incident_id, source=source,
        baseline_sha256=baseline_sha256, split_key=split_key)
    payload = {
        **bindings,
        "status": "COMPLETED",
        "rows": rows_payload,
        "row_sha256": sha256_value(rows_payload),
        "graph_fingerprint": graph.graph_fingerprint,
        "topology_fingerprint": sha256_value(None),
        "relation_rule_versions": {
            relation: list((value,) if isinstance(value, str) else tuple(value))
            for relation, value in graph.rule_versions.items()
        },
        "graph_policy_provenance": dict(graph.policy_provenance),
        "pruning_policy_version": "not-applied",
        "pruning_fingerprint": sha256_value("not-applied"),
        "worker": {
            "pid": os.getpid(),
            "elapsed_seconds": time.perf_counter() - started,
            "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "phase_telemetry": phase_telemetry,
            "after_cache_load_rss_kb": phase_telemetry[1]["rss_kb"],
            "after_event_fingerprints_rss_kb": phase_telemetry[2]["rss_kb"],
            "after_bundle_rss_kb": phase_telemetry[3]["rss_kb"],
            "after_graph_rss_kb": phase_telemetry[4]["rss_kb"],
            "after_feature_extraction_rss_kb": phase_telemetry[5]["rss_kb"],
            "phase_elapsed_seconds": {
                row["phase"]: row["elapsed_seconds"] for row in phase_telemetry
            },
        },
    }
    payload["scientific_payload_sha256"] = sha256_value(_incident_scientific_payload(payload))
    _atomic_json(target, payload)
    return payload


def _run_incident_worker(
    *, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path | None,
    incident_id: str, split: str, base_key: str, split_key: str,
    baseline_snapshot_path: str | Path, baseline_sha256: str,
    baseline_snapshot_bindings: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    spec = output.with_suffix(".worker.json")
    phases = output.with_suffix(".phases.json")
    _atomic_json(spec, {
        "registry_snapshot": registry.snapshot(),
        "rcaeval_cache": str(rcaeval_cache) if rcaeval_cache is not None else None,
        "incident_id": incident_id,
        "split": split,
        "base_key": base_key,
        "split_key": split_key,
        "baseline_snapshot_path": str(Path(baseline_snapshot_path).resolve()),
        "baseline_sha256": baseline_sha256,
        "baseline_snapshot_bindings": dict(baseline_snapshot_bindings),
        "phase_diagnostic_path": str(phases),
        "output": str(output),
    })
    args = [
        sys.executable, "-m", "experiments.final_campaigns_v3", "incident-worker",
        "--spec", str(spec),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        args, cwd=registry.repository_root,
        env={**os.environ, "PYTHONPATH": f"{registry.repository_root}:{registry.repository_root / 'correlation-engine'}"},
        text=True, capture_output=True,
    )
    signal_name = None
    if completed.returncode < 0:
        signal_name = signal.Signals(-completed.returncode).name
    if completed.returncode != 0:
        return {
            "status": "FAILED_WORKER", "incident_id": incident_id,
            "worker_kind": "FEATURE", "command": args,
            "worker_spec": str(spec),
            "phase_diagnostic": str(phases),
            "worker_returncode": completed.returncode, "signal": signal_name,
            "stdout": _diagnostic_tail(completed.stdout),
            "stderr": _diagnostic_tail(completed.stderr),
            "elapsed_seconds": time.perf_counter() - started,
        }
    payload = _read_json(output)
    if not isinstance(payload, dict) or payload.get("status") != "COMPLETED":
        raise ValueError(f"INCIDENT_WORKER_CHECKPOINT_INVALID:{output}")
    # Keep the diagnostic spec throughout subprocess execution and on every
    # failure path.  Only a successfully readable scientific checkpoint makes
    # the baseline-heavy incident spec redundant.  The checkpoint itself is
    # never touched here.
    spec.unlink(missing_ok=True)
    phases.unlink(missing_ok=True)
    payload.setdefault("worker", {})["returncode"] = completed.returncode
    return payload


def _baseline_from_training_incidents(
    loader: FrozenEvidenceLoader, sources: Mapping[str, IncidentSource],
    train_incident_ids: Iterable[str], extractor: RCAFeatureExtractorV3,
) -> BaselineSnapshotV3:
    allowed = tuple(sorted(train_incident_ids))
    if not allowed:
        raise ValueError("training_incident_ids não pode ser vazio.")
    values: dict[str, list[float]] = {}
    metric_values: dict[MetricIdentityV3, list[float]] = {}
    templates: dict[str, set[str]] = {}
    seen = set()
    try:
        for incident_id in allowed:
            source = sources[incident_id]
            events = loader.events(source)
            bundle, _ = FrozenEvidenceLoader._bundle(source, events)
            seen.add(bundle.incident_id)
            for event in bundle.events:
                service = event.service_name
                if not service or extractor.anomaly_policy.is_anomalous(event):
                    continue
                if event.signal_type is SignalType.TRACE:
                    latency = event_latency_ms(event)
                    if latency is not None:
                        values.setdefault(f"service:{service}:latency", []).append(latency)
                elif event.signal_type is SignalType.METRIC and event.metric_value is not None:
                    kind = metric_kind(event.metric_name)
                    if kind:
                        identity = MetricIdentityV3(
                            service, kind, event.metric_name, event.metric_unit,
                            event.metric_aggregation,
                        )
                        metric_values.setdefault(identity, []).append(float(event.metric_value))
                elif event.signal_type is SignalType.LOG:
                    template = log_template(event)
                    if template:
                        templates.setdefault(service, set()).add(template)
            del bundle
            del events
            gc.collect()
    finally:
        loader._last_event_fingerprints = []
    missing = set(allowed) - seen
    if missing:
        raise ValueError(f"Incidentes de treino ausentes da entrada: {sorted(missing)!r}.")
    selections = {}
    identities_by_service_kind: dict[tuple[str, str], list[MetricIdentityV3]] = {}
    for identity in metric_values:
        identities_by_service_kind.setdefault((identity.service_name, identity.metric_kind), []).append(identity)
    for (service, kind), identities in identities_by_service_kind.items():
        selected = min(identities, key=lambda item: (-len(metric_values[item]), item.key))
        selections[f"{service}:{kind}"] = MetricSelectionV3(selected, len(metric_values[selected]))
        values[selected.key] = metric_values[selected]
    from statistics import fmean, pstdev
    statistics = {
        key: BaselineStatistic(fmean(items), pstdev(items), len(items))
        for key, items in values.items()
    }
    return BaselineSnapshotV3(
        statistics, {key: tuple(sorted(items)) for key, items in templates.items()},
        tuple(sorted(allowed)), selections, extractor.anomaly_policy.version,
    )


def _baseline_crash_safe(
    *, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path | None,
    sources: Mapping[str, IncidentSource], train_incident_ids: Iterable[str],
    work_root: Path, base_key: str, split_key: str, resume: bool,
    progress: Callable[[str, int, str | None, Mapping[str, int]], None] | None = None,
) -> tuple[BaselineSnapshotV3, dict[str, int]]:
    """Build baseline through isolated workers; the parent never owns events.

    Individual contribution files retain the original per-incident sequences.
    A dedicated finalizer reconstructs those sequences in sorted incident order,
    preserving P2's fmean/pstdev inputs exactly while releasing its heap on exit.
    """
    baseline_root = work_root / base_key / "baseline"
    contribution_root = baseline_root / "contributions"
    contribution_root.mkdir(parents=True, exist_ok=True)
    counters = {"baseline_checkpoint_hits": 0, "baseline_checkpoint_misses": 0,
                "baseline_checkpoint_invalid": 0, "baseline_checkpoint_recomputed": 0}
    train_ids = tuple(sorted(train_incident_ids))
    snapshot_bindings = _baseline_snapshot_bindings(
        base_key=base_key, split_key=split_key, train_incident_ids=train_ids)
    if resume:
        snapshot = _load_valid_baseline_snapshot(baseline_root / "snapshot.json", snapshot_bindings)
        if snapshot is not None:
            counters["baseline_checkpoint_hits"] += len(train_ids)
            if progress:
                progress("BASELINE", len(train_ids), None, counters)
            return _baseline_from_json(snapshot["baseline_json"]), counters
    for index, incident_id in enumerate(train_ids, start=1):
        source = sources[incident_id]
        target = contribution_root / f"{_checkpoint_name(incident_id)}.json"
        binding = _baseline_contribution_bindings(
            base_key=base_key, incident_id=incident_id, source=source)
        payload = _read_json(target) if target.is_file() else None
        if resume and _load_valid_baseline_contribution(target, binding) is not None:
            counters["baseline_checkpoint_hits"] += 1
            if progress:
                progress("BASELINE", index, incident_id, counters)
            print(f"BASELINE {index}/{len(train_ids)} CHECKPOINT HIT {incident_id}", flush=True)
            continue
        if resume and payload:
            counters["baseline_checkpoint_invalid"] += 1
        else:
            counters["baseline_checkpoint_misses"] += 1
        print(f"BASELINE {index}/{len(train_ids)} CHECKPOINT MISS {incident_id}", flush=True)
        result = _run_baseline_contribution_worker(
            registry=registry, rcaeval_cache=rcaeval_cache, incident_id=incident_id,
            base_key=base_key, output=target)
        if result.get("status") == "FAILED_WORKER":
            state_path = work_root / base_key / "state.json"
            state = _failed_worker_state(
                phase="BASELINE", worker_kind="BASELINE", base_key=base_key,
                split_key=split_key, diagnostic=result, state_path=state_path,
            )
            result = {**result, **state, "diagnostic_state": str(state_path)}
            raise WorkerExecutionError(result)
        counters["baseline_checkpoint_recomputed"] += 1
        if progress:
            progress("BASELINE", index, incident_id, counters)
    if progress:
        progress("BASELINE_FINALIZE", len(train_ids), None, counters)
    try:
        snapshot = _run_baseline_finalizer(
            registry=registry, rcaeval_cache=rcaeval_cache, sources=sources,
            contribution_root=contribution_root, train_incident_ids=train_ids,
            base_key=base_key, split_key=split_key,
            output=baseline_root / "snapshot.json")
    except WorkerExecutionError as exc:
        state_path = work_root / base_key / "state.json"
        state = _failed_worker_state(
            phase="BASELINE_FINALIZE", worker_kind="FINALIZER",
            base_key=base_key, split_key=split_key,
            diagnostic=exc.diagnostic, state_path=state_path,
        )
        exc.diagnostic.update(state, diagnostic_state=str(state_path))
        exc.args = (f"FINALIZER_WORKER_FAILED:none:diagnostic={state_path}",)
        raise
    baseline = _baseline_from_json(snapshot["baseline_json"])
    return baseline, counters


def _baseline_contribution_worker_payload(*, registry: FrozenDatasetRegistry,
                                          rcaeval_cache: str | Path | None,
                                          incident_id: str, base_key: str,
                                          target: Path) -> dict[str, Any]:
    if os.environ.get("CAUSALODS_TEST_FAIL_BASELINE_WORKER") == incident_id:
        sentinel = os.environ.get("CAUSALODS_TEST_BASELINE_WORKER_STDERR")
        if sentinel:
            print(sentinel, file=sys.stderr, flush=True)
        raise RuntimeError(f"TEST_BASELINE_WORKER_FAILURE:{incident_id}")
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources[incident_id]
    events = loader.events(source)
    contribution = _baseline_contribution(events, RCAFeatureExtractorV3())
    payload = {
        "schema_version": "scientific-preprocessing-baseline-contribution-v1",
        "preprocessing_version": PREPROCESSING_VERSION, "base_key": base_key,
        "incident_id": incident_id, "source_manifest_sha256": source.manifest_sha256,
        "source_cache_identity": _source_cache_identity(source),
        "feature_registry_hash": FeatureRegistryV3().fingerprint, "status": "COMPLETED",
        "contribution": contribution, "contribution_sha256": sha256_value(contribution),
        "worker": {"pid": os.getpid(), "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
    }
    _atomic_json(target, payload)
    return payload


def _baseline_contribution_bindings(*, base_key: str, incident_id: str,
                                    source: IncidentSource) -> dict[str, Any]:
    return {"schema_version": "scientific-preprocessing-baseline-contribution-v1",
            "preprocessing_version": PREPROCESSING_VERSION, "base_key": base_key,
            "incident_id": incident_id, "source_manifest_sha256": source.manifest_sha256,
            "source_cache_identity": _source_cache_identity(source),
            "feature_registry_hash": FeatureRegistryV3().fingerprint}


def _load_valid_baseline_contribution(path: Path, bindings: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        payload = _read_json(path)
        if (any(payload.get(key) != value for key, value in bindings.items())
                or payload.get("status") != "COMPLETED"
                or payload.get("contribution_sha256") != sha256_value(payload.get("contribution", {}))):
            return None
        return payload
    except (ValueError, TypeError, KeyError):
        return None


def _baseline_finalizer_payload(*, contribution_root: Path, sources: Mapping[str, IncidentSource], train_incident_ids: Iterable[str],
                                base_key: str, split_key: str, output: Path) -> dict[str, Any]:
    ids = tuple(sorted(train_incident_ids))
    contributions: dict[str, Mapping[str, Any]] = {}
    for incident_id in ids:
        payload = _load_valid_baseline_contribution(
            contribution_root / f"{_checkpoint_name(incident_id)}.json",
            _baseline_contribution_bindings(base_key=base_key, incident_id=incident_id,
                                            source=sources[incident_id]))
        if payload is None:
            raise ValueError(f"BASELINE_CONTRIBUTION_NOT_COMPLETED:{incident_id}")
        contributions[incident_id] = payload["contribution"]
    baseline = _baseline_from_contributions(contributions, ids, RCAFeatureExtractorV3())
    payload = {**_baseline_snapshot_bindings(base_key=base_key, split_key=split_key,
               train_incident_ids=ids),
               "status": "COMPLETED", "baseline_json": baseline.to_json(),
               "baseline_sha256": sha256_value(json.loads(baseline.to_json())),
               "training_incident_ids": list(ids),
               "worker": {"pid": os.getpid(), "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}}
    _atomic_json(output, payload)
    return payload


def _run_worker_command(*, registry: FrozenDatasetRegistry, command: str, spec: Path) -> dict[str, Any]:
    args = [sys.executable, "-m", "experiments.final_campaigns_v3", command, "--spec", str(spec)]
    completed = subprocess.run(args,
        cwd=registry.repository_root, env={**os.environ, "PYTHONPATH": f"{registry.repository_root}:{registry.repository_root / 'correlation-engine'}"},
        text=True, capture_output=True)
    if completed.returncode:
        return {"status": "FAILED_WORKER", "worker_returncode": completed.returncode,
                "signal": signal.Signals(-completed.returncode).name if completed.returncode < 0 else None,
                "stdout": _diagnostic_tail(completed.stdout),
                "stderr": _diagnostic_tail(completed.stderr),
                "command": args, "worker_spec": str(spec)}
    return {"status": "COMPLETED"}


def _run_baseline_contribution_worker(*, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path | None,
                                      incident_id: str, base_key: str, output: Path) -> dict[str, Any]:
    spec = output.with_suffix(".worker.json")
    _atomic_json(spec, {"registry_snapshot": registry.snapshot(), "rcaeval_cache": str(rcaeval_cache) if rcaeval_cache else None,
                        "incident_id": incident_id, "base_key": base_key, "output": str(output)})
    result = _run_worker_command(registry=registry, command="baseline-contribution-worker", spec=spec)
    if result["status"] != "COMPLETED":
        return {**result, "worker_kind": "BASELINE", "incident_id": incident_id}
    payload = _read_json(output)
    payload.setdefault("worker", {})["returncode"] = 0
    _atomic_json(output, payload)
    return payload


def _run_baseline_finalizer(*, registry: FrozenDatasetRegistry, rcaeval_cache: str | Path | None,
                            sources: Mapping[str, IncidentSource], contribution_root: Path, train_incident_ids: Iterable[str], base_key: str, split_key: str,
                            output: Path) -> dict[str, Any]:
    spec = output.with_suffix(".finalizer.json")
    _atomic_json(spec, {"registry_snapshot": registry.snapshot(), "contribution_root": str(contribution_root), "train_incident_ids": list(sorted(train_incident_ids)),
                        "base_key": base_key, "split_key": split_key, "output": str(output)})
    result = _run_worker_command(registry=registry, command="baseline-finalizer", spec=spec)
    if result["status"] != "COMPLETED":
        raise WorkerExecutionError({**result, "worker_kind": "FINALIZER", "incident_id": None})
    payload = _read_json(output)
    if payload.get("status") != "COMPLETED":
        raise RuntimeError("BASELINE_FINALIZER_INVALID")
    payload.setdefault("worker", {})["returncode"] = 0
    _atomic_json(output, payload)
    return payload


def _materialize_base_dataset_memory_bounded(
    *, loader: FrozenEvidenceLoader, sources: Mapping[str, IncidentSource],
    splits: Mapping[str, Iterable[str]], seed: int,
) -> _ScientificDatasetBuild:
    normalized_splits = {name: tuple(sorted(values)) for name, values in splits.items()}
    incident_ids = tuple(sorted({item for values in normalized_splits.values() for item in values}))
    split_by_id = {incident: split for split, ids in normalized_splits.items() for incident in ids}
    roots = {incident_id: sources[incident_id].root_cause_service for incident_id in incident_ids}
    extractor = RCAFeatureExtractorV3()
    baseline = _baseline_from_training_incidents(
        loader, sources, normalized_splits["train"], extractor)
    lifecycle = FeatureInputLifecycleCounter()
    full_view = FeatureRegistryV3().select()
    rows = []
    relation_versions: dict[str, set[str]] = {}
    relation_versions_by_incident = {}
    pruning_versions, pruning_fingerprints = {}, {}
    graph_fingerprints, topology_fingerprints, graph_policy_by_incident = {}, {}, {}
    positives = set()
    try:
        for incident_id in incident_ids:
            source = sources[incident_id]
            events = loader.events(source)
            event_fingerprints = loader.event_fingerprints_aligned(events)
            bundle, _ = FrozenEvidenceLoader._bundle(source, events)
            graph = EventGraphBuilder().build(bundle)
            lifecycle.enter()
            if event_fingerprints is not None:
                if any(node.event is not event for node, event in zip(graph.nodes, events)):
                    event_fingerprints = {
                        EventReference(event.event_id, event.source_reference): fingerprint.hex()
                        for event, fingerprint in zip(events, event_fingerprints)
                    }
                _bind_event_fingerprints(graph, event_fingerprints)
            graph_fingerprints[incident_id] = graph.graph_fingerprint
            topology_fingerprints[incident_id] = sha256_value(None)
            relation_versions_by_incident[incident_id] = {}
            for relation, value in graph.rule_versions.items():
                versions = (value,) if isinstance(value, str) else tuple(value)
                relation_versions.setdefault(relation, set()).update(versions)
                relation_versions_by_incident[incident_id][relation] = tuple(sorted(versions))
            graph_policy_by_incident[incident_id] = dict(graph.policy_provenance)
            pruning_versions[incident_id] = "not-applied"
            pruning_fingerprints[incident_id] = sha256_value("not-applied")
            for vector in extractor.extract(bundle, graph, baselines=baseline):
                root = roots[vector.incident_id]
                if root is None:
                    raise ValueError(f"HEALTHY_TOP1_TARGET_NOT_APPLICABLE:{vector.incident_id}")
                target = RCAServiceTargetV3(
                    vector.incident_id, vector.service_name, vector.service_name == root,
                    LabelStatus.LABELED, "frozen-ground-truth-evaluation-channel",
                    root_cause_rank=1 if vector.service_name == root else None,
                )
                if target.is_root_cause:
                    positives.add(vector.incident_id)
                rows.append(DatasetRowV3(
                    split_by_id[vector.incident_id],
                    _full_view_vector(vector, full_view), target))
            lifecycle.exit()
            del graph
            del bundle
            del events
            gc.collect()
    finally:
        if lifecycle.live:
            lifecycle.live = 0
        loader._last_event_fingerprints = []
    missing = set(roots) - positives
    if missing:
        raise ValueError(f"GROUND_TRUTH_SERVICE_ABSENT_FROM_EVIDENCE:{sorted(missing)}")
    rows = tuple(sorted(rows, key=lambda row: (
        row.split, row.vector.incident_id, row.vector.service_name)))
    normalized_relations = {key: tuple(sorted(value)) for key, value in relation_versions.items()}
    business_policy_versions = sorted({
        value["business_transition_policy"]["version"]
        for value in graph_policy_by_incident.values()
    })
    topology_policy_versions = sorted({
        value["topology_dependency_policy"]["topology_version"]
        for value in graph_policy_by_incident.values()
    })
    policy_provenance = PolicyProvenanceV3(
        normalized_relations, relation_versions_by_incident,
        extractor.graph_feature_policy.version,
        extractor.graph_feature_policy.fingerprint,
        extractor.graph_feature_policy.to_dict(),
        pruning_versions, pruning_fingerprints,
        extractor.anomaly_policy.version,
        extractor.anomaly_policy.fingerprint,
        extractor.anomaly_policy.to_dict(),
        baseline.schema_version,
        ",".join(baseline.metric_selection_policy_versions) or "not-observed",
        ",".join(baseline.statistic_policy_versions) or "not-observed",
        ",".join(business_policy_versions),
        ",".join(topology_policy_versions), graph_fingerprints, topology_fingerprints,
        graph_policy_by_incident,
    )
    fractions = {"train": .7, "validation": .15, "test": 1 - .7 - .15}
    dataset = RCAFeatureDatasetV3(
        rows, full_view, baseline, normalized_splits, seed, fractions,
        policy_provenance, True,
    )
    return _ScientificDatasetBuild(dataset, lifecycle)


def _materialize_base_dataset_crash_safe(
    *, loader: FrozenEvidenceLoader, registry: FrozenDatasetRegistry,
    rcaeval_cache: str | Path | None, splits: Mapping[str, Iterable[str]],
    seed: int, base_key: str, split_key: str, work_root: Path, resume: bool,
    official_baseline_snapshot: Mapping[str, Any] | None = None,
    official_baseline_snapshot_path: str | Path | None = None,
    scientific_tranche_binding: Mapping[str, Any] | None = None,
) -> _ScientificDatasetBuild:
    normalized_splits = {name: tuple(sorted(values)) for name, values in splits.items()}
    incident_ids = tuple(sorted({item for values in normalized_splits.values() for item in values}))
    split_by_id = {incident: split for split, ids in normalized_splits.items() for incident in ids}
    extractor = RCAFeatureExtractorV3()
    state_path = work_root / base_key / "state.json"
    def write_state(status: str, phase: str, **extra: Any) -> None:
        _atomic_json(state_path, {"schema_version": "scientific-preprocessing-work-state-v2",
            "status": status, "phase": phase, "base_key": base_key, "split_key": split_key,
            "parent_pid": os.getpid(), "total_baseline": len(normalized_splits["train"]),
            "total_features": len(incident_ids), **extra})
    def baseline_progress(phase: str, completed: int, incident_id: str | None,
                          counters: Mapping[str, int]) -> None:
        write_state("IN_PROGRESS", phase, completed_baseline=completed, completed_features=0,
                    current_incident_id=incident_id, last_completed_incident=incident_id,
                    **counters)
    write_state("IN_PROGRESS", "BASELINE", completed_baseline=0, completed_features=0)
    using_official_baseline = official_baseline_snapshot_path is not None
    if using_official_baseline:
        if official_baseline_snapshot is None:
            official_baseline_snapshot = _read_json(Path(official_baseline_snapshot_path))
        baseline = _baseline_from_json(official_baseline_snapshot["baseline_json"])
        actual = official_baseline_snapshot.get("baseline_sha256")
        if not isinstance(actual, str) or len(actual) != 64:
            raise RuntimeError("OFFICIAL_BASELINE_SHA256_MISMATCH")
        expected_official_sha = (scientific_tranche_binding or {}).get(
            "official_parent_baseline_sha256",
            "4471f20eab2c0b619eaf8871cb13b5f83fe8611cb539e19d7badc574dc91eae1")
        if actual != expected_official_sha:
            raise RuntimeError("OFFICIAL_BASELINE_SHA256_MISMATCH")
        if official_baseline_snapshot.get("status") != "COMPLETED" or \
                official_baseline_snapshot.get("preprocessing_version") != PREPROCESSING_VERSION:
            raise RuntimeError("OFFICIAL_BASELINE_SNAPSHOT_CONTRACT_MISMATCH")
        official_binding_values = {
            "base_key": official_baseline_snapshot["base_key"],
            "split_sha256": official_baseline_snapshot["split_sha256"],
            "training_incident_ids": tuple(official_baseline_snapshot["training_incident_ids"]),
        }
        if scientific_tranche_binding is not None and (
                official_binding_values["base_key"] != scientific_tranche_binding["parent_base_key"]
                or official_binding_values["split_sha256"] != scientific_tranche_binding[
                    "parent_split_key"]):
            raise RuntimeError("OFFICIAL_PARENT_BASELINE_BINDING_FAIL")
        del official_baseline_snapshot
        gc.collect()
        baseline_report = {"official_parent_baseline_reused": True,
                           "official_parent_baseline_sha256": actual}
    else:
        try:
            baseline, baseline_report = _baseline_crash_safe(
                registry=registry, rcaeval_cache=rcaeval_cache, sources=loader.sources,
                train_incident_ids=normalized_splits["train"], work_root=work_root, base_key=base_key,
                split_key=split_key, resume=resume, progress=baseline_progress,
            )
        except WorkerExecutionError:
            raise
    if using_official_baseline:
        baseline_sha256 = actual
    else:
        baseline_json = baseline.to_json()
        baseline_sha256 = sha256_value(json.loads(baseline_json))
        del baseline_json
        gc.collect()
    baseline_snapshot_path = (Path(official_baseline_snapshot_path)
                              if using_official_baseline
                              else work_root / base_key / "baseline" / "snapshot.json")
    baseline_snapshot_bindings = _baseline_snapshot_bindings(
        base_key=(official_binding_values["base_key"] if using_official_baseline else base_key),
        split_key=(official_binding_values["split_sha256"]
                   if using_official_baseline else split_key),
        train_incident_ids=(official_binding_values["training_incident_ids"]
                            if using_official_baseline
                            else normalized_splits["train"]))
    if scientific_tranche_binding is not None:
        baseline_binding_policy = "frozen-parent-train"
        baseline_binding_provenance = {
            "official_parent_baseline_sha256": baseline_sha256,
            "parent_base_key": scientific_tranche_binding["parent_base_key"],
            "parent_split_sha256": scientific_tranche_binding["parent_split_key"],
            "scientific_tranche_id": scientific_tranche_binding["tranche_id"],
            "scientific_tranche_sha256": scientific_tranche_binding["selection_sha256"],
            "parent_training_incident_ids_sha256": sha256_value(
                sorted(baseline.training_incident_ids)),
            "parent_training_incident_count": len(baseline.training_incident_ids),
        }
        baseline_binding_provenance["baseline_binding_sha256"] = baseline_binding_sha256(
            baseline_binding_provenance, normalized_splits)
        try:
            baseline_reference_path = str(
                baseline_snapshot_path.resolve().relative_to(registry.repository_root.resolve()))
        except ValueError as exc:
            raise RuntimeError("OFFICIAL_BASELINE_REFERENCE_OUTSIDE_REPOSITORY") from exc
        baseline_reference = {
            "schema_version": "frozen-parent-baseline-reference-v1",
            "path": baseline_reference_path,
            "baseline_sha256": baseline_sha256,
            "parent_base_key": baseline_binding_provenance["parent_base_key"],
            "parent_split_sha256": baseline_binding_provenance["parent_split_sha256"],
            "parent_training_incident_ids_sha256": baseline_binding_provenance[
                "parent_training_incident_ids_sha256"],
            "parent_training_incident_count": baseline_binding_provenance[
                "parent_training_incident_count"],
            "preprocessing_version": PREPROCESSING_VERSION,
        }
        baseline_parent_training_ids = tuple(baseline.training_incident_ids)
        total_incidents = sum(len(values) for values in normalized_splits.values())
        split_fractions = {name: len(values) / total_incidents
                           for name, values in normalized_splits.items()}
    else:
        baseline_binding_policy = "split-train-exact"
        baseline_binding_provenance = {}
        split_fractions = {"train": .7, "validation": .15,
                           "test": 1 - .7 - .15}
        baseline_reference = {}
        baseline_parent_training_ids = ()
    incident_root = work_root / base_key / "incidents"
    incident_root.mkdir(parents=True, exist_ok=True)
    counters = {"checkpoint_hits": 0, "checkpoint_misses": 0,
                "checkpoint_invalid": 0, "checkpoint_recomputed": 0}
    rows = []
    relation_versions: dict[str, set[str]] = {}
    relation_versions_by_incident = {}
    pruning_versions, pruning_fingerprints = {}, {}
    graph_fingerprints, topology_fingerprints, graph_policy_by_incident = {}, {}, {}
    worker_reports = []
    for index, incident_id in enumerate(incident_ids, start=1):
        write_state("IN_PROGRESS", "FEATURES", completed_baseline=len(normalized_splits["train"]),
                    completed_features=index - 1, current_incident_id=incident_id,
                    **baseline_report, **counters)
        source = loader.sources[incident_id]
        target = incident_root / f"{_checkpoint_name(incident_id)}.json"
        bindings = _checkpoint_bindings(
            base_key=base_key, incident_id=incident_id, source=source,
            baseline_sha256=baseline_sha256, split_key=split_key)
        payload = _load_valid_incident_checkpoint(target, bindings) if resume else None
        if payload is not None:
            counters["checkpoint_hits"] += 1
            print(f"FEATURES {index}/{len(incident_ids)} CHECKPOINT HIT {incident_id}", flush=True)
        else:
            counters["checkpoint_misses"] += 1 if not target.is_file() else 0
            counters["checkpoint_invalid"] += 1 if resume and target.is_file() else 0
            print(f"FEATURES {index}/{len(incident_ids)} CHECKPOINT MISS {incident_id}", flush=True)
            payload = _run_incident_worker(
                registry=registry, rcaeval_cache=rcaeval_cache, incident_id=incident_id,
                split=split_by_id[incident_id], base_key=base_key, split_key=split_key,
                baseline_snapshot_path=baseline_snapshot_path,
                baseline_sha256=baseline_sha256,
                baseline_snapshot_bindings=baseline_snapshot_bindings, output=target,
            )
            if payload.get("status") == "FAILED_WORKER":
                state_path = work_root / base_key / "state.json"
                state = _failed_worker_state(
                    phase="FEATURES", worker_kind="FEATURE", base_key=base_key,
                    split_key=split_key, diagnostic=payload, state_path=state_path,
                )
                payload = {**payload, **state, **counters,
                           "diagnostic_state": str(state_path)}
                _atomic_json(state_path, payload)
                raise WorkerExecutionError(payload)
            payload = _load_valid_incident_checkpoint(target, bindings)
            if payload is None:
                raise RuntimeError(f"INCIDENT_CHECKPOINT_INVALID_AFTER_WORKER:{incident_id}")
            counters["checkpoint_recomputed"] += 1
        worker_reports.append(payload.get("worker", {}))
        rows.extend(_rows_from_payload(payload["rows"]))
        graph_fingerprints[incident_id] = payload["graph_fingerprint"]
        topology_fingerprints[incident_id] = payload["topology_fingerprint"]
        relation_versions_by_incident[incident_id] = {}
        for relation, versions in payload["relation_rule_versions"].items():
            relation_versions.setdefault(relation, set()).update(versions)
            relation_versions_by_incident[incident_id][relation] = tuple(sorted(versions))
        graph_policy_by_incident[incident_id] = payload["graph_policy_provenance"]
        pruning_versions[incident_id] = payload["pruning_policy_version"]
        pruning_fingerprints[incident_id] = payload["pruning_fingerprint"]
    rows = tuple(sorted(rows, key=lambda row: (
        row.split, row.vector.incident_id, row.vector.service_name)))
    normalized_relations = {key: tuple(sorted(value)) for key, value in relation_versions.items()}
    business_policy_versions = sorted({
        value["business_transition_policy"]["version"]
        for value in graph_policy_by_incident.values()
    })
    topology_policy_versions = sorted({
        value["topology_dependency_policy"]["topology_version"]
        for value in graph_policy_by_incident.values()
    })
    policy_provenance = PolicyProvenanceV3(
        normalized_relations, relation_versions_by_incident,
        extractor.graph_feature_policy.version, extractor.graph_feature_policy.fingerprint,
        extractor.graph_feature_policy.to_dict(), pruning_versions, pruning_fingerprints,
        extractor.anomaly_policy.version, extractor.anomaly_policy.fingerprint,
        extractor.anomaly_policy.to_dict(), baseline.schema_version,
        ",".join(baseline.metric_selection_policy_versions) or "not-observed",
        ",".join(baseline.statistic_policy_versions) or "not-observed",
        ",".join(business_policy_versions), ",".join(topology_policy_versions),
        graph_fingerprints, topology_fingerprints, graph_policy_by_incident,
    )
    dataset_baseline = baseline
    if scientific_tranche_binding is not None:
        dataset_baseline = None
        del baseline
        gc.collect()
    dataset = RCAFeatureDatasetV3(
        rows, FeatureRegistryV3().select(), dataset_baseline, normalized_splits, seed,
        split_fractions, policy_provenance, True,
        baseline_binding_policy, baseline_binding_provenance,
        baseline_reference, baseline_parent_training_ids,
    )
    report = {
        **baseline_report, **counters,
        "parent_pid": os.getpid(), "worker_count": 1,
        "workers": worker_reports,
        "max_live_full_feature_inputs": 0,
    }
    _atomic_json(work_root / base_key / "state.json", {
        "schema_version": "scientific-preprocessing-work-state-v2", "status": "COMPLETED",
        "phase": "DATASET_FINALIZE", "base_key": base_key, "split_key": split_key,
        "completed_baseline": len(normalized_splits["train"]), "completed_features": len(incident_ids),
        "last_completed_incident": incident_ids[-1] if incident_ids else None, **report,
    })
    return _ScientificDatasetBuild(dataset, FeatureInputLifecycleCounter(), report)


@dataclass(frozen=True)
class IncidentSource:
    incident_id: str
    dataset_id: str
    system: str
    manifest_path: Path
    manifest_sha256: str
    root_cause_service: str | None
    available_modalities: tuple[str, ...]


class _FrozenBoundaryScores(Mapping[EventReference, float]):
    """Implicit all-one scores for the authoritative frozen case boundary."""

    _causalods_immutable_frozen_boundary = True

    def __init__(self, events: tuple[UnifiedEvent, ...]):
        self._events = events

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[EventReference]:
        return (EventReference(event.event_id, event.source_reference)
                for event in self._events)

    def __getitem__(self, reference: EventReference) -> float:
        if not isinstance(reference, EventReference):
            raise KeyError(reference)
        # This private mapping is consumed only through its own keys.  Avoid an
        # additional multi-million-entry membership index solely to rediscover
        # that every authoritative-boundary score is exactly one.
        return 1.0


class _FrozenBoundaryAssociations(Sequence[EventAssociation]):
    def __init__(self, events: tuple[UnifiedEvent, ...]):
        self._events = events

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[position] for position in range(*index.indices(len(self))))
        event = self._events[index]
        return EventAssociation(
            EventReference(event.event_id, event.source_reference), 1.0,
            ("frozen_incident_boundary",),
        )


class _FrozenBoundaryProvenance(Sequence[ProvenanceRecord]):
    def __init__(self, events: tuple[UnifiedEvent, ...]):
        self._events = events

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[position] for position in range(*index.indices(len(self))))
        event = self._events[index]
        return ProvenanceRecord(
            EventReference(event.event_id, event.source_reference),
            event.source_dataset,
        )


def _bind_event_fingerprints(graph, values) -> None:
    """Transfer the loader-owned aligned byte list without a list→tuple peak."""
    if (isinstance(values, list) and len(values) == len(graph.nodes)
            and all(isinstance(value, bytes) and len(value) == 32
                    for value in values)):
        object.__setattr__(graph, "_event_fingerprints", values)
        object.__setattr__(graph, "_graph_fingerprint_cache", None)
        return
    graph._bind_event_fingerprints(values)


class FrozenEvidenceLoader:
    def __init__(self, registry: FrozenDatasetRegistry, *, rcaeval_cache: str | Path | None):
        self.registry = registry
        self.rcaeval_cache = (CompressedNormalizedEventCache(rcaeval_cache)
                              if rcaeval_cache else None)
        self.sources = self._sources()
        self._last_event_fingerprints: list[bytes] = []

    def _sources(self) -> dict[str, IncidentSource]:
        result = {}
        for frozen in self.registry.datasets.values():
            index = _read_json(frozen.index)
            for row in index["incidents"]:
                manifest_path = frozen.root / row["manifest_path"]
                manifest = _read_json(manifest_path)
                if frozen.kind == "rcaeval":
                    root = row.get("root_cause_service")
                    modalities = tuple(row.get("modalities", ()))
                    system = row["system"]
                else:
                    truth_path = frozen.root / "incidents" / row["incident_id"].replace("/", "__") / "ground-truth.json"
                    truth = _read_json(truth_path)
                    root = truth.get("root_cause_service")
                    modalities = tuple(row.get("capabilities", ()))
                    system = manifest["source_system"]
                result[row["incident_id"]] = IncidentSource(
                    row["incident_id"], frozen.dataset_id, system, manifest_path,
                    row.get("manifest_sha256") or file_sha256(manifest_path), root,
                    tuple(sorted(modalities)),
                )
        return result

    def events(self, source: IncidentSource) -> tuple[UnifiedEvent, ...]:
        return self._events(source, profile=None)

    def events_profiled(self, source: IncidentSource) -> tuple[tuple[UnifiedEvent, ...], dict[str, Any]]:
        profile = {
            "file_resolution_seconds": 0.0,
            "cache_read_seconds": 0.0,
            "row_conversion_seconds": 0.0,
            "unified_event_seconds": 0.0,
        }
        started = time.perf_counter()
        events = self._events(source, profile=profile)
        profile["total_seconds"] = time.perf_counter() - started
        profile["event_count"] = len(events)
        return events, profile

    def _events(self, source: IncidentSource,
                profile: dict[str, Any] | None) -> tuple[UnifiedEvent, ...]:
        self._last_event_fingerprints = []
        stage = time.perf_counter()
        manifest = _read_json(source.manifest_path)
        frozen = self.registry.datasets[source.dataset_id]
        if profile is not None:
            profile["file_resolution_seconds"] += time.perf_counter() - stage
        if frozen.kind == "rcaeval":
            if self.rcaeval_cache is None:
                raise RuntimeError("RCAEVAL_NORMALIZED_CACHE_NOT_AVAILABLE")
            events = self._rcaeval_events(
                manifest, incident_id=source.incident_id, profile=profile)
            if events is None:
                raise RuntimeError(f"RCAEVAL_NORMALIZED_CACHE_ENTRY_NOT_AVAILABLE:{source.incident_id}")
            return events
        events = []
        allowed = {item.name for item in fields(UnifiedEvent)}
        for relative in manifest["normalized_source_files"]:
            source_path = frozen.root / relative
            stage = time.perf_counter()
            if file_sha256(source_path) != manifest["source_files_sha256"][relative]:
                raise RuntimeError(f"FROZEN_NORMALIZED_EVENTS_HASH_MISMATCH:{source.incident_id}")
            with gzip.open(source_path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            if profile is not None:
                profile["cache_read_seconds"] += time.perf_counter() - stage
            for item in payload:
                stage = time.perf_counter()
                row = {key: value for key, value in item.items() if key in allowed}
                if profile is not None:
                    profile["row_conversion_seconds"] += time.perf_counter() - stage
                    stage = time.perf_counter()
                events.append(UnifiedEvent.from_dict(row))
                if profile is not None:
                    profile["unified_event_seconds"] += time.perf_counter() - stage
        values = tuple(sorted(events, key=lambda item: (item.event_timestamp, item.event_id)))
        return values

    def _rcaeval_events(self, manifest: Mapping[str, Any], *,
                        profile: dict[str, Any] | None,
                        incident_id: str = "unknown") -> tuple[UnifiedEvent, ...] | None:
        if not hasattr(self, "_last_event_fingerprints"):
            self._last_event_fingerprints = []
        import pyarrow.parquet as pq
        try:
            import orjson
            decode = orjson.loads
        except ImportError:  # pragma: no cover - portable fallback
            decode = json.loads

        cache = self.rcaeval_cache
        stage = time.perf_counter()
        header = cache.inspect(
            key=manifest["cache_key"], adapter_version=manifest["adapter_version"],
            configuration_sha256=manifest["adapter_configuration_sha256"],
            source_files_sha256=manifest["source_files_sha256"],
        )
        if profile is not None:
            profile["file_resolution_seconds"] += time.perf_counter() - stage
        if header is None:
            return None
        parquet = pq.ParquetFile(cache.target(manifest["cache_key"]))
        expected_names = {item.name for item in fields(UnifiedEvent)}
        missing_names = sorted(expected_names - set(parquet.schema.names))
        if missing_names:
            raise RuntimeError(
                f"RCAEVAL_CACHE_SCHEMA_MISMATCH:{incident_id}:missing={','.join(missing_names)}"
            )
        encoded_names = {
            name: json.dumps(name, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            for name in parquet.schema.names
        }
        ordered_names = tuple(sorted(parquet.schema.names))
        interned_text_fields = (
            "business_entity_type", "business_operation", "business_outcome",
            "deployment_environment", "error_type", "event_name", "event_type",
            "metric_aggregation", "metric_name", "metric_unit", "schema_version",
            "service_instance_id", "service_name", "service_version", "severity",
            "signal_type", "source_dataset", "status_code", "trace_id", "trace_state",
        )
        immutable_value_caches = {
            "attributes": OrderedDict(),
            "resource_attributes": OrderedDict(),
            "instrumentation_scope": OrderedDict(),
        }
        digest = hashlib.sha256()
        digest.update(b"[")
        events, index = [], 0
        batches = iter(parquet.iter_batches(batch_size=10_000))
        while True:
            stage = time.perf_counter()
            try:
                batch = next(batches)
            except StopIteration:
                break
            if profile is not None:
                profile["cache_read_seconds"] += time.perf_counter() - stage
                stage = time.perf_counter()
            rows = batch.to_pylist()
            if profile is not None:
                profile["row_conversion_seconds"] += time.perf_counter() - stage
            for row in rows:
                stage = time.perf_counter() if profile is not None else 0.0
                null_names = sorted(name for name in ordered_names if row.get(name) is None)
                if null_names:
                    raise RuntimeError(
                        f"RCAEVAL_CACHE_NULL_VALUE:{incident_id}:row={index}:"
                        f"fields={','.join(null_names)}"
                    )
                raw = b"{" + b",".join(
                    encoded_names[name] + b":" + row[name].encode("utf-8")
                    for name in ordered_names
                ) + b"}"
                payload = {name: decode(value) for name, value in row.items()}
                # Share only equal immutable text values.  This is invisible to
                # equality/serialization but prevents millions of duplicate
                # service/modality/name strings in large RCAEval incidents.
                for name in interned_text_fields:
                    value = payload[name]
                    if isinstance(value, str):
                        payload[name] = sys.intern(value)
                for name, values in immutable_value_caches.items():
                    raw_value = row[name]
                    cached = values.get(raw_value)
                    if cached is not None:
                        payload[name] = cached
                if index:
                    digest.update(b",")
                digest.update(raw)
                if profile is not None:
                    profile["row_conversion_seconds"] += time.perf_counter() - stage
                    stage = time.perf_counter()
                # Cache schema names were obtained from the frozen Parquet
                # contract, so the from_dict unknown-field copy/set scan would
                # repeat work already validated by the official materializer.
                try:
                    event = UnifiedEvent(**payload)
                except Exception as exc:
                    raise RuntimeError(
                        f"RCAEVAL_UNIFIED_EVENT_INVALID:{incident_id}:row={index}:"
                        f"error_type={type(exc).__name__}"
                    ) from exc
                events.append(event)
                self._last_event_fingerprints.append(hashlib.sha256(raw).digest())
                for name, values in immutable_value_caches.items():
                    raw_value = row[name]
                    if raw_value not in values:
                        values[raw_value] = getattr(event, name)
                        if name == "attributes" and len(values) > 20_000:
                            values.popitem(last=False)
                if profile is not None:
                    profile["unified_event_seconds"] += time.perf_counter() - stage
                index += 1
        digest.update(b"]")
        if len(events) != header["event_count"] or digest.hexdigest() != header["events_sha256"]:
            raise RuntimeError("Cache comprimido adulterado.")
        return tuple(events)

    def event_fingerprints(self, events: tuple[UnifiedEvent, ...],
                           ) -> Mapping[EventReference, str] | None:
        if len(self._last_event_fingerprints) != len(events):
            return None
        values = {
            EventReference(event.event_id, event.source_reference):
                fingerprint.hex()
            for event, fingerprint in zip(events, self._last_event_fingerprints)
        }
        self._last_event_fingerprints = []
        return values

    def event_fingerprints_aligned(self, events: tuple[UnifiedEvent, ...],
                                   ) -> Sequence[bytes] | None:
        if len(self._last_event_fingerprints) != len(events):
            return None
        # Transfer exclusive ownership.  The graph treats this private aligned
        # sequence as immutable and avoids a simultaneous list + tuple copy.
        values = self._last_event_fingerprints
        self._last_event_fingerprints = []
        return values

    @staticmethod
    def feature_input(source: IncidentSource, events: tuple[UnifiedEvent, ...], *,
                      event_fingerprints: Mapping[EventReference, str] | tuple[bytes, ...] | None = None,
                      ) -> FeatureInputV3:
        bundle, _ = FrozenEvidenceLoader._bundle(source, events)
        graph = EventGraphBuilder().build(bundle)
        if event_fingerprints is not None:
            if not isinstance(event_fingerprints, Mapping) and any(
                node.event is not event for node, event in zip(graph.nodes, events)
            ):
                event_fingerprints = {
                    EventReference(event.event_id, event.source_reference): fingerprint.hex()
                    for event, fingerprint in zip(events, event_fingerprints)
                }
            _bind_event_fingerprints(graph, event_fingerprints)
        return FeatureInputV3(bundle, graph)

    @staticmethod
    def feature_input_profiled(source: IncidentSource, events: tuple[UnifiedEvent, ...], *,
                               event_fingerprints: Mapping[EventReference, str] | tuple[bytes, ...] | None = None,
                               ) -> tuple[FeatureInputV3, dict[str, Any]]:
        started = time.perf_counter()
        bundle, boundary_mode = FrozenEvidenceLoader._bundle(source, events)
        bundle_seconds = time.perf_counter() - started
        graph, graph_profile = EventGraphBuilder().build_profiled(bundle)
        if event_fingerprints is not None:
            if not isinstance(event_fingerprints, Mapping) and any(
                node.event is not event for node, event in zip(graph.nodes, events)
            ):
                event_fingerprints = {
                    EventReference(event.event_id, event.source_reference): fingerprint.hex()
                    for event, fingerprint in zip(events, event_fingerprints)
                }
            _bind_event_fingerprints(graph, event_fingerprints)
        return FeatureInputV3(bundle, graph), {
            "bundle_aggregation_seconds": bundle_seconds,
            "boundary_mode": boundary_mode,
            "graph": graph_profile,
        }

    @staticmethod
    def _bundle(source: IncidentSource, events: tuple[UnifiedEvent, ...],
                ) -> tuple[IncidentEvidenceBundle, str]:
        assembler = IncidentEvidenceAssembler()
        # Above either frozen memory bound the public assembler provably cannot
        # return one bundle containing every event.  The historical branch then
        # constructs the authoritative frozen boundary below, so bypassing the
        # doomed O(events * max_events_per_incident) attempt is exact.
        limit = min(assembler.policy.max_events_per_incident,
                    assembler.policy.max_total_events)
        indexed = None
        if len(events) <= limit and source.dataset_id == "rcaeval":
            indexed = FrozenEvidenceLoader._identity_free_indexed_bundle(
                source, events, assembler)
        bundles = (() if len(events) > limit or indexed is not None
                   else assembler.assemble(events))
        if indexed is not None:
            bundle = indexed
            boundary_mode = "assembler-indexed-equivalent"
        elif len(bundles) == 1 and len(bundles[0].events) == len(events):
            bundle = replace(bundles[0], incident_id=source.incident_id)
            boundary_mode = "assembler-single-bundle"
        else:
            # Public datasets define an authoritative case boundary even when no
            # cross-modality correlation identity exists in their telemetry.
            start, end = min(item.event_timestamp for item in events), max(
                item.event_timestamp for item in events)
            scores = _FrozenBoundaryScores(events)
            bundle = IncidentEvidenceBundle(
                source.incident_id, f"frozen:{source.incident_id}", AnalysisWindow(start, end),
                events, assembler.aggregator.aggregate(events),
                tuple(sorted({item.trace_id for item in events if item.trace_id})),
                tuple(sorted({(item.business_entity_type, item.business_entity_id)
                              for item in events if item.business_entity_id},
                             key=lambda item: ((item[0] or ""), item[1]))),
                tuple(sorted(set(item.value for item in SignalType)
                             - {item.signal_type.value for item in events})),
                (), _FrozenBoundaryAssociations(events), {},
                _FrozenBoundaryProvenance(events), closed=True,
            )
            # IncidentEvidenceBundle freezes arbitrary public mappings by
            # copying them.  Install the private immutable representation only
            # after construction so no per-event score dictionary is created.
            object.__setattr__(bundle, "association_scores", scores)
            boundary_mode = "authoritative-frozen-boundary"
        return bundle, boundary_mode

    @staticmethod
    def _identity_free_indexed_bundle(
        source: IncidentSource, events: tuple[UnifiedEvent, ...],
        assembler: IncidentEvidenceAssembler,
    ) -> IncidentEvidenceBundle | None:
        """Exact indexed specialization of the assembler's identity-free path."""
        if not events or any(
            event.correlation_id or event.trace_id or event.parent_span_id
            or event.span_links or event.business_entity_type or event.business_entity_id
            or not event.service_name
            for event in events
        ):
            return None
        event_keys = [(event.event_id, event.source_reference) for event in events]
        if len(event_keys) != len(set(event_keys)):
            return None
        timestamps = []
        services = set()
        associations = {}
        late = set()
        first = events[0]
        workflow_key = assembler._workflow_key(first, None, None)
        for index, event in enumerate(events):
            reference = EventReference(event.event_id, event.source_reference)
            if index == 0:
                score = 1.0
                reasons = ("incident_seed",)
            else:
                position = bisect_left(timestamps, event.event_timestamp)
                neighbors = []
                if position:
                    neighbors.append(timestamps[position - 1])
                if position < len(timestamps):
                    neighbors.append(timestamps[position])
                temporal = assembler.policy.temporal_score(
                    event.event_timestamp, neighbors)
                related = event.service_name in services
                if temporal > 0 and related:
                    reasons = ("topology_and_temporal_proximity",)
                    score = 0.45 + 0.20 * temporal
                elif temporal > 0:
                    reasons = ("temporal_proximity_only",)
                    score = 0.10 + 0.25 * temporal
                else:
                    reasons, score = (), 0.0
                if score < assembler.policy.association_threshold:
                    return None
            associations[reference] = EventAssociation(reference, score, reasons)
            insort(timestamps, event.event_timestamp)
            services.add(event.service_name)
            if event.late_arrival_ms:
                late.add(reference)
        ordered = tuple(sorted(events, key=lambda item: (
            item.event_timestamp, item.event_id, item.source_reference or "")))
        references = tuple(EventReference(item.event_id, item.source_reference)
                           for item in ordered)
        signals = {event.signal_type.value for event in ordered}
        return IncidentEvidenceBundle(
            source.incident_id, workflow_key,
            AnalysisWindow(ordered[0].event_timestamp, ordered[-1].event_timestamp),
            ordered, assembler.aggregator.aggregate(ordered), (), (),
            tuple(item.value for item in SignalType if item.value not in signals),
            tuple(sorted(late, key=lambda item: (item.event_id, item.source_reference or ""))),
            tuple(associations[reference] for reference in references),
            {reference: associations[reference].score for reference in references},
            tuple(ProvenanceRecord(reference, event.source_dataset)
                  for reference, event in zip(references, ordered)),
            closed=False,
        )


def validate_rcaeval_runtime(*, registry: FrozenDatasetRegistry,
                             rcaeval_cache: str | Path,
                             incident_id: str) -> dict[str, Any]:
    """Read-only host preflight for one frozen RCAEval cache entry."""
    import importlib.util

    dependencies = {name: importlib.util.find_spec(name) is not None
                    for name in ("pyarrow", "joblib", "opentelemetry")}
    dependencies["orjson"] = importlib.util.find_spec("orjson") is not None
    for required in ("pyarrow", "joblib", "opentelemetry"):
        if not dependencies[required]:
            return {"status": "FAIL", "reason": "MISSING_RUNTIME_DEPENDENCY",
                    "module": required, "incident_id": incident_id,
                    "dependencies": dependencies}

    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources.get(incident_id)
    if source is None:
        raise ValueError(f"INCIDENT_NOT_FOUND:{incident_id}")
    if registry.datasets[source.dataset_id].kind != "rcaeval":
        raise ValueError(f"INCIDENT_NOT_RCAEVAL:{incident_id}")
    manifest = _read_json(source.manifest_path)
    cache = loader.rcaeval_cache
    assert cache is not None
    target = cache.target(manifest["cache_key"])
    metadata = cache.metadata_target(manifest["cache_key"])
    watched = (source.manifest_path, target, metadata)
    before = {str(path): (path.stat().st_size, path.stat().st_mtime_ns)
              for path in watched if path.is_file()}
    if not target.is_file() or not os.access(target, os.R_OK):
        raise RuntimeError(f"RCAEVAL_CACHE_ENTRY_NOT_AVAILABLE:{incident_id}:{target}")
    header = cache.inspect(
        key=manifest["cache_key"], adapter_version=manifest["adapter_version"],
        configuration_sha256=manifest["adapter_configuration_sha256"],
        source_files_sha256=manifest["source_files_sha256"],
    )
    if header is None:
        raise RuntimeError(f"RCAEVAL_CACHE_BINDING_MISMATCH:{incident_id}")

    import pyarrow.parquet as pq
    parquet = pq.ParquetFile(target)
    schema_names = tuple(parquet.schema.names)
    expected = {item.name for item in fields(UnifiedEvent)}
    missing = sorted(expected - set(schema_names))
    if missing:
        raise RuntimeError(
            f"RCAEVAL_CACHE_SCHEMA_MISMATCH:{incident_id}:missing={','.join(missing)}"
        )
    try:
        sample_rows = next(parquet.iter_batches(batch_size=128)).to_pylist()
    except (StopIteration, IndexError) as exc:
        raise RuntimeError(f"RCAEVAL_CACHE_EMPTY:{incident_id}") from exc
    if not sample_rows:
        raise RuntimeError(f"RCAEVAL_CACHE_EMPTY:{incident_id}")
    for index, row in enumerate(sample_rows):
        null_fields = sorted(name for name in expected if row.get(name) is None)
        if null_fields:
            raise RuntimeError(
                f"RCAEVAL_CACHE_NULL_VALUE:{incident_id}:row={index}:"
                f"fields={','.join(null_fields)}"
            )
        try:
            payload = {name: json.loads(row[name]) for name in schema_names}
            UnifiedEvent(**payload)
        except Exception as exc:
            raise RuntimeError(
                f"RCAEVAL_UNIFIED_EVENT_SAMPLE_INVALID:{incident_id}:row={index}:"
                f"error_type={type(exc).__name__}"
            ) from exc
    after = {str(path): (path.stat().st_size, path.stat().st_mtime_ns)
             for path in watched if path.is_file()}
    if before != after:
        raise RuntimeError(f"RCAEVAL_PREFLIGHT_MUTATED_INPUT:{incident_id}")
    return {"status": "PASS", "incident_id": incident_id,
            "cache_entry": str(target), "event_count": header["event_count"],
            "parquet_schema": list(schema_names), "dependencies": dependencies,
            "sample_rows_validated": len(sample_rows),
            "official_files_unchanged": True}


def run_heavy_resource_gate(*, registry: FrozenDatasetRegistry,
                            config_dir: str | Path,
                            model_specification: str | Path,
                            rcaeval_cache: str | Path,
                            incident_id: str,
                            reference_baseline: str | Path,
                            output: str | Path,
                            resume: bool = False) -> dict[str, Any]:
    """Run the official incident worker as a non-scientific resource probe."""
    output = Path(output).resolve()
    reference_baseline = Path(reference_baseline).resolve()
    forbidden = (
        registry.repository_root / "artifacts/final-experiments-v3",
        registry.repository_root / "artifacts/scientific-datasets-v3",
        registry.repository_root / "artifacts/validation/rcaeval-stratified-gate-v3/run",
    )
    if any(output == root.resolve() or root.resolve() in output.parents for root in forbidden):
        raise ValueError(f"RESOURCE_GATE_OUTPUT_NOT_ISOLATED:{output}")
    if not reference_baseline.is_file():
        raise FileNotFoundError(f"REFERENCE_BASELINE_NOT_FOUND:{reference_baseline}")
    spec_hash = model_specification_hash(model_specification)
    config = load_campaign_config(Path(config_dir) / "h1a.json")
    if config["model_hash"] != spec_hash:
        raise RuntimeError("MODEL_SPECIFICATION_CONFIG_BINDING_FAIL")
    baseline_raw = _read_json(reference_baseline)
    try:
        baseline_bindings = _baseline_snapshot_bindings(
            base_key=baseline_raw["base_key"],
            split_key=baseline_raw["split_sha256"],
            train_incident_ids=baseline_raw["training_incident_ids"],
        )
    except (KeyError, TypeError) as exc:
        raise RuntimeError("REFERENCE_BASELINE_INVALID") from exc
    baseline_payload = _load_valid_baseline_snapshot(
        reference_baseline, baseline_bindings)
    if baseline_payload is None:
        raise RuntimeError("REFERENCE_BASELINE_INVALID")
    runtime = validate_rcaeval_runtime(
        registry=registry, rcaeval_cache=rcaeval_cache,
        incident_id=incident_id)
    if runtime.get("status") != "PASS":
        raise RuntimeError(
            f"HEAVY_RESOURCE_GATE_CACHE_PREFLIGHT_FAIL:{canonical_json(runtime)}")
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    source = loader.sources.get(incident_id)
    if source is None:
        raise ValueError(f"INCIDENT_NOT_FOUND:{incident_id}")
    manifest = _read_json(source.manifest_path)
    normalized_events_sha256 = manifest.get("normalized_events_sha256")
    if not normalized_events_sha256:
        raise RuntimeError(f"NORMALIZED_EVENTS_SHA256_MISSING:{incident_id}")
    baseline_sha256 = baseline_payload["baseline_sha256"]
    identity = {
        "incident_id": incident_id,
        "manifest_sha256": source.manifest_sha256,
        "normalized_events_sha256": normalized_events_sha256,
        "reference_baseline_sha256": baseline_sha256,
        "feature_registry_fingerprint": FeatureRegistryV3().fingerprint,
        "preprocessing_implementation_version": PREPROCESSING_VERSION,
        "resource_gate_version": HEAVY_RESOURCE_GATE_VERSION,
    }
    resource_gate_key = sha256_value(identity)
    worker_base_key = f"{HEAVY_RESOURCE_GATE_VERSION}:base:{resource_gate_key}"
    worker_split_key = f"{HEAVY_RESOURCE_GATE_VERSION}:split:{resource_gate_key}"
    checkpoint = output / "checkpoint.json"
    report_path = output / "resource-gate-report.json"
    bindings = _checkpoint_bindings(
        base_key=worker_base_key, incident_id=incident_id, source=source,
        baseline_sha256=baseline_sha256, split_key=worker_split_key)
    before = registry.immutability_snapshot()
    cached = _load_valid_incident_checkpoint(checkpoint, bindings) if resume else None
    started = time.perf_counter()
    if cached is None:
        payload = _run_incident_worker(
            registry=registry, rcaeval_cache=rcaeval_cache,
            incident_id=incident_id, split="test", base_key=worker_base_key,
            split_key=worker_split_key,
            baseline_snapshot_path=reference_baseline,
            baseline_sha256=baseline_sha256,
            baseline_snapshot_bindings=baseline_bindings, output=checkpoint)
        checkpoint_status = "MISS"
    else:
        payload = cached
        checkpoint_status = "HIT"
    elapsed = time.perf_counter() - started
    if payload.get("status") == "FAILED_WORKER":
        result = {
            **payload, "resource_gate_only": True, "scientific_eligibility": False,
            "resource_gate_key": resource_gate_key,
            "resource_gate_baseline_source": str(reference_baseline),
            "resource_gate_baseline_sha256": baseline_sha256,
            "resource_gate_baseline_is_scientific_binding": False,
        }
        _atomic_json(report_path, result)
        return result
    validated = _load_valid_incident_checkpoint(checkpoint, bindings)
    if validated is None:
        raise RuntimeError("RESOURCE_GATE_CHECKPOINT_INVALID")
    if registry.immutability_snapshot() != before:
        raise RuntimeError("RESOURCE_GATE_MUTATED_FROZEN_DATASET")
    worker = payload.get("worker") or {}
    phase_rows = worker.get("phase_telemetry") or []
    phase_rss = {row["phase"]: row.get("rss_kb") for row in phase_rows}
    phase_elapsed = {row["phase"]: row.get("elapsed_seconds") for row in phase_rows}
    result = {
        "status": "PASS", "resource_gate_only": True,
        "scientific_eligibility": False,
        "resource_gate_version": HEAVY_RESOURCE_GATE_VERSION,
        "resource_gate_key": resource_gate_key,
        "resource_gate_baseline_source": str(reference_baseline),
        "resource_gate_baseline_sha256": baseline_sha256,
        "resource_gate_baseline_is_scientific_binding": False,
        "incident_id": incident_id,
        "manifest_sha256": source.manifest_sha256,
        "normalized_events_sha256": normalized_events_sha256,
        "event_count": runtime.get("event_count"),
        "cache_runtime_preflight": "PASS",
        "checkpoint_status": checkpoint_status,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": file_sha256(checkpoint),
        "scientific_payload_internal_consistency": "PASS",
        "worker_returncode": worker.get("returncode", 0),
        "signal": None,
        "oom": False,
        "swap_requirement_introduced_by_code": False,
        "worker_peak_rss_kb": worker.get("peak_rss_kb", 0),
        "worker_elapsed_seconds": worker.get("elapsed_seconds", 0),
        "phase_rss_kb": {
            "cache_loaded": phase_rss.get("cache"),
            "fingerprints_ready": phase_rss.get("fingerprints"),
            "bundle_ready": phase_rss.get("bundle"),
            "graph_ready": phase_rss.get("graph"),
            "features_done": phase_rss.get("features"),
        },
        "phase_elapsed_seconds": {
            "cache_load": phase_elapsed.get("cache"),
            "fingerprints": phase_elapsed.get("fingerprints"),
            "bundle": phase_elapsed.get("bundle"),
            "graph": phase_elapsed.get("graph"),
            "features": phase_elapsed.get("features"),
        },
        "elapsed_seconds": elapsed,
        "host_children_max_rss_kb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "frozen_dataset_immutability": "PASS",
        "preprocessing_version": PREPROCESSING_VERSION,
    }
    _atomic_json(report_path, result)
    return result


def _supervise(dataset: RCAFeatureDatasetV3,
               roots: Mapping[str, str | None]) -> RCAFeatureDatasetV3:
    rows = []
    positives = set()
    for row in dataset.rows:
        root = roots[row.vector.incident_id]
        if root is None:
            raise ValueError(f"HEALTHY_TOP1_TARGET_NOT_APPLICABLE:{row.vector.incident_id}")
        target = RCAServiceTargetV3(
            row.vector.incident_id, row.vector.service_name,
            row.vector.service_name == root, LabelStatus.LABELED,
            "frozen-ground-truth-evaluation-channel",
            root_cause_rank=1 if row.vector.service_name == root else None,
        )
        positives.add(row.vector.incident_id) if target.is_root_cause else None
        rows.append(DatasetRowV3(row.split, row.vector, target))
    missing = set(roots) - positives
    if missing:
        raise ValueError(f"GROUND_TRUTH_SERVICE_ABSENT_FROM_EVIDENCE:{sorted(missing)}")
    return RCAFeatureDatasetV3(
        tuple(rows), dataset.feature_view, dataset.baselines, dataset.split_incident_ids,
        dataset.seed, dataset.split_fractions, dataset.policy_provenance, True,
        dataset.baseline_binding_policy, dataset.baseline_binding_provenance,
        dataset.baseline_reference, dataset.baseline_parent_training_incident_ids,
    )


def _apply_view(dataset: RCAFeatureDatasetV3, view: str) -> RCAFeatureDatasetV3:
    excluded = VIEW_GROUPS[view]
    return dataset if not excluded else dataset.ablate(*sorted(excluded, key=lambda item: item.value))


def _forbidden_feature_hits(payload: Mapping[str, Any]) -> list[str]:
    hits = []
    for row_index, row in enumerate(payload.get("rows", ())):
        vector = row.get("vector", {})
        stack = [(f"$.rows[{row_index}].vector.features", vector.get("features", {}))]
        while stack:
            prefix, value = stack.pop()
            if isinstance(value, Mapping):
                for key, child in value.items():
                    path = f"{prefix}.{key}"
                    if str(key).lower() in FORBIDDEN_SCIENTIFIC_INPUT_KEYS | {"actual_services"}:
                        hits.append(path)
                    stack.append((path, child))
            elif isinstance(value, list):
                stack.extend((f"{prefix}[{index}]", child)
                             for index, child in enumerate(value))
    return hits


def validate_catalog(path: str | Path, *, repository_root: str | Path,
                     expected_model_specification_hash: str | None = None) -> dict[str, Any]:
    catalog_path, repo = Path(path).resolve(), Path(repository_root).resolve()
    payload = _read_json(catalog_path)
    if payload.get("schema_version") != CATALOG_VERSION:
        raise ValueError("Scientific benchmark catalog version inválida.")
    if expected_model_specification_hash and payload.get(
            "model_specification_hash") != expected_model_specification_hash:
        raise ValueError("MODEL_SPECIFICATION_HASH_MISMATCH")
    keys, checked, hits = set(), 0, []
    validated_baseline_references: set[Path] = set()
    required = {
        "source_dataset", "source_system", "feature_view", "protocol", "held_out",
        "seed", "path", "dataset_sha256", "feature_view_sha256", "split_sha256",
        "train_incident_ids_sha256", "validation_incident_ids_sha256",
        "test_incident_ids_sha256", "feature_schema_sha256", "preprocessing_version",
    }
    for item in payload.get("datasets", ()):
        if not required <= set(item):
            raise ValueError("Scientific benchmark catalog entry incompleta.")
        key = tuple(item[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed"))
        if key in keys:
            raise ValueError("Scientific benchmark catalog contém chave duplicada.")
        keys.add(key)
        dataset_path = (repo / item["path"]).resolve()
        if dataset_path != repo and repo not in dataset_path.parents:
            raise ValueError("Scientific benchmark dataset escapa do repositório.")
        if not dataset_path.is_file():
            raise ValueError(f"SCIENTIFIC_BENCHMARK_DATASET_NOT_FOUND:{dataset_path}")
        if file_sha256(dataset_path) != item["dataset_sha256"]:
            raise ValueError("SCIENTIFIC_BENCHMARK_DATASET_HASH_MISMATCH")
        dataset_payload = _read_json(dataset_path)
        dataset = BenchmarkDataset.from_json(dataset_path)
        manifest = dataset_payload["manifest"]
        if manifest["feature_view_sha256"] != item["feature_view_sha256"]:
            raise ValueError("SCIENTIFIC_FEATURE_VIEW_HASH_MISMATCH")
        splits = manifest["split_incident_ids"]
        expected_split = sha256_value({name: sorted(splits[name])
                                       for name in ("train", "validation", "test")})
        if expected_split != item["split_sha256"]:
            raise ValueError("SCIENTIFIC_SPLIT_BINDING_FAIL")
        for name in ("train", "validation", "test"):
            if _sha_ids(splits[name]) != item[f"{name}_incident_ids_sha256"]:
                raise ValueError("SCIENTIFIC_SPLIT_IDS_HASH_MISMATCH")
        tranche_fields = {
            "scientific_tranche_id": "scientific_tranche_id",
            "scientific_tranche_sha256": "scientific_tranche_sha256",
            "parent_split_sha256": "parent_split_sha256",
            "parent_base_key": "parent_base_key",
        }
        if item.get("scientific_tranche_id") is not None:
            for field in tranche_fields:
                if payload.get(field) != item.get(field):
                    raise ValueError("SCIENTIFIC_TRANCHE_CATALOG_BINDING_MISMATCH")
            if manifest.get("baseline_binding_policy") != "frozen-parent-train":
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_POLICY_MISSING")
            provenance = manifest.get("baseline_binding_provenance", {})
            for entry_field, provenance_field in tranche_fields.items():
                if item.get(entry_field) != provenance.get(provenance_field):
                    raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_PROVENANCE_MISMATCH")
            if manifest.get("baseline_sha256") != provenance.get(
                    "official_parent_baseline_sha256"):
                raise ValueError("SCIENTIFIC_TRANCHE_OFFICIAL_BASELINE_MISMATCH")
            if manifest.get("baseline_json") is not None:
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_JSON_MUST_NOT_BE_EMBEDDED")
            reference = manifest.get("baseline_reference", {})
            relative = reference.get("path")
            if not isinstance(relative, str) or not relative.strip():
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_REFERENCE_MISSING")
            reference_path = (repo / relative).resolve()
            if reference_path != repo and repo not in reference_path.parents:
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_REFERENCE_OUTSIDE_REPOSITORY")
            if not reference_path.is_file():
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_REFERENCE_NOT_FOUND")
            expected_reference = {
                "baseline_sha256": provenance.get("official_parent_baseline_sha256"),
                "parent_base_key": provenance.get("parent_base_key"),
                "parent_split_sha256": provenance.get("parent_split_sha256"),
                "parent_training_incident_ids_sha256": provenance.get(
                    "parent_training_incident_ids_sha256"),
                "parent_training_incident_count": provenance.get(
                    "parent_training_incident_count"),
                "preprocessing_version": PREPROCESSING_VERSION,
            }
            if any(reference.get(name) != value for name, value in expected_reference.items()):
                raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_REFERENCE_MISMATCH")
            if reference_path not in validated_baseline_references:
                snapshot = _read_json(reference_path)
                snapshot_ids = tuple(snapshot.get("training_incident_ids", ()))
                if snapshot.get("status") != "COMPLETED" \
                        or snapshot.get("preprocessing_version") != PREPROCESSING_VERSION:
                    raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_SNAPSHOT_STATUS_MISMATCH")
                if snapshot.get("baseline_sha256") != reference["baseline_sha256"] \
                        or snapshot.get("base_key") != reference["parent_base_key"] \
                        or snapshot.get("split_sha256") != reference["parent_split_sha256"]:
                    raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_SNAPSHOT_BINDING_MISMATCH")
                if len(snapshot_ids) != reference["parent_training_incident_count"] \
                        or _sha_ids(snapshot_ids) != reference[
                            "parent_training_incident_ids_sha256"]:
                    raise ValueError("SCIENTIFIC_TRANCHE_BASELINE_SNAPSHOT_TRAINING_MISMATCH")
                validated_baseline_references.add(reference_path)
                del snapshot, snapshot_ids
                gc.collect()
        if sha256_value(dict(dataset.feature_schema)) != item["feature_schema_sha256"]:
            raise ValueError("SCIENTIFIC_FEATURE_SCHEMA_HASH_MISMATCH")
        found = _forbidden_feature_hits(dataset_payload)
        hits.extend(found)
        if found:
            raise ValueError(f"SCIENTIFIC_INPUT_LEAKAGE:{found}")
        checked += 1
    if not checked:
        raise ValueError("Scientific benchmark catalog está vazio.")
    return {"valid": True, "datasets_checked": checked, "keys": len(keys),
            "forbidden_hits": hits}


def _write_sha256s(root: Path) -> None:
    files = sorted(path for path in root.rglob("*")
                   if path.is_file() and path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "\n".join(f"{file_sha256(path)}  {path.relative_to(root)}" for path in files) + "\n",
        encoding="utf-8",
    )


def audit_existing_artifacts(output: str | Path) -> dict[str, Any]:
    """Validate resume candidates without trusting filenames or partial JSON."""
    root = Path(output).resolve()
    valid, invalid = [], []
    for target in sorted((root / "datasets").glob("*.json")):
        key = target.stem
        manifest_path = root / "manifests" / target.name
        try:
            payload = _read_json(target)
            BenchmarkDataset.from_json(target)
            if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
                raise ValueError("cache key inválida")
            if not manifest_path.is_file():
                raise ValueError("manifest ausente")
            if _read_json(manifest_path) != payload["manifest"]:
                raise ValueError("manifest divergente")
            valid.append({"cache_key": key, "dataset_sha256": file_sha256(target),
                          "manifest_sha256": file_sha256(manifest_path)})
        except (KeyError, TypeError, ValueError) as exc:
            invalid.append({"cache_key": key, "reason": str(exc)})
    return {"valid": len(valid), "invalid": len(invalid),
            "valid_artifacts": valid, "invalid_artifacts": invalid}


def materialize(*, registry: FrozenDatasetRegistry, config_dir: str | Path,
                output: str | Path, model_specification: str | Path,
                rcaeval_cache: str | Path | None = None, resume: bool = False,
                max_new_artifacts: int | None = None,
                scientific_tranche: str | Path | None = None) -> dict[str, Any]:
    """Materialize all unique planner-bound BenchmarkDatasets; never train a model."""
    output, config_dir = Path(output).resolve(), Path(config_dir).resolve()
    if max_new_artifacts is not None and (
        isinstance(max_new_artifacts, bool) or max_new_artifacts < 0
    ):
        raise ValueError("max_new_artifacts deve ser inteiro não negativo.")
    before = registry.immutability_snapshot()
    preflight = registry.preflight()
    if not preflight["valid"]:
        raise RuntimeError("DATASET_PREFLIGHT_FAIL")
    spec_hash = model_specification_hash(model_specification)
    configs = [load_campaign_config(config_dir / name)
               for name in ("h1a.json", "h1b.json", "h1c.json", "h2.json")]
    if any(item["model_hash"] != spec_hash for item in configs):
        raise RuntimeError("MODEL_SPECIFICATION_CONFIG_BINDING_FAIL")
    if "rcaeval" in registry.datasets and rcaeval_cache is None:
        output.mkdir(parents=True, exist_ok=True)
        _atomic_json(output / "preprocessing-report.json", {
            "schema_version": "scientific-preprocessing-report-v3",
            "status": "NOT_READY", "reason": "RCAEVAL_NORMALIZED_CACHE_NOT_AVAILABLE",
            "new_artifacts": 0, "runtime_predictions_used": False,
            "final_models_trained": False, "final_inference_executed": False,
            "frozen_dataset_immutability": "PASS",
        })
        _write_sha256s(output)
        raise RuntimeError("RCAEVAL_NORMALIZED_CACHE_NOT_AVAILABLE")
    # Top-1 RCA excludes healthy controls.  Their null ground truth remains frozen
    # and is not converted into a fictional positive service.
    capabilities = tuple(item for item in registry.capabilities()
                         if item.fault_type != "healthy")
    planner = __import__("experiments.multimodal_v3.core", fromlist=["ExperimentPlannerV3"])
    config = build_experiment_config(configs[0], capabilities)
    experiment_planner = planner.ExperimentPlannerV3(config)
    assignments = []
    for protocol in config.protocols:
        for seed in config.seeds:
            values, _ = experiment_planner.protocol_assignments(protocol, seed)
            assignments.extend((protocol, seed, item) for item in values if item.applicable)
    views = ("FULL", *H1B_VIEWS, *H1C_VIEWS)
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=rcaeval_cache)
    finalization_started = time.perf_counter()
    finalization_telemetry = output / "finalization-telemetry.json"
    tranche_payload = None
    tranche_parent_splits = None
    official_baseline_snapshot = None
    official_baseline_snapshot_path = None
    if scientific_tranche is not None:
        from .scientific_tranche import load_manifest, validate_manifest
        tranche_payload = load_manifest(scientific_tranche)
        parent_matches = []
        for protocol, seed, assignment in assignments:
            parent_splits = {"train": assignment.train_incident_ids,
                             "validation": assignment.validation_incident_ids,
                             "test": assignment.test_incident_ids}
            parent_split_key, parent_base_key, _ = build_preprocessing_keys(
                registry=registry, sources=loader.sources, splits=parent_splits,
                protocol=protocol, held_out=assignment.held_out, seed=seed)
            if (parent_split_key == tranche_payload.get("parent_split_key")
                    and parent_base_key == tranche_payload.get("parent_base_key")):
                parent_matches.append((protocol, seed, assignment, parent_splits))
        if not parent_matches:
            raise ValueError("SCIENTIFIC_TRANCHE_PARENT_BINDING_NOT_FOUND")
        protocol, seed, _, tranche_parent_splits = parent_matches[0]
        validate_manifest(
            tranche_payload, parent_splits=tranche_parent_splits, protocol=protocol,
            seed=seed, parent_base_key=tranche_payload["parent_base_key"],
            parent_split_key=tranche_payload["parent_split_key"],
            expected_feature_registry_sha256=FeatureRegistryV3().fingerprint,
            expected_preprocessing_version=PREPROCESSING_VERSION)
        baseline_path = (registry.repository_root / "artifacts/final-experiments-v3/scientific-inputs"
                         / "work" / tranche_payload["parent_base_key"] / "baseline/snapshot.json")
        official_baseline_snapshot_path = baseline_path
    entries, new_artifacts, reused_artifacts = [], 0, 0
    artifact_cache = {}
    base_dataset_cache = {}
    stopped_early = False
    for protocol, seed, assignment in assignments:
        capability = assignment.capability
        splits = {"train": assignment.train_incident_ids,
                  "validation": assignment.validation_incident_ids,
                  "test": assignment.test_incident_ids}
        split_key, base_key, incident_ids = build_preprocessing_keys(
            registry=registry, sources=loader.sources, splits=splits,
            protocol=protocol, held_out=assignment.held_out, seed=seed,
        )
        if tranche_payload is not None:
            if (split_key != tranche_payload["parent_split_key"]
                    or base_key != tranche_payload["parent_base_key"]):
                continue
            splits = {name: tuple(tranche_payload[f"{name}_incident_ids"])
                      for name in ("train", "validation", "test")}
            incident_ids = tuple(tranche_payload["incident_ids"])
            # Isolate all checkpoints and datasets from their full-campaign
            # counterparts while preserving the parent binding in provenance.
            base_key = sha256_value({"parent_base_key": base_key,
                                     "scientific_tranche_sha256": tranche_payload["selection_sha256"]})
            # Backward-compatible operational binding for the already frozen
            # incident checkpoints.  Do not expose this as catalog split_sha256.
            split_key = tranche_payload["selection_sha256"]
        effective_split_key = effective_split_sha256(splits)
        manifest_hashes = sorted(
            loader.sources[item].manifest_sha256 for item in incident_ids)
        view_targets = []
        for view in views:
            required = set(experiment_planner.MODALITY_VIEWS.get(view, ()))
            if view == "GRAPH_BUSINESS":
                required = {"business_event"}
            if not required <= set(capability.available_modalities):
                continue
            view_object = _feature_view(view)
            cache_key = sha256_value({
                "dataset_fingerprints": registry.snapshot()["registry_sha256"],
                "manifest_hashes": manifest_hashes,
                "feature_view_hash": view_object.fingerprint, "protocol": protocol,
                "held_out": assignment.held_out, "seed": seed, "split_hash": split_key,
                "feature_registry_hash": FeatureRegistryV3().fingerprint,
                "preprocessing_version": PREPROCESSING_VERSION,
            })
            relative = Path("datasets") / f"{cache_key}.json"
            target = output / relative
            view_targets.append((view, cache_key, relative, target))

        missing = []
        for _, cache_key, _, target in view_targets:
            if cache_key not in artifact_cache:
                valid_existing = False
                if resume and target.is_file():
                    try:
                        payload = _read_json(target)
                        BenchmarkDataset.from_json(target)
                        manifest_path = output / "manifests" / target.name
                        if (not manifest_path.is_file()
                                or _read_json(manifest_path) != payload["manifest"]):
                            raise ValueError("SCIENTIFIC_RESUME_MANIFEST_MISMATCH")
                        valid_existing = True
                    except (KeyError, TypeError, ValueError):
                        valid_existing = False
                if valid_existing:
                    reused_artifacts += 1
                    dataset_payload = payload
                    _record_finalization_phase(
                        finalization_telemetry, "BENCHMARK_VALIDATION_START",
                        started=finalization_started, cache_key=cache_key)
                    benchmark = BenchmarkDataset.from_json(target)
                    _record_finalization_phase(
                        finalization_telemetry, "BENCHMARK_VALIDATION_DONE",
                        started=finalization_started, cache_key=cache_key)
                    manifest = dataset_payload["manifest"]
                    artifact_cache[cache_key] = (target, benchmark, manifest)
                else:
                    missing.append(cache_key)

        if missing:
            if max_new_artifacts is not None and new_artifacts >= max_new_artifacts:
                stopped_early = True
                break
            if base_key not in base_dataset_cache:
                generated = _materialize_base_dataset_crash_safe(
                    loader=loader, registry=registry, rcaeval_cache=rcaeval_cache,
                    splits=splits, seed=seed, base_key=base_key, split_key=split_key,
                    work_root=output / "work", resume=resume,
                    official_baseline_snapshot=official_baseline_snapshot,
                    official_baseline_snapshot_path=official_baseline_snapshot_path,
                    scientific_tranche_binding=tranche_payload)
                max_live = generated.checkpoint_report.get(
                    "max_live_full_feature_inputs", generated.lifecycle.max_live
                ) if generated.checkpoint_report else generated.lifecycle.max_live
                if max_live > 1:
                    raise RuntimeError("FEATURE_INPUT_MEMORY_BOUND_EXCEEDED")
                base_dataset_cache[base_key] = generated.dataset
                _record_finalization_phase(
                    finalization_telemetry, "DATASET_OBJECT_READY",
                    started=finalization_started, cache_key=base_key)
            base_scientific = base_dataset_cache[base_key]
            for view, cache_key, _, target in view_targets:
                if cache_key in artifact_cache:
                    continue
                if max_new_artifacts is not None and new_artifacts >= max_new_artifacts:
                    stopped_early = True
                    break
                scientific = _apply_view(base_scientific, view)
                _record_finalization_phase(
                    finalization_telemetry, "VIEW_SERIALIZATION_START",
                    started=finalization_started, view=view, cache_key=cache_key)
                _atomic_text(target, scientific.to_json() + "\n")
                manifest = scientific.manifest.to_dict()
                _atomic_json(output / "manifests" / f"{cache_key}.json", manifest)
                _record_finalization_phase(
                    finalization_telemetry, "VIEW_SERIALIZATION_DONE",
                    started=finalization_started, view=view, cache_key=cache_key)
                _record_finalization_phase(
                    finalization_telemetry, "BENCHMARK_VALIDATION_START",
                    started=finalization_started, view=view, cache_key=cache_key)
                benchmark = BenchmarkDataset.from_json(target)
                _record_finalization_phase(
                    finalization_telemetry, "BENCHMARK_VALIDATION_DONE",
                    started=finalization_started, view=view, cache_key=cache_key)
                artifact_cache[cache_key] = (target, benchmark, manifest)
                new_artifacts += 1

        for view, cache_key, relative, _ in view_targets:
            if cache_key not in artifact_cache:
                continue
            target, benchmark, manifest = artifact_cache[cache_key]
            try:
                catalog_path = str(target.relative_to(registry.repository_root))
            except ValueError:
                # Controlled/golden runs may live in /tmp and never promote a
                # final catalog; the normal final output remains repository-bound.
                catalog_path = str(target)
            entry = {
                "source_dataset": capability.source_dataset,
                "source_system": capability.source_system,
                "feature_view": view, "protocol": protocol,
                "held_out": assignment.held_out, "seed": seed,
                "path": catalog_path,
                "dataset_sha256": file_sha256(target),
                "benchmark_dataset_sha256": benchmark.dataset_hash,
                "feature_view_sha256": manifest["feature_view_sha256"],
                "split_sha256": effective_split_key,
                "train_incident_ids_sha256": _sha_ids(splits["train"]),
                "validation_incident_ids_sha256": _sha_ids(splits["validation"]),
                "test_incident_ids_sha256": _sha_ids(splits["test"]),
                "feature_schema_sha256": sha256_value(dict(benchmark.feature_schema)),
                "preprocessing_version": PREPROCESSING_VERSION,
                "cache_key": cache_key,
            }
            if tranche_payload is not None:
                entry.update({
                    "scientific_tranche_id": tranche_payload["tranche_id"],
                    "scientific_tranche_sha256": tranche_payload["selection_sha256"],
                    "parent_split_sha256": tranche_payload["parent_split_key"],
                    "parent_base_key": tranche_payload["parent_base_key"],
                })
            entries.append(entry)
        if stopped_early:
            break

    if stopped_early:
        after = registry.immutability_snapshot()
        if before["snapshot_sha256"] != after["snapshot_sha256"]:
            raise RuntimeError("FROZEN_DATASET_IMMUTABILITY_FAIL")
        report = {
            "schema_version": "scientific-preprocessing-report-v3",
            "status": "IN_PROGRESS", "reason": "CONTROLLED_ARTIFACT_LIMIT",
            "new_artifacts": new_artifacts, "reused_artifacts": reused_artifacts,
            "valid_existing": audit_existing_artifacts(output)["valid"],
            "runtime_predictions_used": False, "final_models_trained": False,
            "final_inference_executed": False,
            "base_dataset_cache_entries": len(base_dataset_cache), "artifact_cache_entries": len(artifact_cache),
            "parent_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "frozen_dataset_immutability": "PASS",
        }
        _atomic_json(output / "preprocessing-report.json", report)
        _write_sha256s(output)
        return report
    unique = {}
    for entry in entries:
        key = tuple(entry[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed"))
        if key in unique and unique[key] != entry:
            raise RuntimeError("SCIENTIFIC_BENCHMARK_CATALOG_DUPLICATE_KEY")
        unique[key] = entry
    catalog = {
        "schema_version": CATALOG_VERSION, "preprocessing_version": PREPROCESSING_VERSION,
        "model_specification_hash": spec_hash, "bootstrap_iterations": 1000,
        "healthy_policy": {"top1_eligible": False, "excluded_incidents": 100,
                           "reason": "null root_cause_service; abstention-only control"},
        "datasets": [unique[key] for key in sorted(unique, key=repr)],
    }
    if tranche_payload is not None:
        catalog.update({
            "scientific_tranche_id": tranche_payload["tranche_id"],
            "scientific_tranche_sha256": tranche_payload["selection_sha256"],
            "parent_split_sha256": tranche_payload["parent_split_key"],
            "parent_base_key": tranche_payload["parent_base_key"],
        })
    output.mkdir(parents=True, exist_ok=True)
    _record_finalization_phase(
        finalization_telemetry, "CATALOG_FINALIZE", started=finalization_started)
    _atomic_json(output / "benchmark-catalog.json", catalog)
    audit = validate_catalog(output / "benchmark-catalog.json",
                             repository_root=registry.repository_root,
                             expected_model_specification_hash=spec_hash)
    _atomic_json(output / "anti-leakage-audit.json", {
        "schema_version": "scientific-input-anti-leakage-v1", "status": "PASS",
        "datasets_checked": audit["datasets_checked"],
        "feature_columns_checked": len(FeatureRegistryV3().names), "forbidden_hits": [],
    })
    after = registry.immutability_snapshot()
    if before["snapshot_sha256"] != after["snapshot_sha256"]:
        raise RuntimeError("FROZEN_DATASET_IMMUTABILITY_FAIL")
    report = {
        "schema_version": "scientific-preprocessing-report-v3", "status": "PASS",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "catalog_entries": len(unique), "new_artifacts": new_artifacts,
        "reused_artifacts": reused_artifacts, "runtime_predictions_used": False,
        "final_models_trained": False, "final_inference_executed": False,
        "base_dataset_cache_entries": len(base_dataset_cache), "artifact_cache_entries": len(artifact_cache),
        "parent_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "frozen_dataset_immutability": "PASS",
    }
    _atomic_json(output / "preprocessing-report.json", report)
    _atomic_json(output / "scientific-model-specification.json",
                 _read_json(Path(model_specification)))
    _write_sha256s(output)
    return report
