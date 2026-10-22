from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ssis_migration.resolver.sqlglot_resolver import SQLGlotResolver
from ssis_migration.validator.sql_semantic import SQLSemanticValidator


@dataclass
class SQLResolutionResult:
    status: str
    source_sql: str
    target_sql: str | None
    source_dialect: str
    target_dialect: str
    validation: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_sql": self.source_sql,
            "target_sql": self.target_sql,
            "source_dialect": self.source_dialect,
            "target_dialect": self.target_dialect,
            "validation": self.validation,
            "error": self.error,
        }


class SQLResolutionEngine:
    """
    Deterministic SQL resolution pipeline.

    1. Translate source SQL using SQLGlot.
    2. Validate the translated SQL against migration-specific
       semantic requirements.
    3. Accept the result only when both stages succeed.
    """

    name = "sql_resolution_engine"

    def __init__(
        self,
        resolver: SQLGlotResolver | None = None,
        validator: SQLSemanticValidator | None = None,
    ) -> None:
        self.resolver = resolver or SQLGlotResolver()
        self.validator = validator or SQLSemanticValidator()

    def resolve(
        self,
        sql: str,
        *,
        source_dialect: str,
        target_dialect: str,
        required_features: set[str] | None = None,
    ) -> SQLResolutionResult:
        resolution = self.resolver.resolve(
            sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
        )

        if resolution.status != "RESOLVED":
            return SQLResolutionResult(
                status=resolution.status,
                source_sql=sql,
                target_sql=None,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
                validation={
                    "status": resolution.status, "scope": "SQL_AST_STRUCTURE",
                    "semantic_equivalence": "NOT_VERIFIED",
                    "source_analysis": getattr(resolution, "source_analysis", {}),
                    "target_analysis": getattr(resolution, "target_analysis", {}),
                },
                error=resolution.error,
            )

        validation = self.validator.validate(
            sql,
            resolution.target_sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            required_features=required_features,
        )

        validation_dict = validation.to_dict()

        if validation.status != "PASS":
            return SQLResolutionResult(
                status="REVIEW_REQUIRED",
                source_sql=sql,
                target_sql=resolution.target_sql,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
                validation=validation_dict,
                error="Deterministic semantic validation failed.",
            )

        return SQLResolutionResult(
            status="RESOLVED",
            source_sql=sql,
            target_sql=resolution.target_sql,
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            validation=validation_dict,
        )
