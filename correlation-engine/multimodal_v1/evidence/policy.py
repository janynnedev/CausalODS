from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.evidence.contracts import ServiceIdentity, utc


class ServiceIdentityResolver:
    """Resolve identidade sem inventar dependências com o grafo trace-only."""

    def resolve(self, event: UnifiedEvent) -> ServiceIdentity:
        name = event.service_name
        if not name:
            candidate = event.resource_attributes.get("service.name")
            name = candidate if isinstance(candidate, str) and candidate.strip() else "unknown-service"
        instance = event.service_instance_id
        if not instance:
            candidate = event.resource_attributes.get("service.instance.id")
            instance = candidate if isinstance(candidate, str) and candidate.strip() else None
        return ServiceIdentity(name, instance)


@dataclass(frozen=True)
class IncidentWindowPolicy:
    association_window: timedelta = timedelta(minutes=5)
    allowed_lateness: timedelta = timedelta(seconds=30)
    reopen_window: timedelta = timedelta(minutes=2)
    expiration: timedelta = timedelta(minutes=15)
    max_active_incidents: int = 1_000
    max_events_per_incident: int = 10_000
    max_total_events: int = 100_000
    association_threshold: float = 0.25
    reopen_threshold: float = 0.70
    ambiguity_margin: float = 0.01
    max_log_samples: int = 100

    def __post_init__(self) -> None:
        durations = (self.association_window, self.allowed_lateness, self.reopen_window, self.expiration)
        if any(value < timedelta(0) for value in durations):
            raise ValueError("Janelas temporais não podem ser negativas.")
        if min(self.max_active_incidents, self.max_events_per_incident, self.max_total_events) < 1:
            raise ValueError("Limites de memória devem ser positivos.")
        for name in ("association_threshold", "reopen_threshold", "ambiguity_margin"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"{name} deve estar entre 0 e 1.")
        if self.reopen_threshold < self.association_threshold:
            raise ValueError("reopen_threshold deve ser >= association_threshold.")
        if isinstance(self.max_log_samples, bool) or not isinstance(self.max_log_samples, int) or self.max_log_samples < 1:
            raise ValueError("max_log_samples deve ser positivo.")

    def temporal_score(self, event_at: datetime, timestamps: Iterable[datetime]) -> float:
        distances = [abs((utc(event_at, "event_timestamp") - value).total_seconds()) for value in timestamps]
        if not distances or self.association_window.total_seconds() == 0:
            return 0.0
        distance = min(distances)
        limit = self.association_window.total_seconds()
        return max(0.0, 1.0 - distance / limit)


class KnownTopology:
    """Topologia não direcionada mínima, aceita mapping ou pares de serviços."""

    def __init__(self, topology: Mapping[str, Iterable[str]] | Iterable[tuple[str, str]] | None = None):
        self._edges: set[frozenset[str]] = set()
        if isinstance(topology, Mapping):
            pairs = ((source, target) for source, targets in topology.items() for target in targets)
        else:
            pairs = topology or ()
        for source, target in pairs:
            if isinstance(source, str) and isinstance(target, str) and source != target:
                self._edges.add(frozenset((source, target)))

    def related(self, source: str, target: str) -> bool:
        return source == target or frozenset((source, target)) in self._edges
