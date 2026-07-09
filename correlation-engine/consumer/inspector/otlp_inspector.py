from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest
)


class OTLPInspector:

    def inspect(self, request: ExportTraceServiceRequest):

        print("\n==============================")
        print("EXPORT TRACE REQUEST")
        print("==============================")

        for resource in request.resource_spans:

            print("\n========== RESOURCE ==========")

            print("\nResource Attributes:")

            for attr in resource.resource.attributes:

                print(
                    f"  {attr.key}: {attr.value}"
                )

            print()

            print(
                f"ScopeSpans: {len(resource.scope_spans)}"
            )

            for scope in resource.scope_spans:

                print("\n----- Instrumentation Scope -----")

                print(scope.scope.name)

                print(scope.scope.version)

                print()

                print(
                    f"Spans: {len(scope.spans)}"
                )

                for span in scope.spans:

                    print("\nSPAN")

                    print("Name:", span.name)

                    print("TraceID:", span.trace_id.hex())

                    print("SpanID:", span.span_id.hex())

                    print("Parent:", span.parent_span_id.hex())

                    print("Kind:", span.kind)

                    print("Status:", span.status.code)

                    print()

                    print("Attributes:")

                    for attribute in span.attributes:

                        print(
                            f"   {attribute.key}: {attribute.value}"
                        )

                    print()

                    print(
                        "Events:",
                        len(span.events)
                    )

                    print(
                        "Links:",
                        len(span.links)
                    )