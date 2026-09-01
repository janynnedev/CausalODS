from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Mapping

from explanations_v2 import ExplanationMode, ExplanationService
from explanations_v2.prompt_builder import GROUND_TRUTH_FIELD_NAMES
from multimodal_v1.benchmark_v3 import (
    BenchmarkDataset, BenchmarkRuntimePolicy, train_and_evaluate,
)
from multimodal_v1.degradation_v3 import (
    ObservationDataset, ObservationDegradationHarness, degradation_metrics,
)

from .core import (
    ExperimentRunV3, RunStatusV3, StageFailureV3, canonical_json, sha256_value,
)


def _pipeline_safe_ranking(ranking: Any) -> tuple[dict[str, Any], ...]:
    return tuple({key: value for key, value in dict(item).items()
                  if str(key).lower() not in GROUND_TRUTH_FIELD_NAMES} for item in ranking)


def _reciprocal_rank(prediction: Mapping[str, Any]) -> float:
    value = prediction.get("reciprocal_rank")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0.0 <= float(value) <= 1.0):
        raise StageFailureV3(
            RunStatusV3.FAILED_INPUT_VALIDATION, "incident_prediction",
            "reciprocal_rank por incidente deve ser finito e estar em [0,1].")
    return float(value)


def _validate_benchmark_split(run: ExperimentRunV3, manifest: Mapping[str, Any]) -> None:
    splits = manifest.get("splits", {})
    observed = {
        "train": tuple(sorted(splits.get("train", ()))),
        "validation": tuple(sorted(splits.get("validation", ()))),
        "test": tuple(sorted(splits.get("test", ()))),
    }
    expected = {"train": tuple(sorted(run.train_incident_ids)),
                "validation": tuple(sorted(run.validation_incident_ids)),
                "test": tuple(sorted(run.test_incident_ids))}
    if observed != expected:
        raise StageFailureV3(
            RunStatusV3.FAILED_INPUT_VALIDATION, "benchmark_split",
            "Splits do benchmark divergem do ProtocolPlanner/validation planejados.")


class ExistingBenchmarkDelegateV3:
    """Adapta o benchmark v3 existente; não implementa modelos ou métricas."""

    def __init__(self, datasets: Mapping[tuple[Any, ...], BenchmarkDataset],
                 output: str | Path, *, tuning_budget: int = 1,
                 bootstrap_iterations: int = 100, runtime_policy=None):
        if isinstance(tuning_budget, bool) or not isinstance(tuning_budget, int) \
                or tuning_budget < 1:
            raise ValueError("tuning_budget deve ser inteiro positivo.")
        if isinstance(bootstrap_iterations, bool) or not isinstance(
                bootstrap_iterations, int) or bootstrap_iterations < 1:
            raise ValueError("bootstrap_iterations deve ser inteiro positivo.")
        self.datasets = dict(datasets)
        self.output = Path(output)
        self.tuning_budget = tuning_budget
        self.bootstrap_iterations = bootstrap_iterations
        self.runtime_policy = runtime_policy
        effective_runtime_policy = runtime_policy or BenchmarkRuntimePolicy()
        self._runtime_policy_identity = sha256_value(effective_runtime_policy.to_dict())
        self._cache: dict[tuple[Any, ...], Mapping[str, Any]] = {}

    def _physical_cache_key(self, run: ExperimentRunV3,
                            dataset: BenchmarkDataset) -> tuple[Any, ...]:
        """Identity of every input that can change the persisted benchmark report.

        The delegate configuration is immutable for its campaign-scoped output root.
        Logical catalog coordinates intentionally do not participate: distinct catalog
        entries may resolve to the same immutable physical BenchmarkDataset.
        """
        return (run.method, run.seed, dataset.dataset_hash, self.tuning_budget,
                self.bootstrap_iterations, self._runtime_policy_identity)

    def __call__(self, run: ExperimentRunV3) -> Mapping[str, Any]:
        protocol_key = (run.source_dataset, run.source_system, run.feature_view,
                        run.protocol, run.held_out, run.seed)
        dataset = self.datasets.get(protocol_key)
        if dataset is None:
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "dataset_lookup",
                                 f"BenchmarkDataset ausente para {protocol_key}.")
        if dataset.feature_schema["feature_view_sha256"] != run.feature_view_hash:
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "feature_view",
                                 "FeatureView do benchmark diverge do plano.")
        cache_key = self._physical_cache_key(run, dataset)
        if cache_key not in self._cache:
            try:
                self._cache[cache_key] = train_and_evaluate(
                    run.method, dataset, self.output, seed=run.seed,
                    tuning_budget=self.tuning_budget,
                    bootstrap_iterations=self.bootstrap_iterations,
                    runtime_policy=self.runtime_policy)
            except Exception as exc:
                raise StageFailureV3(RunStatusV3.FAILED_MODEL_EXECUTION,
                                     "benchmark_v3", str(exc)) from exc
        report = self._cache[cache_key]
        prediction = next((item for item in report["incident_predictions"]
                           if item["incident_id"] == run.incident_id), None)
        if prediction is None:
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "benchmark_prediction",
                                 "Incidente não pertence ao test split do artefato.")
        return {"metrics": report["metrics"], "incident_prediction": prediction,
                "manifest": report["manifest"], "artifact_dir": report["artifact_dir"],
                "effective_tuning_budget": self.tuning_budget,
                "bootstrap_iterations": self.bootstrap_iterations}


class H1ExperimentExecutor:
    def __init__(self, benchmark_delegate: Callable[[ExperimentRunV3], Mapping[str, Any]]):
        self.delegate = benchmark_delegate

    def execute(self, run: ExperimentRunV3):
        if run.experiment_family == "H1-B":
            if run.method != "xgboost-rca-v3":
                raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "h1_ablation",
                                     "Ablações estruturais exigem o mesmo xgboost-rca-v3.")
            if run.graph_mode == "NO_GRAPH" and run.pruning_mode != "disabled_ablation":
                raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "h1_ablation",
                                     "NO_GRAPH exige pruning disabled_ablation.")
        report = self.delegate(run)
        prediction = report.get("incident_prediction", {})
        reciprocal_rank = _reciprocal_rank(prediction)
        metrics = report.get("metrics", {})
        manifest = report.get("manifest", {})
        _validate_benchmark_split(run, manifest)
        before = report.get("candidate_count_before")
        after = report.get("candidate_count_after")
        if before is not None:
            before = int(before)
        if after is not None:
            after = int(after)
        if run.pruning_mode == "not_applicable":
            before = after = None
        pruning = {"candidate_count_before": before, "candidate_count_after": after,
                   "pruning_rate": ((before - after) / before
                                    if before is not None and after is not None and before else None),
                   "ground_truth_pruned": (None if run.pruning_mode == "not_applicable"
                                           else report.get("ground_truth_pruned")),
                   "pruning_mode": run.pruning_mode,
                   "pruning_rule_version": (None if run.pruning_mode == "not_applicable"
                                            else report.get("pruning_rule_version"))}
        return ({"predicted_ranking": _pipeline_safe_ranking(prediction.get("ranking", ())),
                 "core_metrics": metrics,
                 "pruning": pruning},
                {"benchmark_version": "rca-model-benchmark-v3",
                 "benchmark_delegated": True,
                 "effective_tuning_budget": report.get("effective_tuning_budget"),
                 "source_manifest_hash": report.get("source_manifest_hash"),
                 "model_manifest": manifest,
                 "train_ids": list(run.train_incident_ids),
                 "validation_ids": list(run.validation_incident_ids),
                 "test_ids": list(run.test_incident_ids)},
                {**metrics, **pruning,
                 "reciprocal_rank": reciprocal_rank,
                 "ground_truth_services": prediction.get("actual_services", ())})


class H2ExperimentExecutor:
    def __init__(self, benchmark_delegate: Callable[[ExperimentRunV3], Mapping[str, Any]]):
        self.delegate = benchmark_delegate

    def execute(self, run: ExperimentRunV3):
        if run.feature_view != "FULL":
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "h2_feature_view",
                                 "H2 exige a mesma FULL FeatureView.")
        report = self.delegate(run)
        manifest = report.get("manifest", {})
        _validate_benchmark_split(run, manifest)
        prediction = report.get("incident_prediction", {})
        reciprocal_rank = _reciprocal_rank(prediction)
        return ({"predicted_ranking": _pipeline_safe_ranking(prediction.get("ranking", ())),
                 "core_metrics": report.get("metrics", {})},
                {"benchmark_version": "rca-model-benchmark-v3", "benchmark_delegated": True,
                 "effective_tuning_budget": report.get("effective_tuning_budget"),
                 "source_manifest_hash": report.get("source_manifest_hash"),
                 "train_ids": manifest.get("splits", {}).get("train", ()),
                 "validation_ids": manifest.get("splits", {}).get("validation", ()),
                 "test_ids": manifest.get("splits", {}).get("test", ()),
                 "tuning_manifest": manifest.get("tuning_protocol"),
                 "model_artifact_hash": manifest.get("model_sha256")},
                {**report.get("metrics", {}), "reciprocal_rank": reciprocal_rank,
                 "ground_truth_services": prediction.get("actual_services", ())})


class H3ExperimentExecutor:
    def __init__(self, service: ExplanationService, rca_results: Mapping[str, Any], *,
                 raw_telemetry: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
                 allow_live_llm: bool = False):
        self.service = service
        self.rca_results = dict(rca_results)
        self.raw_telemetry = raw_telemetry if raw_telemetry is not None else {}
        self.allow_live_llm = allow_live_llm

    def execute(self, run: ExperimentRunV3):
        if run.incident_id not in self.rca_results:
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "rca_lookup",
                                 "RCAResult congelado ausente.")
        if self.allow_live_llm is False and getattr(self.service.llm_client, "is_live_provider", False):
            raise StageFailureV3(RunStatusV3.FAILED_EXPLANATION, "llm_safety",
                                 "Provider live desabilitado.")
        rca = self.rca_results[run.incident_id]
        rca_payload = rca.to_dict() if hasattr(rca, "to_dict") else vars(rca)
        rca_hash = sha256_value(rca_payload)
        mode = run.explanation_mode or ExplanationMode.EVIDENCE_GROUNDED
        raw_telemetry = (self.raw_telemetry.get(run.incident_id, ())
                         if mode == ExplanationMode.RAW_TELEMETRY else ())
        try:
            output = self.service.explain(
                rca, mode=mode, raw_telemetry=raw_telemetry)
        except Exception as exc:
            raise StageFailureV3(RunStatusV3.FAILED_EXPLANATION, "explanation_service",
                                 str(exc)) from exc
        prompt_text = output.prompt.prompt
        if any(field_name in prompt_text.lower()
               for field_name in GROUND_TRUTH_FIELD_NAMES):
            raise StageFailureV3(RunStatusV3.FAILED_EXPLANATION, "prompt_safety",
                                 "Ground truth detectado no prompt.")
        explanation = output.explanation
        catalog = set(output.prompt.sanitized_context.get("allowed_evidence_ids", ()))
        cited = set(explanation.cited_evidence_ids)
        statements = tuple(explanation.evidence_statements)
        total_statement_citations = sum(len(item.evidence_ids) for item in statements)
        applicable = run.explanation_mode == ExplanationMode.EVIDENCE_GROUNDED
        automatic = {
            "root_cause_consistency": explanation.root_cause == rca.root_cause_service,
            "valid_evidence_reference_rate": (
                len(cited & catalog) / len(cited) if applicable and cited else None),
            "evidence_citation_precision": (len(cited & catalog) / len(cited) if applicable and cited else None),
            "evidence_coverage": (len(cited & catalog) / len(catalog)
                                  if applicable and catalog else None),
            "unsupported_claim_rate": (sum(not item.evidence_ids for item in statements) / len(statements)
                                       if statements else 0.0),
            "uncertainty_compliance": explanation.uncertainty in {"low", "medium", "high"},
            "schema_validity_rate": 1.0,
            "fallback_rate": float(explanation.used_fallback),
            "latency_ms": output.provenance.latency_ms,
            "input_tokens": output.provenance.tokens_prompt,
            "output_tokens": output.provenance.tokens_completion,
            "total_tokens": ((output.provenance.tokens_prompt or 0)
                             + (output.provenance.tokens_completion or 0)),
            "cost_usd": output.provenance.cost_usd,
            "statement_citation_count": total_statement_citations,
        }
        return ({"root_cause": explanation.root_cause, "summary": explanation.summary,
                 "confidence": explanation.confidence, "reliability": explanation.reliability,
                 "cited_evidence_ids": list(explanation.cited_evidence_ids),
                 "evidence_statements": [item.to_dict() for item in statements],
                 "used_fallback": explanation.used_fallback,
                 "fallback_reason": explanation.fallback_reason,
                 "supporting_context": output.prompt.sanitized_context},
                {"rca_result_hash": rca_hash, "prompt_hash": output.prompt.prompt_hash,
                 "evidence_hash": output.prompt.evidence_hash, "prompt_text": prompt_text,
                 "selected_raw_telemetry_ids": list(
                     output.prompt.sanitized_context.get("selected_raw_telemetry_ids", ())),
                 "raw_selection_policy_version": output.prompt.sanitized_context.get(
                     "raw_selection_policy_version"),
                 "source_manifest_hash": None,
                 "explanation_provenance_id": output.provenance.provenance_id},
                automatic)


class H4ExperimentExecutor:
    def __init__(self, observations: Mapping[str, ObservationDataset],
                 model_evaluator: Callable[[ExperimentRunV3, ObservationDataset], Mapping[str, Any]],
                 *, harness: ObservationDegradationHarness | None = None,
                 replay_publisher: Any | None = None,
                 replay_topic: str = "causalods.experiments.v3"):
        self.observations = dict(observations)
        self.model_evaluator = model_evaluator
        self.harness = harness or ObservationDegradationHarness()
        self.replay_publisher = replay_publisher
        self.replay_topic = replay_topic
        self._baseline: dict[tuple[str, str, int], Mapping[str, Any]] = {}

    def execute(self, run: ExperimentRunV3):
        if run.incident_id not in self.observations:
            raise StageFailureV3(RunStatusV3.FAILED_INPUT_VALIDATION, "observation_lookup",
                                 "ObservationDataset base ausente.")
        base = self.observations[run.incident_id]
        try:
            degraded = self.harness.apply(base, run.degradation_operator,
                                          run.degradation_severity, seed=run.seed)
        except Exception as exc:
            raise StageFailureV3(RunStatusV3.FAILED_DEGRADATION, "degradation_harness",
                                 str(exc)) from exc
        if degraded.manifest.input_observation_sha256 != base.observation_fingerprint:
            raise StageFailureV3(RunStatusV3.FAILED_DEGRADATION, "pairing",
                                 "Harness não partiu do mesmo dataset base.")
        replay = None
        if run.execution_mode == "kafka-replay":
            if self.replay_publisher is None:
                raise StageFailureV3(RunStatusV3.FAILED_REPLAY, "kafka_replay",
                                     "Publisher Kafka não foi injetado.")
            try:
                replay = self.harness.replay_kafka(
                    degraded, self.replay_publisher, self.replay_topic)
            except Exception as exc:
                raise StageFailureV3(RunStatusV3.FAILED_REPLAY, "kafka_replay", str(exc)) from exc
            if replay.failed:
                raise StageFailureV3(
                    RunStatusV3.FAILED_REPLAY, "kafka_replay",
                    f"Replay confirmou {replay.confirmed}/{replay.attempted}; falhas={replay.failed}.")
        observed = self.model_evaluator(run, degraded.dataset)
        key = (run.source_dataset, run.source_system, run.incident_id, run.protocol,
               run.held_out, run.degradation_operator, run.seed, run.repetition)
        if run.degradation_severity == 0.0:
            self._baseline[key] = {"evaluation": observed, "run_id": run.run_id}
        baseline = self._baseline.get(key)
        if baseline is None:
            raise StageFailureV3(RunStatusV3.FAILED_STATISTICS_PRECONDITION, "h4_pairing",
                                 "Baseline 0% deve ser executado antes da severidade positiva.")
        metrics = degradation_metrics(
            {name: baseline["evaluation"][name] for name in ("top_1", "top_3", "mrr", "fpr")},
            {name: observed[name] for name in ("top_1", "top_3", "mrr", "fpr")},
            degraded.manifest, confidence_values=observed.get("confidence_values", ()),
            outcomes=observed.get("outcomes", ()), abstentions=observed.get("abstentions", 0),
            recovery_time_ms=observed.get("recovery_time_ms"),
            processing_latency_ms=observed.get("processing_latency_ms"))
        accounting = degraded.manifest.accounting
        metrics.update({"actual_loss_ratio": accounting.event_loss_ratio,
                        "expected_loss_ratio": run.degradation_severity,
                        "missing_modalities": list(degraded.manifest.missing_modalities),
                        "degradation_manifest_hash": degraded.manifest.manifest_fingerprint})
        return ({"predicted_ranking": observed.get("predicted_ranking", ()),
                 "confidence": metrics["confidence"], "reliability": observed.get("reliability"),
                 "core_metrics": metrics},
                {"base_observation_sha256": base.observation_fingerprint,
                 "degraded_observation_sha256": degraded.dataset.observation_fingerprint,
                 "ground_truth_sha256": base.ground_truth_fingerprint,
                 "degradation_manifest_hash": degraded.manifest.manifest_fingerprint,
                 "harness_version": degraded.manifest.harness_version,
                 "model_retrained": False, "source_manifest_hash": None,
                 "baseline_0pct_run_id": baseline["run_id"],
                 "robustness_class": "OBSERVABILITY_DEGRADATION",
                 "replay": (None if replay is None else {
                     "attempted": replay.attempted, "confirmed": replay.confirmed,
                     "failed": replay.failed, "topic": self.replay_topic})},
                metrics)
