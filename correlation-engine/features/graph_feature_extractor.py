class GraphFeatureExtractor:


    def extract(self, graph):

        features = {}


        for name,node in graph.nodes.items():


            features[name] = {

                "incoming_dependencies":
                    len(node.called_by),


                "outgoing_dependencies":
                    len(node.calls)

            }


        return features