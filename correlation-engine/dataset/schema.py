from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetRecord:

    # Metadados — não entram no XGBoost.
    sample_id: str
    scenario: str
    service_name: str

    # Features do modelo.
    request_count: int
    error_count: int
    error_rate: float

    avg_latency_ms: float
    max_latency_ms: float

    incoming_dependencies: int
    outgoing_dependencies: int
    dependency_count: int

    is_first_failure: int
    failure_order: int
    failure_lead_time_ms: float

    latency_zscore: float
    anomaly_score: float
    is_anomalous: int

    # Variável alvo.
    root_cause: int