from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.final_campaigns_v3 import core as final
import experiments.final_campaigns_v3.cli as final_cli
from experiments.final_campaigns_v3.cli import (
    bind_runs_to_scientific_tranche, validate_scientific_tranche_parent_binding,
)
from experiments.final_campaigns_v3.providers import trained_artifact_index_path
from experiments.final_campaigns_v3.scientific_tranche import selection_hash
from experiments.final_campaigns_v3.preprocessing import effective_split_sha256
from experiments.multimodal_v3.executors import _validate_benchmark_split
from experiments.multimodal_v3.core import StageFailureV3


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/configs/final-v3"
TRANCHE = json.loads((CONFIG / "scientific-tranche-a-180.json").read_text())


def test_tranche_selection_effective_split_and_parent_split_are_distinct():
    splits = {name: TRANCHE[f"{name}_incident_ids"]
              for name in ("train", "validation", "test")}
    effective = effective_split_sha256(splits)
    assert effective == "e628fd0d91259dc69c1aacc6ee1277de15c9eebe393bffde84f92dc1f5f20f14"
    assert len({effective, TRANCHE["selection_sha256"],
                TRANCHE["parent_split_key"]}) == 3


def _real_parent_and_runs(family="H2"):
    registry = final.FrozenDatasetRegistry.load(CONFIG / "datasets.json", ROOT)
    payload = final.load_campaign_config(CONFIG / ({"H2": "h2.json", "H4": "h4.json"}[family]))
    capabilities = tuple(item for item in registry.capabilities()
                         if item.source_dataset == "rcaeval")
    _, runs = final.build_plan(payload, capabilities)
    parent = next(run for run in runs if (
        run.protocol == "within-dataset" and run.seed == 42
        and final.sha256_value({
            "train": sorted(run.train_incident_ids),
            "validation": sorted(run.validation_incident_ids),
            "test": sorted(run.test_incident_ids),
        }) == TRANCHE["parent_split_key"]))
    return registry, parent, runs


def test_real_tranche_runs_are_test_only_and_bound_to_manifest_splits():
    _, _, runs = _real_parent_and_runs()
    bound = bind_runs_to_scientific_tranche(runs, TRANCHE)
    assert bound
    assert {run.incident_id for run in bound} <= set(TRANCHE["test_incident_ids"])
    assert all(len(run.train_incident_ids) == 110 for run in bound)
    assert all(len(run.validation_incident_ids) == 30 for run in bound)
    assert all(len(run.test_incident_ids) == 40 for run in bound)
    assert all(run.train_incident_ids == tuple(TRANCHE["train_incident_ids"]) for run in bound)
    assert all(run.validation_incident_ids == tuple(TRANCHE["validation_incident_ids"])
               for run in bound)
    assert all(run.test_incident_ids == tuple(TRANCHE["test_incident_ids"]) for run in bound)


def test_tranche_binding_rejects_protocol_seed_source_and_system_leaks():
    _, _, runs = _real_parent_and_runs()
    valid = next(run for run in runs if run.protocol == "within-dataset"
                 and run.seed == 42 and run.held_out is None
                 and run.incident_id in set(TRANCHE["test_incident_ids"]))
    contaminated = (
        valid,
        replace(valid, protocol="cross-system", held_out=valid.source_system, run_id=""),
        replace(valid, protocol="cross-source", held_out="rcaeval", run_id=""),
        replace(valid, protocol="leave-one-system-out", held_out=valid.source_system,
                run_id=""),
        replace(valid, seed=7, run_id=""),
        replace(valid, source_dataset="causalods-local-v3", run_id=""),
        replace(valid, source_system="WRONG", run_id=""),
    )
    bound = bind_runs_to_scientific_tranche(contaminated, TRANCHE)
    assert len(bound) == 1
    assert bound[0].protocol == "within-dataset" and bound[0].seed == 42
    assert bound[0].held_out is None
    assert tuple(map(len, (bound[0].train_incident_ids,
                           bound[0].validation_incident_ids,
                           bound[0].test_incident_ids))) == (110, 30, 40)


def test_all_final_non_h3_tranche_runs_match_real_catalog():
    registry, _, _ = _real_parent_and_runs()
    catalog = json.loads((ROOT / "artifacts/final-experiments-v3"
                          / "scientific-inputs-tranche-a-180"
                          / "benchmark-catalog.json").read_text())
    catalog_keys = {(item["protocol"], item["seed"], item["held_out"],
                     item["feature_view"], item["source_dataset"],
                     item["source_system"]) for item in catalog["datasets"]}
    expected_counts = {"h1a": 160, "h1b": 80, "h1c": 124,
                       "h2": 280, "h4": 2240}
    for family, expected in expected_counts.items():
        payload = final.load_campaign_config(CONFIG / f"{family}.json")
        _, runs = final.build_plan(payload, registry.capabilities())
        bound = bind_runs_to_scientific_tranche(
            runs, TRANCHE, catalog_entries=catalog["datasets"])
        assert len(bound) == expected
        assert all((run.protocol, run.seed, run.held_out, run.feature_view,
                    run.source_dataset, run.source_system) in catalog_keys for run in bound)


def test_actual_benchmark_split_validator_accepts_tranche_and_rejects_parent(tmp_path):
    from experiments.tests.test_final_campaign_hardening_v3 import _catalog_fixture
    from multimodal_v1.benchmark_v3 import BenchmarkDataset

    _, parent, runs = _real_parent_and_runs()
    _, _, dataset_path = _catalog_fixture(tmp_path)
    dataset = BenchmarkDataset.from_json(dataset_path)
    raw_manifest = json.loads(dataset_path.read_text())["manifest"]
    splits = raw_manifest["split_incident_ids"]
    # Bind a real ExperimentRunV3 to the splits of an actually generated and
    # deserialized BenchmarkDataset, then exercise the production validator.
    run = replace(
        runs[0], incident_id=next(iter(splits["test"])),
        train_incident_ids=tuple(splits["train"]),
        validation_incident_ids=tuple(splits["validation"]),
        test_incident_ids=tuple(splits["test"]), run_id="")
    benchmark_manifest = {"splits": splits,
                          "benchmark_dataset_sha256": dataset.dataset_hash}
    _validate_benchmark_split(run, benchmark_manifest)
    parent_for_same_incident = replace(
        run, train_incident_ids=parent.train_incident_ids,
        validation_incident_ids=parent.validation_incident_ids,
        test_incident_ids=parent.test_incident_ids, run_id="")
    with pytest.raises(StageFailureV3) as error:
        _validate_benchmark_split(parent_for_same_incident, benchmark_manifest)
    assert error.value.stage == "benchmark_split"


def test_parent_base_and_split_are_independently_recomputed():
    registry, parent, _ = _real_parent_and_runs()
    assert validate_scientific_tranche_parent_binding(
        tranche=TRANCHE, parent_run=parent, registry=registry)
    for field in ("parent_base_key", "parent_split_key"):
        tampered = dict(TRANCHE)
        tampered[field] = "0" * 64
        tampered["selection_sha256"] = selection_hash(tampered)
        with pytest.raises(ValueError):
            validate_scientific_tranche_parent_binding(
                tranche=tampered, parent_run=parent, registry=registry)


def test_parent_binding_rejects_scientifically_relevant_registry_drift(monkeypatch):
    registry, parent, _ = _real_parent_and_runs()
    original = registry.snapshot

    def drifted_snapshot():
        payload = original()
        payload["datasets"][0]["checksum_manifest_sha256"] = "0" * 64
        return payload

    monkeypatch.setattr(registry, "snapshot", drifted_snapshot)
    with pytest.raises(ValueError, match="REGISTRY_IDENTITY_MISMATCH"):
        validate_scientific_tranche_parent_binding(
            tranche=TRANCHE, parent_run=parent, registry=registry)


def test_parent_binding_rejects_participating_manifest_drift(monkeypatch):
    registry, parent, _ = _real_parent_and_runs()
    original_loader = final_cli.FrozenEvidenceLoader

    class DriftedLoader:
        def __init__(self, registry, rcaeval_cache=None):
            sources = dict(original_loader(registry, rcaeval_cache=rcaeval_cache).sources)
            incident_id = parent.train_incident_ids[0]
            sources[incident_id] = replace(
                sources[incident_id], manifest_sha256="0" * 64)
            self.sources = sources

    monkeypatch.setattr(final_cli, "FrozenEvidenceLoader", DriftedLoader)
    with pytest.raises(ValueError, match="PARENT_BASE_KEY_MISMATCH"):
        validate_scientific_tranche_parent_binding(
            tranche=TRANCHE, parent_run=parent, registry=registry)


def _successful_promotion_fixture(tmp_path, run):
    artifact = tmp_path / "model"
    artifact.mkdir()
    result = {"identity": {"run_id": run.run_id}, "status": {"state": "SUCCESS"},
              "provenance": {"trained_model_artifact_hash": "a" * 64,
                             "serialized_artifact_path": str(artifact)}}
    return result


def test_tranche_promotion_is_isolated_and_full_default_is_unchanged(tmp_path):
    _, _, runs = _real_parent_and_runs()
    learned = next(run for run in bind_runs_to_scientific_tranche(runs, TRANCHE)
                   if run.method == "xgboost-rca-v3")
    result = _successful_promotion_fixture(tmp_path, learned)
    promoted = final.promote_trained_artifacts(
        tmp_path, [result], {learned.run_id: learned}, scientific_tranche=TRANCHE)
    tranche_index = (tmp_path / "artifacts/final-experiments-v3"
                     / "trained-artifacts-tranche-a-180/INDEX.json")
    full_index = tmp_path / "artifacts/final-experiments-v3/trained-artifacts/INDEX.json"
    assert Path(promoted["path"]) == tranche_index
    assert tranche_index.is_file() and not full_index.exists()
    payload = json.loads(tranche_index.read_text())
    assert payload["scientific_tranche_sha256"] == TRANCHE["selection_sha256"]

    full_root = tmp_path / "full"
    full_artifact = full_root / "model"
    full_artifact.mkdir(parents=True)
    full_result = {**result, "provenance": {**result["provenance"],
                                             "serialized_artifact_path": str(full_artifact)}}
    full = final.promote_trained_artifacts(full_root, [full_result], {learned.run_id: learned})
    assert Path(full["path"]) == (full_root / "artifacts/final-experiments-v3"
                                  / "trained-artifacts/INDEX.json")


def test_h4_uses_explicit_tranche_index_and_full_keeps_default(tmp_path):
    tranche = SimpleNamespace(criteria={
        "scientific_trained_artifact_index":
            "artifacts/final-experiments-v3/trained-artifacts-tranche-a-180/INDEX.json"})
    full = SimpleNamespace(criteria={})
    assert trained_artifact_index_path(config=tranche, repository_root=tmp_path) == (
        tmp_path / "artifacts/final-experiments-v3"
        / "trained-artifacts-tranche-a-180/INDEX.json")
    assert trained_artifact_index_path(config=full, repository_root=tmp_path) == (
        tmp_path / "artifacts/final-experiments-v3/trained-artifacts/INDEX.json")
