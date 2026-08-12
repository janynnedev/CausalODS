from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import platform
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import joblib
import numpy as np

from multimodal_v1.benchmark_v3 import (
    BENCHMARK_VERSION,
    BenchmarkDataset,
    BenchmarkRuntimePolicy,
    load_verified_artifact,
    train_and_evaluate,
)
from multimodal_v1.benchmark_v3.core import positive_scores
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.event_graph import EventGraphBuilder
from multimodal_v1.features_v3 import (
    FeatureInputV3,
    FeatureRegistryV3,
    IncidentDatasetGeneratorV3,
    RCAFeatureVectorV3,
    RCAServiceTargetV3,
)
from platform_v3.constants import ARTIFACT_ROOT
from platform_v3.fixtures import build_demo_scenario
from platform_v3.serialization import canonical_json


MODEL_NAME = "xgboost-rca-v3"
MODEL_VERSION = "xgboost-rca-v3-operational-1"
MODEL_PURPOSE = "runtime_e2e_validation"
MODEL_MANIFEST = "xgboost_rca_v3.manifest.json"
MODEL_MANIFEST_SCHEMA = "operational-model-manifest-v1"
TARGET_SCHEMA_VERSION = "rca-service-target-v3"
OPERATIONAL_FEATURE_VIEW_ID = "full-rca-features-v3"
DEFAULT_MODELS_ROOT = ARTIFACT_ROOT / "models"
TRAINING_SEED = 12012


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _within(root: Path, relative: str, field: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{field} deve ser caminho relativo não vazio.")
    root = root.resolve()
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise ValueError(f"{field} aponta para fora do diretório de modelos.")
    return target


def _event(
    *, incident_number: int, signal: str, service: str, offset: int,
    anomalous: bool, trace_id: str, root_span_id: str,
) -> UnifiedEvent:
    base = datetime(2026, 8, 12, 10, tzinfo=timezone.utc) + timedelta(minutes=incident_number)
    service_code = "order" if service == "order-service" else "payment"
    correlation_id = f"operational-training-{incident_number:03d}"
    event_id = f"operational-{incident_number:03d}-{service_code}-{signal}"
    values: dict[str, Any] = {
        "event_id": event_id,
        "event_name": f"{service}.{signal}",
        "event_type": signal,
        "signal_type": signal,
        "event_timestamp": base + timedelta(seconds=offset),
        "correlation_id": correlation_id,
        "service_name": service,
        "source_reference": f"operational-training:{event_id}",
        "source_dataset": "platform-v3-operational-training",
    }
    if signal == "trace":
        span_id = hashlib.sha256(event_id.encode()).hexdigest()[:16]
        values.update(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=root_span_id if service == "payment-service" else None,
            status_code="ERROR" if anomalous else "OK",
            error_type="InjectedOperationalTrainingFailure" if anomalous else None,
            # A duração é deliberadamente não discriminante: o artefato
            # operacional deve aprender evidência de falha/estrutura, não um
            # threshold artificial de latência da fixture de treino.
            attributes={"duration_ms": 20.0},
        )
    elif signal == "log":
        values.update(
            severity="ERROR" if anomalous else "INFO",
            error_type="InjectedOperationalTrainingFailure" if anomalous else None,
            attributes={"body": "request failed" if anomalous else "request completed"},
        )
    elif signal == "metric":
        values.update(
            metric_name="http.server.error.rate",
            metric_unit="1",
            metric_value=0.95 if anomalous else 0.01,
            metric_aggregation="gauge",
            attributes={"is_anomalous": anomalous},
        )
    else:
        values.update(
            business_entity_type="order",
            business_entity_id=correlation_id,
            business_operation="request",
            business_outcome="failed" if anomalous else "approved",
        )
    return UnifiedEvent(**values)


def _operational_training_inputs(count: int = 30) -> tuple[tuple[FeatureInputV3, ...], tuple[RCAServiceTargetV3, ...]]:
    if count < 12:
        raise ValueError("Treino operacional requer ao menos 12 incidentes controlados.")
    inputs: list[FeatureInputV3] = []
    targets: list[RCAServiceTargetV3] = []
    topology = {"order-service": ("payment-service",)}
    for number in range(count):
        root_service = "payment-service" if number % 2 else "order-service"
        trace_id = hashlib.md5(f"operational-{number}".encode()).hexdigest()
        root_span_id = hashlib.sha256(f"root-{number}".encode()).hexdigest()[:16]
        client_span_id = hashlib.sha256(f"client-{number}".encode()).hexdigest()[:16]
        payment_fault = root_service == "payment-service"
        # Semântica real do fault HTTP: no payment failure, o erro 503 aparece
        # no servidor payment e se propaga para client/root spans do order.
        root = _event(
            incident_number=number, signal="trace", service="order-service", offset=0,
            anomalous=True, trace_id=trace_id, root_span_id=root_span_id,
        )
        payload = root.to_dict()
        payload.update(span_id=root_span_id, event_name="POST /orders")
        root = UnifiedEvent.from_dict(payload)
        client = _event(
            incident_number=number, signal="trace", service="order-service", offset=1,
            anomalous=payment_fault, trace_id=trace_id, root_span_id=root_span_id,
        )
        payload = client.to_dict()
        payload.update(
            event_id=f"operational-{number:03d}-order-trace-client",
            event_name="POST",
            span_id=client_span_id,
            parent_span_id=root_span_id,
            source_reference=f"operational-training:operational-{number:03d}-order-trace-client",
        )
        client = UnifiedEvent.from_dict(payload)
        payment = _event(
            incident_number=number, signal="trace", service="payment-service", offset=2,
            anomalous=payment_fault, trace_id=trace_id, root_span_id=client_span_id,
        )
        payload = payment.to_dict()
        payload["event_name"] = "POST /payments"
        payment = UnifiedEvent.from_dict(payload)
        events = [root, client, payment]
        # Parte dos incidentes preserva multimodalidade completa; outra parte é
        # trace-only para que ausência live de business/log/metric não vire proxy.
        if number % 3:
            for service_index, service in enumerate(("order-service", "payment-service")):
                for signal_index, signal in enumerate(("log", "metric", "business_event")):
                    events.append(_event(
                        incident_number=number,
                        signal=signal,
                        service=service,
                        offset=3 + service_index * 3 + signal_index,
                        anomalous=service == root_service,
                        trace_id=trace_id,
                        root_span_id=root_span_id,
                    ))
        bundles = IncidentEvidenceAssembler(topology=topology).assemble(events)
        if len(bundles) != 1:
            raise RuntimeError("Fixture de treino operacional não formou um incidente único.")
        bundle = bundles[0]
        graph = EventGraphBuilder().build(bundle, topology=topology)
        inputs.append(FeatureInputV3(bundle, graph, topology=topology))
        for service in ("order-service", "payment-service"):
            targets.append(RCAServiceTargetV3(
                bundle.incident_id,
                service,
                service == root_service,
                "labeled",
                "controlled_operational_training",
            ))
    return tuple(inputs), tuple(targets)


def build_operational_dataset(*, seed: int = TRAINING_SEED) -> tuple[Any, BenchmarkDataset]:
    inputs, targets = _operational_training_inputs()
    dataset = IncidentDatasetGeneratorV3(seed=seed).generate(
        inputs,
        targets=targets,
        supervised=True,
        train_fraction=0.6,
        validation_fraction=0.2,
    )
    with tempfile.TemporaryDirectory(prefix="causalods-operational-model-") as temporary:
        path = Path(temporary) / "dataset.json"
        path.write_text(dataset.to_json(), encoding="utf-8")
        benchmark_dataset = BenchmarkDataset.from_json(path)
    return dataset, benchmark_dataset


def publish_operational_model(
    models_root: Path = DEFAULT_MODELS_ROOT,
    *, seed: int = TRAINING_SEED,
) -> dict[str, Any]:
    """Publica um modelo real pelo benchmark v3; não é uma campanha científica."""
    models_root = Path(models_root)
    models_root.mkdir(parents=True, exist_ok=True)
    feature_dataset, benchmark_dataset = build_operational_dataset(seed=seed)
    benchmark_root = models_root / "benchmark-artifact"
    result = train_and_evaluate(
        MODEL_NAME,
        benchmark_dataset,
        benchmark_root,
        seed=seed,
        tuning_budget=1,
        bootstrap_iterations=20,
        runtime_policy=BenchmarkRuntimePolicy(1, 1, 1, False),
        overwrite=True,
    )
    artifact_dir = Path(result["artifact_dir"])
    benchmark_manifest = result["manifest"]
    model_path = artifact_dir / benchmark_manifest["model_file"]
    registry = FeatureRegistryV3()
    feature_view = registry.select()
    relative_model = model_path.relative_to(models_root).as_posix()
    relative_artifact = artifact_dir.relative_to(models_root).as_posix()
    manifest = {
        "schema_version": MODEL_MANIFEST_SCHEMA,
        "purpose": MODEL_PURPOSE,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "artifact_filename": relative_model,
        "artifact_sha256": _sha256_file(model_path),
        "benchmark_artifact_dir": relative_artifact,
        "benchmark_run_fingerprint": load_verified_artifact(artifact_dir).integrity["run_fingerprint"],
        "feature_registry_version": registry.schema_version,
        "feature_registry_sha256": registry.fingerprint,
        "feature_view_id": OPERATIONAL_FEATURE_VIEW_ID,
        "feature_view_sha256": feature_view.fingerprint,
        "feature_names": list(feature_view.names),
        "training_dataset_sha256": benchmark_dataset.dataset_hash,
        "target_schema_version": TARGET_SCHEMA_VERSION,
        "benchmark_version": BENCHMARK_VERSION,
        "tuning_protocol": benchmark_manifest["tuning_protocol"],
        "tuning_budget": benchmark_manifest["tuning_budget_requested"],
        "seed": seed,
        "python_version": platform.python_version(),
        "xgboost_version": importlib.metadata.version("xgboost"),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "training_scope": "controlled_operational_training_only",
        "scientific_result": False,
        "final_test_dataset_used": False,
        "preprocessing": benchmark_manifest["preprocessing"],
        "dataset_manifest_sha256": feature_dataset.dataset_sha256,
    }
    manifest_path = models_root / MODEL_MANIFEST
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return {"manifest_path": str(manifest_path), "manifest": manifest}


@dataclass(frozen=True)
class OperationalModel:
    model: Any
    manifest: Mapping[str, Any]
    model_path: Path

    @property
    def model_sha256(self) -> str:
        return str(self.manifest["artifact_sha256"])

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(self.manifest["feature_names"])

    def matrix(self, vectors: Iterable[RCAFeatureVectorV3]) -> tuple[np.ndarray, tuple[RCAFeatureVectorV3, ...]]:
        rows = tuple(vectors)
        if not rows:
            raise ValueError("Inferência operacional requer ao menos um vetor.")
        registry_names = FeatureRegistryV3().names
        if self.feature_names != registry_names:
            raise ValueError("Feature names do modelo divergem do registry operacional.")
        matrix = np.asarray([
            [np.nan if vector.values[name] is None else vector.values[name] for name in self.feature_names]
            for vector in rows
        ], dtype=float)
        return matrix, rows

    def predict(self, vectors: Iterable[RCAFeatureVectorV3]) -> tuple[dict[str, Any], ...]:
        matrix, rows = self.matrix(vectors)
        scores = positive_scores(self.model, matrix)
        predictions = tuple({
            "service_name": vector.service_name,
            "incident_id": vector.incident_id,
            "probability": float(score),
        } for vector, score in zip(rows, scores, strict=True))
        if len({item["service_name"] for item in predictions}) != len(predictions):
            raise ValueError("Inferência produziu serviços duplicados.")
        return predictions


def load_operational_model(models_root: Path = DEFAULT_MODELS_ROOT) -> OperationalModel:
    status = operational_model_status(models_root, load_model=True)
    if not status["valid"]:
        raise ValueError(status["reason"])
    return status["loaded_model"]


def operational_model_status(
    models_root: Path = DEFAULT_MODELS_ROOT,
    *, load_model: bool = False,
) -> dict[str, Any]:
    models_root = Path(models_root)
    manifest_path = models_root / MODEL_MANIFEST
    try:
        if not manifest_path.is_file():
            raise ValueError("manifesto operacional ausente")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifesto operacional deve ser objeto")
        required = {
            "schema_version", "purpose", "model_name", "model_version",
            "artifact_filename", "artifact_sha256", "benchmark_artifact_dir",
            "feature_registry_version", "feature_registry_sha256", "feature_view_id",
            "feature_view_sha256", "feature_names", "training_dataset_sha256",
            "target_schema_version", "benchmark_version", "tuning_protocol",
            "tuning_budget", "seed", "python_version", "xgboost_version", "created_at",
            "preprocessing",
        }
        missing = sorted(required - set(manifest))
        if missing:
            raise ValueError(f"campos obrigatórios ausentes: {missing}")
        if manifest["schema_version"] != MODEL_MANIFEST_SCHEMA:
            raise ValueError("schema_version operacional incompatível")
        if manifest["model_name"] != MODEL_NAME:
            raise ValueError("model_name deve ser xgboost-rca-v3")
        if manifest["purpose"] != MODEL_PURPOSE:
            raise ValueError("purpose operacional não aprovado")
        if manifest["benchmark_version"] != BENCHMARK_VERSION:
            raise ValueError("benchmark_version incompatível")
        registry = FeatureRegistryV3()
        view = registry.select()
        if manifest["feature_registry_version"] != registry.schema_version:
            raise ValueError("FeatureRegistry version divergente")
        if manifest["feature_registry_sha256"] != registry.fingerprint:
            raise ValueError("FeatureRegistry hash divergente")
        if manifest["feature_view_id"] != OPERATIONAL_FEATURE_VIEW_ID:
            raise ValueError("FeatureView id divergente")
        if manifest["feature_view_sha256"] != view.fingerprint:
            raise ValueError("FeatureView hash divergente")
        if tuple(manifest["feature_names"]) != view.names:
            raise ValueError("Feature names divergentes")
        if not isinstance(manifest["preprocessing"], dict) or not manifest["preprocessing"].get("policy"):
            raise ValueError("preprocessing ausente")
        model_path = _within(models_root, manifest["artifact_filename"], "artifact_filename")
        artifact_dir = _within(models_root, manifest["benchmark_artifact_dir"], "benchmark_artifact_dir")
        if not model_path.is_file():
            raise ValueError("artifact operacional ausente")
        if _sha256_file(model_path) != manifest["artifact_sha256"]:
            raise ValueError("artifact_sha256 divergente")
        verified = load_verified_artifact(artifact_dir)
        benchmark_manifest = dict(verified.manifest)
        if benchmark_manifest.get("model_name") != MODEL_NAME:
            raise ValueError("artefato benchmark não é xgboost-rca-v3")
        if benchmark_manifest.get("model_sha256") != manifest["artifact_sha256"]:
            raise ValueError("hash operacional diverge do benchmark verificado")
        if benchmark_manifest.get("dataset_sha256") != manifest["training_dataset_sha256"]:
            raise ValueError("training_dataset_sha256 divergente")
        if benchmark_manifest.get("feature_view_sha256") != view.fingerprint:
            raise ValueError("FeatureView do benchmark divergente")
        if canonical_json(benchmark_manifest.get("preprocessing")) != canonical_json(manifest["preprocessing"]):
            raise ValueError("preprocessing diverge do benchmark")
        model = joblib.load(model_path)
        operational = OperationalModel(model, manifest, model_path)
        fixture_vectors = build_demo_scenario("payment_failure").feature_vectors
        predictions = operational.predict(fixture_vectors)
        if len(predictions) != len(fixture_vectors):
            raise ValueError("quantidade de scores inválida")
        if any(not math.isfinite(item["probability"]) or not 0 <= item["probability"] <= 1
               for item in predictions):
            raise ValueError("scores inválidos")
        ranking = sorted(predictions, key=lambda item: (-item["probability"], item["service_name"]))
        if not ranking or len({item["service_name"] for item in ranking}) != len(ranking):
            raise ValueError("ranking operacional inválido")
        result = {
            "valid": True,
            "reason": "manifesto, hashes, benchmark, load, inferência e ranking válidos",
            "manifest": manifest,
            "manifest_path": str(manifest_path),
            "artifact_path": str(model_path),
            "artifact_sha256": manifest["artifact_sha256"],
            "inference": {"scores": list(predictions), "ranking": ranking},
        }
        if load_model:
            result["loaded_model"] = operational
        return result
    except Exception as exception:  # gate boundary: qualquer load/inferência inválida deve ser FAIL
        return {
            "valid": False,
            "reason": str(exception),
            "manifest_path": str(manifest_path),
        }
