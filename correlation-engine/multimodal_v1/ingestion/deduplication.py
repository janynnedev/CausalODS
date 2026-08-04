from __future__ import annotations

from collections import OrderedDict
from threading import Lock

DeduplicationKey = tuple[str, str]


class EventDeduplicator:
    def __init__(self, capacity: int = 100_000) -> None:
        if capacity < 1:
            raise ValueError("capacity deve ser positiva.")
        self._capacity = capacity
        self._seen: OrderedDict[tuple[str, str | None], None] = OrderedDict()
        self._lock = Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def contains(self, key: DeduplicationKey) -> bool:
        with self._lock:
            if key in self._seen:
                return True
            return False

    def confirm(self, keys: tuple[DeduplicationKey, ...]) -> None:
        with self._lock:
            for key in keys:
                self._seen[key] = None
                self._seen.move_to_end(key)
                if len(self._seen) > self._capacity:
                    self._seen.popitem(last=False)

    def snapshot(self) -> tuple[DeduplicationKey, ...]:
        with self._lock:
            return tuple(self._seen)
