from __future__ import annotations

from collections import Counter
from threading import Lock


COUNTERS = (
    "received", "normalized", "rejected", "late", "duplicates",
    "internal_error", "delivered", "committed",
)


class IngestionMetrics:
    """Contadores internos sem acoplar a ingestão ao Prometheus/runtime v2."""

    def __init__(self) -> None:
        self._values: Counter[tuple[str, str]] = Counter()
        self._lock = Lock()

    def increment(self, counter: str, signal_type: str, amount: int = 1) -> None:
        if counter not in COUNTERS:
            raise ValueError(f"Contador desconhecido: {counter}.")
        with self._lock:
            self._values[(counter, signal_type)] += amount

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                counter: {
                    signal: value
                    for (name, signal), value in sorted(self._values.items())
                    if name == counter
                }
                for counter in COUNTERS
            }
