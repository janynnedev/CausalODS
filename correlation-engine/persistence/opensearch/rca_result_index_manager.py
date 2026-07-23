class RCAResultIndexManager:

    INDEX = "causalods-rca-results"

    def __init__(
        self,
        client,
    ):

        self.client = client

    def create_index(
        self,
    ):

        if self.client.indices.exists(
            index=self.INDEX
        ):

            return

        self.client.indices.create(
            index=self.INDEX,
            body={
                "settings": {
                    "index": {
                        "number_of_shards": 1,
                        "number_of_replicas": 1,
                    }
                },
                "mappings": {
                    "dynamic": False,
                    "properties": {
                        "rca_result_id": {
                            "type": "keyword",
                        },
                        "trace_id": {
                            "type": "keyword",
                        },
                        "persisted_at": {
                            "type": "date",
                        },
                        "confidence": {
                            "type": "float",
                        },
                        "is_reliable": {
                            "type": "boolean",
                        },
                        "top_service": {
                            "type": "keyword",
                        },
                        "top_score": {
                            "type": "float",
                        },
                        "model_version": {
                            "type": "keyword",
                        },
                        "pipeline_version": {
                            "type": "keyword",
                        },
                        "ranking_count": {
                            "type": "integer",
                        },
                        "evidence_count": {
                            "type": "integer",
                        },
                        "payload": {
                            "type": "object",
                            "enabled": False,
                        },
                    },
                },
            },
        )