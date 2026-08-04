from __future__ import annotations

from multimodal_v1.contracts import SignalType
from multimodal_v1.ingestion.base import EventNormalizer
from multimodal_v1.ingestion.business_normalizer import BusinessEventNormalizer
from multimodal_v1.ingestion.errors import UnsupportedSignalError
from multimodal_v1.ingestion.log_normalizer import LogEventNormalizer
from multimodal_v1.ingestion.metric_normalizer import MetricEventNormalizer
from multimodal_v1.ingestion.trace_normalizer import TraceEventNormalizer


class NormalizerRegistry:
    def __init__(self) -> None:
        self._normalizers: dict[SignalType, EventNormalizer] = {}

    def register(self, signal_type: SignalType | str, normalizer: EventNormalizer) -> None:
        self._normalizers[SignalType(signal_type)] = normalizer

    def get(self, signal_type: SignalType | str) -> EventNormalizer:
        try:
            normalized = SignalType(signal_type)
            return self._normalizers[normalized]
        except (ValueError, KeyError) as exception:
            raise UnsupportedSignalError(
                f"signal_type não registrado: {signal_type!r}."
            ) from exception


def default_registry() -> NormalizerRegistry:
    registry = NormalizerRegistry()
    registry.register(SignalType.TRACE, TraceEventNormalizer())
    registry.register(SignalType.LOG, LogEventNormalizer())
    registry.register(SignalType.METRIC, MetricEventNormalizer())
    registry.register(SignalType.BUSINESS_EVENT, BusinessEventNormalizer())
    return registry
