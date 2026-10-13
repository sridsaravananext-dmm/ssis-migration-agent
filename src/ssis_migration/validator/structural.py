"""
Structural validator for migration plans.

Validates that the migration plan has the required structure:
  - All required plan-level fields are present.
  - Each step has required fields (step_id, name, step_type).
  - Step IDs are unique.
  - Dependencies reference existing steps.
  - No circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuralValidation:
    status: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "failures": list(self.failures),
        }


class StructuralValidator:
    """Validate structural integrity of a migration plan."""

    REQUIRED_PLAN_FIELDS = {"package_name", "steps", "metadata"}
    REQUIRED_STEP_FIELDS = {"step_id", "name", "step_type"}

    def validate(
        self, plan_dict: dict[str, Any]
    ) -> StructuralValidation:
        checks: dict[str, bool] = {}
        failures: list[str] = []

        # 1. Plan-level required fields
        missing_fields = self.REQUIRED_PLAN_FIELDS - set(
            plan_dict.keys()
        )
        checks["plan_required_fields"] = not missing_fields
        if missing_fields:
            failures.append(
                "Missing required plan fields: "
                f"{sorted(missing_fields)}"
            )

        # 2. Steps is a non-empty list
        steps = plan_dict.get("steps", [])
        checks["steps_non_empty"] = len(steps) > 0
        if not steps:
            failures.append("Plan has no migration steps.")

        # 3. Each step has required fields
        steps_with_missing: list[str] = []
        for step in steps:
            missing = self.REQUIRED_STEP_FIELDS - set(
                step.keys()
            )
            if missing:
                steps_with_missing.append(
                    f"{step.get('step_id', 'unknown')}: "
                    f"{sorted(missing)}"
                )
        checks["step_required_fields"] = (
            not steps_with_missing
        )
        if steps_with_missing:
            failures.append(
                "Steps with missing required fields: "
                + "; ".join(steps_with_missing)
            )

        # 4. Step IDs are unique
        step_ids = [
            s.get("step_id") for s in steps
        ]
        duplicates = [
            sid for sid in step_ids
            if step_ids.count(sid) > 1
        ]
        checks["step_ids_unique"] = not duplicates
        if duplicates:
            failures.append(
                "Duplicate step IDs: "
                f"{sorted(set(duplicates))}"
            )

        # 5. Dependencies reference existing steps
        valid_ids = set(step_ids)
        broken_deps: list[str] = []
        for step in steps:
            for dep in step.get("dependencies", []):
                if dep not in valid_ids:
                    broken_deps.append(
                        f"{step.get('step_id')} -> {dep}"
                    )
        checks["dependencies_resolvable"] = (
            not broken_deps
        )
        if broken_deps:
            failures.append(
                "Unresolvable dependencies: "
                + "; ".join(broken_deps)
            )

        # 6. No circular dependencies
        has_circular = self._has_circular_dependency(
            steps
        )
        checks["no_circular_dependencies"] = not has_circular
        if has_circular:
            failures.append(
                "Circular dependencies detected in "
                "migration plan."
            )

        status = "PASS" if not failures else "FAIL"

        return StructuralValidation(
            status=status,
            checks=checks,
            failures=failures,
        )

    @staticmethod
    def _has_circular_dependency(
        steps: list[dict[str, Any]],
    ) -> bool:
        step_map = {
            s.get("step_id"): s
            for s in steps
            if s.get("step_id")
        }
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(step_id: str) -> bool:
            if step_id in rec_stack:
                return True
            if step_id in visited:
                return False

            step = step_map.get(step_id)
            if step is None:
                return False

            visited.add(step_id)
            rec_stack.add(step_id)

            for dep in step.get("dependencies", []):
                if has_cycle(dep):
                    return True

            rec_stack.remove(step_id)
            return False

        for step_id in step_map:
            if has_cycle(step_id):
                return True

        return False