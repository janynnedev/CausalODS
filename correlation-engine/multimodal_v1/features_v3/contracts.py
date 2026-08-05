from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


SCHEMA_VERSION = "rca-features-v3"


class FeatureGroup(str, Enum):
    TRACE = "trace"
    LOG = "log"
    METRIC = "metric"
    BUSINESS = "business"
    GRAPH = "graph"
    QUALITY = "quality"


class FeatureType(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"


FeatureValue = float | int | bool | None


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    group: FeatureGroup
    value_type: FeatureType
    semantics: str
    unit: str | None = None
    nullable: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "group": self.group.value,
            "type": self.value_type.value, "semantics": self.semantics,
            "unit": self.unit, "nullable": self.nullable,
        }


@dataclass(frozen=True)
class RCAFeatureVectorV3:
    incident_id: str
    service_name: str
    values: Mapping[str, FeatureValue]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.incident_id, str) or not self.incident_id.strip():
            raise ValueError("incident_id deve ser texto não vazio.")
        if not isinstance(self.service_name, str) or not self.service_name.strip():
            raise ValueError("service_name deve ser texto não vazio.")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version deve ser {SCHEMA_VERSION}.")
        copied = dict(self.values)
        from multimodal_v1.features_v3.registry import FeatureRegistryV3
        registry = FeatureRegistryV3()
        if tuple(copied) != registry.names:
            raise ValueError("Vetor deve conter nomes exatos na ordem do registry canônico.")
        for definition in registry.definitions:
            value = copied[definition.name]
            if value is None:
                if not definition.nullable:
                    raise ValueError(f"Feature {definition.name} não aceita None.")
                continue
            if definition.value_type is FeatureType.BOOLEAN:
                valid = isinstance(value, bool)
            elif definition.value_type is FeatureType.INTEGER:
                valid = isinstance(value, int) and not isinstance(value, bool)
            else:
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not valid:
                raise TypeError(f"Feature {definition.name} incompatível com {definition.value_type.value}.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Feature {definition.name} deve ser finita.")
        object.__setattr__(self, "values", MappingProxyType(copied))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "service_name": self.service_name,
            "features": dict(self.values),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def model_row(self, feature_names: tuple[str, ...], *, missing_value: float = math.nan) -> tuple[float, ...]:
        row = []
        for name in feature_names:
            if name not in self.values:
                raise KeyError(f"Feature ausente do vetor: {name}.")
            value = self.values[name]
            row.append(missing_value if value is None else float(value))
        return tuple(row)


@dataclass(frozen=True)
class RCAFeatureVectorViewV3:
    incident_id: str
    service_name: str
    values: Mapping[str, FeatureValue]
    feature_names: tuple[str, ...]
    feature_fingerprint: str
    ablation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.incident_id, str) or not self.incident_id.strip():
            raise ValueError("incident_id da view deve ser texto não vazio.")
        if not isinstance(self.service_name, str) or not self.service_name.strip():
            raise ValueError("service_name da view deve ser texto não vazio.")
        self.validate()
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def validate(self) -> None:
        from multimodal_v1.features_v3.registry import FEATURES, FeatureViewV3
        definitions = {item.name: item for item in FEATURES}
        if any(name not in definitions for name in self.feature_names):
            raise ValueError("feature_names contém nome não canônico.")
        view = FeatureViewV3(tuple(definitions[name] for name in self.feature_names))
        if tuple(self.values) != self.feature_names:
            raise ValueError("View deve expor exatamente feature_names na ordem declarada.")
        if self.feature_fingerprint != view.fingerprint:
            raise ValueError("feature_fingerprint diverge da FeatureViewV3.")
        if self.ablation_id != view.ablation_id:
            raise ValueError("ablation_id diverge da FeatureViewV3.")
        for definition in view.definitions:
            value = self.values[definition.name]
            if value is None:
                if not definition.nullable:
                    raise ValueError(f"Feature {definition.name} não aceita None.")
                continue
            if definition.value_type is FeatureType.BOOLEAN:
                valid = isinstance(value, bool)
            elif definition.value_type is FeatureType.INTEGER:
                valid = isinstance(value, int) and not isinstance(value, bool)
            else:
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not valid:
                raise TypeError(f"Feature {definition.name} incompatível com {definition.value_type.value}.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Feature {definition.name} deve ser finita.")

    def model_row(self, *, missing_value: float = math.nan) -> tuple[float, ...]:
        return tuple(missing_value if value is None else float(value) for value in self.values.values())

    def to_dict(self) -> dict[str, object]:
        return {"incident_id": self.incident_id, "service_name": self.service_name,
                "ablation_id": self.ablation_id, "feature_fingerprint": self.feature_fingerprint,
                "feature_names": list(self.feature_names),
                "features": dict(self.values)}
