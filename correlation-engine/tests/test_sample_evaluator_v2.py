import numpy as np
import pandas as pd
import pytest

from models.rca_feature_schema_v2 import (
    MODEL_FEATURE_COLUMNS,
)
from training.sample_evaluator_v2 import (
    SampleEvaluatorV2,
)


def make_dataset() -> pd.DataFrame:

    rows = [
        # -------------------------------------------------
        # Sample saudável:
        # nenhuma probabilidade ultrapassa 0.50.
        # -------------------------------------------------
        {
            "sample_id": "healthy-001",
            "scenario": "healthy",
            "service_name": "order-service",
            "root_cause_service": None,
            "fault_delay_ms": 0,
            "generation_seed": 42,
            "request_count": 2,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 80.0,
            "max_latency_ms": 90.0,
            "incoming_dependencies": 0,
            "outgoing_dependencies": 1,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 0.0,
            "anomaly_score": 0.0,
            "is_anomalous": False,
            "exclusive_latency_ms": 20.0,
            "exclusive_latency_ratio": 0.20,
            "root_cause": 0,
        },
        {
            "sample_id": "healthy-001",
            "scenario": "healthy",
            "service_name": "payment-service",
            "root_cause_service": None,
            "fault_delay_ms": 0,
            "generation_seed": 42,
            "request_count": 1,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 60.0,
            "max_latency_ms": 60.0,
            "incoming_dependencies": 1,
            "outgoing_dependencies": 0,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 0.0,
            "anomaly_score": 0.0,
            "is_anomalous": False,
            "exclusive_latency_ms": 60.0,
            "exclusive_latency_ratio": 0.30,
            "root_cause": 0,
        },

        # -------------------------------------------------
        # Falha explícita no payment-service.
        # -------------------------------------------------
        {
            "sample_id": "failure-001",
            "scenario": "payment_failure",
            "service_name": "order-service",
            "root_cause_service": "payment-service",
            "fault_delay_ms": 0,
            "generation_seed": 43,
            "request_count": 2,
            "error_count": 2,
            "error_rate": 1.0,
            "avg_latency_ms": 100.0,
            "max_latency_ms": 120.0,
            "incoming_dependencies": 0,
            "outgoing_dependencies": 1,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": 2,
            "failure_lead_time_ms": 10.0,
            "latency_zscore": 1.0,
            "anomaly_score": 0.5,
            "is_anomalous": True,
            "exclusive_latency_ms": 50.0,
            "exclusive_latency_ratio": 0.30,
            "root_cause": 0,
        },
        {
            "sample_id": "failure-001",
            "scenario": "payment_failure",
            "service_name": "payment-service",
            "root_cause_service": "payment-service",
            "fault_delay_ms": 0,
            "generation_seed": 43,
            "request_count": 2,
            "error_count": 1,
            "error_rate": 0.5,
            "avg_latency_ms": 70.0,
            "max_latency_ms": 80.0,
            "incoming_dependencies": 1,
            "outgoing_dependencies": 0,
            "dependency_count": 1,
            "is_first_failure": True,
            "failure_order": 1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 2.0,
            "anomaly_score": 1.0,
            "is_anomalous": True,
            "exclusive_latency_ms": 80.0,
            "exclusive_latency_ratio": 0.70,
            "root_cause": 1,
        },

        # -------------------------------------------------
        # Latência no order-service.
        # -------------------------------------------------
        {
            "sample_id": "order-latency-001",
            "scenario": "order_latency",
            "service_name": "order-service",
            "root_cause_service": "order-service",
            "fault_delay_ms": 1500,
            "generation_seed": 44,
            "request_count": 2,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 1550.0,
            "max_latency_ms": 1600.0,
            "incoming_dependencies": 0,
            "outgoing_dependencies": 1,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 8.0,
            "anomaly_score": 1.0,
            "is_anomalous": True,
            "exclusive_latency_ms": 1500.0,
            "exclusive_latency_ratio": 0.94,
            "root_cause": 1,
        },
        {
            "sample_id": "order-latency-001",
            "scenario": "order_latency",
            "service_name": "payment-service",
            "root_cause_service": "order-service",
            "fault_delay_ms": 1500,
            "generation_seed": 44,
            "request_count": 1,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 60.0,
            "max_latency_ms": 60.0,
            "incoming_dependencies": 1,
            "outgoing_dependencies": 0,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 0.0,
            "anomaly_score": 0.0,
            "is_anomalous": False,
            "exclusive_latency_ms": 60.0,
            "exclusive_latency_ratio": 0.06,
            "root_cause": 0,
        },

        # -------------------------------------------------
        # Latência no payment-service.
        # -------------------------------------------------
        {
            "sample_id": "payment-latency-001",
            "scenario": "payment_latency",
            "service_name": "order-service",
            "root_cause_service": "payment-service",
            "fault_delay_ms": 1500,
            "generation_seed": 45,
            "request_count": 2,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 1580.0,
            "max_latency_ms": 1620.0,
            "incoming_dependencies": 0,
            "outgoing_dependencies": 1,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 4.0,
            "anomaly_score": 0.5,
            "is_anomalous": True,
            "exclusive_latency_ms": 100.0,
            "exclusive_latency_ratio": 0.07,
            "root_cause": 0,
        },
        {
            "sample_id": "payment-latency-001",
            "scenario": "payment_latency",
            "service_name": "payment-service",
            "root_cause_service": "payment-service",
            "fault_delay_ms": 1500,
            "generation_seed": 45,
            "request_count": 1,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 1550.0,
            "max_latency_ms": 1550.0,
            "incoming_dependencies": 1,
            "outgoing_dependencies": 0,
            "dependency_count": 1,
            "is_first_failure": False,
            "failure_order": -1,
            "failure_lead_time_ms": 0.0,
            "latency_zscore": 9.0,
            "anomaly_score": 1.0,
            "is_anomalous": True,
            "exclusive_latency_ms": 1500.0,
            "exclusive_latency_ratio": 0.93,
            "root_cause": 1,
        },
    ]

    return pd.DataFrame(rows)


class FakePredictorV2:
    """
    Predictor determinístico usado somente para testar
    evaluate_held_out_dataset sem carregar XGBoost.
    """

    def __init__(self):

        self.received_columns = None

    def predict_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> np.ndarray:

        self.received_columns = (
            dataframe.columns.tolist()
        )

        # O dataset de teste foi construído para que:
        # - healthy permaneça abaixo de 0.50;
        # - causas de latência tenham ratio elevado;
        # - primeira falha explícita tenha probabilidade alta.
        probabilities = []

        for _, row in dataframe.iterrows():

            if bool(
                row["is_first_failure"]
            ):

                probability = 0.95

            elif (
                float(
                    row[
                        "exclusive_latency_ratio"
                    ]
                )
                >= 0.80
            ):

                probability = 0.90

            else:

                probability = 0.10

            probabilities.append(
                probability
            )

        return np.asarray(
            probabilities,
            dtype=float,
        )


def test_sample_evaluator_v2_evaluates_by_sample_id():

    dataframe = make_dataset()

    # Probabilidades na mesma ordem das linhas:
    #
    # healthy:
    # order=0.10, payment=0.20
    #
    # payment_failure:
    # order=0.15, payment=0.95
    #
    # order_latency:
    # order=0.90, payment=0.10
    #
    # payment_latency:
    # order=0.10, payment=0.90
    probabilities = np.asarray(
        [
            0.10,
            0.20,
            0.15,
            0.95,
            0.90,
            0.10,
            0.10,
            0.90,
        ],
        dtype=float,
    )

    evaluator = SampleEvaluatorV2()

    metrics, sample_results = (
        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=probabilities,
            decision_threshold=0.50,
        )
    )

    assert len(
        sample_results
    ) == 4

    assert (
        sample_results[
            "sample_id"
        ]
        .nunique()
        == 4
    )

    assert (
        metrics[
            "samples"
        ]
        == 4
    )

    assert (
        metrics[
            "fault_samples"
        ]
        == 3
    )

    assert (
        metrics[
            "healthy_samples"
        ]
        == 1
    )

    assert (
        metrics[
            "overall_sample_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics[
            "fault_detection_rate"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics[
            "fault_top1_localization_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics[
            "fault_end_to_end_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics[
            "healthy_true_negative_rate"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics[
            "healthy_false_positive_rate"
        ]
        == pytest.approx(
            0.0
        )
    )

    assert (
        metrics[
            "mean_root_cause_rank"
        ]
        == pytest.approx(
            1.0
        )
    )


def test_sample_evaluator_v2_returns_expected_roots():

    dataframe = make_dataset()

    probabilities = np.asarray(
        [
            0.10,
            0.20,
            0.15,
            0.95,
            0.90,
            0.10,
            0.10,
            0.90,
        ],
        dtype=float,
    )

    evaluator = SampleEvaluatorV2()

    _, results = (
        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=probabilities,
            decision_threshold=0.50,
        )
    )

    indexed = (
        results.set_index(
            "sample_id"
        )
    )

    assert pd.isna(
        indexed.loc[
            "healthy-001",
            "predicted_root_cause",
        ]
    )

    assert (
        indexed.loc[
            "failure-001",
            "predicted_root_cause",
        ]
        == "payment-service"
    )

    assert (
        indexed.loc[
            "order-latency-001",
            "predicted_root_cause",
        ]
        == "order-service"
    )

    assert (
        indexed.loc[
            "payment-latency-001",
            "predicted_root_cause",
        ]
        == "payment-service"
    )

    assert (
        indexed[
            "sample_correct"
        ]
        .all()
    )


def test_sample_evaluator_v2_detects_healthy_false_positive():

    dataframe = (
        make_dataset()
        .query(
            "sample_id == 'healthy-001'"
        )
        .reset_index(
            drop=True
        )
    )

    # Payment ultrapassa o threshold,
    # causando falso positivo no saudável.
    probabilities = np.asarray(
        [
            0.10,
            0.75,
        ],
        dtype=float,
    )

    evaluator = SampleEvaluatorV2()

    metrics, results = (
        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=probabilities,
            decision_threshold=0.50,
        )
    )

    assert (
        results.iloc[0][
            "top_service"
        ]
        == "payment-service"
    )

    assert (
        results.iloc[0][
            "predicted_root_cause"
        ]
        == "payment-service"
    )

    assert not bool(
        results.iloc[0][
            "sample_correct"
        ]
    )

    assert (
        metrics[
            "healthy_true_negative_rate"
        ]
        == pytest.approx(
            0.0
        )
    )

    assert (
        metrics[
            "healthy_false_positive_rate"
        ]
        == pytest.approx(
            1.0
        )
    )


def test_sample_evaluator_v2_respects_decision_threshold():

    dataframe = (
        make_dataset()
        .query(
            (
                "sample_id "
                "== 'payment-latency-001'"
            )
        )
        .reset_index(
            drop=True
        )
    )

    probabilities = np.asarray(
        [
            0.10,
            0.80,
        ],
        dtype=float,
    )

    evaluator = SampleEvaluatorV2()

    metrics_low, results_low = (
        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=probabilities,
            decision_threshold=0.50,
        )
    )

    metrics_high, results_high = (
        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=probabilities,
            decision_threshold=0.90,
        )
    )

    assert (
        results_low.iloc[0][
            "predicted_root_cause"
        ]
        == "payment-service"
    )

    assert pd.isna(
        results_high.iloc[0][
            "predicted_root_cause"
        ]
    )

    # O ranking continua correto nos dois casos.
    assert (
        metrics_low[
            "fault_top1_localization_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics_high[
            "fault_top1_localization_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    # Mas a decisão end-to-end falha com threshold 0.90.
    assert (
        metrics_low[
            "fault_end_to_end_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        metrics_high[
            "fault_end_to_end_accuracy"
        ]
        == pytest.approx(
            0.0
        )
    )


def test_sample_evaluator_v2_rejects_probability_length_mismatch():

    dataframe = make_dataset()

    evaluator = SampleEvaluatorV2()

    with pytest.raises(
        ValueError,
        match=(
            "Quantidade de probabilidades"
        ),
    ):

        evaluator.evaluate(
            dataframe=dataframe,
            probabilities=np.asarray(
                [
                    0.10,
                    0.20,
                ]
            ),
        )


def test_sample_evaluator_v2_produces_scenario_metrics():

    dataframe = make_dataset()

    probabilities = np.asarray(
        [
            0.10,
            0.20,
            0.15,
            0.95,
            0.90,
            0.10,
            0.10,
            0.90,
        ],
        dtype=float,
    )

    evaluator = SampleEvaluatorV2()

    metrics, _ = evaluator.evaluate(
        dataframe=dataframe,
        probabilities=probabilities,
        decision_threshold=0.50,
    )

    scenario_metrics = (
        metrics[
            "scenario_metrics"
        ]
    )

    assert set(
        scenario_metrics.keys()
    ) == {
        "healthy",
        "payment_failure",
        "order_latency",
        "payment_latency",
    }

    assert (
        scenario_metrics[
            "healthy"
        ][
            "samples"
        ]
        == 1
    )

    assert (
        scenario_metrics[
            "payment_failure"
        ][
            "sample_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        scenario_metrics[
            "order_latency"
        ][
            "top1_localization_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )

    assert (
        scenario_metrics[
            "payment_latency"
        ][
            "sample_accuracy"
        ]
        == pytest.approx(
            1.0
        )
    )


def test_held_out_evaluation_uses_only_model_features():

    dataframe = make_dataset()

    predictor = FakePredictorV2()

    evaluator = SampleEvaluatorV2()

    metrics, sample_results = (
        evaluator
        .evaluate_held_out_dataset(
            dataframe=dataframe,
            predictor=predictor,
            test_size=0.50,
            random_seed=42,
            decision_threshold=0.50,
        )
    )

    assert (
        predictor.received_columns
        == list(
            MODEL_FEATURE_COLUMNS
        )
    )

    # Existem quatro sample_ids e test_size=0.50.
    assert (
        metrics[
            "test_samples"
        ]
        == 2
    )

    # Cada sample possui exatamente dois serviços.
    assert (
        metrics[
            "test_rows"
        ]
        == 4
    )

    assert (
        len(
            sample_results
        )
        == 2
    )

    assert (
        sample_results[
            "sample_id"
        ]
        .nunique()
        == 2
    )

    assert (
        metrics[
            "evaluation_scope"
        ]
        == "held_out_sample_ids"
    )

    assert (
        metrics[
            "random_seed"
        ]
        == 42
    )

    assert (
        metrics[
            "test_size"
        ]
        == pytest.approx(
            0.50
        )
    )