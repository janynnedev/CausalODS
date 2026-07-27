from __future__ import annotations

from typing import Any

from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


class EvaluatorV2:

    def evaluate(
        self,
        *,
        model,
        X_test,
        y_test,
    ) -> dict[str, Any]:

        predictions = model.predict(
            X_test
        )

        probabilities = (
            model.predict_proba(
                X_test
            )
        )

        positive_probabilities = (
            probabilities[:, 1]
            if probabilities.shape[1] > 1
            else probabilities[:, 0]
        )

        metrics: dict[
            str,
            Any,
        ] = {
            "accuracy": float(
                accuracy_score(
                    y_test,
                    predictions,
                )
            ),

            "precision": float(
                precision_score(
                    y_test,
                    predictions,
                    zero_division=0,
                )
            ),

            "recall": float(
                recall_score(
                    y_test,
                    predictions,
                    zero_division=0,
                )
            ),

            "f1": float(
                f1_score(
                    y_test,
                    predictions,
                    zero_division=0,
                )
            ),

            "confusion_matrix": (
                confusion_matrix(
                    y_test,
                    predictions,
                )
                .tolist()
            ),

            "classification_report": (
                classification_report(
                    y_test,
                    predictions,
                    output_dict=True,
                    zero_division=0,
                )
            ),
        }

        if len(
            set(
                y_test.tolist()
            )
        ) > 1:

            metrics[
                "roc_auc"
            ] = float(
                roc_auc_score(
                    y_test,
                    positive_probabilities,
                )
            )

        else:

            metrics[
                "roc_auc"
            ] = None

        return metrics