from __future__ import annotations

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS))

from robustness_live.probes import ProvenanceProbe, SearchHit, parse_consumer_group_lag


def test_parse_consumer_group_lag_sums_partitions():
    output = """
GROUP              TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID HOST CLIENT-ID
correlation-engine traces 0 10 13 3 - - -
correlation-engine traces 1 20 22 2 - - -
"""
    assert parse_consumer_group_lag(output) == 5


def test_parse_consumer_group_lag_ignores_other_topics():
    output = "correlation-engine metrics 0 0 9 9 - - -\n"
    assert parse_consumer_group_lag(output) == 0


def test_provenance_probe_accepts_v2():
    hits = [SearchHit("1", {"model_version": "xgboost-rca-v2", "pipeline_version": "causalods-rca-v2"})]
    valid, details = ProvenanceProbe("v2").validate(hits)
    assert valid is True
    assert details["actual_model_version"] == "xgboost-rca-v2"


def test_provenance_probe_marks_absent_as_unknown():
    valid, _ = ProvenanceProbe("v2").validate([])
    assert valid is None
