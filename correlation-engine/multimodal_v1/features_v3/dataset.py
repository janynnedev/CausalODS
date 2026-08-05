from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

from multimodal_v1.evidence.contracts import IncidentEvidenceBundle
from multimodal_v1.canonical import canonical_json_value, deep_freeze_json
from multimodal_v1.event_graph.contracts import EventEvidenceGraph, PruningResult
from multimodal_v1.features_v3.baselines import BaselineSnapshotV3, TrainingBaselineEstimatorV3
from multimodal_v1.features_v3.contracts import FeatureGroup, RCAFeatureVectorViewV3
from multimodal_v1.features_v3.extractor import RCAFeatureExtractorV3
from multimodal_v1.features_v3.registry import FeatureRegistryV3, FeatureViewV3


def _json(value: object) -> str:
    return json.dumps(canonical_json_value(value), sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class LabelStatus(str, Enum):
    LABELED = "labeled"
    UNKNOWN = "unknown"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class RCAServiceTargetV3:
    incident_id: str
    service_name: str
    is_root_cause: bool | None
    label_status: LabelStatus | str
    source: str
    root_cause_rank: int | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "label_status", LabelStatus(self.label_status))
        for name in ("incident_id", "service_name", "source"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} deve ser texto não vazio.")
        if self.label_status is LabelStatus.LABELED and not isinstance(self.is_root_cause, bool):
            raise ValueError("Target labeled requer is_root_cause booleano explícito.")
        if self.label_status is not LabelStatus.LABELED and self.is_root_cause is not None:
            raise ValueError("Target unknown/excluded deve usar is_root_cause=None.")
        if self.root_cause_rank is not None and (
            isinstance(self.root_cause_rank, bool) or not isinstance(self.root_cause_rank, int)
            or self.root_cause_rank < 1
        ):
            raise ValueError("root_cause_rank deve ser inteiro positivo.")
        if self.root_cause_rank is not None and not (
            self.label_status is LabelStatus.LABELED and self.is_root_cause is True
        ):
            raise ValueError("root_cause_rank existe somente para causa raiz labeled positiva.")
        if self.confidence is not None and (
            isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence deve ser finita entre 0 e 1.")

    def to_dict(self) -> dict[str, object]:
        return {"incident_id": self.incident_id, "service_name": self.service_name,
                "is_root_cause": self.is_root_cause, "label_status": self.label_status.value,
                "root_cause_rank": self.root_cause_rank, "source": self.source,
                "confidence": self.confidence}


@dataclass(frozen=True)
class FeatureInputV3:
    bundle: IncidentEvidenceBundle
    graph: EventEvidenceGraph
    pruning: PruningResult | None = None
    topology: Mapping[str, tuple[str, ...]] | None = None

    @property
    def topology_fingerprint(self) -> str:
        if self.topology is None:
            return _sha(None)
        normalized = {
            source: sorted(set(targets))
            for source, targets in sorted(self.topology.items())
        }
        return _sha(normalized)


@dataclass(frozen=True)
class DatasetRowV3:
    split: str
    vector: RCAFeatureVectorViewV3
    target: RCAServiceTargetV3 | None = None

    def __post_init__(self) -> None:
        if self.split not in {"train", "validation", "test"}:
            raise ValueError("DatasetRowV3.split inválido.")
        if not isinstance(self.vector, RCAFeatureVectorViewV3):
            raise TypeError("DatasetRowV3.vector deve ser RCAFeatureVectorViewV3.")
        if self.target is not None and not isinstance(self.target, RCAServiceTargetV3):
            raise TypeError("DatasetRowV3.target deve ser RCAServiceTargetV3 ou None.")

    def to_dict(self) -> dict[str, object]:
        return {"split": self.split, "vector": self.vector.to_dict(),
                "target": self.target.to_dict() if self.target else None}


@dataclass(frozen=True)
class DatasetManifestV3:
    dataset_schema_version: str
    extractor_version: str
    registry_json: str
    registry_sha256: str
    feature_fingerprint: str
    feature_view_json: str
    feature_view_sha256: str
    feature_names: tuple[str, ...]
    column_count: int
    base_schema_version: str
    baseline_json: str
    baseline_sha256: str
    seed: int
    split_algorithm: str
    split_fractions: Mapping[str, float]
    split_incident_ids: Mapping[str, tuple[str, ...]]
    split_sha256: Mapping[str, str]
    incident_count: int
    row_count: int
    label_distribution: Mapping[str, int]
    ablation_id: str
    policy_provenance: "PolicyProvenanceV3"
    rows_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_schema_version": self.dataset_schema_version,
            "extractor_version": self.extractor_version,
            "registry_json": self.registry_json, "registry_sha256": self.registry_sha256,
            "feature_fingerprint": self.feature_fingerprint,
            "feature_view_json": self.feature_view_json,
            "feature_view_sha256": self.feature_view_sha256,
            "feature_names": list(self.feature_names), "column_count": self.column_count,
            "base_schema_version": self.base_schema_version,
            "baseline_json": self.baseline_json, "baseline_sha256": self.baseline_sha256,
            "seed": self.seed, "split_algorithm": self.split_algorithm,
            "split_fractions": dict(self.split_fractions),
            "split_incident_ids": {key: list(value) for key, value in self.split_incident_ids.items()},
            "split_sha256": dict(self.split_sha256), "incident_count": self.incident_count,
            "row_count": self.row_count, "label_distribution": dict(self.label_distribution),
            "ablation_id": self.ablation_id,
            "policy_provenance": self.policy_provenance.to_dict(),
            "rows_sha256": self.rows_sha256,
        }


@dataclass(frozen=True)
class PolicyProvenanceV3:
    relation_rule_versions: Mapping[str, tuple[str, ...]]
    relation_rule_versions_by_incident: Mapping[str, Mapping[str, tuple[str, ...]]]
    graph_feature_policy_version: str
    graph_feature_policy_fingerprint: str
    graph_feature_policy_config: Mapping[str, object]
    pruning_policy_versions: Mapping[str, str]
    pruning_fingerprints: Mapping[str, str]
    anomaly_policy_version: str
    anomaly_policy_fingerprint: str
    anomaly_policy_config: Mapping[str, object]
    baseline_schema_version: str
    metric_selection_policy_version: str
    zero_variance_policy_version: str
    business_transition_policy_version: str
    topology_dependency_policy_version: str
    graph_fingerprints: Mapping[str, str]
    topology_fingerprints: Mapping[str, str]
    graph_policy_provenance_by_incident: Mapping[str, Mapping[str, object]]

    def __post_init__(self) -> None:
        text_fields = (
            "graph_feature_policy_version", "anomaly_policy_version",
            "baseline_schema_version", "metric_selection_policy_version",
            "zero_variance_policy_version", "business_transition_policy_version",
            "topology_dependency_policy_version",
        )
        if any(not isinstance(getattr(self, name), str) or not getattr(self, name).strip()
               for name in text_fields):
            raise ValueError("Versões de políticas devem ser strings não vazias.")
        for name in ("relation_rule_versions", "pruning_policy_versions", "pruning_fingerprints",
                     "graph_fingerprints", "topology_fingerprints"):
            value = dict(getattr(self, name))
            if any(not isinstance(key, str) or not key.strip() for key in value):
                raise ValueError(f"{name} requer chaves textuais não vazias.")
            if name == "relation_rule_versions":
                if any(not isinstance(versions, tuple) or not versions
                       or any(not isinstance(version, str) or not version.strip() for version in versions)
                       for versions in value.values()):
                    raise ValueError("relation_rule_versions requer tuples de versões não vazias.")
                value = {key: tuple(sorted(set(versions))) for key, versions in value.items()}
            elif any(not isinstance(item, str) or not item.strip() for item in value.values()):
                raise ValueError(f"{name} requer valores textuais não vazios.")
            object.__setattr__(self, name, MappingProxyType({key: value[key] for key in sorted(value)}))
        nested_fields = ("relation_rule_versions_by_incident", "graph_policy_provenance_by_incident")
        for name in nested_fields:
            outer = {}
            for incident, values in sorted(dict(getattr(self, name)).items()):
                if not isinstance(incident, str) or not incident.strip() or not isinstance(values, Mapping):
                    raise ValueError(f"{name} inválido.")
                normalized = {}
                for key, value in sorted(values.items()):
                    if name == "relation_rule_versions_by_incident":
                        normalized[key] = tuple(sorted(set(value)))
                    else:
                        normalized[key] = value
                outer[incident] = deep_freeze_json(normalized)
            object.__setattr__(self, name, MappingProxyType(outer))
        object.__setattr__(self, "graph_feature_policy_config",
                           deep_freeze_json(self.graph_feature_policy_config))
        object.__setattr__(self, "anomaly_policy_config",
                           deep_freeze_json(self.anomaly_policy_config))
        expected_relations: dict[str, set[str]] = {}
        for versions in self.relation_rule_versions_by_incident.values():
            for relation, values in versions.items():
                expected_relations.setdefault(relation, set()).update(values)
        exact_union = {key: tuple(sorted(value)) for key, value in expected_relations.items()}
        if dict(self.relation_rule_versions) != exact_union:
            raise ValueError("relation_rule_versions deve ser a união exata por incidente.")
        sha_fields = ("graph_feature_policy_fingerprint", "anomaly_policy_fingerprint")
        if any(not isinstance(getattr(self, name), str)
               or len(getattr(self, name)) != 64 for name in sha_fields):
            raise ValueError("Fingerprints de políticas devem ser SHA-256.")
        if _sha(self.graph_feature_policy_config) != self.graph_feature_policy_fingerprint:
            raise ValueError("Fingerprint do GraphFeaturePolicy diverge da configuração.")
        if _sha(self.anomaly_policy_config) != self.anomaly_policy_fingerprint:
            raise ValueError("Fingerprint da AnomalyPolicy diverge da configuração.")
        for mapping_name in ("pruning_fingerprints", "graph_fingerprints", "topology_fingerprints"):
            if any(len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
                   for value in getattr(self, mapping_name).values()):
                raise ValueError(f"{mapping_name} deve conter SHA-256 válidos.")
        business_versions, topology_versions = set(), set()
        for provenance in self.graph_policy_provenance_by_incident.values():
            business = provenance.get("business_transition_policy")
            topology = provenance.get("topology_dependency_policy")
            if not isinstance(business, Mapping) or not isinstance(topology, Mapping):
                raise ValueError("Proveniência estrutural de negócio/topologia ausente.")
            if _sha(business) != provenance.get("business_transition_policy_fingerprint"):
                raise ValueError("Fingerprint de BusinessTransitionPolicy divergente.")
            if _sha(topology) != provenance.get("topology_dependency_policy_fingerprint"):
                raise ValueError("Fingerprint de TopologyDependencyPolicy divergente.")
            business_versions.add(business["version"])
            topology_versions.add(topology["topology_version"])
        if self.business_transition_policy_version != ",".join(sorted(business_versions)):
            raise ValueError("Agregado de BusinessTransitionPolicy divergente.")
        if self.topology_dependency_policy_version != ",".join(sorted(topology_versions)):
            raise ValueError("Agregado de TopologyDependencyPolicy divergente.")

    def to_dict(self) -> dict[str, object]:
        return {
            "relation_rule_versions": {key: list(value) for key, value in self.relation_rule_versions.items()},
            "relation_rule_versions_by_incident": {
                incident: {key: list(value) for key, value in versions.items()}
                for incident, versions in self.relation_rule_versions_by_incident.items()
            },
            "graph_feature_policy_version": self.graph_feature_policy_version,
            "graph_feature_policy_fingerprint": self.graph_feature_policy_fingerprint,
            "graph_feature_policy_config": canonical_json_value(self.graph_feature_policy_config),
            "pruning_policy_versions": dict(self.pruning_policy_versions),
            "pruning_fingerprints": dict(self.pruning_fingerprints),
            "anomaly_policy_version": self.anomaly_policy_version,
            "anomaly_policy_fingerprint": self.anomaly_policy_fingerprint,
            "anomaly_policy_config": canonical_json_value(self.anomaly_policy_config),
            "baseline_schema_version": self.baseline_schema_version,
            "metric_selection_policy_version": self.metric_selection_policy_version,
            "zero_variance_policy_version": self.zero_variance_policy_version,
            "business_transition_policy_version": self.business_transition_policy_version,
            "topology_dependency_policy_version": self.topology_dependency_policy_version,
            "graph_fingerprints": dict(self.graph_fingerprints),
            "topology_fingerprints": dict(self.topology_fingerprints),
            "graph_policy_provenance_by_incident": {
                incident: canonical_json_value(value)
                for incident, value in self.graph_policy_provenance_by_incident.items()
            },
        }


@dataclass(frozen=True)
class RCAFeatureDatasetV3:
    rows: tuple[DatasetRowV3, ...]
    feature_view: FeatureViewV3
    baselines: BaselineSnapshotV3
    split_incident_ids: Mapping[str, tuple[str, ...]]
    seed: int
    split_fractions: Mapping[str, float]
    policy_provenance: PolicyProvenanceV3 = None
    supervised: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.feature_view, FeatureViewV3):
            raise TypeError("feature_view deve ser FeatureViewV3.")
        if not isinstance(self.baselines, BaselineSnapshotV3):
            raise TypeError("baselines deve ser BaselineSnapshotV3.")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed deve ser inteiro não booleano.")
        if not isinstance(self.supervised, bool):
            raise TypeError("supervised deve ser booleano.")
        splits = {key: tuple(value) for key, value in self.split_incident_ids.items()}
        if set(splits) != {"train", "validation", "test"}:
            raise ValueError("Dataset requer splits train, validation e test.")
        flattened = [incident for values in splits.values() for incident in values]
        if any(not isinstance(incident, str) or not incident.strip() for incident in flattened):
            raise ValueError("IDs dos splits devem ser strings não vazias.")
        if len(flattened) != len(set(flattened)):
            raise ValueError("Splits devem ser disjuntos.")
        if tuple(sorted(self.baselines.training_incident_ids)) != tuple(sorted(splits["train"])):
            raise ValueError("Baseline deve corresponder exatamente ao split train.")
        split_by_id = {incident: split for split, ids in splits.items() for incident in ids}
        keys = [(row.vector.incident_id, row.vector.service_name) for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("Linha duplicada para (incident_id, service_name).")
        if set(incident for incident, _ in keys) != set(flattened):
            raise ValueError("Incidentes das linhas e dos splits devem coincidir.")
        canonical_rows = tuple(sorted(
            self.rows, key=lambda row: (row.split, row.vector.incident_id, row.vector.service_name)
        ))
        if self.rows != canonical_rows:
            raise ValueError("Linhas devem estar em ordem canônica.")
        for row in self.rows:
            row.vector.validate()
            if split_by_id.get(row.vector.incident_id) != row.split:
                raise ValueError("Linha pertence ao split incorreto.")
            if row.vector.feature_names != self.feature_view.names:
                raise ValueError("Vetor incompatível com feature view.")
            if row.vector.feature_fingerprint != self.feature_view.fingerprint:
                raise ValueError("Fingerprint do vetor incompatível.")
            if self.supervised and (row.target is None or row.target.label_status is not LabelStatus.LABELED):
                raise ValueError("Toda linha supervisionada requer target labeled.")
            if row.target and (row.target.incident_id, row.target.service_name) != (
                row.vector.incident_id, row.vector.service_name
            ):
                raise ValueError("Target diverge da linha.")
        object.__setattr__(self, "split_incident_ids", MappingProxyType(splits))
        fractions = dict(self.split_fractions)
        if set(fractions) != {"train", "validation", "test"} or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0 or value > 1 for value in fractions.values()
        ):
            raise ValueError("split_fractions deve conter frações finitas válidas.")
        if not math.isclose(sum(fractions.values()), 1.0, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("split_fractions deve somar 1 dentro de tolerância 1e-12.")
        object.__setattr__(self, "split_fractions", MappingProxyType(fractions))
        if not isinstance(self.policy_provenance, PolicyProvenanceV3):
            raise TypeError("policy_provenance deve ser PolicyProvenanceV3.")
        incidents = set(flattened)
        for name in ("relation_rule_versions_by_incident", "pruning_policy_versions",
                     "pruning_fingerprints", "graph_fingerprints", "topology_fingerprints",
                     "graph_policy_provenance_by_incident"):
            if set(getattr(self.policy_provenance, name)) != incidents:
                raise ValueError(f"policy_provenance.{name} deve cobrir exatamente os incidentes.")

    @property
    def registry(self) -> FeatureViewV3:
        return self.feature_view

    def matrix(self, split: str) -> tuple[tuple[float, ...], ...]:
        return tuple(row.vector.model_row() for row in self.rows if row.split == split)

    def targets(self, split: str) -> tuple[bool | None, ...]:
        return tuple(row.target.is_root_cause if row.target else None
                     for row in self.rows if row.split == split)

    @property
    def rows_sha256(self) -> str:
        return _sha([row.to_dict() for row in self.rows])

    @property
    def split_sha256(self) -> Mapping[str, str]:
        return MappingProxyType({split: _sha([row.to_dict() for row in self.rows if row.split == split])
                                 for split in ("train", "validation", "test")})

    @property
    def manifest(self) -> DatasetManifestV3:
        baseline_json = self.baselines.to_json()
        canonical_registry = FeatureRegistryV3().to_json()
        feature_view_json = self.feature_view.to_json()
        distribution = {"positive": 0, "negative": 0, "unknown": 0, "excluded": 0, "unlabeled": 0}
        for row in self.rows:
            if row.target is None:
                distribution["unlabeled"] += 1
            elif row.target.label_status is LabelStatus.LABELED:
                distribution["positive" if row.target.is_root_cause else "negative"] += 1
            else:
                distribution[row.target.label_status.value] += 1
        return DatasetManifestV3(
            "rca-dataset-v3", "rca-feature-extractor-v3", canonical_registry,
            _sha(json.loads(canonical_registry)), self.feature_view.fingerprint,
            feature_view_json, _sha(json.loads(feature_view_json)), self.feature_view.names,
            len(self.feature_view.names), "rca-features-v3",
            baseline_json, _sha(json.loads(baseline_json)), self.seed,
            "sorted-unique-seeded-shuffle-v1", self.split_fractions,
            self.split_incident_ids, self.split_sha256, len({row.vector.incident_id for row in self.rows}),
            len(self.rows), distribution, self.feature_view.ablation_id,
            self.policy_provenance, self.rows_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        payload = {"manifest": self.manifest.to_dict(),
                   "rows": [row.to_dict() for row in self.rows]}
        payload["dataset_sha256"] = _sha(payload)
        return payload

    def to_json(self) -> str:
        return _json(self.to_dict())

    @property
    def dataset_sha256(self) -> str:
        return self.to_dict()["dataset_sha256"]

    def ablate(self, *groups: FeatureGroup | str) -> "RCAFeatureDatasetV3":
        excluded = {FeatureGroup(group) for group in groups}
        view = FeatureViewV3(tuple(
            definition for definition in self.feature_view.definitions
            if definition.group not in excluded
        ))
        rows = tuple(DatasetRowV3(row.split, _view_vector(row.vector, view), row.target) for row in self.rows)
        return RCAFeatureDatasetV3(rows, view, self.baselines, self.split_incident_ids,
                                   self.seed, self.split_fractions, self.policy_provenance,
                                   self.supervised)


def _view_vector(vector, view: FeatureViewV3) -> RCAFeatureVectorViewV3:
    return RCAFeatureVectorViewV3(
        vector.incident_id, vector.service_name,
        {name: vector.values[name] for name in view.names}, view.names,
        view.fingerprint, view.ablation_id,
    )


class IncidentDatasetGeneratorV3:
    def __init__(self, *, seed: int = 0, extractor: RCAFeatureExtractorV3 | None = None):
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed deve ser inteiro.")
        self.seed = seed
        if extractor is not None and not isinstance(extractor, RCAFeatureExtractorV3):
            raise TypeError("extractor deve ser RCAFeatureExtractorV3.")
        self.extractor = extractor or RCAFeatureExtractorV3()

    def split(self, incident_ids: Iterable[str], *, train_fraction: float = .7,
              validation_fraction: float = .15) -> Mapping[str, tuple[str, ...]]:
        raw_ids = tuple(incident_ids)
        if any(not isinstance(item, str) or not item.strip() for item in raw_ids):
            raise ValueError("IDs de split devem ser strings não vazias.")
        ids = sorted(set(raw_ids))
        if not ids:
            raise ValueError("Não há incidentes para dividir.")
        fractions = (train_fraction, validation_fraction)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in fractions):
            raise ValueError("Frações de split devem ser números finitos não booleanos.")
        if not 0 < train_fraction <= 1 or not 0 <= validation_fraction < 1 or train_fraction + validation_fraction > 1:
            raise ValueError("Frações de split inválidas.")
        random.Random(self.seed).shuffle(ids)
        train_end = max(1, int(len(ids) * train_fraction))
        validation_end = train_end + int(len(ids) * validation_fraction)
        return MappingProxyType({"train": tuple(sorted(ids[:train_end])),
                                 "validation": tuple(sorted(ids[train_end:validation_end])),
                                 "test": tuple(sorted(ids[validation_end:]))})

    def generate(self, inputs: Iterable[FeatureInputV3], *,
                 targets: Iterable[RCAServiceTargetV3] | None = None,
                 supervised: bool = False, train_fraction: float = .7,
                 validation_fraction: float = .15) -> RCAFeatureDatasetV3:
        items = tuple(inputs)
        if len({item.bundle.incident_id for item in items}) != len(items):
            raise ValueError("Cada FeatureInput deve representar um incident_id único.")
        splits = self.split((item.bundle.incident_id for item in items),
                            train_fraction=train_fraction, validation_fraction=validation_fraction)
        baseline = TrainingBaselineEstimatorV3(self.extractor.anomaly_policy).fit(
            (item.bundle for item in items), training_incident_ids=splits["train"])
        vectors = []
        relation_versions: dict[str, set[str]] = {}
        relation_versions_by_incident = {}
        pruning_versions, pruning_fingerprints = {}, {}
        graph_fingerprints, topology_fingerprints, graph_policy_by_incident = {}, {}, {}
        split_by_id = {incident: split for split, ids in splits.items() for incident in ids}
        for item in sorted(items, key=lambda value: value.bundle.incident_id):
            incident_id = item.bundle.incident_id
            graph_fingerprints[incident_id] = item.graph.graph_fingerprint
            topology_fingerprints[incident_id] = item.topology_fingerprint
            relation_versions_by_incident[incident_id] = {}
            for relation, value in item.graph.rule_versions.items():
                versions = (value,) if isinstance(value, str) else tuple(value)
                relation_versions.setdefault(relation, set()).update(versions)
                relation_versions_by_incident[incident_id][relation] = tuple(sorted(versions))
            graph_policy_by_incident[incident_id] = dict(item.graph.policy_provenance)
            if item.pruning is None:
                pruning_versions[incident_id] = "not-applied"
                pruning_fingerprints[incident_id] = _sha("not-applied")
            else:
                pruning_versions[incident_id] = item.pruning.policy_version
                pruning_fingerprints[incident_id] = item.pruning.pruning_fingerprint
            vectors.extend(self.extractor.extract(
                item.bundle, item.graph, baselines=baseline,
                pruning=item.pruning, topology=item.topology))
        target_items = tuple(targets or ())
        target_map = {}
        for target in target_items:
            if not isinstance(target, RCAServiceTargetV3):
                raise TypeError("targets deve conter RCAServiceTargetV3.")
            key = (target.incident_id, target.service_name)
            if key in target_map:
                raise ValueError("Target duplicado ou conflitante.")
            target_map[key] = target
        vector_keys = {(vector.incident_id, vector.service_name) for vector in vectors}
        extras = set(target_map) - vector_keys
        if extras:
            raise ValueError(f"Targets sem linha correspondente: {sorted(extras)!r}.")
        if supervised and set(target_map) != vector_keys:
            raise ValueError("Modo supervisionado requer target para todas as linhas elegíveis.")
        full_view = FeatureRegistryV3().select()
        rows = tuple(sorted((DatasetRowV3(
            split_by_id[vector.incident_id], _view_vector(vector, full_view),
            target_map.get((vector.incident_id, vector.service_name)),
        ) for vector in vectors), key=lambda row: (row.split, row.vector.incident_id, row.vector.service_name)))
        fractions = {"train": train_fraction, "validation": validation_fraction,
                     "test": 1 - train_fraction - validation_fraction}
        normalized_relations = {key: tuple(sorted(value)) for key, value in relation_versions.items()}
        business_policy_versions = sorted({
            value["business_transition_policy"]["version"]
            for value in graph_policy_by_incident.values()
        })
        topology_policy_versions = sorted({
            value["topology_dependency_policy"]["topology_version"]
            for value in graph_policy_by_incident.values()
        })
        policy_provenance = PolicyProvenanceV3(
            normalized_relations, relation_versions_by_incident,
            self.extractor.graph_feature_policy.version,
            self.extractor.graph_feature_policy.fingerprint,
            self.extractor.graph_feature_policy.to_dict(),
            pruning_versions, pruning_fingerprints,
            self.extractor.anomaly_policy.version,
            self.extractor.anomaly_policy.fingerprint,
            self.extractor.anomaly_policy.to_dict(),
            baseline.schema_version,
            ",".join(baseline.metric_selection_policy_versions) or "not-observed",
            ",".join(baseline.statistic_policy_versions) or "not-observed",
            ",".join(business_policy_versions),
            ",".join(topology_policy_versions), graph_fingerprints, topology_fingerprints,
            graph_policy_by_incident,
        )
        return RCAFeatureDatasetV3(rows, full_view, baseline, splits, self.seed,
                                   fractions, policy_provenance, supervised)
