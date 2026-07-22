from datetime import (
    datetime,
    timezone,
)

from persistence.opensearch.rca_result_index_manager import (
    RCAResultIndexManager,
)


class OpenSearchRCAResultRepository:

    INDEX = RCAResultIndexManager.INDEX

    def __init__(
        self,
        client,
    ):

        self.client = client

    def save(
        self,
        rca_result,
    ):

        ranking = tuple(
            rca_result.ranking
            or ()
        )

        evidence = tuple(
            rca_result.evidence
            or ()
        )

        top_service = (
            ranking[0]
            if ranking
            else None
        )

        document = {
            "rca_result_id":
                rca_result.rca_result_id,

            "trace_id":
                rca_result.trace_id,

            "persisted_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "confidence":
                float(
                    rca_result.confidence
                ),

            "is_reliable":
                bool(
                    rca_result
                    .reliability
                    .is_reliable
                ),

            "top_service":
                (
                    top_service.service_name
                    if top_service is not None
                    else None
                ),

            "top_score":
                (
                    float(
                        top_service
                        .scores
                        .final_score
                    )
                    if top_service is not None
                    else 0.0
                ),

            "model_version":
                str(
                    getattr(
                        rca_result,
                        "model_version",
                        "unknown",
                    )
                ),

            "pipeline_version":
                str(
                    getattr(
                        rca_result,
                        "pipeline_version",
                        "unknown",
                    )
                ),

            "ranking_count":
                len(ranking),

            "evidence_count":
                len(evidence),

            "payload":
                rca_result.to_dict(),
        }

        self.client.index(
            index=self.INDEX,
            id=(
                rca_result
                .rca_result_id
            ),
            body=document,
        )

        return rca_result

    def find_by_id(
        self,
        rca_result_id,
    ):

        response = self.client.get(
            index=self.INDEX,
            id=rca_result_id,
        )

        return response[
            "_source"
        ]

    def find_by_trace_id(
        self,
        trace_id,
        size=100,
    ):

        response = self.client.search(
            index=self.INDEX,
            body={
                "size": size,
                "sort": [
                    {
                        "persisted_at": {
                            "order": "desc",
                        }
                    }
                ],
                "query": {
                    "term": {
                        "trace_id": trace_id,
                    }
                },
            },
        )

        return [
            item["_source"]
            for item in response[
                "hits"
            ][
                "hits"
            ]
        ]

    def find_all(
        self,
        size=100,
    ):

        response = self.client.search(
            index=self.INDEX,
            body={
                "size": size,
                "sort": [
                    {
                        "persisted_at": {
                            "order": "desc",
                        }
                    }
                ],
                "query": {
                    "match_all": {}
                },
            },
        )

        return [
            item["_source"]
            for item in response[
                "hits"
            ][
                "hits"
            ]
        ]