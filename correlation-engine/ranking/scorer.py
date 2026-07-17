from __future__ import annotations

import math

from dataclasses import dataclass
from numbers import Real
from typing import Any


@dataclass(frozen=True)
class RCAWeights:
    """
    Pesos iniciais do ranking.

    anomaly possui peso zero nesta etapa para preservar o
    comportamento anterior do ranking. O score de anomalia já
    será registrado no RCAResult e poderá ser ativado nos
    experimentos de ablação.
    """

    model_probability: float = 0.40

    shap_support: float = 0.25

    temporal: float = 0.20

    graph: float = 0.15

    anomaly: float = 0.00

    def __post_init__(self) -> None:

        values = {
            "model_probability": (
                self.model_probability
            ),
            "shap_support": (
                self.shap_support
            ),
            "temporal": self.temporal,
            "graph": self.graph,
            "anomaly": self.anomaly,
        }

        for name, value in values.items():

            if not isinstance(value, Real):

                raise TypeError(
                    f"O peso {name!r} deve ser numérico."
                )

            if not math.isfinite(float(value)):

                raise ValueError(
                    f"O peso {name!r} deve ser finito."
                )

            if float(value) < 0.0:

                raise ValueError(
                    f"O peso {name!r} não pode ser negativo."
                )

        if self.total <= 0.0:

            raise ValueError(
                "A soma dos pesos deve ser maior que zero."
            )

    @property
    def total(self) -> float:

        return float(
            self.model_probability
            + self.shap_support
            + self.temporal
            + self.graph
            + self.anomaly
        )

    def to_dict(self) -> dict[str, float]:

        return {
            "model_probability": float(
                self.model_probability
            ),
            "shap_support": float(
                self.shap_support
            ),
            "temporal": float(
                self.temporal
            ),
            "graph": float(
                self.graph
            ),
            "anomaly": float(
                self.anomaly
            ),
        }


class RCAScorer:

    def __init__(
        self,
        weights: RCAWeights | None = None,
    ) -> None:

        self.weights = (
            weights
            if weights is not None
            else RCAWeights()
        )

    def calculate(
        self,
        evidence_or_model_probability,
        shap_support=None,
        temporal=None,
        graph=None,
        anomaly=0.0,
    ) -> float:
        """
        Mantém compatibilidade com as duas APIs:

        Nova/objeto:
            calculate(rca_evidence)

        Legada/posicional:
            calculate(
                model_probability,
                shap_support,
                temporal,
                graph,
            )
        """

        is_evidence_object = (
            shap_support is None
            and temporal is None
            and graph is None
            and not isinstance(
                evidence_or_model_probability,
                (int, float, bool),
            )
        )

        if is_evidence_object:

            evidence = (
                evidence_or_model_probability
            )

            return self.score_components(
                model_probability=getattr(
                    evidence,
                    "xgboost_score",
                    getattr(
                        evidence,
                        "model_probability",
                        0.0,
                    ),
                ),
                shap_support=getattr(
                    evidence,
                    "shap_score",
                    getattr(
                        evidence,
                        "shap_support_score",
                        0.0,
                    ),
                ),
                temporal=getattr(
                    evidence,
                    "temporal_score",
                    0.0,
                ),
                graph=getattr(
                    evidence,
                    "graph_score",
                    0.0,
                ),
                anomaly=getattr(
                    evidence,
                    "anomaly_score",
                    0.0,
                ),
            )

        return self.score_components(
            model_probability=(
                evidence_or_model_probability
            ),
            shap_support=shap_support,
            temporal=temporal,
            graph=graph,
            anomaly=anomaly,
        )

    def score_components(
        self,
        *,
        model_probability: Any,
        shap_support: Any,
        temporal: Any,
        graph: Any,
        anomaly: Any = 0.0,
    ) -> float:

        model_probability = self._score(
            model_probability
        )

        shap_support = self._score(
            shap_support
        )

        temporal = self._score(
            temporal
        )

        graph = self._score(
            graph
        )

        anomaly = self._score(
            anomaly
        )

        weighted_sum = (
            model_probability
            * self.weights.model_probability

            + shap_support
            * self.weights.shap_support

            + temporal
            * self.weights.temporal

            + graph
            * self.weights.graph

            + anomaly
            * self.weights.anomaly
        )

        final_score = (
            weighted_sum
            / self.weights.total
        )

        return self._score(
            final_score
        )

    def _score(
        self,
        value: Any,
    ) -> float:

        if value is None:
            return 0.0

        if isinstance(value, bool):
            return float(value)

        try:
            converted = float(value)
        except (TypeError, ValueError):
            return 0.0

        if not math.isfinite(converted):
            return 0.0

        return min(
            max(converted, 0.0),
            1.0,
        )