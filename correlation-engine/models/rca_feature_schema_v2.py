"""
Contrato de features utilizado pelo XGBoost RCA v2.

Esta versão preserva a ordem integral do schema v1
e adiciona ao final as features de latência exclusiva.

Não alterar a ordem das colunas sem criar uma nova
versão do schema.
"""


MODEL_FEATURE_COLUMNS = (
    "request_count",
    "error_count",
    "error_rate",
    "avg_latency_ms",
    "max_latency_ms",
    "incoming_dependencies",
    "outgoing_dependencies",
    "dependency_count",
    "is_first_failure",
    "failure_order",
    "failure_lead_time_ms",
    "latency_zscore",
    "anomaly_score",
    "is_anomalous",

    # Features adicionadas no schema v2.
    "exclusive_latency_ms",
    "exclusive_latency_ratio",
)


BOOLEAN_FEATURE_COLUMNS = (
    "is_first_failure",
    "is_anomalous",
)


INTEGER_FEATURE_COLUMNS = (
    "request_count",
    "error_count",
    "incoming_dependencies",
    "outgoing_dependencies",
    "dependency_count",
    "failure_order",
    "is_first_failure",
    "is_anomalous",
)


FLOAT_FEATURE_COLUMNS = (
    "error_rate",
    "avg_latency_ms",
    "max_latency_ms",
    "failure_lead_time_ms",
    "latency_zscore",
    "anomaly_score",

    # Features v2.
    "exclusive_latency_ms",
    "exclusive_latency_ratio",
)


METADATA_COLUMNS = (
    "sample_id",
    "scenario",
    "service_name",
    "root_cause_service",
    "fault_delay_ms",
    "generation_seed",
)


TARGET_COLUMN = "root_cause"


FEATURE_SCHEMA_VERSION = (
    "rca-features-v2"
)