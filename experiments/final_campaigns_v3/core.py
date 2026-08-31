from __future__ import annotations

import hashlib
import gc
import ctypes
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from itertools import combinations
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.multimodal_v3.core import (
    DatasetCapabilityV3,
    ExperimentConfigV3,
    ExperimentPlannerV3,
    ExperimentRunV3,
    FeatureRegistryV3,
    V2_FREEZE_HASHES,
    canonical_json,
    file_sha256,
    sha256_value,
)
from multimodal_v1.benchmark_v3 import BENCHMARK_VERSION
from multimodal_v1.degradation_v3 import HARNESS_VERSION, OPERATORS, operator_policy


WIRING_VERSION = "final-campaign-wiring-v3"
REGISTRY_VERSION = "frozen-scientific-dataset-registry-v3"
H1A_METHODS = (
    "temporal-baseline", "max-zscore", "first-anomalous", "xgboost-rca-v3",
)
H1B_VIEWS = ("NO_GRAPH", "GRAPH", "GRAPH_BUSINESS")
H1C_VIEWS = (
    "METRIC_ONLY", "TRACE_ONLY", "METRIC_TRACE", "METRIC_TRACE_LOG",
    "METRIC_TRACE_LOG_BUSINESS",
)
H2_METHODS = (
    "temporal-baseline", "max-zscore", "first-anomalous", "logistic-regression",
    "random-forest", "xgboost-rca-v3", "mlp-light-v3",
)
H3_MODES = ("generic", "raw_telemetry", "evidence_grounded")
H4_SEVERITIES = (0.0, 0.1, 0.3, 0.5)
PROTOCOLS = (
    "within-dataset", "cross-system", "cross-source", "leave-one-system-out",
)
FORBIDDEN_SCIENTIFIC_INPUT_KEYS = frozenset({
    "ground_truth", "root_cause", "root_cause_service", "ground_truth_service",
    "fault_target", "fault_target_service", "fault_type", "scenario",
    "scenario_label", "expected_service", "expected_rca_answer",
})
FAMILY_TO_HYPOTHESIS = {
    "H1-A": "H1", "H1-B": "H1", "H1-C": "H1", "H2": "H2", "H3": "H3", "H4": "H4",
}
FAMILY_TO_ORCHESTRATOR = {
    "H1-A": "H1-A", "H1-B": "H1-B", "H1-C": "H1-C",
    "H2": "H2-MODELS", "H3": "H3-EXPLANATIONS", "H4": "H4-DEGRADATION",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON obrigatório inválido: {path}") from exc


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("Referência de arquivo deve ser texto não vazio.")
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Referência escapa do dataset: {relative}")
    return target


def _verify_sha256_manifest(dataset_root: Path, manifest: Path) -> dict[str, Any]:
    if not manifest.is_file():
        return {"valid": False, "reason": "checksum_manifest_missing", "checked": 0}
    checked, failures, seen = 0, [], set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if "  " not in line:
            failures.append(f"malformed_line:{line_number}")
            continue
        digest, relative = line.split("  ", 1)
        if (relative in seen or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            failures.append(f"invalid_entry:{line_number}")
            continue
        seen.add(relative)
        try:
            target = _safe_path(dataset_root, relative)
        except ValueError:
            failures.append(f"unsafe_path:{relative}")
            continue
        actual = file_sha256(target) if target.is_file() else None
        checked += 1
        if actual != digest:
            failures.append(f"checksum_mismatch:{relative}")
    return {"valid": not failures, "checked": checked, "failures": failures}


@dataclass(frozen=True)
class FrozenDataset:
    dataset_id: str
    kind: str
    root: Path
    expected_incidents: int
    expected_usable: int
    checksum_manifest: Path
    checksum_manifest_sha256: str
    index: Path
    capabilities: Path
    materialization_report: Path
    frozen: bool = True

    @property
    def fingerprint(self) -> str:
        return sha256_value({
            "dataset_id": self.dataset_id,
            "kind": self.kind,
            "root": str(self.root),
            "expected_incidents": self.expected_incidents,
            "expected_usable": self.expected_usable,
            "checksum_manifest_sha256": self.checksum_manifest_sha256,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id, "kind": self.kind, "root": str(self.root),
            "expected_incidents": self.expected_incidents,
            "expected_usable": self.expected_usable, "frozen": self.frozen,
            "checksum_manifest": str(self.checksum_manifest),
            "checksum_manifest_sha256": self.checksum_manifest_sha256,
            "index": str(self.index), "capabilities": str(self.capabilities),
            "materialization_report": str(self.materialization_report),
            "dataset_fingerprint": self.fingerprint,
        }


class FrozenDatasetRegistry:
    def __init__(self, repository_root: Path, datasets: Iterable[FrozenDataset]):
        self.repository_root = repository_root.resolve()
        values = tuple(datasets)
        if not values or len({item.dataset_id for item in values}) != len(values):
            raise ValueError("Registry requer dataset_ids únicos.")
        if any(not item.frozen for item in values):
            raise ValueError("Registry final aceita apenas datasets frozen.")
        self.datasets = {item.dataset_id: item for item in values}

    @classmethod
    def load(cls, path: str | Path, repository_root: str | Path) -> "FrozenDatasetRegistry":
        config_path, repo = Path(path).resolve(), Path(repository_root).resolve()
        payload = _read_json(config_path)
        if payload.get("schema_version") != REGISTRY_VERSION:
            raise ValueError("Versão do dataset registry inválida.")
        datasets = []
        for raw in payload.get("datasets", ()):
            root = (repo / raw["root"]).resolve()
            if root != repo and repo not in root.parents:
                raise ValueError("Dataset registry referencia root fora do repositório.")
            datasets.append(FrozenDataset(
                raw["dataset_id"], raw["kind"], root,
                int(raw["expected_incidents"]), int(raw["expected_usable"]),
                _safe_path(root, raw["checksum_manifest"]), raw["checksum_manifest_sha256"],
                _safe_path(root, raw["index"]), _safe_path(root, raw["capabilities"]),
                _safe_path(root, raw["materialization_report"]), bool(raw.get("frozen", False)),
            ))
        return cls(repo, datasets)

    def snapshot(self) -> dict[str, Any]:
        values = [self.datasets[key].to_dict() for key in sorted(self.datasets)]
        return {"schema_version": REGISTRY_VERSION, "datasets": values,
                "registry_sha256": sha256_value(values)}

    def capabilities(self) -> tuple[DatasetCapabilityV3, ...]:
        values = []
        for dataset in self.datasets.values():
            payload = _read_json(dataset.capabilities)
            if payload.get("count") != len(payload.get("capabilities", ())):
                raise ValueError(f"{dataset.dataset_id}: capabilities count divergente.")
            for item in payload["capabilities"]:
                values.append(DatasetCapabilityV3(**item))
        if len({item.incident_id for item in values}) != len(values):
            raise ValueError("incident_id duplicado entre datasets frozen.")
        return tuple(sorted(values, key=lambda item: item.incident_id))

    def immutability_snapshot(self) -> dict[str, Any]:
        rows = []
        for dataset in self.datasets.values():
            files = (dataset.checksum_manifest, dataset.index, dataset.capabilities,
                     dataset.materialization_report)
            hashes = {str(path.relative_to(dataset.root)): file_sha256(path)
                      if path.is_file() else None for path in files}
            rows.append({"dataset_id": dataset.dataset_id, "files": hashes,
                         "checksum_manifest_sha256": hashes.get(
                             str(dataset.checksum_manifest.relative_to(dataset.root)))})
        return {"schema_version": "frozen-dataset-immutability-v1", "datasets": rows,
                "snapshot_sha256": sha256_value(rows)}

    def preflight(self, *, verify_all_hashes: bool = True) -> dict[str, Any]:
        reports = []
        for dataset in self.datasets.values():
            issues = []
            if not dataset.root.is_dir():
                issues.append("dataset_root_missing")
                reports.append({"dataset_id": dataset.dataset_id, "valid": False,
                                "issues": issues})
                continue
            if file_sha256(dataset.checksum_manifest) != dataset.checksum_manifest_sha256:
                issues.append("checksum_manifest_hash_mismatch")
            checksum = (_verify_sha256_manifest(dataset.root, dataset.checksum_manifest)
                        if verify_all_hashes else {"valid": True, "checked": 0, "deferred": True})
            if not checksum["valid"]:
                issues.extend(checksum.get("failures", ()))
            index, caps, material = (_read_json(dataset.index), _read_json(dataset.capabilities),
                                     _read_json(dataset.materialization_report))
            rows = index.get("incidents", ())
            if index.get("count") != dataset.expected_incidents or len(rows) != dataset.expected_incidents:
                issues.append("incident_count_mismatch")
            if caps.get("count") != dataset.expected_incidents \
                    or len(caps.get("capabilities", ())) != dataset.expected_incidents:
                issues.append("capability_count_mismatch")
            usable_index = sum(item.get("usable_for_benchmark") is True for item in rows)
            if usable_index != dataset.expected_usable:
                issues.append("usable_index_mismatch")
            integrity = {}
            for item in rows:
                status = item.get("integrity_status", "UNKNOWN")
                integrity[status] = integrity.get(status, 0) + 1
            gates: dict[str, Any] = {}
            if dataset.kind == "rcaeval":
                expected = {"valid": dataset.expected_incidents,
                            "usable_for_benchmark": dataset.expected_usable,
                            "unusable": 0, "errors": 0}
                for key, value in expected.items():
                    if material.get(key) != value:
                        issues.append(f"materialization_{key}_mismatch")
                if any("business_event" in item.get("available_modalities", ())
                       for item in caps.get("capabilities", ())):
                    issues.append("fabricated_business_event_capability")
            elif dataset.kind == "causalods-local-v3":
                if material.get("completed") != 500 or material.get("usable") != 500 \
                        or material.get("integrity") != {"CLEAN": 500}:
                    issues.append("local_materialization_summary_mismatch")
                campaign = _read_json(dataset.root / "campaign-manifest.json")
                if campaign.get("target_runs") != 500 or campaign.get("materialized_runs") != 500:
                    issues.append("local_campaign_count_mismatch")
                audit = _read_json(dataset.root / "audits/final-audit.json")
                gate_fields = {
                    "identity": "identity_audit", "ground_truth": "ground_truth_audit",
                    "fault_controller_binding": "fault_controller_binding",
                    "full_modality": "full_modality_audit",
                    "metric_provenance": "metric_pipeline_provenance",
                    "telemetry_transport": "telemetry_transport_audit",
                    "anti_leakage": "anti_leakage_audit",
                    "service_reset": "service_state_reset_audit",
                }
                for gate, field_name in gate_fields.items():
                    gates[gate] = audit.get(field_name)
                    if gates[gate] != "PASS":
                        issues.append(f"local_gate_failed:{gate}")
                isolation = audit.get("run_isolation_audit", {})
                gates["run_isolation"] = isolation.get("status")
                if gates["run_isolation"] != "PASS":
                    issues.append("local_gate_failed:run_isolation")
                resume = audit.get("resume_audit", {}).get("resume_validation", {})
                recovery_valid = (resume.get("attempted") == 500
                                  and resume.get("completed") == 500
                                  and resume.get("resumed", 0) + resume.get("new_executions", 0) == 500)
                gates["resume"] = "PASS" if recovery_valid else "FAIL"
                if not recovery_valid:
                    issues.append("local_gate_failed:resume")
                scenarios = {}
                for item in rows:
                    scenarios[item.get("scenario")] = scenarios.get(item.get("scenario"), 0) + 1
                if scenarios != {"healthy": 100, "order_error": 100, "order_latency": 100,
                                 "payment_error": 100, "payment_latency": 100}:
                    issues.append("scenario_distribution_mismatch")
                full = {"trace", "log", "metric", "business_event"}
                if any(set(item.get("capabilities", ())) != full for item in rows):
                    issues.append("local_not_full_multimodal")
            reports.append({
                "dataset_id": dataset.dataset_id, "kind": dataset.kind,
                "root": str(dataset.root), "expected_incidents": dataset.expected_incidents,
                "observed_incidents": len(rows), "usable": usable_index,
                "integrity": integrity, "checksum_validation": checksum,
                "gates": gates, "valid": not issues, "issues": issues,
                "dataset_fingerprint": dataset.fingerprint,
            })
        return {"schema_version": "dataset-preflight-v3", "generated_at": _utc_now(),
                "valid": all(item["valid"] for item in reports), "datasets": reports}


class _CachedExperimentConfigV3(ExperimentConfigV3):
    """Same frozen contract, with a 13C-local cache for its canonical hash."""

    @property
    def configuration_sha256(self) -> str:
        cached = getattr(self, "_final_v3_configuration_sha256", None)
        if cached is None:
            cached = sha256_value(self.to_dict())
            object.__setattr__(self, "_final_v3_configuration_sha256", cached)
        return cached


class _CachedExperimentPlannerV3(ExperimentPlannerV3):
    """Caches immutable FeatureView hashes without changing planner semantics."""

    def __init__(self, config: ExperimentConfigV3):
        super().__init__(config)
        self._final_view_hash_cache: dict[tuple[Any, ...], str] = {}

    def _view_hash(self, include: tuple[str, ...] | None = None,
                   exclude: tuple[str, ...] = ()) -> str:
        key = (include, exclude)
        if key not in self._final_view_hash_cache:
            self._final_view_hash_cache[key] = super()._view_hash(include, exclude)
        return self._final_view_hash_cache[key]

    def protocol_assignments(self, protocol: str, seed: int):
        key = (
            tuple(sha256_value(item.to_dict()) for item in self.config.datasets),
            protocol, seed,
            self.config.test_fraction, self.config.validation_fraction,
            self.config.held_out_systems, self.config.held_out_sources,
        )
        cached = _PROTOCOL_ASSIGNMENT_CACHE.get(key)
        if cached is None:
            cached = super().protocol_assignments(protocol, seed)
            _PROTOCOL_ASSIGNMENT_CACHE[key] = cached
        return cached


_PROTOCOL_ASSIGNMENT_CACHE: dict[tuple[Any, ...], tuple[Any, Any]] = {}


def build_capability_matrix(capabilities: Iterable[DatasetCapabilityV3]) -> dict[str, Any]:
    rows = []
    requirements = ExperimentPlannerV3.MODALITY_VIEWS
    for item in capabilities:
        available = set(item.available_modalities)
        h1b = {"NO_GRAPH": True, "GRAPH": True,
               "GRAPH_BUSINESS": "business_event" in available}
        h1c = {view: set(required) <= available for view, required in requirements.items()}
        rows.append({
            "dataset": item.source_dataset, "incident": item.incident_id,
            "system": item.source_system, "available_modalities": list(item.available_modalities),
            "available": {name: name in available for name in (
                "trace", "log", "metric", "business_event")},
            "applicable_h1b_views": h1b, "applicable_h1c_views": h1c,
        })
    summary = {
        "incidents": len(rows),
        "by_dataset": {dataset: sum(row["dataset"] == dataset for row in rows)
                       for dataset in sorted({row["dataset"] for row in rows})},
        "h1b_applicable": {view: sum(row["applicable_h1b_views"][view] for row in rows)
                            for view in H1B_VIEWS},
        "h1c_applicable": {view: sum(row["applicable_h1c_views"][view] for row in rows)
                            for view in H1C_VIEWS},
    }
    return {"schema_version": "capability-matrix-v3", "valid": bool(rows),
            "summary": summary, "incidents": rows}


def load_campaign_config(path: str | Path) -> dict[str, Any]:
    payload = _read_json(Path(path))
    required = {"schema_version", "campaign_id", "family", "protocols", "seeds",
                "execution_mode", "model_version", "model_hash"}
    if set(payload) - (required | {"sample_size", "repetitions", "tuning_budget",
                                  "test_fraction", "validation_fraction", "criteria"}):
        raise ValueError("Configuração final contém campos desconhecidos.")
    if not required <= set(payload) or payload["schema_version"] != "final-campaign-config-v3":
        raise ValueError("Configuração final incompleta ou com versão inválida.")
    if payload["family"] not in FAMILY_TO_HYPOTHESIS:
        raise ValueError("Família de campanha inválida.")
    if tuple(payload["protocols"]) not in (PROTOCOLS, ("within-dataset",)):
        raise ValueError("Protocolos devem usar exatamente o contrato congelado.")
    if payload["family"] == "H3" and payload.get("sample_size") != 40:
        raise ValueError("H3 exige sample_size=40.")
    return payload


def build_experiment_config(payload: Mapping[str, Any],
                            capabilities: Iterable[DatasetCapabilityV3]) -> ExperimentConfigV3:
    family = payload["family"]
    capabilities = tuple(capabilities)
    # The frozen local dataset keeps all 100 healthy controls usable.  Top-1
    # RCA campaigns, however, cannot invent a positive class for their null
    # root cause; those controls remain available to the separate abstention
    # analysis and are explicitly ineligible here.
    if family in {"H1-A", "H1-B", "H1-C", "H2", "H3", "H4"}:
        capabilities = tuple(item for item in capabilities if item.fault_type != "healthy")
    return _CachedExperimentConfigV3(
        campaign_id=payload["campaign_id"], datasets=tuple(capabilities),
        hypotheses=(FAMILY_TO_HYPOTHESIS[family],), protocols=tuple(payload["protocols"]),
        seeds=tuple(payload["seeds"]), repetitions=int(payload.get("repetitions", 1)),
        execution_mode=payload["execution_mode"], sample_size=payload.get("sample_size"),
        explanation_modes=H3_MODES,
        degradation_operators=tuple(OPERATORS), severities=H4_SEVERITIES,
        allow_live_llm=bool(payload.get("criteria", {}).get("allow_live_llm", False)),
        model_version=payload["model_version"], model_hash=payload["model_hash"],
        tuning_budget=int(payload.get("tuning_budget", 1)),
        test_fraction=float(payload.get("test_fraction", .2)),
        validation_fraction=float(payload.get("validation_fraction", .2)),
        criteria=dict(payload.get("criteria", {})),
    )


def build_plan(payload: Mapping[str, Any], capabilities: Iterable[DatasetCapabilityV3]) -> tuple[
        ExperimentConfigV3, tuple[ExperimentRunV3, ...]]:
    config = build_experiment_config(payload, capabilities)
    family = payload["family"]
    wanted = FAMILY_TO_ORCHESTRATOR[family]
    runs = tuple(run for run in _CachedExperimentPlannerV3(config).plan()
                 if run.experiment_family == wanted)
    if not runs:
        raise ValueError(f"Plano vazio para {family}.")
    return config, runs


def plan_summary(family: str, runs: Iterable[ExperimentRunV3]) -> dict[str, Any]:
    values = tuple(runs)
    dimensions = {
        "datasets": sorted({run.source_dataset for run in values}),
        "incidents": len({run.incident_id for run in values}),
        "protocols": sorted({run.protocol for run in values}),
        "methods": sorted({run.method for run in values}),
        "feature_views": sorted({run.feature_view for run in values}),
        "seeds": sorted({run.seed for run in values}),
        "severity_levels": sorted({run.degradation_severity for run in values
                                    if run.degradation_severity is not None}),
        "degradation_operators": sorted({run.degradation_operator for run in values
                                         if run.degradation_operator}),
        "explanation_modes": sorted({run.explanation_mode for run in values
                                     if run.explanation_mode}),
    }


def compact_plan(payload: Mapping[str, Any], capabilities: Iterable[DatasetCapabilityV3]) -> tuple[
        ExperimentConfigV3, dict[str, Any], dict[str, Any], tuple[ExperimentRunV3, ...]]:
    """Derive real counts from frozen folds without allocating every final run object."""
    config = build_experiment_config(payload, capabilities)
    planner = _CachedExperimentPlannerV3(config)
    family = payload["family"]
    planned = applicable = 0
    assignment_records, h3_runs = [], []
    methods: set[str] = set()
    views: set[str] = set()
    severities: set[float] = set()
    operators: set[str] = set()
    modes: set[str] = set()
    incidents: set[str] = set()
    pairing_detail: dict[str, Any] = {}
    factor_sets: dict[str, set[str]] = {}
    h4_group_count = 0
    h4_group_digest = hashlib.sha256()
    split_hash_cache: dict[tuple[str, ...], str] = {}

    def split_hash(values: tuple[str, ...]) -> str:
        if values not in split_hash_cache:
            split_hash_cache[values] = sha256_value(values)
        return split_hash_cache[values]

    for protocol in config.protocols:
        for seed in config.seeds:
            assignments, manifests = planner.protocol_assignments(protocol, seed)
            sampled = (planner._h3_sample(tuple(item.capability for item in assignments), seed=seed)
                       if family == "H3" else frozenset())
            for assignment in assignments:
                item = assignment.capability
                base = assignment.applicable and item.usable_for_benchmark
                if family != "H3" or item.incident_id in sampled:
                    incidents.add(item.incident_id)
                assignment_records.append({
                    "incident_id": item.incident_id, "protocol": protocol,
                    "held_out": assignment.held_out, "seed": seed,
                    "manifest_sha256": item.manifest_sha256,
                    "available_modalities": list(item.available_modalities),
                    "applicable_protocol": assignment.applicable,
                    "train_sha256": split_hash(assignment.train_incident_ids),
                    "validation_sha256": split_hash(assignment.validation_incident_ids),
                    "test_sha256": split_hash(assignment.test_incident_ids),
                })
                repetitions = config.repetitions
                def pair_key(repetition: int) -> str:
                    return sha256_value((item.source_dataset, item.source_system, protocol,
                                         assignment.held_out, item.incident_id, seed, repetition,
                                         item.manifest_sha256))
                if family == "H1-A":
                    planned += len(H1A_METHODS) * repetitions
                    applicable += len(H1A_METHODS) * repetitions * int(base)
                    methods.update(H1A_METHODS); views.add("FULL")
                    for repetition in range(repetitions):
                        if base:
                            for method in H1A_METHODS:
                                factor_sets.setdefault(method, set()).add(pair_key(repetition))
                elif family == "H1-B":
                    planned += len(H1B_VIEWS) * repetitions
                    applicable += repetitions * int(base) * (
                        2 + int("business_event" in item.available_modalities))
                    methods.add("xgboost-rca-v3"); views.update(H1B_VIEWS)
                    for repetition in range(repetitions):
                        if base:
                            factor_sets.setdefault("NO_GRAPH", set()).add(pair_key(repetition))
                            factor_sets.setdefault("GRAPH", set()).add(pair_key(repetition))
                            if "business_event" in item.available_modalities:
                                factor_sets.setdefault("GRAPH_BUSINESS", set()).add(pair_key(repetition))
                elif family == "H1-C":
                    planned += len(H1C_VIEWS) * repetitions
                    applicable += repetitions * int(base) * sum(
                        set(required) <= set(item.available_modalities)
                        for required in ExperimentPlannerV3.MODALITY_VIEWS.values())
                    methods.add("xgboost-rca-v3"); views.update(H1C_VIEWS)
                    for repetition in range(repetitions):
                        if base:
                            for view, required in ExperimentPlannerV3.MODALITY_VIEWS.items():
                                if set(required) <= set(item.available_modalities):
                                    factor_sets.setdefault(view, set()).add(pair_key(repetition))
                elif family == "H2":
                    planned += len(H2_METHODS) * repetitions
                    applicable += len(H2_METHODS) * repetitions * int(base)
                    methods.update(H2_METHODS); views.add("FULL")
                    for repetition in range(repetitions):
                        if base:
                            for method in H2_METHODS:
                                factor_sets.setdefault(method, set()).add(pair_key(repetition))
                elif family == "H3" and item.incident_id in sampled:
                    planned += len(H3_MODES) * repetitions
                    applicable += len(H3_MODES) * repetitions * int(base)
                    methods.add("xgboost-rca-v3"); views.add("FULL"); modes.update(H3_MODES)
                    for repetition in range(repetitions):
                        for mode in H3_MODES:
                            h3_runs.append(planner._run(
                                assignment, "H3", "H3-EXPLANATIONS", seed=seed,
                                repetition=repetition, method="xgboost-rca-v3",
                                explanation_mode=mode))
                elif family == "H4":
                    for operator in OPERATORS:
                        policy = operator_policy(operator)
                        required = (("topology",) if policy.modalities == ("topology",)
                                    else () if set(policy.modalities) & set(item.available_modalities)
                                    else (policy.modalities[0],))
                        planned += len(H4_SEVERITIES) * repetitions
                        applicable += len(H4_SEVERITIES) * repetitions * int(
                            base and set(required) <= set(item.available_modalities))
                    methods.add("xgboost-rca-v3"); views.add("FULL")
                    operators.update(OPERATORS); severities.update(H4_SEVERITIES)
                    for repetition in range(repetitions):
                        for operator in OPERATORS:
                            policy = operator_policy(operator)
                            required = (("topology",) if policy.modalities == ("topology",)
                                        else () if set(policy.modalities) & set(item.available_modalities)
                                        else (policy.modalities[0],))
                            if base and set(required) <= set(item.available_modalities):
                                key = sha256_value((pair_key(repetition), operator, config.model_hash))
                                h4_group_count += 1
                                h4_group_digest.update(key.encode("ascii"))
    def pairwise_report(values: Mapping[str, set[str]]) -> dict[str, Any]:
        reports = []
        for left, right in combinations(sorted(values), 2):
            a, b = values[left], values[right]
            intersection, only_a, only_b = a & b, a - b, b - a
            reports.append({
                "left": left, "right": right, "applicable_A": len(a),
                "applicable_B": len(b), "paired_intersection": len(intersection),
                "only_A": len(only_a), "only_B": len(only_b),
                "paired_intersection_sha256": sha256_value(sorted(intersection)),
                "only_A_sha256": sha256_value(sorted(only_a)),
                "only_B_sha256": sha256_value(sorted(only_b)),
            })
        return {"pair_key": ["dataset", "source_system", "protocol", "held_out",
                             "incident", "seed", "repetition", "source_manifest_hash"],
                "comparisons": reports,
                "valid": bool(reports) and all(item["paired_intersection"] > 0
                                                for item in reports)}

    if family == "H3":
        sample = {run.incident_id for run in h3_runs}
        pairing_valid = len(sample) == 40 and len(h3_runs) == 120
        pairing_detail = {"paired_incidents": len(sample), "same_rca_required": True,
                          "valid": pairing_valid}
    elif family == "H4":
        pairing_valid = h4_group_count > 0
        pairing_detail = {
            "group_key": ["dataset", "source_system", "protocol", "held_out",
                          "incident", "seed", "repetition", "source_manifest_hash",
                          "operator", "model_specification_hash"],
            "groups": h4_group_count, "groups_sha256": h4_group_digest.hexdigest(),
            "invalid_groups": 0,
            "model_hash_invariant": True, "severity_set": list(H4_SEVERITIES),
            "valid": pairing_valid,
        }
    else:
        pairing_detail = pairwise_report(factor_sets)
        pairing_valid = pairing_detail["valid"]
    summary = {
        "schema_version": "final-execution-plan-summary-v3", "family": family,
        "planned_runs": planned, "applicable_runs": applicable,
        "expected_skips": planned - applicable,
        "datasets": sorted({item.source_dataset for item in config.datasets}),
        "incidents": len(incidents), "protocols": list(config.protocols),
        "methods": sorted(methods), "feature_views": sorted(views),
        "seeds": list(config.seeds), "severity_levels": sorted(severities),
        "degradation_operators": sorted(operators), "explanation_modes": sorted(modes),
        "configuration_hashes": [config.configuration_sha256],
        "run_plan_sha256": sha256_value({
            "configuration": config.configuration_sha256, "family": family,
            "assignments": assignment_records, "planned": planned,
            "applicable": applicable,
        }),
        "status_counts": {"SUCCESS-candidate": applicable,
                          "SKIP_NOT_APPLICABLE": planned - applicable},
    }
    return config, summary, pairing_detail, tuple(h3_runs)
    return {
        "schema_version": "final-execution-plan-summary-v3", "family": family,
        "planned_runs": len(values), "applicable_runs": sum(run.eligibility for run in values),
        "expected_skips": sum(not run.eligibility for run in values),
        **dimensions, "configuration_hashes": sorted({run.configuration_hash for run in values}),
        "run_plan_sha256": sha256_value([run.to_dict() for run in values]),
        "status_counts": {
            "SUCCESS-candidate": sum(run.eligibility for run in values),
            "SKIP_NOT_APPLICABLE": sum(not run.eligibility for run in values),
        },
    }


def _pair_key(run: ExperimentRunV3) -> tuple[Any, ...]:
    return (run.source_dataset, run.source_system, run.protocol, run.held_out,
            run.incident_id, run.seed, run.repetition, run.source_manifest_hash)


def audit_pairing(plans: Mapping[str, Iterable[ExperimentRunV3]]) -> dict[str, Any]:
    issues, reports = [], {}
    def comparisons(factors: Mapping[str, set[tuple[Any, ...]]]):
        values = []
        for left, right in combinations(sorted(factors), 2):
            a, b = factors[left], factors[right]
            common, only_a, only_b = a & b, a - b, b - a
            values.append({"left": left, "right": right,
                           "applicable_A": len(a), "applicable_B": len(b),
                           "paired_intersection": len(common),
                           "only_A": len(only_a), "only_B": len(only_b)})
        return values
    for family, run_values in plans.items():
        runs = tuple(run_values)
        if family in {"H1-A", "H2"}:
            expected = set(H1A_METHODS if family == "H1-A" else H2_METHODS)
            groups: dict[tuple[Any, ...], set[str]] = {}
            factors = {method: set() for method in expected}
            for run in runs:
                if run.eligibility:
                    groups.setdefault(_pair_key(run), set()).add(run.method)
                    factors[run.method].add(_pair_key(run))
            bad = [key for key, methods in groups.items() if methods != expected]
            if bad:
                issues.append(f"{family}:method_sets_differ:{len(bad)}")
            pairwise = comparisons(factors)
            reports[family] = {"paired_incident_sets": len(groups),
                               "comparisons": pairwise, "valid": not bad}
        elif family in {"H1-B", "H1-C"}:
            factor = (lambda run: run.feature_view)
            expected = set(H1B_VIEWS if family == "H1-B" else H1C_VIEWS)
            applicable = {name: {_pair_key(run) for run in runs
                                 if factor(run) == name and run.eligibility} for name in expected}
            pairwise = comparisons(applicable)
            reports[family] = {"pairwise_common_incidents": min(
                                   (item["paired_intersection"] for item in pairwise), default=0),
                               "applicable_by_factor": {key: len(value)
                                                        for key, value in sorted(applicable.items())},
                               "comparisons": pairwise,
                               "valid": bool(pairwise) and all(
                                   item["paired_intersection"] > 0 for item in pairwise)}
            if not reports[family]["valid"]:
                issues.append(f"{family}:no_common_applicable_incidents")
        elif family == "H3":
            groups: dict[tuple[Any, ...], set[str]] = {}
            for run in runs:
                if run.eligibility:
                    groups.setdefault(_pair_key(run), set()).add(run.explanation_mode)
            bad = [key for key, modes in groups.items() if modes != set(H3_MODES)]
            if bad:
                issues.append(f"H3:mode_sets_differ:{len(bad)}")
            reports[family] = {"paired_incidents": len(groups), "same_rca_required": True,
                               "valid": not bad and len(groups) == 40}
            if len(groups) != 40:
                issues.append(f"H3:sample_size:{len(groups)}")
        elif family == "H4":
            groups: dict[tuple[Any, ...], list[ExperimentRunV3]] = {}
            for run in runs:
                if run.eligibility:
                    key = (*_pair_key(run), run.degradation_operator)
                    groups.setdefault(key, []).append(run)
            bad = []
            for key, items in groups.items():
                if ({item.degradation_severity for item in items} != set(H4_SEVERITIES)
                        or len({item.model_hash for item in items}) != 1):
                    bad.append(key)
            if bad:
                issues.append(f"H4:severity_or_model_mismatch:{len(bad)}")
            reports[family] = {"degradation_pairs": len(groups), "valid": not bad,
                               "model_hash_invariant": not bad}
    return {"schema_version": "pairing-audit-v3", "valid": not issues,
            "statistical_plan": {"top1": "paired-mcnemar",
                                 "mrr": "paired-bootstrap-95pct",
                                 "multiple_comparisons": "holm",
                                 "macro_f1": "descriptive"},
            "campaigns": reports, "issues": issues}


def deterministic_blind_map(runs: Iterable[ExperimentRunV3], *, seed: int) -> dict[str, Any]:
    values = sorted((run for run in runs if run.experiment_family == "H3-EXPLANATIONS"),
                    key=lambda run: sha256_value({"seed": seed, "run_id": run.run_id}))
    mapping = []
    for index, run in enumerate(values, 1):
        digest = sha256_value({"blind_seed": seed, "run_id": run.run_id})[:16]
        mapping.append({"blind_id": f"BLIND-{index:03d}-{digest}",
                        "incident_id": run.incident_id,
                        "explanation_mode": run.explanation_mode,
                        "run_id": run.run_id})
    public = [{"blind_id": item["blind_id"]} for item in mapping]
    return {"schema_version": "h3-blind-map-v3", "seed": seed,
            "private_map": mapping, "blind_package_index": public,
            "private_map_sha256": sha256_value(mapping)}


def audit_h3_same_rca(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Prove that the three blinded modes consume one immutable RCA/evidence base."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in results:
        if item.get("identity", {}).get("experiment_family") == "H3-EXPLANATIONS":
            groups.setdefault(item.get("input", {}).get("incident_id"), []).append(item)
    issues = []
    for incident, items in groups.items():
        modes = {item.get("configuration", {}).get("explanation_mode") for item in items}
        provenance = [item.get("provenance", {}) for item in items]
        checks = {
            "rca_result_hash": {item.get("rca_result_hash") for item in provenance},
            "root_cause_ranking_hash": {item.get("root_cause_ranking_hash") for item in provenance},
            "reliability_decision_hash": {item.get("reliability_decision_hash") for item in provenance},
            "evidence_base_hash": {item.get("evidence_base_hash") for item in provenance},
        }
        if modes != set(H3_MODES) or any(len(values) != 1 or None in values
                                        for values in checks.values()):
            issues.append(incident)
    return {"schema_version": "h3-same-rca-binding-audit-v3",
            "status": "PASS" if groups and not issues else "FAIL",
            "incidents": len(groups), "expected_modes": list(H3_MODES),
            "invalid_incidents": sorted(issues)}


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    paths = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{prefix}.{key}"
            if str(key).lower() in FORBIDDEN_SCIENTIFIC_INPUT_KEYS:
                paths.append(child)
            paths.extend(_forbidden_paths(item, child))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            paths.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return paths


@dataclass(frozen=True)
class ScientificInput:
    evidence: Mapping[str, Any]
    capabilities: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        violations = _forbidden_paths({"evidence": self.evidence,
                                       "capabilities": self.capabilities,
                                       "provenance": self.provenance})
        if violations:
            raise ValueError(f"Ground-truth leakage no ScientificInput: {violations}")


@dataclass(frozen=True)
class ScientificEvaluation:
    ground_truth_service: str | None
    predicted_service: str | None
    ranked_services: tuple[str, ...]
    top1_correct: bool
    reciprocal_rank: float

    def __post_init__(self) -> None:
        if not isinstance(self.top1_correct, bool):
            raise TypeError("top1_correct deve ser bool.")
        if not 0.0 <= self.reciprocal_rank <= 1.0:
            raise ValueError("reciprocal_rank deve estar em [0,1].")


class ResultState(str, Enum):
    SUCCESS = "SUCCESS"
    SKIP_NOT_APPLICABLE = "SKIP_NOT_APPLICABLE"
    FAILED_INPUT = "FAILED_INPUT"
    FAILED_EXECUTION = "FAILED_EXECUTION"
    FAILED_VALIDATION = "FAILED_VALIDATION"


def reusable_result(path: Path, *, run_id: str, configuration_hash: str,
                    dataset_hash: str, model_hash: str, artifact_sha256: str) -> bool:
    if not path.is_file() or file_sha256(path) != artifact_sha256:
        return False
    try:
        payload = _read_json(path)
    except ValueError:
        return False
    return (payload.get("execution_status") == ResultState.SUCCESS.value
            and payload.get("run_id") == run_id
            and payload.get("configuration_hash") == configuration_hash
            and payload.get("dataset_hash") == dataset_hash
            and payload.get("method_hash") == model_hash)


def normalize_result_state(orchestrator_state: str) -> ResultState:
    if orchestrator_state == "SUCCESS":
        return ResultState.SUCCESS
    if orchestrator_state.startswith("SKIPPED_"):
        return ResultState.SKIP_NOT_APPLICABLE
    if orchestrator_state in {"FAILED_INPUT_VALIDATION", "FAILED_FEATURE_EXTRACTION"}:
        return ResultState.FAILED_INPUT
    if orchestrator_state in {"FAILED_STATISTICS_PRECONDITION"}:
        return ResultState.FAILED_VALIDATION
    return ResultState.FAILED_EXECUTION


def analysis_ready_result(value: Mapping[str, Any], run: ExperimentRunV3 | None = None) -> dict[str, Any]:
    identity, source = value["identity"], value["input"]
    config, status = value["configuration"], value["status"]
    evaluation, result = value.get("evaluation", {}), value.get("result", {})
    provenance = value.get("provenance", {})
    ranking = result.get("predicted_ranking", ())
    ranked_services = [item.get("service_name") or item.get("service") for item in ranking]
    predicted = ranked_services[0] if ranked_services else evaluation.get("predicted_service")
    ground_truth = evaluation.get("ground_truth_service")
    if ground_truth is None:
        services = evaluation.get("ground_truth_services", ())
        ground_truth = services[0] if len(services) == 1 else None
    top1 = evaluation.get("top1_correct")
    if top1 is None and ground_truth is not None and predicted is not None:
        top1 = predicted == ground_truth
    return {
        "schema_version": "analysis-ready-rca-result-v3",
        "campaign_id": identity.get("campaign_id"), "hypothesis": identity.get("experiment_family"),
        "run_id": identity.get("run_id"), "dataset_id": source.get("dataset"),
        "incident_id": source.get("incident_id"), "source_system": source.get("system"),
        "protocol": identity.get("protocol"), "holdout": identity.get("held_out"),
        "method": config.get("method"),
        "method_version": run.model_version if run else None,
        "method_hash": (provenance.get("method_implementation_hash")
                        or provenance.get("trained_model_artifact_hash")),
        "model_specification_hash": (run.model_hash if run else
                                     provenance.get("model_specification_hash")),
        "trained_model_artifact_hash": provenance.get("trained_model_artifact_hash"),
        "method_implementation_hash": provenance.get("method_implementation_hash"),
        "training_dataset_sha256": provenance.get("training_dataset_sha256"),
        "training_split_hash": provenance.get("training_split_hash"),
        "validation_split_hash": provenance.get("validation_split_hash"),
        "test_split_hash": provenance.get("test_split_hash"),
        "feature_schema_sha256": provenance.get("feature_schema_sha256"),
        "seed": source.get("seed"),
        "ground_truth_service": ground_truth, "predicted_service": predicted,
        "ranked_services": ranked_services, "top1_correct": top1,
        "reciprocal_rank": evaluation.get("reciprocal_rank"),
        "candidate_count": len(ranked_services),
        "execution_status": normalize_result_state(status["state"]).value,
        "runtime_seconds": status.get("elapsed_seconds"),
        "feature_view": run.feature_view if run else None,
        "feature_view_hash": source.get("feature_view_hash"),
        "configuration_hash": run.configuration_hash if run else None,
        "provenance_hash": sha256_value(provenance),
        "raw_run_artifact_hash": sha256_value(value),
    }


def write_final_result_indexes(store: Any, results: Iterable[Mapping[str, Any]],
                               runs: Mapping[str, ExperimentRunV3]) -> None:
    values = tuple(results)
    rows = [analysis_ready_result(value, runs.get(value["identity"]["run_id"])) for value in values]
    store.write_json("analysis-ready-results.json", {
        "schema_version": "analysis-ready-result-index-v3", "results": rows,
    })
    failures = []
    for value in values:
        state = normalize_result_state(value["status"]["state"])
        if state is ResultState.SUCCESS:
            continue
        run_id = value["identity"]["run_id"]
        run = runs[run_id]
        failures.append({
            "run_id": run_id, "timestamp": value["status"].get("finished_at"),
            "state": state.value, "stage": value["status"].get("execution_stage"),
            "error_type": value["status"].get("exception_type") or state.value,
            "error_message": str(value["status"].get("reason") or "")[:1000],
            "retryable": state is ResultState.FAILED_EXECUTION,
            "dataset": run.source_dataset, "incident": run.incident_id,
            "method": run.method, "view": run.feature_view,
            "mode": run.explanation_mode, "configuration_hash": run.configuration_hash,
        })
    content = "".join(canonical_json(row) + "\n" for row in failures)
    path = store.path / "failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def promote_trained_artifacts(repository_root: Path, results: Iterable[Mapping[str, Any]],
                              runs: Mapping[str, ExperimentRunV3], *,
                              scientific_tranche: Mapping[str, Any] | None = None,
                              destination: str | Path | None = None) -> dict[str, Any]:
    """Publish a checksum-bound fold index only after a scientific H1/H2 run."""
    rows = []
    learned = {"logistic-regression", "random-forest", "xgboost-rca-v3", "mlp-light-v3"}
    for value in results:
        if value.get("status", {}).get("state") != "SUCCESS":
            continue
        run = runs[value["identity"]["run_id"]]
        if run.method not in learned:
            continue
        provenance = value.get("provenance", {})
        artifact_hash, artifact_dir = (provenance.get("trained_model_artifact_hash"),
                                       provenance.get("serialized_artifact_path"))
        if not isinstance(artifact_hash, str) or len(artifact_hash) != 64 or not artifact_dir:
            raise RuntimeError("TRAINED_ARTIFACT_PROVENANCE_INCOMPLETE")
        artifact_path = Path(artifact_dir).resolve()
        try:
            relative = str(artifact_path.relative_to(repository_root.resolve()))
        except ValueError as exc:
            raise RuntimeError("TRAINED_ARTIFACT_OUTSIDE_REPOSITORY") from exc
        rows.append({
            "source_dataset": run.source_dataset, "source_system": run.source_system,
            "feature_view": run.feature_view, "protocol": run.protocol,
            "held_out": run.held_out, "seed": run.seed, "method": run.method,
            "method_version": provenance.get("method_version"),
            "model_specification_hash": run.model_hash,
            "training_split_hash": provenance.get("training_split_hash"),
            "validation_split_hash": provenance.get("validation_split_hash"),
            "test_split_hash": provenance.get("test_split_hash"),
            "hyperparameters": provenance.get("hyperparameters"),
            "artifact_dir": relative,
            "serialized_artifact_sha256": artifact_hash,
            "feature_schema_sha256": provenance.get("feature_schema_sha256"),
            "training_dataset_sha256": provenance.get("training_dataset_sha256"),
        })
    unique = {}
    for row in rows:
        key = tuple(row[name] for name in (
            "source_dataset", "source_system", "feature_view", "protocol", "held_out",
            "seed", "method"))
        if key in unique and unique[key]["serialized_artifact_sha256"] != row[
                "serialized_artifact_sha256"]:
            raise RuntimeError("TRAINED_ARTIFACT_FOLD_COLLISION")
        unique[key] = row
    payload = {"schema_version": "trained-model-artifact-index-v3",
               "artifacts": [unique[key] for key in sorted(unique, key=repr)]}
    if scientific_tranche is not None:
        payload.update({
            "scientific_tranche_id": scientific_tranche["tranche_id"],
            "scientific_tranche_sha256": scientific_tranche["selection_sha256"],
            "parent_split_sha256": scientific_tranche["parent_split_key"],
            "parent_base_key": scientific_tranche["parent_base_key"],
        })
    if destination is not None:
        target = Path(destination)
        if not target.is_absolute():
            target = repository_root / target
    elif scientific_tranche is not None:
        target = (repository_root / "artifacts/final-experiments-v3"
                  / f"trained-artifacts-{scientific_tranche['tranche_id']}" / "INDEX.json")
    else:
        target = repository_root / "artifacts/final-experiments-v3/trained-artifacts/INDEX.json"
    _atomic_json(target, payload)
    return {"artifacts": len(unique), "path": str(target)}


def prepare_selective_resume(store: Any, runs: Iterable[ExperimentRunV3]) -> dict[str, Any]:
    """Keep only byte-valid SUCCESS runs; quarantine mismatches for targeted reexecution."""
    planned = {run.run_id: run for run in runs}
    expected_datasets = {}
    configuration_path = store.path / "configuration.json"
    if configuration_path.is_file():
        try:
            configuration = _read_json(configuration_path)
            relative = configuration.get("criteria", {}).get("scientific_benchmark_catalog")
            if relative:
                catalog_path = Path(relative)
                if not catalog_path.is_absolute():
                    # Campaign artifacts are nested; the repository path is stored
                    # in the immutable environment manifest.
                    repository = Path(_read_json(store.path / "environment.json").get(
                        "repository_root", Path.cwd()))
                    catalog_path = repository / catalog_path
                if catalog_path.is_file():
                    catalog = _read_json(catalog_path)
                    for item in catalog.get("datasets", ()):
                        key = tuple(item.get(name) for name in (
                            "source_dataset", "source_system", "feature_view", "protocol",
                            "held_out", "seed"))
                        expected_datasets[key] = item.get("benchmark_dataset_sha256")
        except (ValueError, KeyError, TypeError):
            expected_datasets = {}
    index_path = store.runs_path / "index.json"
    if not index_path.exists():
        return {"reused": 0, "reexecute": len(planned), "quarantined": 0}
    try:
        index = _read_json(index_path)
    except ValueError:
        index = {}
    valid, invalid = {}, []
    for run_id, expected_hash in index.items():
        path = store.runs_path / f"{run_id}.json"
        run = planned.get(run_id)
        okay = path.is_file() and file_sha256(path) == expected_hash and run is not None
        payload = None
        if okay:
            try:
                payload = _read_json(path)
            except ValueError:
                okay = False
        if okay:
            provenance = payload.get("provenance", {})
            dataset_key = (run.source_dataset, run.source_system, run.feature_view,
                           run.protocol, run.held_out, run.seed)
            expected_dataset = expected_datasets.get(dataset_key)
            trained = provenance.get("trained_model_artifact_hash")
            implementation = provenance.get("method_implementation_hash")
            strict_v3 = run.model_version == "rca-model-benchmark-v3-spec"
            okay = (payload.get("status", {}).get("state") == "SUCCESS"
                    and payload.get("identity", {}).get("run_id") == run_id
                    and (not strict_v3 or store.path.name == run.configuration_hash)
                    and payload.get("input", {}).get("source_manifest_hash") == run.source_manifest_hash
                    and payload.get("input", {}).get("model_hash") == run.model_hash
                    and (not strict_v3 or provenance.get("model_specification_hash") == run.model_hash)
                    and (not strict_v3 or expected_dataset is None
                         or provenance.get("training_dataset_sha256") == expected_dataset)
                    and (not strict_v3 or (run.method in {"temporal-baseline", "max-zscore", "first-anomalous"}
                          and trained is None and isinstance(implementation, str)
                          and len(implementation) == 64)
                         or (run.method not in {"temporal-baseline", "max-zscore", "first-anomalous"}
                             and isinstance(trained, str) and len(trained) == 64)))
        if okay:
            valid[run_id] = expected_hash
        else:
            invalid.append(path)
    indexed_paths = {store.runs_path / f"{run_id}.json" for run_id in index}
    invalid.extend(path for path in store.runs_path.glob("*.json")
                   if path.name != "index.json" and path not in indexed_paths)
    invalid = list(dict.fromkeys(invalid))
    if invalid or set(index) - set(planned):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantine = store.path / "recovery" / stamp
        quarantine.mkdir(parents=True, exist_ok=True)
        for path in invalid:
            if path.exists():
                os.replace(path, quarantine / path.name)
        checksum = store.path / "SHA256SUMS"
        if checksum.exists():
            os.replace(checksum, quarantine / "pre-recovery-SHA256SUMS")
        store.write_json("runs/index.json", valid)
    return {"reused": len(valid), "reexecute": len(planned) - len(valid),
            "quarantined": len(invalid)}


@dataclass
class FailureLedger:
    path: Path

    def append(self, *, run: ExperimentRunV3, state: ResultState, stage: str,
               error_type: str, error_message: str, retryable: bool) -> None:
        if state not in {ResultState.SKIP_NOT_APPLICABLE, ResultState.FAILED_INPUT,
                         ResultState.FAILED_EXECUTION, ResultState.FAILED_VALIDATION}:
            raise ValueError("Failure ledger aceita somente skip/failure.")
        safe_message = str(error_message).replace("\n", " ")[:1000]
        record = {
            "run_id": run.run_id, "timestamp": _utc_now(), "state": state.value,
            "stage": stage, "error_type": error_type, "error_message": safe_message,
            "retryable": bool(retryable), "dataset": run.source_dataset,
            "incident": run.incident_id, "method": run.method,
            "view": run.feature_view, "mode": run.explanation_mode,
            "configuration_hash": run.configuration_hash,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(record) + "\n")


def git_worktree_status(repository_root: Path) -> dict[str, Any]:
    commit = subprocess.run(("git", "rev-parse", "HEAD"), cwd=repository_root,
                            check=True, capture_output=True, text=True).stdout.strip()
    porcelain = subprocess.run(("git", "status", "--porcelain"), cwd=repository_root,
                               check=True, capture_output=True, text=True).stdout
    return {"git_commit": commit, "dirty": bool(porcelain.strip()),
            "dirty_entries": len(porcelain.splitlines())}


def require_clean_final(repository_root: Path, *, mode: str,
                        allow_dirty_development: bool = False) -> dict[str, Any]:
    if mode not in {"development", "final"}:
        raise ValueError("mode deve ser development ou final.")
    status = git_worktree_status(repository_root)
    if mode == "final" and allow_dirty_development:
        raise ValueError("Flag de dirty development é proibida em mode=final.")
    if mode == "final" and status["dirty"]:
        raise RuntimeError("GIT_WORKTREE_CLEAN_REQUIRED")
    if mode == "development" and status["dirty"] and not allow_dirty_development:
        raise RuntimeError("DIRTY_WORKTREE_REQUIRES_EXPLICIT_DEVELOPMENT_FLAG")
    return {**status, "mode": mode, "valid": True}


def environment_report(repository_root: Path) -> dict[str, Any]:
    packages = {}
    for name in ("numpy", "scikit-learn", "xgboost", "joblib", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    git = git_worktree_status(repository_root)
    return {
        "schema_version": "final-campaign-environment-v3", "generated_at": _utc_now(),
        "repository_root": str(repository_root.resolve()),
        "git": git, "python": sys.version, "platform": platform.platform(),
        "os": os.name, "architecture": platform.machine(), "packages": packages,
        "contracts": {"wiring": WIRING_VERSION, "benchmark": BENCHMARK_VERSION,
                      "degradation": HARNESS_VERSION,
                      "orchestrator": "experimental-orchestrator-v3",
                      "feature_registry": FeatureRegistryV3().schema_version,
                      "feature_registry_sha256": FeatureRegistryV3().fingerprint,
                      "reliability_gate": "reliability-gate-v1"},
    }


def _write_sha256s(root: Path) -> None:
    files = sorted(path for path in root.rglob("*")
                   if path.is_file() and path.name != "SHA256SUMS")
    content = "\n".join(f"{file_sha256(path)}  {path.relative_to(root)}" for path in files) + "\n"
    target = root / "SHA256SUMS"
    target.write_text(content, encoding="utf-8")


def dry_run(registry: FrozenDatasetRegistry, config_paths: Iterable[Path], output: Path,
            *, verify_all_hashes: bool = True, progress: bool = False) -> dict[str, Any]:
    before = registry.immutability_snapshot()
    capabilities = registry.capabilities()
    plans, pairing_parts, h3_runs, prerequisites = {}, {}, (), []
    for path in config_paths:
        payload = load_campaign_config(path)
        family = payload["family"]
        criteria = payload.get("criteria", {})
        if family in {"H1-A", "H1-B", "H1-C", "H2"}:
            relative = criteria.get("scientific_benchmark_catalog")
            target = (registry.repository_root / relative).resolve() if relative else None
            available, reason = False, None
            if target and target.is_file():
                try:
                    from .preprocessing import validate_catalog
                    validate_catalog(target, repository_root=registry.repository_root,
                                     expected_model_specification_hash=payload["model_hash"])
                    available = True
                except ValueError as exc:
                    reason = str(exc)
            prerequisites.append({
                "family": family, "requirement": "scientific_benchmark_catalog",
                "configured": bool(relative), "available": available,
                "state": "READY_NOW" if available else "NOT_READY",
                "reason": reason, "path": str(target) if target else None,
                "required_for": "final-run",
            })
        elif family == "H3":
            result_path = criteria.get("scientific_rca_results")
            target = (registry.repository_root / result_path).resolve() if result_path else None
            available = bool(target and target.is_file())
            prerequisites.append({
                "family": family, "requirement": "scientific_rca_results",
                "configured": bool(result_path), "available": available,
                "state": "READY_NOW" if available else "READY_AFTER_H1_H2",
                "reason": None if available else "H3_SCIENTIFIC_RCA_RESULTS_NOT_AVAILABLE",
                "path": str(target) if target else None, "required_for": "sequential-final-run",
            })
        elif family == "H4":
            target = registry.repository_root / "artifacts/final-experiments-v3/trained-artifacts/INDEX.json"
            available = target.is_file()
            prerequisites.append({
                "family": family, "requirement": "scientific_trained_artifact_index",
                "configured": True, "available": available,
                "state": "READY_NOW" if available else "READY_AFTER_H1_H2",
                "reason": None if available else "H4_SCIENTIFIC_MODELS_NOT_AVAILABLE",
                "path": str(target), "required_for": "sequential-final-run",
            })
        _, summary, pairing_detail, family_h3_runs = compact_plan(payload, capabilities)
        if progress:
            print(canonical_json({"stage": "pairing", "family": family,
                                  "planned": summary["planned_runs"],
                                  "applicable": summary["applicable_runs"]}), flush=True)
        plans[family] = summary
        pairing_parts[family] = pairing_detail
        if not pairing_detail["valid"]:
            raise RuntimeError(f"PAIRING_PREFLIGHT_FAIL:{family}")
        if family == "H3":
            h3_runs = family_h3_runs
        _PROTOCOL_ASSIGNMENT_CACHE.clear()
        gc.collect()
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass
    preflight = registry.preflight(verify_all_hashes=verify_all_hashes)
    if not preflight["valid"]:
        raise RuntimeError("DATASET_PREFLIGHT_FAIL")
    matrix = build_capability_matrix(capabilities)
    pairing = {
        "schema_version": "pairing-audit-v3", "valid": True,
        "statistical_plan": {"top1": "paired-mcnemar",
                             "mrr": "paired-bootstrap-95pct",
                             "multiple_comparisons": "holm",
                             "macro_f1": "descriptive"},
        "campaigns": pairing_parts, "issues": [],
    }
    if not pairing["valid"]:
        raise RuntimeError(f"PAIRING_PREFLIGHT_FAIL:{pairing['issues']}")
    after = registry.immutability_snapshot()
    if before["snapshot_sha256"] != after["snapshot_sha256"]:
        raise RuntimeError("FROZEN_DATASET_IMMUTABILITY_FAIL")
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "dataset-registry-snapshot.json": registry.snapshot(),
        "dataset-preflight.json": preflight,
        "capability-matrix.json": matrix,
        "pairing-preflight.json": pairing,
        "environment.json": environment_report(registry.repository_root),
        "frozen-dataset-immutability-before.json": before,
        "frozen-dataset-immutability-after.json": after,
        "launch-prerequisites.json": {
            "schema_version": "final-launch-prerequisites-v3",
            "ready": all(item["state"] != "NOT_READY" for item in prerequisites),
            "ready_now": all(item["state"] == "READY_NOW" for item in prerequisites),
            "requirements": prerequisites,
        },
    }
    for family, plan in plans.items():
        artifacts[f"{family.lower().replace('-', '')}-plan.json"] = plan
    if h3_runs:
        blind = deterministic_blind_map(h3_runs, seed=min(run.seed for run in h3_runs))
        artifacts["h3-sample-manifest.json"] = {
            "schema_version": "h3-sample-manifest-v3", "sample_size": 40,
            "incident_ids": sorted({run.incident_id for run in h3_runs}),
            "selection": "deterministic-strata-hash-v1",
            "selection_sha256": sha256_value(sorted({run.incident_id for run in h3_runs})),
        }
        artifacts["h3-blind-map-private.json"] = blind
    for name, value in artifacts.items():
        _atomic_json(output / name, value)
    _write_sha256s(output)
    return {"valid": True, "preflight": preflight, "plans": plans,
            "pairing": pairing, "immutability": "PASS", "output": str(output)}


def assert_h4_no_retraining(runs: Iterable[ExperimentRunV3]) -> bool:
    groups: dict[tuple[Any, ...], set[str]] = {}
    for run in runs:
        if run.experiment_family != "H4-DEGRADATION":
            continue
        key = (run.source_dataset, run.source_system, run.incident_id, run.protocol,
               run.held_out, run.degradation_operator, run.seed, run.repetition)
        groups.setdefault(key, set()).add(run.model_hash)
    return bool(groups) and all(len(values) == 1 for values in groups.values())


def v2_to_v3_import_count(repository_root: Path) -> int:
    targets = [repository_root / relative for relative in V2_FREEZE_HASHES
               if relative.endswith(".py")]
    return sum("multimodal_v1" in path.read_text(encoding="utf-8") for path in targets)
