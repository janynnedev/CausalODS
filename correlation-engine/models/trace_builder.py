from typing import Dict

from models.span import Span

from models.trace_state import (
    TraceState,
    SpanNode
)


class TraceBuilder:
    """
    Reconstrói traces distribuídos
    a partir dos spans normalizados.
    """


    def __init__(self):

        self.active_traces: Dict[
            str,
            TraceState
        ] = {}



    def process_span(
        self,
        span: Span
    ):

        trace_id = span.trace_id


        # cria trace caso não exista

        if trace_id not in self.active_traces:

            self.active_traces[
                trace_id
            ] = TraceState(
                trace_id=trace_id
            )


        trace = self.active_traces[
            trace_id
        ]


        span_node = SpanNode(

            span_id=
                span.span_id,


            parent_span_id=
                span.parent_span_id,


            service_name=
                span.service_name,


            operation_name=
                span.operation_name,


            start_time=
                self._to_datetime(
                    span.start_time
                ),


            end_time=
                self._to_datetime(
                    span.end_time
                ),


            duration_ms=
                span.duration_ns / 1_000_000,


            status_code=
                span.status_code,


            attributes={}
        )


        trace.add_span(
            span_node
        )


        trace.connect_parent_child()


        return trace



    def _to_datetime(
        self,
        timestamp_ns: int
    ):

        from datetime import datetime, timezone


        return datetime.fromtimestamp(
            timestamp_ns / 1_000_000_000,
            tz=timezone.utc
        )



    def get_trace(
        self,
        trace_id: str
    ):

        return self.active_traces.get(
            trace_id
        )



    def finish_trace(
        self,
        trace_id: str
    ):

        return self.active_traces.pop(
            trace_id,
            None
        )