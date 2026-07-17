from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ServiceNode:

    service_name: str

    request_count: int = 0

    error_count: int = 0

    total_latency_ms: float = 0

    max_latency_ms: float = 0

    attributes: Dict = field(default_factory=dict)


    @property
    def average_latency(self):

        if self.request_count == 0:
            return 0

        return (
            self.total_latency_ms /
            self.request_count
        )


    @property
    def error_rate(self):

        if self.request_count == 0:
            return 0

        return (
            self.error_count /
            self.request_count
        )
    

@dataclass
class DependencyEdge:

    source: str

    target: str

    call_count: int = 0

    error_count: int = 0

    total_latency_ms: float = 0


    @property
    def average_latency(self):

        if self.call_count == 0:
            return 0

        return (
            self.total_latency_ms /
            self.call_count
        )
    

class ServiceGraph:


    def __init__(self):

        self.nodes: Dict[
            str,
            ServiceNode
        ] = {}

        self.edges: Dict[
            tuple,
            DependencyEdge
        ] = {}    

    def add_service(
    self,
    service_name
    ):

        if service_name not in self.nodes:

            self.nodes[
                service_name
            ] = ServiceNode(
                service_name
            )

        return self.nodes[
            service_name
        ]    
    
    def add_dependency(
    self,
    source,
    target,
    latency,
    error
    ):

        key = (
            source,
            target
        )

        if key not in self.edges:

            self.edges[
                key
            ] = DependencyEdge(

                source,

                target

            )

        edge = self.edges[key]

        edge.call_count += 1

        edge.total_latency_ms += latency

        if error:
            edge.error_count += 1

    def to_dict(self):

        return {

            "services": {

                name: {

                    "requests":
                        node.request_count,

                    "errors":
                        node.error_count,

                    "error_rate":
                        node.error_rate,

                    "avg_latency":
                        node.average_latency,
                    "max_latency": 
                        node.max_latency_ms,

                }

                for name, node
                in self.nodes.items()

            },

            "dependencies": [

                {

                    "source":
                        edge.source,

                    "target":
                        edge.target,

                    "calls":
                        edge.call_count,

                    "errors":
                        edge.error_count,

                    "avg_latency":
                        edge.average_latency

                }

                for edge
                in self.edges.values()

            ]
        }        
    
    def summary(self):

        lines = []

        lines.append("Service Graph")
        lines.append(
            f"Services: {self.service_count}"
        )
        lines.append(
            f"Dependencies: {self.dependency_count}"
        )
        lines.append("")

        lines.append("Dependencies:")

        if not self.edges:

            lines.append("  (none)")

        else:

            for edge in self.edges.values():

                lines.append(

                    (
                        f"  • {edge.source}"
                        f" -> "
                        f"{edge.target}"
                        f" | calls={edge.call_count}"
                        f" | errors={edge.error_count}"
                        f" | avg_latency={edge.average_latency:.3f} ms"
                    )

                )

        return "\n".join(lines)


    def __repr__(self):

        return self.summary()
    
    @property
    def service_count(self):

        return len(self.nodes)


    @property
    def dependency_count(self):

        return len(self.edges)