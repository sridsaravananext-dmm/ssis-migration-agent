from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AIMigrationRequest:

    agent: dict[str, Any]
    task: dict[str, Any]
    migration_strategy: dict[str, Any]

    source_context: dict[str, Any]
    workflow_context: dict[str, Any]
    destination_context: dict[str, Any]

    requirements: dict[str, Any]

    evidence: dict[str, Any] = field(
        default_factory=dict
    )

    instructions: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task": self.task,
            "migration_strategy": self.migration_strategy,
            "source_context": self.source_context,
            "workflow_context": self.workflow_context,
            "destination_context": self.destination_context,
            "requirements": self.requirements,
            "evidence": self.evidence,
            "instructions": self.instructions,
        }

@dataclass
class AIMigrationResult:

    status: str
    language: str

    code: str

    assumptions: list[str] = field(
        default_factory=list
    )

    unresolved_items: list[str] = field(
        default_factory=list
    )

    validation_requirements: list[str] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "language": self.language,
            "code": self.code,
            "assumptions": self.assumptions,
            "unresolved_items": self.unresolved_items,
            "validation_requirements": (
                self.validation_requirements
            ),
            "metadata": self.metadata,
        }
