from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class SpanNode:
    """
    Representa um span individual dentro do trace.
    """

    span_id: str
    parent_span_id: Optional[str]

    service_name: str
    operation_name: str

    start_time: datetime
    end_time: datetime

    duration_ms: float

    status_code: str

    attributes: Dict = field(default_factory=dict)

    children: List[str] = field(default_factory=list)


@dataclass
class TraceState:
    """
    Estado reconstruído de um trace distribuído.
    """

    trace_id: str

    spans: Dict[str, SpanNode] = field(default_factory=dict)

    root_span_id: Optional[str] = None

    services: List[str] = field(default_factory=list)

    start_time: Optional[datetime] = None

    end_time: Optional[datetime] = None


    def add_span(self, span: SpanNode):
        """
        Adiciona um novo span ao trace.
        """

        self.spans[span.span_id] = span


        # identifica root span
        if span.parent_span_id is None:
            self.root_span_id = span.span_id


        # registra serviços únicos
        if span.service_name not in self.services:
            self.services.append(span.service_name)


        # atualiza janela temporal

        if (
            self.start_time is None
            or span.start_time < self.start_time
        ):
            self.start_time = span.start_time


        if (
            self.end_time is None
            or span.end_time > self.end_time
        ):
            self.end_time = span.end_time



    def connect_parent_child(self):
        """
        Reconstrói a árvore do trace.
        """

        for span in self.spans.values():

            if span.parent_span_id:

                parent = self.spans.get(
                    span.parent_span_id
                )

                if parent:

                    if span.span_id not in parent.children:
                        parent.children.append(
                            span.span_id
                        )


    def get_duration_ms(self):

        if not self.start_time or not self.end_time:
            return 0


        delta = (
            self.end_time -
            self.start_time
        )

        return delta.total_seconds() * 1000



    def get_error_count(self):

        return sum(
            1
            for span in self.spans.values()
            if span.status_code == "ERROR"
        )



    def to_dict(self):

        return {

            "trace_id": self.trace_id,

            "root_span":
                self.root_span_id,

            "services":
                self.services,

            "span_count":
                len(self.spans),

            "duration_ms":
                self.get_duration_ms(),

            "errors":
                self.get_error_count(),

            "spans":
                {
                    sid: {

                        "service":
                            span.service_name,

                        "operation":
                            span.operation_name,

                        "children":
                            span.children

                    }

                    for sid, span
                    in self.spans.items()
                }
        }