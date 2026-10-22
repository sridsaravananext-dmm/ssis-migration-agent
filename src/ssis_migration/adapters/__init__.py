"""Source adapters, introduced without replacing existing migration APIs."""

from .base import (
    SourceAdapter,
    SourceAdapterResult,
    SourceEvidenceError,
    UnsupportedSourceError,
)
from .registry import EXCLUDED_PLATFORMS, SOURCE_PLATFORMS, get_adapter

__all__ = [
    "SourceAdapter", "SourceAdapterResult", "SourceEvidenceError",
    "UnsupportedSourceError", "SOURCE_PLATFORMS", "EXCLUDED_PLATFORMS",
    "get_adapter",
]
