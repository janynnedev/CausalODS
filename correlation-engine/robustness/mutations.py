from __future__ import annotations

from robustness.contracts import (
    MutationResult,
    RobustnessScenario,
    SpanEnvelope,
    TraceFixture,
)
from robustness.fixtures import baseline_envelopes


def control(fixture: TraceFixture) -> MutationResult:
    envelopes = baseline_envelopes(fixture)
    return MutationResult(
        scenario=RobustnessScenario.CONTROL,
        envelopes=envelopes,
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
    )


def duplicate_span(
    fixture: TraceFixture,
    *,
    span_id: str = "span-payment-server",
) -> MutationResult:
    envelopes = list(baseline_envelopes(fixture))
    target_index = next(
        index
        for index, item in enumerate(envelopes)
        if item.span.span_id == span_id
    )
    target = envelopes[target_index]

    duplicate = SpanEnvelope(
        span=target.span,
        arrival_offset_ms=target.arrival_offset_ms + 1,
        sequence=max(item.sequence for item in envelopes) + 1,
    )
    envelopes.insert(target_index + 1, duplicate)

    return MutationResult(
        scenario=RobustnessScenario.DUPLICATE_SPAN,
        envelopes=tuple(envelopes),
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
        introduced_duplicates=1,
        metadata={"duplicated_span_id": span_id},
    )


def out_of_order_spans(fixture: TraceFixture) -> MutationResult:
    baseline = baseline_envelopes(fixture)
    reversed_spans = list(reversed([item.span for item in baseline]))
    envelopes = tuple(
        SpanEnvelope(
            span=span,
            arrival_offset_ms=index * 25,
            sequence=index,
        )
        for index, span in enumerate(reversed_spans)
    )

    return MutationResult(
        scenario=RobustnessScenario.OUT_OF_ORDER_SPANS,
        envelopes=envelopes,
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
        reordered=True,
    )


def incomplete_trace(
    fixture: TraceFixture,
    *,
    dropped_span_id: str = "span-payment-server",
) -> MutationResult:
    envelopes = tuple(
        item
        for item in baseline_envelopes(fixture)
        if item.span.span_id != dropped_span_id
    )

    return MutationResult(
        scenario=RobustnessScenario.INCOMPLETE_TRACE,
        envelopes=envelopes,
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
        dropped_span_ids=(dropped_span_id,),
    )


def delayed_span_before_timeout(
    fixture: TraceFixture,
    *,
    timeout_ms: int,
    span_id: str = "span-payment-server",
    safety_margin_ms: int = 100,
) -> MutationResult:
    if safety_margin_ms <= 0 or safety_margin_ms >= timeout_ms:
        raise ValueError("safety_margin_ms deve estar entre 0 e timeout_ms")

    envelopes = []
    for item in baseline_envelopes(fixture):
        if item.span.span_id == span_id:
            envelopes.append(
                SpanEnvelope(
                    span=item.span,
                    arrival_offset_ms=timeout_ms - safety_margin_ms,
                    sequence=item.sequence,
                )
            )
        else:
            envelopes.append(item)

    envelopes.sort(
        key=lambda item: (
            item.arrival_offset_ms,
            item.sequence,
        )
    )

    return MutationResult(
        scenario=RobustnessScenario.DELAYED_SPAN_BEFORE_TIMEOUT,
        envelopes=tuple(envelopes),
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
        delayed_span_ids=(span_id,),
        reordered=True,
        metadata={
            "timeout_ms": timeout_ms,
            "safety_margin_ms": safety_margin_ms,
        },
    )


def delayed_span_after_timeout(
    fixture: TraceFixture,
    *,
    timeout_ms: int,
    span_id: str = "span-payment-server",
    delay_after_timeout_ms: int = 250,
) -> MutationResult:
    if delay_after_timeout_ms <= 0:
        raise ValueError("delay_after_timeout_ms deve ser positivo")

    envelopes = []
    for item in baseline_envelopes(fixture):
        if item.span.span_id == span_id:
            envelopes.append(
                SpanEnvelope(
                    span=item.span,
                    arrival_offset_ms=timeout_ms + delay_after_timeout_ms,
                    sequence=item.sequence,
                )
            )
        else:
            envelopes.append(item)

    envelopes.sort(
        key=lambda item: (
            item.arrival_offset_ms,
            item.sequence,
        )
    )

    return MutationResult(
        scenario=RobustnessScenario.DELAYED_SPAN_AFTER_TIMEOUT,
        envelopes=tuple(envelopes),
        input_span_count=len(fixture.spans),
        output_envelope_count=len(envelopes),
        delayed_span_ids=(span_id,),
        reordered=True,
        metadata={
            "timeout_ms": timeout_ms,
            "delay_after_timeout_ms": delay_after_timeout_ms,
        },
    )


def build_offline_mutations(
    fixture: TraceFixture,
    *,
    timeout_ms: int = 5_000,
) -> tuple[MutationResult, ...]:
    return (
        control(fixture),
        duplicate_span(fixture),
        out_of_order_spans(fixture),
        incomplete_trace(fixture),
        delayed_span_before_timeout(
            fixture,
            timeout_ms=timeout_ms,
        ),
        delayed_span_after_timeout(
            fixture,
            timeout_ms=timeout_ms,
        ),
    )
