"""Baselines simples e auditáveis para comparação com o CausalODS."""

from .contracts import BaselinePrediction, BaselineSample
from .selectors import (
    FirstAnomalyBaseline,
    MaxAnomalyZScoreBaseline,
    MaxTemporalScoreBaseline,
)

__all__ = [
    "BaselinePrediction",
    "BaselineSample",
    "FirstAnomalyBaseline",
    "MaxAnomalyZScoreBaseline",
    "MaxTemporalScoreBaseline",
]
