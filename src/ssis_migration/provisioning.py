from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def quote_identifier(value: str) -> str:
    """Escape a SQL identifier for use in Databricks SQL."""
    if not value or not value.strip():
        raise ValueError("SQL identifiers must not be empty.")

    return f"`{value.replace('`', '``')}`"


def required_schemas(metadata: dict[str, Any]) -> list[str]:
    """Derive required target schemas from database metadata."""
    tables = metadata.get("tables")

    if not isinstance(tables, list):
        raise ValueError("Metadata must contain a 'tables' list.")

    schemas = set()

    for table in tables:
        if not isinstance(table, dict):
            raise ValueError("Each table definition must be an object.")

        schema = table.get("schema")

        if not isinstance(schema, str) or not schema.strip():
            raise ValueError(
                f"Table {table.get('name', '<unknown>')!r} "
                "has no valid schema."
            )

        schemas.add(schema.strip().lower())

    return sorted(schemas)


def plan_schema_provisioning(
    metadata: dict[str, Any],
    catalog: str,
    existing_schemas: list[str],
) -> dict[str, Any]:
    """Plan missing schemas without executing any SQL."""
    if not catalog or not catalog.strip():
        raise ValueError("A target catalog is required.")

    required = required_schemas(metadata)
    existing = {
        schema.strip().lower()
        for schema in existing_schemas
        if isinstance(schema, str) and schema.strip()
    }

    missing = [
        schema for schema in required
        if schema not in existing
    ]

    statements = [
        "CREATE SCHEMA IF NOT EXISTS "
        f"{quote_identifier(catalog)}.{quote_identifier(schema)};"
        for schema in missing
    ]

    return {
        "catalog": catalog,
        "required_schemas": required,
        "existing_schemas": sorted(existing),
        "missing_schemas": missing,
        "statements": statements,
        "status": "CHANGES_REQUIRED" if missing else "READY",
        "execution_enabled": False,
    }
def execute_schema_provisioning(
    spark: Any,
    plan: dict[str, Any],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Execute a schema plan only when explicitly enabled."""
    if not execute:
        return {
            "status": "DRY_RUN",
            "executed": False,
            "results": [
                {
                    "schema": schema,
                    "status": "PLANNED",
                    "sql": statement,
                }
                for schema, statement in zip(
                    plan["missing_schemas"],
                    plan["statements"],
                )
            ],
        }

    results = []

    for schema, statement in zip(
        plan["missing_schemas"],
        plan["statements"],
    ):
        try:
            spark.sql(statement)

            results.append({
                "schema": schema,
                "status": "CREATED_OR_ALREADY_EXISTS",
                "sql": statement,
            })

        except Exception as exc:
            results.append({
                "schema": schema,
                "status": "FAILED",
                "sql": statement,
                "error": str(exc),
            })

    failed = [
        item for item in results
        if item["status"] == "FAILED"
    ]

    return {
        "status": "FAILED" if failed else "SUCCEEDED",
        "executed": True,
        "results": results,
    }

def provision_from_metadata_file(
    spark: Any,
    metadata_path: str,
    catalog: str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Load metadata, discover schemas, plan and optionally provision."""
    path = Path(metadata_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Destination metadata file not found: {path}"
        )

    with path.open(encoding="utf-8") as file:
        metadata = json.load(file)

    rows = spark.sql(
        f"SHOW SCHEMAS IN {quote_identifier(catalog)}"
    ).collect()

    existing_schemas = [
        row["databaseName"]
        for row in rows
    ]

    plan = plan_schema_provisioning(
        metadata=metadata,
        catalog=catalog,
        existing_schemas=existing_schemas,
    )

    execution = execute_schema_provisioning(
        spark,
        plan,
        execute=execute,
    )

    return {
        "plan": plan,
        "execution": execution,
        "status": execution["status"],
    }