import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    Exemplar, ExponentialHistogram, ExponentialHistogramDataPoint, Gauge,
    Histogram, HistogramDataPoint, Metric, NumberDataPoint, ResourceMetrics,
    ScopeMetrics, Sum, Summary, SummaryDataPoint,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

from multimodal_v1.contracts import SignalType
from multimodal_v1.ingestion.business_normalizer import BusinessEventNormalizer
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.dead_letter import (
    DeadLetterRecord, InMemoryDeadLetterPublisher,
)
from multimodal_v1.ingestion.deduplication import EventDeduplicator
from multimodal_v1.ingestion.kafka_consumer import (
    MultimodalKafkaConsumer, MultimodalKafkaSettings,
)
from multimodal_v1.ingestion.registry import NormalizerRegistry, default_registry
from multimodal_v1.ingestion.service import MultimodalIngestionService, stream_key


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
NANOS = int(NOW.timestamp() * 1_000_000_000)


def kv(key, value):
    return KeyValue(key=key, value=AnyValue(string_value=value))


def context(source="kafka:test", threshold=60_000):
    return NormalizationContext(
        source_reference=source, ingested_timestamp=NOW,
        late_threshold_ms=threshold,
    )


def service():
    dlq = InMemoryDeadLetterPublisher()
    return MultimodalIngestionService(default_registry(), dlq), dlq


def prepare_events(ingestion, signal, payload, ctx, *, confirm=True):
    batch = ingestion.prepare(signal, payload, ctx)
    if confirm:
        ingestion.confirm(batch)
    return batch.events


def trace_payload():
    request = ExportTraceServiceRequest(resource_spans=[ResourceSpans(
        resource=Resource(attributes=[kv("service.name", "order-service")]),
        scope_spans=[ScopeSpans(
            scope=InstrumentationScope(name="orders", version="1.0"),
            spans=[Span(
                trace_id=bytes.fromhex("a" * 32), span_id=bytes.fromhex("b" * 16),
                name="POST /orders", start_time_unix_nano=NANOS,
                end_time_unix_nano=NANOS + 1_000_000,
                attributes=[kv("business.correlation.id", "workflow-1")],
            )],
        )],
    )])
    return request.SerializeToString()


def log_payload(*, traced, service_name="order-service", records=1):
    values = []
    for index in range(records):
        record = LogRecord(
            time_unix_nano=NANOS + index, observed_time_unix_nano=NANOS + index,
            severity_text="INFO",
            body=AnyValue(string_value=f"order accepted {index}"),
            attributes=[kv("event.name", "order.log")],
        )
        if traced:
            record.trace_id = bytes.fromhex("a" * 32)
            record.span_id = bytes.fromhex("b" * 16)
        values.append(record)
    request = ExportLogsServiceRequest(resource_logs=[ResourceLogs(
        resource=Resource(attributes=[kv("service.name", service_name)]),
        scope_logs=[ScopeLogs(scope=InstrumentationScope(name="app"), log_records=values)],
    )])
    return request.SerializeToString()


def metric_payload(*, kind="gauge", exemplar=False, service_name="order-service"):
    if kind in ("gauge", "sum"):
        point = NumberDataPoint(time_unix_nano=NANOS, as_double=42.5,
                                attributes=[kv("http.route", "/orders")])
    elif kind == "histogram":
        point = HistogramDataPoint(
            time_unix_nano=NANOS, count=3, sum=60, min=10, max=30,
            explicit_bounds=[10, 20], bucket_counts=[1, 1, 1],
            attributes=[kv("http.route", "/orders")],
        )
    elif kind == "exponential_histogram":
        point = ExponentialHistogramDataPoint(
            time_unix_nano=NANOS, count=3, sum=60, min=10, max=30,
            scale=2, zero_count=1, zero_threshold=0.01,
            positive=ExponentialHistogramDataPoint.Buckets(offset=1, bucket_counts=[1, 2]),
            negative=ExponentialHistogramDataPoint.Buckets(offset=-1, bucket_counts=[3]),
            attributes=[kv("http.route", "/orders")],
        )
    else:
        point = SummaryDataPoint(
            time_unix_nano=NANOS, count=2, sum=30,
            quantile_values=[
                SummaryDataPoint.ValueAtQuantile(quantile=0.5, value=10),
                SummaryDataPoint.ValueAtQuantile(quantile=0.99, value=20),
            ], attributes=[kv("http.route", "/orders")],
        )
    if exemplar and hasattr(point, "exemplars"):
        point.exemplars.append(Exemplar(
            time_unix_nano=NANOS, as_double=42.5,
            trace_id=bytes.fromhex("a" * 32), span_id=bytes.fromhex("b" * 16),
            filtered_attributes=[kv("sample", "yes")],
        ))
    if kind == "gauge":
        data = Gauge(data_points=[point])
    elif kind == "sum":
        data = Sum(data_points=[point], aggregation_temporality=2, is_monotonic=True)
    elif kind == "histogram":
        data = Histogram(data_points=[point], aggregation_temporality=1)
    elif kind == "exponential_histogram":
        data = ExponentialHistogram(data_points=[point], aggregation_temporality=2)
    else:
        data = Summary(data_points=[point])
    request = ExportMetricsServiceRequest(resource_metrics=[ResourceMetrics(
        resource=Resource(attributes=[
            kv("service.name", service_name), kv("service.instance.id", "instance-1")
        ]),
        scope_metrics=[ScopeMetrics(
            scope=InstrumentationScope(name="http"),
            metrics=[Metric(name="http.server.duration", unit="ms", **{kind: data})],
        )],
    )])
    return request.SerializeToString()


def business_payload(event_id="business-1", timestamp=NOW,
                     schema="business-event-v1", name="order.created"):
    return json.dumps({
        "event_id": event_id, "event_name": name,
        "event_timestamp": timestamp.isoformat(),
        "correlation_id": "workflow-1", "business_entity_id": "order-42",
        "service_name": "order-service", "business_operation": "create_order",
        "business_outcome": "created", "schema_version": schema,
    }).encode()


def test_trace_normalizer_reuses_v2_span_normalizer_and_outputs_unified_event():
    ingestion, dlq = service()
    events = prepare_events(ingestion, SignalType.TRACE, trace_payload(), context())
    assert len(events) == 1
    assert events[0].trace_id == "a" * 32
    assert events[0].correlation_id == "workflow-1"
    assert events[0].instrumentation_scope["name"] == "orders"
    assert dlq.records == []


def test_logs_with_and_without_trace_id_are_ingestible():
    for traced in (True, False):
        ingestion, _ = service()
        event, = prepare_events(ingestion, SignalType.LOG, log_payload(traced=traced), context())
        assert event.signal_type is SignalType.LOG
        assert (event.trace_id is not None) is traced
        assert event.attributes["body"] == "order accepted 0"


def test_metrics_with_and_without_exemplar():
    for exemplar in (True, False):
        ingestion, _ = service()
        event, = prepare_events(ingestion,
            SignalType.METRIC, metric_payload(exemplar=exemplar), context()
        )
        assert event.metric_name == "http.server.duration"
        assert event.metric_value == 42.5
        assert bool(event.attributes["exemplars"]) is exemplar
        assert ("metric_exemplar_descriptor" in event.data_quality_flags) is exemplar


def test_all_minimum_business_event_names_are_supported():
    names = (
        "order.created", "payment.requested", "payment.approved",
        "payment.failed", "order.approved", "order.rejected",
    )
    normalizer = BusinessEventNormalizer()
    for index, name in enumerate(names):
        event, = normalizer.decode_and_normalize(
            business_payload(f"event-{index}", name=name), context()
        )
        assert event.event_name == name
        assert event.signal_type is SignalType.BUSINESS_EVENT


def test_existing_business_schema_is_hardened_for_timezone_ids_and_unknown_fields():
    schema = json.loads((
        Path(__file__).resolve().parents[1]
        / "multimodal_v1/schemas/business-event-v1.schema.json"
    ).read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    valid = json.loads(business_payload())
    assert list(validator.iter_errors(valid)) == []
    for mutation in (
        {"event_timestamp": "2026-08-04T12:00:00"},
        {"correlation_id": "x" * 129},
        {"event_id": "contains whitespace"},
        {"unknown": True},
        {"schema_version": "business-event-v2"},
    ):
        candidate = {**valid, **mutation}
        assert list(validator.iter_errors(candidate)), mutation


def test_duplicate_is_dropped_by_event_id_and_source_reference():
    ingestion, _ = service()
    payload = business_payload()
    assert len(prepare_events(ingestion, "business_event", payload, context("kafka:a"))) == 1
    assert prepare_events(ingestion, "business_event", payload, context("kafka:a")) == ()
    assert len(prepare_events(ingestion, "business_event", payload, context("kafka:b"))) == 1
    assert ingestion.metrics.snapshot()["duplicates"]["business_event"] == 1


def test_out_of_order_event_is_accepted_and_marked():
    ingestion, _ = service()
    newer = business_payload("new", NOW)
    older = business_payload("old", NOW - timedelta(seconds=10))
    prepare_events(ingestion, "business_event", newer, context(threshold=60_000))
    event, = prepare_events(ingestion, "business_event", older, context(threshold=60_000))
    assert "out_of_order" in event.data_quality_flags


def test_late_event_is_counted_and_marked():
    ingestion, _ = service()
    payload = business_payload("late", NOW - timedelta(minutes=5))
    event, = prepare_events(ingestion, "business_event", payload, context(threshold=1000))
    assert "late_arrival" in event.data_quality_flags
    assert ingestion.metrics.snapshot()["late"]["business_event"] == 1


def test_unknown_schema_and_invalid_payload_go_to_dead_letter():
    ingestion, dlq = service()
    assert prepare_events(ingestion,
        "business_event", business_payload(schema="business-event-v99"), context()
    ) == ()
    assert prepare_events(ingestion, "log", b"not-protobuf", context()) == ()
    assert len(dlq.records) == 2
    assert dlq.records[0].error_type == "UnknownBusinessSchemaError"
    assert ingestion.metrics.snapshot()["rejected"] == {
        "business_event": 1, "log": 1,
    }


def test_received_and_normalized_metrics_count_messages_and_events():
    ingestion, _ = service()
    prepare_events(ingestion, "trace", trace_payload(), context())
    snapshot = ingestion.metrics.snapshot()
    assert snapshot["received"]["trace"] == 1
    assert snapshot["normalized"]["trace"] == 1


def test_kafka_settings_use_independent_topics_and_configurable_dlq():
    settings = MultimodalKafkaSettings(dead_letter_topic="custom-dlq")
    topics = settings.topic_signals()
    assert set(topics.values()) == set(SignalType)
    assert len(topics) == 4
    assert settings.dead_letter_topic == "custom-dlq"


@pytest.mark.parametrize("kind", [
    "gauge", "sum", "histogram", "exponential_histogram", "summary",
])
def test_preserves_metric_aggregation_details(kind):
    ingestion, _ = service()
    event, = prepare_events(
        ingestion, "metric", metric_payload(kind=kind), context()
    )
    details = event.attributes["aggregation_details"]
    assert event.metric_aggregation == kind
    if kind == "sum":
        assert details["aggregation_temporality"] == 2
        assert details["is_monotonic"] is True
    elif kind == "histogram":
        assert details["count"] == 3
        assert details["explicit_bounds"] == (10.0, 20.0)
        assert details["bucket_counts"] == (1, 1, 1)
        assert details["min"] == 10
        assert details["max"] == 30
    elif kind == "exponential_histogram":
        assert details["scale"] == 2
        assert details["zero_count"] == 1
        assert details["positive"]["offset"] == 1
        assert details["negative"]["bucket_counts"] == (3,)
    elif kind == "summary":
        assert details["count"] == 2
        assert len(details["quantile_values"]) == 2


def test_exemplar_preserves_timestamp_value_context_and_filtered_attributes():
    ingestion, _ = service()
    event, = prepare_events(
        ingestion, "metric", metric_payload(exemplar=True), context()
    )
    exemplar, = event.attributes["exemplars"]
    assert exemplar["timestamp"].endswith("Z")
    assert exemplar["value"] == 42.5
    assert exemplar["trace_id"] == "a" * 32
    assert exemplar["span_id"] == "b" * 16
    assert exemplar["filtered_attributes"]["sample"] == "yes"
    assert event.span_links == ()


def test_identical_logs_and_metrics_in_different_services_do_not_collide():
    ingestion, _ = service()
    log_a, = prepare_events(ingestion, "log", log_payload(
        traced=False, service_name="service-a"), context("kafka:logs"))
    log_b, = prepare_events(ingestion, "log", log_payload(
        traced=False, service_name="service-b"), context("kafka:logs"))
    metric_a, = prepare_events(ingestion, "metric", metric_payload(
        service_name="service-a"), context("kafka:metrics"))
    metric_b, = prepare_events(ingestion, "metric", metric_payload(
        service_name="service-b"), context("kafka:metrics"))
    assert log_a.event_id != log_b.event_id
    assert metric_a.event_id != metric_b.event_id


def test_independent_streams_do_not_create_false_out_of_order():
    ingestion, _ = service()
    first = json.loads(business_payload("a", NOW))
    second = json.loads(business_payload("b", NOW - timedelta(minutes=1)))
    second["correlation_id"] = "workflow-2"
    prepare_events(ingestion, "business_event", json.dumps(first).encode(), context())
    event, = prepare_events(
        ingestion, "business_event", json.dumps(second).encode(), context()
    )
    assert "out_of_order" not in event.data_quality_flags
    assert stream_key(event)[-1] == "workflow-2"


def test_business_event_cannot_forge_observed_provenance():
    ingestion, _ = service()
    payload = json.loads(business_payload())
    payload["source_reference"] = "forged:source"
    payload["source_dataset"] = "forged-dataset"
    ctx = NormalizationContext(
        source_reference="kafka:business-events-v1:2",
        source_dataset="trusted-dataset", ingested_timestamp=NOW,
        topic="business-events-v1", partition=2, offset=9,
    )
    event, = prepare_events(
        ingestion, "business_event", json.dumps(payload).encode(), ctx
    )
    assert event.source_reference == "kafka:business-events-v1:2"
    assert event.source_dataset == "trusted-dataset"
    assert event.attributes["reported_source_reference"] == "forged:source"
    assert event.attributes["reported_source_dataset"] == "forged-dataset"


def test_future_clock_skew_is_not_silenced():
    ingestion, _ = service()
    ctx = NormalizationContext(
        source_reference="kafka:business", ingested_timestamp=NOW,
        future_tolerance_ms=1000,
    )
    event, = prepare_events(
        ingestion, "business_event",
        business_payload("future", NOW + timedelta(minutes=1)), ctx,
    )
    assert event.late_arrival_ms == 0
    assert "clock_skew_future_event" in event.data_quality_flags


def test_lru_capacity_and_eviction_are_confirm_only():
    dedup = EventDeduplicator(capacity=2)
    assert not dedup.contains(("a", "source"))
    assert dedup.snapshot() == ()
    dedup.confirm((("a", "source"), ("b", "source")))
    dedup.confirm((("c", "source"),))
    assert dedup.snapshot() == (("b", "source"), ("c", "source"))
    assert not dedup.contains(("a", "source"))


@pytest.mark.parametrize("kwargs", [
    {"trace_topic": ""},
    {"group_id": " "},
    {"bootstrap_servers": ""},
    {"trace_topic": "same", "log_topic": "same"},
    {"dead_letter_topic": "logs-v1"},
])
def test_invalid_kafka_topic_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        MultimodalKafkaSettings(**kwargs)


def test_dlq_omits_payload_by_default_and_can_truncate_when_enabled():
    ctx = NormalizationContext(
        source_reference="kafka:logs-v1:3", ingested_timestamp=NOW,
        topic="logs-v1", partition=3, offset=17,
    )
    default = DeadLetterRecord.create("log", ctx, b"secret-payload", ValueError(
        "token=abc authorization=Bearer"
    ))
    assert default.payload_base64 is None
    assert default.original_size_bytes == 14
    assert default.topic == "logs-v1" and default.offset == 17
    assert "abc" not in default.error_message
    included = DeadLetterRecord.create(
        "log", ctx, b"0123456789", ValueError("bad"),
        include_payload=True, max_payload_bytes=4,
    )
    assert included.payload_truncated is True
    assert included.payload_base64 == "MDEyMw=="


class _ExplodingNormalizer:
    def decode_and_normalize(self, payload, context):
        raise RuntimeError("programming defect")


def test_internal_error_propagates_and_is_not_sent_to_dlq():
    registry = NormalizerRegistry()
    registry.register("log", _ExplodingNormalizer())
    dlq = InMemoryDeadLetterPublisher()
    ingestion = MultimodalIngestionService(registry, dlq)
    with pytest.raises(RuntimeError, match="programming defect"):
        ingestion.prepare("log", b"payload", context())
    assert dlq.records == []
    assert ingestion.metrics.snapshot()["internal_error"]["log"] == 1


class _FailingDlq:
    def publish(self, record):
        raise RuntimeError("dlq unavailable")


class _FakeConsumer:
    def __init__(self, fail_commit=False):
        self.commits = []
        self.fail_commit = fail_commit
        self.closed = False

    def commit(self, offsets):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits.append(offsets)

    def close(self):
        self.closed = True


class _FakeProducer:
    def __init__(self):
        self.flushed = False
        self.closed = False

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


class _RetrySink:
    def __init__(self):
        self.attempts = []
        self.fail_first = True

    def deliver(self, events):
        self.attempts.append(tuple(event.event_id for event in events))
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("sink unavailable")


def kafka_message(payload, offset=7):
    return SimpleNamespace(
        topic="logs-v1", partition=2, offset=offset, value=payload,
        key=b"workflow-key", timestamp=int(NOW.timestamp() * 1000),
    )


def test_sink_failure_retry_has_no_loss_and_commits_exact_offset():
    ingestion, _ = service()
    consumer = _FakeConsumer()
    sink = _RetrySink()
    runner = MultimodalKafkaConsumer(
        sink, consumer=consumer, producer=_FakeProducer(), service=ingestion
    )
    message = kafka_message(log_payload(traced=False, records=2))
    with pytest.raises(RuntimeError, match="sink unavailable"):
        runner.process_message(message)
    assert consumer.commits == []
    assert ingestion.deduplicator.snapshot() == ()
    assert ingestion.watermark_snapshot() == {}

    runner.process_message(message)
    assert len(sink.attempts) == 2
    assert sink.attempts[0] == sink.attempts[1]
    committed = consumer.commits[0]
    topic_partition, offset = next(iter(committed.items()))
    assert (topic_partition.topic, topic_partition.partition) == ("logs-v1", 2)
    assert offset.offset == 8
    assert len(ingestion.deduplicator.snapshot()) == 2

    runner.process_message(message)
    assert len(sink.attempts) == 2
    assert ingestion.metrics.snapshot()["duplicates"]["log"] == 2


def test_commit_failure_leaves_state_unconfirmed_for_idempotent_retry():
    ingestion, _ = service()
    consumer = _FakeConsumer(fail_commit=True)
    sink = _RetrySink()
    sink.fail_first = False
    runner = MultimodalKafkaConsumer(
        sink, consumer=consumer, producer=_FakeProducer(), service=ingestion
    )
    with pytest.raises(RuntimeError, match="commit failed"):
        runner.process_message(kafka_message(log_payload(traced=False)))
    assert ingestion.deduplicator.snapshot() == ()
    assert ingestion.watermark_snapshot() == {}


def test_dlq_failure_prevents_commit():
    ingestion = MultimodalIngestionService(default_registry(), _FailingDlq())
    consumer = _FakeConsumer()
    sink = _RetrySink()
    runner = MultimodalKafkaConsumer(
        sink, consumer=consumer, producer=_FakeProducer(), service=ingestion
    )
    with pytest.raises(RuntimeError, match="dlq unavailable"):
        runner.process_message(kafka_message(b"not protobuf"))
    assert consumer.commits == []


def test_explicit_close_closes_consumer_and_flushes_producer():
    ingestion, _ = service()
    consumer, producer = _FakeConsumer(), _FakeProducer()
    runner = MultimodalKafkaConsumer(
        _RetrySink(), consumer=consumer, producer=producer, service=ingestion
    )
    runner.close()
    assert consumer.closed and producer.flushed and producer.closed


def test_producer_is_flushed_and_closed_even_if_consumer_close_fails():
    ingestion, _ = service()
    consumer, producer = _FakeConsumer(), _FakeProducer()
    consumer.close = lambda: (_ for _ in ()).throw(RuntimeError("close failed"))
    runner = MultimodalKafkaConsumer(
        _RetrySink(), consumer=consumer, producer=producer, service=ingestion
    )
    with pytest.raises(RuntimeError, match="close failed"):
        runner.close()
    assert producer.flushed and producer.closed
