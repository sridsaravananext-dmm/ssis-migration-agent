"""Validated run configuration shared by CLI, UI, and backend callers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AssessmentConfig:
    catalog: str = "main"
    layer: str = "unspecified"
    target_schema: str | None = None
    description: str = ""
    default_schema: str = "dbo"
    strict_mode: bool = False
    dry_run: bool = False
    respect_human_edits: bool = True

    def __post_init__(self):
        for key in ('catalog', 'layer', 'description', 'default_schema'):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f'{key} must be text')
        for key in ('strict_mode', 'dry_run', 'respect_human_edits'):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f'{key} must be a boolean')
        if self.target_schema is not None and not isinstance(self.target_schema, str):
            raise ValueError('Target schema must be text')
        if not self.catalog.strip() or not self.default_schema.strip():
            raise ValueError("Catalog and default SQL schema must be non-empty")
        if not self.layer.strip():
            raise ValueError("Layer must be non-empty; use 'unspecified' if unknown")
        if self.target_schema is not None and not self.target_schema.strip():
            raise ValueError("Target schema must be non-empty when supplied")

    def to_dict(self):
        return asdict(self)


def dependency_paths(single=None, multiple=()):
    """Retain the old single-file API while accepting repeatable files."""
    values = ([single] if single else []) + list(multiple or ())
    result = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Dependency file not found: {path}")
        if path not in result:
            result.append(path)
    return result
