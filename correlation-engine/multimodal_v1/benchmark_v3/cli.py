from __future__ import annotations

import argparse
import json
from pathlib import Path

from multimodal_v1.benchmark_v3.core import (
    MODEL_NAMES, BenchmarkDataset, _thaw, canonical_json, compare_predictions,
    evaluate_artifact, load_verified_artifact, stability_summary, train_and_evaluate,
)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_result(path, *, unsafe_unverified_input=False):
    target = Path(path)
    if target.is_dir():
        verified = load_verified_artifact(target)
        return {"manifest": _thaw(verified.manifest), **_thaw(verified.metrics)}
    if not unsafe_unverified_input:
        raise ValueError("JSON avulso requer --unsafe-unverified-input.")
    payload = _load(target)
    if "manifest" not in payload and (target.parent / "manifest.json").exists():
        payload = {"manifest": _load(target.parent / "manifest.json"), **payload}
    return payload


def _write(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rca-benchmark-v3")
    commands = result.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train")
    train.add_argument("--dataset", required=True)
    train.add_argument("--model", required=True, choices=MODEL_NAMES)
    train.add_argument("--output", required=True)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--tuning-budget", type=int, default=3)
    train.add_argument("--bootstrap-iterations", type=int, default=1000)
    train.add_argument("--selection-metric", default="validation_mrr",
                       choices=("validation_mrr", "validation_top_1", "validation_macro_f1"))
    train.add_argument("--overwrite", action="store_true")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--dataset", required=True)
    evaluate.add_argument("--artifact", required=True)
    evaluate.add_argument("--output", required=True)
    evaluate.add_argument("--bootstrap-iterations", type=int, default=1000)
    all_models = commands.add_parser("benchmark-all")
    all_models.add_argument("--dataset", required=True)
    all_models.add_argument("--output", required=True)
    all_models.add_argument("--seeds", default="42")
    all_models.add_argument("--tuning-budget", type=int, default=3)
    all_models.add_argument("--bootstrap-iterations", type=int, default=1000)
    all_models.add_argument("--selection-metric", default="validation_mrr",
                            choices=("validation_mrr", "validation_top_1", "validation_macro_f1"))
    all_models.add_argument("--overwrite", action="store_true")
    compare = commands.add_parser("compare")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--output", required=True)
    compare.add_argument("--seed", type=int, default=42)
    compare.add_argument("--iterations", type=int, default=10000)
    compare.add_argument("--unsafe-unverified-input", action="store_true")
    report = commands.add_parser("export-report")
    report.add_argument("--results", nargs="+", required=True)
    report.add_argument("--output", required=True)
    report.add_argument("--unsafe-unverified-input", action="store_true")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "train":
        dataset = BenchmarkDataset.from_json(args.dataset)
        result = train_and_evaluate(
            args.model, dataset, args.output, seed=args.seed,
            tuning_budget=args.tuning_budget,
            bootstrap_iterations=args.bootstrap_iterations,
            selection_metric=args.selection_metric, overwrite=args.overwrite,
        )
        print(canonical_json({"artifact_dir": result["artifact_dir"],
                              "metrics": result["metrics"]}))
    elif args.command == "evaluate":
        dataset = BenchmarkDataset.from_json(args.dataset)
        result = evaluate_artifact(
            args.artifact, dataset, bootstrap_iterations=args.bootstrap_iterations)
        _write(args.output, result)
        print(canonical_json(result["metrics"]))
    elif args.command == "benchmark-all":
        dataset = BenchmarkDataset.from_json(args.dataset)
        raw_seeds = [value.strip() for value in args.seeds.split(",") if value.strip()]
        if not raw_seeds:
            raise ValueError("benchmark-all requer ao menos uma seed.")
        try:
            seeds = tuple(int(value) for value in raw_seeds)
        except ValueError as exc:
            raise ValueError("Seeds devem ser inteiros válidos.") from exc
        if len(seeds) != len(set(seeds)):
            raise ValueError("Seeds devem ser únicas.")
        results = [train_and_evaluate(
            model, dataset, args.output, seed=seed,
            tuning_budget=args.tuning_budget,
            bootstrap_iterations=args.bootstrap_iterations,
            selection_metric=args.selection_metric, overwrite=args.overwrite,
        ) for seed in seeds for model in MODEL_NAMES]
        payload = {"results": [{"artifact_dir": item["artifact_dir"],
                                 "model": item["manifest"]["model_name"],
                                 "seed": item["manifest"]["seed"],
                                 "metrics": item["metrics"]} for item in results],
                   "stability": stability_summary(results)}
        _write(Path(args.output) / "benchmark-summary.json", payload)
        print(canonical_json(payload))
    elif args.command == "compare":
        comparison = compare_predictions(
            _load_result(args.left, unsafe_unverified_input=args.unsafe_unverified_input),
            _load_result(args.right, unsafe_unverified_input=args.unsafe_unverified_input),
                                         seed=args.seed, iterations=args.iterations)
        _write(args.output, comparison)
        print(canonical_json(comparison))
    else:
        results = [_load_result(path, unsafe_unverified_input=args.unsafe_unverified_input)
                   for path in args.results]
        dataset_hashes = {item.get("manifest", {}).get("dataset_sha256") for item in results}
        if None in dataset_hashes or len(dataset_hashes) != 1:
            raise ValueError("Relatório não combina datasets diferentes sem análise explícita.")
        rows = sorted((item["manifest"]["model_name"], item["manifest"]["seed"],
                       item["metrics"]) for item in results)
        manifest = results[0]["manifest"]
        lines = ["# RCA model benchmark v3", "",
                 f"Dataset SHA-256: `{next(iter(dataset_hashes))}`", "",
                 f"Protocolo: `{manifest.get('tuning_protocol')}`; seleção: `{manifest.get('selection_metric')}`; "
                 f"fit final: `{manifest.get('final_fit_policy')}`.", "",
                 "| Model | Seed | Macro-F1 | Top-1 | Top-3 | MRR | FPR |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        for model, seed, metrics in rows:
            lines.append(f"| {model} | {seed} | {metrics['macro_f1']:.4f} | "
                         f"{metrics['top_1']:.4f} | {metrics['top_3']:.4f} | "
                         f"{metrics['mrr']:.4f} | {metrics['fpr']:.4f} |")
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
