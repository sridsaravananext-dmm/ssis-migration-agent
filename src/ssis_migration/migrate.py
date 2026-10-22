from __future__ import annotations

import argparse
import hashlib
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


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
    *,
    file_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run the repository's real parser -> IR builder -> analyzer pipeline.

    This function does not fabricate target SQL or claim that transformations
    have been translated.
    """
    from ssis_migration.adapters import get_adapter

    return get_adapter("ssis").analyze(
        package_path, procedures_path=procedures_path, file_record=file_record,
    ).to_legacy_result()


def _run_ai_assisted_generation(
    plan_dict: dict[str, Any],
    all_artifacts: list[Any],
    artifacts_dir: Path,
    validation_results: list[dict[str, Any]],
    *,
    ai_provider: str = "databricks",
    databases: list[dict[str, Any]] | None = None,
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
    from ssis_migration.migration import prepare_generation_step

    # Validate every attached binding before initializing a provider/client.
    for step in plan_dict["steps"]:
        prepare_generation_step(step)

    try:
        provider = create_provider(ai_provider)
        orchestrator = AIMigrationOrchestrator(provider)
    except Exception as exc:
        return [{
            "step_id": "package", "step_name": "AI initialization",
            "validator": "ai_orchestrator", "scope": "STATIC_CODE",
            "status": "REJECTED", "error": str(exc),
        }]

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
            ai_validation.append({
                "step_id": step_id, "step_name": step_name,
                "validator": "ai_orchestrator", "scope": "STATIC_CODE",
                "status": "REJECTED", "error": "Missing step artifact.",
            })
            continue

        # Per-item fallback: only invoke the LLM when the
        # deterministic path produced a placeholder. If the
        # artifact already has real generated code, skip it.
        if (
            artifact.code
            and artifact.artifact_type not in {"AI_CONTRACT", "AI_MIGRATION_ARTIFACT"}
            and "AI_REQUIRED" not in artifact.code
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
                migration_step={**step, "databases": databases or []},
                procedure_definitions=proc_defs
                or None,
            )
        except Exception as exc:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "scope": "STATIC_CODE",
                "status": "REJECTED",
                "error": str(exc),
            })
            continue

        from ssis_migration.agent.code_validator import validate_generated_code

        code_result = validate_generated_code(
            result.code, step_name, language=result.language,
        )
        contract = step.get("metadata", {}).get("migration_contract")
        if contract:
            result.unresolved_items = list(dict.fromkeys([
                *result.unresolved_items, *contract["unresolved_items"],
            ]))
            result.validation_requirements = list(dict.fromkeys([
                *contract["validation_requirements"], *result.validation_requirements,
            ]))
        language_aliases = {"python": "pyspark", "py": "pyspark", "sql": "spark_sql", "databricks": "spark_sql", "databricks_sql": "spark_sql"}
        response_key = result.language.strip().lower().replace(" ", "_").replace("-", "_")
        expected_key = artifact.language.strip().lower().replace(" ", "_").replace("-", "_")
        response_language = language_aliases.get(response_key, response_key)
        expected_language = language_aliases.get(expected_key, expected_key)
        if response_language == "pyspark":
            from ssis_migration.validator.readiness import validate_operations

            embedded_result = validate_operations({step_id: [{"operation_id": "ai_candidate", "code": result.code}]})[0]
            code_result["issues"].extend(embedded_result["issues"])
            if embedded_result["status"] == "REJECTED" or code_result["status"] == "REJECTED":
                code_result["status"] = "REJECTED"
            elif embedded_result["status"] == "REVIEW_REQUIRED":
                code_result["status"] = "REVIEW_REQUIRED"
        language_matches = response_language == expected_language
        status = result.status
        if not language_matches or code_result["status"] == "REJECTED":
            status = "REJECTED"
        elif result.unresolved_items or code_result["status"] == "REVIEW_REQUIRED":
            status = "REVIEW_REQUIRED"

        if status == "GENERATED" and result.code:
            # Replace the AI contract with
            # LLM-generated code.
            artifact.code = result.code
            artifact.language = response_language
            artifact.artifact_type = "NOTEBOOK_CELL"
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
            artifact.metadata["ai_validation_requirements"] = result.validation_requirements
            artifact.metadata["static_code_validation"] = code_result

            # Write the LLM-generated code.
            ext = "py" if artifact.language == "pyspark" else "sql"
            code_file = artifacts_dir / f"{safe_name(step_id)}.{ext}"
            code_file.write_text(
                result.code, encoding="utf-8"
            )

            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "scope": "STATIC_CODE",
                "status": "PASS",
                "code_validation": code_result,
                "assumptions": result.assumptions,
                "validation_requirements": result.validation_requirements,
                "unresolved_items": (
                    result.unresolved_items
                ),
            })
        else:
            ai_validation.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "ai_orchestrator",
                "scope": "STATIC_CODE",
                "status": status,
                "code_validation": code_result,
                "language_matches": language_matches,
                "validation_requirements": result.validation_requirements,
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
    readiness: dict[str, Any] | None = None,
    validation_results: list[dict[str, Any]] | None = None,
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
    if readiness:
        lines.append(f"- **Lifecycle:** {readiness['lifecycle_status']}")
        lines.append(f"- **Acceptance:** {readiness['acceptance']['status']} (not accepted)")
    lines.append("- **Validation scope:** static checks only; execution and data reconciliation have not run.")
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
        lines.append("These are generation strategy counts, not verified semantic coverage.")
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
                        if (
                            not op_code.strip()
                            or any(marker in op_code.upper() for marker in ("AI_REQUIRED", "REVIEW_REQUIRED", "TODO", "PLACEHOLDER"))
                        ):
                            ai_ops_total += 1
                        else:
                            det_ops_total += 1
            total_ops = det_ops_total + ai_ops_total + error_ops_total
            if total_ops:
                det_op_pct = round(det_ops_total / total_ops * 100, 1)
                ai_op_pct = round(ai_ops_total / total_ops * 100, 1)
                lines.append("### Initial Operation Generation (history; AI may replace a step)")
                lines.append("")
                lines.append(f"- **Total operations generated:** {total_ops}")
                lines.append(f"- **Operations without placeholder markers:** {det_ops_total} ({det_op_pct}%) — not proof of equivalence")
                lines.append(f"- **Operations requiring implementation/review:** {ai_ops_total} ({ai_op_pct}%)")
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

            findings = [v for v in (validation_results or []) if v.get("step_id") == step.get("step_id")]
            if any(v.get("status") not in {"PASS"} for v in findings):
                status = "Blocked / review required"
            else:
                status = "Generated; behavior unverified"

            lines.append(
                f"| {name} | {strat} | {lang} | {src} | {tgt} | {status} |"
            )
        lines.append("")

        # Validation recommendations
        lines.append("## Validation Recommendations")
        lines.append("")
        lines.append("### Static Validation")
        lines.append(f"- Review report generated with {total_steps} steps")
        lines.append(f"- {det_count} steps selected deterministic generation; see validation results for actual findings")
        if ai_count > 0 or review_count > 0:
            lines.append(f"- {ai_count + review_count} steps require AI generation or manual review")
            lines.append("- Supply stored procedure definitions to enable AI generation")
        lines.append("")
        lines.append("### Runtime Validation")
        lines.append("- Resolve all blocking findings before controlled execution on an explicitly selected Databricks test target")
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

    if readiness and readiness.get("blocking_issues"):
        lines.append("## Blocking Findings")
        lines.append("")
        for issue in readiness["blocking_issues"]:
            lines.append(f"- {issue}")
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
    source_diagnostics: list[dict[str, Any]] | None = None,
    source_evidence: list[dict[str, Any]] | None = None,
    file_dependencies: list[dict[str, Any]] | None = None,
    normalized_ir: dict[str, Any] | None = None,
    workspace_config: dict[str, Any] | None = None,
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
    from ssis_migration.agent.code_validator import validate_generated_code
    from ssis_migration.validator.readiness import (
        summarize_readiness,
        validate_operations,
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
    if normalized_ir is not None:
        from ssis_migration.ir.normalized import validate_normalized_ir
        from ssis_migration.migration import bind_plan_to_ir, validate_plan_ir_binding

        validate_normalized_ir(normalized_ir, legacy_ir=object_to_dict(package_ir))
        plan_dict = bind_plan_to_ir(plan_dict, normalized_ir)
        validate_plan_ir_binding(plan_dict, normalized_ir)
    plan_dict["metadata"]["source_diagnostics"] = source_diagnostics or []
    if workspace_config is not None:
        plan_dict['metadata']['workspace_config'] = workspace_config
        for step in plan_dict['steps']:
            step.setdefault('metadata',{})['workspace_config'] = workspace_config
    package_output.mkdir(parents=True, exist_ok=True)
    artifacts_dir = package_output / "generated_artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    plan_file = package_output / "migration_plan.json"
    write_json(plan_file, plan_dict)
    plan_binding_file = None
    if normalized_ir is not None:
        plan_binding_file = package_output / "plan_ir_binding.json"
        write_json(plan_binding_file, plan_dict["metadata"]["normalized_ir_binding"])

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

    from ssis_migration.migration import build_migration_contract, validate_contract
    from ssis_migration.generator.job_generator import JobGenerator

    # Reject invalid dependency graphs and build every contract before any
    # deterministic or AI target code is generated.
    JobGenerator().generate(plan_dict, notebook_path="pending_contract_validation")
    package_source = str(Path(package_ir.source_file["file_path"]).absolute()) if package_ir.source_file else None
    evidence = [
        file for file in (source_evidence or [])
        if file["role"] != "package" or file["path"] == package_source
    ]
    contracts = []
    contract_results = []
    for step in plan_dict["steps"]:
        contract = build_migration_contract(
            step, source_platform="ssis", evidence_files=evidence,
            file_dependencies=file_dependencies, supporting_definitions=procedures_defs,
            source_diagnostics=source_diagnostics,
        ).to_dict()
        validate_contract(contract, step)
        step["metadata"]["migration_contract"] = contract
        contracts.append(contract)
        contract_results.append({
            "step_id": step["step_id"], "step_name": step["name"],
            "validator": "migration_contract", "scope": "CONTRACT_STRUCTURE",
            "contract_id": contract["contract_id"],
            "status": "REVIEW_REQUIRED" if contract["unresolved_items"] else "PASS",
            "unresolved_items": contract["unresolved_items"],
        })
    contracts_file = package_output / "migration_contracts.json"
    write_json(contracts_file, {
        "contract_bundle_version": "1.0", "package_name": plan_dict["package_name"],
        "validation_scope": "STRUCTURAL_ONLY", "contracts": contracts,
    })
    write_json(plan_file, plan_dict)

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
                    "migration_contract_id": step["metadata"]["migration_contract"]["contract_id"],
                })
            except Exception as op_exc:
                op_results.append({
                    "operation_id": op.operation_id,
                    "operation_type": op.operation_type,
                    "order": op.order,
                    "error": str(op_exc),
                    "migration_contract_id": step["metadata"]["migration_contract"]["contract_id"],
                })

        operations_by_step[step_id] = op_results

    # -------------------------------------------------
    # AI-assisted generation (per-item fallback)
    # -------------------------------------------------

    ai_results: list[dict[str, Any]] = []
    if ai:
        ai_results = _run_ai_assisted_generation(
            plan_dict=plan_dict,
            all_artifacts=all_artifacts,
            artifacts_dir=artifacts_dir,
            validation_results=[],
            ai_provider=ai_provider,
            databases=[database.to_dict() for database in context.databases],
        )

    # NotebookAssembler selects a whole AI artifact instead of the earlier
    # operations when AI generation succeeds. Keep those attempts on disk,
    # but only the active code path determines static readiness.
    ai_strategy_steps = {
        step["step_id"] for step in plan_dict["steps"]
        if step.get("metadata", {}).get("migration_strategy", {}).get("strategy") == "AI_ASSISTED"
    }
    ai_steps = {
        artifact.source_step_id for artifact in all_artifacts
        if artifact.metadata.get("ai_generated") and artifact.source_step_id in ai_strategy_steps
    }
    validation_results = validate_operations({
        step_id: operations for step_id, operations in operations_by_step.items()
        if step_id not in ai_steps
    })
    validation_results.extend(ai_results)
    validation_results.extend(contract_results)
    validation_results.extend({
        "step_id": "package", "step_name": package_name,
        "validator": "source_intake", "scope": "SOURCE_EVIDENCE",
        "status": "REJECTED" if diagnostic["severity"] == "ERROR" else "REVIEW_REQUIRED",
        "issues": [diagnostic],
    } for diagnostic in (source_diagnostics or []) if diagnostic["severity"] in {"WARNING", "ERROR"})
    generated_artifacts: list[dict[str, Any]] = []
    for artifact in all_artifacts:
        step_id = artifact.source_step_id
        active_artifact = step_id in ai_steps or not operations_by_step.get(step_id)
        pending = artifact.artifact_type in {"AI_CONTRACT", "AI_MIGRATION_ARTIFACT", "UNRESOLVED"}
        if pending:
            code_result = {
                "status": "REVIEW_REQUIRED",
                "issues": [{"code": "IMPLEMENTATION_REQUIRED", "severity": "WARNING", "message": "Artifact is an unresolved translation contract, not executable implementation."}],
            }
        else:
            code_result = validate_generated_code(artifact.code, artifact.name, language=artifact.language)
        if active_artifact:
            validation_results.append({
                "step_id": step_id, "step_name": artifact.name,
                "validator": "artifact_code", "scope": "STATIC_CODE", **code_result,
            })
            if not pending and artifact.language in {"python", "py", "pyspark"}:
                validation_results.extend(validate_operations({step_id: [{
                    "operation_id": "active_artifact", "code": artifact.code,
                }]}))
        stem = safe_name(step_id)
        extension = "py" if artifact.language in {"pyspark", "python"} else "sql"
        code_file = artifacts_dir / f"{stem}.{extension}"
        code_file.write_text(artifact.code, encoding="utf-8")
        meta_file = artifacts_dir / f"{stem}_meta.json"
        write_json(meta_file, {key: value for key, value in artifact.to_dict().items() if key != "code"})
        operations_file = artifacts_dir / f"{stem}_operations.json"
        write_json(operations_file, operations_by_step.get(step_id, []))
        generated_artifacts.append({
            "step_id": step_id, "code": str(code_file),
            "metadata": str(meta_file), "operations": str(operations_file),
        })

    # -------------------------------------------------
    # Assemble the Databricks notebook (.ipynb)
    # -------------------------------------------------

    notebook_assembler = NotebookAssembler()
    for step in plan_dict["steps"]:
        findings = [
            record for record in validation_results
            if record.get("step_id") == step["step_id"]
            or (record.get("step_id") == "package" and record.get("validator") == "source_intake")
        ]
        blocking = [record for record in findings if record.get("status") != "PASS"]
        step["metadata"]["static_validation"] = {
            "status": "REVIEW_REQUIRED" if blocking else "PASS",
            "findings": blocking,
        }
    write_json(plan_file, plan_dict)
    notebook_file = notebook_assembler.assemble_and_write(
        plan_dict=plan_dict,
        artifacts=all_artifacts,
        output_path=package_output,
        operations_by_step=operations_by_step,
    )
    notebook = json.loads(notebook_file.read_text(encoding="utf-8"))
    notebook_validation = notebook_assembler.validate(notebook)
    validation_results.append({
        "step_id": "package", "step_name": package_name,
        "validator": "notebook", **notebook_validation,
    })
    for cell in notebook["cells"]:
        metadata = cell.get("metadata", {})
        if metadata.get("blocked"):
            validation_results.append({
                "step_id": metadata.get("step_id", "package"),
                "step_name": metadata.get("step_id", package_name),
                "validator": "notebook_step", "scope": "STATIC_NOTEBOOK",
                "status": "REVIEW_REQUIRED", "issues": metadata.get("blocking_reasons", []),
            })

    from ssis_migration.generator.job_generator import JobGenerator

    job_generator = JobGenerator()
    selected_schema = workspace_config['schemas'][workspace_config['target_layer']] if workspace_config else 'default'
    job = job_generator.generate(plan_dict, str(notebook_file), catalog=catalog or "main", schema=selected_schema)
    job_file = package_output / f"{package_name}_job.json"
    job_settings = job.to_dict()
    if workspace_config:
        from ssis_migration.workspace import configure_job
        job_settings = configure_job(job_settings,workspace_config)
    write_json(job_file, job_settings)
    dabs_file = package_output / "databricks.yml"
    dabs = json.loads(job_generator.generate_dabs_yaml(
        plan_dict, str(notebook_file), catalog=catalog or "main", schema=selected_schema,
    ))
    if workspace_config:
        for key,settings in dabs['resources']['jobs'].items():
            dabs['resources']['jobs'][key] = configure_job(settings,workspace_config,variables=True)
        for name in ('notebook_root','existing_cluster_id'):
            dabs['variables'][name] = {'description':'User-selected '+name.replace('_',' ')}
            if workspace_config[name]:dabs['variables'][name]['default'] = workspace_config[name]
    write_json(dabs_file,dabs)
    validation_results.append({
        "step_id": "package", "step_name": package_name,
        "validator": "job_graph", "scope": "STATIC_JOB_GRAPH",
        "status": "PASS", "checks": {"declared_dependencies_valid": True},
    })
    readiness = summarize_readiness(
        validation_results,
        unresolved_items=plan_dict.get("unresolved", []),
        generated_artifact_count=len(all_artifacts),
    )
    validation_file = package_output / "validation_results.json"
    write_json(validation_file, validation_results)
    readiness_file = package_output / "readiness.json"
    write_json(readiness_file, readiness)

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
            readiness=readiness,
            validation_results=validation_results,
        ),
        encoding="utf-8",
    )

    return {
        "notebook_file": str(notebook_file),
        "review_file": str(review_file),
        "plan_file": str(plan_file),
        "contracts_file": str(contracts_file),
        "plan_binding_file": str(plan_binding_file) if plan_binding_file else None,
        "validation_file": str(validation_file),
        "readiness_file": str(readiness_file),
        "job_file": str(job_file),
        "dabs_file": str(dabs_file),
        "artifacts": generated_artifacts,
        "readiness": readiness,
        "validation_count": len(validation_results),
        "artifact_count": len(all_artifacts),
    }


def _package_intake_diagnostics(
    intake: dict[str, Any], record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep another package's parse error separate from shared-input errors."""
    package_ids = {
        file["file_id"] for file in intake["files"]
        if file["role"] == "package" or Path(file["path"]).suffix.lower() == ".dtsx"
    }
    findings = []
    for diagnostic in intake["diagnostics"]:
        affected = set(diagnostic["file_ids"])
        global_decision = any(token in diagnostic["code"] for token in ("MIXED", "EXCLUDED", "PLATFORM_CONFLICT"))
        if (
            not global_decision and affected and affected <= package_ids
            and record["file_id"] not in affected
        ):
            continue
        findings.append(diagnostic)
    return findings


def _intake_role_path(
    intake: dict[str, Any], role: str, explicit: str | None,
) -> str | None:
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    candidates = [
        file for file in intake["files"]
        if file["role"] == role and file["status"] == "AVAILABLE"
    ]
    if len(candidates) == 1:
        return candidates[0]["path"]
    if len(candidates) > 1:
        intake["diagnostics"].append({
            "code": "AMBIGUOUS_DEPENDENCY_ROLE", "severity": "ERROR",
            "message": f"Multiple {role} files were supplied. The existing SSIS loader accepts one export for this role; explicitly select it instead of silently choosing the first.",
            "file_ids": [file["file_id"] for file in candidates],
            "evidence": [
                {"file_id": file["file_id"], "locator": "/", "rule": "unique_legacy_dependency_role"}
                for file in candidates
            ],
        })
        intake["status"] = "BLOCKED"
    return None


def _verify_intake_files(intake: dict[str, Any]) -> None:
    """Do not use changed input bytes under an earlier provenance snapshot."""
    from ssis_migration.adapters.base import SourceEvidenceError

    for record in intake["files"]:
        if record["status"] != "AVAILABLE":
            continue
        path = Path(record["path"])
        if path.is_symlink():
            raise SourceEvidenceError(f"Source evidence became a symbolic link: {path}")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SourceEvidenceError(f"Source evidence is no longer readable: {path}") from exc
        if len(content) != record["size_bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise SourceEvidenceError(f"Source evidence changed after intake: {path}")


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
    dependency_files: list[str] | None = None,
    source_platform: str | None = None,
    file_roles: dict[str, str] | None = None,
    target_mapping: str | None = None,
    workspace_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Discover and analyze SSIS packages and create a per-package migration
    workspace.

    execute=False is the safe default. Deployment is deliberately not
    claimed here; it must be connected to validated generation adapters.

    Legacy status/count fields describe artifact generation only. The
    versioned lifecycle_status and acceptance fields describe readiness.
    """
    if execute:
        raise ValueError("Execution is not implemented; generate and review artifacts with execute=False.")
    if workspace_config is not None:
        from ssis_migration.workspace import validate_workspace_config
        workspace_config = validate_workspace_config(workspace_config)
        if catalog is not None and catalog != workspace_config['catalog']:
            raise ValueError('Catalog argument conflicts with explicit workspace settings.')
        if Path(source).expanduser().resolve() != Path(workspace_config['source_path']).expanduser().resolve():
            raise ValueError('Source path conflicts with explicit workspace settings.')
        if not Path(output).expanduser().resolve().is_relative_to(Path(workspace_config['artifact_path']).expanduser().resolve()):
            raise ValueError('Output path must be inside the configured artifact destination.')

    from ssis_migration.intake import describe_file
    selected = str(source_platform or '').strip().lower().replace('-', '_').replace(' ', '_')
    explicit_tm1 = selected in {'tm1', 'ibm_tm1', 'planning_analytics'}
    detected_tm1 = source_platform is None and Path(source).expanduser().is_file() and describe_file(source)['format'] == 'tm1_source_bundle_v1'
    if explicit_tm1 or detected_tm1:
        if ai or any((catalog, connection_mapping, ddl_path, procedures_path, dependency_files, file_roles, base_path)):
            raise ValueError('TM1 uses explicit bundle roles and target mappings; declare dependencies inside its manifest. AI generation is unavailable.')
        if target_mapping is not None:
            from ssis_migration.generator.tm1.pipeline import run_tm1_generation
            return run_tm1_generation(source, output, target_mapping, workspace_config=workspace_config)
        if workspace_config is not None:
            raise ValueError('Native TM1 workspace settings require a target mapping for candidate generation.')
        from ssis_migration.adapters.tm1.pipeline import run_tm1_review
        return run_tm1_review(source, output)

    if target_mapping is not None:
        raise ValueError('Target mappings are currently supported only for explicit TM1 native bundles.')
    if workspace_config is not None:
        catalog = workspace_config['catalog']

    from ssis_migration.validator.readiness import summarize_readiness
    from ssis_migration.intake import build_intake_manifest
    from ssis_migration.schemas import validate_document, validate_evidence_references

    source_path = Path(source).expanduser().absolute()
    output_path = Path(output).expanduser().resolve()
    if source_path.is_dir() and source_path == output_path:
        raise ValueError("Choose an output directory separate from the supplied source directory.")

    supplied_dependencies = list(dependency_files or [])
    effective_roles = dict(file_roles or {})
    for path, role in (
        (connection_mapping, "connection_mapping"),
        (ddl_path, "ddl"), (procedures_path, "procedures"),
    ):
        if path:
            supplied_dependencies.append(path)
            effective_roles.setdefault(str(Path(path).expanduser().resolve()), role)
    if source_path.is_file():
        auto_mapping = source_path.parent / "connection_mapping.json"
        if not connection_mapping and auto_mapping.is_file():
            supplied_dependencies.append(str(auto_mapping))
    intake = build_intake_manifest(
        source_path, dependencies=supplied_dependencies,
        base_path=base_path or Path.cwd(), platform=source_platform,
        role_overrides=effective_roles, exclude_paths=[output_path],
    )
    for explicit_path, expected_role in (
        (connection_mapping, "connection_mapping"), (ddl_path, "ddl"),
        (procedures_path, "procedures"),
    ):
        if not explicit_path:
            continue
        record = next((
            file for file in intake["files"]
            if Path(file["path"]).absolute() == Path(explicit_path).expanduser().absolute()
        ), None)
        if record and record["confirmed_role"] not in {None, expected_role}:
            intake["diagnostics"].append({
                "code": "CONFLICTING_ROLE_CONFIRMATIONS", "severity": "ERROR",
                "message": f"Explicit {expected_role} input conflicts with the confirmed {record['confirmed_role']} role for {record['path']}.",
                "file_ids": [record["file_id"]],
                "evidence": [{"file_id": record["file_id"], "locator": "decision:/role", "rule": "conflicting_explicit_role"}],
            })
            intake["status"] = "BLOCKED"
    connection_mapping = _intake_role_path(intake, "connection_mapping", connection_mapping)
    ddl_path = _intake_role_path(intake, "ddl", ddl_path)
    procedures_path = _intake_role_path(intake, "procedures", procedures_path)
    selected_paths = {
        str(Path(path).expanduser().absolute())
        for path in (connection_mapping, ddl_path, procedures_path) if path
    }
    mapping_ids = {
        record["file_id"] for record in intake["files"]
        if record["role"] == "connection_mapping" and record["path"] in selected_paths
    }
    metadata_ids = {
        edge["target_file_id"] for edge in intake["dependencies"]
        if edge["source_file_id"] in mapping_ids and edge["status"] == "RESOLVED"
    }
    for record in intake["files"]:
        unconsumed = record["role"] in {"parameters", "configuration", "script"} or (
            record["role"] == "database_metadata" and record["file_id"] not in metadata_ids
        )
        if record["status"] == "AVAILABLE" and unconsumed:
            intake["diagnostics"].append({
                "code": "UNCONSUMED_DEPENDENCY", "severity": "WARNING",
                "message": f"File {record['path']} is inventoried as {record['role']}, but the current SSIS pipeline does not consume it. Behavior depending on it remains unresolved.",
                "file_ids": [record["file_id"]],
                "evidence": [{"file_id": record["file_id"], "locator": "/", "rule": "legacy_input_consumption"}],
            })
    if intake["status"] == "READY" and any(d["severity"] == "WARNING" for d in intake["diagnostics"]):
        intake["status"] = "REVIEW_REQUIRED"
    validate_document(intake, "intake_manifest")
    validate_evidence_references(intake)
    package_records = [file for file in intake["files"] if (
        file["role"] == "package"
        or Path(file["path"]).suffix.lower() == ".dtsx"
        or (file["role"] == "script" and file["platform"] != "unknown")
    )]
    if source_path.is_file() and not package_records:
        package_records = [file for file in intake["files"] if Path(file["path"]) == source_path]
    packages = [Path(file["path"]) for file in package_records]

    output_path.mkdir(parents=True, exist_ok=True)

    run_id = uuid4().hex
    intake_file = output_path / f"intake_manifest.{run_id}.json"
    write_json(intake_file, intake)
    report: dict[str, Any] = {
        "run_id": run_id,
        "result_version": "1.0",
        "legacy_status_semantics": "Artifact generation only; consult lifecycle_status and acceptance for readiness.",
        "tool": "ssis-migration-agent",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "EXECUTE_REQUESTED" if execute else "ANALYZE_ONLY",
        "target": {
            "platform": "databricks",
            "catalog": catalog,
        },
        "source": str(source_path),
        "intake_manifest": str(intake_file),
        "source_intake": {
            "manifest_version": intake["manifest_version"],
            "detected_platform": intake["detected_platform"],
            "confirmed_platform": intake["confirmed_platform"],
            "status": intake["status"], "supplied_file_count": len(intake["files"]),
            "diagnostic_count": len(intake["diagnostics"]),
        },
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

    if workspace_config is not None:
        report['workspace_config'] = workspace_config
        write_json(output_path/'workspace_config.json',workspace_config)

    for package_path, file_record in zip(packages, package_records):
        package_name = safe_name(package_path.stem)
        package_output = output_path / package_name
        if package_output.exists() and any(package_output.iterdir()):
            # Preserve prior outputs/user files and never expose an old
            # success manifest as the result of a failed refresh.
            package_output = output_path / f"{package_name}_{uuid4().hex}"
        package_output.mkdir(parents=True, exist_ok=True)

        entry: dict[str, Any] = {
            "package": str(package_path),
            "name": package_path.stem,
            "run_id": run_id,
            "status": "FAILED",
            "artifacts": {"intake_manifest": str(intake_file)},
            "detected_platform": file_record["platform"],
            "export_format": file_record["format"],
            "export_version": file_record["export_version"],
            "lifecycle_status": "BLOCKED",
        }

        try:
            source_diagnostics = _package_intake_diagnostics(intake, file_record)
            entry["source_diagnostics"] = source_diagnostics
            if connection_mapping and not Path(connection_mapping).is_file():
                raise FileNotFoundError(f"Supplied connection mapping does not exist: {connection_mapping}")
            errors = [finding["message"] for finding in source_diagnostics if finding["severity"] == "ERROR"]
            if errors:
                raise ValueError("Source intake is blocked: " + "; ".join(errors))
            _verify_intake_files(intake)
            result = analyze_one_package(
                package_path,
                procedures_path=procedures_path,
                file_record=file_record,
            )
            package_ir = result["package_ir"]
            analysis = result["analysis"]
            source_envelope = result["source_envelope"]
            source_diagnostics.extend(source_envelope["diagnostics"])
            from ssis_migration.ir.normalized import validate_normalized_ir

            normalized_ir = result["normalized_ir"]
            validate_normalized_ir(normalized_ir, legacy_ir=object_to_dict(package_ir))
            source_diagnostics.extend(d for d in normalized_ir["diagnostics"] if d not in source_diagnostics)
            normalized_file = package_output / "normalized_ir.json"
            write_json(normalized_file, normalized_ir)
            normalization = {
                "ir_version": normalized_ir["ir_version"], "status": normalized_ir["status"],
                "semantic_status": normalized_ir["semantic_status"],
                "node_count": len(normalized_ir["nodes"]), "edge_count": len(normalized_ir["edges"]),
                "unresolved_edge_count": sum(e["resolution"] == "UNRESOLVED" for e in normalized_ir["edges"]),
                "capability_gaps": normalized_ir["capability_gaps"],
            }
            entry["normalization"] = normalization
            entry["artifacts"]["normalized_ir"] = str(normalized_file)
            envelope_file = package_output / "source_envelope.json"
            write_json(envelope_file, source_envelope)
            entry["artifacts"]["source_envelope"] = str(envelope_file)
            ir_file = package_output / "package_ir.json"
            analysis_file = package_output / "analysis.json"
            write_json(ir_file, object_to_dict(package_ir))
            write_json(analysis_file, analysis)
            entry["artifacts"].update({"ir": str(ir_file), "analysis": str(analysis_file)})

            # -------------------------------------------------
            # Resolve connection mapping
            # -------------------------------------------------

            mapping_path: Path | None = None
            if connection_mapping:
                mapping_path = Path(
                    connection_mapping
                ).resolve()
                if not mapping_path.is_file():
                    raise FileNotFoundError(f"Supplied connection mapping does not exist: {mapping_path}")
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
                    source_diagnostics=source_diagnostics,
                    source_evidence=intake["files"],
                    file_dependencies=intake["dependencies"],
                    normalized_ir=normalized_ir,
                    workspace_config=workspace_config,
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
                    source_diagnostics=source_diagnostics,
                    source_evidence=intake["files"],
                    file_dependencies=intake["dependencies"],
                    normalized_ir=normalized_ir,
                    workspace_config=workspace_config,
                )
                metadata_stage = "SUCCEEDED_DTSX_DERIVED"

            generation_artifacts = {
                "ir": str(ir_file), "analysis": str(analysis_file),
                "normalized_ir": str(normalized_file),
                "intake_manifest": str(intake_file), "source_envelope": str(envelope_file),
                "plan": gen_result["plan_file"],
                "contracts": gen_result["contracts_file"],
                "plan_ir_binding": gen_result["plan_binding_file"],
                "validation": gen_result["validation_file"],
                "readiness": gen_result["readiness_file"],
                "notebook": gen_result["notebook_file"],
                "review": gen_result["review_file"],
                "job": gen_result["job_file"], "dabs": gen_result["dabs_file"],
                "generated_dir": str(package_output / "generated_artifacts"),
                "artifact_count": gen_result["artifact_count"],
                "validation_count": gen_result["validation_count"],
            }
            _verify_intake_files(intake)
            stages = {
                "package_discovery": "SUCCEEDED", "parsing": "SUCCEEDED",
                "source_intake": "REVIEW_REQUIRED" if any(d["severity"] == "WARNING" for d in source_diagnostics) else "READY",
                "source_envelope_validation": "SUCCEEDED",
                "ir_build": "SUCCEEDED", "analysis": "SUCCEEDED",
                "normalized_ir_build": "SUCCEEDED", "normalized_ir_validation": "SUCCEEDED",
                "normalized_plan_binding": "SUCCEEDED",
                "migration_contract_generation": "SUCCEEDED",
                "migration_contract_schema_validation": "SUCCEEDED",
                "target_metadata_discovery": metadata_stage,
                "transformation_generation": "SUCCEEDED",
                "notebook_generation": "SUCCEEDED",
                "job_pipeline_generation": "SUCCEEDED",
                "static_validation": "SUCCEEDED",
                "data_validation": "NOT_RUN", "execution_validation": "NOT_RUN",
                "deployment": "NOT_IMPLEMENTED", "data_migration": "NOT_IMPLEMENTED",
            }
            manifest_file = package_output / "migration_manifest.json"
            generation_artifacts["manifest"] = str(manifest_file)
            manifest = {
                "run_id": run_id,
                "package_name": package_path.stem,
                "source_package": str(package_path),
                "detected_platform": file_record["platform"],
                "export_format": file_record["format"],
                "export_version": file_record["export_version"],
                "target": report["target"], "artifacts": generation_artifacts,
                "stages": stages,
                "normalization": normalization,
                "stage_semantics": "SUCCEEDED records completion of the stage; inspect scoped validation findings for pass/fail. Data checks and execution did not run.",
                **gen_result["readiness"],
            }
            write_json(manifest_file, manifest)
            entry.update({
                "status": "MIGRATED",
                "legacy_status_semantics": report["legacy_status_semantics"],
                "artifacts": generation_artifacts,
                "stages": stages,
                **gen_result["readiness"],
            })

        except Exception as exc:
            error_file = package_output / "error.json"
            write_json(error_file, {
                "error": str(exc),
                "traceback": traceback.format_exc(),
            })
            entry["error"] = str(exc)
            entry["error_file"] = str(error_file)
            entry.update(summarize_readiness([{
                "step_id": "package", "step_name": package_path.stem,
                "validator": "pipeline", "scope": "STATIC_PIPELINE",
                "status": "REJECTED", "error": str(exc),
            }]))

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

    run_validation = [{
        "step_id": package["name"], "step_name": package["name"],
        "validator": "package_readiness", "scope": "STATIC_PIPELINE",
        "status": "REVIEW_REQUIRED" if package["lifecycle_status"] == "BLOCKED" else "PASS",
        "issues": package.get("blocking_issues", []),
    } for package in report["packages"]]
    run_validation.extend({
        "step_id": "intake", "step_name": "Source intake",
        "validator": "source_intake", "scope": "SOURCE_EVIDENCE",
        "status": "REJECTED" if diagnostic["severity"] == "ERROR" else "REVIEW_REQUIRED",
        "issues": [diagnostic],
    } for diagnostic in intake["diagnostics"] if diagnostic["severity"] in {"ERROR", "WARNING"})
    report.update(summarize_readiness(run_validation, generated_artifact_count=migrated))
    report["blocked_package_count"] = sum(p["lifecycle_status"] == "BLOCKED" for p in report["packages"])

    write_json(output_path / "migration_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory offline exports, migrate SSIS packages, or generate bounded TM1 Databricks candidates with an explicit target mapping."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to an SSIS export, input directory, or explicit TM1 bundle manifest; TM1 candidate generation requires --target-mapping.",
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
    parser.add_argument("--source-platform", choices=["ssis", "tm1", "informatica", "bods", "odi", "datastage", "ab_initio"], help="Confirm the source platform; unimplemented and excluded platforms are explicitly blocked.")
    parser.add_argument("--dependency", action="append", default=[], help="Additional supplied dependency file (repeatable); no source-system connection is used.")
    parser.add_argument("--file-role", action="append", default=[], metavar="PATH=ROLE", help="Confirm a supplied file's role; contradictory content remains a blocker.")
    parser.add_argument("--ddl", help="Explicitly selected DDL export file.")
    parser.add_argument("--procedures", help="Explicitly selected procedure definitions export file.")
    parser.add_argument("--target-mapping", help="Explicit TM1 Databricks mapping JSON; opt into bounded candidate generation.")

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

    file_roles = {}
    for decision in args.file_role:
        path, separator, role = decision.rpartition("=")
        if not separator or not path or not role:
            parser.error("--file-role requires PATH=ROLE")
        if path in file_roles and file_roles[path] != role:
            parser.error(f"Conflicting role confirmations for {path!r}")
        file_roles[path] = role

    result = run_migration(
        source=args.source,
        output=args.output,
        catalog=args.catalog,
        connection_mapping=args.connection_mapping,
        base_path=args.base_path,
        execute=False,
        ai=args.ai,
        ai_provider=args.ai_provider,
        ddl_path=args.ddl,
        procedures_path=args.procedures,
        dependency_files=args.dependency,
        source_platform=args.source_platform,
        file_roles=file_roles,
        target_mapping=args.target_mapping,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
