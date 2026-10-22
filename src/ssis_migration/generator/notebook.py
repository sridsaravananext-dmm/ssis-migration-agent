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

import ast
import json
import re
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
            "_step_id = dbutils.widgets.get('step_id')\n"
            "_catalog = dbutils.widgets.get('catalog')\n"
            "dbutils.widgets.text('schema', 'default', 'Target schema')\n"
            "_schema = dbutils.widgets.get('schema')\n"
        ))
        cells[-1]["metadata"]["migration_role"] = "parameters"
        workspace_config = plan_dict.get('metadata',{}).get('workspace_config')
        if workspace_config is not None:
            from ssis_migration.workspace import widget_code
            # Replace the default parameter cell before assembly, never patch
            # already validated/generated notebook bytes after publication.
            parameters = "import json\n" + "dbutils.widgets.text('step_id', 'all', 'Step ID to execute')\n"
            parameters += widget_code(workspace_config)
            parameters += "_step_id = dbutils.widgets.get('step_id')\n"
            cells[-1]['source'] = self._split_source(parameters)

        # -------------------------------------------------
        # Setup cell: ETL control-table helpers
        # -------------------------------------------------

        helper_code, helper_names = self._provided_helpers(plan_dict)
        # Utilities are supplied with explicit evidence; the assembler never
        # invents source tables, period columns or control-table behavior.
        requested_helpers = any(
            self._referenced_helpers(str(op.get("code", "")))
            for ops in operations_by_step.values() for op in ops
        ) or any(self._referenced_helpers(a.code) for a in artifacts)
        if helper_code and requested_helpers:
            cells.append(self._code_cell(helper_code))
            cells[-1]["metadata"].update({
                "migration_role": "utilities",
                "source_evidence": plan_dict["metadata"]["control_helpers"]["evidence"],
            })

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
                # Code remains subject to independent language/static checks.
                cells.append(self._code_cell(
                    self._wrap_step_code(
                        step_id,
                        self._artifact_code(artifact),
                    )
                ))

            else:
                ops = operations_by_step.get(step_id, [])

                if ops:
                    code_blocks: list[str] = []

                    for op in sorted(ops, key=lambda o: o.get("order", 0)):
                        op_id = op.get("operation_id", "unknown")
                        op_type = op.get("operation_type", "UNKNOWN")

                        if "error" in op:
                            error_lines = "\n".join(
                                "# " + line for line in str(op["error"]).splitlines()
                            )
                            code_blocks.append(
                                f"# [{op_id}] ({op_type})\n"
                                f"# ERROR:\n{error_lines}\n"
                            )
                        else:
                            op_code = op.get("code") or "# AI_REQUIRED: Operation has no implementation"
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
                    # No operations available; route the artifact by its
                    # declared language rather than interpreting SQL as Python.
                    cells.append(self._code_cell(self._wrap_step_code(
                        step_id, self._artifact_code(artifact)
                    )))

                else:
                    cells.append(self._markdown_cell(
                        f"*No code generated for this step.*\n"
                    ))
                    cells.append(self._code_cell(self._wrap_step_code(
                        step_id, "# AI_REQUIRED: No code generated for this step."
                    )))

            # Every step code cell has stable identity and an explicit
            # execution blocker before DML when any operation is incomplete.
            cell = cells[-1]
            if cell["cell_type"] == "code":
                reasons: list[str] = []
                source = "".join(cell["source"])
                if self._has_incomplete_code(source) or (
                    artifact and (is_ai_generated or not operations_by_step.get(step_id))
                    and self._has_incomplete_code(artifact.code)
                ):
                    reasons.append("Incomplete or failed migration operation")
                missing_helpers = sorted(self._referenced_helpers(source) - helper_names)
                if missing_helpers:
                    reasons.append(
                        "MISSING_CONTROL_HELPER_EVIDENCE: " + ", ".join(missing_helpers)
                    )
                if artifact and artifact.metadata.get("ai_unresolved"):
                    reasons.append("AI response contains unresolved source behavior")
                if artifact and artifact.metadata.get("ai_status") not in {None, "GENERATED"}:
                    reasons.append("AI response has not passed generation review")
                if metadata.get("static_validation", {}).get("status") not in {None, "PASS"}:
                    reasons.append("Independent static validation found unresolved or invalid code")
                if plan_dict.get("unresolved"):
                    reasons.append("Migration plan contains unresolved behavior")
                if reasons:
                    # Put the guard ahead of every operation, including any
                    # staging DELETE that otherwise precedes a placeholder.
                    source = (
                        f"if _step_id in ('all', {step_id!r}):\n"
                        f"    raise RuntimeError({'; '.join(reasons)!r})\n"
                        + source
                    )
                    cell["source"] = self._split_source(source)
                cell["metadata"].update({
                    "migration_role": "step",
                    "step_id": step_id,
                    "blocked": bool(reasons),
                    "blocking_reasons": reasons,
                })

        for index, cell in enumerate(cells):
            cell["id"] = f"migration-cell-{index}"

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
        notebook["metadata"]["migration_validation"] = self.validate(notebook)

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

    _CONTROL_HELPERS = frozenset({
        "get_last_etl_cutoff", "get_lineage_key", "complete_lineage",
        "update_etl_cutoff", "new_cutoff",
    })

    @classmethod
    def _referenced_helpers(cls, code: str) -> set[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()
        defined = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        } | {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        return {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            and node.id in cls._CONTROL_HELPERS and node.id not in defined
        }

    @staticmethod
    def _provided_helpers(plan_dict: dict[str, Any]) -> tuple[str, set[str]]:
        """Accept explicit utility code and evidence, never synthesize it.

        ``metadata.control_helpers`` requires ``code``, nonempty ``evidence``
        and ``verified=True`` from the calling evidence workflow. Static
        validation does not independently establish semantic verification.
        """
        contract = plan_dict.get("metadata", {}).get("control_helpers", {})
        if not isinstance(contract, dict):
            return "", set()
        code = contract.get("code")
        evidence = contract.get("evidence")
        if not (
            contract.get("verified") is True
            and isinstance(code, str) and code.strip()
            and isinstance(evidence, list) and evidence and all(evidence)
        ):
            return "", set()
        try:
            tree = ast.parse(code)
            compile(tree, "<supplied_migration_utilities>", "exec")
        except (SyntaxError, ValueError, TypeError):
            return "", set()
        defined: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    defined.update(
                        name.id for name in ast.walk(target)
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                    )
        return code, defined

    @staticmethod
    def _artifact_code(artifact: GeneratedArtifact) -> str:
        if artifact.language.lower() in {"sql", "spark_sql", "databricks_sql"}:
            return f"%sql\n{artifact.code}"
        if artifact.language.lower() in {"python", "pyspark"}:
            return artifact.code
        return f"# REVIEW_REQUIRED: Unsupported artifact language {artifact.language!r}"

    @staticmethod
    def _has_incomplete_code(code: str) -> bool:
        return bool(re.search(
            r"^\s*(?:#|--)\s*.*(?:AI_REQUIRED|REVIEW_REQUIRED|TODO\b|ERROR:|placeholder)",
            code, flags=re.MULTILINE | re.IGNORECASE,
        ))

    @staticmethod
    def validate(notebook: Any) -> dict[str, Any]:
        """Check notebook shape and compile code cells without execution.

        PASS describes static checks only, never migration acceptance or
        data equivalence. REVIEW_REQUIRED retains explicit blocked steps.
        """
        issues: list[dict[str, Any]] = []

        def issue(code: str, message: str, cell: int | None = None,
                  severity: str = "ERROR") -> None:
            item: dict[str, Any] = {
                "code": code, "severity": severity, "message": message,
            }
            if cell is not None:
                item["cell"] = cell
            issues.append(item)

        if not isinstance(notebook, dict):
            issue("NOTEBOOK_SHAPE", "Notebook must be a JSON object")
        else:
            if notebook.get("nbformat") != 4 or notebook.get("nbformat_minor") != 5:
                issue("NOTEBOOK_VERSION", "Expected Jupyter notebook format 4.5")
            metadata = notebook.get("metadata")
            databricks = metadata.get("application/databricks") if isinstance(metadata, dict) else None
            if (
                not isinstance(databricks, dict)
                or databricks.get("language") != "python"
                or not isinstance(databricks.get("name"), str)
                or not databricks["name"].strip()
            ):
                issue("NOTEBOOK_METADATA", "Missing Python Databricks notebook metadata")
            cells = notebook.get("cells")
            if not isinstance(cells, list):
                issue("NOTEBOOK_CELLS", "Notebook cells must be a list")
                cells = []
            cell_ids: set[str] = set()
            for index, cell in enumerate(cells):
                if not isinstance(cell, dict):
                    issue("CELL_SHAPE", "Cell must be an object", index)
                    continue
                cell_id = cell.get("id")
                if not isinstance(cell_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cell_id) or cell_id in cell_ids:
                    issue("CELL_ID", "Missing, invalid or duplicate cell ID", index)
                else:
                    cell_ids.add(cell_id)
                cell_metadata = cell.get("metadata")
                if not isinstance(cell_metadata, dict):
                    issue("CELL_METADATA", "Cell metadata must be an object", index)
                    cell_metadata = {}
                if cell.get("cell_type") not in {"code", "markdown", "raw"}:
                    issue("CELL_TYPE", "Unsupported notebook cell type", index)
                source = cell.get("source")
                if isinstance(source, list) and all(isinstance(line, str) for line in source):
                    source = "".join(source)
                if not isinstance(source, str):
                    issue("CELL_SOURCE", "Cell source must be a string or list of strings", index)
                    continue
                if cell.get("cell_type") == "code":
                    count = cell.get("execution_count")
                    if "execution_count" not in cell or (count is not None and type(count) is not int) or not isinstance(cell.get("outputs"), list):
                        issue("CODE_CELL_SHAPE", "Invalid code-cell execution fields", index)
                    try:
                        compile(source, f"<migration_cell_{index}>", "exec")
                    except (SyntaxError, ValueError, TypeError) as exc:
                        issue("PYTHON_SYNTAX", str(exc), index)
                    if cell_metadata.get("blocked"):
                        reasons = cell_metadata.get("blocking_reasons", [])
                        if not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons):
                            issue("CELL_METADATA", "Blocking reasons must be a list of strings", index)
                        else:
                            issue("BLOCKED_STEP", "; ".join(reasons) or "Step is blocked", index, "WARNING")
        return {
            "status": "REJECTED" if any(i["severity"] == "ERROR" for i in issues) else "REVIEW_REQUIRED" if issues else "PASS",
            "scope": "STATIC_NOTEBOOK",
            "issues": issues,
        }

    def _order_steps(
        self,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Topologically sort validated IDs; invalid graphs cannot run."""
        step_map: dict[str, dict[str, Any]] = {}
        for step in steps:
            step_id = step.get("step_id")
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError("Migration graph has a missing or invalid step ID")
            if step_id in step_map:
                raise ValueError(f"Migration graph has duplicate step ID: {step_id}")
            step_map[step_id] = step
        for step_id, step in step_map.items():
            deps = step.get("dependencies", [])
            if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps):
                raise ValueError(f"Invalid dependencies for step {step_id}")
            for dep in deps:
                if dep not in step_map:
                    raise ValueError(f"Unknown dependency {dep!r} for step {step_id!r}")
        visited: set[str] = set()
        visiting: set[str] = set()
        ordered: list[dict[str, Any]] = []

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError(f"Circular dependency involving step {step_id!r}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dep in step_map[step_id].get("dependencies", []):
                visit(dep)
            visiting.remove(step_id)
            visited.add(step_id)
            ordered.append(step_map[step_id])

        for step_id in step_map:
            visit(step_id)
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
        guard = f"if _step_id in ('all', {step_id!r}):"
        if code.startswith("%sql\n"):
            sql_body = code[len("%sql\n"):]
            inner_code = f"spark.sql({sql_body!r})"
        else:
            inner_code = code
        # A comment is not a Python suite. Preserve the original evidence,
        # but make a missing implementation fail before any target writes.
        try:
            has_statements = bool(ast.parse(inner_code).body)
        except SyntaxError:
            has_statements = True  # validate() reports the actual syntax error.
        if not has_statements:
            inner_code += (
                "\n# REVIEW_REQUIRED: Migration step has no executable implementation"
                "\nraise RuntimeError('Migration step has no executable implementation')"
            )
        inner = "\n".join(
            "    " + line if line.strip() else line
            for line in inner_code.split("\n")
        )
        return f"{guard}\n{inner}"
