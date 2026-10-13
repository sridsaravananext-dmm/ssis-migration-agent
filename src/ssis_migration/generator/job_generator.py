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
of the migration. Dimensions run before facts when no
explicit dependencies exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


_STEP_TYPE_ORDER = {
    "DIMENSION_LOAD": 0,
    "FACT_LOAD": 1,
}


def _step_sort_key(step: dict[str, Any]) -> tuple[int, str]:
    stype = step.get("step_type", "")
    return (
        _STEP_TYPE_ORDER.get(stype, 99),
        step.get("step_id", step.get("name", "")),
    )


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

        steps = sorted(
            plan.get("steps", []),
            key=_step_sort_key,
        )

        if cluster_spec is None:
            cluster_spec = self._default_cluster(catalog)

        job_clusters = [
            {
                "job_cluster_key": "migration_cluster",
                "new_cluster": cluster_spec,
            }
        ]

        tasks: list[dict[str, Any]] = []
        step_keys: dict[str, str] = {}

        for step in steps:
            step_id = step.get(
                "step_id", step.get("name", "")
            )
            task_key = self._safe_key(step_id)
            step_keys[step_id] = task_key

            deps = step.get("dependencies", [])
            dep_keys = [
                step_keys.get(d, d)
                for d in deps
                if d in step_keys
            ]

            if not dep_keys and step.get("step_type") == "FACT_LOAD":
                dep_keys = [
                    step_keys[s["step_id"]]
                    for s in steps
                    if s.get("step_type") == "DIMENSION_LOAD"
                    and s.get("step_id") in step_keys
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
        """Generate a Declarative Automation Bundle YAML."""
        job = self.generate(
            plan,
            notebook_path,
            catalog=catalog,
            schema=schema,
        )
        package_name = plan.get(
            "package_name", "ssis_migration"
        )

        lines: list[str] = []
        lines.append("bundle:")
        lines.append(f"  name: {package_name}_migration")
        lines.append("")
        lines.append("variables:")
        lines.append("  catalog:")
        lines.append(f'    default: "{catalog}"')
        lines.append("  schema:")
        lines.append(f'    default: "{schema}"')
        lines.append("")
        lines.append("resources:")
        lines.append("  jobs:")
        lines.append(f"    {package_name}_migration_job:")
        lines.append(f"      name: {job.name}")
        lines.append("      tags:")
        for k, v in job.tags.items():
            lines.append(f'        {k}: "{v}"')
        lines.append("      tasks:")
        for task in job.tasks:
            lines.append(f"        - task_key: {task['task_key']}")
            lines.append(f"          job_cluster_key: {task.get('job_cluster_key', '')}")
            desc = task.get('description', '')
            lines.append(f'          description: "{desc}"')
            if "depends_on" in task:
                lines.append("          depends_on:")
                for dep in task["depends_on"]:
                    lines.append(f"            - task_key: {dep['task_key']}")
            nb = task.get("notebook_task", {})
            lines.append("          notebook_task:")
            lines.append(f"            notebook_path: \"{nb.get('notebook_path', '')}\"")
            lines.append("            base_parameters:")
            for pk, pv in nb.get("base_parameters", {}).items():
                lines.append(f"              {pk}: \"{pv}\"")
            lines.append(f"            source: {nb.get('source', 'WORKSPACE')}")

        return "\n".join(lines)

    @staticmethod
    def _safe_key(key: str) -> str:
        """Convert a step_id to a valid task_key."""
        return (
            key.replace("-", "_")
            .replace(" ", "_")
            .replace(".", "_")
            .lower()
        )

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
