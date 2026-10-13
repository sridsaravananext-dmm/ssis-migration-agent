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


def analyze_one_package(package_path: Path) -> dict[str, Any]:
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

    # Keep the established repository API: analyze the built IR, not
    # the original parser result.
    analysis = analyze_package(package_ir.to_dict())

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
) -> list[dict[str, Any]]:
    """
    Call the LLM orchestrator for AI_ASSISTED steps.

    For each step with strategy AI_ASSISTED, sends the
    migration context to the Databricks Foundation Model
    and replaces the AI contract placeholder with
    LLM-generated code.

    Returns additional validation results for the
    LLM-generated code.
    """
    from ssis_migration.agent.databricks_provider import (
        DatabricksProvider,
    )
    from ssis_migration.agent.orchestrator import (
        AIMigrationOrchestrator,
    )

    provider = DatabricksProvider()
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


def generate_migration_artifacts(
    package_ir: Any,
    analysis: dict[str, Any],
    connection_mapping_path: Path,
    base_path: Path,
    package_output: Path,
    *,
    ai: bool = False,
    catalog: str | None = None,
    package_name: str = "",
) -> dict[str, Any]:
    """
    Run planner -> generator -> validator on the analyzed package.

    Requires a connection mapping file with database metadata paths.
    Produces migration_plan.json, generated_artifacts/, and
    validation_results.json in the package output directory.
    """
    from ssis_migration.database.connection_mapping_loader import (
        load_connection_mapping,
    )
    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.planner.planner import MigrationPlanner
    from ssis_migration.generator.dispatcher import GeneratorDispatcher
    from ssis_migration.generator.operations import MigrationOperationBuilder
    from ssis_migration.generator.databricks_operations import (
        DatabricksOperationGenerator,
    )
    from ssis_migration.validator.sql_semantic import SQLSemanticValidator
    from ssis_migration.generator.sql_translator import (
        SQLServerToSparkSQLTranslator,
    )
    from ssis_migration.agent.code_validator import (
        validate_generated_code,
    )
    from ssis_migration.generator.notebook import (
        NotebookAssembler,
    )

    connection_mapping = load_connection_mapping(
        connection_mapping_path
    )

    context = build_migration_context(
        package_ir=package_ir,
        ssis_analysis=analysis,
        connection_mapping=connection_mapping,
        base_path=base_path,
    )

    planner = MigrationPlanner()
    plan = planner.build_plan(context)
    plan_dict = plan.to_dict()

    plan_file = package_output / "migration_plan.json"
    write_json(plan_file, plan_dict)

    if not package_name:
        package_name = safe_name(
            plan_dict.get("package_name", "migration")
        )

    dispatcher = GeneratorDispatcher()
    op_builder = MigrationOperationBuilder()
    op_generator = DatabricksOperationGenerator()
    sql_validator = SQLSemanticValidator()
    translator = SQLServerToSparkSQLTranslator()

    generated_artifacts: list[dict[str, Any]] = []
    validation_results: list[dict[str, Any]] = []
    all_artifacts: list[Any] = []
    operations_by_step: dict[str, list[dict[str, Any]]] = {}

    artifacts_dir = package_output / "generated_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    for step in plan_dict["steps"]:
        step_id = step["step_id"]
        step_name = step["name"]

        artifact = dispatcher.generate(step)
        all_artifacts.append(artifact)

        ext = "py" if artifact.language == "pyspark" else "sql"
        code_file = artifacts_dir / f"{step_id}.{ext}"
        code_file.write_text(
            artifact.code, encoding="utf-8"
        )

        artifact_meta = {
            k: v
            for k, v in artifact.to_dict().items()
            if k != "code"
        }
        meta_file = artifacts_dir / f"{step_id}_meta.json"
        write_json(meta_file, artifact_meta)

        generated_artifacts.append({
            "step_id": step_id,
            "step_name": step_name,
            "artifact_type": artifact.artifact_type,
            "language": artifact.language,
            "code_file": str(code_file),
            "metadata_file": str(meta_file),
        })

        strat = step.get(
            "metadata", {}
        ).get("migration_strategy", {})

        if strat.get("strategy") == "DETERMINISTIC":
            source_procedures = step[
                "metadata"
            ].get(
                "migration_intent", {}
            ).get("source_procedures", [])
            if source_procedures:
                source_sql_full = source_procedures[
                    0
                ].get("definition", "")
                source_query = (
                    translator._extract_main_query(
                        source_sql_full
                    )
                )
                trans = artifact.metadata.get(
                    "translation", {}
                )
                translated_sql = trans.get("sql", "")

                if source_query and translated_sql:
                    sql_result = sql_validator.validate(
                        source_sql=source_query,
                        target_sql=translated_sql,
                        source_dialect="tsql",
                        target_dialect="spark",
                    )
                    validation_results.append({
                        "step_id": step_id,
                        "step_name": step_name,
                        "validator": "sql_semantic",
                        "status": sql_result.status,
                        "checks": dict(
                            sql_result.checks
                        ),
                        "failures": list(
                            sql_result.failures
                        ),
                    })

        if (
            artifact.language == "pyspark"
            and artifact.code
        ):
            code_result = validate_generated_code(
                artifact.code, step_name
            )
            validation_results.append({
                "step_id": step_id,
                "step_name": step_name,
                "validator": "code_validator",
                "status": code_result["status"],
                "issues": code_result["issues"],
            })

        operations = op_builder.build(step)
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

        ops_file = artifacts_dir / f"{step_id}_operations.json"
        write_json(ops_file, op_results)
        operations_by_step[step_id] = op_results

    # -------------------------------------------------
    # AI-assisted generation for AI_ASSISTED steps
    # -------------------------------------------------

    if ai:
        ai_results = _run_ai_assisted_generation(
            plan_dict=plan_dict,
            all_artifacts=all_artifacts,
            artifacts_dir=artifacts_dir,
            validation_results=validation_results,
        )
        validation_results.extend(ai_results)

        # Re-write meta files for artifacts modified by
        # AI generation so they include ai_generated,
        # ai_status, ai_assumptions, and ai_unresolved.
        for artifact in all_artifacts:
            if artifact.metadata.get("ai_generated"):
                step_id = artifact.source_step_id
                artifact_meta = {
                    k: v
                    for k, v in artifact.to_dict().items()
                    if k != "code"
                }
                meta_file = (
                    artifacts_dir / f"{step_id}_meta.json"
                )
                write_json(meta_file, artifact_meta)

    validation_file = package_output / "validation_results.json"
    write_json(validation_file, validation_results)

    # Assemble all generated artifacts into a single
    # Databricks notebook (.ipynb).
    notebook_assembler = NotebookAssembler()
    notebook_file = notebook_assembler.assemble_and_write(
        plan_dict=plan_dict,
        artifacts=all_artifacts,
        output_path=package_output,
        operations_by_step=operations_by_step,
    )

    # Generate Databricks Job definition and DABs YAML
    # from the migration plan.
    from ssis_migration.generator.job_generator import (
        JobGenerator,
    )

    job_gen = JobGenerator()
    job_def = job_gen.generate(
        plan_dict,
        notebook_path=str(notebook_file),
        catalog=catalog or "main",
    )
    job_file = package_output / f"{package_name}_job.json"
    job_file.write_text(
        job_def.to_json(), encoding="utf-8"
    )

    dabs_yaml = job_gen.generate_dabs_yaml(
        plan_dict,
        notebook_path=str(notebook_file),
        catalog=catalog or "main",
    )
    dabs_file = package_output / "databricks.yml"
    dabs_file.write_text(
        dabs_yaml, encoding="utf-8"
    )

    # -------------------------------------------------
    # Generate data validation queries
    # -------------------------------------------------

    data_validation_file: str | None = None
    data_validation_notebook: str | None = None
    data_validation_report: str | None = None

    try:
        from ssis_migration.validator.data_validation import (
            DataValidator,
        )

        source_meta: dict[str, Any] = {}
        target_meta: dict[str, Any] = {}

        for m in connection_mapping.mappings:
            meta_path = (
                base_path / m.metadata_file
                if not Path(m.metadata_file).is_absolute()
                else Path(m.metadata_file)
            )
            if not meta_path.is_file():
                continue
            meta_data = json.loads(
                meta_path.read_text(encoding="utf-8")
            )
            if m.role == "source":
                source_meta = meta_data
            elif m.role == "destination":
                target_meta = meta_data

        if source_meta or target_meta:
            dv = DataValidator()
            dv_report = dv.generate(
                plan_dict,
                source_meta,
                target_meta,
                catalog=catalog or "main",
            )

            dv_sql = dv.generate_sql_file(dv_report)
            dv_sql_file = (
                package_output / f"{package_name}_data_validation.sql"
            )
            dv_sql_file.write_text(dv_sql, encoding="utf-8")
            data_validation_file = str(dv_sql_file)

            dv_nb = dv.generate_notebook(dv_report)
            dv_nb_file = (
                package_output
                / f"{package_name}_Data_Validation.ipynb"
            )
            dv_nb_file.write_text(
                json.dumps(dv_nb, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            data_validation_notebook = str(dv_nb_file)

            dv_report_file = (
                package_output / "data_validation_report.json"
            )
            dv_report_file.write_text(
                dv_report.to_json(), encoding="utf-8"
            )
            data_validation_report = str(dv_report_file)

    except Exception:
        pass

    return {
        "plan_file": str(plan_file),
        "artifacts": generated_artifacts,
        "validation_file": str(validation_file),
        "notebook_file": str(notebook_file),
        "job_file": str(job_file),
        "dabs_file": str(dabs_file),
        "data_validation_file": data_validation_file,
        "data_validation_notebook": data_validation_notebook,
        "data_validation_report": data_validation_report,
        "artifact_count": len(generated_artifacts),
        "validation_count": len(validation_results),
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
            result = analyze_one_package(package_path)
            package_ir = result["package_ir"]
            analysis = result["analysis"]

            ir_data = object_to_dict(package_ir)
            analysis_data = object_to_dict(analysis)

            ir_file = package_output / "package_ir.json"
            analysis_file = package_output / "analysis.json"

            write_json(ir_file, ir_data)
            write_json(analysis_file, analysis_data)

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
            # Run planner -> generator -> validator
            # -------------------------------------------------

            generation_stages: dict[str, str] = {
                "target_metadata_discovery": "NOT_CONNECTED",
                "transformation_generation": "NOT_CONNECTED",
                "data_validation": "NOT_IMPLEMENTED",
            }

            generation_artifacts: dict[str, Any] = {}

            if mapping_path and mapping_path.is_file():
                gen_result = generate_migration_artifacts(
                    package_ir=package_ir,
                    analysis=analysis,
                    connection_mapping_path=mapping_path,
                    base_path=resolved_base,
                    package_output=package_output,
                    ai=ai,
                    catalog=catalog,
                    package_name=package_name,
                )

                generation_stages = {
                    "target_metadata_discovery": "SUCCEEDED",
                    "transformation_generation": "SUCCEEDED",
                    "data_validation": (
                        "SUCCEEDED"
                        if gen_result.get("data_validation_report")
                        else "NOT_AVAILABLE"
                    ),
                }

                generation_artifacts = {
                    "plan": gen_result["plan_file"],
                    "validation": gen_result[
                        "validation_file"
                    ],
                    "notebook": gen_result[
                        "notebook_file"
                    ],
                    "job": gen_result.get(
                        "job_file"
                    ),
                    "dabs": gen_result.get(
                        "dabs_file"
                    ),
                    "data_validation": gen_result.get(
                        "data_validation_file"
                    ),
                    "data_validation_notebook": gen_result.get(
                        "data_validation_notebook"
                    ),
                    "data_validation_report": gen_result.get(
                        "data_validation_report"
                    ),
                    "generated_dir": str(
                        package_output
                        / "generated_artifacts"
                    ),
                    "artifact_count": gen_result[
                        "artifact_count"
                    ],
                    "validation_count": gen_result[
                        "validation_count"
                    ],
                }

            # The manifest is derived from actual
            # parser/analyzer output. It does not contain
            # hard-coded WWI schemas or table names.
            manifest = {
                "package_name": package_path.stem,
                "source_package": str(package_path),
                "target": {
                    "platform": "databricks",
                    "catalog": catalog,
                },
                "artifacts": {
                    "ir": str(ir_file),
                    "analysis": str(analysis_file),
                    **generation_artifacts,
                },
                "stages": {
                    "package_discovery": "SUCCEEDED",
                    "parsing": "SUCCEEDED",
                    "ir_build": "SUCCEEDED",
                    "analysis": "SUCCEEDED",
                    **generation_stages,
                    "job_pipeline_generation": "SUCCEEDED",
                    "deployment": "NOT_IMPLEMENTED",
                    "data_migration": "NOT_IMPLEMENTED",
                },
            }

            manifest_file = package_output / "migration_manifest.json"
            write_json(manifest_file, manifest)

            entry.update({
                "status": (
                    "MIGRATED"
                    if mapping_path
                    else "ANALYZED"
                ),
                "artifacts": {
                    "ir": str(ir_file),
                    "analysis": str(analysis_file),
                    "manifest": str(manifest_file),
                    **generation_artifacts,
                },
                "stages": manifest["stages"],
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
            "Enable LLM-assisted generation for AI_ASSISTED "
            "steps. Uses the Databricks Foundation Model "
            "API to generate migration code for steps "
            "that cannot be deterministically translated."
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
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
