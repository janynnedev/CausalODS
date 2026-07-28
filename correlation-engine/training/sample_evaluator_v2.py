from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from sklearn.model_selection import (
    GroupShuffleSplit,
)

from models.rca_feature_schema_v2 import (
    MODEL_FEATURE_COLUMNS,
)


class SampleEvaluatorV2:

    def evaluate(
        self,
        *,
        dataframe: pd.DataFrame,
        probabilities: np.ndarray,
        decision_threshold: float = 0.5,
    ) -> tuple[
        dict[str, Any],
        pd.DataFrame,
    ]:

        if len(
            dataframe
        ) != len(
            probabilities
        ):

            raise ValueError(
                (
                    "Quantidade de probabilidades "
                    "não corresponde às linhas "
                    "do dataset."
                )
            )

        evaluated = (
            dataframe.copy()
        )

        evaluated[
            "root_cause_probability"
        ] = probabilities.astype(
            float
        )

        sample_rows = []

        for (
            sample_id,
            sample,
        ) in evaluated.groupby(
            "sample_id",
            sort=True,
        ):

            scenario = str(
                sample[
                    "scenario"
                ].iloc[0]
            )

            expected_root = (
                sample[
                    "root_cause_service"
                ]
                .dropna()
            )

            expected_root_service = (
                str(
                    expected_root.iloc[0]
                )
                if not expected_root.empty
                else None
            )

            ordered = (
                sample.sort_values(
                    by=(
                        "root_cause_probability"
                    ),
                    ascending=False,
                    kind="stable",
                )
            )

            top_row = (
                ordered.iloc[0]
            )

            top_service = str(
                top_row[
                    "service_name"
                ]
            )

            top_probability = float(
                top_row[
                    "root_cause_probability"
                ]
            )

            predicted_root_service = (
                top_service
                if (
                    top_probability
                    >= decision_threshold
                )
                else None
            )

            is_healthy = (
                expected_root_service
                is None
            )

            localization_correct = (
                True
                if is_healthy
                else (
                    top_service
                    == expected_root_service
                )
            )

            sample_correct = (
                predicted_root_service
                == expected_root_service
            )

            root_rank = None

            if not is_healthy:

                ordered_services = (
                    ordered[
                        "service_name"
                    ]
                    .astype(str)
                    .tolist()
                )

                root_rank = (
                    ordered_services.index(
                        expected_root_service
                    )
                    + 1
                )

            sample_rows.append(
                {
                    "sample_id":
                        sample_id,

                    "scenario":
                        scenario,

                    "expected_root_cause":
                        expected_root_service,

                    "top_service":
                        top_service,

                    "top_probability":
                        top_probability,

                    "predicted_root_cause":
                        predicted_root_service,

                    "localization_correct":
                        bool(
                            localization_correct
                        ),

                    "sample_correct":
                        bool(
                            sample_correct
                        ),

                    "root_cause_rank":
                        root_rank,

                    "decision_threshold":
                        float(
                            decision_threshold
                        ),
                }
            )

        sample_results = pd.DataFrame(
            sample_rows
        )

        metrics = (
            self._metrics(
                sample_results
            )
        )

        return (
            metrics,
            sample_results,
        )

    def evaluate_held_out_dataset(
        self,
        *,
        dataframe: pd.DataFrame,
        predictor,
        test_size: float = 0.20,
        random_seed: int = 42,
        decision_threshold: float = 0.5,
    ) -> tuple[
        dict[str, Any],
        pd.DataFrame,
    ]:

        splitter = (
            GroupShuffleSplit(
                n_splits=1,
                test_size=test_size,
                random_state=random_seed,
            )
        )

        groups = (
            dataframe[
                "sample_id"
            ]
        )

        _, test_indexes = next(
            splitter.split(
                dataframe,
                groups=groups,
            )
        )

        test_dataframe = (
            dataframe
            .iloc[
                test_indexes
            ]
            .copy()
            .reset_index(
                drop=True
            )
        )

        model_dataframe = (
            test_dataframe[
                list(
                    MODEL_FEATURE_COLUMNS
                )
            ]
            .copy()
        )

        probabilities = (
            predictor
            .predict_dataframe(
                model_dataframe
            )
        )

        metrics, sample_results = (
            self.evaluate(
                dataframe=test_dataframe,
                probabilities=probabilities,
                decision_threshold=(
                    decision_threshold
                ),
            )
        )

        metrics.update(
            {
                "evaluation_scope":
                    "held_out_sample_ids",

                "test_size":
                    float(
                        test_size
                    ),

                "random_seed":
                    int(
                        random_seed
                    ),

                "test_rows":
                    int(
                        len(
                            test_dataframe
                        )
                    ),

                "test_samples":
                    int(
                        test_dataframe[
                            "sample_id"
                        ]
                        .nunique()
                    ),
            }
        )

        return (
            metrics,
            sample_results,
        )

    @staticmethod
    def _metrics(
        sample_results: pd.DataFrame,
    ) -> dict[str, Any]:

        healthy = sample_results[
            sample_results[
                "expected_root_cause"
            ]
            .isna()
        ]

        faults = sample_results[
            sample_results[
                "expected_root_cause"
            ]
            .notna()
        ]

        total_samples = len(
            sample_results
        )

        fault_samples = len(
            faults
        )

        healthy_samples = len(
            healthy
        )

        scenario_metrics = {}

        for (
            scenario,
            items,
        ) in sample_results.groupby(
            "scenario",
            sort=True,
        ):

            scenario_metrics[
                str(scenario)
            ] = {
                "samples":
                    int(
                        len(items)
                    ),

                "sample_accuracy":
                    float(
                        items[
                            "sample_correct"
                        ]
                        .mean()
                    ),

                "top1_localization_accuracy":
                    float(
                        items[
                            "localization_correct"
                        ]
                        .mean()
                    ),

                "mean_top_probability":
                    float(
                        items[
                            "top_probability"
                        ]
                        .mean()
                    ),
            }

        root_ranks = (
            faults[
                "root_cause_rank"
            ]
            .dropna()
        )

        return {
            "samples":
                int(
                    total_samples
                ),

            "overall_sample_accuracy":
                float(
                    sample_results[
                        "sample_correct"
                    ]
                    .mean()
                )
                if total_samples
                else 0.0,

            "fault_samples":
                int(
                    fault_samples
                ),

            "fault_detection_rate":
                float(
                    faults[
                        "predicted_root_cause"
                    ]
                    .notna()
                    .mean()
                )
                if fault_samples
                else 0.0,

            "fault_top1_localization_accuracy":
                float(
                    faults[
                        "localization_correct"
                    ]
                    .mean()
                )
                if fault_samples
                else 0.0,

            "fault_end_to_end_accuracy":
                float(
                    faults[
                        "sample_correct"
                    ]
                    .mean()
                )
                if fault_samples
                else 0.0,

            "healthy_samples":
                int(
                    healthy_samples
                ),

            "healthy_true_negative_rate":
                float(
                    healthy[
                        "sample_correct"
                    ]
                    .mean()
                )
                if healthy_samples
                else 0.0,

            "healthy_false_positive_rate":
                float(
                    (
                        healthy[
                            "predicted_root_cause"
                        ]
                        .notna()
                    )
                    .mean()
                )
                if healthy_samples
                else 0.0,

            "mean_root_cause_rank":
                float(
                    root_ranks.mean()
                )
                if not root_ranks.empty
                else None,

            "scenario_metrics":
                scenario_metrics,
        }