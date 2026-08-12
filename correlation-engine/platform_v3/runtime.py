from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from kafka import KafkaConsumer
from opensearchpy import OpenSearch
from xgboost import DMatrix

from explanations_v2 import ExplanationService
from models.rca_context import RCAContext
from models.rca_result import RCAResult
from multimodal_v1.anomaly import DEFAULT_ANOMALY_POLICY
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.evidence.contracts import EventReference
from multimodal_v1.event_graph import CandidatePruner, EventGraphBuilder
from multimodal_v1.features_v3 import FeatureRegistryV3, RCAFeatureExtractorV3
from multimodal_v1.ingestion.kafka_consumer import (
    MultimodalKafkaConsumer,
    MultimodalKafkaSettings,
)
from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.opensearch import OpenSearchProjectionRepository, TemplateCatalog
from platform_v3.operational_model import (
    DEFAULT_MODELS_ROOT,
    MODEL_NAME,
    MODEL_VERSION,
    OperationalModel,
    load_operational_model,
)
from platform_v3.serialization import canonical_json
from ranking.evidence_builder import EvidenceBuilder
from ranking.ranking_service import RCARankingService
from ranking.reliability_gate import ReliabilityGate


RUNTIME_VERSION = "causalods-platform-v3-runtime-1"
TOPOLOGY = {"order-service": ("payment-service",)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass
class RuntimeHealth:
    started_at: str = field(default_factory=_utc_now)
    alive: bool = True
    consumer_connected: bool = False
    consumer_running: bool = False
    model_loaded: bool = False
    opensearch_reachable: bool = False
    ready: bool = False
    consumed_events: int = 0
    processed_incidents: int = 0
    last_incident_id: str | None = None
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **values: Any) -> None:
        with self._lock:
            for name, value in values.items():
                setattr(self, name, value)
            self.ready = bool(
                self.alive and self.consumer_connected and self.consumer_running
                and self.model_loaded and self.opensearch_reachable
            )

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, int(getattr(self, name)) + amount)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "runtime_version": RUNTIME_VERSION,
                "started_at": self.started_at,
                "alive": self.alive,
                "consumer_connected": self.consumer_connected,
                "consumer_running": self.consumer_running,
                "model_loaded": self.model_loaded,
                "opensearch_reachable": self.opensearch_reachable,
                "ready": self.ready,
                "consumed_events": self.consumed_events,
                "processed_incidents": self.processed_incidents,
                "last_incident_id": self.last_incident_id,
                "last_error": self.last_error,
            }


class RuntimeSink:
    """Borda idempotente entre ingestão Kafka e lifecycle de incidentes."""

    def __init__(self, repository: OpenSearchProjectionRepository, health: RuntimeHealth):
        self.repository = repository
        self.health = health
        self.adapter = VisualizationDocumentAdapter()
        self.assembler = IncidentEvidenceAssembler(topology=TOPOLOGY)
        self._last_seen: dict[str, float] = {}
        self._processed: set[str] = set()
        self._lock = threading.RLock()

    def deliver(self, events) -> None:
        with self._lock:
            for event in events:
                decision = self.assembler.ingest(event)
                if not decision.associated or not decision.incident_id:
                    continue
                document = self.adapter.unified_event(event, incident_id=decision.incident_id)
                self.repository.index("unified_events", document["event_id"], document)
                self._last_seen[decision.incident_id] = time.monotonic()
                self.health.increment("consumed_events")

    def ready_bundles(self, *, idle_seconds: float) -> tuple[Any, ...]:
        now = time.monotonic()
        with self._lock:
            bundles = []
            for bundle in self.assembler.active_bundles():
                if bundle.incident_id in self._processed:
                    continue
                if now - self._last_seen.get(bundle.incident_id, now) < idle_seconds:
                    continue
                modalities = {event.signal_type.value for event in bundle.events}
                services = {event.service_name for event in bundle.events if event.service_name}
                anomalous = any(DEFAULT_ANOMALY_POLICY.is_anomalous(event) for event in bundle.events)
                # Métricas sem identidade podem formar bundles próprios. Somente uma
                # cadeia distribuída com falha observada está apta à RCA operacional.
                if "trace" not in modalities or len(services) < 2 or not anomalous:
                    continue
                self._processed.add(bundle.incident_id)
                bundles.append(bundle)
            return tuple(bundles)


class RuntimeProcessor:
    def __init__(
        self,
        model: OperationalModel,
        repository: OpenSearchProjectionRepository,
    ) -> None:
        self.model = model
        self.repository = repository
        self.adapter = VisualizationDocumentAdapter()
        self.graph_builder = EventGraphBuilder()
        self.pruner = CandidatePruner()
        self.extractor = RCAFeatureExtractorV3()
        self.evidence_builder = EvidenceBuilder()
        self.ranking = RCARankingService()
        self.reliability = ReliabilityGate(
            minimum_confidence=0.50,
            minimum_margin=0.05,
            minimum_evidence_count=2,
            minimum_source_count=2,
        )
        self.explanations = ExplanationService()

    @staticmethod
    def _score(value: Any) -> float:
        if value is None or isinstance(value, bool):
            return float(value or 0.0)
        try:
            converted = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, converted)) if math.isfinite(converted) else 0.0

    def _shap(self, vectors) -> dict[str, dict[str, Any]]:
        matrix, rows = self.model.matrix(vectors)
        pipeline = self.model.model
        transformed = pipeline.named_steps["imputer"].transform(matrix)
        booster = pipeline.named_steps["model"].get_booster()
        contributions = booster.predict(DMatrix(transformed), pred_contribs=True)
        names = self.model.feature_names
        result = {}
        for vector, row in zip(rows, contributions, strict=True):
            feature_values = transformed[len(result)]
            values = np.asarray(row[:-1], dtype=float)
            positive = float(np.clip(values, 0, None).sum())
            negative = float(np.abs(np.clip(values, None, 0)).sum())
            total = positive + negative
            items = [{
                "feature_name": name,
                "feature_value": float(feature_value),
                "shap_value": float(shap_value),
                "direction": "supporting" if shap_value > 0 else "opposing" if shap_value < 0 else "neutral",
            } for name, feature_value, shap_value in zip(names, feature_values, values, strict=True)]
            items.sort(key=lambda item: -abs(item["shap_value"]))
            result[vector.service_name] = {
                "support_score": positive / total if total else 0.0,
                "raw_positive_support": positive,
                "raw_negative_support": negative,
                "contributions": items,
                "method": "xgboost-pred-contribs-v1",
            }
        return result

    def process(self, bundle) -> dict[str, Any]:
        graph = self.graph_builder.build(bundle, topology=TOPOLOGY)
        services = tuple(sorted({
            event.service_name for event in bundle.events if event.service_name
        }))
        symptoms = tuple(EventReference(event.event_id, event.source_reference)
                         for event in bundle.events if DEFAULT_ANOMALY_POLICY.is_anomalous(event))
        pruning = self.pruner.prune(graph, services, symptoms)
        vectors = self.extractor.extract(
            bundle,
            graph,
            pruning=pruning,
            topology=TOPOLOGY,
        )
        predictions = self.model.predict(vectors)
        prediction_map = {
            item["service_name"]: {"probability": item["probability"]}
            for item in predictions
        }
        shap_results = self._shap(vectors)
        features = {vector.service_name: dict(vector.values) for vector in vectors}
        temporal, graph_scores, anomaly = {}, {}, {}
        for vector in vectors:
            values = vector.values
            distance = values["distance_to_first_failure"]
            temporal[vector.service_name] = {
                "score": 1.0 / (1.0 + distance) if distance is not None and distance >= 0 else 0.0,
                "distance_to_first_failure": distance,
            }
            graph_scores[vector.service_name] = {
                "score": max(
                    self._score(values["centrality"]),
                    self._score((values["affected_descendant_count"] or 0) / max(1, len(services) - 1)),
                ),
                "centrality": values["centrality"],
            }
            anomaly[vector.service_name] = {
                "score": max(
                    self._score(values["error_span_ratio"]),
                    float(bool(values["exception_present"])),
                    float((values["log_error_count"] or 0) > 0),
                    float((values["anomalous_metric_count"] or 0) > 0),
                    float((values["business_failure_count"] or 0) > 0),
                )
            }
        trace_id = bundle.traces[0]
        registry = FeatureRegistryV3()
        context = RCAContext(
            trace_id=trace_id,
            services=services,
            feature_vectors=features,
            temporal_results=temporal,
            graph_results=graph_scores,
            anomaly_results=anomaly,
            predictions=prediction_map,
            shap_results=shap_results,
            model_version=MODEL_VERSION,
            pipeline_version=RUNTIME_VERSION,
            adapter_metadata={"source": "multimodal-runtime-v3"},
        )
        evidence = self.evidence_builder.build(context)
        ranking = self.ranking.rank_context(context, evidence)
        reliability = self.reliability.evaluate(ranking, evidence)
        metadata = {
            "incident_id": bundle.incident_id,
            "correlation_ids": sorted({event.correlation_id for event in bundle.events if event.correlation_id}),
            "trace_ids": list(bundle.traces),
            "event_ids": [event.event_id for event in bundle.events],
            "analysis_window": {
                "start": bundle.analysis_window.start.isoformat(),
                "end": bundle.analysis_window.end.isoformat(),
            },
            "model_name": MODEL_NAME,
            "model_hash": self.model.model_sha256,
            "feature_registry_version": registry.schema_version,
            "feature_registry_hash": registry.fingerprint,
            "feature_view_id": self.model.manifest["feature_view_id"],
            "feature_view_hash": self.model.manifest["feature_view_sha256"],
            "graph_rule_versions": dict(graph.rule_versions),
            "pruning_policy_version": pruning.policy_version,
            "runtime_version": RUNTIME_VERSION,
        }
        rca = RCAResult.create(
            trace_id=trace_id,
            ranking=list(ranking),
            evidence=list(evidence),
            reliability=reliability,
            model_version=MODEL_VERSION,
            pipeline_version=RUNTIME_VERSION,
            metadata=metadata,
        )
        rca = replace(rca, rca_result_id=f"rca-v3-{_hash({'incident_id': bundle.incident_id, 'model': self.model.model_sha256})[:24]}")
        explanation_result = self.explanations.explain(
            rca,
            raw_telemetry=tuple(event.to_dict() for event in bundle.events),
        )
        explanation = explanation_result.explanation
        explanation_id = f"explanation-v3-{_hash({'rca_result_id': rca.rca_result_id, 'prompt_hash': explanation.prompt_hash})[:24]}"

        incident_document = self.adapter.incident(bundle, graph)
        rca_document = self.adapter.rca_result(
            rca,
            model_hash=self.model.model_sha256,
            feature_registry_hash=registry.fingerprint,
            graph_rule_versions=graph.rule_versions,
            shap_values=shap_results,
        )
        explanation_document = self.adapter.explanation(
            explanation,
            incident_id=bundle.incident_id,
            explanation_id=explanation_id,
            evidence_hash=rca_document["payload_hash"],
            provider="deterministic-fallback" if explanation.used_fallback else "llm",
        )
        self.repository.index("incidents", bundle.incident_id, incident_document)
        self.repository.index("rca_results", rca.rca_result_id, rca_document)
        self.repository.index("explanations", explanation_id, explanation_document)
        return {
            "incident_id": bundle.incident_id,
            "trace_id": trace_id,
            "rca_result_id": rca.rca_result_id,
            "explanation_id": explanation_id,
            "root_cause_service": rca.root_cause_service,
            "confidence": rca.confidence,
            "reliable": rca.reliability.is_reliable,
            "event_count": len(bundle.events),
            "modalities": sorted({event.signal_type.value for event in bundle.events}),
        }


class _HealthHandler(BaseHTTPRequestHandler):
    health: RuntimeHealth

    def do_GET(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
        if self.path not in {"/health", "/ready"}:
            self.send_error(404)
            return
        payload = self.health.snapshot()
        body = canonical_json(payload).encode()
        status = 200 if (self.path == "/health" or payload["ready"]) else 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _open_search() -> OpenSearch:
    host = os.getenv("PLATFORM_V3_OPENSEARCH_HOST", "causalods-opensearch")
    port = int(os.getenv("PLATFORM_V3_OPENSEARCH_PORT", "9200"))
    return OpenSearch(hosts=[{"host": host, "port": port}], use_ssl=False,
                      verify_certs=False, timeout=10)


def run_runtime() -> int:
    health = RuntimeHealth()
    models_root = Path(os.getenv("PLATFORM_V3_MODEL_ROOT", str(DEFAULT_MODELS_ROOT)))
    model = load_operational_model(models_root)
    health.update(model_loaded=True)
    opensearch = _open_search()
    if not opensearch.ping():
        raise RuntimeError("OpenSearch v3 não está acessível.")
    template_root = Path(os.getenv(
        "PLATFORM_V3_TEMPLATE_ROOT",
        str(Path(__file__).resolve().parents[2] / "infrastructure/platform-v3/opensearch/templates"),
    ))
    repository = OpenSearchProjectionRepository(opensearch, TemplateCatalog(template_root))
    repository.provision()
    health.update(opensearch_reachable=True)

    settings = MultimodalKafkaSettings()
    topics = tuple(settings.topic_signals())
    raw_consumer = KafkaConsumer(
        *topics,
        bootstrap_servers=settings.bootstrap_servers,
        group_id=settings.group_id,
        auto_offset_reset="latest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )
    required_topics = (settings.trace_topic, settings.log_topic, settings.metric_topic)
    connected = all(raw_consumer.partitions_for_topic(topic) for topic in required_topics)
    health.update(consumer_connected=connected)
    if not connected:
        raise RuntimeError("Consumer Kafka não localizou os tópicos multimodais obrigatórios.")

    sink = RuntimeSink(repository, health)
    consumer = MultimodalKafkaConsumer(sink, settings, consumer=raw_consumer)
    processor = RuntimeProcessor(model, repository)

    def consume_loop() -> None:
        health.update(consumer_running=True)
        try:
            # consumer_timeout permite revisar readiness sem declarar processo
            # morto; reabre iteração sobre o mesmo consumer continuamente.
            while health.alive:
                for message in consumer.consumer:
                    consumer.process_message(message)
        except Exception as exception:  # health boundary; não mascara falha
            health.update(consumer_running=False, last_error=f"{type(exception).__name__}: {exception}")

    thread = threading.Thread(target=consume_loop, name="platform-v3-kafka", daemon=True)
    thread.start()

    def process_loop() -> None:
        idle = float(os.getenv("PLATFORM_V3_INCIDENT_IDLE_SECONDS", "3"))
        while health.alive:
            try:
                health.update(opensearch_reachable=bool(opensearch.ping()))
                for bundle in sink.ready_bundles(idle_seconds=idle):
                    result = processor.process(bundle)
                    health.increment("processed_incidents")
                    health.update(last_incident_id=result["incident_id"], last_error=None)
                    print(canonical_json({"runtime_event": "incident_processed", **result}), flush=True)
            except Exception as exception:
                health.update(last_error=f"{type(exception).__name__}: {exception}")
                print(canonical_json({"runtime_event": "processing_error", "error_type": type(exception).__name__, "message": str(exception)}), flush=True)
            time.sleep(0.5)

    monitor = threading.Thread(target=process_loop, name="platform-v3-processor", daemon=True)
    monitor.start()

    handler = type("RuntimeHealthHandler", (_HealthHandler,), {"health": health})
    port = int(os.getenv("PLATFORM_V3_HEALTH_PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(canonical_json({"runtime_event": "ready", "port": port, "health": health.snapshot()}), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        health.update(alive=False, consumer_running=False)
        server.server_close()
        consumer.close()
    return 0
