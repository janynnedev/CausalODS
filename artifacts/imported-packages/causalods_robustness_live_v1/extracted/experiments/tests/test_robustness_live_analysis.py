from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "analyze_robustness_live.py"
spec = importlib.util.spec_from_file_location("analyze_robustness_live", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_summary_counts_duplicates_and_loss():
    rows = [
        {
            "scenario": "kafka_backlog",
            "technical_valid": True,
            "restoration_ok": True,
            "recovery_time_ms": 1000,
            "processing_latency_ms": 2000,
            "validation": {"valid": True},
            "rca_summary": {"count": 1},
            "incident_summary": {"count": 1},
            "observation": {"data_loss_count": 0, "provenance_valid": True},
        },
        {
            "scenario": "kafka_backlog",
            "technical_valid": True,
            "restoration_ok": True,
            "recovery_time_ms": 3000,
            "processing_latency_ms": 4000,
            "validation": {"valid": False},
            "rca_summary": {"count": 2},
            "incident_summary": {"count": 2},
            "observation": {"data_loss_count": 1, "provenance_valid": False},
        },
    ]
    result = module.summarize(rows)
    scenario = result["scenarios"][0]
    assert scenario["invariant_valid"] == 1
    assert scenario["rca_duplicate_runs"] == 1
    assert scenario["incident_duplicate_runs"] == 1
    assert scenario["data_loss_runs"] == 1
    assert scenario["median_recovery_time_ms"] == 2000
