"""
Semantic validator for migration plans.

Validates that the migration plan preserves key semantics
from the source SSIS package:
  - All steps have assigned migration strategies.
  - All steps have target objects.
  - Unresolved items are reported.
  - No steps have UNRESOLVED strategy.
  - Source procedures are covered by the plan (when analysis is provided).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SemanticValidation:
    status: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }


class SemanticValidator:
    """Validate migration plan semantics."""

    def validate(
        self,
        plan_dict: dict[str, Any],
        *,
        analysis: dict[str, Any] | None = None,
    ) -> SemanticValidation:
        """
        Validate that the migration plan preserves
        key semantics from the source package.

        Args:
            plan_dict: The migration plan as a dict.
            analysis: Optional analysis dict for
                cross-referencing source semantics.
        """
        checks: dict[str, bool] = {}
        failures: list[str] = []
        warnings: list[str] = []

        steps = plan_dict.get("steps", [])

        # 1. All steps have migration strategies
        steps_without_strategy: list[str] = []
        for step in steps:
            strategy = (
                step.get("metadata", {})
                .get("migration_strategy", {})
                .get("strategy")
            )
            if not strategy:
                steps_without_strategy.append(
                    step.get("step_id", "unknown")
                )
        checks["all_steps_have_strategy"] = (
            not steps_without_strategy
        )
        if steps_without_strategy:
            failures.append(
                "Steps without migration strategy: "
                + ", ".join(steps_without_strategy)
            )

        # 2. All steps have target objects
        steps_without_targets: list[str] = []
        for step in steps:
            targets = step.get("target_objects", [])
            if not targets:
                steps_without_targets.append(
                    step.get("step_id", "unknown")
                )
        checks["all_steps_have_targets"] = (
            not steps_without_targets
        )
        if steps_without_targets:
            warnings.append(
                "Steps without target objects: "
                + ", ".join(steps_without_targets)
            )

        # 3. Unresolved items are reported
        unresolved = plan_dict.get("unresolved", [])
        checks["unresolved_reported"] = True
        if unresolved:
            warnings.append(
                f"Plan has {len(unresolved)} "
                f"unresolved item(s)."
            )

        # 4. No UNRESOLVED artifact types in steps
        unresolved_steps: list[str] = []
        for step in steps:
            strategy = (
                step.get("metadata", {})
                .get("migration_strategy", {})
                .get("strategy", "")
            )
            if strategy == "UNRESOLVED":
                unresolved_steps.append(
                    step.get("step_id", "unknown")
                )
        checks["no_unresolved_strategies"] = (
            not unresolved_steps
        )
        if unresolved_steps:
            warnings.append(
                "Steps with UNRESOLVED strategy: "
                + ", ".join(unresolved_steps)
            )

        # 5. Cross-reference with analysis (if provided)
        if analysis:
            source_procedures: set[str] = set()
            for task in analysis.get(
                "execute_sql_tasks", []
            ):
                for proc in task.get(
                    "source_procedures", []
                ):
                    name = proc.get("procedure")
                    if name:
                        source_procedures.add(name)

            plan_procedures: set[str] = set()
            for step in steps:
                intent = step.get(
                    "metadata", {}
                ).get("migration_intent", {})
                for proc in intent.get(
                    "source_procedures", []
                ):
                    name = proc.get("procedure")
                    if name:
                        plan_procedures.add(name)

            missing = (
                source_procedures - plan_procedures
            )
            checks["all_source_procedures_covered"] = (
                not missing
            )
            if missing:
                warnings.append(
                    "Source procedures not in plan: "
                    + ", ".join(sorted(missing))
                )

        # Determine status
        if failures:
            status = "FAIL"
        elif warnings:
            status = "REVIEW_REQUIRED"
        else:
            status = "PASS"

        return SemanticValidation(
            status=status,
            checks=checks,
            failures=failures,
            warnings=warnings,
        )