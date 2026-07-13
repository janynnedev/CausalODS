from kafka import KafkaConsumer

from config.settings import Settings
from consumer.otlp_deserializer import OTLPDeserializer
from normalizer.span_normalizer import SpanNormalizer
from models.trace_builder import TraceBuilder

from correlation.trace_lifecycle import (
    TraceLifecycleManager
)
from persistence.opensearch.client import OpenSearchClient
from persistence.opensearch.trace_repository import (
    OpenSearchTraceRepository
)
from graph.graph_builder import GraphBuilder


def is_noise_span(span):

    return (
        span.operation_name
        == "GET /actuator/prometheus"
    )



class TraceConsumer:


    def __init__(self):

        self.consumer = KafkaConsumer(
            Settings.KAFKA_TOPIC,
            bootstrap_servers=
                Settings.KAFKA_BOOTSTRAP_SERVERS
        )


        self.deserializer = OTLPDeserializer()

        self.normalizer = SpanNormalizer()

        self.trace_builder = TraceBuilder()

        self.lifecycle = TraceLifecycleManager(
            timeout_seconds=5
        )

        client = OpenSearchClient()

        self.trace_repository = (
            OpenSearchTraceRepository(
                client.get_client()
            )
        )

        self.graph_builder = GraphBuilder()


    def consume(self):

        while True:

            records = self.consumer.poll(
                timeout_ms=1000
            )


            for _, messages in records.items():

                for message in messages:

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

                        print(
                            "SPAN NORMALIZADO:",
                            span.service_name,
                            span.operation_name,
                            span.trace_id
                        )

                        if is_noise_span(span):
                            continue


                        self.trace_builder.process_span(
                            span
                        )

            self.check_completed_traces()
                    
    
    def check_completed_traces(self):

        print("Verificando traces ativos:", len(self.trace_builder.active_traces))

        finished = []

        for trace_id, trace in list(
            self.trace_builder.active_traces.items()
        ):

            if self.lifecycle.should_finish(
                trace
            ):
                print(
                    "Trace pronto para finalizar:",
                    trace_id
                )

                finished.append(trace_id)


        for trace_id in finished:

            trace = (
                self.trace_builder
                .finish_trace(
                    trace_id
                )
            )

            print(
                "\n========== TRACE FINALIZADO =========="
            )

            trace_document = (
                trace.to_dict()
            )


            self.trace_repository.save(
                trace_document
            )


            print(
                trace_document
            )

            graph = self.graph_builder.build(
                trace
            )

            print()

            print("========== SERVICE GRAPH ==========")

            print(graph)


            