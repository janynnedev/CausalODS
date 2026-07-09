from datetime import datetime

from models.trace_builder import TraceBuilder
from graph.graph_builder import GraphBuilder



builder = TraceBuilder()


builder.process_span({

    "trace_id":"trace01",

    "span_id":"root",

    "parent_span_id":None,

    "service_name":
        "order-service",

    "operation_name":
        "createOrder",

    "start_time":
        datetime.now(),

    "end_time":
        datetime.now(),

    "duration_ms":100,

    "status":
        "OK"

})



builder.process_span({

    "trace_id":"trace01",

    "span_id":"payment",

    "parent_span_id":
        "root",

    "service_name":
        "payment-service",

    "operation_name":
        "pay",

    "start_time":
        datetime.now(),

    "end_time":
        datetime.now(),

    "duration_ms":200,

    "status":
        "ERROR"

})



trace = builder.get_trace(
    "trace01"
)


graph_builder = GraphBuilder()


graph = graph_builder.build(
    trace
)


print(
    graph.to_dict()
)