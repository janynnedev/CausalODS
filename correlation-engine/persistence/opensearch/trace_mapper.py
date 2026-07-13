class OpenSearchTraceMapper:


    def map(self, trace):

        return {

            "trace_id": trace["trace_id"],

            "root_span": trace["root_span"],

            "services": trace["services"],

            "span_count": trace["span_count"],

            "duration_ms": trace["duration_ms"],

            "errors": trace["errors"],

            "spans": trace["spans"]

        }