from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from robustness.contracts import (
    LifecycleObservation,
    MutationResult,
    SpanFixture,
    TraceFixture,
)


def semantic_fingerprint(
    spans: list[SpanFixture] | tuple[SpanFixture, ...],
) -> str:
    payload = [
        span.semantic_dict()
        for span in sorted(
            spans,
            key=lambda item: item.span_id,
        )
    ]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class TraceLifecycleSimulator:
    timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms deve ser positivo")

    def run(
        self,
        fixture: TraceFixture,
        mutation: MutationResult,
    ) -> LifecycleObservation:
        accepted: dict[str, SpanFixture] = {}
        duplicates = 0
        late = 0
        finalizations = 0
        completed = False
        timed_out = False
        finalized_at_ms: int | None = None
        events: list[dict[str, Any]] = []
        first_arrival_ms: int | None = None
        finalized = False

        ordered = sorted(
            mutation.envelopes,
            key=lambda item: (
                item.arrival_offset_ms,
                item.sequence,
            ),
        )

        for envelope in ordered:
            arrival = envelope.arrival_offset_ms
            if first_arrival_ms is None:
                first_arrival_ms = arrival

            if (
                not finalized
                and first_arrival_ms is not None
                and arrival - first_arrival_ms >= self.timeout_ms
            ):
                finalized = True
                finalizations += 1
                timed_out = True
                finalized_at_ms = first_arrival_ms + self.timeout_ms
                events.append(
                    {
                        "event": "trace_timed_out",
                        "at_ms": finalized_at_ms,
                        "accepted_unique_spans": len(accepted),
                    }
                )

            if finalized:
                late += 1
                events.append(
                    {
                        "event": "late_span_rejected",
                        "span_id": envelope.span.span_id,
                        "at_ms": arrival,
                    }
                )
                continue

            span_id = envelope.span.span_id
            if span_id in accepted:
                duplicates += 1
                events.append(
                    {
                        "event": "duplicate_span_detected",
                        "span_id": span_id,
                        "at_ms": arrival,
                    }
                )
                continue

            accepted[span_id] = envelope.span
            events.append(
                {
                    "event": "span_accepted",
                    "span_id": span_id,
                    "at_ms": arrival,
                }
            )

            if set(accepted) == set(fixture.expected_span_ids):
                finalized = True
                completed = True
                finalizations += 1
                finalized_at_ms = arrival
                events.append(
                    {
                        "event": "trace_completed",
                        "at_ms": arrival,
                        "accepted_unique_spans": len(accepted),
                    }
                )

        if not finalized:
            finalized = True
            finalizations += 1
            timed_out = True
            start = first_arrival_ms or 0
            finalized_at_ms = start + self.timeout_ms
            events.append(
                {
                    "event": "trace_timed_out",
                    "at_ms": finalized_at_ms,
                    "accepted_unique_spans": len(accepted),
                }
            )

        missing = tuple(
            sorted(
                set(fixture.expected_span_ids)
                - set(accepted)
            )
        )
        latency = (
            None
            if first_arrival_ms is None or finalized_at_ms is None
            else finalized_at_ms - first_arrival_ms
        )

        return LifecycleObservation(
            scenario=mutation.scenario,
            trace_id=fixture.trace_id,
            received_envelopes=len(mutation.envelopes),
            accepted_unique_spans=len(accepted),
            duplicate_spans_detected=duplicates,
            late_spans_rejected=late,
            trace_finalization_count=finalizations,
            trace_completed=completed,
            trace_timed_out=timed_out,
            decision_allowed=completed and not timed_out,
            expected_span_count=len(fixture.expected_span_ids),
            missing_span_ids=missing,
            semantic_fingerprint=semantic_fingerprint(
                tuple(accepted.values())
            ),
            finalized_at_ms=finalized_at_ms,
            processing_latency_ms=latency,
            events=tuple(events),
        )
