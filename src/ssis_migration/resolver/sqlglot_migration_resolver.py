from __future__ import annotations

from typing import Any

from ssis_migration.resolver.base import Resolver
from ssis_migration.resolver.resolution_engine import (
    SQLResolutionEngine,
)


class SQLGlotMigrationResolver(Resolver):
    """
    Generic Resolver adapter around the existing deterministic
    SQLGlot resolution pipeline.
    """

    name = "sqlglot"

    def __init__(
        self,
        engine: SQLResolutionEngine | None = None,
    ) -> None:
        self.engine = engine or SQLResolutionEngine()

    def can_handle(
        self,
        context: dict[str, Any],
    ) -> bool:
        source_platform = str(
            context.get("source_platform", "")
        ).lower()

        target_platform = str(
            context.get("target_platform", "")
        ).lower()

        source_language = str(
            context.get("source_language", "")
        ).lower()

        target_language = str(
            context.get("target_language", "")
        ).lower()

        supported_source_platforms = {
            "sql_server",
            "mssql",
            "tsql",
            "sqlserver",
        }

        supported_source_languages = {
            "tsql",
            "t-sql",
        }

        supported_target_platforms = {
            "databricks",
            "azure_databricks",
        }

        supported_target_languages = {
            "spark_sql",
            "databricks_sql",
        }

        source_execution_engine = str(
            context.get("source_execution_engine", "")
        ).lower()

        if source_platform in supported_source_platforms:
            source_supported = True
        elif source_execution_engine in supported_source_platforms:
            source_supported = True
        else:
            source_supported = (
                source_language in supported_source_languages
            )

        if target_platform:
            target_supported = (
                target_platform in supported_target_platforms
            )
        else:
            target_supported = (
                target_language in supported_target_languages
            )

        return source_supported and target_supported
        
    def resolve(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        source_sql = context.get("source_sql")

        if not isinstance(source_sql, str) or not source_sql.strip():
            return {
                "status": "UNRESOLVED",
                "resolver": self.name,
                "error": "source_sql is required.",
            }

        source_dialect = context.get(
            "source_dialect",
            "tsql",
        )

        target_dialect = context.get(
            "target_dialect",
            "databricks",
        )

        required_features = set(
            context.get(
                "required_features",
                [],
            )
        )

        result = self.engine.resolve(
            source_sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            required_features=required_features,
        )

        return {
            "status": result.status,
            "resolver": self.name,
            "source_sql": result.source_sql,
            "target_sql": result.target_sql,
            "source_dialect": result.source_dialect,
            "target_dialect": result.target_dialect,
            "validation": result.validation,
            "error": result.error,
        }