"""Auditable providers backed by scientific JSON manifests, never callable bundles."""

from __future__ import annotations

import json
import os
import bisect
import hashlib
import re
import mmap
import time
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from models.evidence import EvidenceDirection, EvidenceItem, EvidenceType
from models.rca_result import RCAResult, RankedService, ReliabilityDecision, ScoreBreakdown
from multimodal_v1.benchmark_v3 import load_verified_artifact
from multimodal_v1.benchmark_v3.core import (
    evaluate_observation_rankings,
    positive_scores,
)
from multimodal_v1.degradation_v3 import ObservationDataset
from multimodal_v1.features_v3 import (
    BaselineSnapshotV3, BaselineStatistic, FeatureRegistryV3, MetricIdentityV3,
    MetricSelectionV3, RCAFeatureExtractorV3,
)
from multimodal_v1.features_v3.dataset import baseline_binding_sha256

from experiments.multimodal_v3.core import file_sha256, sha256_value

from .preprocessing import PREPROCESSING_VERSION, FrozenEvidenceLoader, IncidentSource
from ranking.reliability_gate import ReliabilityGate


H4_RELIABILITY_ADAPTER_VERSION = "h4-scientific-ranking-evidence-adapter-v1"
H4_LATENCY_BOUNDARY = (
    "degraded-observation-to-feature-extraction-to-cached-model-scoring-ranking-"
    "to-reliability-decision;verified-model-deserialization-excluded"
)


def evaluate_h4_ranking(incident_id: str, ranked, truth) -> dict[str, float]:
    """Delegate H4 ranking metrics, including candidate-level FPR, to benchmark-v3."""
    truth = frozenset(truth)
    observation = {
        "execution_status": "SUCCESS",
        "incident_id": incident_id,
        "ranked_services": [
            {"incident_id": incident_id, "service_name": item.service_name,
             "score": float(score), "is_root_cause": item.service_name in truth}
            for score, item in ranked
        ],
    }
    return evaluate_observation_rankings([observation])["metrics"]


def evaluate_h4_reliability(ranked, names) -> ReliabilityDecision:
    """Apply the frozen production gate to model ranking plus extracted-feature evidence.

    H4 uses the raw frozen-model score as its ranking score.  The adapter supplies two
    already-produced evidence sources for the leading candidate: the model prediction
    and the frozen FULL feature vector.  It does not derive labels or tune thresholds.
    """
    services, evidence = [], []
    for position, (score, vector) in enumerate(ranked, 1):
        value = min(max(float(score), 0.0), 1.0)
        model_id = f"h4-model-{position}"
        feature_id = f"h4-features-{position}"
        services.append(RankedService(position, vector.service_name, ScoreBreakdown(
            value, 0.0, 0.0, 0.0, 0.0, value,
            {"model_probability": 1.0}, "frozen-model-score-v1"),
            (model_id, feature_id)))
        evidence.extend((
            EvidenceItem(model_id, EvidenceType.MODEL, "PredictionPipeline",
                         "Frozen H4 model score.", service_name=vector.service_name,
                         metric_name="root_cause_probability", raw_value=value,
                         normalized_value=value, direction=EvidenceDirection.SUPPORTING),
            EvidenceItem(feature_id, EvidenceType.RANKING, "RCAFeatureExtractorV3",
                         "Frozen FULL feature vector used for H4 scoring.",
                         service_name=vector.service_name, metric_name="feature_vector",
                         raw_value={name: vector.values[name] for name in names},
                         direction=EvidenceDirection.SUPPORTING,
                         attributes={"feature_names": list(names)}),
        ))
    return ReliabilityGate().evaluate(tuple(services), tuple(evidence))


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Scientific provider JSON inválido: {path}") from exc


def _inside(repository_root: Path, relative: str) -> Path:
    target = (repository_root / relative).resolve()
    if target != repository_root and repository_root not in target.parents:
        raise ValueError("Scientific provider path escapa do repositório.")
    return target


def _rca_result(payload):
    ranking = tuple(RankedService(
        int(item["position"]), item["service_name"], ScoreBreakdown(**item["scores"]),
        tuple(item.get("evidence_ids", ())),
    ) for item in payload["ranking"])
    def timestamp(value):
        return (datetime.fromisoformat(value.replace("Z", "+00:00"))
                if isinstance(value, str) else value)
    evidence = tuple(EvidenceItem(
        evidence_id=item["evidence_id"], evidence_type=EvidenceType(item["evidence_type"]),
        source_component=item["source_component"], description=item["description"],
        service_name=item.get("service_name"), metric_name=item.get("metric_name"),
        raw_value=item.get("raw_value"), normalized_value=item.get("normalized_value"),
        unit=item.get("unit"), direction=EvidenceDirection(item.get("direction", "neutral")),
        observed_at=timestamp(item.get("observed_at")), attributes=item.get("attributes", {}),
        tags=tuple(item.get("tags", ())),
    ) for item in payload["evidence"])
    reliability = ReliabilityDecision(**{
        **payload["reliability"],
        "passed_rules": tuple(payload["reliability"].get("passed_rules", ())),
        "failed_rules": tuple(payload["reliability"].get("failed_rules", ())),
    })
    return RCAResult(
        payload["rca_result_id"], payload["trace_id"], payload.get("root_cause_service"),
        payload["confidence"], ranking, evidence, reliability, payload["model_version"],
        payload["pipeline_version"], datetime.fromisoformat(
            payload["generated_at"].replace("Z", "+00:00")), payload.get("schema_version", "1.0"),
        payload.get("metadata", {}),
    )


class FrozenRawTelemetryMapping(Mapping[str, tuple[Mapping[str, Any], ...]]):
    """Immutable, uncached view over frozen raw telemetry files."""

    def __init__(self, repository_root: str | Path, descriptors, *, verified=False) -> None:
        self._repository_root = Path(repository_root).resolve()
        self._descriptors = {
            item["incident_id"]: (item["raw_telemetry_path"], item["raw_telemetry_sha256"])
            for item in descriptors
        }
        self._verified = verified

    def __len__(self) -> int:
        return len(self._descriptors)

    def __iter__(self) -> Iterator[str]:
        return iter(self._descriptors)

    def __contains__(self, incident_id: object) -> bool:
        return incident_id in self._descriptors

    def __getitem__(self, incident_id: str) -> tuple[Mapping[str, Any], ...]:
        try:
            relative, expected_sha256 = self._descriptors[incident_id]
        except KeyError:
            raise KeyError(incident_id) from None
        path = _inside(self._repository_root, relative)
        if not path.is_file():
            raise ValueError("H3 raw telemetry congelada ausente.")
        if not self._verified and file_sha256(path) != expected_sha256:
            raise ValueError("H3 scientific RCA bundle checksum mismatch.")
        if path.stat().st_size < 64 * 1024 * 1024:
            payload = _read(path)
            if not isinstance(payload, list) or any(
                    not isinstance(item, Mapping) for item in payload):
                raise ValueError("H3 raw telemetry deve ser uma lista JSON de objetos.")
            return tuple(payload)
        # These files are canonical JSON emitted by h3_materializer: every
        # top-level object starts with the lexicographically first `attributes`
        # key and has a mandatory event_id. Scan canonical object bytes in C-backed
        # chunks and decode only the same 50 objects PromptBuilder would retain.
        selected, sequence = [], 0
        for event_id, raw_object in _iter_canonical_event_objects(path):
            key = (event_id, sequence)
            sequence += 1
            position = bisect.bisect_right([entry[0] for entry in selected], key)
            if position < 50:
                selected.insert(position, (key, raw_object))
                del selected[50:]
        result = tuple(json.loads(raw_object) for _, raw_object in selected)
        if any(not isinstance(item, Mapping) for item in result):
            raise ValueError("H3 raw telemetry deve ser uma lista JSON de objetos.")
        return result


_TOP_LEVEL_DELIMITER = b'},{"attributes":'
_EVENT_ID_BYTES = re.compile(br'"event_id":"([^"\\]+)"')


def _iter_canonical_event_objects(path: Path, chunk_size: int = 8 * 1024 * 1024):
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            last = data.rfind(b"]")
            if (data[:15] != b'[{"attributes":' or last < 1
                    or data[last + 1:].strip()):
                raise ValueError("H3 raw telemetry deve ser uma lista JSON de objetos.")
            start = 1
            while True:
                boundary = data.find(_TOP_LEVEL_DELIMITER, start)
                if boundary < 0:
                    raw_object = data[start:last]
                else:
                    raw_object = data[start:boundary + 1]
                match = _EVENT_ID_BYTES.search(raw_object)
                if match is None:
                    raise ValueError("H3 canonical raw telemetry sem event_id.")
                yield match.group(1).decode("utf-8"), raw_object
                if boundary < 0:
                    return
                # The delimiter includes the opening bytes of the next object.
                start = boundary + 2


def _iter_json_array(path: Path, chunk_size: int = 1024 * 1024):
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        buffer, position, eof, started = "", 0, False, False
        while True:
            if position > chunk_size:
                buffer, position = buffer[position:], 0
            while position >= len(buffer) and not eof:
                chunk = handle.read(chunk_size)
                eof = not chunk
                buffer += chunk
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if not started:
                if position >= len(buffer) and not eof:
                    continue
                if position >= len(buffer) or buffer[position] != "[":
                    raise ValueError("H3 raw telemetry deve ser uma lista JSON de objetos.")
                position += 1
                started = True
                continue
            while position < len(buffer) and (buffer[position].isspace() or buffer[position] == ","):
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                return
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if eof:
                    raise ValueError("H3 raw telemetry JSON truncado.")
                chunk = handle.read(chunk_size)
                eof = not chunk
                buffer += chunk
                continue
            position = end
            yield value


def load_h3_inputs(*, config, repository_root, expected_incident_ids=None,
                   expected_sample_sha256=None):
    """Load the post-H1/H2 RCA bundle and prove all three modes share one RCA."""
    relative = config.criteria.get("scientific_rca_results")
    path = _inside(Path(repository_root).resolve(), relative) if relative else None
    if path is None or not path.is_file():
        raise RuntimeError("H3_SCIENTIFIC_RCA_RESULTS_NOT_AVAILABLE")
    index = _read(path)
    if index.get("schema_version") != "scientific-rca-bundle-index-v3":
        raise ValueError("H3 scientific RCA bundle index inválido.")
    if (expected_incident_ids is None) != (expected_sample_sha256 is None):
        raise ValueError("H3 authoritative sample binding incompleto.")
    if expected_incident_ids is None:
        if config.datasets and all(
                item.fault_type is None and item.scenario is None
                and item.rca_correct is None and item.reliable is None
                for item in config.datasets):
            raise ValueError("H3_AUTHORITATIVE_SAMPLE_BINDING_REQUIRED")
        from experiments.multimodal_v3.core import ExperimentPlannerV3
        planner = ExperimentPlannerV3(config)
        expected = tuple(sorted({
            run.incident_id for run in planner.plan()
            if run.hypothesis == "H3" and run.experiment_family == "H3-EXPLANATIONS"
        }))
        expected_hash = sha256_value(list(expected))
    else:
        expected = tuple(expected_incident_ids)
        if expected != tuple(sorted(set(expected))) or len(expected) != 40:
            raise ValueError("H3 authoritative incident IDs inválidos.")
        expected_hash = sha256_value(list(expected))
        if expected_sample_sha256 != expected_hash:
            raise ValueError("H3 authoritative sample hash inválido.")
    indexed = tuple(item.get("incident_id") for item in index.get("incidents", ()))
    if len(expected) != 40 or indexed != expected:
        raise ValueError("H3 scientific RCA bundle diverge da amostra congelada.")
    if index.get("h3_sample_selection_sha256") != expected_hash:
        raise ValueError("H3 scientific RCA bundle sample selection hash mismatch.")
    rca_results = {}
    descriptors = tuple(index.get("incidents", ()))
    for item in descriptors:
        rca_path = _inside(Path(repository_root).resolve(), item["rca_result_path"])
        raw_path = _inside(Path(repository_root).resolve(), item["raw_telemetry_path"])
        if not rca_path.is_file() or not raw_path.is_file():
            raise ValueError("H3 scientific RCA bundle file ausente.")
        if file_sha256(rca_path) != item["rca_result_sha256"] \
                or file_sha256(raw_path) != item["raw_telemetry_sha256"]:
            raise ValueError("H3 scientific RCA bundle checksum mismatch.")
        payload = _read(rca_path)
        rca = _rca_result(payload)
        if sha256_value(rca.to_dict()) != item["rca_result_contract_sha256"]:
            raise ValueError("H3 RCAResult contract hash mismatch.")
        rca_results[item["incident_id"]] = rca
    raw_telemetry = FrozenRawTelemetryMapping(repository_root, descriptors, verified=True)
    if len(rca_results) != 40 or len(raw_telemetry) != 40:
        raise RuntimeError("H3_SCIENTIFIC_RCA_RESULTS_NOT_AVAILABLE")
    return {"rca_results": rca_results, "raw_telemetry": raw_telemetry}


def _baseline(payload):
    statistics = {key: BaselineStatistic(**value)
                  for key, value in payload["statistics"].items()}
    selections = {}
    for key, value in payload.get("metric_selections", {}).items():
        identity = MetricIdentityV3(
            value["service_name"], value["metric_kind"], value["metric_name"],
            value.get("metric_unit"), value.get("metric_aggregation"))
        selections[key] = MetricSelectionV3(identity, value["sample_count"], value["policy"])
    return BaselineSnapshotV3(
        statistics, {key: tuple(value) for key, value in payload["log_templates"].items()},
        tuple(payload["training_incident_ids"]), selections,
        payload["anomaly_policy_version"], payload["schema_version"],
    )


def _resolve_scientific_baseline(dataset_manifest: Mapping[str, Any],
                                 repository_root: str | Path) -> BaselineSnapshotV3:
    """Resolve and cryptographically verify an authoritative benchmark baseline."""
    policy = dataset_manifest.get("baseline_binding_policy", "split-train-exact")
    expected_sha256 = dataset_manifest.get("baseline_sha256")
    baseline_json = dataset_manifest.get("baseline_json")
    expected_parent_ids = None
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("H4 scientific baseline SHA inválido.")
    if policy == "split-train-exact":
        if not isinstance(baseline_json, str):
            raise ValueError("H4 split-train-exact requer baseline_json embutido.")
    elif policy == "frozen-parent-train":
        if baseline_json is not None:
            raise ValueError("H4 frozen-parent-train não aceita baseline_json embutido.")
        reference = dataset_manifest.get("baseline_reference")
        required = {"schema_version", "path", "baseline_sha256", "parent_base_key",
                    "parent_split_sha256", "parent_training_incident_ids_sha256",
                    "parent_training_incident_count", "preprocessing_version"}
        if (not isinstance(reference, Mapping) or set(reference) != required
                or reference.get("schema_version") != "frozen-parent-baseline-reference-v1"):
            raise ValueError("H4 frozen-parent baseline_reference inválida.")
        provenance = dataset_manifest.get("baseline_binding_provenance")
        provenance_required = {
            "official_parent_baseline_sha256", "parent_base_key", "parent_split_sha256",
            "scientific_tranche_id", "scientific_tranche_sha256",
            "parent_training_incident_ids_sha256", "parent_training_incident_count",
            "baseline_binding_sha256",
        }
        if not isinstance(provenance, Mapping) or set(provenance) != provenance_required:
            raise ValueError("H4 frozen-parent baseline provenance inválida.")
        expected_reference = {
            "baseline_sha256": provenance["official_parent_baseline_sha256"],
            "parent_base_key": provenance["parent_base_key"],
            "parent_split_sha256": provenance["parent_split_sha256"],
            "parent_training_incident_ids_sha256": provenance[
                "parent_training_incident_ids_sha256"],
            "parent_training_incident_count": provenance["parent_training_incident_count"],
            "preprocessing_version": PREPROCESSING_VERSION,
        }
        if any(reference.get(name) != value for name, value in expected_reference.items()):
            raise ValueError("H4 frozen-parent reference/provenance divergem.")
        if reference["baseline_sha256"] != expected_sha256:
            raise ValueError("H4 frozen-parent baseline SHA diverge do dataset.")
        parent_ids = dataset_manifest.get("baseline_parent_training_incident_ids")
        splits = dataset_manifest.get("split_incident_ids")
        if not isinstance(parent_ids, list) or not isinstance(splits, Mapping):
            raise ValueError("H4 frozen-parent training/split binding ausente.")
        expected_parent_ids = tuple(parent_ids)
        if (len(set(parent_ids)) != reference["parent_training_incident_count"]
                or sha256_value(sorted(parent_ids)) != reference[
                    "parent_training_incident_ids_sha256"]
                or not set(splits.get("train", ())) <= set(parent_ids)
                or (set(splits.get("validation", ())) | set(splits.get("test", ())))
                & set(parent_ids)):
            raise ValueError("H4 frozen-parent dataset training identity diverge.")
        if provenance["baseline_binding_sha256"] != baseline_binding_sha256(
                provenance, splits):
            raise ValueError("H4 frozen-parent canonical binding diverge.")
        relative = reference.get("path")
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("H4 frozen-parent baseline path ausente.")
        snapshot_path = _inside(Path(repository_root).resolve(), relative)
        if not snapshot_path.is_file():
            raise ValueError("H4 frozen-parent baseline snapshot ausente.")
        snapshot = _read(snapshot_path)
        training_ids = snapshot.get("training_incident_ids")
        if (snapshot.get("status") != "COMPLETED"
                or snapshot.get("preprocessing_version") != PREPROCESSING_VERSION):
            raise ValueError("H4 frozen-parent baseline snapshot inválido.")
        if (snapshot.get("baseline_sha256") != reference["baseline_sha256"]
                or snapshot.get("base_key") != reference["parent_base_key"]
                or snapshot.get("split_sha256") != reference["parent_split_sha256"]):
            raise ValueError("H4 frozen-parent snapshot binding diverge.")
        if (not isinstance(training_ids, list)
                or len(training_ids) != reference["parent_training_incident_count"]
                or sha256_value(sorted(training_ids)) != reference[
                    "parent_training_incident_ids_sha256"]):
            raise ValueError("H4 frozen-parent training identity diverge.")
        baseline_json = snapshot.get("baseline_json")
        if not isinstance(baseline_json, str):
            raise ValueError("H4 frozen-parent snapshot baseline_json inválido.")
    else:
        raise ValueError("H4 baseline_binding_policy desconhecida.")
    try:
        baseline_payload = json.loads(baseline_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("H4 scientific baseline_json inválido.") from exc
    if not isinstance(baseline_payload, Mapping) \
            or sha256_value(baseline_payload) != expected_sha256:
        raise ValueError("H4 scientific baseline payload hash mismatch.")
    if expected_parent_ids is not None and tuple(
            baseline_payload.get("training_incident_ids", ())) != expected_parent_ids:
        raise ValueError("H4 frozen-parent baseline payload training identity diverge.")
    return _baseline(baseline_payload)


class ScientificH4Evaluator:
    def __init__(self, entries, catalog, repository_root: Path):
        self.entries = entries
        self.catalog = catalog
        self.repository_root = repository_root
        self._models = {}
        self._metadata = {}
        self.maximum_metadata_cache_entries_seen = 0

    def _entry(self, run):
        key = (run.source_dataset, run.source_system, run.feature_view, run.protocol,
               run.held_out, run.seed, run.method)
        try:
            return self.entries[key]
        except KeyError as exc:
            raise RuntimeError(f"H4_SCIENTIFIC_MODEL_NOT_AVAILABLE:{key}") from exc

    def _catalog(self, run):
        key = (run.source_dataset, run.source_system, run.feature_view, run.protocol,
               run.held_out, run.seed)
        try:
            return self.catalog[key]
        except KeyError as exc:
            raise RuntimeError(f"H4_SCIENTIFIC_INPUT_NOT_AVAILABLE:{key}") from exc

    def _metadata_for(self, run):
        catalog = self._catalog(run)
        entry = self._entry(run)
        cache_key = (catalog["path"], entry["artifact_dir"])
        if cache_key in self._metadata:
            return self._metadata[cache_key]
        dataset_path = _inside(self.repository_root, catalog["path"])
        if not dataset_path.is_file() or file_sha256(dataset_path) != catalog.get("dataset_sha256"):
            raise ValueError("H4 scientific dataset hash mismatch.")
        dataset_payload = _read(dataset_path)
        manifest = dataset_payload.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("H4 scientific dataset manifest inválido.")
        baseline = _resolve_scientific_baseline(manifest, self.repository_root)
        feature_view_json = manifest.get("feature_view_json")
        if not isinstance(feature_view_json, str):
            raise ValueError("H4 feature_view_json ausente ou inválido.")
        try:
            feature_view = json.loads(feature_view_json)
        except json.JSONDecodeError as exc:
            raise ValueError("H4 feature_view_json malformado.") from exc
        features = feature_view.get("features") if isinstance(feature_view, Mapping) else None
        if (not isinstance(features, list) or not features
                or any(not isinstance(item, Mapping) or not isinstance(item.get("name"), str)
                       for item in features)):
            raise ValueError("H4 feature view sem features ordenadas válidas.")
        names = tuple(item["name"] for item in features)
        if len(names) != len(set(names)):
            raise ValueError("H4 feature view contém nomes duplicados.")
        feature_sha256 = hashlib.sha256(feature_view_json.encode()).hexdigest()
        expected_feature_sha256 = manifest.get("feature_view_sha256")
        artifact_path = _inside(self.repository_root, entry["artifact_dir"])
        verified = load_verified_artifact(artifact_path)
        if (feature_sha256 != expected_feature_sha256
                or expected_feature_sha256 != catalog.get("feature_view_sha256")
                or expected_feature_sha256 != entry.get("feature_schema_sha256")
                or expected_feature_sha256 != verified.manifest.get("feature_view_sha256")
                or names != tuple(verified.manifest.get("feature_names", ()))):
            raise ValueError("H4 feature view/artifact binding mismatch.")
        metadata = (baseline, names, manifest["baseline_sha256"], feature_sha256)
        self._metadata[cache_key] = metadata
        self.maximum_metadata_cache_entries_seen = max(
            self.maximum_metadata_cache_entries_seen, len(self._metadata))
        return metadata

    def _vectors(self, run, observation):
        baseline, names, _, _ = self._metadata_for(run)
        source = IncidentSource(run.incident_id, run.source_dataset, run.source_system,
                                Path("."), run.source_manifest_hash, None,
                                tuple(run.available_modalities))
        feature_input = FrozenEvidenceLoader.feature_input(source, observation.events)
        vectors = RCAFeatureExtractorV3().extract(
            feature_input.bundle, feature_input.graph, baselines=baseline,
            topology=observation.topology)
        return vectors, names

    def __call__(self, run, observation):
        entry = self._entry(run)
        artifact_path = _inside(self.repository_root, entry["artifact_dir"])
        verified = load_verified_artifact(artifact_path)
        if verified.manifest["model_sha256"] != entry["serialized_artifact_sha256"]:
            raise ValueError("H4 trained artifact hash mismatch.")
        cache_key = str(artifact_path)
        if cache_key not in self._models:
            self._models[cache_key] = joblib.load(
                artifact_path / verified.manifest["model_file"])
        started = time.perf_counter()
        vectors, names = self._vectors(run, observation)
        matrix = np.asarray([[np.nan if vector.values[name] is None else vector.values[name]
                              for name in names] for vector in vectors], dtype=float)
        scores = positive_scores(self._models[cache_key], matrix)
        ranked = sorted(zip(scores, vectors), key=lambda item: (-item[0], item[1].service_name))
        truth = set(observation.ground_truth[run.incident_id])
        canonical = evaluate_h4_ranking(run.incident_id, ranked, truth)
        reliability = evaluate_h4_reliability(ranked, names)
        processing_latency_ms = (time.perf_counter() - started) * 1000.0
        prediction = [{"service_name": item.service_name, "score": float(score)}
                      for score, item in ranked]
        return {
            "top_1": canonical["top_1"],
            "top_3": canonical["top_3"],
            "mrr": canonical["mrr"], "fpr": canonical["fpr"],
            "confidence_values": [float(ranked[0][0])],
            "outcomes": [bool(canonical["top_1"])],
            "abstentions": int(not reliability.is_reliable),
            "reliability": reliability.is_reliable,
            "reliability_decision": reliability.to_dict(),
            "reliability_policy": {
                "method": ReliabilityGate.METHOD_VERSION,
                "adapter": H4_RELIABILITY_ADAPTER_VERSION,
                "minimum_confidence": reliability.minimum_confidence,
                "minimum_margin": reliability.minimum_margin,
                "minimum_evidence_count": ReliabilityGate().minimum_evidence_count,
                "minimum_source_count": ReliabilityGate().minimum_source_count,
            },
            "processing_latency_ms": processing_latency_ms,
            "latency_boundary": H4_LATENCY_BOUNDARY,
            "predicted_ranking": prediction,
            "predicted_service": prediction[0]["service_name"],
            "ground_truth_service": next(iter(truth)) if len(truth) == 1 else None,
            "feature_hash": sha256_value([
                {"service": item.service_name,
                 "features": {name: item.values[name] for name in names}}
                for item in vectors]),
            "trained_model_artifact_hash": entry["serialized_artifact_sha256"],
        }

    def feature_hash(self, run, observation):
        vectors, names = self._vectors(run, observation)
        return sha256_value([
            {"service": item.service_name,
             "features": {name: item.values[name] for name in names}}
            for item in vectors])


def trained_artifact_index_path(*, config, repository_root: str | Path) -> Path:
    """Resolve the explicitly configured H4 model index, defaulting to full."""
    root = Path(repository_root).resolve()
    relative = config.criteria.get(
        "scientific_trained_artifact_index",
        "artifacts/final-experiments-v3/trained-artifacts/INDEX.json")
    return _inside(root, relative)


class FrozenH4ObservationMapping(Mapping[str, ObservationDataset]):
    """Read-only, one-incident view over the frozen H4 test observations."""

    def __init__(self, loader: FrozenEvidenceLoader,
                 sources: Mapping[str, IncidentSource]) -> None:
        self._loader = loader
        self._sources = dict(sources)
        self._cached_incident_id: str | None = None
        self._cached_observation: ObservationDataset | None = None
        self.telemetry_load_count = 0
        self.maximum_cached_incidents_seen = 0

    def __len__(self) -> int:
        return len(self._sources)

    def __iter__(self) -> Iterator[str]:
        return iter(self._sources)

    def __contains__(self, incident_id: object) -> bool:
        return incident_id in self._sources

    def __getitem__(self, incident_id: str) -> ObservationDataset:
        if incident_id not in self._sources:
            raise KeyError(incident_id)
        if incident_id == self._cached_incident_id:
            assert self._cached_observation is not None
            return self._cached_observation
        source = self._sources[incident_id]
        if source.root_cause_service is None:
            raise ValueError("H4 frozen root cause ausente.")
        events = self._loader.events(source)
        observation = ObservationDataset(
            events, {}, {incident_id: (source.root_cause_service,)})
        self.telemetry_load_count += 1
        self._cached_incident_id = incident_id
        self._cached_observation = observation
        self.maximum_cached_incidents_seen = max(
            self.maximum_cached_incidents_seen, len(self.current_cached_incidents))
        return observation

    @property
    def current_cached_incidents(self) -> tuple[str, ...]:
        return (() if self._cached_incident_id is None
                else (self._cached_incident_id,))


def load_h4_inputs(*, config, repository_root):
    """Resolve frozen observations and post-H1/H2 verified models explicitly."""
    repository_root = Path(repository_root).resolve()
    catalog_path = _inside(repository_root, config.criteria["scientific_benchmark_catalog"])
    artifact_index = trained_artifact_index_path(
        config=config, repository_root=repository_root)
    if not catalog_path.is_file():
        raise RuntimeError("SCIENTIFIC_BENCHMARK_CATALOG_NOT_FOUND")
    if not artifact_index.is_file():
        raise RuntimeError("H4_SCIENTIFIC_MODELS_NOT_AVAILABLE")
    catalog_payload, artifacts = _read(catalog_path), _read(artifact_index)
    tranche_fields = ("scientific_tranche_id", "scientific_tranche_sha256",
                      "parent_split_sha256", "parent_base_key")
    if any(field in catalog_payload or field in artifacts for field in tranche_fields):
        if any(catalog_payload.get(field) != artifacts.get(field) for field in tranche_fields):
            raise RuntimeError("H4_SCIENTIFIC_TRANCHE_ARTIFACT_BINDING_MISMATCH")
    catalog = {tuple(item[name] for name in (
        "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed")): item
        for item in catalog_payload["datasets"]}
    entries = {tuple(item[name] for name in (
        "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed", "method")): item
        for item in artifacts["artifacts"]}
    cache = os.environ.get("CAUSALODS_RCAEVAL_NORMALIZED_CACHE")
    tranche_relative = config.criteria.get("scientific_tranche")
    if not tranche_relative:
        raise RuntimeError("H4_SCIENTIFIC_TRANCHE_NOT_CONFIGURED")
    tranche = _read(_inside(repository_root, tranche_relative))
    selected_incidents = frozenset(tranche.get("test_incident_ids", ()))
    if len(selected_incidents) != 40:
        raise RuntimeError("H4_SCIENTIFIC_TRANCHE_TEST_BINDING_INVALID")
    registry = __import__("experiments.final_campaigns_v3.core", fromlist=[
        "FrozenDatasetRegistry"]).FrozenDatasetRegistry.load(
            repository_root / "experiments/configs/final-v3/datasets.json", repository_root)
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=cache)
    sources = {}
    for capability in config.datasets:
        if capability.incident_id not in selected_incidents:
            continue
        try:
            source = loader.sources[capability.incident_id]
        except KeyError:
            raise RuntimeError(
                "H4_SCIENTIFIC_TRANCHE_OBSERVATION_BINDING_MISMATCH") from None
        if source.root_cause_service is None:
            raise RuntimeError("H4_SCIENTIFIC_TRANCHE_ROOT_CAUSE_MISSING")
        sources[source.incident_id] = source
    if set(sources) != selected_incidents:
        raise RuntimeError("H4_SCIENTIFIC_TRANCHE_OBSERVATION_BINDING_MISMATCH")
    observations = FrozenH4ObservationMapping(loader, sources)
    evaluator = ScientificH4Evaluator(entries, catalog, repository_root)
    return {"observations": observations, "model_evaluator": evaluator,
            "feature_hasher": evaluator.feature_hash}
