from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ssis_migration.ddl_generator import generate_ddl
from ssis_migration.provisioning import (
    provision_from_metadata_file,
)


def _split_sql_statements(sql: str) -> list[str]:
    """Split generated DDL on semicolons; generated DDL contains no literals
    with embedded semicolons.
    """
    return [
        statement.strip() + ";"
        for statement in sql.split(";")
        if statement.strip()
    ]


def run_structure_migration(
    spark: Any,
    metadata_path: str,
    catalog: str,
    output_dir: str,
    *,
    execute: bool = False,
    column_mapping: bool = True,
) -> dict[str, Any]:
    """Provision schemas and create target tables from metadata.

    Schema provisioning and table creation are disabled unless execute=True.
    Review findings are reported separately. Incomplete table definitions
    block execution; supported geography columns can be created while their
    data-conversion warning remains visible.
    """
    metadata_file = Path(metadata_path)
    output_path = Path(output_dir)

    if not metadata_file.is_file():
        raise FileNotFoundError(
            f"Destination metadata not found: {metadata_file}"
        )

    output_path.mkdir(parents=True, exist_ok=True)

    metadata = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )

    schema_result = provision_from_metadata_file(
        spark=spark,
        metadata_path=str(metadata_file),
        catalog=catalog,
        execute=execute,
    )

    if schema_result["execution"]["status"] == "FAILED":
        report = {
            "status": "FAILED",
            "mode": "EXECUTE" if execute else "DRY_RUN",
            "stage": "SCHEMA_PROVISIONING",
            "schema_provisioning": schema_result,
        }
        (output_path / "migration_run_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report

    ddl, ddl_report = generate_ddl(
        metadata=metadata,
        catalog=catalog,
        column_mapping=column_mapping,
    )

    ddl_file = output_path / "generated_dw_ddl_preview.sql"
    review_file = output_path / "generated_dw_ddl_review.json"

    ddl_file.write_text(ddl, encoding="utf-8")
    review_file.write_text(
        json.dumps(ddl_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    statements = _split_sql_statements(ddl)

    # Incomplete definitions must never be created.
    incomplete_findings = [
        item for item in ddl_report["review_items"]
        if (
            "omitted one or more columns" in item.get("issue", "")
            or "Missing table schema" in item.get("issue", "")
            or "Unnamed column" in item.get("issue", "")
            or item.get("issue", "").startswith("Unsupported source type")
            or item.get("issue", "").startswith("Invalid decimal")
        )
    ]

    if incomplete_findings:
        table_result = {
            "status": "BLOCKED",
            "executed": False,
            "reason": "Incomplete or unsupported table definitions exist.",
            "results": [],
        }
    elif not execute:
        table_result = {
            "status": "DRY_RUN",
            "executed": False,
            "results": [
                {"sql": statement, "status": "PLANNED"}
                for statement in statements
            ],
        }
    else:
        results = []

        for statement in statements:
            try:
                spark.sql(statement)
                results.append({
                    "sql": statement,
                    "status": "SUCCEEDED",
                })
            except Exception as exc:
                results.append({
                    "sql": statement,
                    "status": "FAILED",
                    "error": str(exc),
                })

        failures = [
            item for item in results
            if item["status"] == "FAILED"
        ]

        table_result = {
            "status": "FAILED" if failures else "SUCCEEDED",
            "executed": True,
            "results": results,
        }

    report = {
        "mode": "EXECUTE" if execute else "DRY_RUN",
        "catalog": catalog,
        "status": (
            "FAILED"
            if (
                schema_result["execution"]["status"] == "FAILED"
                or table_result["status"] in {"FAILED", "BLOCKED"}
            )
            else (
                "REVIEW_REQUIRED"
                if ddl_report["review_items"]
                else table_result["status"]
            )
        ),
        "schema_provisioning": {
            "status": schema_result["execution"]["status"],
            "missing_schemas": schema_result["plan"]["missing_schemas"],
            "executed": schema_result["execution"]["executed"],
            "results": schema_result["execution"]["results"],
        },
        "table_creation": {
            **table_result,
            "planned_statement_count": len(statements),
            "generated_table_count": ddl_report["generated_table_count"],
            "review_findings": ddl_report["review_items"],
            "column_mapping": column_mapping,
        },
        "data_migration": {
            "status": "NOT_IMPLEMENTED",
            "loaded_tables": 0,
        },
        "transformation_generation": {
            "status": "NOT_RUN",
        },
    }

    (output_path / "migration_run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return report
