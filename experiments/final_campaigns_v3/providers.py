"""Auditable providers backed by scientific JSON manifests, never callable bundles."""

from __future__ import annotations

import json
import os
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

    def __init__(self, repository_root: str | Path, descriptors) -> None:
        self._repository_root = Path(repository_root).resolve()
        self._descriptors = {
            item["incident_id"]: (item["raw_telemetry_path"], item["raw_telemetry_sha256"])
            for item in descriptors
        }

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
        if file_sha256(path) != expected_sha256:
            raise ValueError("H3 scientific RCA bundle checksum mismatch.")
        payload = _read(path)
        if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
            raise ValueError("H3 raw telemetry deve ser uma lista JSON de objetos.")
        return tuple(payload)


def load_h3_inputs(*, config, repository_root):
    """Load the post-H1/H2 RCA bundle and prove all three modes share one RCA."""
    relative = config.criteria.get("scientific_rca_results")
    path = _inside(Path(repository_root).resolve(), relative) if relative else None
    if path is None or not path.is_file():
        raise RuntimeError("H3_SCIENTIFIC_RCA_RESULTS_NOT_AVAILABLE")
    index = _read(path)
    if index.get("schema_version") != "scientific-rca-bundle-index-v3":
        raise ValueError("H3 scientific RCA bundle index inválido.")
    from experiments.multimodal_v3.core import ExperimentPlannerV3
    planner = ExperimentPlannerV3(config)
    expected = tuple(sorted({
        run.incident_id for run in planner.plan()
        if run.hypothesis == "H3" and run.experiment_family == "H3-EXPLANATIONS"
    }))
    indexed = tuple(item.get("incident_id") for item in index.get("incidents", ()))
    if len(expected) != 40 or indexed != expected:
        raise ValueError("H3 scientific RCA bundle diverge da amostra congelada.")
    if index.get("h3_sample_selection_sha256") != sha256_value(list(expected)):
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
    raw_telemetry = FrozenRawTelemetryMapping(repository_root, descriptors)
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
