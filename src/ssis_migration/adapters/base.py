"""Source boundaries that preserve the established SSIS compatibility API.

The source envelope is versioned separately from the legacy IR. Registering an
adapter does not establish semantic equivalence or target execution readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class UnsupportedSourceError(ValueError):
    """A source platform, signature or export version is not supported."""

    def __init__(self, message: str, *, diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


class SourceEvidenceError(ValueError):
    """Supplied file evidence no longer identifies the bytes being parsed."""


@dataclass
class SourceAdapterResult:
    """Parsed source and compatibility IR with an evidence-backed envelope."""

    parsed: dict[str, Any]
    package_ir: Any
    analysis: dict[str, Any]
    source_envelope: dict[str, Any]
    normalized_ir: dict[str, Any] | None = None

    def to_legacy_result(self) -> dict[str, Any]:
        """Keep existing caller keys and add the neutral source envelope."""
        result = {
            "parsed": self.parsed,
            "package_ir": self.package_ir,
            "analysis": self.analysis,
            "source_envelope": self.source_envelope,
        }
        if self.normalized_ir is not None:
            result["normalized_ir"] = self.normalized_ir
        return result


@runtime_checkable
class SourceAdapter(Protocol):
    """Minimal common entry point for source-specific analysis.

    ``package_ir`` remains the existing IR during this incremental migration.
    Source adapters must normalize syntax before common analysis and preserve
    unsupported behavior explicitly rather than substituting another parser.
    Implemented adapters may additionally return a schema-validated neutral
    graph in ``normalized_ir`` while legacy consumers retain ``package_ir``.
    """

    platform: str

    def analyze(
        self,
        source_path: str | Path,
        *,
        procedures_path: str | Path | None = None,
        file_record: dict[str, Any] | None = None,
    ) -> SourceAdapterResult:
        ...
