from pipeline.rca_context_factory import (
    RCAContextFactory,
)

from pipeline.result_adapters import (
    PredictionResultAdapter,
    TemporalResultAdapter,
    normalize_feature_vectors,
)


class FakeFeature:

    def __init__(
        self,
        service_name,
        latency_zscore,
        graph_score,
    ):

        self.service_name = service_name
        self.latency_zscore = (
            latency_zscore
        )
        self.graph_score = graph_score


class FakeTrace:

    def __init__(self):

        self.trace_id = "trace-001"

        self.services = [
            "order-service",
            "payment-service",
        ]

        self.spans = {}


def test_normalizes_feature_list():

    normalized = (
        normalize_feature_vectors(
            [
                FakeFeature(
                    "order-service",
                    1.0,
                    0.2,
                ),
                FakeFeature(
                    "payment-service",
                    4.0,
                    0.8,
                ),
            ]
        )
    )

    assert "payment-service" in normalized

    assert (
        normalized[
            "payment-service"
        ]["latency_zscore"]
        == 4.0
    )


def test_temporal_adapter_reads_services():

    adapter = TemporalResultAdapter()

    result = adapter.adapt(
        raw={
            "services": {
                "payment-service": {
                    "score": 0.9,
                    "is_first_failure": True,
                },
                "order-service": {
                    "score": 0.2,
                },
            }
        },
        services=(
            "order-service",
            "payment-service",
        ),
    )

    assert (
        result["payment-service"]["score"]
        == 0.9
    )

    assert (
        result["payment-service"][
            "is_first_anomaly"
        ]
        is True
    )


def test_prediction_adapter():

    adapter = PredictionResultAdapter()

    result = adapter.adapt(
        raw={
            "order-service": 0.2,
            "payment-service": 0.8,
        },
        services=(
            "order-service",
            "payment-service",
        ),
    )

    assert (
        result["payment-service"][
            "probability"
        ]
        == 0.8
    )

def test_prediction_adapter_uses_confidence_from_predictor_list():

    raw = [
        {
            "service": "order-service",
            "prediction": 0,
            "confidence": 0.10,
        },
        {
            "service": "payment-service",
            "prediction": 1,
            "confidence": 0.85,
        },
    ]

    result = (
        PredictionResultAdapter()
        .adapt(
            raw=raw,
            services=(
                "order-service",
                "payment-service",
            ),
        )
    )

    assert (
        result[
            "order-service"
        ][
            "probability"
        ]
        == 0.10
    )

    assert (
        result[
            "payment-service"
        ][
            "probability"
        ]
        == 0.85
    )

    assert (
        result[
            "payment-service"
        ][
            "prediction"
        ]
        == 1
    )

def test_builds_rca_context():

    factory = RCAContextFactory()

    context = factory.from_legacy(
        trace=FakeTrace(),
        feature_vectors=[
            FakeFeature(
                "order-service",
                1.0,
                0.2,
            ),
            FakeFeature(
                "payment-service",
                4.0,
                0.8,
            ),
        ],
        temporal_results={
            "services": {
                "payment-service": {
                    "score": 0.9,
                    "is_first_failure": True,
                },
                "order-service": {
                    "score": 0.2,
                },
            }
        },
        predictions={
            "order-service": 0.2,
            "payment-service": 0.8,
        },
        shap_results={
            "order-service": {
                "latency_zscore": 0.05,
            },
            "payment-service": {
                "latency_zscore": 0.42,
            },
        },
        model_version="xgboost-rca-v1",
    )

    assert context.trace_id == (
        "trace-001"
    )

    assert (
        context.predictions[
            "payment-service"
        ]["probability"]
        == 0.8
    )

    assert (
        context.temporal_results[
            "payment-service"
        ]["is_first_anomaly"]
        is True
    )

    assert (
        context.anomaly_results[
            "payment-service"
        ]["score"]
        == 1.0
    )