from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from statistics import fmean, pstdev
from types import MappingProxyType
from typing import Iterable, Mapping

from multimodal_v1.contracts import SignalType, UnifiedEvent
from multimodal_v1.evidence.contracts import IncidentEvidenceBundle
from multimodal_v1.anomaly import AnomalyPolicyV1, DEFAULT_ANOMALY_POLICY


_NUMBER = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}|\b\d+(?:\.\d+)?\b")


def log_template(event: UnifiedEvent) -> str | None:
    value = event.attributes.get("body", event.attributes.get("message"))
    if not isinstance(value, str) or not value.strip():
        return None
    return _NUMBER.sub("<value>", " ".join(value.lower().split()))


def metric_kind(name: str | None) -> str | None:
    normalized = (name or "").lower().replace("-", "_").replace(".", "_")
    if "cpu" in normalized:
        return "cpu"
    if "memory" in normalized or "mem_" in normalized:
        return "memory"
    if "error" in normalized and ("rate" in normalized or "count" in normalized):
        return "error_rate"
    if ("request" in normalized or "throughput" in normalized) and ("rate" in normalized or "count" in normalized):
        return "request_rate"
    if any(term in normalized for term in ("saturation", "utilization", "queue_depth")):
        return "saturation"
    return None


def event_latency_ms(event: UnifiedEvent) -> float | None:
    for key in ("duration_ms", "latency_ms", "span.duration_ms"):
        value = event.attributes.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    start = event.attributes.get("start_time_unix_nano")
    end = event.attributes.get("end_time_unix_nano")
    if all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end)) and end >= start:
        return (end - start) / 1_000_000
    return None


@dataclass(frozen=True)
class BaselineStatistic:
    mean: float
    stddev: float
    count: int
    zero_variance_saturation: float = 12.0
    policy_version: str = "zscore-v3-zero-variance-isclose-1e-12-saturated-12"

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ValueError("BaselineStatistic.count deve ser inteiro positivo.")
        if not all(math.isfinite(value) for value in (self.mean, self.stddev, self.zero_variance_saturation)):
            raise ValueError("BaselineStatistic requer valores finitos.")
        if self.stddev < 0:
            raise ValueError("BaselineStatistic.stddev não pode ser negativo.")
        if self.zero_variance_saturation <= 0:
            raise ValueError("zero_variance_saturation deve ser positiva.")

    def zscore(self, value: float) -> float:
        if self.stddev == 0:
            if math.isclose(value, self.mean, rel_tol=1e-12, abs_tol=1e-12):
                return 0.0
            return math.copysign(self.zero_variance_saturation, value - self.mean)
        return (value - self.mean) / self.stddev


@dataclass(frozen=True)
class MetricIdentityV3:
    service_name: str
    metric_kind: str
    metric_name: str
    metric_unit: str | None
    metric_aggregation: str | None

    @property
    def key(self) -> str:
        return json.dumps([self.service_name, self.metric_kind, self.metric_name,
                           self.metric_unit, self.metric_aggregation], separators=(",", ":"))


@dataclass(frozen=True)
class MetricSelectionV3:
    identity: MetricIdentityV3
    sample_count: int
    policy: str = "max-normal-training-samples-then-lexicographic-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, MetricIdentityV3):
            raise TypeError("MetricSelectionV3.identity deve ser MetricIdentityV3.")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count < 1:
            raise ValueError("MetricSelectionV3.sample_count deve ser inteiro positivo.")


@dataclass(frozen=True)
class BaselineSnapshotV3:
    statistics: Mapping[str, BaselineStatistic]
    log_templates: Mapping[str, tuple[str, ...]]
    training_incident_ids: tuple[str, ...]
    metric_selections: Mapping[str, MetricSelectionV3] = None
    anomaly_policy_version: str = DEFAULT_ANOMALY_POLICY.version
    schema_version: str = "rca-feature-baselines-v3"

    def __post_init__(self) -> None:
        if any(not isinstance(item, str) or not item.strip() for item in self.training_incident_ids):
            raise ValueError("training_incident_ids deve conter strings não vazias.")
        object.__setattr__(self, "statistics", MappingProxyType(dict(self.statistics)))
        object.__setattr__(self, "log_templates", MappingProxyType(dict(self.log_templates)))
        object.__setattr__(self, "metric_selections", MappingProxyType(dict(self.metric_selections or {})))
        if any(not all(math.isfinite(value) for value in (item.mean, item.stddev))
               for item in self.statistics.values()):
            raise ValueError("Baseline contém estatística não finita.")
        statistic_versions = {item.policy_version for item in self.statistics.values()}
        selection_versions = {item.policy for item in self.metric_selections.values()}
        if len(statistic_versions) > 1 or len(selection_versions) > 1:
            raise ValueError("Baseline mistura versões de política incompatíveis.")

    @property
    def statistic_policy_versions(self) -> tuple[str, ...]:
        return tuple(sorted({item.policy_version for item in self.statistics.values()}))

    @property
    def metric_selection_policy_versions(self) -> tuple[str, ...]:
        return tuple(sorted({item.policy for item in self.metric_selections.values()}))

    def statistic(self, service: str, kind: str) -> BaselineStatistic | None:
        if kind == "latency":
            return self.statistics.get(f"service:{service}:latency")
        selection = self.metric_selections.get(f"{service}:{kind}")
        return self.statistics.get(selection.identity.key) if selection else None

    def metric_selection(self, service: str, kind: str) -> MetricSelectionV3 | None:
        return self.metric_selections.get(f"{service}:{kind}")

    def known_templates(self, service: str) -> frozenset[str]:
        return frozenset(self.log_templates.get(service, ()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "training_incident_ids": list(self.training_incident_ids),
            "statistics": {
                key: {"mean": value.mean, "stddev": value.stddev, "count": value.count,
                      "zero_variance_saturation": value.zero_variance_saturation,
                      "policy_version": value.policy_version}
                for key, value in sorted(self.statistics.items())
            },
            "log_templates": {key: list(value) for key, value in sorted(self.log_templates.items())},
            "metric_selections": {
                key: {"service_name": value.identity.service_name,
                      "metric_kind": value.identity.metric_kind,
                      "metric_name": value.identity.metric_name,
                      "metric_unit": value.identity.metric_unit,
                      "metric_aggregation": value.identity.metric_aggregation,
                      "sample_count": value.sample_count, "policy": value.policy}
                for key, value in sorted(self.metric_selections.items())
            },
            "anomaly_policy_version": self.anomaly_policy_version,
            "statistic_policy_versions": list(self.statistic_policy_versions),
            "metric_selection_policy_versions": list(self.metric_selection_policy_versions),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)


class TrainingBaselineEstimatorV3:
    """Ajusta somente bundles explicitamente pertencentes ao split de treino."""

    def __init__(self, anomaly_policy: AnomalyPolicyV1 | None = None):
        self.anomaly_policy = anomaly_policy or DEFAULT_ANOMALY_POLICY

    def fit(self, bundles: Iterable[IncidentEvidenceBundle], *,
            training_incident_ids: Iterable[str]) -> BaselineSnapshotV3:
        allowed = frozenset(training_incident_ids)
        if not allowed:
            raise ValueError("training_incident_ids não pode ser vazio.")
        bundles = tuple(bundles)
        bundle_ids = [bundle.incident_id for bundle in bundles]
        if len(bundle_ids) != len(set(bundle_ids)):
            raise ValueError("Bundles duplicados não são permitidos no baseline.")
        unknown = allowed - set(bundle_ids)
        if unknown:
            raise ValueError(f"Incidentes de treino ausentes da entrada: {sorted(unknown)!r}.")
        values: dict[str, list[float]] = {}
        metric_values: dict[MetricIdentityV3, list[float]] = {}
        templates: dict[str, set[str]] = {}
        seen = set()
        for bundle in bundles:
            if bundle.incident_id not in allowed:
                continue
            seen.add(bundle.incident_id)
            for event in bundle.events:
                service = event.service_name
                if not service or self.anomaly_policy.is_anomalous(event):
                    continue
                if event.signal_type is SignalType.TRACE:
                    latency = event_latency_ms(event)
                    if latency is not None:
                        values.setdefault(f"service:{service}:latency", []).append(latency)
                elif event.signal_type is SignalType.METRIC and event.metric_value is not None:
                    kind = metric_kind(event.metric_name)
                    if kind:
                        identity = MetricIdentityV3(
                            service, kind, event.metric_name, event.metric_unit,
                            event.metric_aggregation,
                        )
                        metric_values.setdefault(identity, []).append(float(event.metric_value))
                elif event.signal_type is SignalType.LOG:
                    template = log_template(event)
                    if template:
                        templates.setdefault(service, set()).add(template)
        selections = {}
        identities_by_service_kind: dict[tuple[str, str], list[MetricIdentityV3]] = {}
        for identity in metric_values:
            identities_by_service_kind.setdefault((identity.service_name, identity.metric_kind), []).append(identity)
        for (service, kind), identities in identities_by_service_kind.items():
            selected = min(identities, key=lambda item: (-len(metric_values[item]), item.key))
            selections[f"{service}:{kind}"] = MetricSelectionV3(selected, len(metric_values[selected]))
            values[selected.key] = metric_values[selected]
        statistics = {
            key: BaselineStatistic(fmean(items), pstdev(items), len(items))
            for key, items in values.items()
        }
        return BaselineSnapshotV3(
            statistics, {key: tuple(sorted(items)) for key, items in templates.items()},
            tuple(sorted(allowed)), selections, self.anomaly_policy.version,
        )
