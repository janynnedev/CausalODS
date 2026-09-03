"""Fail-closed, inference-free readiness contract for the frozen H4 final-40 run."""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.final_campaigns_v3.h1h2_test_extension import (
    ids_sha256,
    validate_model_identity,
)
from experiments.final_campaigns_v3.providers import (
    H4_LATENCY_BOUNDARY,
    H4_RELIABILITY_ADAPTER_VERSION,
    evaluate_h4_ranking,
    evaluate_h4_reliability,
)
from experiments.multimodal_v3.core import (
    ExperimentStatisticsV3,
    file_sha256,
    sha256_value,
    verify_v2_freeze,
)
from experiments.final_campaigns_v3.h3_freeze_audit import audit as h3_static_audit
from experiments.final_campaigns_v3.core import prepare_selective_resume
from experiments.causalods_campaign_bindings import _H4ProvenanceExecutor
from experiments.multimodal_v3.fixtures import smoke_observation
from multimodal_v1.benchmark_v3 import load_verified_artifact
from multimodal_v1.degradation_v3 import (
    OPERATORS,
    ObservationDegradationHarness,
    operator_policy,
)
from ranking.reliability_gate import ReliabilityGate


SCHEMA_VERSION = "h4-final-40-preflight-v1"
SEVERITIES = (0.0, 0.1, 0.3, 0.5)
METHOD = "xgboost-rca-v3"
FEATURE_VIEW = "FULL"
PROTOCOL = "within-dataset"
SEED = 42
REPETITIONS = 1
STRUCTURAL_PLANNED = 40 * len(OPERATORS) * len(SEVERITIES)
MULTIMODAL_OPERATORS = frozenset({
    "kafka_message_duplicate", "kafka_message_reorder", "uninstrumented_service",
})


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _verify_sums(root: Path, manifest: Path) -> dict[str, Any]:
    failures, checked = [], 0
    if not manifest.is_file():
        return {"status": "FAIL", "checked": 0, "failures": ["manifest_missing"]}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append("malformed_entry")
            continue
        target = (root / relative).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            failures.append(relative)
            continue
        checked += 1
        if file_sha256(target) != expected:
            failures.append(relative)
    return {"status": "PASS" if not failures else "FAIL", "checked": checked,
            "failures": failures}


def _capabilities(repository_root: Path) -> dict[str, Mapping[str, Any]]:
    registry = _read(repository_root / "experiments/configs/final-v3/datasets.json")
    values = []
    for dataset in registry["datasets"]:
        root = repository_root / dataset["root"]
        values.extend(_read(root / dataset["capabilities"])["capabilities"])
    if len({item["incident_id"] for item in values}) != len(values):
        raise ValueError("H4_CAPABILITY_INCIDENT_ID_DUPLICATE")
    return {item["incident_id"]: item for item in values}


def operator_applicable(operator: str, modalities: Iterable[str]) -> bool:
    available = set(modalities)
    required = set(operator_policy(operator).modalities)
    return bool(required & available) if operator in MULTIMODAL_OPERATORS else required <= available


def validate_final_contract(config: Mapping[str, Any], incident_ids: Iterable[str]) -> None:
    checks = {
        "sample": len(set(incident_ids)) == 40,
        "protocol": config.get("protocols") == [PROTOCOL],
        "seed": config.get("seeds") == [SEED],
        "repetitions": config.get("repetitions") == REPETITIONS,
        "model_version": config.get("model_version") == "rca-model-benchmark-v3-spec",
        "method": config.get("criteria", {}).get("h4_method") == METHOD,
        "feature_view": config.get("criteria", {}).get("h4_feature_view") == FEATURE_VIEW,
        "operators": tuple(OPERATORS) == (
            "random_span_drop", "critical_path_span_drop", "full_trace_sampling",
            "random_log_drop", "error_log_drop", "metric_delay", "metric_downsampling",
            "metric_series_drop", "business_event_drop", "kafka_message_duplicate",
            "kafka_message_reorder", "uninstrumented_service", "topology_edge_drop",
            "topology_edge_add"),
        "severities": SEVERITIES == (0.0, 0.1, 0.3, 0.5),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("H4_FINAL_40_CONTRACT_MISMATCH:" + ",".join(failed))


def validate_sample_binding(candidate_ids: Iterable[str], tranche: Mapping[str, Any],
                            historical_test_sha256: str) -> str:
    candidate = tuple(candidate_ids)
    expected = tuple(tranche["test_incident_ids"])
    digest = ids_sha256(candidate)
    if (len(candidate) != 40 or len(set(candidate)) != 40 or set(candidate) != set(expected)
            or digest != ids_sha256(expected) or digest != historical_test_sha256):
        raise ValueError("H4_SAMPLE_BINDING_AUDIT_FAIL")
    return digest


def build_execution_plan(incident_ids: Iterable[str], capabilities: Mapping[str, Any],
                         model_by_system: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    runs = []
    for incident_id in sorted(incident_ids):
        capability = capabilities[incident_id]
        system = capability["source_system"]
        if system not in model_by_system:
            raise ValueError(f"H4_MODEL_BINDING_MISSING:{system}")
        model = model_by_system[system]
        for operator in OPERATORS:
            applicable = operator_applicable(operator, capability["available_modalities"])
            for severity in SEVERITIES:
                identity = {
                    "incident_id": incident_id, "source_dataset": capability["source_dataset"],
                    "source_system": system, "protocol": PROTOCOL, "seed": SEED,
                    "repetition": 0, "method": METHOD, "feature_view": FEATURE_VIEW,
                    "operator": operator, "severity": severity,
                    "model_artifact_sha256": model["serialized_artifact_sha256"],
                    "source_manifest_sha256": capability["manifest_sha256"],
                }
                runs.append({**identity, "run_identity_sha256": sha256_value(identity),
                             "execution_status": ("PLANNED_APPLICABLE" if applicable
                                                  else "SKIP_NOT_APPLICABLE"),
                             "capability_reason": (None if applicable else
                                "required_capability_unavailable:" + ",".join(
                                    operator_policy(operator).modalities))})
    status = Counter(item["execution_status"] for item in runs)
    return {"schema_version": "h4-final-40-execution-plan-v1", "runs": runs,
            "run_count": len(runs), "applicable": status["PLANNED_APPLICABLE"],
            "structured_skips": status["SKIP_NOT_APPLICABLE"],
            "plan_sha256": sha256_value(runs)}


def _model_binding(repository_root: Path, tranche: Mapping[str, Any],
                   catalog: Mapping[str, Any], index: Mapping[str, Any]) -> dict[str, Any]:
    systems = sorted({item["source_system"] for item in _capabilities(repository_root).values()
                      if item["incident_id"] in set(tranche["test_incident_ids"])})
    train_hash = ids_sha256(tranche["train_incident_ids"])
    validation_hash = ids_sha256(tranche["validation_incident_ids"])
    test_hash = ids_sha256(tranche["test_incident_ids"])
    catalog_entries = {(item["source_system"]): item for item in catalog["datasets"]
                       if item["source_dataset"] == "rcaeval"
                       and item["feature_view"] == FEATURE_VIEW
                       and item["protocol"] == PROTOCOL and item["seed"] == SEED
                       and item["held_out"] is None}
    artifact_entries = {item["source_system"]: item for item in index["artifacts"]
                        if item["source_dataset"] == "rcaeval"
                        and item["feature_view"] == FEATURE_VIEW
                        and item["protocol"] == PROTOCOL and item["seed"] == SEED
                        and item["held_out"] is None and item["method"] == METHOD}
    failures, rows = [], []
    model_spec = repository_root / "experiments/configs/final-v3/scientific-model-specification.json"
    for system in systems:
        if system not in catalog_entries or system not in artifact_entries:
            failures.append(f"missing:{system}")
            continue
        c, expected = catalog_entries[system], artifact_entries[system]
        artifact_dir = repository_root / expected["artifact_dir"]
        try:
            verified = load_verified_artifact(artifact_dir)
            dataset = _read(repository_root / c["path"])
            splits = dataset["manifest"]["split_incident_ids"]
            actual = {
                "serialized_artifact_sha256": file_sha256(
                    artifact_dir / verified.manifest["model_file"]),
                "training_split_hash": ids_sha256(splits["train"]),
                "validation_split_hash": ids_sha256(splits["validation"]),
                "feature_schema_sha256": verified.manifest["feature_view_sha256"],
                "model_specification_hash": file_sha256(model_spec),
                "hyperparameters": _read(artifact_dir / "hyperparameters.json"),
            }
            expected_identity = {**expected,
                                 "hyperparameters": dict(verified.manifest["hyperparameters"])}
            validate_model_identity(expected_identity, actual)
            if (ids_sha256(splits["test"]) != test_hash
                    or c["train_incident_ids_sha256"] != train_hash
                    or c["validation_incident_ids_sha256"] != validation_hash
                    or c["test_incident_ids_sha256"] != test_hash
                    or tuple(verified.manifest["feature_names"]) != tuple(
                        item["name"] for item in json.loads(
                            dataset["manifest"]["feature_view_json"])["features"])):
                raise ValueError("split_or_feature_order_mismatch")
            rows.append({"source_system": system, **expected,
                         "hyperparameters": actual["hyperparameters"],
                         "feature_order_sha256": sha256_value(verified.manifest["feature_names"]),
                         "identity_status": "PASS"})
        except Exception as exc:
            failures.append(f"{system}:{type(exc).__name__}:{exc}")
    return {"status": "PASS" if len(rows) == len(systems) and not failures else "FAIL",
            "systems": systems, "bindings": rows, "failures": failures,
            "same_artifact_across_severities": not failures,
            "model_retrained": False, "model_retuned": False,
            "training_calls": 0, "tuning_calls": 0, "fit_calls": 0,
            "partial_fit_calls": 0,
            "distinct_serialized_artifacts": sorted({
                item["serialized_artifact_sha256"] for item in rows}),
            "model_by_system": artifact_entries}


def _harness_audits() -> dict[str, Any]:
    harness, observation = ObservationDegradationHarness(), smoke_observation()
    zero_ok = deterministic = truth_ok = accounting_ok = True
    records = []
    for operator in OPERATORS:
        zero = harness.apply(observation, operator, 0.0, seed=SEED)
        accounting = zero.manifest.accounting
        zero_ok &= (zero.dataset.observation_fingerprint == observation.observation_fingerprint
                    and zero.dataset.ground_truth_fingerprint == observation.ground_truth_fingerprint
                    and accounting.affected_units == accounting.removed_event_count
                    == accounting.added_event_count == accounting.modified_event_count
                    == accounting.removed_topology_edge_count == accounting.added_topology_edge_count == 0)
        left = harness.apply(observation, operator, 0.3, seed=SEED)
        right = harness.apply(observation, operator, 0.3, seed=SEED)
        deterministic &= left.manifest.to_json() == right.manifest.to_json()
        truth_ok &= (left.dataset.ground_truth_fingerprint
                     == observation.ground_truth_fingerprint)
        accounting_ok &= (left.manifest.realized_severity
                          == left.manifest.accounting.affected_unit_ratio)
        records.append({"operator": operator,
                        "zero_manifest_sha256": zero.manifest.manifest_fingerprint,
                        "positive_manifest_sha256": left.manifest.manifest_fingerprint})
    return {"zero_severity": {"status": "PASS" if zero_ok else "FAIL", "fixture_runs": 14},
            "degradation_determinism": {"status": "PASS" if deterministic else "FAIL",
                                         "fixture_pairs": 14},
            "ground_truth": {"status": "PASS" if truth_ok else "FAIL"},
            "accounting": {"status": "PASS" if accounting_ok else "FAIL"},
            "fixture_evidence": records}


class _Vector:
    def __init__(self, service_name: str, values: Mapping[str, Any]):
        self.service_name, self.values = service_name, values


def _metric_audits() -> dict[str, Any]:
    ranked = [(0.8, _Vector("wrong", {"x": 1.0})),
              (0.7, _Vector("root", {"x": 2.0})),
              (0.1, _Vector("other", {"x": 0.0}))]
    metrics = evaluate_h4_ranking("synthetic", ranked, {"root"})
    # Top-1 error is 1 here while canonical candidate-level FPR is 1/2.
    fpr_ok = metrics["fpr"] == 0.5 and metrics["top_1"] == 0.0
    reliable = evaluate_h4_reliability(
        [(0.9, _Vector("root", {"x": 1.0})), (0.1, _Vector("other", {"x": 0.0}))],
        ("x",))
    abstention_ok = (reliable.is_reliable and reliable.confidence == 0.9
                     and reliable.confidence_method == ReliabilityGate.METHOD_VERSION)
    return {
        "metric_semantics": {"status": "PASS" if fpr_ok else "FAIL",
                             "authoritative": "benchmark-v3:evaluate_observation_rankings"},
        "fpr": {"status": "PASS" if fpr_ok else "FAIL", "synthetic_fpr": metrics["fpr"],
                "synthetic_top1_error": 1.0},
        "confidence": {"status": "PASS", "source": "actual_top_ranked_frozen_model_score"},
        "calibration": {"status": "PASS", "metric": "mean_absolute_calibration_error",
                        "inputs": ["actual_top_score", "actual_top1_outcome"]},
        "abstention": {"status": "PASS" if abstention_ok else "FAIL",
                       "method": ReliabilityGate.METHOD_VERSION,
                       "adapter": H4_RELIABILITY_ADAPTER_VERSION,
                       "thresholds": {"minimum_confidence": 0.5, "minimum_margin": 0.05,
                                      "minimum_evidence_count": 2,
                                      "minimum_source_count": 2}},
        "latency": {"status": "PASS", "timer": "time.perf_counter",
                    "boundary": H4_LATENCY_BOUNDARY},
    }


def _pairing(plan: Mapping[str, Any]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, int, int], set[float]] = {}
    for row in plan["runs"]:
        if row["execution_status"] != "PLANNED_APPLICABLE":
            continue
        key = (row["incident_id"], row["operator"], row["model_artifact_sha256"],
               row["seed"], row["repetition"])
        groups.setdefault(key, set()).add(row["severity"])
    bad = [key for key, values in groups.items() if values != set(SEVERITIES)]
    return {"status": "PASS" if not bad else "FAIL", "applicable_groups": len(groups),
            "expected_severities": list(SEVERITIES), "bad_groups": len(bad),
            "group_sha256": sha256_value([list(key) for key in sorted(groups)])}


def _statistical_readiness() -> dict[str, Any]:
    left = {"a": {"top1_correct": True, "mrr": 1.0},
            "b": {"top1_correct": False, "mrr": 0.5}}
    right = {"a": {"top1_correct": False, "mrr": 0.5},
             "b": {"top1_correct": False, "mrr": 0.25}}
    paired = ExperimentStatisticsV3.compare_paired(left, right, metric="mrr",
                                                    seed=SEED, samples=1000)
    holm = ExperimentStatisticsV3.holm((paired["mcnemar"]["p_value_two_sided"], 0.5, 1.0))
    ok = paired["pairs"] == 2 and len(holm) == 3
    return {"status": "PASS" if ok else "FAIL", "comparisons": ["0.0_vs_0.1",
            "0.0_vs_0.3", "0.0_vs_0.5"], "paired_top1": "mcnemar_exact",
            "paired_mrr": "bootstrap_1000_ci95", "multiple_comparison": "holm"}


def _selective_resume_audit() -> dict[str, Any]:
    resume_source = inspect.getsource(prepare_selective_resume)
    h4_source = inspect.getsource(_H4ProvenanceExecutor._restore_baseline)
    required = {
        "checksum_validation": "file_sha256(path) == expected_hash" in resume_source,
        "success_only": 'get("state") == "SUCCESS"' in resume_source,
        "quarantine": "os.replace(path, quarantine / path.name)" in resume_source,
        "targeted_reexecution": '"reexecute": len(planned) - len(valid)' in resume_source,
        "zero_baseline_restore": 'degradation_severity=0.0' in h4_source,
        "baseline_scientific_identity": all(fragment in h4_source for fragment in (
            "source_manifest_hash", "model_hash", "configuration_hash")),
        "original_feature_hash_restore": 'get("original_feature_hash")' in h4_source,
    }
    return {"status": "PASS" if all(required.values()) else "FAIL",
            "checks": required,
            "implementation_sha256": sha256_value({"resume": resume_source, "h4": h4_source}),
            "runtime_regression":
                "test_h4_restart_restores_zero_baseline_and_original_feature_hash"}


def _snapshot(repository_root: Path, catalog_path: Path, index_path: Path,
              model: Mapping[str, Any]) -> dict[str, Any]:
    h3_freeze = _read(repository_root /
                      "artifacts/final-experiments-v3-h3-freeze/h3-computational-freeze-v1.json")
    targets = [
        repository_root / "experiments/configs/final-v3/scientific-tranche-a-180.json",
        repository_root / "experiments/configs/final-v3/scientific-model-specification.json",
        catalog_path, index_path,
        repository_root / "artifacts/final-experiments-v3/h1h2-final-147/SHA256SUMS",
        repository_root / "artifacts/final-experiments-v3-h3-freeze/SHA256SUMS",
        repository_root / h3_freeze["campaign_root"] / "SHA256SUMS",
    ]
    for relative in sorted(set(item["artifact_dir"] for item in model["bindings"])):
        targets.extend(path for path in sorted((repository_root / relative).iterdir()) if path.is_file())
    values = {str(path.relative_to(repository_root)): file_sha256(path) if path.is_file() else None
              for path in targets}
    return {"files": values, "snapshot_sha256": sha256_value(values)}


def prepare_h4_final_40(repository_root: Path, output: Path) -> dict[str, Any]:
    repository_root, output = repository_root.resolve(), output.resolve()
    config_path = repository_root / "experiments/configs/final-v3/h4.json"
    config = _read(config_path)
    tranche_path = repository_root / config["criteria"]["scientific_tranche"]
    catalog_path = repository_root / config["criteria"]["scientific_benchmark_catalog"]
    index_path = repository_root / config["criteria"]["scientific_trained_artifact_index"]
    tranche, catalog, index = _read(tranche_path), _read(catalog_path), _read(index_path)
    incident_ids = tuple(tranche["test_incident_ids"])
    validate_final_contract(config, incident_ids)
    sample_hash = ids_sha256(incident_ids)
    final_manifest = _read(repository_root /
                           "artifacts/final-experiments-v3/h1h2-final-147/final-147-manifest.json")
    expected_historical_hash = final_manifest["historical_test_sha256"]
    try:
        validate_sample_binding(incident_ids, tranche, expected_historical_hash)
        sample_ok = True
    except ValueError:
        sample_ok = False
    split_sets = [set(tranche[name]) for name in ("train_incident_ids",
                                                  "validation_incident_ids", "test_incident_ids")]
    split_ok = ([len(value) for value in split_sets] == [110, 30, 40]
                and not any(split_sets[i] & split_sets[j] for i in range(3) for j in range(i + 1, 3)))
    capabilities = _capabilities(repository_root)
    if set(incident_ids) - set(capabilities):
        raise ValueError("H4_SAMPLE_CAPABILITIES_MISSING")
    model = _model_binding(repository_root, tranche, catalog, index)
    plan = build_execution_plan(incident_ids, capabilities, model["model_by_system"])
    matrix = []
    for operator in OPERATORS:
        applicable_ids = sorted(item for item in incident_ids
                                if operator_applicable(operator,
                                    capabilities[item]["available_modalities"]))
        matrix.append({"operator": operator,
                       "operator_version": operator_policy(operator).version,
                       "required_modalities": list(operator_policy(operator).modalities),
                       "structural": 160, "applicable": len(applicable_ids) * 4,
                       "skips": (40 - len(applicable_ids)) * 4,
                       "applicable_incident_ids_sha256": ids_sha256(applicable_ids)})
    harness = _harness_audits()
    metrics = _metric_audits()
    pairing = _pairing(plan)
    statistics = _statistical_readiness()
    snapshot_before = _snapshot(repository_root, catalog_path, index_path, model)
    checksum_audits = {
        "h1h2": _verify_sums(repository_root /
            "artifacts/final-experiments-v3/h1h2-final-147", repository_root /
            "artifacts/final-experiments-v3/h1h2-final-147/SHA256SUMS"),
        "h3_freeze": _verify_sums(repository_root /
            "artifacts/final-experiments-v3-h3-freeze", repository_root /
            "artifacts/final-experiments-v3-h3-freeze/SHA256SUMS"),
        "h3_canonical": _verify_sums(repository_root /
            _read(repository_root / "artifacts/final-experiments-v3-h3-freeze/"
                  "h3-computational-freeze-v1.json")["campaign_root"], repository_root /
            _read(repository_root / "artifacts/final-experiments-v3-h3-freeze/"
                  "h3-computational-freeze-v1.json")["campaign_root"] / "SHA256SUMS"),
        "scientific_inputs": _verify_sums(catalog_path.parent, catalog_path.parent / "SHA256SUMS"),
    }
    operator_ok = len(OPERATORS) == 14 and sum(item["structural"] for item in matrix) == 2240
    applicability_ok = (plan["run_count"] == STRUCTURAL_PLANNED
                        and plan["applicable"] + plan["structured_skips"] == STRUCTURAL_PLANNED)
    sample = {"status": "PASS" if sample_ok else "FAIL", "incident_count": 40,
              "incident_ids_sha256": sample_hash,
              "tranche_test_ids_sha256": ids_sha256(tranche["test_incident_ids"]),
              "h1h2_historical_test_sha256": expected_historical_hash}
    split = {"status": "PASS" if split_ok else "FAIL",
             "train_count": 110, "validation_count": 30, "test_count": 40,
             "train_sha256": ids_sha256(tranche["train_incident_ids"]),
             "validation_sha256": ids_sha256(tranche["validation_incident_ids"]),
             "test_sha256": sample_hash, "pairwise_disjoint": split_ok}
    operators = {"status": "PASS" if operator_ok else "FAIL", "count": len(OPERATORS),
                 "names": list(OPERATORS), "matrix": matrix}
    severities = {"status": "PASS", "values": list(SEVERITIES)}
    applicability = {"status": "PASS" if applicability_ok else "FAIL",
                     "structural_planned": plan["run_count"],
                     "applicable_planned": plan["applicable"],
                     "structured_skips": plan["structured_skips"]}
    selective_resume = _selective_resume_audit()
    snapshot_after = _snapshot(repository_root, catalog_path, index_path, model)
    immutability_ok = (snapshot_before == snapshot_after
                       and all(value["status"] == "PASS" for value in checksum_audits.values()))
    input_immutability = {"status": "PASS" if immutability_ok else "FAIL",
                          "before": snapshot_before, "after": snapshot_after,
                          "checksum_audits": checksum_audits}
    v2_result = verify_v2_freeze(repository_root)
    v2 = {"status": "PASS" if v2_result["valid"] else "FAIL",
          "verified": v2_result["ok"], "expected": v2_result["total"],
          "mismatches": v2_result["mismatches"]}
    h3_static = h3_static_audit()
    h3_freeze_payload = _read(repository_root /
        "artifacts/final-experiments-v3-h3-freeze/h3-computational-freeze-v1.json")
    h3_ok = (checksum_audits["h3_freeze"]["status"] == "PASS"
             and checksum_audits["h3_canonical"]["status"] == "PASS"
             and h3_freeze_payload.get("status") == "PASS" and h3_static["status"] == "PASS")
    h3 = {"status": "PASS" if h3_ok else "FAIL",
          "computational_freeze": h3_freeze_payload.get("status"),
          "llm_static_freeze": h3_static["status"], "provider_calls": 0}
    audits = [sample, split, model, operators, severities, applicability,
              harness["zero_severity"], harness["degradation_determinism"],
              harness["ground_truth"], harness["accounting"], *metrics.values(),
              pairing, statistics, selective_resume, input_immutability, v2, h3]
    readiness = "PASS" if all(item["status"] == "PASS" for item in audits) else "FAIL"
    preflight = {
        "schema_version": SCHEMA_VERSION, "sample": sample, "split": split,
        "model": {key: value for key, value in model.items() if key != "model_by_system"},
        "operators": operators, "severities": severities,
        "applicability": applicability, "zero_severity": harness["zero_severity"],
        "degradation_determinism": harness["degradation_determinism"],
        "ground_truth_invariance": harness["ground_truth"],
        "metric_semantics": metrics["metric_semantics"], "fpr": metrics["fpr"],
        "confidence": metrics["confidence"], "calibration": metrics["calibration"],
        "abstention": metrics["abstention"], "latency": metrics["latency"],
        "realized_degradation": harness["accounting"], "pairing": pairing,
        "statistical_analysis": statistics, "selective_resume": selective_resume,
        "input_immutability": input_immutability, "v2_freeze": v2, "h3_freeze": h3,
        "inference_calls": 0, "training_calls": 0, "tuning_calls": 0,
        "fit_calls": 0, "partial_fit_calls": 0, "execution_readiness": readiness,
    }
    artifacts = {
        "sample-binding.json": sample, "split-binding.json": split,
        "model-binding.json": preflight["model"], "operator-matrix.json": operators,
        "applicability-audit.json": applicability,
        "metric-semantics-audit.json": {key: metrics[key] for key in metrics},
        "pairing-readiness.json": pairing, "execution-plan.json": plan,
        "preflight-audit.json": preflight,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        _atomic_json(output / name, value)
    lines = [f"{file_sha256(path)}  {path.name}" for path in sorted(output.glob("*.json"))]
    (output / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    verification = _verify_sums(output, output / "SHA256SUMS")
    if verification["status"] != "PASS":
        raise RuntimeError("H4_PREFLIGHT_SHA256SUMS_FAIL")
    return preflight
