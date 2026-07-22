from persistence.opensearch.rca_result_repository import (
    OpenSearchRCAResultRepository,
)


class FakeClient:

    def __init__(
        self,
    ):

        self.index_calls = []

    def index(
        self,
        **kwargs,
    ):

        self.index_calls.append(
            kwargs
        )

        return {
            "result": "created",
        }


class FakeScores:

    final_score = 0.20


class FakeRankedService:

    service_name = "order-service"
    scores = FakeScores()


class FakeReliability:

    is_reliable = False


class FakeRCAResult:

    rca_result_id = "rca-test-01"
    trace_id = "trace-test-01"

    confidence = 0.20

    model_version = "model-test-v1"
    pipeline_version = "pipeline-test-v1"

    ranking = (
        FakeRankedService(),
    )

    evidence = (
        {
            "evidence_id": "ev-01",
        },
    )

    reliability = (
        FakeReliability()
    )

    def to_dict(
        self,
    ):

        return {
            "rca_result_id":
                self.rca_result_id,

            "trace_id":
                self.trace_id,

            "confidence":
                self.confidence,

            "ranking": [
                {
                    "service_name":
                        "order-service",

                    "final_score":
                        0.20,
                }
            ],

            "evidence": [
                {
                    "evidence_id":
                        "ev-01",
                }
            ],

            "reliability": {
                "is_reliable":
                    False,
            },
        }


def test_repository_persists_complete_rca_result():

    client = FakeClient()

    repository = (
        OpenSearchRCAResultRepository(
            client
        )
    )

    result = FakeRCAResult()

    returned = repository.save(
        result
    )

    assert returned is result

    assert len(
        client.index_calls
    ) == 1

    call = client.index_calls[0]

    assert (
        call["index"]
        == "causalods-rca-results"
    )

    assert (
        call["id"]
        == "rca-test-01"
    )

    document = call[
        "body"
    ]

    assert (
        document["trace_id"]
        == "trace-test-01"
    )

    assert (
        document["is_reliable"]
        is False
    )

    assert (
        document["top_service"]
        == "order-service"
    )

    assert (
        document["top_score"]
        == 0.20
    )

    assert (
        document["ranking_count"]
        == 1
    )

    assert (
        document["evidence_count"]
        == 1
    )

    assert (
        document["payload"][
            "rca_result_id"
        ]
        == "rca-test-01"
    )