"""Auditable providers backed by scientific JSON manifests, never callable bundles."""

from __future__ import annotations

import json
import os
import bisect
import re
import mmap
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
from multimodal_v1.benchmark_v3.core import positive_scores
from multimodal_v1.degradation_v3 import ObservationDataset
from multimodal_v1.features_v3 import (
    BaselineSnapshotV3, BaselineStatistic, FeatureRegistryV3, MetricIdentityV3,
    MetricSelectionV3, RCAFeatureExtractorV3,
)

from experiments.multimodal_v3.core import file_sha256, sha256_value

from .preprocessing import FrozenEvidenceLoader, IncidentSource


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


class ScientificH4Evaluator:
    def __init__(self, entries, catalog, repository_root: Path):
        self.entries = entries
        self.catalog = catalog
        self.repository_root = repository_root
        self._models = {}

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

    def _vectors(self, run, observation):
        catalog = self._catalog(run)
        dataset_payload = _read(_inside(self.repository_root, catalog["path"]))
        baseline = _baseline(json.loads(dataset_payload["manifest"]["baseline_json"]))
        source = IncidentSource(run.incident_id, run.source_dataset, run.source_system,
                                Path("."), run.source_manifest_hash, None,
                                tuple(run.available_modalities))
        feature_input = FrozenEvidenceLoader.feature_input(source, observation.events)
        vectors = RCAFeatureExtractorV3().extract(
            feature_input.bundle, feature_input.graph, baselines=baseline,
            topology=observation.topology)
        names = tuple(json.loads(dataset_payload["manifest"]["feature_view_json"])[
            "features"][index]["name"] for index in range(
                len(json.loads(dataset_payload["manifest"]["feature_view_json"])["features"])))
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
        vectors, names = self._vectors(run, observation)
        matrix = np.asarray([[np.nan if vector.values[name] is None else vector.values[name]
                              for name in names] for vector in vectors], dtype=float)
        scores = positive_scores(self._models[cache_key], matrix)
        ranked = sorted(zip(scores, vectors), key=lambda item: (-item[0], item[1].service_name))
        truth = set(observation.ground_truth[run.incident_id])
        first = min((index for index, (_, item) in enumerate(ranked, 1)
                     if item.service_name in truth), default=len(ranked) + 1)
        prediction = [{"service_name": item.service_name, "score": float(score)}
                      for score, item in ranked]
        return {
            "top_1": float(first == 1), "top_3": float(first <= 3),
            "mrr": 1 / first, "fpr": float(first != 1),
            "confidence_values": [float(ranked[0][0])], "outcomes": [first == 1],
            "abstentions": 0, "processing_latency_ms": 0.0,
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
    registry = __import__("experiments.final_campaigns_v3.core", fromlist=[
        "FrozenDatasetRegistry"]).FrozenDatasetRegistry.load(
            repository_root / "experiments/configs/final-v3/datasets.json", repository_root)
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=cache)
    observations = {}
    for capability in config.datasets:
        source = loader.sources[capability.incident_id]
        if source.root_cause_service is None:
            continue
        events = loader.events(source)
        observations[source.incident_id] = ObservationDataset(
            events, {}, {source.incident_id: (source.root_cause_service,)})
    evaluator = ScientificH4Evaluator(entries, catalog, repository_root)
    return {"observations": observations, "model_evaluator": evaluator,
            "feature_hasher": evaluator.feature_hash}
