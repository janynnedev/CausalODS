from dataclasses import dataclass, field

from .span import Span


@dataclass

class Trace:

    trace_id: str

    spans: list[Span] = field(default_factory=list)

    def add_span(self, span: Span):

        self.spans.append(span)