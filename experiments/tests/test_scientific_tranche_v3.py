from __future__ import annotations

import copy
import random

import pytest

from experiments.final_campaigns_v3.scientific_tranche import (
    build_manifest, complement, selection_hash, validate_manifest,
)


def _fixture():
    systems = ("OB", "SS", "TT")
    qualities = ("CLEAN", "PARTIAL")
    modalities = (("metric",), ("log", "metric"), ("metric", "trace"),
                  ("log", "metric", "trace"))
    faults = ("cpu", "mem", "disk", "delay", "loss", "socket", "f1", "f3")
    manifests = []
    for index in range(735):
        quality = qualities[index % len(qualities)]
        manifests.append({
            "incident_id": f"incident-{index:04d}", "system": systems[index % 3],
            "integrity_status": quality, "modalities": modalities[index % 4],
            "fault_type": faults[index % len(faults)],
            "event_count_by_modality": {"metric": (index + 1) * 17},
        })
    splits = {"train": [x["incident_id"] for x in manifests[:470]],
              "validation": [x["incident_id"] for x in manifests[470:588]],
              "test": [x["incident_id"] for x in manifests[588:]]}
    return manifests, splits


def _build(manifests, splits):
    return build_manifest(
        parent_splits=splits, metadata=manifests, tranche_id="tranche-a-180",
        requested_counts={"train": 110, "validation": 30, "test": 40},
        protocol="within-dataset", seed=42, parent_base_key="b" * 64,
        parent_split_key="s" * 64)


def _validate(value, splits):
    return validate_manifest(value, parent_splits=splits, protocol="within-dataset",
                             seed=42, parent_base_key="b" * 64,
                             parent_split_key="s" * 64)


def test_selection_is_deterministic_order_independent_and_representative():
    manifests, splits = _fixture()
    shuffled = list(manifests)
    random.Random(987).shuffle(shuffled)
    first, second = _build(manifests, splits), _build(shuffled, splits)
    assert first == second
    assert first["selection_sha256"] == selection_hash(first)
    assert [len(first[f"{s}_incident_ids"]) for s in splits] == [110, 30, 40]
    assert _validate(first, splits)["NO_SPLIT_OVERLAP"] == "PASS"
    selected = set(first["incident_ids"])
    by_id = {item["incident_id"]: item for item in manifests}
    assert {by_id[x]["system"] for x in selected} == {"OB", "SS", "TT"}
    assert {by_id[x]["integrity_status"] for x in selected} == {"CLEAN", "PARTIAL"}
    assert len({tuple(by_id[x]["modalities"]) for x in selected}) == 4


@pytest.mark.parametrize("mutation", ("id", "split", "hash", "parent_base", "parent_split"))
def test_tampering_is_rejected(mutation):
    manifests, splits = _fixture()
    value = copy.deepcopy(_build(manifests, splits))
    if mutation == "id":
        value["train_incident_ids"][0] = "outside-parent"
    elif mutation == "split":
        value["train_incident_ids"][0] = value["test_incident_ids"][0]
    elif mutation == "hash":
        value["selection_sha256"] = "0" * 64
    elif mutation == "parent_base":
        value["parent_base_key"] = "0" * 64
    else:
        value["parent_split_key"] = "0" * 64
    with pytest.raises(ValueError):
        _validate(value, splits)


def test_complement_preserves_parent_splits_and_exact_union():
    manifests, splits = _fixture()
    value = _build(manifests, splits)
    rest = complement(splits, value)
    assert sum(map(len, rest.values())) == 555
    for split in splits:
        chosen = set(value[f"{split}_incident_ids"])
        assert chosen.isdisjoint(rest[split])
        assert chosen | set(rest[split]) == set(splits[split])
