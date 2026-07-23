from typing import Any

from persistence.opensearch.trace_mapper import (
    OpenSearchTraceMapper,
)


class OpenSearchTraceRepository:

    INDEX = "causalods-traces"

    INDEX_DEFINITION = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },

        "mappings": {
            "dynamic": False,

            "properties": {
                "trace_id": {
                    "type": "keyword",
                },

                "root_span": {
                    "type": "keyword",
                },

                "services": {
                    "type": "keyword",
                },

                "span_count": {
                    "type": "integer",
                },

                "duration_ms": {
                    "type": "double",
                },

                "errors": {
                    "type": "integer",
                },

                "spans": {
                    "type": "nested",
                    "dynamic": False,

                    "properties": {
                        "span_id": {
                            "type": "keyword",
                        },

                        "service": {
                            "type": "keyword",
                        },

                        "operation": {
                            "type": "keyword",
                        },

                        "children": {
                            "type": "keyword",
                        },
                    },
                },
            },
        },
    }


    def __init__(
        self,
        client: Any,
    ) -> None:

        self.client = client

        self.mapper = (
            OpenSearchTraceMapper()
        )

        self.ensure_index()


    def ensure_index(
        self,
    ) -> None:

        exists = (
            self.client
            .indices
            .exists(
                index=self.INDEX
            )
        )

        if exists:
            return

        self.client.indices.create(
            index=self.INDEX,
            body=self.INDEX_DEFINITION,
        )


    def save(
        self,
        trace: dict[str, Any],
    ) -> dict[str, Any]:

        document = (
            self.mapper.map(
                trace
            )
        )

        trace_id = document.get(
            "trace_id"
        )

        if not trace_id:
            raise ValueError(
                "Não é possível persistir "
                "um trace sem trace_id."
            )

        self.client.index(
            index=self.INDEX,
            id=str(trace_id),
            body=document,
            refresh=False,
        )

        return trace


    def find_by_id(
        self,
        trace_id: str,
    ) -> dict[str, Any]:

        response = self.client.get(
            index=self.INDEX,
            id=trace_id,
        )

        return response["_source"]