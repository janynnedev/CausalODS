"""Orquestração científica multimodal v3, isolada do runtime trace-only v2."""

from .core import (
    CampaignStoreV3, CampaignValidatorV3, DatasetCapabilityResolverV3,
    DatasetCapabilityV3,
    ExperimentConfigV3, ExperimentOrchestratorV3, ExperimentPlannerV3,
    ExperimentReportBuilderV3, ExperimentRunResultV3, ExperimentRunV3,
    ExperimentStatisticsV3, RunStatusV3, derive_validation_split,
)
from .executors import (
    H1ExperimentExecutor, H2ExperimentExecutor, H3ExperimentExecutor,
    H4ExperimentExecutor,
)

__all__ = [
    "CampaignStoreV3", "CampaignValidatorV3", "DatasetCapabilityResolverV3",
    "DatasetCapabilityV3",
    "ExperimentConfigV3", "ExperimentOrchestratorV3", "ExperimentPlannerV3",
    "ExperimentReportBuilderV3", "ExperimentRunResultV3", "ExperimentRunV3",
    "ExperimentStatisticsV3", "H1ExperimentExecutor", "H2ExperimentExecutor",
    "H3ExperimentExecutor", "H4ExperimentExecutor", "RunStatusV3",
    "derive_validation_split",
]
