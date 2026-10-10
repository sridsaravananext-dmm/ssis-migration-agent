from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot


@dataclass
class SQLGlotResolution:
    status: str
    source_sql: str
    target_sql: str | None
    source_dialect: str
    target_dialect: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_sql": self.source_sql,
            "target_sql": self.target_sql,
            "source_dialect": self.source_dialect,
            "target_dialect": self.target_dialect,
            "error": self.error,
        }


class SQLGlotResolver:
    """
    Deterministic SQL dialect resolver.

    This component translates relational SQL between dialects.
    It does not perform migration planning or semantic validation.
    """

    name = "sqlglot"

    def resolve(
        self,
        sql: str,
        *,
        source_dialect: str,
        target_dialect: str,
    ) -> SQLGlotResolution:

        try:
            converted = sqlglot.transpile(
                sql,
                read=source_dialect,
                write=target_dialect,
            )

            if len(converted) != 1:
                return SQLGlotResolution(
                    status="REVIEW_REQUIRED",
                    source_sql=sql,
                    target_sql=None,
                    source_dialect=source_dialect,
                    target_dialect=target_dialect,
                    error=(
                        "Expected exactly one SQL statement, "
                        f"but SQLGlot produced {len(converted)}."
                    ),
                )

            return SQLGlotResolution(
                status="RESOLVED",
                source_sql=sql,
                target_sql=converted[0],
                source_dialect=source_dialect,
                target_dialect=target_dialect,
            )

        except Exception as exc:
            return SQLGlotResolution(
                status="UNRESOLVED",
                source_sql=sql,
                target_sql=None,
                source_dialect=source_dialect,
                target_dialect=target_dialect,
                error=f"{type(exc).__name__}: {exc}",
            )