from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from experiments.causalods_campaign_bindings import _H4ProvenanceExecutor
from experiments.final_campaigns_v3 import core as final
from experiments.final_campaigns_v3.preprocessing import (
    PREPROCESSING_VERSION, IncidentSource, _PlannerBoundGenerator, _forbidden_feature_hits,
    _materialize_base_dataset_memory_bounded, _supervise, validate_catalog,
)
from experiments.multimodal_v3 import CampaignStoreV3, DatasetCapabilityV3
from experiments.multimodal_v3.fixtures import smoke_observation
from multimodal_v1.benchmark_v3 import BenchmarkDataset
from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence import IncidentEvidenceAssembler
from multimodal_v1.event_graph import EventGraphBuilder
from multimodal_v1.features_v3 import (
    FeatureInputV3, IncidentDatasetGeneratorV3, RCAServiceTargetV3,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/configs/final-v3"
SPEC_HASH = "4860ee5fe2c49a5ff8cacf34d007c1041062a78aceab0646a259ae588b6356db"
V2_HASH = "f418d45f92d91f338092768c1ee9e2dc257047b422d98a24b0be65cae01379d8"
BASE = datetime(2026, 8, 16, tzinfo=timezone.utc)


def _campaign(family, **changes):
    payload = {
        "schema_version": "final-campaign-config-v3", "campaign_id": f"test-{family}",
        "family": family, "protocols": ["within-dataset"], "seeds": [42],
        "repetitions": 1, "execution_mode": "offline",
        "model_version": "rca-model-benchmark-v3-spec", "model_hash": SPEC_HASH,
        "test_fraction": .2, "validation_fraction": .2, "criteria": {},
    }
    payload.update(changes)
    return payload


def _caps(count=40, *, business=True):
    modalities = ("business_event", "log", "metric", "trace") if business else (
        "log", "metric", "trace")
    return tuple(DatasetCapabilityV3(
        "fixture", "system-a" if index % 2 else "system-b", f"incident-{index:03d}",
        modalities, hashlib.sha256(f"manifest-{index}".encode()).hexdigest(),
        fault_type="error",
    ) for index in range(count))


def _feature_input(number):
    correlation = f"workflow-{number}"
    trace = f"{number:032x}"[-32:]
    events = (
        UnifiedEvent(event_id=f"trace-{number}", event_name="request", event_type="trace",
                     signal_type="trace", event_timestamp=BASE + timedelta(seconds=number),
                     service_name="payment-service", correlation_id=correlation,
                     trace_id=trace, span_id=f"{number:016x}"[-16:],
                     source_reference=f"fixture:trace:{number}",
                     attributes={"duration_ms": float(number + 1)}),
        UnifiedEvent(event_id=f"metric-{number}", event_name="cpu", event_type="metric",
                     signal_type="metric", event_timestamp=BASE + timedelta(seconds=number + 1),
                     service_name="payment-service", correlation_id=correlation,
                     source_reference=f"fixture:metric:{number}",
                     metric_name="system.cpu.utilization", metric_value=float(number + 10)),
    )
    bundle = IncidentEvidenceAssembler().assemble(events)[0]
    bundle = replace(bundle, incident_id=f"incident-{number}")
    return FeatureInputV3(bundle, EventGraphBuilder().build(bundle))


class _MemoryBoundedFixtureLoader:
    def __init__(self, values):
        self.values = values
        self._last_event_fingerprints = []

    def events(self, source):
        self._last_event_fingerprints = []
        return self.values[source.incident_id]

    def event_fingerprints_aligned(self, events):
        return None


def _catalog_fixture(tmp_path):
    inputs = tuple(_feature_input(index) for index in range(5))
    targets = tuple(RCAServiceTargetV3(
        item.bundle.incident_id, "payment-service", True, "labeled", "fixture")
        for item in inputs)
    dataset = IncidentDatasetGeneratorV3(seed=42).generate(
        inputs, targets=targets, supervised=True, train_fraction=.6,
        validation_fraction=.2)
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(dataset.to_json() + "\n")
    benchmark = BenchmarkDataset.from_json(dataset_path)
    manifest = json.loads(dataset_path.read_text())["manifest"]
    splits = manifest["split_incident_ids"]
    sha_ids = lambda values: final.sha256_value(sorted(values))
    entry = {
        "source_dataset": "fixture", "source_system": "system-a",
        "feature_view": "FULL", "protocol": "within-dataset", "held_out": "fixture",
        "seed": 42, "path": str(dataset_path.relative_to(ROOT)) if ROOT in dataset_path.parents
        else str(dataset_path),
        "dataset_sha256": final.file_sha256(dataset_path),
        "feature_view_sha256": manifest["feature_view_sha256"],
        "split_sha256": final.sha256_value({name: sorted(splits[name])
                                             for name in ("train", "validation", "test")}),
        "train_incident_ids_sha256": sha_ids(splits["train"]),
        "validation_incident_ids_sha256": sha_ids(splits["validation"]),
        "test_incident_ids_sha256": sha_ids(splits["test"]),
        "feature_schema_sha256": final.sha256_value(dict(benchmark.feature_schema)),
        "preprocessing_version": PREPROCESSING_VERSION,
    }
    # Validator requires files below repository_root.  A tmp-specific root keeps
    # path containment semantics covered without touching project artifacts.
    entry["path"] = dataset_path.name
    catalog = tmp_path / "benchmark-catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "scientific-benchmark-catalog-v3",
        "model_specification_hash": SPEC_HASH, "datasets": [entry]}))
    return catalog, entry, dataset_path


def test_all_final_configs_bind_the_specification_not_v2():
    assert final.file_sha256(CONFIG / "scientific-model-specification.json") == SPEC_HASH
    for name in ("h1a", "h1b", "h1c", "h2", "h3", "h4"):
        payload = json.loads((CONFIG / f"{name}.json").read_text())
        assert payload["model_version"] == "rca-model-benchmark-v3-spec"
        assert payload["model_hash"] == SPEC_HASH
        assert payload["model_hash"] != V2_HASH


def test_catalog_contract_and_integrity(tmp_path):
    catalog, _, _ = _catalog_fixture(tmp_path)
    report = validate_catalog(catalog, repository_root=tmp_path,
                              expected_model_specification_hash=SPEC_HASH)
    assert report["valid"] and report["datasets_checked"] == 1


def test_memory_bounded_scientific_dataset_matches_legacy_multi_incident_split():
    inputs = tuple(_feature_input(index) for index in range(6))
    splits = {
        "train": ("incident-0", "incident-1", "incident-2"),
        "validation": ("incident-3",),
        "test": ("incident-4", "incident-5"),
    }
    roots = {item.bundle.incident_id: "payment-service" for item in inputs}
    legacy = _supervise(
        _PlannerBoundGenerator(splits, seed=42).generate(inputs),
        roots,
    )
    sources = {
        item.bundle.incident_id: IncidentSource(
            item.bundle.incident_id, "fixture", "system-a", Path("manifest.json"),
            hashlib.sha256(item.bundle.incident_id.encode()).hexdigest(),
            "payment-service", ("trace", "metric"),
        )
        for item in inputs
    }
    loader = _MemoryBoundedFixtureLoader({
        item.bundle.incident_id: item.bundle.events for item in inputs
    })
    bounded = _materialize_base_dataset_memory_bounded(
        loader=loader, sources=sources, splits=splits, seed=42)
    assert bounded.lifecycle.max_live == 1
    assert bounded.dataset.to_json() == legacy.to_json()
    assert bounded.dataset.manifest.baseline_json == legacy.manifest.baseline_json
    assert bounded.dataset.manifest.baseline_sha256 == legacy.manifest.baseline_sha256
    assert bounded.dataset.manifest.rows_sha256 == legacy.manifest.rows_sha256
    assert bounded.dataset.targets("train") == legacy.targets("train")


def test_catalog_duplicate_key_missing_file_and_hash_mismatch_rejected(tmp_path):
    catalog, entry, dataset = _catalog_fixture(tmp_path)
    payload = json.loads(catalog.read_text())
    payload["datasets"].append(dict(entry))
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicada"):
        validate_catalog(catalog, repository_root=tmp_path)
    payload["datasets"] = [{**entry, "path": "missing.json"}]
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="NOT_FOUND"):
        validate_catalog(catalog, repository_root=tmp_path)
    payload["datasets"] = [{**entry, "dataset_sha256": "0" * 64}]
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        validate_catalog(catalog, repository_root=tmp_path)
    assert dataset.is_file()


def test_catalog_feature_view_and_split_mismatch_rejected(tmp_path):
    catalog, entry, _ = _catalog_fixture(tmp_path)
    payload = json.loads(catalog.read_text())
    payload["datasets"] = [{**entry, "feature_view_sha256": "0" * 64}]
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="FEATURE_VIEW"):
        validate_catalog(catalog, repository_root=tmp_path)
    payload["datasets"] = [{**entry, "split_sha256": "0" * 64}]
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SPLIT_BINDING"):
        validate_catalog(catalog, repository_root=tmp_path)


def test_forbidden_feature_key_is_structurally_rejected():
    payload = {"rows": [{"vector": {"features": {
        "latency_mean": 1.0, "nested": {"root_cause_service": "payment"}}}}]}
    assert _forbidden_feature_hits(payload) == [
        "$.rows[0].vector.features.nested.root_cause_service"]


def test_pairing_reports_exact_factor_intersections():
    runs = final.build_plan(_campaign("H1-B"), _caps())[1]
    victim = next(run for run in runs if run.feature_view == "GRAPH_BUSINESS" and run.eligibility)
    reduced = tuple(run for run in runs if run.run_id != victim.run_id)
    report = final.audit_pairing({"H1-B": reduced})["campaigns"]["H1-B"]
    pair = next(item for item in report["comparisons"]
                if {item["left"], item["right"]} == {"GRAPH", "GRAPH_BUSINESS"})
    assert abs(pair["applicable_A"] - pair["applicable_B"]) == 1
    assert pair["only_A"] + pair["only_B"] == 1
    assert pair["paired_intersection"] == min(pair["applicable_A"], pair["applicable_B"])


def test_rcaeval_like_business_denominator_is_smaller_but_pairing_valid():
    mixed = _caps(20, business=True) + tuple(replace(item, incident_id=f"no-business-{i}",
        manifest_sha256=hashlib.sha256(f"no-business-{i}".encode()).hexdigest(),
        available_modalities=("log", "metric", "trace"))
        for i, item in enumerate(_caps(20, business=False)))
    runs = final.build_plan(_campaign("H1-B"), mixed)[1]
    report = final.audit_pairing({"H1-B": runs})["campaigns"]["H1-B"]
    assert report["valid"]
    assert report["applicable_by_factor"]["GRAPH_BUSINESS"] \
        < report["applicable_by_factor"]["GRAPH"]


def test_h3_missing_mode_and_h4_missing_severity_fail_pairing():
    h3 = final.build_plan(_campaign("H3", sample_size=40), _caps(50))[1]
    assert not final.audit_pairing({"H3": tuple(run for run in h3
        if not (run.incident_id == h3[0].incident_id and run.explanation_mode == "generic"))})["valid"]
    h4 = final.build_plan(_campaign("H4"), _caps())[1]
    victim = next(run for run in h4 if run.degradation_severity == .3 and run.eligibility)
    assert not final.audit_pairing({"H4": tuple(run for run in h4
        if run.run_id != victim.run_id)})["valid"]


def test_h4_restart_restores_zero_baseline_and_original_feature_hash(tmp_path):
    runs = final.build_plan(_campaign("H4"), _caps())[1]
    group = [run for run in runs if run.incident_id == runs[0].incident_id
             and run.degradation_operator == runs[0].degradation_operator]
    group = sorted(group, key=lambda run: run.degradation_severity)
    observation = smoke_observation()
    observations = {group[0].incident_id: observation}

    def evaluator(run, dataset):
        retained = len(dataset.events) / len(observation.events)
        return {"top_1": retained, "top_3": 1.0, "mrr": max(.5, retained),
                "fpr": 1 - retained, "confidence_values": (retained,),
                "outcomes": (retained >= .5,), "abstentions": 0,
                "processing_latency_ms": 1.0, "predicted_ranking": (),
                "feature_hash": final.sha256_value(dataset.observation_payload())}

    store = tmp_path / group[0].configuration_hash
    first = _H4ProvenanceExecutor(
        observations, evaluator, feature_hasher=lambda run, value:
        final.sha256_value(value.observation_payload()), result_store_path=store)
    executed = {group[0].run_id: (group[0], *first.execute(group[0]))}
    for run in group[1:]:
        executed[run.run_id] = (run, *first.execute(run))
    run_root = store / "runs"
    run_root.mkdir(parents=True)
    index = {}
    for run, result, provenance, evaluation in executed.values():
        path = run_root / f"{run.run_id}.json"
        path.write_text(json.dumps({
            "identity": {"run_id": run.run_id},
            "input": {"source_manifest_hash": run.source_manifest_hash,
                      "model_hash": run.model_hash},
            "result": result,
            "provenance": {**provenance,
                           "model_specification_hash": run.model_hash,
                           "trained_model_artifact_hash": "b" * 64},
            "evaluation": evaluation, "status": {"state": "SUCCESS"},
        }))
        index[run.run_id] = final.file_sha256(path)
    (run_root / "index.json").write_text(json.dumps(index))
    corrupt = next(run_root / f"{run.run_id}.json" for run in group
                   if run.degradation_severity == .3)
    corrupt.write_text(corrupt.read_text() + " ")
    fake_store = CampaignStoreV3(tmp_path, group[0].configuration_hash)
    recovery = final.prepare_selective_resume(fake_store, group)
    assert recovery == {"reused": 3, "reexecute": 1, "quarantined": 1}
    restarted = _H4ProvenanceExecutor(
        observations, evaluator, feature_hasher=lambda run, value:
        final.sha256_value(value.observation_payload()), result_store_path=store)
    result, provenance, _ = restarted.execute(next(run for run in group
                                                    if run.degradation_severity == .3))
    assert result and provenance["original_feature_hash"]
    assert "H4_ORIGINAL_FEATURE_HASH_MISSING" not in json.dumps(result)


def test_h3_and_h4_providers_fail_closed_before_sequential_dependencies(tmp_path):
    from experiments.final_campaigns_v3.providers import load_h3_inputs, load_h4_inputs
    config = final.build_experiment_config(_campaign("H3", sample_size=40,
        criteria={"scientific_rca_results": "missing/INDEX.json"}), _caps(50))
    with pytest.raises(RuntimeError, match="H3_SCIENTIFIC_RCA_RESULTS_NOT_AVAILABLE"):
        load_h3_inputs(config=config, repository_root=tmp_path)
    h4 = final.build_experiment_config(_campaign("H4", criteria={
        "scientific_benchmark_catalog": "missing/catalog.json"}), _caps())
    with pytest.raises(RuntimeError, match="SCIENTIFIC_BENCHMARK_CATALOG_NOT_FOUND"):
        load_h4_inputs(config=h4, repository_root=tmp_path)
