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
)

METADATA_COLUMNS = (
    "service_name",
)

TARGET_COLUMN = "root_cause"

FEATURE_SCHEMA_VERSION = "rca-features-v1"