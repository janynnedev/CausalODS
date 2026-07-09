from dataclasses import dataclass



@dataclass
class DatasetRecord:


    service_name:str


    request_count:int

    error_count:int

    error_rate:float

    avg_latency_ms:float


    incoming_dependencies:int

    outgoing_dependencies:int


    is_first_failure:int


    downstream_impact:float

    propagation_depth:int


    latency_zscore:float


    anomaly_score:float


    root_cause:int