from dataclasses import dataclass, asdict


@dataclass
class RCAFeature:

    service_name: str

    request_count: int

    error_count: int

    error_rate: float

    avg_latency_ms: float

    max_latency_ms: float

    incoming_dependencies: int

    outgoing_dependencies: int

    dependency_count: int

    is_first_failure: bool

    failure_order: int

    failure_lead_time_ms: float

    latency_zscore: float

    anomaly_score: float

    is_anomalous: bool

    exclusive_latency_ms: float = 0.0

    exclusive_latency_ratio: float = 0.0



    def to_dict(self):

        return asdict(self)