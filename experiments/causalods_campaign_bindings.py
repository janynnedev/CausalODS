"""Official dependency-injection boundary for final CausalODS campaigns.

The factory never discovers scientific inputs implicitly.  Paths and provider
factories must be declared in ``ExperimentConfigV3.criteria`` and are checked
before an executor is returned.
"""

from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path
from typing import Any, Mapping
import hashlib

from explanations_v2 import ExplanationService
from multimodal_v1.benchmark_v3 import BenchmarkDataset
from multimodal_v1.degradation_v3 import ObservationDegradationHarness

from experiments.final_campaigns_v3.live_client import EnvironmentOpenAICompatibleClient
from experiments.final_campaigns_v3.h3_openai_freeze import (
    load_manifest as load_h3_llm_manifest, prompt_provenance_fields,
)
from experiments.final_campaigns_v3.preprocessing import validate_catalog
from experiments.multimodal_v3 import (
    ExperimentPlannerV3, H1ExperimentExecutor, H2ExperimentExecutor,
    H3ExperimentExecutor, H4ExperimentExecutor,
)
from experiments.multimodal_v3.core import sha256_value
from experiments.multimodal_v3.executors import ExistingBenchmarkDelegateV3


HEURISTIC_METHODS = frozenset({"temporal-baseline", "max-zscore", "first-anomalous"})


class _AuditedBenchmarkDelegate:
    def __init__(self, delegate):
        self.delegate = delegate
        self.reports = {}

    def __call__(self, run):
        value = self.delegate(run)
        self.reports[run.run_id] = value
        return value


class _BenchmarkProvenanceExecutor:
    """Separate benchmark specification identity from per-fold trained artifacts."""

    def __init__(self, delegate, audit):
        self.delegate, self.audit = delegate, audit

    def execute(self, run):
        result, provenance, evaluation = self.delegate.execute(run)
        report = self.audit.reports.get(run.run_id, {})
        manifest = provenance.get("model_manifest") or {}
        trained = provenance.get("model_artifact_hash") or manifest.get("model_sha256")
        if run.method in HEURISTIC_METHODS:
            trained = None
            implementation_hash = __import__("hashlib").sha256(
                f"benchmark-v3:{run.method}:HeuristicEstimator".encode()).hexdigest()
        else:
            implementation_hash = None
            if not isinstance(trained, str) or len(trained) != 64:
                raise RuntimeError("TRAINED_MODEL_ARTIFACT_PROVENANCE_MISSING")
        provenance = {
            **provenance,
            "method": run.method,
            "method_version": (f"{run.method}-benchmark-v3"),
            "model_specification_hash": run.model_hash,
            "trained_model_artifact_hash": trained,
            "method_implementation_hash": implementation_hash,
            "training_split_hash": __import__("hashlib").sha256(
                json.dumps(sorted(run.train_incident_ids), separators=(",", ":")).encode()).hexdigest(),
            "validation_split_hash": __import__("hashlib").sha256(
                json.dumps(sorted(run.validation_incident_ids), separators=(",", ":")).encode()).hexdigest(),
            "test_split_hash": __import__("hashlib").sha256(
                json.dumps(sorted(run.test_incident_ids), separators=(",", ":")).encode()).hexdigest(),
            "feature_schema_sha256": manifest.get("feature_view_sha256") or run.feature_view_hash,
            "training_dataset_sha256": manifest.get("dataset_sha256"),
            "serialized_artifact_path": (None if trained is None else report.get("artifact_dir")),
            "hyperparameters": manifest.get("hyperparameters"),
        }
        return result, provenance, evaluation


class _AuditedLLMClient:
    is_live_provider = True
    fail_closed = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.requested_model = delegate.requested_model
        self.last_response = None

    def generate(self, prompt: str, *, temperature: float):
        self.last_response = self.delegate.generate(prompt, temperature=temperature)
        return self.last_response


class _H3ProvenanceExecutor:
    def __init__(self, delegate, client, *, temperature: float):
        self.delegate, self.client, self.temperature = delegate, client, temperature

    def execute(self, run):
        self.client.last_response = None
        result, provenance, evaluation = self.delegate.execute(run)
        response = self.client.last_response
        if response is None:
            raise RuntimeError("LLM_RAW_RESPONSE_PROVENANCE_MISSING")
        rca = self.delegate.rca_results[run.incident_id]
        rca_payload = rca.to_dict()
        freeze_manifest = load_h3_llm_manifest()
        mode = run.explanation_mode
        rendered_prompt = provenance.get("prompt_text")
        if mode not in freeze_manifest["prompt_hashes"] or not isinstance(rendered_prompt, str):
            raise RuntimeError("H3_PROMPT_PROVENANCE_BINDING_INVALID")
        prompt_provenance = prompt_provenance_fields(
            freeze_manifest, mode=mode, prompt=rendered_prompt,
            prompt_hash=provenance.get("prompt_hash"))
        digest = lambda value: hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        return result, {
            **provenance, "provider": "openai",
            "requested_model_id": self.client.requested_model,
            "returned_model_id": response.model_name,
            "api_family": "chat-completions",
            "reasoning_effort": "none",
            "top_p_policy": "provider_default_omitted",
            "api_seed_policy": "omitted",
            "max_completion_tokens": 1024,
            "input_token_budget": getattr(self.client.delegate, "manifest", {}).get(
                "input_token_budget"),
            "estimated_input_tokens": getattr(
                self.client.delegate, "last_estimated_input_tokens", None),
            "finish_reason": response.finish_reason,
            "response_id": response.response_id,
            "provider_prompt_tokens": response.tokens_prompt,
            "provider_completion_tokens": response.tokens_completion,
            "provider_total_tokens": response.total_tokens,
            "system_fingerprint": response.system_fingerprint,
            "request_id": response.request_id,
            "llm_model_identifier": response.model_name,
            "llm_temperature": self.temperature,
            "prompt_template_version": "prompt-builder-v1",
            **prompt_provenance,
            "input_evidence_hash": provenance.get("evidence_hash"),
            "evidence_base_hash": digest(rca_payload.get("evidence", ())),
            "root_cause_ranking_hash": digest({
                "root_cause_service": rca_payload.get("root_cause_service"),
                "ranking": rca_payload.get("ranking", ())}),
            "reliability_decision_hash": digest(rca_payload.get("reliability", {})),
            "raw_output_hash": response.response_hash,
        }, evaluation


def _observation_counts(dataset):
    counts = {name: 0 for name in ("trace", "log", "metric", "business_event", "topology")}
    for event in dataset.events:
        counts[event.signal_type.value] += 1
    counts["topology"] = sum(len(targets) for targets in dataset.topology.values())
    return counts


class _RecordingHarness:
    def __init__(self):
        self.delegate = ObservationDegradationHarness()
        self.last = None

    def apply(self, *args, **kwargs):
        self.last = self.delegate.apply(*args, **kwargs)
        return self.last

    def replay_kafka(self, *args, **kwargs):
        return self.delegate.replay_kafka(*args, **kwargs)


class _H4ProvenanceExecutor:
    def __init__(self, observations, evaluator, *, replay_publisher=None,
                 feature_hasher=None, result_store_path: Path | None = None):
        self.harness = _RecordingHarness()
        self.last_evaluation = None

        def recording(run, dataset):
            self.last_evaluation = evaluator(run, dataset)
            return self.last_evaluation

        self.delegate = H4ExperimentExecutor(
            observations, recording, harness=self.harness,
            replay_publisher=replay_publisher,
        )
        self.observations = observations
        self.feature_hasher = feature_hasher
        self.result_store_path = Path(result_store_path) if result_store_path else None
        self.original_feature_hashes = {}

    @staticmethod
    def _group_key(run):
        return (run.source_dataset, run.source_system, run.incident_id, run.protocol,
                run.held_out, run.degradation_operator, run.seed, run.repetition)

    def _restore_baseline(self, run):
        """Load the successful 0% result; never re-run its campaign inference."""
        if self.result_store_path is None:
            return None
        zero = replace(run, degradation_severity=0.0, run_id="")
        path = self.result_store_path / "runs" / f"{zero.run_id}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        valid = (
            payload.get("status", {}).get("state") == "SUCCESS"
            and payload.get("identity", {}).get("run_id") == zero.run_id
            and payload.get("input", {}).get("source_manifest_hash") == run.source_manifest_hash
            and payload.get("input", {}).get("model_hash") == run.model_hash
            and self.result_store_path.name == run.configuration_hash
        )
        if not valid:
            return None
        evaluation = payload.get("evaluation", {})
        if not {"top_1", "top_3", "mrr", "fpr"} <= set(evaluation):
            return None
        self.delegate._baseline[self._group_key(run)] = {
            "evaluation": evaluation, "run_id": zero.run_id,
        }
        original = payload.get("provenance", {}).get("original_feature_hash")
        if isinstance(original, str) and len(original) == 64:
            self.original_feature_hashes[self._group_key(run)] = original
        return payload

    def execute(self, run):
        key = self._group_key(run)
        if run.degradation_severity != 0.0 and key not in self.delegate._baseline:
            self._restore_baseline(run)
        result, provenance, evaluation = self.delegate.execute(run)
        degraded = self.harness.last.dataset
        observed = self.last_evaluation or {}
        feature_hash = observed.get("feature_hash")
        if not isinstance(feature_hash, str) or len(feature_hash) != 64:
            raise RuntimeError("H4_DEGRADED_FEATURE_HASH_MISSING")
        if run.degradation_severity == 0.0:
            self.original_feature_hashes[key] = feature_hash
        base = self.observations[run.incident_id]
        original_feature_hash = (
            self.feature_hasher(run, base) if self.feature_hasher is not None
            else self.original_feature_hashes.get(key)
        )
        if not isinstance(original_feature_hash, str) or len(original_feature_hash) != 64:
            original_feature_hash = self.original_feature_hashes.get(key)
        if original_feature_hash is None:
            raise RuntimeError("H4_ORIGINAL_FEATURE_HASH_MISSING")
        provenance = {
            **provenance,
            "original_incident_hash": base.observation_fingerprint,
            "degraded_evidence_hash": degraded.observation_fingerprint,
            "original_modality_counts": _observation_counts(base),
            "degraded_modality_counts": _observation_counts(degraded),
            "original_feature_hash": original_feature_hash,
            "degraded_feature_hash": feature_hash,
            "degradation_seed": run.seed,
        }
        evaluation = {**evaluation,
                      "ground_truth_service": observed.get("ground_truth_service"),
                      "predicted_service": observed.get("predicted_service")}
        return result, provenance, evaluation


def _factory(reference: str):
    if not isinstance(reference, str) or ":" not in reference:
        raise ValueError("Provider explícito deve usar module:function.")
    module, name = reference.split(":", 1)
    return getattr(importlib.import_module(module), name)


def _load_benchmark_catalog(path: Path, output: Path, *, tuning_budget: int,
                            repository_root: Path, model_specification_hash: str):
    if not path.is_file():
        raise ValueError(f"SCIENTIFIC_BENCHMARK_CATALOG_NOT_FOUND:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "scientific-benchmark-catalog-v3":
        raise ValueError("Scientific benchmark catalog version inválida.")
    validate_catalog(path, repository_root=repository_root,
                     expected_model_specification_hash=model_specification_hash)
    datasets = {}
    for item in payload.get("datasets", ()):
        dataset_path = (repository_root / item["path"]).expanduser().resolve()
        key = tuple(item[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out", "seed"
        ))
        if key in datasets:
            raise ValueError("Scientific benchmark catalog contém chave duplicada.")
        datasets[key] = BenchmarkDataset.from_json(dataset_path)
    if not datasets:
        raise ValueError("Scientific benchmark catalog está vazio.")
    return ExistingBenchmarkDelegateV3(
        datasets, output / "benchmark-v3", tuning_budget=tuning_budget,
        bootstrap_iterations=int(payload.get("bootstrap_iterations", 1000)),
    )


def build_executors(*, config, output: Path, repository_root: Path) -> dict[str, Any]:
    """Build concrete frozen-contract executors from explicit scientific providers."""
    criteria = dict(config.criteria)
    scientific_config = replace(config, datasets=tuple(
        replace(item, fault_type=None, scenario=None, rca_correct=None, reliable=None)
        for item in config.datasets
    ))
    selected = set(config.hypotheses)
    result: dict[str, Any] = {}
    if selected & {"H1", "H2"}:
        catalog = criteria.get("scientific_benchmark_catalog")
        if not catalog:
            raise ValueError("SCIENTIFIC_BENCHMARK_CATALOG_NOT_CONFIGURED")
        delegate = _load_benchmark_catalog(
            (repository_root / catalog).resolve(), Path(output),
            tuning_budget=config.tuning_budget, repository_root=repository_root,
            model_specification_hash=config.model_hash,
        )
        audit = _AuditedBenchmarkDelegate(delegate)
        if "H1" in selected:
            result["H1"] = _BenchmarkProvenanceExecutor(H1ExperimentExecutor(audit), audit)
        if "H2" in selected:
            result["H2"] = _BenchmarkProvenanceExecutor(H2ExperimentExecutor(audit), audit)
    if "H3" in selected:
        original_h3_runs = tuple(
            run for run in ExperimentPlannerV3(config).plan()
            if run.hypothesis == "H3" and run.experiment_family == "H3-EXPLANATIONS"
        )
        expected_h3_incident_ids = tuple(sorted({
            run.incident_id for run in original_h3_runs
        }))
        expected_h3_sample_sha256 = sha256_value(list(expected_h3_incident_ids))
        if len(expected_h3_incident_ids) != 40:
            raise ValueError("H3_AUTHORITATIVE_SAMPLE_BINDING_INVALID")
        provider_ref = criteria.get("h3_input_provider")
        if not provider_ref:
            raise ValueError("H3_SCIENTIFIC_RCA_PROVIDER_NOT_CONFIGURED")
        supplied = _factory(provider_ref)(
            config=scientific_config, repository_root=repository_root,
            expected_incident_ids=expected_h3_incident_ids,
            expected_sample_sha256=expected_h3_sample_sha256,
        )
        if not isinstance(supplied, Mapping) or "rca_results" not in supplied:
            raise ValueError("H3 input provider deve retornar rca_results e raw_telemetry.")
        live_client = EnvironmentOpenAICompatibleClient.from_environment(
            model=criteria.get("llm_model"), endpoint=criteria.get("llm_endpoint"),
            timeout_seconds=float(criteria.get("llm_timeout_seconds", 60)),
            max_output_tokens=int(criteria.get("llm_max_output_tokens", 1024)),
        )
        client = _AuditedLLMClient(live_client)
        temperature = float(criteria.get("llm_temperature", 0.0))
        service = ExplanationService(
            llm_client=client, temperature=temperature)
        delegate = H3ExperimentExecutor(
            service, supplied["rca_results"],
            raw_telemetry=supplied.get("raw_telemetry", {}), allow_live_llm=True,
        )
        result["H3"] = _H3ProvenanceExecutor(
            delegate, client, temperature=temperature)
    if "H4" in selected:
        provider_ref = criteria.get("h4_input_provider")
        if not provider_ref:
            raise ValueError("H4_SCIENTIFIC_INPUT_PROVIDER_NOT_CONFIGURED")
        supplied = _factory(provider_ref)(config=scientific_config, repository_root=repository_root)
        if not isinstance(supplied, Mapping) or not {"observations", "model_evaluator"} <= set(supplied):
            raise ValueError("H4 provider deve retornar observations e model_evaluator.")
        result["H4"] = _H4ProvenanceExecutor(
            supplied["observations"], supplied["model_evaluator"],
            replay_publisher=supplied.get("replay_publisher"),
            feature_hasher=supplied.get("feature_hasher"), result_store_path=output,
        )
    if selected != set(result):
        raise ValueError("Executor factory não cobriu todas as hipóteses.")
    return result
