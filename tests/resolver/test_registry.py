
from __future__ import annotations

from typing import Any

from ssis_migration.resolver.base import Resolver
from ssis_migration.resolver.registry import ResolverRegistry
from ssis_migration.resolver.sqlglot_migration_resolver import (
    SQLGlotMigrationResolver,
)


def test_registry_selects_sqlglot_for_ssis_tsql_to_databricks():
    context = {
        "source_platform": "ssis",
        "source_execution_engine": "sql_server",
        "source_language": "tsql",
        "target_platform": "databricks",
        "target_language": "pyspark",
        "source_dialect": "tsql",
        "target_dialect": "databricks",
        "source_sql": (
            "SELECT CustomerID FROM Sales.Customers"
        ),
        "required_features": [],
    }

    registry = ResolverRegistry(
        resolvers=[SQLGlotMigrationResolver()]
    )

    selection = registry.select(context)

    assert selection.status == "SELECTED"
    assert selection.resolver is not None
    assert selection.resolver.name == "sqlglot"


def test_registry_returns_unresolved_when_no_resolver_matches():
    context = {
        "source_platform": "tm1",
        "target_platform": "databricks",
        "source_language": "tm1",
        "target_language": "pyspark",
    }

    registry = ResolverRegistry(
        resolvers=[SQLGlotMigrationResolver()]
    )

    result = registry.resolve(context)

    assert result["status"] == "UNRESOLVED"
    assert result["resolver"] is None


class AlwaysMatchingResolver(Resolver):
    def __init__(self, name: str) -> None:
        self.name = name

    def can_handle(
        self,
        context: dict[str, Any],
    ) -> bool:
        return True

    def resolve(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "RESOLVED",
            "resolver": self.name,
        }


def test_registry_reports_ambiguity_when_multiple_resolvers_match():
    registry = ResolverRegistry(
        resolvers=[
            AlwaysMatchingResolver("resolver_a"),
            AlwaysMatchingResolver("resolver_b"),
        ]
    )

    selection = registry.select({"source_platform": "example"})

    assert selection.status == "AMBIGUOUS"
    assert selection.resolver is None
    assert selection.candidates == [
        "resolver_a",
        "resolver_b",
    ]