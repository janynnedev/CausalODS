from __future__ import annotations

from models.rca_context import RCAContext
from models.rca_result import RCAResult

from ranking.evidence_builder import (
    EvidenceBuilder,
)

from ranking.ranking_service import (
    RCARankingService,
)

from ranking.reliability_gate import (
    ReliabilityGate,
)


class RCAResultFactory:
    """
    Orquestra a conversão:

        RCAContext
        → EvidenceItem[]
        → RankedService[]
        → ReliabilityDecision
        → RCAResult
    """

    FACTORY_VERSION = "rca-result-factory-v1"

    def __init__(
        self,
        *,
        evidence_builder: (
            EvidenceBuilder | None
        ) = None,
        ranking_service: (
            RCARankingService | None
        ) = None,
        reliability_gate: (
            ReliabilityGate | None
        ) = None,
    ) -> None:

        self.evidence_builder = (
            evidence_builder
            if evidence_builder is not None
            else EvidenceBuilder()
        )

        self.ranking_service = (
            ranking_service
            if ranking_service is not None
            else RCARankingService()
        )

        self.reliability_gate = (
            reliability_gate
            if reliability_gate is not None
            else ReliabilityGate()
        )

    def build(
        self,
        context: RCAContext,
    ) -> RCAResult:

        context.validate(
            strict=True
        )

        evidence = (
            self.evidence_builder.build(
                context
            )
        )

        ranking = (
            self.ranking_service
            .rank_context(
                context=context,
                evidence=evidence,
            )
        )

        reliability = (
            self.reliability_gate
            .evaluate(
                ranking=ranking,
                evidence=evidence,
            )
        )

        return RCAResult.create(
            trace_id=context.trace_id,
            ranking=list(ranking),
            evidence=list(evidence),
            reliability=reliability,
            model_version=(
                context.model_version
            ),
            pipeline_version=(
                context.pipeline_version
            ),
            metadata={
                "factory": (
                    "RCAResultFactory"
                ),
                "factory_version": (
                    self.FACTORY_VERSION
                ),
                "service_count": len(
                    context.services
                ),
                "evidence_count": len(
                    evidence
                ),
                "adapter_metadata": dict(
                    context.adapter_metadata
                ),
            },
        )