from models.service_graph import ServiceGraph



class GraphBuilder:


    def build(self, trace):

        graph = ServiceGraph()


        for span in trace.spans.values():


            graph.add_service(
                span.service_name
            )


            graph.nodes[
                span.service_name
            ].request_count += 1



            if span.status_code == "ERROR":

                graph.nodes[
                    span.service_name
                ].error_count += 1



            for child_id in span.children:


                child = trace.spans[
                    child_id
                ]


                graph.add_dependency(

                    span.service_name,

                    child.service_name,

                    child.duration_ms,

                    child.status_code == "ERROR"

                )


        return graph