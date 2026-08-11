from __future__ import annotations

import argparse
from dataclasses import replace
import importlib
import json
from pathlib import Path

from .core import (
    CampaignStoreV3, CampaignValidatorV3, DatasetCapabilityV3,
    ExperimentConfigV3, ExperimentOrchestratorV3, ExperimentPlannerV3,
    ExperimentReportBuilderV3, build_campaign_manifest,
)
from .fixtures import build_smoke_orchestrator


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path) -> ExperimentConfigV3:
    payload = json.loads(path.read_text(encoding="utf-8"))
    datasets = tuple(DatasetCapabilityV3(**item) for item in payload.pop("datasets"))
    allowed = {field.name for field in __import__("dataclasses").fields(ExperimentConfigV3)}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Configuração contém campos desconhecidos: {sorted(unknown)}")
    for name in ("hypotheses", "protocols", "seeds", "explanation_modes",
                 "degradation_operators", "severities", "held_out_systems",
                 "held_out_sources"):
        if name in payload:
            payload[name] = tuple(payload[name])
    return ExperimentConfigV3(datasets=datasets, **payload)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="CausalODS experimental orchestrator v3")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "resume", "validate", "analyze", "export-paper"):
        item = commands.add_parser(name)
        item.add_argument("--config", type=Path, required=True)
        item.add_argument("--campaign-id")
        item.add_argument("--output", type=Path, default=Path("results/experiments-v3"))
        item.add_argument("--hypotheses")
        item.add_argument("--datasets")
        item.add_argument("--systems")
        item.add_argument("--protocols")
        item.add_argument("--seeds")
        item.add_argument("--repetitions", type=int)
        item.add_argument("--execution-mode", choices=("offline", "kafka-replay"))
        item.add_argument("--sample-size", type=int)
        item.add_argument("--explanation-modes")
        item.add_argument("--allow-live-llm", action="store_true", default=False)
        item.add_argument("--degradation-operators")
        item.add_argument("--severities")
        item.add_argument("--executor-factory",
                          help="Factory module:function que injeta executores científicos existentes.")
        item.add_argument("--dry-run", action="store_true")
        item.add_argument("--resume", action="store_true", dest="resume_flag")
        item.add_argument("--human-ratings", type=Path)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--campaign-id", default="smoke-v3")
    smoke.add_argument("--output", type=Path, default=Path("results/experiments-v3"))
    return root


def _load_results(store: CampaignStoreV3):
    return tuple(store.load_completed().values())


def _load_executor_factory(reference: str, config: ExperimentConfigV3,
                           output: Path):
    if ":" not in reference:
        raise ValueError("--executor-factory deve usar module:function.")
    module_name, function_name = reference.split(":", 1)
    function = getattr(importlib.import_module(module_name), function_name)
    executors = function(config=config, output=output, repository_root=REPOSITORY_ROOT)
    if not isinstance(executors, dict) or not set(config.hypotheses) <= set(executors):
        raise ValueError("Factory não retornou executores para todas as hipóteses selecionadas.")
    return executors


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "smoke":
        orchestrator = build_smoke_orchestrator(args.output, REPOSITORY_ROOT, args.campaign_id)
        results = orchestrator.run(resume=False)
        runs = orchestrator.planner.plan()
        validation = CampaignValidatorV3().validate(
            runs, results, store=orchestrator.store, repository_root=REPOSITORY_ROOT)
        ExperimentReportBuilderV3().build(orchestrator.store, results, validation)
        print(json.dumps({"campaign": args.campaign_id, "runs": len(results),
                          "complete": validation["complete"]}, sort_keys=True))
        return 0 if validation["complete"] else 2
    config = load_config(args.config)
    changes = {}
    for name in ("hypotheses", "protocols", "explanation_modes", "degradation_operators"):
        value = getattr(args, name)
        if value:
            changes[name] = tuple(item.strip() for item in value.split(",") if item.strip())
    if args.seeds:
        changes["seeds"] = tuple(int(item) for item in args.seeds.split(","))
    if args.severities:
        changes["severities"] = tuple(float(item) for item in args.severities.split(","))
    for name in ("campaign_id", "repetitions", "execution_mode", "sample_size"):
        value = getattr(args, name)
        if value is not None:
            changes[name] = value
    selected = config.datasets
    if args.datasets:
        names = set(args.datasets.split(","))
        selected = tuple(item for item in selected if item.source_dataset in names)
    if args.systems:
        names = set(args.systems.split(","))
        selected = tuple(item for item in selected if item.source_system in names)
    if selected != config.datasets:
        changes["datasets"] = selected
    if args.allow_live_llm:
        changes["allow_live_llm"] = True
    config = replace(config, **changes) if changes else config
    store = CampaignStoreV3(args.output, config.campaign_id)
    planner = ExperimentPlannerV3(config)
    runs = planner.plan()
    if args.command == "plan" or args.dry_run:
        manifest_path = store.path / "campaign-manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["identity"]["configuration_sha256"] != config.configuration_sha256:
                raise ValueError("Diretório de campanha contém configuração divergente.")
        else:
            store.write_json("campaign-manifest.json",
                             build_campaign_manifest(config, runs, REPOSITORY_ROOT), immutable=True)
        store.write_plan(runs)
        print(json.dumps(planner.summary(runs), sort_keys=True))
        return 0
    if args.command in {"run", "resume"}:
        if not args.executor_factory:
            raise ValueError(
                "run/resume requer --executor-factory module:function; a injeção explícita "
                "impede descoberta implícita de datasets, modelos ou credenciais.")
        orchestrator = ExperimentOrchestratorV3(
            config, store, _load_executor_factory(args.executor_factory, config, args.output),
            REPOSITORY_ROOT)
        results = orchestrator.run(resume=args.command == "resume" or args.resume_flag)
        validation = CampaignValidatorV3().validate(
            runs, results, store=store, repository_root=REPOSITORY_ROOT)
        print(json.dumps({"campaign": config.campaign_id, "runs": len(results),
                          "complete": validation["complete"]}, sort_keys=True))
        return 0 if validation["complete"] else 2
    results = _load_results(store)
    validation = CampaignValidatorV3().validate(
        runs, results, store=store, repository_root=REPOSITORY_ROOT)
    if args.command == "validate":
        store.write_json("experiment-validity.json", validation)
        store.write_sha256s()
        print(json.dumps(validation, sort_keys=True))
        return 0 if validation["complete"] else 2
    report_builder = ExperimentReportBuilderV3()
    report_builder.build(store, results, validation)
    if args.human_ratings:
        store.write_json("h3/h3-human-agreement.json",
                         report_builder.import_human_ratings(args.human_ratings))
        store.write_sha256s()
    print(json.dumps({"runs": len(results), "complete": validation["complete"]}, sort_keys=True))
    return 0 if validation["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
