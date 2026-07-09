from datetime import datetime

from models.trace_builder import TraceBuilder



builder = TraceBuilder()


builder.process_span({

    "trace_id":"trace01",

    "span_id":"root",

    "parent_span_id":None,

    "service_name":"order-service",

    "operation_name":"createOrder",

    "start_time":
        datetime.now(),

    "end_time":
        datetime.now(),

    "duration_ms":100,

    "status":"OK"

})


trace = builder.get_trace(
    "trace01"
)


print(
    trace.to_dict()
)