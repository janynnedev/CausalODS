class GraphFeatureExtractor:

    def extract(
        self,
        graph,
    ):

        graph_features = {}

        for service_name in graph.nodes:

            incoming_services = {
                edge.source
                for edge in graph.edges.values()
                if edge.target == service_name
            }

            outgoing_services = {
                edge.target
                for edge in graph.edges.values()
                if edge.source == service_name
            }

            incoming_dependencies = len(
                incoming_services
            )

            outgoing_dependencies = len(
                outgoing_services
            )

            graph_features[
                service_name
            ] = {
                "incoming_dependencies":
                    incoming_dependencies,

                "outgoing_dependencies":
                    outgoing_dependencies,

                "dependency_count":
                    (
                        incoming_dependencies
                        + outgoing_dependencies
                    ),
            }

        return graph_features