from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class NormalizedSpan:

    trace_id: str

    span_id: str

    parent_span_id: str

    service: str

    operation: str

    kind: str

    start_time: int

    end_time: int

    duration_ms: float

    status: str

    attributes: Dict[str, Any] = field(default_factory=dict)