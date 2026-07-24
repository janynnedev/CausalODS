from kafka import KafkaConsumer

from config.settings import Settings

from consumer.otlp_deserializer import OTLPDeserializer
from normalizer.span_normalizer import SpanNormalizer

from models.trace_builder import TraceBuilder

from correlation.trace_lifecycle import (
    TraceLifecycleManager
)

from persistence.opensearch.client import (
    OpenSearchClient
)

from persistence.opensearch.trace_repository import (
    OpenSearchTraceRepository
)

from pipeline.rca_pipeline import (
    RCAPipeline
)

from persistence.opensearch.repository import (
    OpenSearchIncidentRepository
)

from persistence.opensearch.rca_result_index_manager import (
    RCAResultIndexManager
)

from persistence.opensearch.rca_result_repository import (
    OpenSearchRCAResultRepository
)

from persistence.opensearch.incident_index_manager import (
    IncidentIndexManager
)

IGNORED_OPERATIONS = frozenset(
    {
        "GET /actuator/prometheus",
        "GET /actuator/health",
    }
)

def is_noise_span(
    span,
) -> bool:

    operation_name = str(
        getattr(
            span,
            "operation_name",
            "",
        )
        or ""
    ).strip()

    return (
        operation_name
        in IGNORED_OPERATIONS
    )


class TraceConsumer:


    def __init__(self):

        self.consumer = KafkaConsumer(

            Settings.KAFKA_TOPIC,

            bootstrap_servers=
                Settings.KAFKA_BOOTSTRAP_SERVERS,

            group_id=
                Settings.CONSUMER_GROUP,

            auto_offset_reset="latest"
        )


        self.deserializer = (
            OTLPDeserializer()
        )

        self.normalizer = (
            SpanNormalizer()
        )

        self.trace_builder = (
            TraceBuilder()
        )

        self.lifecycle = (
            TraceLifecycleManager(
                timeout_seconds=5
            )
        )


        client = (
            OpenSearchClient()
            .get_client()
        )

        IncidentIndexManager(
            client
        ).create_index()

        trace_repository = (
            OpenSearchTraceRepository(
                client
            )
        )

        incident_repository = (
            OpenSearchIncidentRepository(
                client
            )
        )

        rca_result_repository = (
            OpenSearchRCAResultRepository(
                client
            )
        )

        self.pipeline = (
            RCAPipeline(
                trace_repository=(
                    trace_repository
                ),
                incident_repository=(
                    incident_repository
                ),
                rca_result_repository=(
                    rca_result_repository
                )
            )
    )


    def consume(self):

        while True:

            records = self.consumer.poll(
                timeout_ms=1000
            )


            for _, messages in records.items():

                for message in messages:

                    self.process_message(
                        message
                    )


            self.check_completed_traces()


    def process_message(
        self,
        message
    ):

        request = (
            self.deserializer
            .deserialize(
                message.value
            )
        )


        spans = (
            self.normalizer
            .normalize(
                request
            )
        )


        for span in spans:

            if is_noise_span(span):
                continue

            self.trace_builder.process_span(
                span
            )


    def check_completed_traces(self):

        finished = []

        for trace_id, trace in list(

            self.trace_builder
            .active_traces
            .items()

        ):

            if self.lifecycle.should_finish(
                trace
            ):

                finished.append(
                    trace_id
                )


        for trace_id in finished:

            trace = (
                self.trace_builder
                .finish_trace(
                    trace_id
                )
            )

            self.pipeline.process(
                trace
            )