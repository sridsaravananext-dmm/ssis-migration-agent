from __future__ import annotations

from typing import Any

from ssis_migration.resolver.sql_procedure import (
    extract_relational_sql,
)

from ssis_migration.resolver.sql_features import (
    detect_required_features,
)

PLATFORM_TO_SQL_DIALECT = {
    "sql_server": "tsql",
    "mssql": "tsql",
    "sqlserver": "tsql",
    "databricks": "databricks",
    "azure_databricks": "databricks",
}


def build_resolver_context(
    migration_step: dict[str, Any],
) -> dict[str, Any]:
    """
    Build resolver-specific context from the normalized migration step.

    This adapter intentionally does not modify MigrationContract.
    Resolver-specific details belong here rather than in the
    platform-independent migration contract.
    """

    source_platform = str(
        migration_step.get(
            "source_platform",
            "",
        )
    ).lower()

    target_platform = str(
        migration_step.get(
            "target_platform",
            "",
        )
    ).lower()

    target_language = str(
        migration_step.get(
            "target_language",
            "",
        )
    ).lower()

    metadata = migration_step.get(
        "metadata",
        {},
    )

    migration_intent = metadata.get(
        "migration_intent",
        {},
    )

    source_procedures = migration_intent.get(
        "source_procedures",
        [],
    )

    source_execution_engine = None

    if source_procedures:
        source_execution_engine = "sql_server"

    source_dialect = (
        "tsql"
        if source_execution_engine == "sql_server"
        else None
    )

    target_dialect = PLATFORM_TO_SQL_DIALECT.get(
        target_platform
    )

    source_language = (
        "tsql"
        if source_dialect == "tsql"
        else ""
    )

    # ------------------------------------------------------------
    # Extract relational SQL from the source procedure.
    #
    # The migration intent contains the full SQL Server procedure
    # definition. The SQL resolver should receive only the
    # relational statement(s), not CREATE PROCEDURE / SET / RETURN.
    # ------------------------------------------------------------

    source_sql = None

    if source_procedures:
        first_procedure = source_procedures[0]

        if isinstance(first_procedure, dict):
            definition = first_procedure.get(
                "definition"
            )

            if isinstance(definition, str) and definition.strip():
                source_sql = extract_relational_sql(
                    definition
                )

    # ------------------------------------------------------------
    # Required SQL semantic features.
    #
    # Keep this empty for now. We will populate it only from
    # deterministic semantic evidence rather than guessing from
    # coarse planner metadata.
    # ------------------------------------------------------------

    required_features = set()

    if source_sql and source_dialect:
        required_features = detect_required_features(
            source_sql,
            source_dialect=source_dialect,
        )

    return {
        "source_platform": source_platform,
        "source_execution_engine": source_execution_engine,
        "target_platform": target_platform,
        "source_language": source_language,
        "target_language": target_language,
        "source_sql": source_sql,
        "source_dialect": source_dialect,
        "target_dialect": target_dialect,
        "required_features": sorted(
            required_features
        ),
        "migration_intent": migration_intent,
    }