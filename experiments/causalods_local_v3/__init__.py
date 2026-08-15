"""Controlled, serial causalods-local-v3 scientific dataset generator."""

from .core import (
    DATASET_ID,
    EXPECTED_MODALITIES,
    SCENARIOS,
    Campaign,
    GroundTruth,
    Scenario,
    audit_anti_leakage,
    derive_repeat_seed,
)

__all__ = (
    "DATASET_ID", "EXPECTED_MODALITIES", "SCENARIOS", "Campaign",
    "GroundTruth", "Scenario", "audit_anti_leakage", "derive_repeat_seed",
)
