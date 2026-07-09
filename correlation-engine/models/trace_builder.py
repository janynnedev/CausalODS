from typing import Dict

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
        normalized_span: dict
    ):

        trace_id = (
            normalized_span["trace_id"]
        )


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



        span = SpanNode(

            span_id =
                normalized_span["span_id"],

            parent_span_id =
                normalized_span.get(
                    "parent_span_id"
                ),

            service_name =
                normalized_span[
                    "service_name"
                ],

            operation_name =
                normalized_span[
                    "operation_name"
                ],


            start_time =
                normalized_span[
                    "start_time"
                ],


            end_time =
                normalized_span[
                    "end_time"
                ],


            duration_ms =
                normalized_span[
                    "duration_ms"
                ],


            status_code =
                normalized_span[
                    "status"
                ],


            attributes =
                normalized_span.get(
                    "attributes",
                    {}
                )
        )


        trace.add_span(span)


        trace.connect_parent_child()


        return trace



    def get_trace(
        self,
        trace_id:str
    ):

        return self.active_traces.get(
            trace_id
        )



    def finish_trace(
        self,
        trace_id:str
    ):

        trace = (
            self.active_traces.pop(
                trace_id,
                None
            )
        )

        return trace