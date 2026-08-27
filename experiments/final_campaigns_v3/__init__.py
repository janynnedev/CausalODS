"""Final scientific campaign wiring over the frozen v3 contracts."""

from .core import (
    H1A_METHODS,
    H1B_VIEWS,
    H1C_VIEWS,
    H2_METHODS,
    H3_MODES,
    H4_SEVERITIES,
    PROTOCOLS,
    FailureLedger,
    FrozenDatasetRegistry,
    ResultState,
    ScientificEvaluation,
    ScientificInput,
    audit_pairing,
    build_capability_matrix,
    build_experiment_config,
    build_plan,
    deterministic_blind_map,
    dry_run,
    reusable_result,
)

__all__ = [
    "H1A_METHODS", "H1B_VIEWS", "H1C_VIEWS", "H2_METHODS", "H3_MODES",
    "H4_SEVERITIES", "PROTOCOLS", "FailureLedger", "FrozenDatasetRegistry",
    "ResultState", "ScientificEvaluation", "ScientificInput", "audit_pairing",
    "build_capability_matrix", "build_experiment_config", "build_plan",
    "deterministic_blind_map", "dry_run", "reusable_result",
]
