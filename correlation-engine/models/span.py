from dataclasses import dataclass
from typing import Optional


@dataclass
class Span:

    trace_id: str

    span_id: str

    parent_span_id: Optional[str]

    service_name: str

    operation_name: str

    start_time: int

    end_time: int

    duration_ns: int

    status_code: str