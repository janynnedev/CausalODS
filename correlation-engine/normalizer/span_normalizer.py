from datetime import datetime

from models.span import Span


class SpanNormalizer:


    def normalize(self, request):

        spans = []

        for resource in request.resource_spans:

            service_name = self._extract_service_name(resource)

            for scope in resource.scope_spans:

                for span in scope.spans:

                    start_time = span.start_time_unix_nano
                    end_time = span.end_time_unix_nano

                    normalized = Span(
                        trace_id = span.trace_id.hex(),
                        span_id = span.span_id.hex(),
                        parent_span_id = self._parent_id(span),
                        service_name = service_name,
                        operation_name = span.name,
                        start_time = start_time,
                        end_time = end_time,
                        duration_ns = end_time - start_time,
                        status_code = self._status(span)
                    )
                    spans.append(normalized)
        return spans

    def _extract_service_name(self, resource):
        for attribute in resource.resource.attributes:
            if attribute.key == "service.name":
                return attribute.value.string_value
            
        return "unknown"

    def _parent_id(self, span):

        if not span.parent_span_id:
            return None

        return span.parent_span_id.hex()

    def _status(self, span):
        code = span.status.code

        if code == 2:
            return "ERROR"
        return "OK"