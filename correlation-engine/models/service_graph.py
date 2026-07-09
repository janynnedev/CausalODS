from dataclasses import dataclass, field
from typing import Dict, List



@dataclass
class EdgeMetrics:

    source: str

    target: str

    call_count: int = 0

    total_latency_ms: float = 0

    error_count: int = 0



    @property
    def avg_latency_ms(self):

        if self.call_count == 0:
            return 0

        return (
            self.total_latency_ms /
            self.call_count
        )



    @property
    def error_rate(self):

        if self.call_count == 0:
            return 0

        return (
            self.error_count /
            self.call_count
        )



@dataclass
class ServiceNode:


    name: str

    calls: List[str] = field(
        default_factory=list
    )

    called_by: List[str] = field(
        default_factory=list
    )

    request_count: int = 0

    error_count: int = 0



@dataclass
class ServiceGraph:


    nodes: Dict[
        str,
        ServiceNode
    ] = field(
        default_factory=dict
    )


    edges: Dict[
        str,
        EdgeMetrics
    ] = field(
        default_factory=dict
    )



    def add_service(
        self,
        service_name:str
    ):

        if service_name not in self.nodes:

            self.nodes[service_name] = ServiceNode(
                name=service_name
            )



    def add_dependency(
        self,
        source:str,
        target:str,
        latency_ms:float,
        error:bool
    ):


        self.add_service(source)
        self.add_service(target)



        if target not in self.nodes[source].calls:

            self.nodes[source].calls.append(
                target
            )


        if source not in self.nodes[target].called_by:

            self.nodes[target].called_by.append(
                source
            )



        edge_key = (
            f"{source}->{target}"
        )


        if edge_key not in self.edges:

            self.edges[edge_key] = EdgeMetrics(
                source,
                target
            )


        edge = self.edges[edge_key]


        edge.call_count += 1

        edge.total_latency_ms += latency_ms


        if error:
            edge.error_count += 1



    def to_dict(self):

        return {

            "nodes":
            {
                name:{
                    "requests":node.request_count,
                    "errors":node.error_count
                }

                for name,node
                in self.nodes.items()
            },


            "edges":
            {

                key:{

                    "calls":edge.call_count,

                    "avg_latency":
                        edge.avg_latency_ms,

                    "error_rate":
                        edge.error_rate

                }

                for key,edge
                in self.edges.items()

            }

        }