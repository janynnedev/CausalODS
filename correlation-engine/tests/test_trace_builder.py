from datetime import (
    datetime,
    timezone,
)

from models.span import Span
from models.trace_builder import TraceBuilder


def test_trace_builder_creates_trace():

    builder = TraceBuilder()

    # Dados brutos no formato recebido do OpenTelemetry:
    # timestamps e duração em nanossegundos.
    start_time_ns = 1_000_000_000

    duration_ns = 100_000_000  # 100 ms

    end_time_ns = (
        start_time_ns
        + duration_ns
    )

    root_span = Span(
        trace_id="trace01",
        span_id="root",
        parent_span_id=None,
        service_name="order-service",
        operation_name="createOrder",
        start_time=start_time_ns,
        end_time=end_time_ns,
        duration_ns=duration_ns,
        status_code="OK",
    )

    builder.process_span(
        root_span
    )

    trace = builder.get_trace(
        "trace01"
    )

    assert trace is not None
    assert trace.trace_id == "trace01"
    assert trace.root_span_id == "root"
    assert "root" in trace.spans

    stored_span = trace.spans["root"]

    assert (
        stored_span.service_name
        == "order-service"
    )

    assert (
        stored_span.operation_name
        == "createOrder"
    )

    expected_start_time = (
        datetime.fromtimestamp(
            start_time_ns
            / 1_000_000_000,
            tz=timezone.utc,
        )
    )

    expected_end_time = (
        datetime.fromtimestamp(
            end_time_ns
            / 1_000_000_000,
            tz=timezone.utc,
        )
    )

    assert (
        stored_span.start_time
        == expected_start_time
    )

    assert (
        stored_span.end_time
        == expected_end_time
    )

    assert (
        stored_span.duration_ms
        == 100.0
    )

    assert stored_span.status_code == "OK"

    assert stored_span.parent_span_id is None

    assert stored_span.children == []

def test_trace_builder_connects_parent_and_child():

    builder = TraceBuilder()

    root_span = Span(
        trace_id="trace02",
        span_id="root",
        parent_span_id=None,
        service_name="order-service",
        operation_name="createOrder",
        start_time=1_000_000_000,
        end_time=1_200_000_000,
        duration_ns=200_000_000,
        status_code="OK",
    )

    child_span = Span(
        trace_id="trace02",
        span_id="payment-span",
        parent_span_id="root",
        service_name="payment-service",
        operation_name="processPayment",
        start_time=1_020_000_000,
        end_time=1_170_000_000,
        duration_ns=150_000_000,
        status_code="OK",
    )

    builder.process_span(
        root_span
    )

    builder.process_span(
        child_span
    )

    trace = builder.get_trace(
        "trace02"
    )

    assert trace is not None
    assert len(trace.spans) == 2

    assert "root" in trace.spans

    assert (
        "payment-span"
        in trace.spans
    )

    stored_root = trace.spans[
        "root"
    ]

    stored_child = trace.spans[
        "payment-span"
    ]

    assert (
        stored_child.parent_span_id
        == "root"
    )

    assert (
        stored_child.service_name
        == "payment-service"
    )

    assert (
        stored_child.duration_ms
        == 150.0
    )

    assert (
        "payment-span"
        in stored_root.children
    )    