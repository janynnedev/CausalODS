from __future__ import annotations

from models.evidence import EvidenceItem
from models.rca_result import (
    RankedService,
    ReliabilityDecision,
)


class ReliabilityGate:
    """
    Decide se um RCAResult possui suporte suficiente para criar
    um incidente operacional.

    Um resultado não confiável continua sendo armazenável e
    avaliável. Ele apenas não deve disparar automaticamente uma
    conclusão operacional.
    """

    METHOD_VERSION = "reliability-gate-v1"

    def __init__(
        self,
        minimum_confidence: float = 0.50,
        minimum_margin: float = 0.05,
        minimum_evidence_count: int = 2,
        minimum_source_count: int = 2,
    ) -> None:

        self.minimum_confidence = (
            self._score(
                minimum_confidence
            )
        )

        self.minimum_margin = (
            self._score(
                minimum_margin
            )
        )

        if minimum_evidence_count < 1:

            raise ValueError(
                "minimum_evidence_count deve ser "
                "maior que zero."
            )

        if minimum_source_count < 1:

            raise ValueError(
                "minimum_source_count deve ser "
                "maior que zero."
            )

        self.minimum_evidence_count = (
            minimum_evidence_count
        )

        self.minimum_source_count = (
            minimum_source_count
        )

    def evaluate(
        self,
        ranking: tuple[
            RankedService,
            ...,
        ],
        evidence: tuple[
            EvidenceItem,
            ...,
        ],
    ) -> ReliabilityDecision:

        passed_rules: list[str] = []
        failed_rules: list[str] = []

        if not ranking:

            failed_rules.append(
                "ranking_present"
            )

            return ReliabilityDecision(
                is_reliable=False,
                confidence=0.0,
                confidence_method=(
                    self.METHOD_VERSION
                ),
                minimum_confidence=(
                    self.minimum_confidence
                ),
                margin_to_second=None,
                minimum_margin=(
                    self.minimum_margin
                ),
                passed_rules=tuple(
                    passed_rules
                ),
                failed_rules=tuple(
                    failed_rules
                ),
            )

        passed_rules.append(
            "ranking_present"
        )

        top = ranking[0]

        confidence = (
            top.scores.final_score
        )

        if confidence >= (
            self.minimum_confidence
        ):
            passed_rules.append(
                "minimum_confidence"
            )
        else:
            failed_rules.append(
                "minimum_confidence"
            )

        if len(ranking) >= 2:

            margin_to_second = max(
                confidence
                - ranking[
                    1
                ].scores.final_score,
                0.0,
            )

            if margin_to_second >= (
                self.minimum_margin
            ):
                passed_rules.append(
                    "minimum_margin"
                )
            else:
                failed_rules.append(
                    "minimum_margin"
                )

        else:

            margin_to_second = None

            passed_rules.append(
                "single_candidate"
            )

        top_evidence = tuple(
            item
            for item in evidence
            if getattr(
                item,
                "service_name",
                None,
            )
            == top.service_name
        )

        if len(top_evidence) >= (
            self.minimum_evidence_count
        ):
            passed_rules.append(
                "minimum_evidence_count"
            )
        else:
            failed_rules.append(
                "minimum_evidence_count"
            )

        source_components = {
            str(
                getattr(
                    item,
                    "source_component",
                    "",
                )
            )
            for item in top_evidence
            if getattr(
                item,
                "source_component",
                None,
            )
        }

        if len(source_components) >= (
            self.minimum_source_count
        ):
            passed_rules.append(
                "minimum_source_diversity"
            )
        else:
            failed_rules.append(
                "minimum_source_diversity"
            )

        return ReliabilityDecision(
            is_reliable=(
                len(failed_rules) == 0
            ),
            confidence=confidence,
            confidence_method=(
                self.METHOD_VERSION
            ),
            minimum_confidence=(
                self.minimum_confidence
            ),
            margin_to_second=(
                margin_to_second
            ),
            minimum_margin=(
                self.minimum_margin
            ),
            passed_rules=tuple(
                passed_rules
            ),
            failed_rules=tuple(
                failed_rules
            ),
        )

    def _score(
        self,
        value,
    ) -> float:

        converted = float(value)

        return min(
            max(converted, 0.0),
            1.0,
        )