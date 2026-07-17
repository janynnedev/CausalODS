from models.span import Span
from models.trace_builder import TraceBuilder
from models.service_graph import ServiceGraph
from features.graph_feature_extractor import (
    GraphFeatureExtractor,
)
from graph.graph_builder import GraphBuilder


def build_order_payment_trace():

    trace_builder = TraceBuilder()

    root_span = Span(
        trace_id="trace-graph-01",
        span_id="order-span",
        parent_span_id=None,
        service_name="order-service",
        operation_name="createOrder",
        start_time=1_000_000_000,
        end_time=1_300_000_000,
        duration_ns=300_000_000,
        status_code="OK",
    )

    payment_span = Span(
        trace_id="trace-graph-01",
        span_id="payment-span",
        parent_span_id="order-span",
        service_name="payment-service",
        operation_name="processPayment",
        start_time=1_050_000_000,
        end_time=1_250_000_000,
        duration_ns=200_000_000,
        status_code="OK",
    )

    trace_builder.process_span(
        root_span
    )

    trace_builder.process_span(
        payment_span
    )

    return trace_builder.get_trace(
        "trace-graph-01"
    )


def test_graph_builder_creates_service_dependency():

    trace = build_order_payment_trace()

    assert trace is not None

    graph = GraphBuilder().build(
        trace
    )

    assert isinstance(
        graph,
        ServiceGraph,
    )

    assert set(graph.nodes.keys()) == {
        "order-service",
        "payment-service",
    }

    order_node = graph.nodes[
        "order-service"
    ]

    payment_node = graph.nodes[
        "payment-service"
    ]

    assert (
        order_node.service_name
        == "order-service"
    )

    assert order_node.request_count == 1
    assert order_node.error_count == 0

    assert (
        order_node.total_latency_ms
        == 300.0
    )

    assert (
        order_node.max_latency_ms
        == 300.0
    )

    assert (
        payment_node.service_name
        == "payment-service"
    )

    assert payment_node.request_count == 1
    assert payment_node.error_count == 0

    assert (
        payment_node.total_latency_ms
        == 200.0
    )

    assert (
        payment_node.max_latency_ms
        == 200.0
    )

    edge_key = (
        "order-service",
        "payment-service",
    )

    assert edge_key in graph.edges

    edge = graph.edges[
        edge_key
    ]

    assert edge.source == "order-service"
    assert edge.target == "payment-service"
    assert edge.call_count == 1
    assert edge.error_count == 0

    assert (
        edge.total_latency_ms
        == 200.0
    )


def test_graph_to_dict_is_serializable():

    trace = build_order_payment_trace()

    graph = GraphBuilder().build(
        trace
    )

    payload = graph.to_dict()

    assert isinstance(
        payload,
        dict,
    )

    assert set(payload.keys()) == {
        "services",
        "dependencies",
    }

    assert (
        payload["services"][
            "order-service"
        ]["requests"]
        == 1
    )

    assert (
        payload["services"][
            "order-service"
        ]["avg_latency"]
        == 300.0
    )

    assert (
        payload["services"][
            "payment-service"
        ]["avg_latency"]
        == 200.0
    )

    assert len(
        payload["dependencies"]
    ) == 1

    dependency = payload[
        "dependencies"
    ][0]

    assert dependency == {
        "source": "order-service",
        "target": "payment-service",
        "calls": 1,
        "errors": 0,
        "avg_latency": 200.0,
    }


def test_graph_builder_aggregates_repeated_calls():

    trace_builder = TraceBuilder()

    root_span = Span(
        trace_id="trace-graph-02",
        span_id="order-span",
        parent_span_id=None,
        service_name="order-service",
        operation_name="createOrder",
        start_time=1_000_000_000,
        end_time=1_500_000_000,
        duration_ns=500_000_000,
        status_code="OK",
    )

    first_payment_span = Span(
        trace_id="trace-graph-02",
        span_id="payment-span-1",
        parent_span_id="order-span",
        service_name="payment-service",
        operation_name="processPayment",
        start_time=1_050_000_000,
        end_time=1_250_000_000,
        duration_ns=200_000_000,
        status_code="OK",
    )

    second_payment_span = Span(
        trace_id="trace-graph-02",
        span_id="payment-span-2",
        parent_span_id="order-span",
        service_name="payment-service",
        operation_name="confirmPayment",
        start_time=1_280_000_000,
        end_time=1_430_000_000,
        duration_ns=150_000_000,
        status_code="OK",
    )

    trace_builder.process_span(
        root_span
    )

    trace_builder.process_span(
        first_payment_span
    )

    trace_builder.process_span(
        second_payment_span
    )

    trace = trace_builder.get_trace(
        "trace-graph-02"
    )

    graph = GraphBuilder().build(
        trace
    )

    edge = graph.edges[
        (
            "order-service",
            "payment-service",
        )
    ]

    assert edge.call_count == 2
    assert edge.error_count == 0

    assert (
        edge.total_latency_ms
        == 350.0
    )

    payment_node = graph.nodes[
        "payment-service"
    ]

    assert payment_node.request_count == 2

    assert (
        payment_node.total_latency_ms
        == 350.0
    )

    assert (
        payment_node.max_latency_ms
        == 200.0
    )

def test_graph_feature_extractor_uses_edges():

    trace = build_order_payment_trace()

    graph = GraphBuilder().build(
        trace
    )

    features = (
        GraphFeatureExtractor()
        .extract(
            graph
        )
    )

    assert features[
        "order-service"
    ] == {
        "incoming_dependencies": 0,
        "outgoing_dependencies": 1,
        "dependency_count": 1,
    }

    assert features[
        "payment-service"
    ] == {
        "incoming_dependencies": 1,
        "outgoing_dependencies": 0,
        "dependency_count": 1,
    }    

def test_graph_feature_extractor_counts_unique_dependencies():

    trace_builder = TraceBuilder()

    root_span = Span(
        trace_id="trace-feature-graph",
        span_id="order-span",
        parent_span_id=None,
        service_name="order-service",
        operation_name="createOrder",
        start_time=1_000_000_000,
        end_time=1_500_000_000,
        duration_ns=500_000_000,
        status_code="OK",
    )

    first_payment_span = Span(
        trace_id="trace-feature-graph",
        span_id="payment-span-1",
        parent_span_id="order-span",
        service_name="payment-service",
        operation_name="processPayment",
        start_time=1_050_000_000,
        end_time=1_250_000_000,
        duration_ns=200_000_000,
        status_code="OK",
    )

    second_payment_span = Span(
        trace_id="trace-feature-graph",
        span_id="payment-span-2",
        parent_span_id="order-span",
        service_name="payment-service",
        operation_name="confirmPayment",
        start_time=1_280_000_000,
        end_time=1_430_000_000,
        duration_ns=150_000_000,
        status_code="OK",
    )

    trace_builder.process_span(
        root_span
    )

    trace_builder.process_span(
        first_payment_span
    )

    trace_builder.process_span(
        second_payment_span
    )

    trace = trace_builder.get_trace(
        "trace-feature-graph"
    )

    graph = GraphBuilder().build(
        trace
    )

    features = (
        GraphFeatureExtractor()
        .extract(
            graph
        )
    )

    assert features[
        "order-service"
    ]["outgoing_dependencies"] == 1

    assert features[
        "payment-service"
    ]["incoming_dependencies"] == 1    