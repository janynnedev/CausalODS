"""Offline 40x3 final-message token audit; never calls an LLM provider."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import multiprocessing

from explanations_v2.prompt_builder import PromptBuilder
from experiments.multimodal_v3.core import sha256_value

from .h3_materializer import expected_h3_runs
from .h3_openai_freeze import (
    ChatMessageTokenEstimator, CONDITIONS, messages_for_prompt, rendered_input_hash,
)
from .providers import FrozenRawTelemetryMapping, load_h3_inputs


ROOT = Path(__file__).resolve().parents[2]


def _selected_raw(args):
    repository_root, descriptor = args
    mapping = FrozenRawTelemetryMapping(repository_root, (descriptor,), verified=True)
    return descriptor["incident_id"], mapping[descriptor["incident_id"]]


def percentile(values, quantile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def run_audit(*, repository_root: Path = ROOT) -> dict:
    config_path = repository_root / "experiments/configs/final-v3/h3.json"
    registry_path = repository_root / "experiments/configs/final-v3/datasets.json"
    _, _, config, _, incident_ids = expected_h3_runs(
        config_path, registry_path, repository_root)
    supplied = load_h3_inputs(
        config=config, repository_root=repository_root,
        expected_incident_ids=incident_ids,
        expected_sample_sha256=sha256_value(list(incident_ids)))
    index = json.loads((repository_root /
        config.criteria["scientific_rca_results"]).read_text(encoding="utf-8"))
    with multiprocessing.Pool(processes=min(4, len(incident_ids))) as pool:
        raw_by_id = dict(pool.map(
            _selected_raw, ((repository_root, item) for item in index["incidents"])))
    builder, estimator, rows = PromptBuilder(), ChatMessageTokenEstimator(), []
    for incident_id in incident_ids:
        rca = supplied["rca_results"][incident_id]
        evidence = __import__("explanations_v2.service", fromlist=["ExplanationService"]).ExplanationService()._build_evidence(rca)
        rca_hash = sha256_value(rca.to_dict())
        for mode in CONDITIONS:
            raw = raw_by_id[incident_id] if mode == "raw_telemetry" else ()
            prompt = builder.build(evidence, mode=mode, temperature=0.0, raw_telemetry=raw)
            tokens = estimator.count(messages_for_prompt(prompt.prompt))
            rows.append({"incident_id": incident_id, "mode": mode,
                         "estimated_input_tokens": tokens,
                         "rendered_input_hash": rendered_input_hash(prompt.prompt),
                         "rca_result_hash": rca_hash})
    values = [row["estimated_input_tokens"] for row in rows]
    candidates = (8192, 16384, 32768, 65536)
    budget = next((candidate for candidate in candidates if max(values) <= .8 * candidate), None)
    return {
        "schema_version": "h3-input-token-audit-v1", "requests_checked": len(rows),
        "minimum_input_tokens": min(values), "median_input_tokens": statistics.median(values),
        "p95_input_tokens": percentile(values, .95), "maximum_input_tokens": max(values),
        "selected_budget": budget,
        "minimum_headroom": (budget - max(values) if budget else None),
        "over_budget_count": (sum(value > budget for value in values) if budget else len(values)),
        "budget_freeze": ("PASS" if budget else "FAIL"),
        "tokenizer": {"library": "tiktoken", "version": estimator.library_version,
                      "encoding": estimator.encoding_name, "model_mapping": "exact snapshot",
                      "counting_method": estimator.method, "count_kind": "deterministic estimate"},
        "rca_identity_audit": ("PASS" if all(len({row["rca_result_hash"] for row in rows
            if row["incident_id"] == incident}) == 1 for incident in incident_ids) else "FAIL"),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, sort_keys=True))
    return 0 if report["budget_freeze"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
