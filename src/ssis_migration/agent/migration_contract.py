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

    # Additive bridge fields. Legacy positional/keyword construction remains
    # available; executable consumers validate contracts built by the bridge.
    contract_version: str = "1.0"
    contract_id: str = ""
    binding_digest: str = ""
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    implementation_strategy: dict[str, Any] = field(default_factory=dict)
    evidence_files: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
