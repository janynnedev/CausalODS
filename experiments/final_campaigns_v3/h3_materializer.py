"""Deterministic materialization of frozen RCA inputs consumed by final H3."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

import joblib
import numpy as np
from xgboost import DMatrix

from models.rca_context import RCAContext
from models.rca_result import RCAResult
from multimodal_v1.benchmark_v3 import load_verified_artifact
from multimodal_v1.benchmark_v3.core import positive_scores
from multimodal_v1.features_v3 import FeatureRegistryV3, RCAFeatureExtractorV3
from pipeline.rca_result_factory import RCAResultFactory

from experiments.multimodal_v3.core import canonical_json, file_sha256, sha256_value

from .core import FrozenDatasetRegistry, build_plan, load_campaign_config
from .preprocessing import FrozenEvidenceLoader
from .providers import _baseline


SCHEMA_VERSION = "scientific-rca-bundle-index-v3"
BUNDLE_VERSION = "h3-scientific-rca-bundle-v1"
PIPELINE_VERSION = "causalods-scientific-rca-materializer-v1"
FORBIDDEN_KEYS = frozenset({
    "ground_truth", "actual_services", "fault_service", "fault_location",
    "is_root_cause", "label_status", "root_cause_rank",
})


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"H3 scientific JSON inválido: {path}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _safe_id(incident_id: str) -> str:
    return hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:24]


def _forbidden_paths(value: Any, prefix: str = "$") -> tuple[str, ...]:
    found = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            if str(key).lower() in FORBIDDEN_KEYS:
                found.append(path)
            found.extend(_forbidden_paths(child, path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_forbidden_paths(child, f"{prefix}[{index}]"))
    return tuple(found)


def _repository_commit(repository_root: Path) -> str | None:
    try:
        return subprocess.run(
            ("git", "rev-parse", "HEAD"), cwd=repository_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def expected_h3_runs(config_path: Path, registry_path: Path, repository_root: Path):
    payload = load_campaign_config(config_path)
    if payload["family"] != "H3":
        raise ValueError("Materializador requer configuração H3.")
    registry = FrozenDatasetRegistry.load(registry_path, repository_root)
    config, runs = build_plan(payload, registry.capabilities())
    incident_ids = tuple(sorted({run.incident_id for run in runs}))
    if len(runs) != 120 or len(incident_ids) != 40:
        raise ValueError("Plano H3 congelado deve conter 120 runs e 40 incidentes.")
    if {run.method for run in runs} != {"xgboost-rca-v3"}:
        raise ValueError("H3 não possui método RCA único e explicitamente congelado.")
    return payload, registry, config, runs, incident_ids


def _select_model(index: Mapping[str, Any], *, model_hash: str,
                  repository_root: Path) -> tuple[Mapping[str, Any], Any, Any]:
    candidates = [item for item in index.get("artifacts", ())
                  if item.get("method") == "xgboost-rca-v3"
                  and item.get("feature_view") == "FULL"
                  and item.get("seed") == 42
                  and item.get("model_specification_hash") == model_hash]
    physical = {(item.get("artifact_dir"), item.get("serialized_artifact_sha256"))
                for item in candidates}
    if len(physical) != 1:
        raise RuntimeError("H3_FROZEN_XGBOOST_MODEL_AMBIGUOUS_OR_MISSING")
    artifact_dir, model_sha256 = next(iter(physical))
    artifact_path = (repository_root / artifact_dir).resolve()
    verified = load_verified_artifact(artifact_path)
    manifest = dict(verified.manifest)
    if (manifest.get("model_name") != "xgboost-rca-v3"
            or manifest.get("model_sha256") != model_sha256):
        raise ValueError("H3 frozen model artifact binding mismatch.")
    model = joblib.load(artifact_path / manifest["model_file"])
    return candidates[0], verified, model


def _shap(model: Any, vectors: Iterable[Any], names: tuple[str, ...]):
    rows = tuple(vectors)
    matrix = np.asarray([[np.nan if row.values[name] is None else row.values[name]
                          for name in names] for row in rows], dtype=float)
    transformed = model.named_steps["imputer"].transform(matrix)
    contributions = model.named_steps["model"].get_booster().predict(
        DMatrix(transformed), pred_contribs=True)
    result = {}
    for vector, feature_values, contribution in zip(
            rows, transformed, contributions, strict=True):
        values = np.asarray(contribution[:-1], dtype=float)
        positive = float(np.clip(values, 0, None).sum())
        negative = float(np.abs(np.clip(values, None, 0)).sum())
        total = positive + negative
        items = [{
            "feature_name": name, "feature_value": float(feature_value),
            "shap_value": float(shap_value),
            "direction": ("supporting" if shap_value > 0 else
                          "opposing" if shap_value < 0 else "neutral"),
        } for name, feature_value, shap_value in zip(
            names, feature_values, values, strict=True)]
        items.sort(key=lambda item: (-abs(item["shap_value"]), item["feature_name"]))
        result[vector.service_name] = {
            "support_score": positive / total if total else 0.0,
            "raw_positive_support": positive,
            "raw_negative_support": negative,
            "contributions": items,
            "method": "xgboost-pred-contribs-v1",
        }
    return result


def _score(value: Any) -> float:
    try:
        converted = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, converted)) if math.isfinite(converted) else 0.0


def _resolve_rca_context_id(bundle: Any, incident_id: str) -> tuple[str, str, str | None]:
    """Resolve the legacy RCAContext.trace_id field without fabricating OTel identity."""
    if bundle.traces:
        observed_trace_id = bundle.traces[0]
        return observed_trace_id, "observed_trace_id", observed_trace_id
    context_id = "scientific-context-" + sha256_value({
        "incident_id": incident_id,
        "workflow_key": bundle.workflow_key,
    })[:32]
    return context_id, "synthetic_scientific_context", None


def _build_rca(*, incident_id: str, feature_input: Any, baseline: Any,
               model: Any, model_sha256: str, feature_schema_sha256: str) -> RCAResult:
    vectors = RCAFeatureExtractorV3().extract(
        feature_input.bundle, feature_input.graph, baselines=baseline)
    names = FeatureRegistryV3().names
    matrix = np.asarray([[np.nan if vector.values[name] is None else vector.values[name]
                          for name in names] for vector in vectors], dtype=float)
    probabilities = positive_scores(model, matrix)
    predictions = {vector.service_name: {"probability": float(probability)}
                   for vector, probability in zip(vectors, probabilities, strict=True)}
    services = tuple(vector.service_name for vector in vectors)
    temporal, graph, anomaly = {}, {}, {}
    for vector in vectors:
        values = vector.values
        distance = values["distance_to_first_failure"]
        temporal[vector.service_name] = {
            "score": 1 / (1 + distance) if distance is not None and distance >= 0 else 0.0,
            "distance_to_first_failure": distance,
        }
        graph[vector.service_name] = {
            "score": max(_score(values["centrality"]), _score(
                (values["affected_descendant_count"] or 0) / max(1, len(services) - 1))),
            "centrality": values["centrality"],
        }
        anomaly[vector.service_name] = {"score": max(
            _score(values["error_span_ratio"]), float(bool(values["exception_present"])),
            float((values["log_error_count"] or 0) > 0),
            float((values["anomalous_metric_count"] or 0) > 0),
            float((values["business_failure_count"] or 0) > 0),
        )}
    context_id, identity_kind, observed_trace_id = _resolve_rca_context_id(
        feature_input.bundle, incident_id)
    context = RCAContext(
        trace_id=context_id, services=services,
        feature_vectors={vector.service_name: dict(vector.values) for vector in vectors},
        temporal_results=temporal, graph_results=graph, anomaly_results=anomaly,
        predictions=predictions, shap_results=_shap(model, vectors, names),
        model_version="xgboost-rca-v3",
        pipeline_version=PIPELINE_VERSION,
        adapter_metadata={"incident_id": incident_id, "materializer": BUNDLE_VERSION},
    )
    result = RCAResultFactory().build(context)
    deterministic = sha256_value({"incident_id": incident_id, "model": model_sha256,
                                  "pipeline": PIPELINE_VERSION})[:24]
    return replace(
        result, rca_result_id=f"rca-h3-{deterministic}",
        generated_at=feature_input.bundle.analysis_window.end,
        metadata={**dict(result.metadata), "incident_id": incident_id,
                  "model_artifact_sha256": model_sha256,
                  "feature_schema_sha256": feature_schema_sha256,
                  "context_identity_kind": identity_kind,
                  "observed_trace_id": observed_trace_id,
                  "workflow_key": feature_input.bundle.workflow_key,
                  "ground_truth_accessed": False},
    )


def _scientific_fingerprint(index: Mapping[str, Any]) -> str:
    return sha256_value({
        "schema_version": index["schema_version"],
        "bundle_version": index["bundle_version"],
        "incident_count": index["incident_count"],
        "h3_sample_selection_sha256": index["h3_sample_selection_sha256"],
        "model": index["model"], "feature_schema": index["feature_schema"],
        "dataset_split": index["dataset_split"],
        "incidents": index["incidents"],
    })


def validate_bundle(index_path: Path, *, expected_incidents: Iterable[str] | None = None,
                    repository_root: Path | None = None) -> Mapping[str, Any]:
    index_path = index_path.resolve()
    root = (repository_root or index_path.parents[3]).resolve()
    index = _read(index_path)
    if index.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("H3 bundle schema_version inválido.")
    incidents = index.get("incidents")
    ids = tuple(item.get("incident_id") for item in incidents or ())
    if index.get("incident_count") != 40 or len(ids) != 40 or ids != tuple(sorted(set(ids))):
        raise ValueError("H3 bundle deve conter exatamente 40 incidentes ordenados e únicos.")
    if expected_incidents is not None and ids != tuple(sorted(expected_incidents)):
        raise ValueError("H3 bundle diverge da amostra congelada.")
    for item in incidents:
        for path_key, hash_key in (("rca_result_path", "rca_result_sha256"),
                                   ("raw_telemetry_path", "raw_telemetry_sha256"),
                                   ("provenance_path", "provenance_sha256")):
            path = (root / item[path_key]).resolve()
            if root not in path.parents or not path.is_file() or file_sha256(path) != item[hash_key]:
                raise ValueError(f"H3 bundle checksum/path mismatch: {item['incident_id']}:{path_key}")
        rca = _read(root / item["rca_result_path"])
        raw = _read(root / item["raw_telemetry_path"])
        if _forbidden_paths(rca) or _forbidden_paths(raw):
            raise ValueError("H3 bundle contém campo de ground truth proibido.")
        from .providers import _rca_result
        parsed = _rca_result(rca)
        if sha256_value(parsed.to_dict()) != item["rca_result_contract_sha256"]:
            raise ValueError("H3 RCAResult contract hash mismatch.")
        if not isinstance(raw, list):
            raise ValueError("H3 raw telemetry deve ser uma lista JSON.")
    if index.get("scientific_bundle_fingerprint") != _scientific_fingerprint(index):
        raise ValueError("H3 scientific bundle fingerprint mismatch.")
    return index


def materialize_h3_inputs(*, config_path: Path, registry_path: Path, output: Path,
                          repository_root: Path, artifact_index_path: Path,
                          benchmark_catalog_path: Path, sample_manifest_path: Path,
                          rcaeval_cache: str | Path | None = None,
                          validate_only: bool = False) -> Mapping[str, Any]:
    repository_root = repository_root.resolve()
    payload, registry, config, runs, incident_ids = expected_h3_runs(
        config_path, registry_path, repository_root)
    sample = _read(sample_manifest_path)
    if (tuple(sample.get("incident_ids", ())) != incident_ids
            or sample.get("selection_sha256") != sha256_value(list(incident_ids))):
        raise ValueError("H3 sample manifest diverge do planner congelado.")
    if validate_only:
        return validate_bundle(output / "INDEX.json", expected_incidents=incident_ids,
                               repository_root=repository_root)
    if (output / "INDEX.json").exists():
        return validate_bundle(output / "INDEX.json", expected_incidents=incident_ids,
                               repository_root=repository_root)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Destino H3 não vazio e sem bundle idêntico verificável.")
    output = output.resolve()
    try:
        bundle_relative = output.relative_to(repository_root)
    except ValueError as exc:
        raise ValueError("Destino H3 deve permanecer dentro do repositório.") from exc

    artifact_index = _read(artifact_index_path)
    model_entry, verified, model = _select_model(
        artifact_index, model_hash=config.model_hash, repository_root=repository_root)
    catalog = _read(benchmark_catalog_path)
    full = [item for item in catalog.get("datasets", ()) if item.get("feature_view") == "FULL"]
    physical = {(item["path"], item["benchmark_dataset_sha256"]) for item in full}
    if len(physical) != 1:
        raise RuntimeError("H3_FULL_SCIENTIFIC_DATASET_AMBIGUOUS")
    dataset_path, dataset_sha256 = next(iter(physical))
    dataset_payload = _read(repository_root / dataset_path)
    dataset_manifest = dataset_payload["manifest"]
    baseline_json = dataset_manifest.get("baseline_json")
    if baseline_json is None:
        reference = dataset_manifest.get("baseline_reference", {})
        snapshot = _read(repository_root / reference["path"])
        if (snapshot.get("baseline_sha256") != reference.get("baseline_sha256")
                or snapshot.get("baseline_sha256") != dataset_manifest.get("baseline_sha256")):
            raise ValueError("H3 frozen baseline reference hash mismatch.")
        baseline_json = snapshot["baseline_json"]
    baseline_payload = json.loads(baseline_json)
    if sha256_value(baseline_payload) != dataset_manifest.get("baseline_sha256"):
        raise ValueError("H3 frozen baseline payload hash mismatch.")
    baseline = _baseline(baseline_payload)
    cache = rcaeval_cache or os.environ.get("CAUSALODS_RCAEVAL_NORMALIZED_CACHE")
    loader = FrozenEvidenceLoader(registry, rcaeval_cache=cache)
    for incident_id in incident_ids:
        source = loader.sources[incident_id]
        if source.dataset_id != "rcaeval":
            continue
        if loader.rcaeval_cache is None:
            raise RuntimeError("RCAEVAL_NORMALIZED_CACHE_NOT_AVAILABLE")
        manifest = _read(source.manifest_path)
        if loader.rcaeval_cache.inspect(
                key=manifest["cache_key"], adapter_version=manifest["adapter_version"],
                configuration_sha256=manifest["adapter_configuration_sha256"],
                source_files_sha256=manifest["source_files_sha256"]) is None:
            raise RuntimeError(
                f"RCAEVAL_NORMALIZED_CACHE_ENTRY_NOT_AVAILABLE:{incident_id}")
    staging = output.with_name(f".{output.name}.materializing")
    if staging.exists():
        raise FileExistsError(f"Staging H3 anterior requer auditoria manual: {staging}")
    staging.mkdir(parents=True)
    entries = []
    for incident_id in incident_ids:
        source = loader.sources[incident_id]
        events = loader.events(source)
        raw = [event.to_dict() for event in events]
        if _forbidden_paths(raw):
            raise ValueError(f"H3 raw telemetry leakage: {incident_id}")
        feature_input = FrozenEvidenceLoader.feature_input(source, events)
        rca = _build_rca(
            incident_id=incident_id, feature_input=feature_input, baseline=baseline,
            model=model, model_sha256=model_entry["serialized_artifact_sha256"],
            feature_schema_sha256=model_entry["feature_schema_sha256"])
        rca_payload = rca.to_dict()
        if _forbidden_paths(rca_payload):
            raise ValueError(f"H3 RCA inference leakage: {incident_id}")
        provenance = {
            "schema_version": "scientific-rca-provenance-v1",
            "incident_id": incident_id, "source_dataset": source.dataset_id,
            "source_system": source.system,
            "source_manifest_sha256": source.manifest_sha256,
            "h3_sample_selection_sha256": sample["selection_sha256"],
            "model_artifact_sha256": model_entry["serialized_artifact_sha256"],
            "benchmark_run_fingerprint": verified.integrity["run_fingerprint"],
            "feature_schema_sha256": model_entry["feature_schema_sha256"],
            "training_dataset_sha256": dataset_sha256,
            "model_retrained": False, "model_retuned": False,
            "ground_truth_accessed": False,
        }
        relative = bundle_relative / "incidents" / _safe_id(incident_id)
        files = {"rca-result.json": rca_payload, "raw-telemetry.json": raw,
                 "provenance.json": provenance}
        descriptor = {"incident_id": incident_id}
        for filename, value in files.items():
            target = staging / relative.relative_to(bundle_relative) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            content = _payload_bytes(value)
            target.write_bytes(content)
            stem = filename.removesuffix(".json").replace("-", "_")
            descriptor[f"{stem}_path"] = (relative / filename).as_posix()
            descriptor[f"{stem}_sha256"] = _sha256_bytes(content)
        descriptor["rca_result_contract_sha256"] = sha256_value(rca.to_dict())
        descriptor["model_artifact_sha256"] = model_entry["serialized_artifact_sha256"]
        entries.append(descriptor)
    index = {
        "schema_version": SCHEMA_VERSION, "bundle_version": BUNDLE_VERSION,
        "generated_at": "deterministic-from-frozen-inputs",
        "incident_count": len(entries), "incidents": entries,
        "h3_sample_selection_sha256": sample["selection_sha256"],
        "source_scientific_configuration_sha256": config.configuration_sha256,
        "source_commit": _repository_commit(repository_root),
        "model": {"method": "xgboost-rca-v3",
                  "artifact_sha256": model_entry["serialized_artifact_sha256"],
                  "benchmark_run_fingerprint": verified.integrity["run_fingerprint"]},
        "feature_schema": {"sha256": model_entry["feature_schema_sha256"],
                           "registry_sha256": FeatureRegistryV3().fingerprint},
        "dataset_split": {
            "training_dataset_sha256": dataset_sha256,
            "scientific_tranche_id": artifact_index.get("scientific_tranche_id"),
            "scientific_tranche_sha256": artifact_index.get("scientific_tranche_sha256"),
            "parent_split_sha256": artifact_index.get("parent_split_sha256"),
            "parent_base_key": artifact_index.get("parent_base_key"),
        },
    }
    index["scientific_bundle_fingerprint"] = _scientific_fingerprint(index)
    (staging / "INDEX.json").write_bytes(_payload_bytes(index))
    os.replace(staging, output)
    return validate_bundle(output / "INDEX.json", expected_incidents=incident_ids,
                           repository_root=repository_root)
