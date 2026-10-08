from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SemanticRisk:
    """
    A place where target implementation could diverge from
    required source behavior.
    """

    description: str
    source_behavior: str
    target_behavior: str
    severity: str
    mitigation: str | None = None


@dataclass
class MigrationContract:
    """
    Platform-independent migration specification.

    The contract contains normalized migration semantics rather
    than raw source syntax or platform-specific implementation
    details.
    """

    # Identity
    step_id: str
    source_platform: str
    target_platform: str

    # Semantic analysis
    source_behavior: dict[str, Any]
    required_behavior: dict[str, Any]

    # Target analysis
    target_capabilities: dict[str, Any] = field(
        default_factory=dict
    )
    target_design: dict[str, Any] = field(
        default_factory=dict
    )

    # Cross-cutting concerns
    assumptions: list[str] = field(
        default_factory=list
    )
    unresolved_items: list[str] = field(
        default_factory=list
    )
    semantic_risks: list[SemanticRisk] = field(
        default_factory=list
    )
    validation_requirements: list[str] = field(
        default_factory=list
    )

    # Lifecycle
    status: str = "DRAFT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "source_platform": self.source_platform,
            "target_platform": self.target_platform,
            "source_behavior": self.source_behavior,
            "required_behavior": self.required_behavior,
            "target_capabilities": self.target_capabilities,
            "target_design": self.target_design,
            "assumptions": list(self.assumptions),
            "unresolved_items": list(
                self.unresolved_items
            ),
            "semantic_risks": [
                asdict(risk)
                if isinstance(risk, SemanticRisk)
                else risk
                for risk in self.semantic_risks
            ],
            "validation_requirements": list(
                self.validation_requirements
            ),
            "status": self.status,
        }