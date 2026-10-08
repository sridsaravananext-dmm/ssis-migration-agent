from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GeneratedArtifact:
    artifact_id: str
    name: str
    artifact_type: str
    target_platform: str
    language: str
    code: str
    source_step_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type,
            "target_platform": self.target_platform,
            "language": self.language,
            "code": self.code,
            "source_step_id": self.source_step_id,
            "metadata": self.metadata,
        }
