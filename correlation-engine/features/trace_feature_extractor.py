class TraceFeatureExtractor:

    def extract(self, trace):

        features = {}

        for service in trace.services:

            spans = [

                span

                for span in trace.spans.values()

                if span.service_name == service

            ]

            request_count = len(spans)

            error_count = sum(

                1

                for span in spans

                if span.status_code == "ERROR"

            )

            total_latency = sum(

                span.duration_ms

                for span in spans

            )

            max_latency = max(

                (
                    span.duration_ms
                    for span in spans
                ),

                default=0

            )

            features[service] = {

                "request_count":
                    request_count,

                "error_count":
                    error_count,

                "error_rate":
                    (
                        error_count /
                        request_count
                    )
                    if request_count
                    else 0,

                "avg_latency_ms":
                    (
                        total_latency /
                        request_count
                    )
                    if request_count
                    else 0,

                "max_latency_ms":
                    max_latency

            }

        return features