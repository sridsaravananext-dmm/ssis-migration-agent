"""
Generate Databricks Job definitions from a migration plan.

Converts the deterministic migration plan's step list and
dependencies into:
  1. A Databricks Job JSON definition (per-step notebook tasks
     with dependency ordering).
  2. A Declarative Automation Bundle (DABs) YAML file for
     deployment via `databricks bundle deploy`.

The job runs the generated migration notebook with a
`step_id` parameter so each task executes only its portion
of the migration. Dependencies come only from the supplied
plan; task names and types do not imply execution order.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobDefinition:
    """A Databricks Job definition ready for the Jobs API."""

    name: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    job_clusters: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    schedule: dict[str, Any] | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "tasks": self.tasks,
            "tags": self.tags,
        }
        if self.job_clusters:
            result["job_clusters"] = self.job_clusters
        if self.parameters:
            result["parameters"] = self.parameters
        if self.schedule:
            result["schedule"] = self.schedule
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class JobGenerator:
    """
    Generate Databricks Job definitions from a migration plan.
    """

    def generate(
        self,
        plan: dict[str, Any],
        notebook_path: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        cluster_spec: dict[str, Any] | None = None,
    ) -> JobDefinition:
        """
        Build a JobDefinition from the migration plan.
        """
        package_name = plan.get(
            "package_name", "SSIS_Migration"
        )
        job_name = f"{package_name} Migration"

        steps = plan.get("steps", [])
        step_keys = self._validate_steps(steps)

        if cluster_spec is None:
            cluster_spec = self._default_cluster(catalog)

        job_clusters = [
            {
                "job_cluster_key": "migration_cluster",
                "new_cluster": cluster_spec,
            }
        ]

        tasks: list[dict[str, Any]] = []
        for step in steps:
            step_id = step["step_id"]
            task_key = step_keys[step_id]

            deps = step.get("dependencies", [])
            dep_keys = [
                step_keys[d] for d in dict.fromkeys(deps)
            ]

            task: dict[str, Any] = {
                "task_key": task_key,
                "job_cluster_key": "migration_cluster",
                "notebook_task": {
                    "notebook_path": notebook_path,
                    "base_parameters": {
                        "step_id": step_id,
                        "catalog": catalog,
                        "schema": schema,
                    },
                    "source": "WORKSPACE",
                },
                "description": (
                    f"{step.get('name', step_id)} "
                    f"({step.get('step_type', 'UNKNOWN')})"
                ),
            }

            if dep_keys:
                task["depends_on"] = [
                    {"task_key": dk}
                    for dk in dep_keys
                ]

            tasks.append(task)

        parameters = [
            {"name": "catalog", "default": catalog},
            {"name": "schema", "default": schema},
        ]

        return JobDefinition(
            name=job_name,
            tasks=tasks,
            job_clusters=job_clusters,
            parameters=parameters,
            tags={
                "source": "ssis_migration",
                "package": package_name,
            },
        )

    def generate_dabs_yaml(
        self,
        plan: dict[str, Any],
        notebook_path: str,
        *,
        catalog: str = "main",
        schema: str = "default",
    ) -> str:
        """Generate bundle YAML using its JSON-compatible representation.

        JSON is valid YAML and preserves arbitrary quoted names and paths
        without requiring an optional YAML serializer dependency.
        """
        job = self.generate(
            plan,
            notebook_path,
            catalog=catalog,
            schema=schema,
        )
        package_name = plan.get(
            "package_name", "ssis_migration"
        )

        job_settings = job.to_dict()
        for parameter in job_settings["parameters"]:
            parameter["default"] = "${var." + parameter["name"] + "}"
        for task in job_settings["tasks"]:
            parameters = task["notebook_task"]["base_parameters"]
            parameters["catalog"] = "${var.catalog}"
            parameters["schema"] = "${var.schema}"
        resource_name = re.sub(r"[^A-Za-z0-9_]", "_", package_name)
        resource_name = resource_name.strip("_") or "ssis_migration"
        bundle = {
            "bundle": {"name": f"{resource_name}_migration"},
            "variables": {
                "catalog": {"default": catalog},
                "schema": {"default": schema},
            },
            "resources": {
                "jobs": {f"{resource_name}_migration_job": job_settings}
            },
        }
        return json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"

    def _validate_steps(
        self, steps: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Validate the complete graph before translating any dependencies."""
        if not isinstance(steps, list):
            raise ValueError("Migration steps must be a list.")
        keys: dict[str, str] = {}
        key_owners: dict[str, str] = {}
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Each migration step must be an object.")
            step_id = step.get("step_id")
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError("Each migration step needs a nonempty step_id.")
            if step_id in keys:
                raise ValueError(f"Duplicate migration step_id: {step_id}")
            key = self._safe_key(step_id)
            if not key or len(key) > 100:
                raise ValueError(f"Invalid Databricks task key for step: {step_id}")
            if key in key_owners:
                raise ValueError(
                    f"Databricks task-key collision: {key_owners[key]} and {step_id}"
                )
            keys[step_id] = key
            key_owners[key] = step_id

        dependencies: dict[str, list[str]] = {}
        for step in steps:
            step_id = step["step_id"]
            deps = step.get("dependencies", [])
            if not isinstance(deps, list) or any(
                not isinstance(dep, str) for dep in deps
            ):
                raise ValueError(f"Dependencies must be a list of step IDs: {step_id}")
            missing = [dep for dep in deps if dep not in keys]
            if missing:
                raise ValueError(f"Unknown dependencies for {step_id}: {missing}")
            dependencies[step_id] = deps

        active: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in active:
                raise ValueError(f"Circular migration dependency involving: {step_id}")
            if step_id in visited:
                return
            active.add(step_id)
            for dep in dependencies[step_id]:
                visit(dep)
            active.remove(step_id)
            visited.add(step_id)

        for step_id in keys:
            visit(step_id)
        return keys

    @staticmethod
    def _safe_key(key: str) -> str:
        """Convert a step_id to a valid task_key."""
        return re.sub(r"[^A-Za-z0-9_]", "_", key).strip("_").lower()

    @staticmethod
    def _default_cluster(catalog: str) -> dict[str, Any]:
        """Return a minimal cluster spec."""
        return {
            "spark_version": "15.4.x-scala2.12",
            "node_type_id": "i3.xlarge",
            "num_workers": 1,
            "spark_conf": {
                "spark.sql.catalog.unity":
                    "com.databricks.sql.catalog.UnityCatalog",
            },
        }
