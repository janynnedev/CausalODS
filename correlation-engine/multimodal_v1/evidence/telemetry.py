from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class AssemblerTelemetry:
    _values: dict[str, float] = field(default_factory=lambda: {
        "input_events": 0, "associated_events": 0, "unassociated_events": 0,
        "duplicate_events": 0, "closed_bundles": 0, "reopened_bundles": 0,
        "expired_bundles": 0, "memory_limit_rejections": 0,
        "association_latency_ms_total": 0.0, "association_latency_ms_max": 0.0,
        "association_latency_samples": 0, "bundle_size": 0, "active_bundles": 0,
    })
    _lock: Lock = field(default_factory=Lock)

    def increment(self, name: str, value: float = 1) -> None:
        with self._lock:
            self._values[name] = self._values.get(name, 0) + value

    def observe_latency(self, milliseconds: float) -> None:
        with self._lock:
            self._values["association_latency_ms_total"] += milliseconds
            self._values["association_latency_samples"] += 1
            self._values["association_latency_ms_max"] = max(
                self._values["association_latency_ms_max"], milliseconds
            )

    def gauges(self, *, bundle_size: int, active_bundles: int) -> None:
        with self._lock:
            self._values["bundle_size"] = bundle_size
            self._values["active_bundles"] = active_bundles

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            result = dict(self._values)
        samples = result["association_latency_samples"]
        result["association_latency_ms_average"] = (
            result["association_latency_ms_total"] / samples if samples else 0.0
        )
        return result

