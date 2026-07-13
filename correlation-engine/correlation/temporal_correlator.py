from datetime import datetime



class TemporalCorrelator:


    def analyze(self, trace):

        timeline = self.build_service_timeline(
            trace
        )

        first_failure = (
            self.detect_first_failure(
                timeline
            )
        )

        latency_order = (
            self.compute_latency_order(
                timeline
            )
        )

        propagation = (
            self.compute_propagation(
                trace
            )
        )

        service_depth = (
            self.compute_service_depth(
                trace
            )
        )

        return {

            "timeline":
                timeline,

            "first_failure":
                first_failure,

            "latency_order":
                latency_order,

            "propagation":
                propagation,

            "service_depth":
                service_depth
        }


    def build_service_timeline(self, trace):

        timeline = []

        for span in trace.spans.values():

            timeline.append({

                "service":
                    span.service_name,

                "operation":
                    span.operation_name,

                "span_id":
                    span.span_id,

                "parent":
                    span.parent_span_id,

                "start":
                    span.start_time,

                "end":
                    span.end_time,

                "duration":
                    span.duration_ms,

                "error":
                    span.status_code == "ERROR"
            })

        timeline.sort(
            key=lambda item: item["start"]
        )

        return timeline


    def detect_first_failure(self, timeline):

        for item in timeline:

            if item["error"]:

                return item

        return None


    def compute_latency_order(self, timeline):

        ordered = sorted(

            timeline,

            key=lambda item: item["duration"],

            reverse=True

        )

        return [

            item["service"]

            for item in ordered

        ]


    def compute_propagation(self, trace):

        propagation = defaultdict(list)

        for span in trace.spans.values():

            if not span.parent_span_id:
                continue

            parent = trace.spans.get(
                span.parent_span_id
            )

            if not parent:
                continue

            if (

                span.service_name

                not in

                propagation[parent.service_name]

            ):

                propagation[
                    parent.service_name
                ].append(

                    span.service_name

                )

        return dict(propagation)


    def compute_service_depth(self, trace):

        depth = {}

        visited = set()

        root = trace.root_span_id

        if root is None:

            return depth


        def visit(span_id, level):

            if span_id in visited:
                return

            visited.add(span_id)

            span = trace.spans[span_id]

            depth[
                span.service_name
            ] = level

            for child in span.children:

                visit(
                    child,
                    level + 1
                )


        visit(root, 0)

        return depth