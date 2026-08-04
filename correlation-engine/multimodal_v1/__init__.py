"""Contratos multimodais v1, isolados da pipeline trace-only v2."""

from multimodal_v1.contracts import (
    DerivedEvidence,
    SignalType,
    UnifiedEvent,
)
from multimodal_v1.span_adapter import SpanToUnifiedEventAdapter

__all__ = [
    "DerivedEvidence",
    "SignalType",
    "SpanToUnifiedEventAdapter",
    "UnifiedEvent",
]
