import pytest
from pipeline.rca_context_factory import (
    RCAContextFactory,
)

from pipeline.result_adapters import (
    PredictionResultAdapter,
    TemporalResultAdapter,
    normalize_feature_vectors,
    ShapResultAdapter,
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

def test_shap_adapter_accepts_v2_list_results():

    raw_results = [
        {
            "service":
                "order-service",

            "service_name":
                "order-service",

            "support_score":
                0.09604661999652761,

            "raw_positive_support":
                1.0144933330047934,

            "raw_negative_support":
                9.548016134183854,

            "contributions": [
                {
                    "feature_name":
                        "exclusive_latency_ratio",

                    "feature_value":
                        0.01,

                    "shap_value":
                        -4.5,

                    "direction":
                        "opposing",
                },
                {
                    "feature_name":
                        "max_latency_ms",

                    "feature_value":
                        1590.0,

                    "shap_value":
                        1.0144933330047934,

                    "direction":
                        "supporting",
                },
            ],
        },
        {
            "service":
                "payment-service",

            "service_name":
                "payment-service",

            "support_score":
                0.8936888450031673,

            "raw_positive_support":
                9.777010953461286,

            "raw_negative_support":
                1.163050577044487,

            "contributions": [
                {
                    "feature_name":
                        "exclusive_latency_ratio",

                    "feature_value":
                        0.99,

                    "shap_value":
                        7.5,

                    "direction":
                        "supporting",
                },
                {
                    "feature_name":
                        "exclusive_latency_ms",

                    "feature_value":
                        1575.0,

                    "shap_value":
                        2.277010953461286,

                    "direction":
                        "supporting",
                },
                {
                    "feature_name":
                        "failure_order",

                    "feature_value":
                        -1,

                    "shap_value":
                        -1.163050577044487,

                    "direction":
                        "opposing",
                },
            ],
        },
    ]

    feature_vectors = {
        "order-service": {
            "exclusive_latency_ratio":
                0.01,

            "max_latency_ms":
                1590.0,
        },
        "payment-service": {
            "exclusive_latency_ratio":
                0.99,

            "exclusive_latency_ms":
                1575.0,

            "failure_order":
                -1,
        },
    }

    adapted = (
        ShapResultAdapter()
        .adapt(
            raw=raw_results,
            services=(
                "order-service",
                "payment-service",
            ),
            feature_vectors=(
                feature_vectors
            ),
        )
    )

    assert (
        adapted[
            "order-service"
        ][
            "support_score"
        ]
        == pytest.approx(
            0.09604661999652761
        )
    )

    assert (
        adapted[
            "payment-service"
        ][
            "support_score"
        ]
        == pytest.approx(
            0.8936888450031673
        )
    )

    assert (
        adapted[
            "payment-service"
        ][
            "raw_positive_support"
        ]
        == pytest.approx(
            9.777010953461286
        )
    )

    assert (
        adapted[
            "payment-service"
        ][
            "raw_negative_support"
        ]
        == pytest.approx(
            1.163050577044487
        )
    )

    assert len(
        adapted[
            "payment-service"
        ][
            "contributions"
        ]
    ) == 3

    assert (
        adapted[
            "payment-service"
        ][
            "support_normalization_method"
        ]
        == "explicit_shap_support_score"
    )


def test_shap_adapter_preserves_legacy_mapping():

    raw_results = {
        "order-service": {
            "feature_a": -2.0,
            "feature_b": 1.0,
        },
        "payment-service": {
            "feature_a": 4.0,
            "feature_b": -1.0,
        },
    }

    adapted = (
        ShapResultAdapter()
        .adapt(
            raw=raw_results,
            services=(
                "order-service",
                "payment-service",
            ),
            feature_vectors={
                "order-service": {},
                "payment-service": {},
            },
        )
    )

    assert (
        adapted[
            "order-service"
        ][
            "support_score"
        ]
        == pytest.approx(
            0.25
        )
    )

    assert (
        adapted[
            "payment-service"
        ][
            "support_score"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        adapted[
            "payment-service"
        ][
            "support_normalization_method"
        ]
        == (
            "max_normalized_positive_support"
        )
    )    