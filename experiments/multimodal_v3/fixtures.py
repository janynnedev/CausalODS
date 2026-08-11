from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from explanations_v2 import (
    DeterministicTemplateExplainer, EvidenceValidator, ExplanationService,
    ExplanationValidator, InMemoryExplanationProvenanceRepository, LLMResponse,
    PromptBuilder,
)
from models.evidence import EvidenceDirection, EvidenceItem, EvidenceType
from models.rca_result import (
    RCAResult, RankedService, ReliabilityDecision, ScoreBreakdown,
)
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.degradation_v3 import ObservationDataset

from .core import (
    CampaignStoreV3, DatasetCapabilityV3, ExperimentConfigV3,
    ExperimentOrchestratorV3, sha256_value,
)
from .executors import (
    H1ExperimentExecutor, H2ExperimentExecutor, H3ExperimentExecutor,
    H4ExperimentExecutor,
)


BASE = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


class StubLLMClientV3:
    requested_model = "stub-llm-v3"
    is_live_provider = False

    def generate(self, prompt: str, *, temperature: float) -> LLMResponse:
        grounded = '"mode": "evidence_grounded"' in prompt
        statements = [{"text": "evidência temporal", "evidence_ids": ["e-root"]}] if grounded else []
        citations = ["e-root"] if grounded else []
        payload = {"root_cause": "payment-service", "summary": "Explicação controlada.",
                   "evidence_statements": statements, "propagation_path": [],
                   "recommendations": ["inspecionar serviço"],
                   "cited_evidence_ids": citations}
        return LLMResponse(json.dumps(payload), "stub-llm-v3", 20, 10, 1.0, 0.0)


def smoke_rca() -> RCAResult:
    evidence = EvidenceItem(
        evidence_id="e-root", evidence_type=EvidenceType.TEMPORAL,
        source_component="smoke-temporal", description="primeira falha",
        service_name="payment-service", raw_value=0.9, normalized_value=0.9,
        direction=EvidenceDirection.SUPPORTING, observed_at=BASE,
        attributes={"trace_id": "a" * 32})
    ranking = (
        RankedService(1, "payment-service", ScoreBreakdown(
            .9, .6, .9, .5, .8, .8, {"model_probability": 1.0}), ("e-root",)),
        RankedService(2, "order-service", ScoreBreakdown(
            .2, .1, .2, .3, .2, .2, {"model_probability": 1.0}), ()),
    )
    reliability = ReliabilityDecision(True, .8, "smoke", .5, .6, .1, ("minimum",), ())
    return RCAResult("rca-smoke", "a" * 32, "payment-service", .8, ranking,
                     (evidence,), reliability, "xgboost-rca-v3", "causalods-rca-v3",
                     BASE, metadata={"model_hash": "a" * 64,
                                     "analysis_window": {"start": "2026-08-11T12:00:00Z",
                                                         "end": "2026-08-11T12:05:00Z"}})


def smoke_observation() -> ObservationDataset:
    values = []
    for index, signal in enumerate(("trace", "log", "metric", "business_event")):
        payload = {"event_id": f"event-{signal}", "event_name": f"smoke.{signal}",
                   "event_type": signal, "signal_type": signal,
                   "event_timestamp": BASE + timedelta(seconds=index),
                   "service_name": "payment-service", "correlation_id": "smoke-1",
                   "source_reference": f"smoke:{signal}", "source_dataset": "local"}
        if signal == "trace":
            payload.update(trace_id="a" * 32, span_id="b" * 16,
                           attributes={"critical_path": True, "duration_ms": 10})
        elif signal == "log":
            payload.update(severity="ERROR", attributes={"body": "payment failed"})
        elif signal == "metric":
            payload.update(metric_name="cpu.utilization", metric_value=90.0)
        else:
            payload.update(business_operation="payment", business_outcome="failed",
                           business_entity_type="order", business_entity_id="order-1")
        values.append(UnifiedEvent(**payload))
    return ObservationDataset(tuple(values), {"order-service": ("payment-service",)},
                              {"incident-smoke": ("payment-service",)})


def smoke_config(campaign_id: str = "smoke-v3") -> ExperimentConfigV3:
    capabilities = tuple(DatasetCapabilityV3(
        "local", "causalods", incident_id,
        ("trace", "log", "metric", "business_event", "topology"),
        chr(ord("b") + index) * 64, "payment_failure")
        for index, incident_id in enumerate(("incident-smoke", "train-a", "train-b")))
    return ExperimentConfigV3(
        campaign_id, capabilities, seeds=(7,), repetitions=1,
        sample_size=1, degradation_operators=("random_span_drop",),
        model_hash="a" * 64, tuning_budget=1)


def smoke_benchmark(run):
    ranking = ({"service_name": "payment-service", "score": .8, "is_root_cause": True},
               {"service_name": "order-service", "score": .2, "is_root_cause": False})
    metrics = {"precision": 1.0, "recall": 1.0, "macro_f1": 1.0, "fpr": 0.0,
               "top_1": 1.0, "top_3": 1.0, "mrr": 1.0,
               "single_row_p50_ms": .1, "single_row_p95_ms": .2,
               "single_row_p99_ms": .3, "batch_throughput_rows_s": 1000.0,
               "python_tracemalloc_peak_bytes": 1024, "model_size_bytes": 128,
               "training_seconds": .01}
    return {"metrics": metrics,
            "incident_prediction": {"incident_id": run.incident_id,
                                    "actual_services": ["payment-service"],
                                    "ranking": ranking, "reciprocal_rank": 1.0},
            "candidate_count_before": 2,
            "candidate_count_after": 2 if run.pruning_mode == "disabled_ablation" else 1,
            "ground_truth_pruned": False, "pruning_rule_version": "candidate-pruning-v1",
            "source_manifest_hash": run.source_manifest_hash,
            "manifest": {"model_name": run.method, "model_sha256": run.model_hash,
                         "splits": {"train": list(run.train_incident_ids),
                                    "validation": list(run.validation_incident_ids),
                                    "test": list(run.test_incident_ids)},
                         "tuning_protocol": "equal-budget-validation-ranking-v1"}}


def smoke_model_evaluator(run, dataset):
    retained = len(dataset.events) / 4
    score = min(1.0, retained)
    return {"top_1": score, "top_3": 1.0, "mrr": max(.5, score),
            "fpr": 1 - score, "confidence_values": (max(.1, score),),
            "outcomes": (score >= .5,), "abstentions": int(score < .5),
            "reliability": score >= .5, "processing_latency_ms": 1.0,
            "predicted_ranking": ({"service_name": "payment-service", "score": score},)}


def build_smoke_orchestrator(output: Path, repository_root: Path,
                             campaign_id: str = "smoke-v3") -> ExperimentOrchestratorV3:
    config = smoke_config(campaign_id)
    executors = smoke_executor_factory(config=config, output=output,
                                       repository_root=repository_root)
    return ExperimentOrchestratorV3(
        config, CampaignStoreV3(output, campaign_id), executors, repository_root)


def smoke_executor_factory(*, config: ExperimentConfigV3, output: Path,
                           repository_root: Path):
    """Factory somente para fixtures/smoke; campanhas reais injetam bindings próprios."""
    service = ExplanationService(
        llm_client=StubLLMClientV3(), prompt_builder=PromptBuilder(),
        evidence_validator=EvidenceValidator(), explanation_validator=ExplanationValidator(),
        deterministic_explainer=DeterministicTemplateExplainer(),
        provenance_repository=InMemoryExplanationProvenanceRepository(), temperature=0.0)
    incident_ids = tuple(item.incident_id for item in config.datasets)
    observation = smoke_observation()
    raw = tuple(event.to_dict() for event in observation.events)
    executors = {"H1": H1ExperimentExecutor(smoke_benchmark),
                 "H2": H2ExperimentExecutor(smoke_benchmark),
                 "H3": H3ExperimentExecutor(
                     service, {incident_id: smoke_rca() for incident_id in incident_ids},
                     raw_telemetry={incident_id: raw for incident_id in incident_ids}),
                 "H4": H4ExperimentExecutor(
                     {incident_id: observation for incident_id in incident_ids},
                     smoke_model_evaluator)}
    return executors
