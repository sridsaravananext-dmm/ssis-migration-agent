from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from ssis_migration.languages.sql import parse_sql, normalize_dialect


@dataclass
class SQLGlotResolution:
    status: str
    source_sql: str
    target_sql: str | None
    source_dialect: str
    target_dialect: str
    error: str | None = None
    source_analysis: dict[str, Any] = field(default_factory=dict)
    target_analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_sql": self.source_sql,
            "target_sql": self.target_sql,
            "source_dialect": self.source_dialect,
            "target_dialect": self.target_dialect,
            "error": self.error,
            "scope": "STATIC_SQL_TRANSLATION",
            "semantic_equivalence": "NOT_VERIFIED",
            "source_analysis": self.source_analysis,
            "target_analysis": self.target_analysis,
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

        source = parse_sql(sql, dialect=source_dialect)
        result = SQLGlotResolution('UNRESOLVED', sql, None, source_dialect, target_dialect,
                                   source_analysis=source.report)
        if not source.supported:
            result.status = 'REVIEW_REQUIRED' if source.report['status'] == 'UNSUPPORTED' else 'UNRESOLVED'
            result.error = '; '.join(d['message'] for d in source.report['diagnostics'])
            return result
        if len(source.expressions) != 1:
            result.status = 'REVIEW_REQUIRED'
            result.error = f'Expected exactly one SQL statement, but parsed {len(source.expressions)}.'
            return result
        try:
            target_read = normalize_dialect(target_dialect)
            features = {f for s in source.report['statements'] for f in s['features']}
            if 'TEMPORAL_TABLE_AS_OF' in features and source.report['dialect'] not in {'spark', 'databricks'}:
                result.status = 'REVIEW_REQUIRED'
                result.error = 'Source historical reads require an explicit history strategy; Delta time travel is not an equivalent rewrite.'
                return result
            converted = source.expressions[0].sql(dialect=target_read, unsupported_level=sqlglot.ErrorLevel.RAISE)
            target = parse_sql(converted, dialect=target_read)
            result.target_analysis = target.report
            if not target.supported or len(target.expressions) != 1:
                result.status = 'REVIEW_REQUIRED'
                result.error = 'Rendered target SQL did not pass complete structural analysis.'
                return result
            result.status, result.target_sql = 'RESOLVED', converted
            return result

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            return result
