"""
Assemble generated per-step artifacts into a Databricks notebook.

The NotebookAssembler takes a migration plan, the generated artifacts,
and per-step operations produced by the generator stage, and produces
a single Databricks notebook in Jupyter (.ipynb) format.

Each migration step becomes a section in the notebook with:
  - A markdown cell documenting the step metadata.
  - For AI_ASSISTED steps: a markdown cell with the AI contract.
  - A code cell containing the concatenated operations code.

Steps are ordered by their declared dependencies (topological sort).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ssis_migration.generator.models import GeneratedArtifact


class NotebookAssembler:
    """Assemble generated migration artifacts into a Databricks notebook."""

    def assemble(
        self,
        plan_dict: dict[str, Any],
        artifacts: list[GeneratedArtifact],
        operations_by_step: dict[str, list[dict[str, Any]]]
        | None = None,
    ) -> dict[str, Any]:
        """
        Build a Databricks notebook (Jupyter .ipynb format) from a
        migration plan and its generated artifacts.

        Args:
            plan_dict: The migration plan as a dictionary (from
                MigrationPlan.to_dict()).
            artifacts: List of GeneratedArtifact objects, one per step.
            operations_by_step: Optional dict mapping step_id to a
                list of operation dicts (each with operation_id,
                operation_type, order, and code or error).

        Returns:
            A dictionary representing the notebook in Jupyter .ipynb
            format.
        """
        if operations_by_step is None:
            operations_by_step = {}

        artifacts_by_step: dict[str, GeneratedArtifact] = {
            a.source_step_id: a for a in artifacts
        }

        ordered_steps = self._order_steps(plan_dict["steps"])

        cells: list[dict[str, Any]] = []

        # -------------------------------------------------
        # Title cell
        # -------------------------------------------------

        step_count = len(plan_dict["steps"])
        unresolved_count = len(plan_dict.get("unresolved", []))

        cells.append(self._markdown_cell(
            f"# {plan_dict['package_name']} \u2014 Databricks Migration\n"
            f"\n"
            f"Generated from SSIS package by the migration agent.\n"
            f"\n"
            f"| Field | Value |\n"
            f"| --- | --- |\n"
            f"| Steps | {step_count} |\n"
            f"| Unresolved | {unresolved_count} |\n"
            f"| Planner | {plan_dict.get('metadata', {}).get('planner', 'N/A')} |\n"
        ))

        # -------------------------------------------------
        # Parameter cell for Databricks Job execution
        # -------------------------------------------------

        cells.append(self._code_cell(
            "# Databricks Job parameters\n"
            "# When run via a Databricks Job, each task passes\n"
            "# step_id to execute only its portion.\n"
            "# When run interactively, step_id=\"all\" runs\n"
            "# every step in dependency order.\n"
            "dbutils.widgets.text('step_id', 'all', 'Step ID to execute')\n"
            "dbutils.widgets.text('catalog', 'main', 'Target catalog')\n"
            "dbutils.widgets.text('schema', 'default', 'Target schema')\n"
            "_step_id = dbutils.widgets.get('step_id')\n"
            "_catalog = dbutils.widgets.get('catalog')\n"
            "_schema = dbutils.widgets.get('schema')\n"
        ))

        # -------------------------------------------------
        # Unresolved items warning
        # -------------------------------------------------

        unresolved = plan_dict.get("unresolved", [])
        if unresolved:
            items = "\n".join(
                f"- {item}" for item in unresolved
            )
            cells.append(self._markdown_cell(
                f"## \u26a0 Unresolved Items\n\n"
                f"The following items could not be automatically\n"
                f"resolved and require manual review:\n\n"
                f"{items}\n"
            ))

        # -------------------------------------------------
        # One section per step
        # -------------------------------------------------

        for step in ordered_steps:
            step_id = step["step_id"]
            step_name = step["name"]
            step_type = step["step_type"]
            dependencies = step.get("dependencies", [])

            metadata = step.get("metadata", {})
            strategy = metadata.get(
                "migration_strategy", {}
            ).get("strategy", "UNKNOWN")

            target_objects = step.get("target_objects", [])
            source_objects = step.get("source_objects", [])

            artifact = artifacts_by_step.get(step_id)

            # --- Markdown cell: step metadata ---

            dep_str = (
                ", ".join(f"`{d}`" for d in dependencies)
                if dependencies
                else "None"
            )
            target_str = (
                ", ".join(f"`{t}`" for t in target_objects)
                if target_objects
                else "N/A"
            )
            source_str = (
                ", ".join(f"`{s}`" for s in source_objects)
                if source_objects
                else "N/A"
            )

            cells.append(self._markdown_cell(
                f"## {step_name}\n\n"
                f"| Field | Value |\n"
                f"| --- | --- |\n"
                f"| Step ID | `{step_id}` |\n"
                f"| Step Type | `{step_type}` |\n"
                f"| Strategy | `{strategy}` |\n"
                f"| Dependencies | {dep_str} |\n"
                f"| Source Objects | {source_str} |\n"
                f"| Target Objects | {target_str} |\n"
            ))

            # --- AI-assisted step handling ---

            is_ai_generated = (
                strategy == "AI_ASSISTED"
                and artifact is not None
                and artifact.metadata.get("ai_generated") is True
            )

            if (
                strategy == "AI_ASSISTED"
                and artifact
                and artifact.code
                and artifact.code.strip()
                and not is_ai_generated
            ):
                # Show the AI migration contract for steps
                # that were NOT yet AI-generated (still
                # unresolved or pending).
                contract = artifact.code
                cells.append(self._markdown_cell(
                    f"<details>\n"
                    f"<summary>AI Migration Contract (click to expand)</summary>\n\n"
                    f"```python\n"
                    f"{contract}\n"
                    f"```\n\n"
                    f"</details>\n"
                ))

            if is_ai_generated:
                # Show AI generation metadata for steps that
                # were successfully generated by the LLM.
                ai_status = artifact.metadata.get(
                    "ai_status", "GENERATED"
                )
                ai_assumptions = artifact.metadata.get(
                    "ai_assumptions", []
                )
                ai_unresolved = artifact.metadata.get(
                    "ai_unresolved", []
                )

                notes = (
                    f"**AI-Generated Code**"
                    f" (status: `{ai_status}`)\n"
                )
                if ai_assumptions:
                    notes += "\n**Assumptions:**\n"
                    for a in ai_assumptions:
                        notes += f"- {a}\n"
                if ai_unresolved:
                    notes += "\n**Unresolved items:**\n"
                    for u in ai_unresolved:
                        notes += f"- {u}\n"

                cells.append(self._markdown_cell(notes))

            # --- Code cell ---

            if is_ai_generated:
                # Use the LLM-generated code directly.
                cells.append(self._code_cell(
                    self._wrap_step_code(step_id, artifact.code)
                ))

            else:
                ops = operations_by_step.get(step_id, [])

                if ops:
                    code_blocks: list[str] = []

                    for op in sorted(ops, key=lambda o: o.get("order", 0)):
                        op_id = op.get("operation_id", "unknown")
                        op_type = op.get("operation_type", "UNKNOWN")

                        if "error" in op:
                            code_blocks.append(
                                f"# [{op_id}] ({op_type})\n"
                                f"# ERROR: {op['error']}\n"
                            )
                        else:
                            op_code = op.get("code", "")
                            code_blocks.append(
                                f"# [{op_id}] ({op_type})\n"
                            f"{op_code}\n"
                        )

                    cells.append(self._code_cell(
                        self._wrap_step_code(
                            step_id,
                            "\n\n".join(code_blocks),
                        )
                    ))

                elif (
                    artifact
                    and artifact.code
                    and artifact.code.strip()
                ):
                    # No operations available; use artifact
                    # code directly
                    if artifact.language == "spark_sql":
                        cells.append(self._code_cell(
                            self._wrap_step_code(
                                step_id,
                                f"%sql\n{artifact.code}",
                            )
                        ))
                    else:
                        cells.append(self._code_cell(
                            self._wrap_step_code(
                                step_id,
                                artifact.code,
                            )
                        ))

                else:
                    cells.append(self._markdown_cell(
                        f"*No code generated for this step.*\n"
                    ))

        # -------------------------------------------------
        # Assemble notebook
        # -------------------------------------------------

        notebook: dict[str, Any] = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "application/databricks": {
                    "name": (
                        f"{plan_dict['package_name']}_Migration"
                    ),
                    "language": "python",
                },
            },
            "cells": cells,
        }

        return notebook

    def assemble_and_write(
        self,
        plan_dict: dict[str, Any],
        artifacts: list[GeneratedArtifact],
        output_path: Path,
        operations_by_step: dict[str, list[dict[str, Any]]]
        | None = None,
    ) -> Path:
        """
        Assemble the notebook and write it to a .ipynb file.

        Args:
            plan_dict: The migration plan as a dictionary.
            artifacts: List of GeneratedArtifact objects.
            output_path: Directory where the notebook will be
                written.
            operations_by_step: Optional dict mapping step_id to a
                list of operation dicts.

        Returns:
            Path to the written notebook file.
        """
        notebook = self.assemble(
            plan_dict, artifacts, operations_by_step
        )

        notebook_file = (
            output_path
            / f"{plan_dict['package_name']}_Migration.ipynb"
        )
        notebook_file.parent.mkdir(
            parents=True, exist_ok=True
        )
        notebook_file.write_text(
            json.dumps(
                notebook, indent=2, ensure_ascii=False
            ),
            encoding="utf-8",
        )

        return notebook_file

    def _order_steps(
        self,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Topologically sort steps by their dependencies.

        Steps with no dependencies come first. Steps with dependencies
        come after all their dependencies. If a circular dependency is
        detected, the step is added anyway to avoid blocking the
        notebook generation.
        """
        step_map = {s["step_id"]: s for s in steps}
        visited: set[str] = set()
        ordered: list[dict[str, Any]] = []

        def visit(
            step_id: str,
            path: set[str],
        ) -> None:
            if step_id in visited:
                return

            step = step_map.get(step_id)
            if step is None:
                return

            if step_id in path:
                # Circular dependency: add to avoid blocking
                if step_id not in visited:
                    ordered.append(step)
                    visited.add(step_id)
                return

            path.add(step_id)

            for dep in step.get("dependencies", []):
                visit(dep, path)

            path.remove(step_id)

            if step_id not in visited:
                ordered.append(step)
                visited.add(step_id)

        for step in steps:
            visit(step["step_id"], set())

        return ordered

    def _markdown_cell(
        self, source: str
    ) -> dict[str, Any]:
        """Create a markdown cell in Jupyter .ipynb format."""
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": self._split_source(source),
        }

    def _code_cell(
        self, source: str
    ) -> dict[str, Any]:
        """Create a code cell in Jupyter .ipynb format."""
        return {
            "cell_type": "code",
            "metadata": {},
            "source": self._split_source(source),
            "outputs": [],
            "execution_count": None,
        }

    @staticmethod
    def _split_source(source: str) -> list[str]:
        """
        Split source text into a list of lines with trailing
        newlines, as required by the Jupyter .ipynb format.
        """
        if not source:
            return [""]
        lines = source.split("\n")
        return [
            line + "\n" for line in lines[:-1]
        ] + [lines[-1]]

    @staticmethod
    def _wrap_step_code(step_id: str, code: str) -> str:
        """
        Wrap step code with a conditional guard so the
        notebook can run a single step when invoked by a
        Databricks Job task with step_id parameter.

        ``%sql`` magic cells are converted to
        ``spark.sql()`` calls so they can be nested
        inside the Python ``if`` block.
        """
        guard = f'if _step_id in ("all", "{step_id}"):'

        if code.startswith("%sql\n"):
            sql_body = code[len("%sql\n"):]
            inner = (
                f'    spark.sql("""\n'
                f'{sql_body}\n'
                f'    """)'
            )
        else:
            lines = code.split("\n")
            inner = "\n".join(
                ("    " + line if line.strip() else line)
                for line in lines
            )

        return f"{guard}\n{inner}"