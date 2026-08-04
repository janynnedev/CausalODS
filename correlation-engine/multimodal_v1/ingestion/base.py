from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from multimodal_v1.contracts import UnifiedEvent
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.errors import IngestionError, SemanticInputError


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
        try:
            return tuple(self.normalize(self.decode(payload), context))
        except IngestionError:
            raise
        except ValueError as exception:
            raise SemanticInputError(str(exception)) from exception
