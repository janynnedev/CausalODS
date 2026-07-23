class IncidentIndexManager:

    INDEX = "causalods-incidents"

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
                        "incident_id": {
                            "type": "keyword",
                        },

                        "timestamp": {
                            "type": "date",
                        },

                        "status": {
                            "type": "keyword",
                        },

                        "root_cause": {
                            "properties": {
                                "service": {
                                    "type": "keyword",
                                },

                                "confidence": {
                                    "type": "float",
                                },
                            },
                        },

                        "evidence": {
                            "type": "object",
                            "enabled": False,
                        },
                    },
                },
            },
        )