from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from multimodal_v1.degradation_v3 import HARNESS_VERSION, operator_policy
from multimodal_v1.features_v3 import FeatureGroup, FeatureRegistryV3
from multimodal_v1.event_graph import EdgeRuleRegistry
from multimodal_v1.public_datasets_v3 import (
    EvaluationProtocol, IncidentDescriptor, ProtocolPlanner,
    PUBLIC_DATASET_PROTOCOL_VERSION,
)


ORCHESTRATOR_VERSION = "experimental-orchestrator-v3"
RUN_SCHEMA_VERSION = "experiment-run-v3"
MANIFEST_SCHEMA_VERSION = "campaign-manifest-v3"
KNOWN_MODALITIES = frozenset({"trace", "log", "metric", "business_event", "topology"})
HYPOTHESES = ("H1", "H2", "H3", "H4")
EXPLANATION_MODES = ("generic", "raw_telemetry", "evidence_grounded")
MODEL_METHODS = (
    "temporal-baseline", "max-zscore", "first-anomalous",
    "logistic-regression", "random-forest", "xgboost-rca-v3", "mlp-light-v3",
)
V2_FREEZE_HASHES = {
    "correlation-engine/models/rca_feature_schema.py": "57c9bfa7074fb13f3dc03e6ec4e71f513b3d27ba7f602a01c70993f5a978cfc3",
    "correlation-engine/models/rca_feature_schema_v2.py": "1d48fe99ca89d6f46d8fe552a9c2a4df7dc6b219ac59f4f437015ca93ed876a0",
    "correlation-engine/pipeline/feature_pipeline.py": "d1cef03efd348153cd32d3c0d6f103446e336bfb116ce3bed834f1dcabbf3390",
    "correlation-engine/pipeline/prediction_pipeline.py": "f371ddde888509832b3be764651b2f6e30175f1a0f8eb80622ff4b88c8cc2d6d",
    "correlation-engine/pipeline/prediction_pipeline_v2.py": "fa89c6634c2a6b15699d6e2c44e17a9098f47dce8c078aae9825c13da14ea723",
    "correlation-engine/pipeline/rca_pipeline.py": "654b10c14dd947b8599b8722f8f30192d87901656650fee2534ab42ee956006e",
    "correlation-engine/models/rca_context.py": "dc39524c8778cf5ba118e7a9974c83ad31bcf7f4218e2dd8c53b646db042a303",
    "correlation-engine/models/rca_result.py": "742cb0b9519eb71b4c50c3d3f17aa69fd582a4ac330a062357ab613065fe05f5",
    "correlation-engine/training/models/xgboost_rca.pkl": "f0160972e4f977fd6cea2a9bf4819b75f2cc7265b8a75d6fe9ce7ed8b355c4c6",
    "correlation-engine/training/models/xgboost_rca_v2.pkl": "f418d45f92d91f338092768c1ee9e2dc257047b422d98a24b0be65cae01379d8",
    "correlation-engine/training/models/xgboost_rca_v2.manifest.json": "b9fff2f06aa162acd00228860d5e9910ef303d585dc80bc0e80dc968e5221523",
    "correlation-engine/training/data/rca_dataset_v2.csv": "cb30ebea60d818ac59c506493900392b89978232cff4c12336a5e3a10dd71d2a",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = value if not isinstance(value, (set, frozenset)) else sorted(value, key=str)
        return [_jsonable(item) for item in items]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON científico não aceita NaN/Infinity.")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_v2_freeze(repository_root: Path) -> dict[str, Any]:
    observed = {}
    mismatches = []
    for relative, expected in V2_FREEZE_HASHES.items():
        path = repository_root / relative
        actual = file_sha256(path) if path.is_file() else None
        observed[relative] = {"expected": expected, "actual": actual, "valid": actual == expected}
        if actual != expected:
            mismatches.append(relative)
    return {"valid": not mismatches, "ok": len(observed) - len(mismatches),
            "total": len(observed), "mismatches": mismatches, "files": observed}


class RunStatusV3(str, Enum):
    SUCCESS = "SUCCESS"
    SKIPPED_MODALITY_UNAVAILABLE = "SKIPPED_MODALITY_UNAVAILABLE"
    SKIPPED_PROTOCOL_NOT_APPLICABLE = "SKIPPED_PROTOCOL_NOT_APPLICABLE"
    FAILED_INPUT_VALIDATION = "FAILED_INPUT_VALIDATION"
    FAILED_FEATURE_EXTRACTION = "FAILED_FEATURE_EXTRACTION"
    FAILED_MODEL_EXECUTION = "FAILED_MODEL_EXECUTION"
    FAILED_DEGRADATION = "FAILED_DEGRADATION"
    FAILED_EXPLANATION = "FAILED_EXPLANATION"
    FAILED_REPLAY = "FAILED_REPLAY"
    FAILED_STATISTICS_PRECONDITION = "FAILED_STATISTICS_PRECONDITION"
    FAILED_INTERNAL = "FAILED_INTERNAL"


@dataclass(frozen=True)
class DatasetCapabilityV3:
    source_dataset: str
    source_system: str
    incident_id: str
    available_modalities: tuple[str, ...]
    manifest_sha256: str
    fault_type: str | None = None
    usable_for_benchmark: bool = True
    rca_correct: bool | None = None
    reliable: bool | None = None
    scenario: str | None = None

    def __post_init__(self) -> None:
        for name in ("source_dataset", "source_system", "incident_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} deve ser texto não vazio.")
        modalities = tuple(sorted(set(self.available_modalities)))
        if set(modalities) - KNOWN_MODALITIES:
            raise ValueError("Capability contém modalidade desconhecida.")
        if len(self.manifest_sha256) != 64 or any(
                character not in "0123456789abcdef" for character in self.manifest_sha256):
            raise ValueError("manifest_sha256 deve ser SHA-256.")
        if type(self.usable_for_benchmark) is not bool:
            raise TypeError("usable_for_benchmark deve ser bool.")
        for name in ("rca_correct", "reliable"):
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} deve ser bool ou None.")
        object.__setattr__(self, "available_modalities", modalities)

    @classmethod
    def from_incident_manifest(cls, manifest: Any) -> "DatasetCapabilityV3":
        return cls(source_dataset=manifest.source_dataset,
                   source_system=manifest.source_system,
                   incident_id=manifest.incident_id,
                   available_modalities=tuple(manifest.modalities),
                   manifest_sha256=manifest.fingerprint,
                   fault_type=manifest.fault_type,
                   usable_for_benchmark=manifest.usable_for_benchmark,
                   scenario=getattr(manifest, "source_case", None))

    def to_dict(self) -> dict[str, Any]:
        return {"source_dataset": self.source_dataset, "source_system": self.source_system,
                "incident_id": self.incident_id,
                "available_modalities": list(self.available_modalities),
                "manifest_sha256": self.manifest_sha256, "fault_type": self.fault_type,
                "usable_for_benchmark": self.usable_for_benchmark,
                "rca_correct": self.rca_correct, "reliable": self.reliable,
                "scenario": self.scenario}


class DatasetCapabilityResolverV3:
    """Resolve capabilities exclusivamente dos IncidentManifest dos adapters existentes."""

    @staticmethod
    def resolve(adapters: Mapping[str, Any]) -> tuple[DatasetCapabilityV3, ...]:
        capabilities = []
        seen = set()
        for adapter_name, adapter in sorted(adapters.items()):
            for descriptor in adapter.list_incidents():
                manifest = adapter.load_incident(descriptor.incident_id)
                if (manifest.incident_id != descriptor.incident_id
                        or manifest.source_dataset != descriptor.source_dataset
                        or manifest.source_system != descriptor.system):
                    raise ValueError(f"Adapter {adapter_name} retornou descriptor/manifest divergentes.")
                capability = DatasetCapabilityV3.from_incident_manifest(manifest)
                if capability.incident_id in seen:
                    raise ValueError("incident_id duplicado entre adapters.")
                seen.add(capability.incident_id)
                capabilities.append(capability)
        if not capabilities:
            raise ValueError("Nenhum IncidentManifest disponível nos adapters.")
        return tuple(sorted(capabilities, key=lambda item: item.incident_id))


@dataclass(frozen=True)
class ExperimentConfigV3:
    campaign_id: str
    datasets: tuple[DatasetCapabilityV3, ...]
    hypotheses: tuple[str, ...] = HYPOTHESES
    protocols: tuple[str, ...] = ("within-dataset",)
    seeds: tuple[int, ...] = (42,)
    repetitions: int = 1
    execution_mode: str = "offline"
    sample_size: int | None = None
    explanation_modes: tuple[str, ...] = EXPLANATION_MODES
    degradation_operators: tuple[str, ...] = ("random_span_drop",)
    severities: tuple[float, ...] = (0.0, 0.1, 0.3, 0.5)
    allow_live_llm: bool = False
    model_version: str = "xgboost-rca-v3"
    model_hash: str = "0" * 64
    tuning_budget: int = 1
    test_fraction: float = 0.2
    validation_fraction: float = 0.2
    held_out_systems: tuple[str, ...] = ()
    held_out_sources: tuple[str, ...] = ()
    criteria: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.campaign_id.strip() or any(character.isspace() for character in self.campaign_id):
            raise ValueError("campaign_id deve ser não vazio e sem espaços.")
        if not self.datasets or len({item.incident_id for item in self.datasets}) != len(self.datasets):
            raise ValueError("datasets requer incident_ids únicos.")
        if not set(self.hypotheses) <= set(HYPOTHESES) or not self.hypotheses:
            raise ValueError("hypotheses inválidas.")
        for name in ("hypotheses", "protocols", "seeds", "explanation_modes",
                     "degradation_operators", "severities", "held_out_systems",
                     "held_out_sources"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} não aceita duplicatas.")
        try:
            tuple(EvaluationProtocol(item) for item in self.protocols)
        except ValueError as exc:
            raise ValueError("protocols inválidos.") from exc
        if self.execution_mode not in {"offline", "kafka-replay"}:
            raise ValueError("execution_mode inválido.")
        if not self.seeds or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in self.seeds):
            raise ValueError("seeds deve conter inteiros.")
        if isinstance(self.repetitions, bool) or self.repetitions < 1:
            raise ValueError("repetitions deve ser positivo.")
        if self.sample_size is not None and (isinstance(self.sample_size, bool) or self.sample_size < 1):
            raise ValueError("sample_size deve ser positivo ou None.")
        if not set(self.explanation_modes) <= set(EXPLANATION_MODES):
            raise ValueError("explanation_modes inválidos.")
        for severity in self.severities:
            if severity not in {0.0, 0.1, 0.3, 0.5}:
                raise ValueError("Severidades suportadas: 0, 0.1, 0.3 e 0.5.")
        if type(self.allow_live_llm) is not bool:
            raise TypeError("allow_live_llm deve ser bool.")
        if (not isinstance(self.model_version, str) or not self.model_version.strip()
                or len(self.model_hash) != 64
                or any(character not in "0123456789abcdef" for character in self.model_hash)):
            raise ValueError("Modelo requer version e artifact hash SHA-256.")
        if isinstance(self.tuning_budget, bool) or not isinstance(self.tuning_budget, int) \
                or self.tuning_budget < 1:
            raise ValueError("tuning_budget deve ser inteiro positivo.")
        if not isinstance(self.criteria, Mapping):
            raise TypeError("criteria deve ser Mapping.")
        for name in ("test_fraction", "validation_fraction"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
                raise ValueError(f"{name} deve estar entre 0 e 1.")
        for name in ("held_out_systems", "held_out_sources"):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)) or any(not item.strip() for item in values):
                raise ValueError(f"{name} deve ser coleção de textos não vazios.")

    def to_dict(self) -> dict[str, Any]:
        return {"campaign_id": self.campaign_id,
                "datasets": [item.to_dict() for item in sorted(self.datasets, key=lambda x: x.incident_id)],
                "hypotheses": list(self.hypotheses), "protocols": list(self.protocols),
                "seeds": list(self.seeds), "repetitions": self.repetitions,
                "execution_mode": self.execution_mode, "sample_size": self.sample_size,
                "explanation_modes": list(self.explanation_modes),
                "degradation_operators": list(self.degradation_operators),
                "severities": list(self.severities), "allow_live_llm": self.allow_live_llm,
                "model_version": self.model_version, "model_hash": self.model_hash,
                "tuning_budget": self.tuning_budget,
                "test_fraction": self.test_fraction,
                "validation_fraction": self.validation_fraction,
                "held_out_systems": list(self.held_out_systems),
                "held_out_sources": list(self.held_out_sources),
                "criteria": dict(self.criteria)}

    @property
    def configuration_sha256(self) -> str:
        return sha256_value(self.to_dict())


@dataclass(frozen=True)
class ExperimentRunV3:
    campaign_id: str
    hypothesis: str
    experiment_family: str
    source_dataset: str
    source_system: str
    incident_id: str
    protocol: str
    held_out: str | None
    feature_view: str
    feature_view_hash: str
    graph_mode: str
    pruning_mode: str
    method: str
    model_version: str
    model_hash: str
    seed: int
    degradation_operator: str | None
    degradation_severity: float | None
    explanation_mode: str | None
    repetition: int
    execution_mode: str
    configuration_hash: str
    available_modalities: tuple[str, ...]
    required_modalities: tuple[str, ...]
    fault_type: str | None
    scenario: str | None
    source_manifest_hash: str
    eligibility: bool
    skip_reason: str | None
    train_incident_ids: tuple[str, ...]
    validation_incident_ids: tuple[str, ...]
    test_incident_ids: tuple[str, ...]
    run_id: str = ""

    def __post_init__(self) -> None:
        identity = self.identity_payload()
        expected = sha256_value(identity)
        if self.run_id and self.run_id != expected:
            raise ValueError("run_id diverge da identidade canônica.")
        object.__setattr__(self, "run_id", expected)
        if self.eligibility == (self.skip_reason is not None):
            raise ValueError("eligibility e skip_reason são inconsistentes.")

    def identity_payload(self) -> dict[str, Any]:
        return {name: _jsonable(getattr(self, name)) for name in (
            "campaign_id", "hypothesis", "experiment_family", "source_dataset",
            "source_system", "incident_id", "protocol", "held_out", "feature_view",
            "feature_view_hash", "graph_mode", "pruning_mode", "method", "model_version",
            "model_hash", "seed", "degradation_operator", "degradation_severity",
            "explanation_mode", "repetition", "execution_mode", "configuration_hash")}

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, **self.identity_payload(),
                "available_modalities": list(self.available_modalities),
                "required_modalities": list(self.required_modalities),
                "fault_type": self.fault_type, "scenario": self.scenario,
                "source_manifest_hash": self.source_manifest_hash,
                "missing_modalities": sorted(set(self.required_modalities) - set(self.available_modalities)),
                "eligibility": self.eligibility, "skip_reason": self.skip_reason,
                "train_incident_ids": list(self.train_incident_ids),
                "validation_incident_ids": list(self.validation_incident_ids),
                "test_incident_ids": list(self.test_incident_ids)}


def derive_validation_split(train_incident_ids: Iterable[str], *, seed: int,
                            fraction: float = 0.2) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values = sorted(set(train_incident_ids))
    if len(values) < 2:
        raise ValueError("Treino insuficiente para derivar validation sem leakage.")
    shuffled = list(values)
    random.Random(seed).shuffle(shuffled)
    size = min(len(values) - 1, max(1, round(len(values) * fraction)))
    validation = tuple(sorted(shuffled[:size]))
    training = tuple(sorted(shuffled[size:]))
    return training, validation


@dataclass(frozen=True)
class ProtocolAssignmentV3:
    capability: DatasetCapabilityV3
    protocol: str
    held_out: str | None
    train_incident_ids: tuple[str, ...]
    validation_incident_ids: tuple[str, ...]
    test_incident_ids: tuple[str, ...]
    applicable: bool = True
    reason: str | None = None


class ExperimentPlannerV3:
    MODALITY_VIEWS = {
        "METRIC_ONLY": ("metric",), "TRACE_ONLY": ("trace",),
        "METRIC_TRACE": ("metric", "trace"),
        "METRIC_TRACE_LOG": ("metric", "trace", "log"),
        "METRIC_TRACE_LOG_BUSINESS": ("metric", "trace", "log", "business_event"),
    }

    def __init__(self, config: ExperimentConfigV3):
        self.config = config
        self.registry = FeatureRegistryV3()

    def _view_hash(self, include: tuple[str, ...] | None = None,
                   exclude: tuple[str, ...] = ()) -> str:
        included = None if include is None else tuple(FeatureGroup(item) for item in include)
        excluded = tuple(FeatureGroup(item) for item in exclude)
        return self.registry.select(include_groups=included, exclude_groups=excluded).fingerprint

    def _run(self, assignment: ProtocolAssignmentV3, hypothesis: str, family: str, *,
             seed: int, repetition: int, method: str,
             feature_view: str = "FULL", feature_hash: str | None = None,
             graph_mode: str = "GRAPH_BUSINESS", pruning_mode: str = "production",
             required: tuple[str, ...] = (), operator: str | None = None,
             severity: float | None = None, explanation_mode: str | None = None) -> ExperimentRunV3:
        capability = assignment.capability
        missing = sorted(set(required) - set(capability.available_modalities))
        eligible = assignment.applicable and capability.usable_for_benchmark and not missing
        reason = None
        if not assignment.applicable:
            reason = f"protocol_not_applicable:{assignment.reason}"
        elif missing:
            reason = f"missing_modalities:{','.join(missing)}"
        elif not capability.usable_for_benchmark:
            reason = "dataset_not_usable_for_benchmark"
        return ExperimentRunV3(
            self.config.campaign_id, hypothesis, family, capability.source_dataset,
            capability.source_system, capability.incident_id, assignment.protocol,
            assignment.held_out, feature_view,
            feature_hash or self._view_hash(), graph_mode, pruning_mode, method,
            self.config.model_version if method == "xgboost-rca-v3" else f"{method}-v3",
            self.config.model_hash if method == "xgboost-rca-v3" else "0" * 64,
            seed, operator, severity, explanation_mode, repetition,
            self.config.execution_mode, self.config.configuration_sha256,
            capability.available_modalities, tuple(required), capability.fault_type,
            capability.scenario, capability.manifest_sha256,
            eligible, reason,
            assignment.train_incident_ids, assignment.validation_incident_ids,
            assignment.test_incident_ids)

    @staticmethod
    def _descriptor(item: DatasetCapabilityV3) -> IncidentDescriptor:
        return IncidentDescriptor(
            item.incident_id, item.source_dataset, item.source_system,
            item.fault_type or "unspecified", f"manifest:{item.manifest_sha256}",
            item.fault_type, None)

    @staticmethod
    def _manifest_payload(manifest: Any) -> dict[str, Any]:
        return {
            "version": manifest.version,
            "protocol": manifest.protocol.value,
            "fingerprint": manifest.fingerprint,
            "incident_ids": list(manifest.incident_ids),
            "systems": list(manifest.systems),
            "sources": list(manifest.sources),
            "folds": [{"held_out": fold.held_out,
                       "train_incident_ids": list(fold.train_incident_ids),
                       "test_incident_ids": list(fold.test_incident_ids),
                       "seed": fold.seed, "test_fraction": fold.test_fraction}
                      for fold in manifest.folds],
        }

    def protocol_assignments(self, protocol: str, seed: int) -> tuple[
            tuple[ProtocolAssignmentV3, ...], tuple[dict[str, Any], ...]]:
        """Delega os folds ao ProtocolPlanner v2 e deriva validation só do train."""
        mode = EvaluationProtocol(protocol)
        capabilities = {item.incident_id: item for item in self.config.datasets}
        descriptors = {item.incident_id: self._descriptor(item)
                       for item in self.config.datasets}
        manifests, failures = [], []

        def create(values: tuple[IncidentDescriptor, ...], **kwargs: Any) -> None:
            try:
                manifests.append(ProtocolPlanner.manifest(values, mode, **kwargs))
            except (TypeError, ValueError) as exc:
                failures.append((tuple(item.incident_id for item in values), str(exc),
                                 kwargs.get("held_out_system") or kwargs.get("held_out_source")))

        values = tuple(descriptors[key] for key in sorted(descriptors))
        if mode is EvaluationProtocol.WITHIN_DATASET:
            groups: dict[str, list[IncidentDescriptor]] = {}
            for item in values:
                groups.setdefault(item.source_dataset, []).append(item)
            for group in groups.values():
                create(tuple(group), seed=seed, test_fraction=self.config.test_fraction)
        elif mode is EvaluationProtocol.CROSS_SYSTEM:
            held_outs = self.config.held_out_systems or tuple(sorted({item.system for item in values}))
            for held_out in held_outs:
                create(values, held_out_system=held_out)
        elif mode is EvaluationProtocol.CROSS_SOURCE:
            held_outs = self.config.held_out_sources or tuple(
                sorted({item.source_dataset for item in values}))
            for held_out in held_outs:
                create(values, held_out_source=held_out)
        else:
            create(values)

        assignments: list[ProtocolAssignmentV3] = []
        for manifest in manifests:
            for fold in manifest.folds:
                try:
                    train, validation = derive_validation_split(
                        fold.train_incident_ids, seed=seed,
                        fraction=self.config.validation_fraction)
                    applicable, reason = True, None
                except ValueError as exc:
                    train, validation, applicable, reason = (), (), False, str(exc)
                for incident_id in fold.test_incident_ids:
                    assignments.append(ProtocolAssignmentV3(
                        capabilities[incident_id], protocol, fold.held_out,
                        train, validation, fold.test_incident_ids, applicable, reason))
        for incident_ids, reason, held_out in failures:
            for incident_id in incident_ids:
                assignments.append(ProtocolAssignmentV3(
                    capabilities[incident_id], protocol, held_out, (), (), (), False, reason))
        records = tuple(self._manifest_payload(item) for item in manifests)
        records += tuple({"version": PUBLIC_DATASET_PROTOCOL_VERSION, "protocol": protocol,
                          "applicable": False, "incident_ids": list(ids),
                          "held_out": held_out, "reason": reason}
                         for ids, reason, held_out in failures)
        return tuple(sorted(assignments, key=lambda item: (
            item.held_out or "", item.capability.incident_id))), records

    def protocol_manifests(self) -> tuple[dict[str, Any], ...]:
        records = []
        for protocol in self.config.protocols:
            for seed in self.config.seeds:
                _, manifests = self.protocol_assignments(protocol, seed)
                records.extend({"seed": seed, **item} for item in manifests)
        return tuple(records)

    def plan(self) -> tuple[ExperimentRunV3, ...]:
        runs = []
        for protocol in self.config.protocols:
            for seed in self.config.seeds:
                assignments, _ = self.protocol_assignments(protocol, seed)
                sampled_h3 = self._h3_sample(
                    tuple(item.capability for item in assignments), seed=seed)
                for repetition in range(self.config.repetitions):
                    for assignment in assignments:
                        item = assignment.capability
                        if "H1" in self.config.hypotheses:
                            for method in (*MODEL_METHODS[:3], "xgboost-rca-v3"):
                                runs.append(self._run(assignment, "H1", "H1-A",
                                                      seed=seed, repetition=repetition, method=method,
                                                      graph_mode=("GRAPH_BUSINESS" if method == "xgboost-rca-v3"
                                                                  else "BASELINE"),
                                                      pruning_mode=("production" if method == "xgboost-rca-v3"
                                                                    else "not_applicable")))
                            for mode, excluded, pruning, required in (
                                ("NO_GRAPH", ("graph",), "disabled_ablation", ()),
                                ("GRAPH", ("business",), "production", ()),
                                ("GRAPH_BUSINESS", (), "production", ("business_event",)),
                            ):
                                runs.append(self._run(
                                    assignment, "H1", "H1-B", seed=seed,
                                    repetition=repetition, method="xgboost-rca-v3",
                                    feature_view=mode, feature_hash=self._view_hash(exclude=excluded),
                                    graph_mode=mode, pruning_mode=pruning, required=required))
                            for view, required in self.MODALITY_VIEWS.items():
                                groups = tuple("business" if value == "business_event" else value for value in required)
                                runs.append(self._run(
                                    assignment, "H1", "H1-C", seed=seed,
                                    repetition=repetition, method="xgboost-rca-v3",
                                    feature_view=view,
                                    feature_hash=self._view_hash(include=(*groups, "quality")),
                                    required=required))
                        if "H2" in self.config.hypotheses:
                            for method in MODEL_METHODS:
                                runs.append(self._run(assignment, "H2", "H2-MODELS",
                                                      seed=seed, repetition=repetition, method=method))
                        if "H3" in self.config.hypotheses and item.incident_id in sampled_h3:
                            for mode in self.config.explanation_modes:
                                runs.append(self._run(
                                    assignment, "H3", "H3-EXPLANATIONS",
                                    seed=seed, repetition=repetition, method="xgboost-rca-v3",
                                    explanation_mode=mode))
                        if "H4" in self.config.hypotheses:
                            for operator in self.config.degradation_operators:
                                policy = operator_policy(operator)
                                required = (("topology",) if policy.modalities == ("topology",)
                                            else () if set(policy.modalities) & set(item.available_modalities)
                                            else (policy.modalities[0],))
                                for severity in self.config.severities:
                                    runs.append(self._run(
                                        assignment, "H4", "H4-DEGRADATION",
                                        seed=seed, repetition=repetition, method="xgboost-rca-v3",
                                        required=required, operator=operator, severity=severity))
        return tuple(sorted(runs, key=lambda run: (
            run.hypothesis, run.experiment_family, run.source_dataset,
            run.source_system, run.incident_id, run.degradation_operator or "",
            -1.0 if run.degradation_severity is None else run.degradation_severity,
            run.seed, run.repetition, run.explanation_mode or "", run.method,
            run.feature_view, run.run_id)))

    def _h3_sample(self, candidates: tuple[DatasetCapabilityV3, ...], *, seed: int) -> frozenset[str]:
        values = list({item.incident_id: item for item in candidates}.values())
        size = len(values) if self.config.sample_size is None else min(self.config.sample_size, len(values))
        strata: dict[tuple[Any, ...], list[DatasetCapabilityV3]] = {}
        for item in values:
            key = (item.source_dataset, item.source_system, item.fault_type,
                   item.rca_correct, item.reliable, item.scenario)
            strata.setdefault(key, []).append(item)
        for group in strata.values():
            group.sort(key=lambda item: sha256_value(
                {"seed": seed, "incident_id": item.incident_id}))
        selected = []
        ordered_keys = sorted(strata, key=lambda key: canonical_json(key))
        while len(selected) < size:
            progressed = False
            for key in ordered_keys:
                if strata[key] and len(selected) < size:
                    selected.append(strata[key].pop(0))
                    progressed = True
            if not progressed:
                break
        return frozenset(item.incident_id for item in selected)

    @staticmethod
    def summary(runs: Iterable[ExperimentRunV3]) -> dict[str, int]:
        values = tuple(runs)
        return {"expected_executions": sum(item.eligibility for item in values),
                "expected_skips": sum(not item.eligibility for item in values),
                **{f"expected_{hypothesis}": sum(item.hypothesis == hypothesis for item in values)
                   for hypothesis in HYPOTHESES}}

    @classmethod
    def feature_view_manifests(cls, runs: Iterable[ExperimentRunV3]) -> tuple[dict[str, Any], ...]:
        all_groups = {item.value for item in FeatureGroup}
        records = {}
        for run in runs:
            if run.feature_view in cls.MODALITY_VIEWS:
                active = {"business" if item == "business_event" else item
                          for item in cls.MODALITY_VIEWS[run.feature_view]} | {"quality"}
            elif run.graph_mode == "NO_GRAPH":
                active = all_groups - {"graph"}
            elif run.graph_mode == "GRAPH":
                active = all_groups - {"business"}
            else:
                active = set(all_groups)
            key = (run.feature_view, run.feature_view_hash, run.pruning_mode)
            records[key] = {"id": run.feature_view, "sha256": run.feature_view_hash,
                            "active_groups": sorted(active),
                            "inactive_groups": sorted(all_groups - active),
                            "pruning_mode": run.pruning_mode}
        return tuple(records[key] for key in sorted(records))


@dataclass(frozen=True)
class ExperimentRunResultV3:
    run: ExperimentRunV3
    status: RunStatusV3
    reason: str | None
    exception_type: str | None
    execution_stage: str
    started_at: datetime
    finished_at: datetime
    result: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    evaluation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is not RunStatusV3.SUCCESS and not self.reason:
            raise ValueError("Run não SUCCESS requer reason.")
        _jsonable(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": RUN_SCHEMA_VERSION,
                "identity": {key: value for key, value in self.run.to_dict().items()
                             if key in {"run_id", "campaign_id", "hypothesis", "experiment_family",
                                        "protocol", "held_out", "repetition", "execution_mode"}},
                "input": {"dataset": self.run.source_dataset, "system": self.run.source_system,
                          "incident_id": self.run.incident_id,
                          "fault_type": self.run.fault_type, "scenario": self.run.scenario,
                          "source_manifest_hash": (self.provenance.get("source_manifest_hash")
                                                   or self.run.source_manifest_hash),
                          "feature_view_hash": self.run.feature_view_hash,
                          "model_hash": self.run.model_hash, "seed": self.run.seed},
                "configuration": {"method": self.run.method, "graph_mode": self.run.graph_mode,
                                  "pruning_mode": self.run.pruning_mode,
                                  "explanation_mode": self.run.explanation_mode,
                                  "degradation_operator": self.run.degradation_operator,
                                  "degradation_severity": self.run.degradation_severity,
                                  "execution_mode": self.run.execution_mode},
                "result": dict(self.result), "provenance": dict(self.provenance),
                "evaluation": dict(self.evaluation),
                "status": {"state": self.status.value, "reason": self.reason,
                           "exception_type": self.exception_type,
                           "execution_stage": self.execution_stage,
                           "started_at": _jsonable(self.started_at),
                           "finished_at": _jsonable(self.finished_at),
                           "elapsed_seconds": max(0.0, (self.finished_at - self.started_at).total_seconds())}}


class ExperimentExecutorV3(Protocol):
    def execute(self, run: ExperimentRunV3) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]: ...


class StageFailureV3(RuntimeError):
    def __init__(self, status: RunStatusV3, stage: str, message: str):
        super().__init__(message)
        self.status, self.stage = status, stage


class CampaignStoreV3:
    def __init__(self, root: str | Path, campaign_id: str):
        self.path = Path(root).expanduser().resolve() / campaign_id
        self.runs_path = self.path / "runs"

    @staticmethod
    def _atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def write_json(self, relative: str, payload: Any, *, immutable: bool = False) -> Path:
        path = self.path / relative
        content = canonical_json(payload) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                if immutable:
                    raise ValueError(f"Artefato imutável diverge: {relative}.")
            else:
                return path
        self._atomic(path, content)
        return path

    def write_plan(self, runs: tuple[ExperimentRunV3, ...]) -> None:
        rows = [run.to_dict() for run in runs]
        self.write_json("experiment-plan.json", {"schema_version": "experiment-plan-v3", "runs": rows}, immutable=True)
        fields = ("run_id", "hypothesis", "experiment_family", "source_dataset", "source_system",
                  "incident_id", "protocol", "feature_view", "method", "seed", "repetition",
                  "graph_mode", "pruning_mode", "degradation_operator", "degradation_severity",
                  "explanation_mode", "execution_mode", "eligibility", "skip_reason")
        output = []
        import io
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})
        self._atomic(self.path / "experiment-plan.csv", stream.getvalue())

    def _index(self) -> dict[str, str]:
        path = self.runs_path / "index.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_run(self, value: ExperimentRunResultV3) -> None:
        target = self.runs_path / f"{value.run.run_id}.json"
        if target.exists():
            raise FileExistsError(f"Run já persistido: {value.run.run_id}.")
        self._atomic(target, canonical_json(value.to_dict()) + "\n")
        index = self._index()
        index[value.run.run_id] = file_sha256(target)
        self.write_json("runs/index.json", index)

    def load_completed(self) -> dict[str, dict[str, Any]]:
        index = self._index()
        on_disk = {path.stem for path in self.runs_path.glob("*.json")
                   if path.name != "index.json"}
        if on_disk != set(index):
            raise ValueError("Índice de runs diverge dos arquivos persistidos.")
        result = {}
        for run_id, expected in index.items():
            path = self.runs_path / f"{run_id}.json"
            if not path.is_file() or file_sha256(path) != expected:
                raise ValueError(f"Checksum inválido para run {run_id}.")
            result[run_id] = json.loads(path.read_text(encoding="utf-8"))
        return result

    def verify_sha256s(self) -> None:
        checksum_path = self.path / "SHA256SUMS"
        if not checksum_path.is_file():
            raise ValueError("SHA256SUMS ausente.")
        expected = {}
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            if "  " not in line:
                raise ValueError("SHA256SUMS malformado.")
            digest, relative = line.split("  ", 1)
            if relative in expected or len(digest) != 64:
                raise ValueError("SHA256SUMS duplicado ou inválido.")
            target = (self.path / relative).resolve()
            if self.path not in target.parents or not target.is_file():
                raise ValueError("SHA256SUMS referencia caminho inválido.")
            expected[relative] = digest
        observed = {str(path.relative_to(self.path)) for path in self.path.rglob("*")
                    if path.is_file() and path.name != "SHA256SUMS"}
        if observed != set(expected):
            raise ValueError("SHA256SUMS não cobre exatamente a campanha.")
        if any(file_sha256(self.path / relative) != digest
               for relative, digest in expected.items()):
            raise ValueError("SHA256SUMS contém checksum divergente.")

    def write_sha256s(self) -> None:
        files = [path for path in self.path.rglob("*") if path.is_file() and path.name != "SHA256SUMS"]
        lines = [f"{file_sha256(path)}  {path.relative_to(self.path)}" for path in sorted(files)]
        self._atomic(self.path / "SHA256SUMS", "\n".join(lines) + "\n")


def _git(repository_root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(("git", *args), cwd=repository_root, check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_campaign_manifest(config: ExperimentConfigV3, runs: tuple[ExperimentRunV3, ...],
                            repository_root: Path) -> dict[str, Any]:
    freeze = verify_v2_freeze(repository_root)
    dependencies = {}
    for name in ("numpy", "scikit-learn", "xgboost", "joblib", "pytest"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    registry = FeatureRegistryV3()
    graph_registry = EdgeRuleRegistry.defaults()
    return {"identity": {"schema_version": MANIFEST_SCHEMA_VERSION,
                         "campaign_id": config.campaign_id,
                         "created_at": datetime.now(timezone.utc),
                         "configuration_sha256": config.configuration_sha256},
            "repository": {"git_commit": _git(repository_root, "rev-parse", "HEAD"),
                           "git_branch": _git(repository_root, "branch", "--show-current"),
                           "dirty_worktree": bool(_git(repository_root, "status", "--porcelain")),
                           "v2_freeze_hash_status": freeze},
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "cpu": platform.processor(), "cpu_count": os.cpu_count(),
                            "memory_total_bytes": _memory_total(), "dependencies": dependencies},
            "contracts": {"UnifiedEvent": "unified-event-v1",
                          "IncidentEvidence": "incident-evidence-bundle-v3",
                          "EventEvidenceGraph": "event-evidence-graph-v3",
                          "EventEvidenceGraph_rule_versions": graph_registry.to_dict(),
                          "EventEvidenceGraph_rule_registry_hash": graph_registry.fingerprint,
                          "feature_registry_version": registry.schema_version,
                          "feature_registry_hash": registry.fingerprint,
                          "FeatureView_hashes": sorted({run.feature_view_hash for run in runs}),
                          "FeatureViews": list(
                              ExperimentPlannerV3.feature_view_manifests(runs)),
                          "degradation_harness_version": HARNESS_VERSION,
                          "EvidenceBuilder_version": "evidence-builder-v1",
                          "PromptBuilder_version": "prompt-builder-v1"},
            "datasets": {"incident_count": len(config.datasets),
                         "capabilities": [item.to_dict() for item in config.datasets],
                         "protocol_version": PUBLIC_DATASET_PROTOCOL_VERSION,
                         "protocol_manifests": list(
                             ExperimentPlannerV3(config).protocol_manifests())},
            "models": {"method": "xgboost-rca-v3", "model_version": config.model_version,
                       "artifact_hash": config.model_hash, "tuning_budget": config.tuning_budget,
                       "hyperparameter_tuning_manifest": config.criteria.get("tuning_manifest"),
                       "training_dataset_hash": config.criteria.get("training_dataset_hash")},
            "experiment": {"hypotheses": list(config.hypotheses), "seeds": list(config.seeds),
                           "repetitions": config.repetitions,
                           **ExperimentPlannerV3.summary(runs),
                           "execution_mode": config.execution_mode},
            "llm": {"allow_live_llm": config.allow_live_llm, "temperature": 0.0,
                    "provider": config.criteria.get("llm_provider"),
                    "model": config.criteria.get("llm_model"),
                    "max_input_tokens": config.criteria.get("llm_max_input_tokens"),
                    "max_output_tokens": config.criteria.get("llm_max_output_tokens"),
                    "timeout_seconds": config.criteria.get("llm_timeout_seconds"),
                    "retry_policy": config.criteria.get("llm_retry_policy", "none-in-tests")}}


def _memory_total() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


class ExperimentOrchestratorV3:
    def __init__(self, config: ExperimentConfigV3, store: CampaignStoreV3,
                 executors: Mapping[str, ExperimentExecutorV3], repository_root: Path):
        self.config, self.store = config, store
        self.executors, self.repository_root = dict(executors), repository_root
        self.planner = ExperimentPlannerV3(config)

    def prepare(self) -> tuple[ExperimentRunV3, ...]:
        runs = self.planner.plan()
        manifest_path = self.store.path / "campaign-manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["identity"]["configuration_sha256"] != self.config.configuration_sha256:
                raise ValueError("--resume recusado: configuration_sha256 diverge.")
        else:
            self.store.write_json("campaign-manifest.json",
                                  build_campaign_manifest(self.config, runs, self.repository_root),
                                  immutable=True)
        self.store.write_plan(runs)
        h3_incidents = sorted({run.incident_id for run in runs if run.hypothesis == "H3"})
        self.store.write_json(
            "h3/h3-sample-manifest.json",
            {"schema_version": "h3-sample-manifest-v3", "sample_size": len(h3_incidents),
             "incident_ids": h3_incidents,
             "selection": "deterministic-strata-hash-v1"}, immutable=True)
        return runs

    def run(self, *, resume: bool = False) -> tuple[dict[str, Any], ...]:
        runs = self.prepare()
        completed = self.store.load_completed() if resume else {}
        if not resume and completed:
            raise FileExistsError("Campanha já contém runs; use --resume.")
        for run in runs:
            if run.run_id in completed:
                continue
            started = datetime.now(timezone.utc)
            status, reason, exception_type, stage = RunStatusV3.SUCCESS, None, None, "execution"
            result = provenance = evaluation = {}
            if not run.eligibility:
                status = (RunStatusV3.SKIPPED_PROTOCOL_NOT_APPLICABLE
                          if (run.skip_reason or "").startswith("protocol_not_applicable:")
                          else RunStatusV3.SKIPPED_MODALITY_UNAVAILABLE)
                reason, stage = run.skip_reason, "planning"
            else:
                try:
                    executor = self.executors[run.hypothesis]
                    result, provenance, evaluation = executor.execute(run)
                    provenance = {**dict(provenance),
                                  "orchestrator_version": ORCHESTRATOR_VERSION,
                                  "pipeline_version": "causalods-rca-v3",
                                  "feature_registry_hash": FeatureRegistryV3().fingerprint,
                                  "model_version": run.model_version,
                                  "protocol_version": PUBLIC_DATASET_PROTOCOL_VERSION,
                                  "contract_versions": {
                                      "UnifiedEvent": "unified-event-v1",
                                      "ExperimentRun": RUN_SCHEMA_VERSION},
                                  "graph_rule_versions": EdgeRuleRegistry.defaults().to_dict()}
                    provenance.setdefault("train_ids", list(run.train_incident_ids))
                    provenance.setdefault("validation_ids", list(run.validation_incident_ids))
                    provenance.setdefault("test_ids", list(run.test_incident_ids))
                except StageFailureV3 as exc:
                    status, reason, exception_type, stage = exc.status, str(exc), type(exc).__name__, exc.stage
                except Exception as exc:  # boundary converts an experimental failure, not a model error
                    status, reason, exception_type = RunStatusV3.FAILED_INTERNAL, str(exc), type(exc).__name__
            value = ExperimentRunResultV3(run, status, reason, exception_type, stage,
                                          started, datetime.now(timezone.utc), result,
                                          provenance, evaluation)
            self.store.write_run(value)
            completed[run.run_id] = value.to_dict()
        self.store.write_sha256s()
        persisted = self.store.load_completed()
        return tuple(persisted[key] for key in sorted(persisted))


class CampaignValidatorV3:
    def validate(self, runs: Iterable[ExperimentRunV3], results: Iterable[Mapping[str, Any]],
                 *, store: CampaignStoreV3 | None = None,
                 repository_root: Path | None = None) -> dict[str, Any]:
        planned = {run.run_id: run for run in runs}
        values = tuple(results)
        ids = [item["identity"]["run_id"] for item in values]
        issues = []
        if len(ids) != len(set(ids)):
            issues.append("duplicate_run")
        missing, extra = set(planned) - set(ids), set(ids) - set(planned)
        issues.extend(f"missing_run:{item}" for item in sorted(missing))
        issues.extend(f"extra_run:{item}" for item in sorted(extra))
        for item in values:
            status = item["status"]
            if status["state"] not in {value.value for value in RunStatusV3}:
                issues.append(f"unknown_status:{item['identity']['run_id']}")
            if status["state"].startswith("FAILED_"):
                issues.append(f"failed_run:{item['identity']['run_id']}:{status['state']}")
            if status["state"].startswith("SKIPPED_") and not status.get("reason"):
                issues.append(f"skip_without_reason:{item['identity']['run_id']}")
            try:
                _reject_nonfinite(item)
            except ValueError:
                issues.append(f"nonfinite:{item['identity']['run_id']}")
            prompt = item.get("provenance", {}).get("prompt_text", "")
            if any(forbidden in prompt.lower() for forbidden in (
                    "ground_truth", "fault_service", "fault_location", "is_root_cause")):
                issues.append(f"ground_truth_in_prompt:{item['identity']['run_id']}")
            if _contains_ground_truth({key: item.get(key, {}) for key in (
                    "input", "configuration", "result", "provenance")}):
                issues.append(f"ground_truth_outside_evaluation:{item['identity']['run_id']}")
            run = planned.get(item["identity"]["run_id"])
            provenance = item.get("provenance", {})
            if run:
                expected_skip = (RunStatusV3.SKIPPED_PROTOCOL_NOT_APPLICABLE.value
                                 if (run.skip_reason or "").startswith("protocol_not_applicable:")
                                 else RunStatusV3.SKIPPED_MODALITY_UNAVAILABLE.value)
                if not run.eligibility and status["state"] != expected_skip:
                    issues.append(f"skip_status_mismatch:{item['identity']['run_id']}")
                if run.eligibility and status["state"].startswith("SKIPPED_"):
                    issues.append(f"unexpected_skip:{item['identity']['run_id']}")
            if run and item.get("input", {}).get("source_manifest_hash") != run.source_manifest_hash:
                issues.append(f"source_manifest_mismatch:{item['identity']['run_id']}")
            observed_modalities = set(provenance.get("observed_modalities", ()))
            if run and not observed_modalities <= set(run.available_modalities):
                issues.append(f"illegal_modality:{item['identity']['run_id']}")
            if (item["status"]["state"] == "SUCCESS"
                    and provenance.get("orchestrator_version") != ORCHESTRATOR_VERSION):
                issues.append(f"version_mismatch:{item['identity']['run_id']}")
            train = set(provenance.get("train_ids", ()))
            validation_ids = set(provenance.get("validation_ids", ()))
            test = set(provenance.get("test_ids", ()))
            if train & test or validation_ids & test:
                issues.append(f"split_leakage:{item['identity']['run_id']}")
            if provenance.get("model_retrained") is True and item["identity"]["hypothesis"] == "H4":
                issues.append(f"h4_model_retrained:{item['identity']['run_id']}")
        h3 = [item for item in values if item["identity"]["hypothesis"] == "H3"
              and item["status"]["state"] == "SUCCESS"]
        by_incident: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for item in h3:
            key = (item["input"]["dataset"], item["input"]["system"],
                   item["input"]["incident_id"], item["identity"].get("protocol"),
                   item["input"]["seed"], item["identity"].get("repetition"))
            by_incident.setdefault(key, []).append(item)
        expected_h3: dict[tuple[Any, ...], set[str]] = {}
        for run in planned.values():
            if run.hypothesis == "H3" and run.eligibility:
                key = (run.source_dataset, run.source_system, run.incident_id,
                       run.protocol, run.seed, run.repetition)
                expected_h3.setdefault(key, set()).add(run.explanation_mode)
        for incident, items in by_incident.items():
            roots = {item["result"].get("root_cause") for item in items}
            rca_hashes = {item["provenance"].get("rca_result_hash") for item in items}
            modes = {item["configuration"].get("explanation_mode") for item in items}
            if len(roots) != 1 or len(rca_hashes) != 1 or modes != expected_h3.get(incident, set()):
                issues.append(f"h3_pairing:{incident}")
        h4 = [item for item in values if item["identity"]["hypothesis"] == "H4"
              and item["status"]["state"] == "SUCCESS"]
        groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for item in h4:
            config = item["configuration"]
            key = (item["input"]["incident_id"], config["degradation_operator"],
                   item["input"]["seed"], item["identity"].get("protocol"),
                   item["identity"].get("held_out"), item["identity"].get("repetition"))
            groups.setdefault(key, []).append(item)
        expected_h4: dict[tuple[Any, ...], set[float]] = {}
        for run in planned.values():
            if run.hypothesis == "H4" and run.eligibility:
                key = (run.incident_id, run.degradation_operator, run.seed,
                       run.protocol, run.held_out, run.repetition)
                expected_h4.setdefault(key, set()).add(run.degradation_severity)
        for key, items in groups.items():
            severities = {item["configuration"]["degradation_severity"] for item in items}
            bases = {item["provenance"].get("base_observation_sha256") for item in items}
            models = {item["input"].get("model_hash") for item in items}
            references = {item["provenance"].get("baseline_0pct_run_id") for item in items}
            zero_ids = {item["identity"]["run_id"] for item in items
                        if item["configuration"]["degradation_severity"] == 0.0}
            if (severities != expected_h4.get(key, set()) or 0.0 not in severities
                    or len(bases) != 1 or len(models) != 1 or references != zero_ids):
                issues.append(f"h4_pairing:{key}")
        if store is not None:
            try:
                store.load_completed()
                store.verify_sha256s()
            except ValueError:
                issues.append("checksum_mismatch")
        freeze = verify_v2_freeze(repository_root) if repository_root else {"valid": True}
        if not freeze["valid"]:
            issues.append("v2_freeze_mismatch")
        return {"schema_version": "campaign-validation-v3", "complete": not issues,
                "expected_runs": len(planned), "observed_runs": len(values),
                "issues": issues, "v2_freeze_hash_status": freeze}


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_nonfinite(item)


def _contains_ground_truth(value: Any) -> bool:
    forbidden = {"ground_truth", "fault_service", "fault_location", "is_root_cause",
                 "actual_services"}
    if isinstance(value, Mapping):
        return any(str(key).lower() in forbidden or _contains_ground_truth(item)
                   for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return any(_contains_ground_truth(item) for item in value)
    return False


class ExperimentStatisticsV3:
    @staticmethod
    def compare_paired(left: Mapping[str, Mapping[str, Any]],
                       right: Mapping[str, Mapping[str, Any]], *, metric: str,
                       seed: int = 42, samples: int = 1000) -> dict[str, Any]:
        from experiments.analysis_utils import mcnemar_exact, paired_bootstrap_difference
        keys = tuple(sorted(set(left) & set(right)))
        if set(left) != set(right) or not keys:
            raise ValueError("Comparação requer alinhamento pareado exato.")
        continuous = paired_bootstrap_difference(
            [left[key][metric] for key in keys], [right[key][metric] for key in keys],
            samples=samples, seed=seed)
        mcnemar = mcnemar_exact(
            [bool(left[key]["top1_correct"]) for key in keys],
            [bool(right[key]["top1_correct"]) for key in keys])
        return {"metric": metric, "pairs": len(keys), "continuous": continuous,
                "mcnemar": mcnemar}

    @staticmethod
    def holm(p_values: Iterable[float]) -> tuple[float, ...]:
        from experiments.analyze_latency_sensitivity import holm_adjust
        return tuple(holm_adjust(list(p_values)))


class ExperimentReportBuilderV3:
    HUMAN_RUBRIC_FIELDS = (
        "utility", "clarity", "perceived_confidence", "traceability",
        "uncertainty_adequacy",
    )
    FILES = {
        "h1": ("h1-overall.csv", "h1-by-dataset.csv", "h1-by-scenario.csv",
               "h1-structural-ablation.csv", "h1-modality-ablation.csv",
               "h1-pruning.csv", "h1-paired-statistics.csv"),
        "h2": ("h2-model-comparison.csv", "h2-performance.csv", "h2-runtime-cost.csv",
               "h2-seed-stability.csv", "h2-paired-statistics.csv"),
        "h3": ("h3-automatic-evaluation.csv", "h3-by-mode.csv", "h3-fallbacks.csv",
               "h3-cost-latency.csv", "h3-human-evaluation-blinded.csv"),
        "h4": ("h4-by-operator.csv", "h4-by-severity.csv", "h4-by-modality.csv",
               "h4-confidence-abstention.csv", "h4-degradation-curves.csv"),
    }

    def build(self, store: CampaignStoreV3, results: Iterable[Mapping[str, Any]],
              validation: Mapping[str, Any]) -> None:
        values = tuple(results)
        by_hypothesis = {hypothesis: [item for item in values
                                     if item["identity"]["hypothesis"] == hypothesis]
                         for hypothesis in HYPOTHESES}
        h1 = by_hypothesis["H1"]
        self._csv_auto(store.path / "h1/h1-overall.csv",
                       self._aggregate(h1, ("family", "method")))
        self._csv_auto(store.path / "h1/h1-by-dataset.csv",
                       self._aggregate(h1, ("family", "dataset", "method", "feature_view")))
        self._csv_auto(store.path / "h1/h1-by-scenario.csv",
                       self._aggregate(h1, ("family", "scenario", "method", "feature_view")))
        self._csv_auto(store.path / "h1/h1-structural-ablation.csv",
                       self._aggregate(self._family(h1, "H1-B"), ("graph_mode", "pruning_mode")))
        self._csv_auto(store.path / "h1/h1-modality-ablation.csv",
                       self._aggregate(self._family(h1, "H1-C"), ("feature_view",)))
        self._csv_auto(store.path / "h1/h1-pruning.csv", [self._row(item) for item in h1])

        h2 = by_hypothesis["H2"]
        for name in ("h2-model-comparison.csv", "h2-performance.csv",
                     "h2-runtime-cost.csv"):
            self._csv_auto(store.path / "h2" / name,
                           self._aggregate(h2, ("method", "feature_view")))
        self._csv_auto(store.path / "h2/h2-seed-stability.csv", self._seed_stability(h2))

        h3 = by_hypothesis["H3"]
        self._csv_auto(store.path / "h3/h3-automatic-evaluation.csv",
                       [self._row(item) for item in h3])
        self._csv_auto(store.path / "h3/h3-by-mode.csv",
                       self._aggregate(h3, ("explanation_mode",)))
        self._csv_auto(store.path / "h3/h3-fallbacks.csv",
                       [self._row(item) for item in h3
                        if item.get("result", {}).get("used_fallback")])
        self._csv_auto(store.path / "h3/h3-cost-latency.csv",
                       self._aggregate(h3, ("explanation_mode",)))

        h4 = by_hypothesis["H4"]
        self._csv_auto(store.path / "h4/h4-by-operator.csv",
                       self._aggregate(h4, ("operator",)))
        self._csv_auto(store.path / "h4/h4-by-severity.csv",
                       self._aggregate(h4, ("severity",)))
        self._csv_auto(store.path / "h4/h4-by-modality.csv",
                       self._aggregate(h4, ("target_modalities", "severity")))
        self._csv_auto(store.path / "h4/h4-confidence-abstention.csv",
                       self._aggregate(h4, ("operator", "severity")))
        self._csv_auto(store.path / "h4/h4-degradation-curves.csv",
                       [self._row(item) for item in h4])
        paired = {"H1": self._paired_rows(values, "H1"),
                  "H2": self._paired_rows(values, "H2")}
        stat_fields = ("family", "comparison", "metric", "pairs", "value_a", "value_b",
                       "delta_absolute", "delta_relative", "ci95_lower", "ci95_upper",
                       "mcnemar_p_value", "holm_adjusted_p_value")
        self._csv(store.path / "h1/h1-paired-statistics.csv", paired["H1"], stat_fields)
        self._csv(store.path / "h2/h2-paired-statistics.csv", paired["H2"], stat_fields)
        store.write_json("statistics/paired-comparisons.json",
                         {"schema_version": "paired-statistics-v3", "families": paired})
        blinded, condition_map = self._blind_h3(values)
        self._csv(store.path / "h3" / "h3-human-evaluation-blinded.csv", blinded,
                  ("evaluation_item_id", "incident_id", "condition_code", "explanation",
                   "supporting_context", "utility", "clarity", "perceived_confidence",
                   "traceability", "uncertainty_adequacy", "reviewer_id"))
        condition_path = store.write_json("h3/h3-human-condition-map.json", condition_map)
        try:
            condition_path.chmod(0o600)
        except OSError:
            pass
        statuses = {}
        for item in values:
            statuses[item["status"]["state"]] = statuses.get(item["status"]["state"], 0) + 1
        store.write_json("paper/summary-metrics.json", {"runs": len(values), "statuses": statuses})
        summaries = []
        for hypothesis in HYPOTHESES:
            successful = sum(item["identity"]["hypothesis"] == hypothesis
                             and item["status"]["state"] == "SUCCESS" for item in values)
            state = "INCONCLUSIVE"
            summaries.append(f"- {hypothesis}: {state} (runs SUCCESS={successful}; classificação descritiva, não confirmação automática).")
        (store.path / "paper").mkdir(parents=True, exist_ok=True)
        CampaignStoreV3._atomic(store.path / "paper/hypothesis-summary.md",
                                "# Hypothesis summary\n\n" + "\n".join(summaries) + "\n")
        CampaignStoreV3._atomic(store.path / "paper/experiment-validity-report.md",
                                "# Experiment validity report\n\n"
                                f"Status: {'COMPLETE' if validation['complete'] else 'INCOMPLETE'}\n\n"
                                + "\n".join(f"- {issue}" for issue in validation["issues"]) + "\n")
        store.write_sha256s()

    @staticmethod
    def _row(item: Mapping[str, Any]) -> dict[str, Any]:
        row = {"run_id": item["identity"]["run_id"],
                "family": item["identity"]["experiment_family"],
                "incident_id": item["input"]["incident_id"],
                "dataset": item["input"]["dataset"], "system": item["input"]["system"],
                "scenario": item["input"].get("scenario") or item["input"].get("fault_type"),
                "status": item["status"]["state"], "method": item["configuration"]["method"],
                "feature_view": item["input"]["feature_view_hash"],
                "graph_mode": item["configuration"].get("graph_mode"),
                "pruning_mode": item["configuration"].get("pruning_mode"),
                "explanation_mode": item["configuration"]["explanation_mode"],
                "operator": item["configuration"]["degradation_operator"],
                "target_modalities": ("|".join(operator_policy(
                    item["configuration"]["degradation_operator"]).modalities)
                    if item["configuration"]["degradation_operator"] else None),
                "severity": item["configuration"]["degradation_severity"],
                "metrics": canonical_json(item.get("evaluation", {}))}
        for name, value in item.get("evaluation", {}).items():
            if value is None or type(value) is bool or isinstance(value, (int, float, str)):
                row[name] = value
        return row

    @staticmethod
    def _family(items: Iterable[Mapping[str, Any]], family: str) -> list[Mapping[str, Any]]:
        return [item for item in items if item["identity"]["experiment_family"] == family]

    @classmethod
    def _aggregate(cls, items: Iterable[Mapping[str, Any]],
                   group_fields: tuple[str, ...]) -> list[dict[str, Any]]:
        rows = [cls._row(item) for item in items]
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(tuple(row.get(field) for field in group_fields), []).append(row)
        output = []
        reserved = set(cls._row_fields())
        for key, group in sorted(groups.items(), key=lambda item: canonical_json(item[0])):
            record = {field: value for field, value in zip(group_fields, key)}
            record.update({"planned_runs": len(group),
                           "successful_runs": sum(row["status"] == "SUCCESS" for row in group),
                           "skipped_runs": sum(row["status"].startswith("SKIPPED_") for row in group),
                           "failed_runs": sum(row["status"].startswith("FAILED_") for row in group)})
            metric_names = sorted(set().union(*(set(row) for row in group)) - reserved)
            for name in metric_names:
                values = [row.get(name) for row in group if row["status"] == "SUCCESS"]
                booleans = [value for value in values if type(value) is bool]
                numbers = [float(value) for value in values
                           if not isinstance(value, bool) and isinstance(value, (int, float))]
                if booleans:
                    record[f"{name}_rate"] = sum(booleans) / len(booleans)
                elif numbers:
                    record[f"{name}_mean"] = statistics.fmean(numbers)
            output.append(record)
        return output

    @classmethod
    def _seed_stability(cls, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        aggregates = cls._aggregate(items, ("method",))
        by_method = {row["method"]: row for row in aggregates}
        for method, group in __import__("itertools").groupby(
                sorted(items, key=lambda item: item["configuration"]["method"]),
                key=lambda item: item["configuration"]["method"]):
            values = [item.get("evaluation", {}).get("macro_f1") for item in group]
            numbers = [float(value) for value in values
                       if not isinstance(value, bool) and isinstance(value, (int, float))]
            by_method[method]["macro_f1_standard_deviation"] = (
                statistics.stdev(numbers) if len(numbers) > 1 else 0.0 if numbers else None)
        return [by_method[key] for key in sorted(by_method)]

    @staticmethod
    def _row_fields() -> tuple[str, ...]:
        return ("run_id", "family", "incident_id", "dataset", "system", "status",
                "method", "feature_view", "graph_mode", "pruning_mode",
                "scenario", "explanation_mode", "operator", "target_modalities",
                "severity", "metrics")

    @staticmethod
    def _paired_rows(values: Iterable[Mapping[str, Any]], hypothesis: str) -> list[dict[str, Any]]:
        family = "H1-A" if hypothesis == "H1" else "H2-MODELS"
        selected = [item for item in values
                    if item["identity"]["hypothesis"] == hypothesis
                    and item["identity"]["experiment_family"] == family
                    and item["status"]["state"] == "SUCCESS"]
        by_method: dict[str, dict[str, dict[str, Any]]] = {}
        for item in selected:
            metric = item.get("evaluation", {}).get("macro_f1")
            if isinstance(metric, bool) or not isinstance(metric, (int, float)):
                continue
            ranking = item.get("result", {}).get("predicted_ranking", ())
            predicted = ranking[0].get("service_name") if ranking else None
            actual = set(item.get("evaluation", {}).get("ground_truth_services", ()))
            pair_id = sha256_value({
                "dataset": item["input"]["dataset"], "system": item["input"]["system"],
                "incident": item["input"]["incident_id"],
                "protocol": item["identity"].get("protocol"),
                "seed": item["input"]["seed"],
                "repetition": item["identity"].get("repetition")})
            by_method.setdefault(item["configuration"]["method"], {})[pair_id] = {
                "macro_f1": float(metric), "top1_correct": predicted in actual}
        target = by_method.get("xgboost-rca-v3", {})
        rows = []
        raw_p = []
        for method in sorted(set(by_method) - {"xgboost-rca-v3"}):
            left = by_method[method]
            if set(left) != set(target) or not left:
                continue
            comparison = ExperimentStatisticsV3.compare_paired(
                left, target, metric="macro_f1", samples=100)
            value_a = statistics.fmean(item["macro_f1"] for item in left.values())
            value_b = statistics.fmean(item["macro_f1"] for item in target.values())
            delta = value_b - value_a
            p_value = comparison["mcnemar"]["p_value_two_sided"]
            rows.append({"family": family, "comparison": f"{method}__vs__xgboost-rca-v3",
                         "metric": "macro_f1", "pairs": comparison["pairs"],
                         "value_a": value_a, "value_b": value_b,
                         "delta_absolute": delta,
                         "delta_relative": delta / value_a if value_a else None,
                         "ci95_lower": comparison["continuous"]["ci95_lower"],
                         "ci95_upper": comparison["continuous"]["ci95_upper"],
                         "mcnemar_p_value": p_value})
            raw_p.append(float(p_value))
        adjusted = ExperimentStatisticsV3.holm(raw_p) if raw_p else ()
        for row, p_value in zip(rows, adjusted):
            row["holm_adjusted_p_value"] = p_value
        return rows

    @staticmethod
    def _csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> None:
        import io
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=tuple(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in writer.fieldnames})
        CampaignStoreV3._atomic(path, stream.getvalue())

    @classmethod
    def _csv_auto(cls, path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
        values = list(rows)
        fields = []
        for row in values:
            for field_name in row:
                if field_name not in fields:
                    fields.append(field_name)
        cls._csv(path, values, fields or ("no_data",))

    @staticmethod
    def _blind_h3(values: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
        h3 = [item for item in values if item["identity"]["hypothesis"] == "H3"
              and item["status"]["state"] == "SUCCESS"]
        modes = sorted({item["configuration"]["explanation_mode"] for item in h3})
        mapping = {f"C{index + 1}-{sha256_value(mode)[:8]}": mode for index, mode in enumerate(modes)}
        reverse = {mode: code for code, mode in mapping.items()}
        rows = []
        for item in sorted(h3, key=lambda value: value["identity"]["run_id"]):
            rows.append({"evaluation_item_id": sha256_value(item["identity"]["run_id"])[:16],
                         "incident_id": item["input"]["incident_id"],
                         "condition_code": reverse[item["configuration"]["explanation_mode"]],
                         "explanation": item["result"].get("summary", ""),
                         "supporting_context": canonical_json(
                             ExperimentReportBuilderV3._blind_context(
                                 item["result"].get("supporting_context", {}))),
                         "utility": "", "clarity": "", "perceived_confidence": "",
                         "traceability": "", "uncertainty_adequacy": "", "reviewer_id": ""})
        return rows, mapping

    @staticmethod
    def _blind_context(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key.replace("raw_telemetry", "observation"):
                    ExperimentReportBuilderV3._blind_context(item)
                    for key, item in value.items() if key != "mode"}
        if isinstance(value, (tuple, list)):
            return [ExperimentReportBuilderV3._blind_context(item) for item in value]
        if isinstance(value, str) and value in EXPLANATION_MODES:
            return "BLINDED_CONDITION"
        return value

    @classmethod
    def import_human_ratings(cls, path: str | Path) -> dict[str, Any]:
        """Valida ratings cegos e calcula concordância apenas com pares suficientes."""
        with Path(path).open(encoding="utf-8", newline="") as stream:
            rows = tuple(csv.DictReader(stream))
        required = {"evaluation_item_id", "reviewer_id", *cls.HUMAN_RUBRIC_FIELDS}
        if not rows or not required <= set(rows[0]):
            raise ValueError("Arquivo de ratings vazio ou sem colunas obrigatórias.")
        normalized = []
        for row in rows:
            if not row["evaluation_item_id"].strip() or not row["reviewer_id"].strip():
                raise ValueError("Ratings requerem item e reviewer não vazios.")
            ratings = {}
            for field_name in cls.HUMAN_RUBRIC_FIELDS:
                try:
                    value = float(row[field_name])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Rating inválido em {field_name}.") from exc
                if not 1 <= value <= 5 or not math.isfinite(value):
                    raise ValueError(f"Rating {field_name} deve estar entre 1 e 5.")
                ratings[field_name] = value
            normalized.append((row["evaluation_item_id"], row["reviewer_id"], ratings))
        by_item: dict[str, list[tuple[str, Mapping[str, float]]]] = {}
        for item_id, reviewer_id, ratings in normalized:
            by_item.setdefault(item_id, []).append((reviewer_id, ratings))
        pairs = []
        for items in by_item.values():
            unique = {reviewer: ratings for reviewer, ratings in items}
            reviewers = sorted(unique)
            for left_index, left in enumerate(reviewers):
                for right in reviewers[left_index + 1:]:
                    pairs.append((unique[left], unique[right]))
        if not pairs:
            return {"schema_version": "human-agreement-v3", "status": "INSUFFICIENT_DATA",
                    "items": len(by_item), "reviewers": len({row[1] for row in normalized}),
                    "paired_item_reviewer_comparisons": 0, "agreement": None}
        agreement = {field_name: sum(
            left[field_name] == right[field_name] for left, right in pairs) / len(pairs)
            for field_name in cls.HUMAN_RUBRIC_FIELDS}
        return {"schema_version": "human-agreement-v3", "status": "CALCULATED",
                "items": len(by_item), "reviewers": len({row[1] for row in normalized}),
                "paired_item_reviewer_comparisons": len(pairs),
                "agreement": agreement,
                "agreement_definition": "exact percent agreement on the 1-5 rubric"}
