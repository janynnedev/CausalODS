from __future__ import annotations

import argparse
from dataclasses import replace
import importlib
import json
import re
from pathlib import Path

from experiments.multimodal_v3.core import (
    CampaignStoreV3, ExperimentOrchestratorV3, ExperimentPlannerV3,
    build_campaign_manifest, canonical_json, sha256_value,
)

from .core import (
    FrozenDatasetRegistry, assert_h4_no_retraining, audit_h3_same_rca, build_plan,
    compact_plan, deterministic_blind_map, dry_run,
    environment_report, load_campaign_config, plan_summary, prepare_selective_resume,
    promote_trained_artifacts, require_clean_final, write_final_result_indexes,
)
from .preprocessing import (
    FrozenEvidenceLoader, _incident_worker_payload, _read_json, _registry_from_snapshot,
    build_preprocessing_keys, materialize,
)
from .scientific_tranche import (
    load_manifest as load_scientific_tranche, selection_hash, validate_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _FilteredPlanner:
    def __init__(self, config, runs):
        self.config, self._runs = config, tuple(runs)

    def plan(self):
        return self._runs

    def summary(self, runs):
        return ExperimentPlannerV3.summary(runs)


def bind_runs_to_scientific_tranche(runs, tranche, *, catalog_entries=None):
    """Return immutable test-only runs bound to the tranche split contract."""
    splits = {name: tuple(tranche[f"{name}_incident_ids"])
              for name in ("train", "validation", "test")}
    test_ids = set(splits["test"])
    expected_protocol = tranche["protocol"]
    expected_seed = tranche["seed"]

    def incident_source_system(incident_id):
        match = re.fullmatch(r"rcaeval:re[123](ob|ss|tt)_.+", incident_id)
        return match.group(1).upper() if match else None

    compatible = tuple(run for run in runs if (
        run.protocol == expected_protocol
        and run.seed == expected_seed
        and run.held_out is None
        and run.incident_id in test_ids
        and run.source_dataset == run.incident_id.split(":", 1)[0]
        and run.source_system == incident_source_system(run.incident_id)
    ))
    if catalog_entries is not None:
        catalog_keys = {(item["protocol"], item["seed"], item.get("held_out"),
                         item["feature_view"], item["source_dataset"],
                         item["source_system"]) for item in catalog_entries}
        compatible = tuple(run for run in compatible if (
            run.protocol, run.seed, run.held_out, run.feature_view,
            run.source_dataset, run.source_system) in catalog_keys)
    if not compatible:
        raise ValueError("SCIENTIFIC_TRANCHE_NO_COMPATIBLE_PARENT_RUNS")
    bound = tuple(replace(
        run, train_incident_ids=splits["train"],
        validation_incident_ids=splits["validation"],
        test_incident_ids=splits["test"], run_id="",
    ) for run in compatible)
    if any(run.protocol != expected_protocol or run.seed != expected_seed
           or run.held_out is not None or run.incident_id not in test_ids
           or run.train_incident_ids != splits["train"]
           or run.validation_incident_ids != splits["validation"]
           or run.test_incident_ids != splits["test"] for run in bound):
        raise ValueError("SCIENTIFIC_TRANCHE_BOUND_RUN_CONTRACT_FAIL")
    return bound


def validate_scientific_tranche_parent_binding(*, tranche, parent_run, registry):
    """Recompute both parent keys from frozen registry/manifests and planner split."""
    parent_splits = {"train": parent_run.train_incident_ids,
                     "validation": parent_run.validation_incident_ids,
                     "test": parent_run.test_incident_ids}
    sources = FrozenEvidenceLoader(registry, rcaeval_cache=None).sources
    # Registry v3 hashes include absolute frozen paths.  Recover the exact
    # original identity boundary from the authoritative parent finalizer, but
    # only after independently matching every path-independent scientific
    # registry field and the parent training membership.
    finalizer = (registry.repository_root / "artifacts/final-experiments-v3"
                 / "scientific-inputs/work" / tranche["parent_base_key"]
                 / "baseline/snapshot.finalizer.json")
    frozen = _read_json(finalizer)
    frozen_registry = frozen.get("registry_snapshot", {})

    def scientific_registry_identity(snapshot):
        # dataset_fingerprint is itself derived from the absolute root, so the
        # checksum identity and frozen counts are the relocation-stable facts.
        fields = ("dataset_id", "kind", "checksum_manifest_sha256", "expected_incidents",
                  "expected_usable", "frozen")
        return sorted(({name: item.get(name) for name in fields}
                       for item in snapshot.get("datasets", ())),
                      key=lambda item: item["dataset_id"])

    if scientific_registry_identity(frozen_registry) != scientific_registry_identity(
            registry.snapshot()):
        raise ValueError("SCIENTIFIC_TRANCHE_PARENT_REGISTRY_IDENTITY_MISMATCH")
    if frozen.get("base_key") != tranche["parent_base_key"] \
            or frozen.get("split_key") != tranche["parent_split_key"] \
            or tuple(sorted(frozen.get("train_incident_ids", ()))) != tuple(sorted(
                parent_splits["train"])):
        raise ValueError("SCIENTIFIC_TRANCHE_PARENT_FINALIZER_BINDING_MISMATCH")
    computed_split, computed_base, _ = build_preprocessing_keys(
        registry=registry, sources=sources, splits=parent_splits,
        protocol=parent_run.protocol, held_out=parent_run.held_out, seed=parent_run.seed,
        registry_sha256=frozen_registry["registry_sha256"])
    return validate_manifest(
        tranche, parent_splits=parent_splits, protocol=parent_run.protocol,
        seed=parent_run.seed, parent_base_key=computed_base,
        parent_split_key=computed_split,
        expected_feature_registry_sha256=__import__(
            "experiments.multimodal_v3.core", fromlist=["FeatureRegistryV3"]
        ).FeatureRegistryV3().fingerprint,
        expected_preprocessing_version="scientific-input-preprocessing-v3",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CausalODS final campaign wiring v3")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--registry", type=Path, required=True)
    preflight.add_argument("--config-dir", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    preprocess = commands.add_parser("preprocess")
    preprocess.add_argument("--registry", type=Path, required=True)
    preprocess.add_argument("--config-dir", type=Path, required=True)
    preprocess.add_argument("--output", type=Path, required=True)
    preprocess.add_argument("--model-specification", type=Path, required=True)
    preprocess.add_argument("--rcaeval-cache", type=Path)
    preprocess.add_argument("--resume", action="store_true")
    preprocess.add_argument("--max-new-artifacts", type=int)
    preprocess.add_argument("--scientific-tranche", type=Path)
    incident = commands.add_parser("preprocess-incident")
    incident.add_argument("--registry", type=Path, required=True)
    incident.add_argument("--config-dir", type=Path, required=True)
    incident.add_argument("--model-specification", type=Path, required=True)
    incident.add_argument("--rcaeval-cache", type=Path)
    incident.add_argument("--incident-id", required=True)
    incident.add_argument("--output", type=Path, required=True)
    incident.add_argument("--protocol", required=True)
    incident.add_argument("--seed", type=int, required=True)
    incident.add_argument("--held-out")
    incident.add_argument("--resume", action="store_true")
    prepare = commands.add_parser("prepare-baseline")
    prepare.add_argument("--registry", type=Path, required=True)
    prepare.add_argument("--config-dir", type=Path, required=True)
    prepare.add_argument("--model-specification", type=Path, required=True)
    prepare.add_argument("--rcaeval-cache", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--protocol", required=True)
    prepare.add_argument("--seed", type=int, required=True)
    prepare.add_argument("--incident-id", required=True)
    prepare.add_argument("--held-out")
    prepare.add_argument("--resume", action="store_true")
    resolve = commands.add_parser("resolve-preprocessing-binding")
    resolve.add_argument("--registry", type=Path, required=True)
    resolve.add_argument("--config-dir", type=Path, required=True)
    resolve.add_argument("--model-specification", type=Path, required=True)
    resolve.add_argument("--rcaeval-cache", type=Path)
    resolve.add_argument("--incident-id", required=True)
    resolve.add_argument("--protocol", required=True)
    resolve.add_argument("--seed", type=int, required=True)
    resolve.add_argument("--held-out")
    runtime = commands.add_parser("validate-rcaeval-runtime")
    runtime.add_argument("--registry", type=Path, required=True)
    runtime.add_argument("--rcaeval-cache", type=Path, required=True)
    runtime.add_argument("--incident-id", required=True)
    heavy = commands.add_parser("heavy-resource-gate")
    heavy.add_argument("--registry", type=Path, required=True)
    heavy.add_argument("--config-dir", type=Path, required=True)
    heavy.add_argument("--model-specification", type=Path, required=True)
    heavy.add_argument("--rcaeval-cache", type=Path, required=True)
    heavy.add_argument("--incident-id", required=True)
    heavy.add_argument("--reference-baseline", type=Path, required=True)
    heavy.add_argument("--output", type=Path, required=True)
    heavy.add_argument("--resume", action="store_true")
    worker = commands.add_parser("incident-worker")
    worker.add_argument("--spec", type=Path, required=True)
    baseline_worker = commands.add_parser("baseline-contribution-worker")
    baseline_worker.add_argument("--spec", type=Path, required=True)
    baseline_finalizer = commands.add_parser("baseline-finalizer")
    baseline_finalizer.add_argument("--spec", type=Path, required=True)
    profile = commands.add_parser("profile-preprocessing")
    profile.add_argument("--registry", type=Path, required=True)
    profile.add_argument("--rcaeval-cache", type=Path, required=True)
    profile.add_argument("--incident", required=True)
    profile.add_argument("--output", type=Path)
    diagnostic = commands.add_parser("diagnose-cache")
    diagnostic.add_argument("--registry", type=Path, required=True)
    diagnostic.add_argument("--rcaeval-cache", type=Path, required=True)
    diagnostic.add_argument("--incident", required=True)
    diagnostic.add_argument("--output", type=Path)
    audit = commands.add_parser("audit-preprocess-resume")
    audit.add_argument("--output", type=Path, required=True)
    for command in ("dry-run", "run", "resume"):
        item = commands.add_parser(command)
        item.add_argument("--config", type=Path, required=True)
        item.add_argument("--registry", type=Path, required=True)
        item.add_argument("--output", type=Path,
                          default=Path("artifacts/final-experiments-v3"))
        item.add_argument("--mode", choices=("development", "final"),
                          default="development" if command == "dry-run" else "final")
        item.add_argument("--allow-dirty-development", action="store_true")
        item.add_argument("--executor-factory",
                          default="experiments.causalods_campaign_bindings:build_executors")
        item.add_argument("--scientific-tranche", type=Path)
    return parser


def _configs(directory: Path) -> tuple[Path, ...]:
    # H4 has the largest transient pairing state; evaluating it first keeps the
    # combined preflight below the coordinator memory ceiling.
    expected = tuple(directory / f"{name}.json" for name in ("h4", "h3", "h2", "h1c", "h1b", "h1a"))
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise ValueError(f"Configs finais ausentes: {missing}")
    return expected


def _factory(reference: str):
    if ":" not in reference:
        raise ValueError("--executor-factory deve usar module:function.")
    module, name = reference.split(":", 1)
    return getattr(importlib.import_module(module), name)


def _write_pre_execution(store: CampaignStoreV3, payload, registry, config, runs,
                         evaluation_context) -> None:
    store.write_json("configuration.json", payload, immutable=True)
    store.write_json("dataset-registry-snapshot.json", registry.snapshot(), immutable=True)
    store.write_json("environment.json", environment_report(REPOSITORY_ROOT), immutable=True)
    store.write_json("execution-plan.json", {
        "schema_version": "execution-plan-v3", "summary": plan_summary(payload["family"], runs),
        "runs": [run.to_dict() for run in runs],
    }, immutable=True)
    store.write_json("scientific-evaluation-context.json", {
        "schema_version": "scientific-evaluation-context-v3",
        "access_policy": "evaluation-only-after-inference",
        "incidents": evaluation_context,
    }, immutable=True)
    planner = ExperimentPlannerV3(config)
    store.write_json("split-manifest.json", {
        "schema_version": "split-manifest-v3",
        "protocol_manifests": list(planner.protocol_manifests()),
    }, immutable=True)
    store.write_json("model-manifest.json", {
        "model_version": config.model_version, "model_hash": config.model_hash,
        "training_protocol": list(config.protocols), "seed": list(config.seeds),
        "tuning_budget": config.tuning_budget,
        "deterministic_level": "seeded-library-best-effort",
    }, immutable=True)
    if payload["family"] == "H3":
        store.write_json("prompt-manifest.json", {
            "modes": list(config.explanation_modes), "temperature": config.criteria.get("llm_temperature", 0.0),
            "model": config.criteria.get("llm_model"), "template": "prompt-builder-v1",
            "live_binding_required": True,
        }, immutable=True)
    if payload["family"] == "H4":
        store.write_json("degradation-manifest.json", {
            "operators": list(config.degradation_operators), "severities": list(config.severities),
            "model_hash": config.model_hash, "model_retrained": False,
            "h4_no_retraining": assert_h4_no_retraining(runs),
        }, immutable=True)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-rcaeval-runtime":
        from .preprocessing import validate_rcaeval_runtime
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        try:
            result = validate_rcaeval_runtime(
                registry=registry, rcaeval_cache=args.rcaeval_cache,
                incident_id=args.incident_id,
            )
        except Exception as exc:
            result = {"status": "FAIL", "incident_id": args.incident_id,
                      "reason": type(exc).__name__, "error": str(exc)}
        print(canonical_json(result))
        return 0 if result.get("status") == "PASS" else 1
    if args.command == "heavy-resource-gate":
        from .preprocessing import run_heavy_resource_gate
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        result = run_heavy_resource_gate(
            registry=registry, config_dir=args.config_dir,
            model_specification=args.model_specification,
            rcaeval_cache=args.rcaeval_cache, incident_id=args.incident_id,
            reference_baseline=args.reference_baseline,
            output=args.output, resume=args.resume)
        print(canonical_json(result))
        return 0 if result.get("status") == "PASS" else 1
    if args.command == "incident-worker":
        spec = _read_json(args.spec)
        registry_snapshot = spec["registry_snapshot"]
        worker_args = {name: spec[name] for name in (
            "rcaeval_cache", "incident_id", "split", "base_key", "split_key",
            "baseline_snapshot_path", "baseline_sha256",
            "baseline_snapshot_bindings", "phase_diagnostic_path", "output")}
        del spec
        import gc
        gc.collect()
        registry = _registry_from_snapshot(registry_snapshot)
        del registry_snapshot
        gc.collect()
        source = __import__(
            "experiments.final_campaigns_v3.preprocessing",
            fromlist=["FrozenEvidenceLoader"],
        ).FrozenEvidenceLoader(registry, rcaeval_cache=None).sources[worker_args["incident_id"]]
        result = _incident_worker_payload(
            registry=registry, rcaeval_cache=worker_args["rcaeval_cache"],
            incident_id=worker_args["incident_id"], split=worker_args["split"],
            base_key=worker_args["base_key"], split_key=worker_args["split_key"],
            baseline_snapshot_path=worker_args["baseline_snapshot_path"],
            baseline_sha256=worker_args["baseline_sha256"],
            baseline_snapshot_bindings=worker_args["baseline_snapshot_bindings"],
            phase_diagnostic_path=worker_args["phase_diagnostic_path"],
            source=source, target=Path(worker_args["output"]),
        )
        print(canonical_json({
            "status": "COMPLETED", "incident_id": result["incident_id"],
            "worker": result["worker"],
        }))
        return 0
    if args.command == "baseline-contribution-worker":
        from .preprocessing import _baseline_contribution_worker_payload
        spec = _read_json(args.spec)
        registry = _registry_from_snapshot(spec["registry_snapshot"])
        result = _baseline_contribution_worker_payload(
            registry=registry, rcaeval_cache=spec.get("rcaeval_cache"),
            incident_id=spec["incident_id"], base_key=spec["base_key"],
            target=Path(spec["output"]),
        )
        print(canonical_json({"status": "COMPLETED", "incident_id": result["incident_id"]}))
        return 0
    if args.command == "baseline-finalizer":
        from .preprocessing import FrozenEvidenceLoader, _baseline_finalizer_payload
        spec = _read_json(args.spec)
        registry = _registry_from_snapshot(spec["registry_snapshot"])
        result = _baseline_finalizer_payload(
            contribution_root=Path(spec["contribution_root"]),
            sources=FrozenEvidenceLoader(registry, rcaeval_cache=None).sources,
            train_incident_ids=spec["train_incident_ids"], base_key=spec["base_key"],
            split_key=spec["split_key"],
            output=Path(spec["output"]),
        )
        print(canonical_json({"status": "COMPLETED", "baseline_sha256": result["baseline_sha256"]}))
        return 0
    if args.command == "audit-preprocess-resume":
        from .preprocessing import audit_existing_artifacts
        result = audit_existing_artifacts(args.output)
        print(canonical_json(result))
        return 0 if not result["invalid"] else 1
    if args.command in {"profile-preprocessing", "diagnose-cache"}:
        from .profiling import diagnose_remote_cache, print_report, profile_incident
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        if args.command == "profile-preprocessing":
            result = profile_incident(
                registry=registry, rcaeval_cache=args.rcaeval_cache,
                incident_id=args.incident, output=args.output,
            )
        else:
            result = diagnose_remote_cache(
                registry=registry, rcaeval_cache=args.rcaeval_cache,
                incident_id=args.incident, output=args.output,
            )
        print_report(result)
        return 0
    if args.command == "preprocess":
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        result = materialize(
            registry=registry, config_dir=args.config_dir, output=args.output,
            model_specification=args.model_specification,
            rcaeval_cache=args.rcaeval_cache, resume=args.resume,
            max_new_artifacts=args.max_new_artifacts,
            scientific_tranche=args.scientific_tranche,
        )
        print(canonical_json(result))
        return 0
    if args.command in {"preprocess-incident", "prepare-baseline", "resolve-preprocessing-binding"}:
        from .preprocessing import (
            _baseline_crash_safe, _checkpoint_name, _checkpoint_bindings,
            _failed_worker_state,
            _load_valid_baseline_snapshot, _load_valid_incident_checkpoint, _run_incident_worker,
            model_specification_hash, resolve_preprocessing_binding,
        )
        from .core import build_experiment_config
        from experiments.multimodal_v3.core import ExperimentPlannerV3, FeatureRegistryV3
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        model_specification_hash(args.model_specification)
        loader = __import__(
            "experiments.final_campaigns_v3.preprocessing",
            fromlist=["FrozenEvidenceLoader", "RCAFeatureExtractorV3"],
        )
        evidence_loader = loader.FrozenEvidenceLoader(registry, rcaeval_cache=args.rcaeval_cache)
        if args.incident_id not in evidence_loader.sources:
            raise ValueError(f"INCIDENT_NOT_FOUND:{args.incident_id}")
        config_payload = load_campaign_config(args.config_dir / "h1a.json")
        capabilities = tuple(item for item in registry.capabilities() if item.fault_type != "healthy")
        config = build_experiment_config(config_payload, capabilities)
        planner = ExperimentPlannerV3(config)
        binding = resolve_preprocessing_binding(
            registry=registry, experiment_planner=planner,
            sources=evidence_loader.sources, protocol=args.protocol,
            seed=args.seed, incident_id=args.incident_id, held_out=args.held_out,
        )
        assignment, splits = binding.assignment, binding.splits
        split_key, base_key = binding.split_key, binding.base_key
        incident_split, all_ids = binding.incident_split, binding.incident_ids
        if args.command == "resolve-preprocessing-binding":
            source = evidence_loader.sources[args.incident_id]
            print(canonical_json({"status": "RESOLVED", "incident_id": args.incident_id,
                "source_dataset": source.dataset_id, "source_system": source.system,
                "protocol": args.protocol, "seed": args.seed, "held_out": assignment.held_out,
                "base_key": base_key, "split_key": split_key,
                "train_count": len(splits["train"]), "validation_count": len(splits["validation"]),
                "test_count": len(splits["test"]), "total_incidents": len(all_ids),
                "incident_split": incident_split, "feature_registry_hash": FeatureRegistryV3().fingerprint,
                "preprocessing_version": "scientific-input-preprocessing-v3"}))
            return 0
        snapshot = args.output / "work" / base_key / "baseline" / "snapshot.json"
        if args.command == "prepare-baseline":
            from .preprocessing import _atomic_json
            state_path = args.output / "work" / base_key / "state.json"
            def baseline_progress(phase, completed, current_incident_id, counters):
                _atomic_json(state_path, {"schema_version": "scientific-preprocessing-work-state-v2",
                    "status": "IN_PROGRESS", "phase": phase, "base_key": base_key,
                    "split_key": split_key, "parent_pid": __import__("os").getpid(),
                    "total_baseline": len(splits["train"]), "completed_baseline": completed,
                    "total_features": 0, "completed_features": 0,
                    "current_incident_id": current_incident_id,
                    "last_completed_incident": current_incident_id, **counters})
            try:
                baseline, report = _baseline_crash_safe(
                    registry=registry, rcaeval_cache=args.rcaeval_cache,
                    sources=evidence_loader.sources, train_incident_ids=splits["train"],
                    work_root=args.output / "work", base_key=base_key, split_key=split_key,
                    resume=args.resume, progress=baseline_progress,
                )
            except RuntimeError:
                # WorkerExecutionError has already persisted the bounded,
                # structured diagnostic at this exact planner binding.
                raise
            from .preprocessing import _baseline_snapshot_bindings
            payload = _load_valid_baseline_snapshot(snapshot, _baseline_snapshot_bindings(
                base_key=base_key, split_key=split_key, train_incident_ids=splits["train"]))
            if payload is None:
                raise RuntimeError("OFFICIAL_BASELINE_CHECKPOINT_INVALID")
            _atomic_json(state_path, {"schema_version": "scientific-preprocessing-work-state-v2",
                "status": "COMPLETED", "phase": "BASELINE_FINALIZE", "base_key": base_key,
                "split_key": split_key, "parent_pid": __import__("os").getpid(),
                "total_baseline": len(splits["train"]), "completed_baseline": len(splits["train"]),
                "total_features": 0, "completed_features": 0, "baseline_sha256": payload["baseline_sha256"],
                **report})
            print(canonical_json({"status": "COMPLETED", "base_key": base_key,
                                  "split_key": split_key,
                                  "baseline_sha256": sha256_value(json.loads(baseline.to_json())),
                                  **report}))
            return 0
        from .preprocessing import _baseline_snapshot_bindings
        snapshot_payload = _load_valid_baseline_snapshot(snapshot, _baseline_snapshot_bindings(
            base_key=base_key, split_key=split_key, train_incident_ids=splits["train"]))
        if snapshot_payload is None:
            raise RuntimeError("OFFICIAL_BASELINE_NOT_AVAILABLE: run prepare-baseline or preprocess --resume with identical planner bindings first")
        baseline_json = snapshot_payload["baseline_json"]
        target = args.output / "work" / base_key / "incidents" / f"{_checkpoint_name(args.incident_id)}.json"
        source = evidence_loader.sources[args.incident_id]
        bindings = _checkpoint_bindings(base_key=base_key, incident_id=args.incident_id, source=source,
            baseline_sha256=sha256_value(json.loads(baseline_json)), split_key=split_key)
        cached = _load_valid_incident_checkpoint(target, bindings) if args.resume else None
        if cached is not None:
            print(canonical_json({"status": "COMPLETED", "incident_id": args.incident_id, "base_key": base_key,
                "split_key": split_key, "checkpoint_status": "HIT", "worker_executed": False,
                "checkpoint": str(target), "worker_returncode": None}))
            return 0
        checkpoint_status = "INVALID_RECOMPUTED" if target.is_file() else "MISS"
        result = _run_incident_worker(
            registry=registry, rcaeval_cache=args.rcaeval_cache,
            incident_id=args.incident_id,
            split=incident_split,
            base_key=base_key, split_key=split_key,
            baseline_snapshot_path=snapshot,
            baseline_sha256=snapshot_payload["baseline_sha256"],
            baseline_snapshot_bindings=_baseline_snapshot_bindings(
                base_key=base_key, split_key=split_key,
                train_incident_ids=splits["train"]), output=target,
        )
        if result.get("status") == "FAILED_WORKER":
            state_path = args.output / "work" / base_key / "state.json"
            state = _failed_worker_state(
                phase="FEATURES", worker_kind="FEATURE", base_key=base_key,
                split_key=split_key, diagnostic=result, state_path=state_path,
            )
            result = {**result, **state, "diagnostic_state": str(state_path)}
            print(canonical_json(result))
            return 1
        print(canonical_json({
            "status": "COMPLETED", "base_key": base_key, "split_key": split_key,
            "incident_id": args.incident_id, "checkpoint_status": checkpoint_status,
            "worker_executed": True, "checkpoint": str(target),
            "worker": result.get("worker", {}), "worker_returncode": result.get("worker", {}).get("returncode"),
        }))
        return 0
    if args.command == "preflight":
        registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
        result = dry_run(registry, _configs(args.config_dir), args.output, progress=True)
        print(json.dumps({"valid": result["valid"], "output": result["output"]}, sort_keys=True))
        return 0
    registry = FrozenDatasetRegistry.load(args.registry, REPOSITORY_ROOT)
    payload = load_campaign_config(args.config)
    if args.command == "dry-run":
        if not registry.preflight()["valid"]:
            raise RuntimeError("DATASET_PREFLIGHT_FAIL")
        require_clean_final(REPOSITORY_ROOT, mode=args.mode,
                            allow_dirty_development=args.allow_dirty_development)
        config, summary, pairing, h3_runs = compact_plan(payload, registry.capabilities())
        root = args.output / payload["family"] / payload["campaign_id"]
        store = CampaignStoreV3(root, config.configuration_sha256)
        store.write_json("configuration.json", payload, immutable=True)
        store.write_json("environment.json", environment_report(REPOSITORY_ROOT), immutable=True)
        store.write_json("compact-execution-plan.json", {
            "schema_version": "compact-execution-plan-v3", "not_for_analysis": True,
            "summary": summary, "pairing": pairing,
        }, immutable=True)
        if h3_runs:
            store.write_json("h3-blind-map-private.json", deterministic_blind_map(
                h3_runs, seed=min(run.seed for run in h3_runs)), immutable=True)
        store.write_sha256s()
        print(json.dumps(summary, sort_keys=True))
        return 0
    tranche = load_scientific_tranche(args.scientific_tranche) if args.scientific_tranche else None
    if tranche is not None and payload["family"] != "H3":
        # The documented isolated preprocessing root is deterministic from the
        # immutable tranche id.  This prevents a tranche run from silently
        # consuming the full-campaign catalog configured in the frozen family
        # file; the source config on disk remains untouched.
        payload = json.loads(json.dumps(payload))
        payload.setdefault("criteria", {})["scientific_benchmark_catalog"] = str(
            Path("artifacts/final-experiments-v3")
            / f"scientific-inputs-{tranche['tranche_id']}" / "benchmark-catalog.json")
        if payload["family"] == "H4":
            payload["criteria"]["scientific_trained_artifact_index"] = str(
                Path("artifacts/final-experiments-v3")
                / f"trained-artifacts-{tranche['tranche_id']}" / "INDEX.json")
    config, planned_runs = build_plan(payload, registry.capabilities())
    if tranche is not None:
        if tranche.get("selection_sha256") != selection_hash(tranche):
            raise ValueError("SCIENTIFIC_TRANCHE_SELECTION_SHA256_MISMATCH")
        parent = next((run for run in planned_runs if (
            run.protocol == tranche.get("protocol") and run.seed == tranche.get("seed")
            and sha256_value({
                "train": sorted(run.train_incident_ids),
                "validation": sorted(run.validation_incident_ids),
                "test": sorted(run.test_incident_ids),
            }) == tranche.get("parent_split_key"))), None)
        if parent is None:
            raise ValueError("SCIENTIFIC_TRANCHE_PARENT_SPLIT_NOT_IN_CAMPAIGN")
        validate_scientific_tranche_parent_binding(
            tranche=tranche, parent_run=parent, registry=registry)
        if family := payload["family"]:
            if family != "H3":
                catalog_path = (REPOSITORY_ROOT / payload["criteria"][
                    "scientific_benchmark_catalog"]).resolve()
                catalog_entries = _read_json(catalog_path).get("datasets", ())
                planned_runs = bind_runs_to_scientific_tranche(
                    planned_runs, tranche, catalog_entries=catalog_entries)
    if not registry.preflight()["valid"]:
        raise RuntimeError("DATASET_PREFLIGHT_FAIL")
    require_clean_final(REPOSITORY_ROOT, mode=args.mode,
                        allow_dirty_development=args.allow_dirty_development)
    family = payload["family"]
    root = args.output / family / payload["campaign_id"]
    store = CampaignStoreV3(root, config.configuration_sha256)
    evaluation_context = list({run.incident_id: {
        "incident_id": run.incident_id, "source_manifest_hash": run.source_manifest_hash,
        "fault_type": run.fault_type, "scenario": run.scenario,
    } for run in planned_runs}.values())
    # Labels stay in a separate evaluation-only artifact and never reach executors.
    runs = tuple(replace(run, fault_type=None, scenario=None) for run in planned_runs)
    _write_pre_execution(store, payload, registry, config, runs, evaluation_context)
    if tranche is not None:
        store.write_json("scientific-tranche.json", {
            "schema_version": tranche["schema_version"],
            "scientific_tranche_id": tranche["tranche_id"],
            "scientific_tranche_sha256": tranche["selection_sha256"],
            "parent_split_sha256": tranche["parent_split_key"],
            "parent_base_key": tranche["parent_base_key"],
            "h3_sampling_unchanged": family == "H3",
        }, immutable=True)
    executors = _factory(args.executor_factory)(
        config=config, output=store.path, repository_root=REPOSITORY_ROOT)
    orchestrator = ExperimentOrchestratorV3(config, store, executors, REPOSITORY_ROOT)
    orchestrator.planner = _FilteredPlanner(config, runs)
    recovery = (prepare_selective_resume(store, runs)
                if args.command == "resume" else None)
    results = orchestrator.run(resume=args.command == "resume")
    write_final_result_indexes(store, results, {run.run_id: run for run in runs})
    if family == "H2":
        promote_trained_artifacts(
            REPOSITORY_ROOT, results, {run.run_id: run for run in runs},
            scientific_tranche=tranche)
    if family == "H3":
        audit = audit_h3_same_rca(results)
        store.write_json("h3-same-rca-binding.json", audit)
        if audit["status"] != "PASS":
            raise RuntimeError("H3_SAME_RCA_BINDING_FAIL")
    if recovery is not None:
        store.write_json("resume-report.json", recovery)
    store.write_sha256s()
    print(canonical_json({"family": family, "runs": len(results), "mode": args.mode,
                          "recovery": recovery}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
