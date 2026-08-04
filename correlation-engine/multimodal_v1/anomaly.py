from __future__ import annotations

from dataclasses import dataclass

from multimodal_v1.contracts import UnifiedEvent


@dataclass(frozen=True)
class AnomalyPolicyV1:
    version: str = "multimodal-anomaly-v1"

    def is_anomalous(self, event: UnifiedEvent) -> bool:
        if not isinstance(event, UnifiedEvent):
            raise TypeError("AnomalyPolicyV1 requer UnifiedEvent.")
        severity = (event.severity or "").upper()
        outcome = (event.business_outcome or "").lower()
        status = event.status_code
        return bool(
            event.error_type
            or severity in {"ERROR", "FATAL", "CRITICAL"}
            or (isinstance(status, int) and not isinstance(status, bool) and status >= 400)
            or (isinstance(status, str) and status.upper() in {"ERROR", "FAILED", "FAILURE"})
            or outcome in {"failed", "failure", "rejected", "error"}
            or event.attributes.get("is_anomalous") is True
            or "anomalous" in event.data_quality_flags
        )


DEFAULT_ANOMALY_POLICY = AnomalyPolicyV1()


def is_anomalous(event: UnifiedEvent) -> bool:
    return DEFAULT_ANOMALY_POLICY.is_anomalous(event)
