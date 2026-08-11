"""Projeção, visualização e release validation isoladas da plataforma v3."""

from platform_v3.constants import INDEX_NAMES, REQUIRED_DASHBOARDS
from platform_v3.documents import VisualizationDocumentAdapter
from platform_v3.opensearch import OpenSearchProjectionRepository, TemplateCatalog

__all__ = [
    "INDEX_NAMES",
    "REQUIRED_DASHBOARDS",
    "OpenSearchProjectionRepository",
    "TemplateCatalog",
    "VisualizationDocumentAdapter",
]
