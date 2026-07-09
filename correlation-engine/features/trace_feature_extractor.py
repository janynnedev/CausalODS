class TraceFeatureExtractor:


    def extract(self, trace):

        features = {}


        for service in trace.services:

            spans = [

                s

                for s in trace.spans.values()

                if s.service_name == service

            ]


            request_count = len(spans)


            errors = sum(

                1

                for s in spans

                if s.status_code == "ERROR"

            )


            latency = sum(

                s.duration_ms

                for s in spans

            )


            features[service] = {

                "request_count":
                    request_count,


                "error_count":
                    errors,


                "error_rate":
                    errors / request_count
                    if request_count else 0,


                "avg_latency_ms":
                    latency / request_count
                    if request_count else 0

            }


        return features