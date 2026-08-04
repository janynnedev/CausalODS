from multimodal_v1.identity import PropagationContext, normalize_distributed_id
from multimodal_v1.identity_context import (
    CONFLICT_FLAG,
    aggregate_correlation_ids,
)
from multimodal_v1.kafka_propagation import (
    KafkaContextPropagator,
    contains_sensitive_header,
)


def test_kafka_helper_preserves_w3c_fields_and_only_allowlisted_baggage():
    propagator = KafkaContextPropagator()
    original = PropagationContext(
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        tracestate="vendor=value",
        baggage=(
            "business.correlation.id=workflow-1,tenant=acme,token=secret,"
            "email=a@b.test,authorization=Bearer,arbitrary=value"
        ),
        correlation_id="workflow-1",
    )
    headers = propagator.inject(original)
    extracted = propagator.extract(headers)
    assert extracted.traceparent == original.traceparent
    assert extracted.tracestate == original.tracestate
    assert extracted.correlation_id == "workflow-1"
    assert extracted.baggage == "business.correlation.id=workflow-1"
    assert not contains_sensitive_header(headers)
    encoded = b"".join(value for _, value in headers)
    for forbidden in (b"tenant", b"secret", b"a@b.test", b"Bearer", b"arbitrary"):
        assert forbidden not in encoded


def test_baggage_is_not_copied_automatically():
    headers = KafkaContextPropagator().inject(
        PropagationContext(baggage="tenant=acme,authorization=BearerSecret")
    )
    assert headers == []


def test_fan_out_and_fan_in_are_future_link_descriptors():
    contexts = (
        PropagationContext(traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01"),
        PropagationContext(traceparent="00-" + "c" * 32 + "-" + "d" * 16 + "-01"),
    )
    links = KafkaContextPropagator().build_span_links(contexts)
    assert len(links) == 2
    assert {link["traceparent"] for link in links} == {
        context.traceparent for context in contexts
    }


def test_distributed_id_validation_is_non_throwing():
    assert normalize_distributed_id("workflow.valid-1") == ("workflow.valid-1", ())
    assert normalize_distributed_id("x" * 129) == (
        None, ("invalid_business_correlation_id",)
    )


def test_identity_aggregation_zero_one_and_conflict():
    assert aggregate_correlation_ids([]).correlation_id is None
    single = aggregate_correlation_ids(["workflow-1", "workflow-1"])
    assert single.correlation_id == "workflow-1"
    assert single.correlation_ids == ("workflow-1",)

    conflict = aggregate_correlation_ids(["workflow-b", "workflow-a"])
    assert conflict.correlation_id is None
    assert conflict.correlation_ids == ("workflow-a", "workflow-b")
    assert CONFLICT_FLAG in conflict.data_quality_flags


def test_identity_conflict_is_independent_of_arrival_order():
    forward = aggregate_correlation_ids(["workflow-a", "workflow-b"])
    reverse = aggregate_correlation_ids(["workflow-b", "workflow-a"])
    assert forward == reverse
