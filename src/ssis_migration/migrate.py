from __future__ import annotations

import argparse
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def safe_name(value: str) -> str:
    """Convert a package/object name into a portable artifact name."""
    result = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return result.strip("_") or "migration"


def json_default(value: Any) -> str:
    return str(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
            default=json_default,
        ),
        encoding="utf-8",
    )


def discover_packages(source: Path) -> list[Path]:
    """Discover one package or every DTSX file beneath a directory."""
    if not source.exists():
        raise FileNotFoundError(f"Source does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() != ".dtsx":
            raise ValueError(f"Expected a .dtsx file: {source}")
        return [source]

    packages = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() == ".dtsx"
    )

    if not packages:
        raise FileNotFoundError(
            f"No .dtsx packages found under {source}"
        )

    return packages


def object_to_dict(value: Any) -> Any:
    """Convert common IR objects to JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(k): object_to_dict(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [object_to_dict(v) for v in value]

    if hasattr(value, "to_dict"):
        return object_to_dict(value.to_dict())

    if hasattr(value, "__dict__"):
        return {
            str(k): object_to_dict(v)
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }

    return str(value)


def analyze_one_package(
    package_path: Path,
    procedures_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Run the repository's real parser -> IR builder -> analyzer pipeline.

    This function does not fabricate target SQL or claim that transformations
    have been translated.
    """
    from ssis_migration.parser.package_parser import SSISPackageParser
    from ssis_migration.ir.builder import IRBuilder
    from ssis_migration.analyzer import analyze_package

    parsed = SSISPackageParser(str(package_path)).parse()
    package_ir = IRBuilder().build(parsed)

    # Load procedure bodies if a procedures file is supplied.
    procedure_bodies: dict[str, str] | None = None
    if procedures_path:
        from ssis_migration.procedure_file_loader import load_procedures
        procedure_bodies = load_procedures(procedures_path)

    # Keep the established repository API: analyze the built IR, not
    # the original parser result.
    analysis = analyze_package(
        package_ir.to_dict(),
        procedure_bodies=procedure_bodies,
    )

    return {
        "parsed": parsed,
        "package_ir": package_ir,
        "analysis": analysis,
    }


def _run_ai_assisted_generation(
    plan_dict: dict[str, Any],
    all_artifacts: list[Any],
    artifacts_dir: Path,
    validation_results: list[dict[str, Any]],
    *,
    ai_provider: str = "databricks",
) -> list[dict[str, Any]]:
    """
    Call the LLM orchestrator for AI_ASSISTED steps.

    This is a per-item fallback: only steps whose deterministic
    code is a placeholder are sent to the LLM. Steps that already
    have real generated code are left untouched.

    Returns additional validation results for the
    LLM-generated code.
    """
    from ssis_migration.agent.provider_factory import (
        create_provider,
    )
    from ssis_migration.agent.orchestrator import (
        AIMigrationOrchestrator,
    )

    provider = create_provider(ai_provider)
    orchestrator = AIMigrationOrchestrator(provider)

    artifacts_by_step = {
        a.source_step_id: a for a in all_artifacts
    }

    ai_validation: list[dict[str, Any]] = []

    for step in plan_dict["steps"]:
        strategy = (
            step.get("metadata", {})
            .get("migration_strategy", {})
            .get("strategy", "")
        )

        if strategy != "AI_ASSISTED":
            continue

        step_id = step["step_id"]
        step_name = step["name"]
        artifact = artifacts_by_step.get(step_id)

        if artifact is None:
            continue

        # Per-item fallback: only invoke the LLM when the
        # deterministic path produced a placeholder. If the
        # artifact already has real generated code, skip it.
        if (
            artifact.code
            and "AI_ASSISTED" not in artifact.code
            and "placeholder" not in artifact.code.lower()
        ):
            continue

        # Extract procedure definitions from the
        # step metadata — both source and destination.
        intent = step.get("metadata", {}).get(
            "migration_intent", {}
        )
        proc_defs: dict[str, str] = {}
        for proc in intent.get("source_procedures", []):
            name = proc.get("procedure", "")
            definition = proc.get("definition", "")
            if name and definition:
                proc_defs[name] = definition
        for proc in intent.get("destination_procedures", []):
            name = proc.get("procedure", "")
            definition = proc.get("definition", "")
            if name and definition:
                proc_defs[name] = definition

        try:
            result = orchestrator.run(
                migration_step=step,
                procedure_definitions=proc_defs
                or None,
            )
        except Exception as exc:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": "ERROR",
                "error": str(exc),
            })
            continue

        if result.status == "GENERATED" and result.code:
            # Replace the AI contract with
            # LLM-generated code.
            artifact.code = result.code
            artifact.metadata["ai_generated"] = True
            artifact.metadata["ai_status"] = (
                result.status
            )
            artifact.metadata["ai_assumptions"] = (
                result.assumptions
            )
            artifact.metadata["ai_unresolved"] = (
                result.unresolved_items
            )

            # Write the LLM-generated code.
            ext = "py" if artifact.language == "pyspark" else "sql"
            code_file = artifacts_dir / f"{step_id}.{ext}"
            code_file.write_text(
                result.code, encoding="utf-8"
            )

            # Validate the LLM-generated code.
            from ssis_migration.agent.code_validator import (
                validate_generated_code,
            )

            code_result = validate_generated_code(
                result.code, step_name
            )
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": result.status,
                "code_validation": code_result,
                "assumptions": result.assumptions,
                "unresolved_items": (
                    result.unresolved_items
                ),
            })
        else:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "status": result.status,
                "unresolved_items": (
                    result.unresolved_items
                ),
            })

    return ai_validation


def _generate_review_report(
    package_name: str,
    plan_dict: dict[str, Any],
    analysis: dict[str, Any],
    catalog: str,
    notebook_file: str,
    operations_by_step: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """
    Generate a human-readable markdown review report
    summarizing the migration analysis and output.
    """
    lines: list[str] = []

    summary = analysis.get("summary", {})
    sql_tasks = analysis.get("sql_tasks", {})
    control_flow = analysis.get("control_flow", {})

    lines.append(f"# Migration Review: {package_name}")
    lines.append("")
    lines.append(f"- **Catalog:** {catalog}")
    lines.append(f"- **Notebook:** `{notebook_file}`")
    lines.append("")

    # --- Package summary ---
    lines.append("## Package Summary")
    lines.append("")
    lines.append(f"- Executables: {summary.get('executables', 0)}")
    lines.append(f"- Data flows: {summary.get('data_flows', 0)}")
    lines.append(f"- Execute SQL tasks: {summary.get('execute_sql_tasks', 0)}")
    lines.append(f"- Precedence constraints: {summary.get('precedence_constraints', 0)}")
    lines.append(f"- Variables: {summary.get('variables', 0)}")
    lines.append("")

    # --- SQL task classification ---
    if sql_tasks:
        lines.append("## Execute SQL Task Classification")
        lines.append("")
        lines.append("| Classification | Count |")
        lines.append("| --- | ---: |")
        for cls, count in sorted(
            sql_tasks.get("classification_counts", {}).items()
        ):
            lines.append(f"| {cls} | {count} |")
        lines.append("")
        unresolved = sql_tasks.get("unresolved_procedures", [])
        if unresolved:
            lines.append(f"### Unresolved Procedures ({len(unresolved)})")
            lines.append("")
            for proc in unresolved:
                lines.append(f"- `{proc}`")
            lines.append("")
        else:
            lines.append("*All procedures resolved.*")
            lines.append("")

    # --- Control flow ---
    if control_flow:
        edges = control_flow.get("execution_edges", [])
        lines.append("## Control Flow")
        lines.append("")
        lines.append(f"- Execution edges: {len(edges)}")
        lines.append("")

    # --- Data flows ---
    data_flows = analysis.get("data_flows", [])
    if data_flows:
        lines.append("## Data Flows")
        lines.append("")
        lines.append("| Flow | Components | Mappings |")
        lines.append("| --- | ---: | ---: |")
        for flow in data_flows:
            lines.append(
                f"| {flow.get('name', '?')} "
                f"| {flow.get('component_count', 0)} "
                f"| {flow.get('mapping_count', 0)} |"
            )
        lines.append("")

        # --- Column lineage validation per data flow ---
        lines.append("### Column Lineage Validation")
        lines.append("")
        for flow in data_flows:
            flow_name = flow.get("name") or "?"
            mappings = flow.get("column_mappings", [])
            sources = flow.get("sources", [])
            destinations = flow.get("destinations", [])
            lineage = flow.get("lineage", {})

            lines.append(f"#### {flow_name}")
            lines.append("")

            src_names = ", ".join(
                s.get("name") or "?" for s in sources
            ) if sources else "N/A"
            dst_names = ", ".join(
                d.get("name") or "?" for d in destinations
            ) if destinations else "N/A"
            lines.append(f"- **Source:** {src_names}")
            lines.append(f"- **Destination:** {dst_names}")
            lines.append(f"- **Column mappings:** {len(mappings)}")

            # Flag flows with no mappings
            if not mappings:
                lines.append("- **WARNING:** No column mappings found. Lineage may be lost.")
                lines.append("")
                continue

            # Show source -> destination column lineage
            lines.append("")
            lines.append("| Source Column | Destination Column | Type |")
            lines.append("| --- | --- | --- |")
            for m in mappings:
                src_col = m.get("source_column") or "?"
                dst_col = m.get("destination_column") or "?"
                map_type = m.get("mapping_type") or "DIRECT"
                lines.append(
                    f"| {src_col} | {dst_col} | {map_type} |"
                )
            lines.append("")

            # Check for unmapped destination columns
            mapped_dst = {
                (m.get("destination_column") or "").lower()
                for m in mappings
            }
            for dst in destinations:
                for col in dst.get("columns", []):
                    col_name = col.get("name") or ""
                    if col_name and col_name.lower() not in mapped_dst:
                        lines.append(
                            f"- **WARNING:** Destination column `{col_name}` has no source mapping."
                        )
            lines.append("")

    # --- Migration plan steps with AI utilization ---
    steps = plan_dict.get("steps", [])
    if steps:
        # Classify steps by strategy
        strategy_counts: dict[str, int] = {}
        ai_required_steps: list[str] = []
        deterministic_steps: list[str] = []
        review_required_steps: list[str] = []

        for step in steps:
            strat = (
                step.get("metadata", {})
                .get("migration_strategy", {})
                .get("strategy", "UNKNOWN")
            )
            strategy_counts[strat] = strategy_counts.get(strat, 0) + 1

            step_name = step.get("name", "?")
            if strat == "AI_ASSISTED":
                ai_required_steps.append(step_name)
            elif strat == "DETERMINISTIC":
                deterministic_steps.append(step_name)
            elif strat == "REVIEW_REQUIRED":
                review_required_steps.append(step_name)

        # AI Utilization Summary
        total_steps = len(steps)
        ai_count = len(ai_required_steps)
        det_count = len(deterministic_steps)
        review_count = len(review_required_steps)
        det_pct = round(det_count / total_steps * 100, 1) if total_steps else 0
        ai_pct = round(ai_count / total_steps * 100, 1) if total_steps else 0
        review_pct = round(review_count / total_steps * 100, 1) if total_steps else 0

        lines.append("## AI Utilization Summary")
        lines.append("")
        lines.append(f"- **Total steps:** {total_steps}")
        lines.append(f"- **Deterministic:** {det_count} ({det_pct}%)")
        lines.append(f"- **AI-Assisted:** {ai_count} ({ai_pct}%)")
        lines.append(f"- **Review required:** {review_count} ({review_pct}%)")
        lines.append("")

        if deterministic_steps:
            lines.append(f"**Deterministic steps:** {', '.join(deterministic_steps)}")
            lines.append("")
        if ai_required_steps:
            lines.append(f"**AI-Assisted steps:** {', '.join(ai_required_steps)}")
            lines.append("")
        if review_required_steps:
            lines.append(f"**Review-required steps:** {', '.join(review_required_steps)}")
            lines.append("")

        # Operation-level breakdown (deterministic vs AI-required)
        if operations_by_step:
            det_ops_total = 0
            ai_ops_total = 0
            error_ops_total = 0
            for step_id, ops in operations_by_step.items():
                for op in ops:
                    if "error" in op:
                        error_ops_total += 1
                    else:
                        op_code = op.get("code", "")
                        if "AI_REQUIRED" in op_code or "placeholder" in op_code.lower():
                            ai_ops_total += 1
                        else:
                            det_ops_total += 1
            total_ops = det_ops_total + ai_ops_total + error_ops_total
            if total_ops:
                det_op_pct = round(det_ops_total / total_ops * 100, 1)
                ai_op_pct = round(ai_ops_total / total_ops * 100, 1)
                lines.append("### Operation-Level Breakdown")
                lines.append("")
                lines.append(f"- **Total operations generated:** {total_ops}")
                lines.append(f"- **Deterministic operations:** {det_ops_total} ({det_op_pct}%) — control flow, staging, lineage, cutoff")
                lines.append(f"- **AI-required operations:** {ai_ops_total} ({ai_op_pct}%) — extract & migrate SQL")
                if error_ops_total:
                    lines.append(f"- **Failed operations:** {error_ops_total} — see step table for details")
                lines.append("")

        # Detailed step table with validation status
        lines.append("## Migration Plan Steps")
        lines.append("")
        lines.append("| Step | Strategy | Language | Source Objects | Target Objects | Status |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for step in steps:
            strat = (
                step.get("metadata", {})
                .get("migration_strategy", {})
                .get("strategy", "?")
            )
            name = step.get("name") or "?"
            lang = step.get("target_language") or "?"
            src = ", ".join(step.get("source_objects", [])) or "N/A"
            tgt = ", ".join(step.get("target_objects", [])) or "N/A"

            # Validation status
            if strat == "DETERMINISTIC":
                status = "✅ Ready"
            elif strat == "AI_ASSISTED":
                status = "🤖 AI needed"
            elif strat == "REVIEW_REQUIRED":
                status = "⚠️ Review"
            else:
                status = "❓ Unknown"

            lines.append(
                f"| {name} | {strat} | {lang} | {src} | {tgt} | {status} |"
            )
        lines.append("")

        # Validation recommendations
        lines.append("## Validation Recommendations")
        lines.append("")
        lines.append("### Static Validation")
        lines.append(f"- Review report generated with {total_steps} steps")
        lines.append(f"- {det_count} steps have deterministic code (control flow + SQL)")
        if ai_count > 0 or review_count > 0:
            lines.append(f"- {ai_count + review_count} steps require AI generation or manual review")
            lines.append("- Supply stored procedure definitions to enable AI generation")
        lines.append("")
        lines.append("### Runtime Validation")
        lines.append("- Execute the generated notebook on Databricks")
        lines.append("- Compare row counts between source and target tables")
        lines.append("- Verify column types match the original schema")
        lines.append("- Check execution order matches the original SSIS control flow")
        lines.append("")

    # --- Component type counts ---
    comp_counts = analysis.get("component_type_counts", {})
    if comp_counts:
        lines.append("## Component Types")
        lines.append("")
        for comp_type, count in sorted(comp_counts.items()):
            lines.append(f"- {comp_type}: {count}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generated by ssis-migration-agent*")

    return "\n".join(lines)


def _apply_procedure_file_fallback(
    operations: list[Any],
    procedures_defs: dict[str, str],
    translator: Any,
) -> None:
    """
    Substitute user-supplied procedure definitions when the
    database-metadata definition cannot be deterministically
    translated.

    Precedence:

    1. Database metadata definition (real source) — used when it
       translates successfully.
    2. Procedures-file definition (user-supplied) — used when the
       metadata definition fails deterministic translation.

    The substituted source is recorded in operation metadata
    (``sql_source``) for traceability; nothing is fabricated.
    """

    for operation in operations:

        if operation.operation_type == "EXTRACT":

            name = operation.metadata.get("source_procedure")

            current_sql = operation.sql

            if current_sql and translator.translate(
                current_sql
            ).status == "TRANSLATED":
                operation.metadata["sql_source"] = "database_metadata"
                continue

            fallback = procedures_defs.get(name or "")

            if (
                fallback
                and translator.translate(fallback).status
                == "TRANSLATED"
            ):
                operation.sql = fallback
                operation.metadata["sql_source"] = "procedures_file"
                operation.metadata[
                    "source_procedure_definition"
                ] = fallback

        elif operation.operation_type in ("INSERT", "MERGE"):

            name = operation.metadata.get("procedure")

            current_definition = operation.metadata.get(
                "procedure_definition"
            )

            if current_definition and translator.translate(
                current_definition
            ).status == "TRANSLATED":
                operation.metadata["sql_source"] = "database_metadata"
                continue

            fallback = procedures_defs.get(name or "")

            if (
                fallback
                and translator.translate(fallback).status
                == "TRANSLATED"
            ):
                operation.metadata["procedure_definition"] = fallback
                operation.metadata["sql_source"] = "procedures_file"


def generate_migration_artifacts(
    package_ir: Any,
    analysis: dict[str, Any],
    connection_mapping_path: Path | None,
    base_path: Path,
    package_output: Path,
    *,
    ai: bool = False,
    ai_provider: str = "databricks",
    catalog: str | None = None,
    package_name: str = "",
    ddl_path: str | Path | None = None,
    procedures_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Run planner -> generator -> notebook + review report.

    When connection_mapping_path is provided, loads external database
    metadata via the mapping. When None, derives table/column metadata
    from the .dtsx package IR alone.
    Produces a Databricks notebook (.ipynb) and a review report (.md)
    in the package output directory.
    """
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.planner.planner import MigrationPlanner
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    from ssis_migration.generator.operations import MigrationOperationBuilder
    from ssis_migration.generator.databricks_operations import (
        DatabricksOperationGenerator,
    )
    from ssis_migration.generator.notebook import (
        NotebookAssembler,
    )

    connection_mapping = None
    if connection_mapping_path is not None:
        from ssis_migration.database.connection_mapping_loader import (
            load_connection_mapping,
        )
        connection_mapping = load_connection_mapping(
            connection_mapping_path
        )

    context = build_migration_context(
        package_ir=package_ir,
        ssis_analysis=analysis,
        connection_mapping=connection_mapping,
        base_path=base_path,
        ddl_path=ddl_path,
    )

    planner = MigrationPlanner()
    plan = planner.build_plan(context)
    plan_dict = plan.to_dict()

    if not package_name:
        package_name = safe_name(
            plan_dict.get("package_name", "migration")
        )

    dispatcher = GeneratorDispatcher()
    op_builder = MigrationOperationBuilder()
    op_generator = DatabricksOperationGenerator()

    # User-supplied procedure definitions are a translation fallback
    # for operations whose database-metadata definition cannot be
    # deterministically translated.
    procedures_defs: dict[str, str] = {}
    if procedures_path:
        from ssis_migration.procedure_file_loader import (
            load_procedures,
        )
        procedures_defs = load_procedures(procedures_path)

    all_artifacts: list[Any] = []
    operations_by_step: dict[str, list[dict[str, Any]]] = {}

    for step in plan_dict["steps"]:
        step_id = step["step_id"]

        artifact = dispatcher.generate(step)
        all_artifacts.append(artifact)

        operations = op_builder.build(step)

        if procedures_defs:
            _apply_procedure_file_fallback(
                operations,
                procedures_defs,
                op_generator.translator,
            )
        op_results: list[dict[str, Any]] = []
        for op in operations:
            try:
                op_code = op_generator.generate_operation(
                    op.to_dict()
                )
                op_results.append({
                    "operation_id": op.operation_id,
                    "operation_type": op.operation_type,
                    "order": op.order,
                    "code": op_code,
                })
            except Exception as op_exc:
                op_results.append({
                    "operation_id": op.operation_id,
                    "operation_type": op.operation_type,
                    "order": op.order,
                    "error": str(op_exc),
                })

        operations_by_step[step_id] = op_results

    # -------------------------------------------------
    # AI-assisted generation (per-item fallback)
    # -------------------------------------------------

    if ai:
        ai_results = _run_ai_assisted_generation(
            plan_dict=plan_dict,
            all_artifacts=all_artifacts,
            artifacts_dir=package_output,
            validation_results=[],
            ai_provider=ai_provider,
        )

    # -------------------------------------------------
    # Assemble the Databricks notebook (.ipynb)
    # -------------------------------------------------

    notebook_assembler = NotebookAssembler()
    notebook_file = notebook_assembler.assemble_and_write(
        plan_dict=plan_dict,
        artifacts=all_artifacts,
        output_path=package_output,
        operations_by_step=operations_by_step,
    )

    # -------------------------------------------------
    # Generate the migration review report (markdown)
    # -------------------------------------------------

    review_file = package_output / f"{package_name}_review.md"
    review_file.write_text(
        _generate_review_report(
            package_name=package_name,
            plan_dict=plan_dict,
            analysis=analysis,
            catalog=catalog or "main",
            notebook_file=str(notebook_file),
            operations_by_step=operations_by_step,
        ),
        encoding="utf-8",
    )

    return {
        "notebook_file": str(notebook_file),
        "review_file": str(review_file),
        "artifact_count": len(all_artifacts),
    }


def run_migration(
    source: str,
    output: str,
    *,
    catalog: str | None = None,
    connection_mapping: str | None = None,
    base_path: str | None = None,
    execute: bool = False,
    ai: bool = False,
    ai_provider: str = "databricks",
    ddl_path: str | None = None,
    procedures_path: str | None = None,
) -> dict[str, Any]:
    """
    Discover and analyze SSIS packages and create a per-package migration
    workspace.

    execute=False is the safe default. Deployment is deliberately not
    claimed here; it must be connected to validated generation adapters.
    """
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    packages = discover_packages(source_path)

    output_path.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "tool": "ssis-migration-agent",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "EXECUTE_REQUESTED" if execute else "ANALYZE_ONLY",
        "target": {
            "platform": "databricks",
            "catalog": catalog,
        },
        "source": str(source_path),
        "output": str(output_path),
        "package_count": len(packages),
        "packages": [],
        "deployment": {
            "status": "NOT_IMPLEMENTED",
            "message": (
                "No target artifacts have been deployed. Connect validated "
                "DDL, transformation generation, and resource deployment "
                "adapters before enabling execution."
            ),
        },
    }

    for package_path in packages:
        package_name = safe_name(package_path.stem)
        package_output = output_path / package_name
        package_output.mkdir(parents=True, exist_ok=True)

        entry: dict[str, Any] = {
            "package": str(package_path),
            "name": package_path.stem,
            "status": "FAILED",
            "artifacts": {},
        }

        try:
            result = analyze_one_package(
                package_path,
                procedures_path=procedures_path,
            )
            package_ir = result["package_ir"]
            analysis = result["analysis"]

            # -------------------------------------------------
            # Resolve connection mapping
            # -------------------------------------------------

            mapping_path: Path | None = None
            if connection_mapping:
                mapping_path = Path(
                    connection_mapping
                ).resolve()
            else:
                auto_mapping = (
                    package_path.parent
                    / "connection_mapping.json"
                )
                if auto_mapping.is_file():
                    mapping_path = auto_mapping.resolve()

            resolved_base = (
                Path(base_path).resolve()
                if base_path
                else Path.cwd()
            )

            # -------------------------------------------------
            # Run planner -> generator -> notebook + report
            # -------------------------------------------------

            if mapping_path and mapping_path.is_file():
                gen_result = generate_migration_artifacts(
                    package_ir=package_ir,
                    analysis=analysis,
                    connection_mapping_path=mapping_path,
                    base_path=resolved_base,
                    package_output=package_output,
                    ai=ai,
                    ai_provider=ai_provider,
                    catalog=catalog,
                    package_name=package_name,
                    ddl_path=ddl_path,
                    procedures_path=procedures_path,
                )
                metadata_stage = "SUCCEEDED"
            else:
                gen_result = generate_migration_artifacts(
                    package_ir=package_ir,
                    analysis=analysis,
                    connection_mapping_path=None,
                    base_path=resolved_base,
                    package_output=package_output,
                    ai=ai,
                    ai_provider=ai_provider,
                    catalog=catalog,
                    package_name=package_name,
                    ddl_path=ddl_path,
                    procedures_path=procedures_path,
                )
                metadata_stage = "SUCCEEDED_DTSX_DERIVED"

            entry.update({
                "status": "MIGRATED",
                "artifacts": {
                    "notebook": gen_result["notebook_file"],
                    "review": gen_result["review_file"],
                },
                "stages": {
                    "package_discovery": "SUCCEEDED",
                    "parsing": "SUCCEEDED",
                    "ir_build": "SUCCEEDED",
                    "analysis": "SUCCEEDED",
                    "target_metadata_discovery": metadata_stage,
                    "transformation_generation": "SUCCEEDED",
                    "notebook_generation": "SUCCEEDED",
                    "deployment": "NOT_IMPLEMENTED",
                    "data_migration": "NOT_IMPLEMENTED",
                },
            })

        except Exception as exc:
            error_file = package_output / "error.json"
            write_json(error_file, {
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            entry["error"] = str(exc)
            entry["error_file"] = str(error_file)

        report["packages"].append(entry)

    succeeded = sum(
        1 for p in report["packages"]
        if p["status"] in ("ANALYZED", "MIGRATED")
    )
    migrated = sum(
        1 for p in report["packages"]
        if p["status"] == "MIGRATED"
    )
    report["analyzed_package_count"] = succeeded
    report["migrated_package_count"] = migrated
    report["failed_package_count"] = (
        len(packages) - succeeded
    )

    if succeeded == 0:
        report["status"] = "ANALYSIS_FAILED"
    elif migrated > 0:
        report["status"] = (
            "MIGRATION_COMPLETE_DEPLOYMENT_PENDING"
        )
    else:
        report["status"] = (
            "ANALYSIS_COMPLETE_DEPLOYMENT_PENDING"
        )

    write_json(output_path / "migration_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover and analyze SSIS packages."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to a .dtsx file or directory containing packages.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Directory where generated migration artifacts are written.",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="Target Databricks catalog name.",
    )
    parser.add_argument(
        "--connection-mapping",
        default=None,
        help=(
            "Path to connection_mapping.json. If omitted, "
            "looks for connection_mapping.json in the "
            "package directory."
        ),
    )
    parser.add_argument(
        "--base-path",
        default=None,
        help=(
            "Root directory for resolving relative "
            "metadata file paths in the connection "
            "mapping. Defaults to the current working "
            "directory."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Request execution. Currently rejected because deployment "
            "adapters are not yet implemented."
        ),
    )

    parser.add_argument(
        "--ai",
        action="store_true",
        help=(
            "Enable LLM-assisted generation as a per-item "
            "fallback for steps that cannot be deterministically "
            "translated. Off by default."
        ),
    )

    parser.add_argument(
        "--ai-provider",
        default="databricks",
        choices=["databricks", "openai", "puter", "mock"],
        help=(
            "AI provider to use when --ai is enabled. "
            "Default: databricks."
        ),
    )

    args = parser.parse_args()

    if args.execute:
        parser.error(
            "--execute is not available yet: target deployment and "
            "execution adapters have not been implemented."
        )

    result = run_migration(
        source=args.source,
        output=args.output,
        catalog=args.catalog,
        connection_mapping=args.connection_mapping,
        base_path=args.base_path,
        execute=False,
        ai=args.ai,
        ai_provider=args.ai_provider,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
