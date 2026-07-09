from dataclasses import dataclass, asdict


@dataclass
class RCAFeature:

    service_name: str


    # Trace features

    request_count: int = 0

    error_count: int = 0

    error_rate: float = 0.0

    avg_latency_ms: float = 0.0


    # Graph features

    incoming_dependencies: int = 0

    outgoing_dependencies: int = 0


    # Temporal features

    is_first_failure: int = 0

    failure_lead_time_ms: float = 0.0


    # Causal features

    downstream_impact: float = 0.0

    propagation_depth: int = 0


    # Anomaly features

    latency_zscore: float = 0.0

    anomaly_score: float = 0.0



    def to_dict(self):

        return asdict(self)