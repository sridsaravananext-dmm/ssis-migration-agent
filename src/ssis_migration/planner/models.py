from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MigrationStep:

    step_id: str

    name: str

    step_type: str

    source_components: list[str] = field(
        default_factory=list
    )

    source_objects: list[str] = field(
        default_factory=list
    )

    target_components: list[str] = field(
        default_factory=list
    )

    target_objects: list[str] = field(
        default_factory=list
    )

    transformations: list[dict[str, Any]] = field(
        default_factory=list
    )

    dependencies: list[str] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class MigrationPlan:

    package_name: str

    steps: list[MigrationStep] = field(
        default_factory=list
    )

    unresolved: list[dict[str, Any]] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        return {
            "package_name": self.package_name,

            "steps": [
                {
                    "step_id": step.step_id,
                    "name": step.name,
                    "step_type": step.step_type,
                    "source_components": step.source_components,
                    "source_objects": step.source_objects,
                    "target_components": step.target_components,
                    "target_objects": step.target_objects,
                    "transformations": step.transformations,
                    "dependencies": step.dependencies,
                    "metadata": step.metadata,
                }
                for step in self.steps
            ],

            "unresolved": self.unresolved,

            "metadata": self.metadata,
        }