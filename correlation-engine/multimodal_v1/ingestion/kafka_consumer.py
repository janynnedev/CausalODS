from __future__ import annotations

import os
from dataclasses import dataclass, field

from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from kafka.structs import OffsetAndMetadata

from multimodal_v1.contracts import SignalType
from multimodal_v1.ingestion.context import NormalizationContext
from multimodal_v1.ingestion.dead_letter import KafkaDeadLetterPublisher
from multimodal_v1.ingestion.delivery import BatchSink
from multimodal_v1.ingestion.registry import default_registry
from multimodal_v1.ingestion.service import MultimodalIngestionService


def _environment(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class MultimodalKafkaSettings:
    bootstrap_servers: str = field(default_factory=lambda: _environment(
        "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"))
    group_id: str = field(default_factory=lambda: _environment(
        "MULTIMODAL_CONSUMER_GROUP", "causalods-multimodal-v3"))
    trace_topic: str = field(default_factory=lambda: _environment(
        "MULTIMODAL_TRACE_TOPIC", "traces-v3"))
    log_topic: str = field(default_factory=lambda: _environment(
        "MULTIMODAL_LOG_TOPIC", "logs-v1"))
    metric_topic: str = field(default_factory=lambda: _environment(
        "MULTIMODAL_METRIC_TOPIC", "metrics-v1"))
    business_topic: str = field(default_factory=lambda: _environment(
        "BUSINESS_EVENT_TOPIC", "business-events-v1"))
    dead_letter_topic: str = field(default_factory=lambda: _environment(
        "MULTIMODAL_DEAD_LETTER_TOPIC", "multimodal-dead-letter-v1"))

    def __post_init__(self) -> None:
        named = {
            "bootstrap_servers": self.bootstrap_servers,
            "group_id": self.group_id,
            "trace_topic": self.trace_topic, "log_topic": self.log_topic,
            "metric_topic": self.metric_topic, "business_topic": self.business_topic,
            "dead_letter_topic": self.dead_letter_topic,
        }
        for name, value in named.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} não pode ser vazio.")
        inputs = (
            self.trace_topic, self.log_topic, self.metric_topic,
            self.business_topic,
        )
        if len(set(inputs)) != 4:
            raise ValueError("Os quatro tópicos de entrada devem ser distintos.")
        if self.dead_letter_topic in inputs:
            raise ValueError("O tópico DLQ deve ser distinto dos tópicos de entrada.")

    def topic_signals(self) -> dict[str, SignalType]:
        return {
            self.trace_topic: SignalType.TRACE,
            self.log_topic: SignalType.LOG,
            self.metric_topic: SignalType.METRIC,
            self.business_topic: SignalType.BUSINESS_EVENT,
        }


class MultimodalKafkaConsumer:
    """Ponto de entrada v3 explícito; não referenciado pelo TraceConsumer v2."""

    def __init__(self, sink: BatchSink,
                 settings: MultimodalKafkaSettings | None = None,
                 *, consumer=None, producer=None,
                 service: MultimodalIngestionService | None = None) -> None:
        self.settings = settings or MultimodalKafkaSettings()
        topics = self.settings.topic_signals()
        self.consumer = consumer or KafkaConsumer(
            *topics, bootstrap_servers=self.settings.bootstrap_servers,
            group_id=self.settings.group_id, auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        self.producer = producer or KafkaProducer(
            bootstrap_servers=self.settings.bootstrap_servers
        )
        self.service = service or MultimodalIngestionService(
            default_registry(),
            KafkaDeadLetterPublisher(self.producer, self.settings.dead_letter_topic),
        )
        self.sink = sink

    def process_message(self, message) -> None:
        signal = self.settings.topic_signals()[message.topic]
        context = NormalizationContext.from_kafka_message(message)
        batch = self.service.prepare(signal, message.value, context)
        self.service.deliver(batch, self.sink)

        partition = TopicPartition(message.topic, message.partition)
        offsets = {
            partition: OffsetAndMetadata(message.offset + 1, "", -1)
        }
        # Se commit falhar, dedupe/watermark não são confirmados. O retry
        # reenvia o lote inteiro a um sink obrigatoriamente idempotente.
        self.consumer.commit(offsets=offsets)
        self.service.confirm(batch)
        self.service.metrics.increment("committed", signal.value)

    def consume(self) -> None:
        try:
            for message in self.consumer:
                self.process_message(message)
        finally:
            self.close()

    def close(self) -> None:
        try:
            self.consumer.close()
        finally:
            try:
                self.producer.flush()
            finally:
                self.producer.close()

    def __enter__(self) -> "MultimodalKafkaConsumer":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()
