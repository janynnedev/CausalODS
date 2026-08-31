from __future__ import annotations

from dataclasses import replace
import json

import pytest

import experiments.final_campaigns_v3.preprocessing as preprocessing
from experiments.multimodal_v3.core import sha256_value
from multimodal_v1.benchmark_v3 import BenchmarkDataset
from multimodal_v1.features_v3 import (
    DatasetRowV3, IncidentDatasetGeneratorV3, RCAFeatureDatasetV3, RCAServiceTargetV3,
)
from multimodal_v1.features_v3.dataset import (
    _sha as legacy_dataset_sha256, baseline_binding_sha256,
)


def _exact_dataset():
    from experiments.tests.test_final_campaign_hardening_v3 import _feature_input
    inputs = tuple(_feature_input(index) for index in range(8))
    targets = tuple(RCAServiceTargetV3(
        item.bundle.incident_id, "payment-service", True, "labeled", "fixture")
        for item in inputs)
    return IncidentDatasetGeneratorV3(seed=42).generate(
        inputs, targets=targets, supervised=True, train_fraction=.5,
        validation_fraction=.25)


def _frozen_dataset(*, unicode_baseline=False):
    exact = _exact_dataset()
    parent_ids = tuple(sorted((*exact.baselines.training_incident_ids,
                               "parent-extra-1", "parent-extra-2")))
    baseline = replace(exact.baselines, training_incident_ids=parent_ids)
    if unicode_baseline:
        baseline = replace(baseline, log_templates={
            **dict(baseline.log_templates),
            "serviço-pagamento": ("falha crítica",),
        })
    splits = exact.split_incident_ids
    provenance = {
        "official_parent_baseline_sha256": sha256_value(json.loads(baseline.to_json())),
        "parent_base_key": "1" * 64,
        "parent_split_sha256": "2" * 64,
        "scientific_tranche_id": "fixture-tranche",
        "scientific_tranche_sha256": "3" * 64,
        "parent_training_incident_ids_sha256": sha256_value(sorted(parent_ids)),
        "parent_training_incident_count": len(parent_ids),
    }
    provenance["baseline_binding_sha256"] = baseline_binding_sha256(provenance, splits)
    reference = {
        "schema_version": "frozen-parent-baseline-reference-v1",
        "path": "artifacts/fixture/baseline/snapshot.json",
        "baseline_sha256": provenance["official_parent_baseline_sha256"],
        "parent_base_key": provenance["parent_base_key"],
        "parent_split_sha256": provenance["parent_split_sha256"],
        "parent_training_incident_ids_sha256": provenance[
            "parent_training_incident_ids_sha256"],
        "parent_training_incident_count": provenance["parent_training_incident_count"],
        "preprocessing_version": preprocessing.PREPROCESSING_VERSION,
    }
    total = sum(len(values) for values in splits.values())
    fractions = {name: len(values) / total for name, values in splits.items()}
    return RCAFeatureDatasetV3(
        exact.rows, exact.feature_view, baseline, splits, exact.seed, fractions,
        exact.policy_provenance, exact.supervised, "frozen-parent-train", provenance,
        reference, parent_ids)


def test_frozen_parent_contract_serialization_ablation_and_benchmark(tmp_path):
    dataset = _frozen_dataset()
    assert set(dataset.split_incident_ids["train"]) < set(
        dataset.baselines.training_incident_ids)
    assert not (set(dataset.split_incident_ids["validation"])
                & set(dataset.baselines.training_incident_ids))
    assert not (set(dataset.split_incident_ids["test"])
                & set(dataset.baselines.training_incident_ids))
    manifest = dataset.manifest.to_dict()
    assert manifest["baseline_binding_policy"] == "frozen-parent-train"
    assert manifest["baseline_json"] is None
    assert manifest["baseline_reference"] == dict(dataset.baseline_reference)
    assert manifest["baseline_binding_provenance"] == dict(
        dataset.baseline_binding_provenance)
    ablated = dataset.ablate("business")
    assert ablated.baseline_binding_policy == dataset.baseline_binding_policy
    assert dict(ablated.baseline_binding_provenance) == dict(
        dataset.baseline_binding_provenance)
    assert dict(ablated.baseline_reference) == dict(dataset.baseline_reference)
    path = tmp_path / "dataset.json"
    path.write_text(dataset.to_json())
    benchmark = BenchmarkDataset.from_json(path)
    assert benchmark.feature_schema["baseline_binding_policy"] == "frozen-parent-train"
    assert benchmark.feature_schema["baseline_sha256"] == manifest["baseline_sha256"]


def test_unicode_baseline_uses_frozen_scientific_canonicalization(tmp_path):
    dataset = _frozen_dataset(unicode_baseline=True)
    baseline_value = json.loads(dataset.baselines.to_json())
    core_sha = sha256_value(baseline_value)
    before_fix_sha = legacy_dataset_sha256(baseline_value)
    assert before_fix_sha != core_sha
    assert dataset.manifest.baseline_sha256 == core_sha
    assert dataset.baseline_binding_provenance[
        "official_parent_baseline_sha256"] == core_sha
    path = tmp_path / "unicode-dataset.json"
    path.write_text(dataset.to_json())
    assert BenchmarkDataset.from_json(path).feature_schema["baseline_sha256"] == core_sha


def test_exact_policy_keeps_legacy_unicode_dataset_hash_compatibility(tmp_path):
    exact = _exact_dataset()
    baseline = replace(exact.baselines, log_templates={
        **dict(exact.baselines.log_templates), "serviço": ("falha crítica",)})
    dataset = RCAFeatureDatasetV3(
        exact.rows, exact.feature_view, baseline, exact.split_incident_ids,
        exact.seed, exact.split_fractions, exact.policy_provenance, True)
    baseline_value = json.loads(baseline.to_json())
    assert dataset.manifest.baseline_sha256 == legacy_dataset_sha256(baseline_value)
    path = tmp_path / "exact-unicode.json"
    path.write_text(dataset.to_json())
    BenchmarkDataset.from_json(path)


def test_split_train_exact_default_remains_strict():
    exact = _exact_dataset()
    bad = replace(exact.baselines, training_incident_ids=(
        *exact.baselines.training_incident_ids, "outside"))
    with pytest.raises(ValueError, match="exatamente"):
        RCAFeatureDatasetV3(
            exact.rows, exact.feature_view, bad, exact.split_incident_ids,
            exact.seed, exact.split_fractions, exact.policy_provenance, exact.supervised)


@pytest.mark.parametrize("mutation", (
    "train_outside", "validation_overlap", "test_overlap", "baseline_sha",
    "parent_split", "tranche_sha", "missing", "fractions",
))
def test_frozen_parent_contract_rejects_invalid_bindings(mutation):
    dataset = _frozen_dataset()
    splits = {name: tuple(values) for name, values in dataset.split_incident_ids.items()}
    provenance = dict(dataset.baseline_binding_provenance)
    fractions = dict(dataset.split_fractions)
    if mutation == "train_outside":
        splits["train"] = (*splits["train"][:-1], "not-in-parent")
    elif mutation == "validation_overlap":
        splits["validation"] = (*splits["validation"][:-1], splits["train"][0])
    elif mutation == "test_overlap":
        splits["test"] = (*splits["test"][:-1], splits["train"][0])
    elif mutation == "baseline_sha":
        provenance["official_parent_baseline_sha256"] = "9" * 64
    elif mutation == "parent_split":
        provenance["parent_split_sha256"] = "9" * 64
    elif mutation == "tranche_sha":
        provenance["scientific_tranche_sha256"] = "9" * 64
    elif mutation == "missing":
        provenance.pop("parent_base_key")
    else:
        fractions = {"train": .7, "validation": .15, "test": .15}
    # Deliberately do not recompute the immutable binding after tampering.
    with pytest.raises(ValueError):
        RCAFeatureDatasetV3(
            dataset.rows, dataset.feature_view, dataset.baselines, splits,
            dataset.seed, fractions, dataset.policy_provenance, True,
            "frozen-parent-train", provenance, dataset.baseline_reference,
            dataset.baseline_parent_training_incident_ids)


def test_tranche_finalization_reuses_all_checkpoints_without_workers(tmp_path, monkeypatch):
    dataset = _frozen_dataset()
    by_incident = {}
    policy = dataset.policy_provenance
    for incident_id in sorted({row.vector.incident_id for row in dataset.rows}):
        by_incident[preprocessing._checkpoint_name(incident_id)] = {
            "status": "COMPLETED", "incident_id": incident_id,
            "rows": [row.to_dict() for row in dataset.rows
                     if row.vector.incident_id == incident_id],
            "graph_fingerprint": policy.graph_fingerprints[incident_id],
            "topology_fingerprint": policy.topology_fingerprints[incident_id],
            "relation_rule_versions": {
                name: list(values) for name, values in
                policy.relation_rule_versions_by_incident[incident_id].items()},
            "graph_policy_provenance": dict(
                policy.graph_policy_provenance_by_incident[incident_id]),
            "pruning_policy_version": policy.pruning_policy_versions[incident_id],
            "pruning_fingerprint": policy.pruning_fingerprints[incident_id],
            "worker": {"fixture": True},
        }
    baseline_json = dataset.baselines.to_json()
    baseline_sha = sha256_value(json.loads(baseline_json))
    snapshot = tmp_path / "parent-baseline.json"
    snapshot.write_text(json.dumps({
        "base_key": "1" * 64, "split_sha256": "2" * 64,
        "training_incident_ids": list(dataset.baselines.training_incident_ids),
        "baseline_json": baseline_json, "baseline_sha256": baseline_sha,
        "status": "COMPLETED",
        "preprocessing_version": preprocessing.PREPROCESSING_VERSION}))
    monkeypatch.setattr(preprocessing, "_checkpoint_bindings", lambda **_kwargs: {})
    monkeypatch.setattr(
        preprocessing, "_load_valid_incident_checkpoint",
        lambda path, _bindings: by_incident.get(path.stem))
    monkeypatch.setattr(
        preprocessing, "_run_incident_worker",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("all valid checkpoints must be HIT")))
    loader = type("Loader", (), {"sources": {
        incident: object() for incident in dataset.split_incident_ids["train"]
        + dataset.split_incident_ids["validation"] + dataset.split_incident_ids["test"]}})()
    registry = type("Registry", (), {"repository_root": tmp_path})()
    built = preprocessing._materialize_base_dataset_crash_safe(
        loader=loader, registry=registry, rcaeval_cache=None,
        splits=dataset.split_incident_ids, seed=dataset.seed,
        base_key="tranche-work", split_key="tranche-selection",
        work_root=tmp_path / "work", resume=True,
        official_baseline_snapshot_path=snapshot,
        scientific_tranche_binding={
            "parent_base_key": "1" * 64, "parent_split_key": "2" * 64,
            "tranche_id": "fixture-tranche", "selection_sha256": "3" * 64,
            "official_parent_baseline_sha256": baseline_sha})
    assert built.checkpoint_report["checkpoint_hits"] == len(by_incident)
    assert built.checkpoint_report["checkpoint_recomputed"] == 0
    assert built.dataset.baseline_binding_policy == "frozen-parent-train"
    assert built.dataset.baselines is None
    assert len(built.dataset.baseline_parent_training_incident_ids) > len(
        built.dataset.split_incident_ids["train"])


def test_tranche_catalog_preserves_and_validates_baseline_provenance(tmp_path):
    dataset = _frozen_dataset()
    reference_path = tmp_path / dataset.baseline_reference["path"]
    reference_path.parent.mkdir(parents=True)
    reference_path.write_text(json.dumps({
        "status": "COMPLETED",
        "preprocessing_version": preprocessing.PREPROCESSING_VERSION,
        "baseline_sha256": dataset.baseline_reference["baseline_sha256"],
        "base_key": dataset.baseline_reference["parent_base_key"],
        "split_sha256": dataset.baseline_reference["parent_split_sha256"],
        "training_incident_ids": list(
            dataset.baseline_parent_training_incident_ids),
    }))
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(dataset.to_json())
    manifest = dataset.manifest.to_dict()
    provenance = manifest["baseline_binding_provenance"]
    splits = manifest["split_incident_ids"]
    entry = {
        "source_dataset": "fixture", "source_system": "system-a",
        "feature_view": "FULL", "protocol": "within-dataset", "held_out": None,
        "seed": 42, "path": "dataset.json",
        "dataset_sha256": preprocessing.file_sha256(dataset_path),
        "benchmark_dataset_sha256": dataset.dataset_sha256,
        "feature_view_sha256": manifest["feature_view_sha256"],
        "split_sha256": sha256_value({name: sorted(splits[name]) for name in splits}),
        "train_incident_ids_sha256": sha256_value(sorted(splits["train"])),
        "validation_incident_ids_sha256": sha256_value(sorted(splits["validation"])),
        "test_incident_ids_sha256": sha256_value(sorted(splits["test"])),
        "feature_schema_sha256": sha256_value(dict(
            BenchmarkDataset.from_json(dataset_path).feature_schema)),
        "preprocessing_version": preprocessing.PREPROCESSING_VERSION,
        "cache_key": "fixture", "scientific_tranche_id": provenance["scientific_tranche_id"],
        "scientific_tranche_sha256": provenance["scientific_tranche_sha256"],
        "parent_split_sha256": provenance["parent_split_sha256"],
        "parent_base_key": provenance["parent_base_key"],
    }
    catalog = tmp_path / "benchmark-catalog.json"
    catalog_payload = {
        "schema_version": preprocessing.CATALOG_VERSION,
        "preprocessing_version": preprocessing.PREPROCESSING_VERSION,
        "model_specification_hash": "a" * 64, "datasets": [entry],
        "scientific_tranche_id": provenance["scientific_tranche_id"],
        "scientific_tranche_sha256": provenance["scientific_tranche_sha256"],
        "parent_split_sha256": provenance["parent_split_sha256"],
        "parent_base_key": provenance["parent_base_key"],
    }
    catalog.write_text(json.dumps(catalog_payload))
    effective = preprocessing.effective_split_sha256(splits)
    assert provenance["scientific_tranche_sha256"] != effective
    assert entry["split_sha256"] == effective
    assert entry["scientific_tranche_sha256"] == provenance[
        "scientific_tranche_sha256"]
    assert entry["parent_split_sha256"] == provenance["parent_split_sha256"]
    assert preprocessing.validate_catalog(
        catalog, repository_root=tmp_path,
        expected_model_specification_hash="a" * 64)["valid"]
    mutations = (
        ("split_sha256", "9" * 64, "SPLIT_BINDING_FAIL"),
        ("scientific_tranche_sha256", "8" * 64, "CATALOG_BINDING_MISMATCH"),
        ("parent_split_sha256", "7" * 64, "CATALOG_BINDING_MISMATCH"),
    )
    for field, value, message in mutations:
        mutated_entry = {**entry, field: value}
        mutated = {**catalog_payload, "datasets": [mutated_entry]}
        catalog.write_text(json.dumps(mutated))
        with pytest.raises(ValueError, match=message):
            preprocessing.validate_catalog(catalog, repository_root=tmp_path)


def test_frozen_parent_large_baseline_is_not_duplicated_in_views():
    dataset = _frozen_dataset()
    marker = "BASELINE_PAYLOAD_MUST_NOT_APPEAR_" + ("x" * 1_000_000)
    baseline = replace(dataset.baselines, log_templates={
        **dict(dataset.baselines.log_templates), "large-fixture": (marker,)})
    provenance = dict(dataset.baseline_binding_provenance)
    provenance["official_parent_baseline_sha256"] = sha256_value(
        json.loads(baseline.to_json()))
    reference = dict(dataset.baseline_reference)
    reference["baseline_sha256"] = provenance["official_parent_baseline_sha256"]
    provenance["baseline_binding_sha256"] = baseline_binding_sha256(
        provenance, dataset.split_incident_ids)
    compact = RCAFeatureDatasetV3(
        dataset.rows, dataset.feature_view, baseline, dataset.split_incident_ids,
        dataset.seed, dataset.split_fractions, dataset.policy_provenance, True,
        "frozen-parent-train", provenance, reference,
        dataset.baseline_parent_training_incident_ids)
    for view in (compact, compact.ablate("business"), compact.ablate("graph")):
        serialized = view.to_json()
        assert marker not in serialized
        assert len(serialized) < len(baseline.to_json())
        assert view.manifest.baseline_json is None
        assert dict(view.baseline_reference) == reference
