from __future__ import annotations

import sys
import unittest

from pathlib import Path


EXPERIMENTS = Path(__file__).resolve().parents[1]
if str(EXPERIMENTS) not in sys.path:
    sys.path.insert(0, str(EXPERIMENTS))

from baselines import (  # noqa: E402
    BaselineSample,
    FirstAnomalyBaseline,
    MaxAnomalyZScoreBaseline,
    MaxTemporalScoreBaseline,
)


def sample(evidence: list[dict], ground_truth: str | None = "payment-service") -> BaselineSample:
    return BaselineSample(
        source_version="v2",
        scenario="payment_latency" if ground_truth else "healthy",
        iteration=1,
        run_id="run-1",
        trace_id="trace-1",
        rca_result_id="rca-1",
        ground_truth=ground_truth,
        campaign_row={"top_service": ground_truth, "confidence": 0.8},
        rca_document={"payload": {"evidence": evidence}},
    )


class BaselinesTest(unittest.TestCase):
    def test_max_temporal_score(self) -> None:
        evidence = [
            {
                "evidence_type": "temporal",
                "service_name": "order-service",
                "normalized_value": 0.1,
                "attributes": {"rank": 2},
            },
            {
                "evidence_type": "temporal",
                "service_name": "payment-service",
                "normalized_value": 0.9,
                "attributes": {"rank": 1},
            },
        ]
        result = MaxTemporalScoreBaseline().predict(sample(evidence))
        self.assertEqual(result.predicted_service, "payment-service")
        self.assertTrue(result.correct)

    def test_max_anomaly_zscore_abstains_at_zero(self) -> None:
        evidence = [
            {
                "evidence_type": "anomaly",
                "service_name": "payment-service",
                "raw_value": {"zscore": 0.0},
            }
        ]
        result = MaxAnomalyZScoreBaseline().predict(sample(evidence))
        self.assertIsNone(result.predicted_service)
        self.assertTrue(result.abstained)
        self.assertFalse(result.correct)

    def test_first_anomaly_uses_earliest_timestamp(self) -> None:
        evidence = [
            {
                "evidence_type": "temporal",
                "service_name": "order-service",
                "normalized_value": 0.4,
                "raw_value": {
                    "is_first_anomaly": False,
                    "anomaly_started_at": "2026-01-01T00:00:02+00:00",
                },
            },
            {
                "evidence_type": "temporal",
                "service_name": "payment-service",
                "normalized_value": 0.8,
                "raw_value": {
                    "is_first_anomaly": True,
                    "anomaly_started_at": "2026-01-01T00:00:01+00:00",
                },
            },
        ]
        result = FirstAnomalyBaseline().predict(sample(evidence))
        self.assertEqual(result.predicted_service, "payment-service")
        self.assertTrue(result.correct)

    def test_healthy_abstention_is_correct(self) -> None:
        result = MaxAnomalyZScoreBaseline().predict(
            sample(
                [
                    {
                        "evidence_type": "anomaly",
                        "service_name": "order-service",
                        "raw_value": {"zscore": 0.0},
                    }
                ],
                ground_truth=None,
            )
        )
        self.assertTrue(result.abstained)
        self.assertTrue(result.correct)


if __name__ == "__main__":
    unittest.main()
