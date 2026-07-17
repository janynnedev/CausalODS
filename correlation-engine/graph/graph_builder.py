from models.service_graph import ServiceGraph


class GraphBuilder:

    def build(
        self,
        trace,
    ):

        graph = ServiceGraph()

        for span in trace.spans.values():

            node = graph.add_service(
                span.service_name
            )

            node.request_count += 1

            node.total_latency_ms += (
                span.duration_ms
            )

            node.max_latency_ms = max(
                node.max_latency_ms,
                span.duration_ms,
            )

            if span.status_code == "ERROR":

                node.error_count += 1

            for child_id in span.children:

                child = trace.spans[
                    child_id
                ]

                graph.add_dependency(
                    span.service_name,
                    child.service_name,
                    child.duration_ms,
                    child.status_code == "ERROR",
                )

        return graph