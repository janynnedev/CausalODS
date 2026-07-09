class RCAFeatureExtractor:



    def extract(
        self,
        graph,
        temporal_result
    ):


        features = []


        for name,node in graph.nodes.items():


            service_features = {


                "service":
                    name,


                "request_count":
                    node.request_count,


                "error_count":
                    node.error_count,


                "error_rate":
                    (
                    node.error_count /
                    node.request_count
                    )
                    if node.request_count
                    else 0,


                "incoming_calls":
                    len(node.called_by),


                "outgoing_calls":
                    len(node.calls),


                "is_first_failure":
                    (
                    temporal_result[
                    "first_failure"
                    ]
                    and
                    temporal_result[
                    "first_failure"
                    ]["service"]
                    == name
                    )

            }


            features.append(
                service_features
            )


        return features