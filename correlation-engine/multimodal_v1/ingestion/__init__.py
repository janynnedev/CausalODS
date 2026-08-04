"""Ingestão multimodal v3 opt-in; não importada pelo runtime v2."""

from multimodal_v1.ingestion.registry import NormalizerRegistry, default_registry
from multimodal_v1.ingestion.service import MultimodalIngestionService

__all__ = ["MultimodalIngestionService", "NormalizerRegistry", "default_registry"]
