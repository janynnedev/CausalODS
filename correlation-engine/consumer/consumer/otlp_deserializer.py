from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest
)


class OTLPDeserializer:

    def deserialize(self, payload: bytes):

        request = ExportTraceServiceRequest()

        request.ParseFromString(payload)

        return request