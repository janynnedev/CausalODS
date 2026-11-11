from pathlib import Path


PLATFORM_VERSION = "causalods-platform-v3"
PROJECTION_VERSION = "visualization-projection-v3"
RELEASE_SCHEMA_VERSION = "platform-release-manifest-v3"
VALIDATION_SCHEMA_VERSION = "platform-validation-report-v3"

INDEX_NAMES = {
    "unified_events": "causalods-v3-unified-events",
    "incidents": "causalods-v3-incidents",
    "rca_results": "causalods-v3-rca-results",
    "explanations": "causalods-v3-explanations",
    "experiment_runs": "causalods-v3-experiment-runs",
}

REQUIRED_DASHBOARDS = (
    "Incident Overview",
    "Root Cause Detail",
    "Multimodal Evidence",
    "Event Evidence Graph",
    "Observability Quality & Robustness",
    "Experimental Results H1-H4",
    "Explanation Audit",
    "Platform Health",
)

ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE_ROOT = ROOT / "infrastructure" / "platform-v3"
TEMPLATE_ROOT = INFRASTRUCTURE_ROOT / "opensearch" / "templates"
DASHBOARD_ROOT = INFRASTRUCTURE_ROOT / "grafana" / "dashboards"
PROVISIONING_ROOT = INFRASTRUCTURE_ROOT / "grafana" / "provisioning"
ARTIFACT_ROOT = ROOT / "artifacts" / "platform-v3"

OPERATIONAL_GROUND_TRUTH_FIELDS = frozenset(
    {
        "ground_truth",
        "fault_service",
        "fault_location",
        "is_root_cause",
        "actual_services",
        "root_cause_label",
        "target",
    }
)

SECRET_FIELD_NAMES = frozenset(
    {
        "authorization",
        "password",
        "passwd",
        "secret",
        "api_key",
        "openai_api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "private_key",
    }
)
