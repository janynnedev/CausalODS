from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.context import NormalizationContext


class EventNormalizer(ABC):
    @abstractmethod
    def decode(self, payload: bytes):
        raise NotImplementedError

    @abstractmethod
    def normalize(self, decoded, context: NormalizationContext) -> Sequence[UnifiedEvent]:
        raise NotImplementedError

    def decode_and_normalize(
        self, payload: bytes, context: NormalizationContext
    ) -> tuple[UnifiedEvent, ...]:
        return tuple(self.normalize(self.decode(payload), context))
