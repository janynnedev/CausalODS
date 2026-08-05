from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol

from multimodal_v1.canonical import canonical_json_value, deep_freeze_json
from multimodal_v1.contracts import UnifiedEvent


HARNESS_VERSION = "observation-degradation-harness-v3"
MODALITIES = ("trace", "log", "metric", "business_event", "topology")
OPERATORS = (
    "random_span_drop", "critical_path_span_drop", "full_trace_sampling",
    "random_log_drop", "error_log_drop", "metric_delay", "metric_downsampling",
    "metric_series_drop", "business_event_drop", "kafka_message_duplicate",
    "kafka_message_reorder", "uninstrumented_service", "topology_edge_drop",
    "topology_edge_add",
)
SEVERITY_UNITS = ("event", "trace", "metric_point", "metric_series", "message",
                  "message_position", "service", "topology_edge")
METRIC_DIRECTIONS = {"top_1": "higher_is_better", "top_3": "higher_is_better",
                     "mrr": "higher_is_better", "fpr": "lower_is_better"}


def _json(value: Any) -> str:
    return json.dumps(canonical_json_value(value), sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _quantity(count: int, severity: float) -> int:
    if not count or not severity:
        return 0
    return min(count, max(1, math.floor(count * severity + .5)))


class DegradationSeverity(float, Enum):
    NONE = 0.0
    TEN = 0.1
    THIRTY = 0.3
    FIFTY = 0.5


@dataclass(frozen=True)
class OperatorPolicyV3:
    name: str
    version: str
    modalities: tuple[str, ...]
    severity_unit: str
    rounding_policy: str = "half-up-min-one-v1"
    configuration: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        if isinstance(self.modalities, (str, bytes)):
            raise TypeError("modalities deve ser coleção, não texto.")
        object.__setattr__(self, "modalities", tuple(self.modalities))
        if (not isinstance(self.name, str) or self.name not in OPERATORS
                or not isinstance(self.version, str) or not self.version.strip()):
            raise ValueError("Política de operador inválida.")
        if (not self.modalities or len(self.modalities) != len(set(self.modalities))
                or any(item not in MODALITIES for item in self.modalities)
                or list(self.modalities) != sorted(
                    self.modalities, key=lambda item: MODALITIES.index(item))):
            raise ValueError("Modalidades da política devem ser conhecidas, únicas e canônicas.")
        if self.severity_unit not in SEVERITY_UNITS:
            raise ValueError("severity_unit desconhecida.")
        if not isinstance(self.rounding_policy, str) or not self.rounding_policy.strip():
            raise ValueError("rounding_policy deve ser texto não vazio.")
        object.__setattr__(self, "configuration", deep_freeze_json(self.configuration or {}))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version,
                "modalities": list(self.modalities), "severity_unit": self.severity_unit,
                "rounding_policy": self.rounding_policy,
                "configuration": canonical_json_value(self.configuration)}

    @property
    def fingerprint(self) -> str:
        return _sha(self.to_dict())


def _policies() -> dict[str, OperatorPolicyV3]:
    definitions = {
        "random_span_drop": (("trace",), "event", "random-span-drop-v1", {}),
        "critical_path_span_drop": (("trace",), "event", "critical-path-annotated-v1",
                                    {"eligible": "attributes.critical_path == true"}),
        "full_trace_sampling": (("trace",), "trace", "full-trace-sampling-v1", {}),
        "random_log_drop": (("log",), "event", "random-log-drop-v1", {}),
        "error_log_drop": (("log",), "event", "error-log-policy-v1",
                           {"severity": ["ERROR", "CRITICAL", "FATAL"],
                            "status": ["ERROR"], "error_type": True,
                            "anomaly_attributes": ["exception.type", "error.type"]}),
        "metric_delay": (("metric",), "metric_point", "synthetic-metric-delay-v1",
                         {"semantics": "metadata-only; replay scheduling unchanged"}),
        "metric_downsampling": (("metric",), "metric_point", "global-temporal-series-downsampling-v2",
                                {"preserve_endpoints": True, "selection": "evenly-spaced-interior",
                                 "allocation": "proportional-largest-remainder-v1",
                                 "dimension_policy": "resource_attributes.metric.dimensions-v1"}),
        "metric_series_drop": (("metric",), "metric_series", "metric-series-drop-v2",
                               {"dimension_policy": "resource_attributes.metric.dimensions-v1"}),
        "business_event_drop": (("business_event",), "event", "business-event-drop-v1", {}),
        "kafka_message_duplicate": (("trace", "log", "metric", "business_event"),
                                    "message", "kafka-message-duplicate-v1", {}),
        "kafka_message_reorder": (("trace", "log", "metric", "business_event"),
                                  "message_position", "deterministic-rotation-v1", {}),
        "uninstrumented_service": (("trace", "log", "metric", "business_event"),
                                   "service", "uninstrumented-service-v1", {}),
        "topology_edge_drop": (("topology",), "topology_edge", "topology-edge-drop-v1", {}),
        "topology_edge_add": (("topology",), "topology_edge", "topology-edge-add-v1", {}),
    }
    return {name: OperatorPolicyV3(name, version, modalities, unit, configuration=config)
            for name, (modalities, unit, version, config) in definitions.items()}


OPERATOR_POLICIES = deep_freeze_json({name: policy.to_dict()
                                     for name, policy in _policies().items()})


def operator_policy(name: str) -> OperatorPolicyV3:
    if name not in OPERATORS:
        raise ValueError(f"Operador desconhecido: {name}.")
    value = OPERATOR_POLICIES[name]
    return OperatorPolicyV3(value["name"], value["version"], tuple(value["modalities"]),
                            value["severity_unit"], value["rounding_policy"],
                            value["configuration"])


def degradation_selection_identity(*, observation_sha256: str, operator: str,
                                   operator_version: str, operator_fingerprint: str,
                                   severity: float, seed: int,
                                   parameters_sha256: str) -> dict[str, Any]:
    return {"observation_sha256": observation_sha256, "operator": operator,
            "operator_version": operator_version,
            "operator_fingerprint": operator_fingerprint,
            "severity": severity, "seed": seed,
            "parameters_sha256": parameters_sha256}


def run_identity(*, input_dataset_sha256: str, ground_truth_sha256: str,
                 selection_identity: Mapping[str, Any]) -> dict[str, Any]:
    return {"input_dataset_sha256": input_dataset_sha256,
            "ground_truth_sha256": ground_truth_sha256,
            "selection_fingerprint": _sha(selection_identity),
            "selection_identity": canonical_json_value(selection_identity)}


def derive_run_id(identity: Mapping[str, Any]) -> str:
    fingerprint = _sha(identity)
    selection = identity["selection_identity"]
    return (f"degradation-{selection['operator']}-{int(selection['severity'] * 100)}-"
            f"seed-{selection['seed']}-{fingerprint[:12]}")


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _reference(event: UnifiedEvent) -> tuple[str, str | None]:
    return event.event_id, event.source_reference


@dataclass(frozen=True)
class ObservationDataset:
    events: tuple[UnifiedEvent, ...]
    topology: Mapping[str, tuple[str, ...]]
    ground_truth: Mapping[str, tuple[str, ...]]
    schema_version: str = "observation-dataset-v1"
    allow_duplicate_references: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema_version deve ser texto não vazio.")
        if not isinstance(self.allow_duplicate_references, bool):
            raise TypeError("allow_duplicate_references deve ser booleano.")
        if not isinstance(self.events, tuple) or any(
                not isinstance(event, UnifiedEvent) for event in self.events):
            raise TypeError("events deve ser tuple de UnifiedEvent.")
        references = [_reference(event) for event in self.events]
        if not self.allow_duplicate_references and len(references) != len(set(references)):
            raise ValueError("ObservationDataset base rejeita EventReference duplicada.")
        if not isinstance(self.topology, Mapping):
            raise TypeError("topology deve ser Mapping.")
        if not isinstance(self.ground_truth, Mapping):
            raise TypeError("ground_truth deve ser Mapping.")
        topology = {}
        for source, targets in self.topology.items():
            if not isinstance(source, str) or not source.strip():
                raise ValueError("Serviço source da topologia deve ser texto não vazio.")
            if isinstance(targets, (str, bytes)):
                raise TypeError("Targets da topologia devem ser iterável de strings, não texto.")
            try:
                normalized = tuple(sorted(set(targets)))
            except TypeError as exc:
                raise TypeError("Targets da topologia devem ser iteráveis.") from exc
            if any(not isinstance(target, str) or not target.strip() for target in normalized):
                raise ValueError("Targets da topologia devem ser textos não vazios.")
            if source in normalized:
                raise ValueError("Self-loop não é permitido em observation-dataset-v1.")
            topology[source] = normalized
        truth = {}
        for incident, services in self.ground_truth.items():
            if not isinstance(incident, str) or not incident.strip():
                raise ValueError("ground_truth requer incident_id textual.")
            if isinstance(services, (str, bytes)):
                raise TypeError("Targets do ground truth devem ser iterável de strings, não texto.")
            try:
                normalized = tuple(sorted(set(services)))
            except TypeError as exc:
                raise TypeError("Targets do ground truth devem ser iteráveis.") from exc
            if not normalized or any(not isinstance(item, str) or not item.strip()
                                     for item in normalized):
                raise ValueError("ground_truth requer targets textuais não vazios.")
            truth[incident] = normalized
        object.__setattr__(self, "topology", deep_freeze_json(topology))
        object.__setattr__(self, "ground_truth", deep_freeze_json(truth))

    def observation_payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version,
                "allow_duplicate_references": self.allow_duplicate_references,
                "events": [event.to_dict() for event in self.events],
                "topology": canonical_json_value(self.topology)}

    @property
    def observation_fingerprint(self) -> str:
        return _sha(self.observation_payload())

    @property
    def ground_truth_fingerprint(self) -> str:
        return _sha(self.ground_truth)

    @property
    def dataset_fingerprint(self) -> str:
        return _sha({"observation_fingerprint": self.observation_fingerprint,
                     "ground_truth_fingerprint": self.ground_truth_fingerprint})

    def to_dict(self) -> dict[str, Any]:
        return {**self.observation_payload(),
                "ground_truth": canonical_json_value(self.ground_truth)}

    @property
    def fingerprint(self) -> str:
        """Compatibilidade: fingerprint representa o envelope completo."""
        return self.dataset_fingerprint


@dataclass(frozen=True)
class DegradationAccountingV3:
    operator: str
    eligible_units: int
    requested_units: int
    affected_units: int
    unit_type: str
    input_event_count: int
    output_event_count: int
    removed_event_count: int = 0
    added_event_count: int = 0
    modified_event_count: int = 0
    input_topology_edge_count: int = 0
    output_topology_edge_count: int = 0
    removed_topology_edge_count: int = 0
    added_topology_edge_count: int = 0
    originally_requested_units: int | None = None
    requested_expansion_reason: str | None = None

    def __post_init__(self) -> None:
        if self.operator not in OPERATORS:
            raise ValueError("Accounting requer operador conhecido.")
        if self.unit_type not in SEVERITY_UNITS:
            raise ValueError("unit_type desconhecido.")
        numeric_fields = ("eligible_units", "requested_units", "affected_units",
                          "input_event_count", "output_event_count", "removed_event_count",
                          "added_event_count", "modified_event_count",
                          "input_topology_edge_count", "output_topology_edge_count",
                          "removed_topology_edge_count", "added_topology_edge_count")
        for name in numeric_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if self.requested_units > self.eligible_units or self.affected_units > self.eligible_units:
            raise ValueError("Unidades solicitadas/afetadas excedem elegibilidade.")
        expanded = self.originally_requested_units is not None
        if self.affected_units > self.requested_units:
            raise ValueError("affected_units não pode exceder requested_units.")
        if expanded:
            if (self.operator != "kafka_message_reorder"
                    or isinstance(self.originally_requested_units, bool)
                    or not isinstance(self.originally_requested_units, int)
                    or self.originally_requested_units < 0
                    or self.originally_requested_units >= self.requested_units
                    or not self.requested_expansion_reason):
                raise ValueError("Expansão de requested_units inválida.")
        elif self.requested_expansion_reason is not None:
            raise ValueError("requested_expansion_reason requer expansão declarada.")
        if self.output_event_count != (
                self.input_event_count - self.removed_event_count + self.added_event_count):
            raise ValueError("Contagens de eventos são inconsistentes.")
        if self.output_topology_edge_count != (
                self.input_topology_edge_count - self.removed_topology_edge_count
                + self.added_topology_edge_count):
            raise ValueError("Contagens de topologia são inconsistentes.")
        if self.modified_event_count > max(self.input_event_count, self.output_event_count):
            raise ValueError("modified_event_count excede conjunto aplicável.")
        if self.operator not in {"topology_edge_drop", "topology_edge_add"} and any((
                self.input_topology_edge_count, self.output_topology_edge_count,
                self.removed_topology_edge_count, self.added_topology_edge_count)):
            raise ValueError("Operador não topológico deve usar counts de topologia zero.")

    def _ratio(self, numerator: int, denominator: int) -> float | None:
        if denominator:
            return numerator / denominator
        return 0.0 if numerator == 0 else None

    @property
    def event_loss_ratio(self):
        return self._ratio(self.removed_event_count, self.input_event_count)

    @property
    def event_addition_ratio(self):
        return self._ratio(self.added_event_count, self.input_event_count)

    @property
    def event_modification_ratio(self):
        return self._ratio(self.modified_event_count, self.input_event_count)

    @property
    def topology_edge_loss_ratio(self):
        return self._ratio(self.removed_topology_edge_count, self.input_topology_edge_count)

    @property
    def topology_edge_addition_ratio(self):
        denominator = (self.eligible_units if self.operator == "topology_edge_add"
                       and self.unit_type == "topology_edge"
                       else self.input_topology_edge_count)
        return self._ratio(self.added_topology_edge_count, denominator)

    @property
    def affected_unit_ratio(self):
        return self._ratio(self.affected_units, self.eligible_units)

    @property
    def realized_severity(self):
        return self.affected_unit_ratio

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "event_loss_ratio": self.event_loss_ratio,
                "event_addition_ratio": self.event_addition_ratio,
                "event_modification_ratio": self.event_modification_ratio,
                "topology_edge_loss_ratio": self.topology_edge_loss_ratio,
                "topology_edge_addition_ratio": self.topology_edge_addition_ratio,
                "affected_unit_ratio": self.affected_unit_ratio,
                "realized_severity": self.realized_severity}


@dataclass(frozen=True)
class DegradationManifest:
    run_id: str
    operator: str
    operator_version: str
    operator_fingerprint: str
    modalities: tuple[str, ...]
    severity: float
    seed: int
    input_dataset_sha256: str
    input_observation_sha256: str
    output_dataset_sha256: str
    output_observation_sha256: str
    ground_truth_sha256: str
    selection_fingerprint: str
    run_identity: Mapping[str, Any]
    parameters_sha256: str
    parameters: Mapping[str, Any]
    accounting: DegradationAccountingV3
    removed_by_modality: Mapping[str, int]
    affected_references: tuple[Any, ...]
    operator_metadata: Mapping[str, Any]
    missing_modalities: tuple[str, ...]
    saturation_reason: str | None = None
    harness_version: str = HARNESS_VERSION

    def __post_init__(self) -> None:
        if self.harness_version != HARNESS_VERSION:
            raise ValueError("harness_version incompatível.")
        if self.operator not in OPERATORS:
            raise ValueError("Manifesto contém operador desconhecido.")
        if isinstance(self.severity, bool):
            raise TypeError("severity do manifesto não aceita booleano.")
        try:
            DegradationSeverity(self.severity)
        except (ValueError, TypeError) as exc:
            raise ValueError("Manifesto contém severidade não suportada.") from exc
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed do manifesto deve ser inteiro não booleano.")
        if not isinstance(self.accounting, DegradationAccountingV3):
            raise TypeError("accounting deve ser DegradationAccountingV3.")
        for name in ("parameters", "removed_by_modality", "operator_metadata", "run_identity"):
            object.__setattr__(self, name, deep_freeze_json(getattr(self, name)))
        object.__setattr__(self, "affected_references",
                           tuple(deep_freeze_json(item) for item in self.affected_references))
        for name in ("input_dataset_sha256", "input_observation_sha256",
                     "output_dataset_sha256", "output_observation_sha256",
                     "ground_truth_sha256", "selection_fingerprint",
                     "parameters_sha256", "operator_fingerprint"):
            if not _is_sha256(getattr(self, name)):
                raise ValueError(f"{name} deve ser SHA-256 hexadecimal.")
        if self.parameters_sha256 != _sha(self.parameters):
            raise ValueError("parameters_sha256 diverge dos parâmetros.")
        expected_input_dataset = _sha({
            "observation_fingerprint": self.input_observation_sha256,
            "ground_truth_fingerprint": self.ground_truth_sha256})
        expected_output_dataset = _sha({
            "observation_fingerprint": self.output_observation_sha256,
            "ground_truth_fingerprint": self.ground_truth_sha256})
        if self.input_dataset_sha256 != expected_input_dataset:
            raise ValueError("input_dataset_sha256 diverge das identidades componentes.")
        if self.output_dataset_sha256 != expected_output_dataset:
            raise ValueError("output_dataset_sha256 diverge das identidades componentes.")
        policy = operator_policy(self.operator)
        if self.operator_version != policy.version:
            raise ValueError("operator_version diverge da política real.")
        if self.operator_fingerprint != policy.fingerprint:
            raise ValueError("operator_fingerprint diverge da política real.")
        if self.modalities != policy.modalities:
            raise ValueError("modalities divergem da política real.")
        if self.accounting.operator != self.operator or self.accounting.unit_type != policy.severity_unit:
            raise ValueError("Accounting diverge da política do operador.")
        if (tuple(self.missing_modalities) != tuple(sorted(set(self.missing_modalities),
                                                         key=MODALITIES.index))
                or any(item not in MODALITIES for item in self.missing_modalities)):
            raise ValueError("missing_modalities deve ser conhecida, única e canônica.")
        if set(self.removed_by_modality) != set(MODALITIES):
            raise ValueError("removed_by_modality deve conter exatamente as modalidades.")
        selection = degradation_selection_identity(
            observation_sha256=self.input_observation_sha256, operator=self.operator,
            operator_version=self.operator_version,
            operator_fingerprint=self.operator_fingerprint, severity=self.severity,
            seed=self.seed, parameters_sha256=self.parameters_sha256)
        if self.selection_fingerprint != _sha(selection):
            raise ValueError("selection_fingerprint diverge da identidade observacional.")
        identity = run_identity(
            input_dataset_sha256=self.input_dataset_sha256,
            ground_truth_sha256=self.ground_truth_sha256,
            selection_identity=selection)
        if canonical_json_value(self.run_identity) != identity:
            raise ValueError("run_identity diverge da identidade reconstruída.")
        if self.run_id != derive_run_id(identity):
            raise ValueError("run_id diverge da identidade reconstruída.")
        expected = _quantity(self.accounting.eligible_units, self.severity)
        expanded = self.accounting.originally_requested_units is not None
        if expanded:
            if not (self.operator == "kafka_message_reorder" and expected == 1
                    and self.accounting.originally_requested_units == expected
                    and self.accounting.requested_units == 2
                    and self.accounting.requested_expansion_reason
                    and self.accounting.eligible_units >= 2):
                raise ValueError("Expansão do reorder diverge da severidade esperada.")
        elif self.accounting.requested_units != expected:
            raise ValueError("requested_units diverge da severidade solicitada.")
        if self.accounting.affected_units < self.accounting.requested_units:
            if not self.saturation_reason:
                raise ValueError("Saturação deve ser registrada quando affected < requested.")
        elif self.saturation_reason and not expanded:
            allowed_noop = self.accounting.eligible_units == 0 and expected == 0
            if not allowed_noop:
                raise ValueError("saturation_reason sem saturação contratual.")
        removed = canonical_json_value(self.removed_by_modality)
        for modality, count in removed.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"removed_by_modality.{modality} deve ser inteiro não negativo.")
        if sum(removed[item] for item in MODALITIES[:-1]) != self.accounting.removed_event_count:
            raise ValueError("Soma de removed_by_modality diverge dos eventos removidos.")
        if removed["topology"] != self.accounting.removed_topology_edge_count:
            raise ValueError("Perda topology diverge do accounting.")

    @property
    def realized_severity(self) -> float:
        return self.accounting.realized_severity

    def _payload(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "operator": self.operator,
                "operator_version": self.operator_version,
                "operator_fingerprint": self.operator_fingerprint,
                "modalities": list(self.modalities), "severity": self.severity,
                "realized_severity": self.realized_severity, "seed": self.seed,
                "input_dataset_sha256": self.input_dataset_sha256,
                "input_observation_sha256": self.input_observation_sha256,
                "output_dataset_sha256": self.output_dataset_sha256,
                "output_observation_sha256": self.output_observation_sha256,
                "ground_truth_sha256": self.ground_truth_sha256,
                "selection_fingerprint": self.selection_fingerprint,
                "run_identity": canonical_json_value(self.run_identity),
                "parameters_sha256": self.parameters_sha256,
                "parameters": canonical_json_value(self.parameters),
                "accounting": self.accounting.to_dict(),
                "removed_by_modality": canonical_json_value(self.removed_by_modality),
                "affected_references": canonical_json_value(self.affected_references),
                "operator_metadata": canonical_json_value(self.operator_metadata),
                "missing_modalities": list(self.missing_modalities),
                "saturation_reason": self.saturation_reason,
                "harness_version": self.harness_version}

    @property
    def manifest_fingerprint(self) -> str:
        return _sha(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "manifest_fingerprint": self.manifest_fingerprint}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @property
    def input_sha256(self) -> str:
        return self.input_dataset_sha256

    @property
    def output_sha256(self) -> str:
        return self.output_dataset_sha256


@dataclass(frozen=True)
class DegradationRun:
    dataset: ObservationDataset
    manifest: DegradationManifest

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, ObservationDataset):
            raise TypeError("DegradationRun.dataset deve ser ObservationDataset.")
        if not isinstance(self.manifest, DegradationManifest):
            raise TypeError("DegradationRun.manifest deve ser DegradationManifest.")
        if self.manifest.output_dataset_sha256 != self.dataset.dataset_fingerprint:
            raise ValueError("Manifest output_dataset_sha256 diverge do dataset.")
        if self.manifest.output_observation_sha256 != self.dataset.observation_fingerprint:
            raise ValueError("Manifest output_observation_sha256 diverge do dataset.")
        if self.manifest.ground_truth_sha256 != self.dataset.ground_truth_fingerprint:
            raise ValueError("Manifest ground_truth_sha256 diverge do dataset.")
        duplicate_mode = self.dataset.allow_duplicate_references
        if duplicate_mode != (self.manifest.operator == "kafka_message_duplicate"
                              and self.manifest.accounting.added_event_count > 0):
            raise ValueError("Modo de duplicatas diverge do operador/manifesto.")


@dataclass(frozen=True)
class KafkaReplayResult:
    attempted: int
    confirmed: int
    failed: int
    failures: tuple[str, ...]
    policy_version: str = "synchronous-confirm-continue-v1"

    def __post_init__(self) -> None:
        for name in ("attempted", "confirmed", "failed"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} deve ser inteiro não negativo.")
        if self.attempted != self.confirmed + self.failed:
            raise ValueError("attempted deve ser confirmed + failed.")
        if not isinstance(self.failures, tuple):
            raise TypeError("failures deve ser tuple.")
        if self.failed != len(self.failures):
            raise ValueError("failed deve corresponder a len(failures).")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version deve ser texto não vazio.")


class KafkaReplayPublisher(Protocol):
    def send(self, topic: str, *, key: bytes, value: bytes) -> Any: ...


def _series_key(event: UnifiedEvent) -> tuple[Any, ...]:
    dimensions = event.resource_attributes.get("metric.dimensions", {})
    return (event.service_name, event.service_instance_id, event.metric_name,
            event.metric_unit, event.metric_aggregation, _json(dimensions))


def _event_order(event: UnifiedEvent) -> tuple[Any, ...]:
    return (event.event_timestamp, event.event_id, event.source_reference or "")


class ObservationDegradationHarness:
    def apply(self, dataset: ObservationDataset, operator: str, severity: float,
              *, seed: int, parameters: Mapping[str, Any] | None = None) -> DegradationRun:
        if not isinstance(dataset, ObservationDataset):
            raise TypeError("dataset deve ser ObservationDataset.")
        policy = operator_policy(operator)
        if isinstance(severity, bool):
            raise TypeError("severity não aceita booleano.")
        try:
            level = DegradationSeverity(severity)
        except ValueError as exc:
            raise ValueError("Severidade deve ser 0%, 10%, 30% ou 50%.") from exc
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed deve ser inteiro não booleano.")
        params = deep_freeze_json(parameters or {})
        params_hash = _sha(params)
        selection = degradation_selection_identity(
            observation_sha256=dataset.observation_fingerprint, operator=operator,
            operator_version=policy.version, operator_fingerprint=policy.fingerprint,
            severity=level.value, seed=seed, parameters_sha256=params_hash)
        selection_fingerprint = _sha(selection)
        rng = random.Random(selection_fingerprint)
        original, events = dataset.events, list(dataset.events)
        topology = {source: set(targets) for source, targets in dataset.topology.items()}
        input_edges = sum(len(targets) for targets in topology.values())
        removed_indexes: set[int] = set()
        affected_refs: list[Any] = []
        metadata: dict[str, Any] = {}
        eligible = requested = affected = added_events = modified_events = 0
        removed_edges = added_edges = 0
        saturation = None

        def indexes(signal):
            return [index for index, event in enumerate(events)
                    if event.signal_type.value == signal]

        def random_drop(pool):
            nonlocal eligible, requested, affected, removed_indexes, events
            eligible, requested = len(pool), _quantity(len(pool), level.value)
            removed_indexes = set(rng.sample(pool, requested)) if requested else set()
            affected = len(removed_indexes)
            events = [event for index, event in enumerate(events) if index not in removed_indexes]

        if operator in {"random_span_drop", "random_log_drop", "business_event_drop"}:
            random_drop(indexes({"random_span_drop": "trace", "random_log_drop": "log",
                                 "business_event_drop": "business_event"}[operator]))
        elif operator == "critical_path_span_drop":
            pool = [index for index in indexes("trace")
                    if events[index].attributes.get("critical_path") is True]
            random_drop(pool)
            if not pool and level.value:
                saturation = "no-eligible-critical-path-annotated-span"
        elif operator == "error_log_drop":
            pool = [index for index in indexes("log") if _error_log(events[index])]
            random_drop(pool)
            if not pool and level.value:
                saturation = "no-eligible-error-log"
        elif operator == "full_trace_sampling":
            groups: dict[str, list[int]] = {}
            for index in indexes("trace"):
                groups.setdefault(events[index].trace_id or events[index].event_id, []).append(index)
            keys = sorted(groups)
            eligible, requested = len(keys), _quantity(len(keys), level.value)
            chosen = rng.sample(keys, requested) if requested else []
            removed_indexes = {index for key in chosen for index in groups[key]}
            affected = len(chosen)
            affected_refs = [{"trace_id": key, "removed_events": len(groups[key])} for key in sorted(chosen)]
            events = [event for index, event in enumerate(events) if index not in removed_indexes]
        elif operator == "metric_series_drop":
            groups = _metric_groups(events)
            keys = sorted(groups, key=_json)
            eligible, requested = len(keys), _quantity(len(keys), level.value)
            chosen = rng.sample(keys, requested) if requested else []
            removed_indexes = {index for key in chosen for index in groups[key]}
            affected = len(chosen)
            affected_refs = [{"series": list(key), "removed_points": len(groups[key])}
                             for key in sorted(chosen, key=_json)]
            events = [event for index, event in enumerate(events) if index not in removed_indexes]
        elif operator == "metric_downsampling":
            groups = _metric_groups(events)
            removable = {key: _interior_temporal_indexes(events, values)
                         for key, values in groups.items()}
            eligible = sum(len(values) for values in removable.values())
            requested = _quantity(eligible, level.value)
            allocations = _largest_remainder_allocation(
                {key: len(values) for key, values in removable.items()}, requested)
            series_metadata = []
            for key in sorted(groups, key=_json):
                candidates = removable[key]
                count = allocations[key]
                chosen = _evenly_spaced(candidates, count)
                removed_indexes.update(chosen)
                quota = requested * len(candidates) / eligible if eligible else 0.0
                series_metadata.append({"series": list(key), "original_count": len(groups[key]),
                                        "final_count": len(groups[key]) - len(chosen),
                                        "capacity": len(candidates), "ideal_quota": quota,
                                        "allocation": count,
                                        "removed_points": [_reference_dict(original[index])
                                                           for index in chosen]})
            affected = len(removed_indexes)
            metadata["series"] = series_metadata
            metadata["allocation_policy"] = "proportional-largest-remainder-v1"
            if affected < requested:
                saturation = "global-downsampling-capacity-exhausted"
            events = [event for index, event in enumerate(events) if index not in removed_indexes]
        elif operator == "metric_delay":
            pool = indexes("metric")
            eligible, requested = len(pool), _quantity(len(pool), level.value)
            chosen = sorted(rng.sample(pool, requested)) if requested else []
            delay_ms = params.get("delay_ms", 30_000)
            _validate_delay(delay_ms)
            changes = []
            for index in chosen:
                before = events[index].late_arrival_ms
                events[index] = _delay(events[index], delay_ms)
                changes.append({"reference": _reference_dict(original[index]),
                                "before_late_arrival_ms": before,
                                "after_late_arrival_ms": events[index].late_arrival_ms})
            affected = modified_events = len(chosen)
            affected_refs, metadata["delay_changes"] = changes, changes
            metadata["delay_semantics"] = "synthetic metadata; replay scheduling unchanged"
        elif operator == "kafka_message_duplicate":
            eligible, requested = len(events), _quantity(len(events), level.value)
            chosen = sorted(rng.sample(range(len(events)), requested)) if requested else []
            copies = [events[index] for index in chosen]
            events.extend(copies)
            affected = added_events = len(copies)
            affected_refs = [{"reference": _reference_dict(original[index]), "copies": 1}
                             for index in chosen]
        elif operator == "kafka_message_reorder":
            eligible = len(events)
            originally_requested = requested = _quantity(len(events), level.value)
            if requested == 1 and len(events) >= 2:
                requested = 2
                saturation = "requested-one-expanded-to-two-for-materializable-reorder"
            chosen = sorted(rng.sample(range(len(events)), requested)) if requested else []
            if len(chosen) > 1:
                sources = chosen[-1:] + chosen[:-1]
                rotated = [events[index] for index in sources]
                changes = []
                for destination, source, event in zip(chosen, sources, rotated):
                    events[destination] = event
                    changes.append({"reference": _reference_dict(event),
                                    "original_position": source, "final_position": destination})
                affected = modified_events = len(chosen)
                affected_refs, metadata["position_changes"] = changes, changes
            elif requested:
                saturation = "reorder-not-materializable-with-single-message"
        elif operator == "uninstrumented_service":
            services = sorted({event.service_name for event in events if event.service_name})
            eligible, requested = len(services), _quantity(len(services), level.value)
            chosen = sorted(rng.sample(services, requested)) if requested else []
            selected = set(chosen)
            removed_indexes = {index for index, event in enumerate(events)
                               if event.service_name in selected}
            events = [event for index, event in enumerate(events) if index not in removed_indexes]
            affected = len(chosen)
            affected_refs = [{"service_name": service,
                              "removed_events": sum(event.service_name == service for event in original)}
                             for service in chosen]
        elif operator in {"topology_edge_drop", "topology_edge_add"}:
            existing = sorted((source, target) for source, targets in topology.items()
                              for target in targets)
            if operator == "topology_edge_drop":
                eligible, requested = len(existing), _quantity(len(existing), level.value)
                chosen = rng.sample(existing, requested) if requested else []
                for source, target in chosen:
                    topology[source].remove(target)
                affected = removed_edges = len(chosen)
            else:
                services = sorted(set(topology) | {target for _, target in existing} |
                                  {event.service_name for event in events if event.service_name})
                absent = [(source, target) for source in services for target in services
                          if source != target and target not in topology.get(source, set())]
                eligible, requested = len(absent), _quantity(len(absent), level.value)
                chosen = rng.sample(absent, requested) if requested else []
                for source, target in chosen:
                    topology.setdefault(source, set()).add(target)
                affected = added_edges = len(chosen)
            affected_refs = [{"source": source, "target": target}
                             for source, target in sorted(chosen)]

        if level.value and not eligible and saturation is None:
            saturation = "no-eligible-units"
        if not affected_refs and removed_indexes:
            affected_refs = [_reference_dict(original[index]) for index in sorted(removed_indexes)]
        output = ObservationDataset(
            tuple(events), topology, dataset.ground_truth,
            allow_duplicate_references=(operator == "kafka_message_duplicate" and bool(added_events)))
        output_edges = sum(len(targets) for targets in output.topology.values())
        is_topology = operator in {"topology_edge_drop", "topology_edge_add"}
        accounting = DegradationAccountingV3(
            operator, eligible, requested, affected, policy.severity_unit,
            len(original), len(events), len(removed_indexes), added_events, modified_events,
            input_edges if is_topology else 0, output_edges if is_topology else 0,
            removed_edges, added_edges,
            originally_requested if operator == "kafka_message_reorder"
            and originally_requested != requested else None,
            saturation if operator == "kafka_message_reorder"
            and originally_requested != requested else None)
        if level.value == 0:
            if output.observation_fingerprint != dataset.observation_fingerprint:
                raise RuntimeError("Invariante 0% violada: observação foi modificada.")
            if output.dataset_fingerprint != dataset.dataset_fingerprint:
                raise RuntimeError("Invariante 0% violada: dataset foi modificado.")
            if output.ground_truth_fingerprint != dataset.ground_truth_fingerprint:
                raise RuntimeError("Invariante 0% violada: ground truth foi modificado.")
            alteration_counts = (
                accounting.requested_units, accounting.affected_units,
                accounting.removed_event_count, accounting.added_event_count,
                accounting.modified_event_count,
                accounting.removed_topology_edge_count,
                accounting.added_topology_edge_count,
            )
            if any(alteration_counts) or affected_refs:
                raise RuntimeError("Invariante 0% violada: alterações foram contabilizadas.")
        removed_by = {signal: 0 for signal in MODALITIES}
        for index in removed_indexes:
            removed_by[original[index].signal_type.value] += 1
        removed_by["topology"] = removed_edges
        missing = tuple(signal for signal in MODALITIES if _modality_count(output, signal) == 0)
        identity = run_identity(
            input_dataset_sha256=dataset.dataset_fingerprint,
            ground_truth_sha256=dataset.ground_truth_fingerprint,
            selection_identity=selection)
        run_id = derive_run_id(identity)
        manifest = DegradationManifest(
            run_id, operator, policy.version, policy.fingerprint, policy.modalities,
            level.value, seed, dataset.dataset_fingerprint,
            dataset.observation_fingerprint, output.dataset_fingerprint,
            output.observation_fingerprint, dataset.ground_truth_fingerprint,
            selection_fingerprint, identity, params_hash, params, accounting,
            removed_by, tuple(affected_refs), metadata, missing, saturation)
        return DegradationRun(output, manifest)

    @staticmethod
    def replay_offline(run: DegradationRun) -> tuple[dict[str, Any], ...]:
        return tuple(event.to_dict() for event in run.dataset.events)

    @staticmethod
    def replay_kafka(run: DegradationRun, publisher: KafkaReplayPublisher,
                     topic: str) -> KafkaReplayResult:
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("topic deve ser texto não vazio.")
        confirmed, failures = 0, []
        for event in run.dataset.events:
            try:
                acknowledgement = publisher.send(
                    topic, key=event.event_id.encode(), value=event.to_json().encode())
                if hasattr(acknowledgement, "get"):
                    acknowledgement.get()
                confirmed += 1
            except Exception as exc:
                failures.append(f"{event.event_id}:{type(exc).__name__}")
        return KafkaReplayResult(len(run.dataset.events), confirmed, len(failures),
                                 tuple(failures))


def _reference_dict(event: UnifiedEvent) -> dict[str, Any]:
    return {"event_id": event.event_id, "source_reference": event.source_reference}


def _metric_groups(events):
    groups = {}
    for index, event in enumerate(events):
        if event.signal_type.value == "metric":
            groups.setdefault(_series_key(event), []).append(index)
    return groups


def _interior_temporal_indexes(events, indexes):
    ordered = sorted(indexes, key=lambda index: _event_order(events[index]))
    return ordered[1:-1] if len(ordered) > 2 else []


def _evenly_spaced(indexes, count):
    if count <= 0:
        return []
    if count >= len(indexes):
        return list(indexes)
    positions = [math.floor((step + .5) * len(indexes) / count) for step in range(count)]
    return [indexes[min(position, len(indexes) - 1)] for position in positions]


def _largest_remainder_allocation(capacities: Mapping[tuple[Any, ...], int],
                                  requested: int) -> dict[tuple[Any, ...], int]:
    total = sum(capacities.values())
    if requested < 0 or requested > total:
        raise ValueError("requested deve estar entre zero e a capacidade global.")
    quotas = {key: (requested * capacity / total if total else 0.0)
              for key, capacity in capacities.items()}
    allocated = {key: min(capacities[key], math.floor(quota))
                 for key, quota in quotas.items()}
    remaining = requested - sum(allocated.values())
    order = sorted(capacities, key=lambda key: (-(quotas[key] - math.floor(quotas[key])),
                                                _json(key)))
    while remaining:
        progressed = False
        for key in order:
            if allocated[key] < capacities[key]:
                allocated[key] += 1
                remaining -= 1
                progressed = True
                if not remaining:
                    break
        if not progressed:
            break
    return allocated


def _error_log(event: UnifiedEvent) -> bool:
    severity = (event.severity or "").upper()
    status = str(event.status_code or "").upper()
    attributes = event.attributes
    return (severity in {"ERROR", "CRITICAL", "FATAL"} or status == "ERROR"
            or bool(event.error_type) or bool(attributes.get("exception.type"))
            or bool(attributes.get("error.type")))


def _validate_delay(delay_ms):
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or delay_ms < 0:
        raise ValueError("delay_ms deve ser inteiro não negativo.")


def _delay(event: UnifiedEvent, delay_ms: int) -> UnifiedEvent:
    _validate_delay(delay_ms)
    payload = event.to_dict()
    payload["late_arrival_ms"] = (event.late_arrival_ms or 0) + delay_ms
    flags = set(payload.get("data_quality_flags", ()))
    flags.add("synthetic_metric_delay")
    payload["data_quality_flags"] = sorted(flags)
    return UnifiedEvent.from_dict(payload)


def _modality_count(dataset: ObservationDataset, modality: str) -> int:
    if modality == "topology":
        return sum(len(value) for value in dataset.topology.values())
    return sum(event.signal_type.value == modality for event in dataset.events)


def _probability_mapping(value, name):
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} deve ser mapping.")
    required = ("top_1", "top_3", "mrr", "fpr")
    if set(value) != set(required):
        raise ValueError(f"{name} deve conter exatamente {list(required)}.")
    result = {}
    for field in required:
        item = value.get(field)
        if (isinstance(item, bool) or not isinstance(item, (int, float))
                or not math.isfinite(item) or not 0 <= item <= 1):
            raise ValueError(f"{name}.{field} deve ser finito em [0,1].")
        result[field] = float(item)
    return result


def _optional_duration(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} deve ser finito não negativo ou None.")


def degradation_metrics(baseline: Mapping[str, float], observed: Mapping[str, float],
                        manifest: DegradationManifest, *, confidence_values: Iterable[float] = (),
                        outcomes: Iterable[bool] = (), abstentions: int = 0,
                        recovery_time_ms: float | None = None,
                        processing_latency_ms: float | None = None) -> dict[str, Any]:
    if not isinstance(manifest, DegradationManifest):
        raise TypeError("manifest deve ser DegradationManifest.")
    base, current = _probability_mapping(baseline, "baseline"), _probability_mapping(observed, "observed")
    confidences, truth = tuple(confidence_values), tuple(outcomes)
    if len(confidences) != len(truth) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1 for value in confidences):
        raise ValueError("confidence_values deve conter valores finitos em [0,1].")
    if any(not isinstance(value, bool) for value in truth):
        raise TypeError("outcomes deve conter somente booleanos.")
    if (isinstance(abstentions, bool) or not isinstance(abstentions, int)
            or abstentions < 0):
        raise ValueError("abstentions deve ser inteiro não negativo.")
    _optional_duration(recovery_time_ms, "recovery_time_ms")
    _optional_duration(processing_latency_ms, "processing_latency_ms")
    calibration = (statistics.fmean(abs(float(correct) - confidence)
                                    for confidence, correct in zip(confidences, truth))
                   if confidences else None)
    delta = {name: current[name] - base[name] for name in base}
    percent_change = {name: ((current[name] - base[name]) / abs(base[name]) * 100
                             if base[name] else None) for name in base}
    performance_degradation = {}
    undefined_reasons = {}
    for name, direction in METRIC_DIRECTIONS.items():
        if base[name] == 0:
            performance_degradation[name] = None
            undefined_reasons[name] = "baseline_zero"
        elif direction == "higher_is_better":
            performance_degradation[name] = (base[name] - current[name]) / base[name] * 100
        else:
            performance_degradation[name] = (current[name] - base[name]) / base[name] * 100
    accounting = manifest.accounting
    return {**current, "confidence": statistics.fmean(confidences) if confidences else None,
            "calibration": calibration,
            "abstention": abstentions / (len(truth) + abstentions)
            if truth or abstentions else 0.0,
            "recovery_time_ms": recovery_time_ms,
            "processing_latency_ms": processing_latency_ms,
            "delta_from_0pct": delta, "percent_change_from_0pct": percent_change,
            "performance_degradation_percent": performance_degradation,
            "performance_degradation_undefined_reasons": undefined_reasons,
            "metric_directions": dict(METRIC_DIRECTIONS),
            "operator": manifest.operator, "requested_severity": manifest.severity,
            "realized_severity": manifest.realized_severity,
            "unit_type": accounting.unit_type,
            "event_loss_ratio": accounting.event_loss_ratio,
            "event_addition_ratio": accounting.event_addition_ratio,
            "event_modification_ratio": accounting.event_modification_ratio,
            "topology_edge_loss_ratio": accounting.topology_edge_loss_ratio,
            "topology_edge_addition_ratio": accounting.topology_edge_addition_ratio}
