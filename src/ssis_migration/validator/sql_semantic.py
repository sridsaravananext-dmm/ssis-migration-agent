from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass
class SQLSemanticValidation:
    status: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "failures": list(self.failures),
        }


class SQLSemanticValidator:
    """
    Deterministic structural validator for translated SQL.

    This validates migration-relevant SQL structure. It does not
    execute the SQL and does not claim runtime equivalence.
    """

    @staticmethod
    def _normalized_predicates(expression: exp.Expression) -> list[str]:
        where = expression.find(exp.Where)

        if where is None:
            return []

        predicates = []

        for predicate in where.this.flatten():
            if isinstance(predicate, exp.Predicate):
                predicates.append(
                    predicate.sql()
                )

        return predicates
    
    def validate(
        self,
        source_sql: str,
        target_sql: str,
        *,
        source_dialect: str,
        target_dialect: str,
        required_features: set[str] | None = None,
    ) -> SQLSemanticValidation:

        required_features = required_features or set()

        checks: dict[str, bool] = {}
        failures: list[str] = []

        try:
            source = sqlglot.parse_one(
                source_sql,
                read=source_dialect,
            )

            target = sqlglot.parse_one(
                target_sql,
                read=target_dialect,
            )

        except Exception as exc:
            return SQLSemanticValidation(
                status="UNRESOLVED",
                checks={},
                failures=[
                    f"SQL parsing failed: {type(exc).__name__}: {exc}"
                ],
            )

        # ----------------------------------------------------------
        # SELECT projection structure
        # ----------------------------------------------------------

        source_selects = list(source.find_all(exp.Select))
        target_selects = list(target.find_all(exp.Select))

        source_projection_counts = [
            len(select.expressions)
            for select in source_selects
        ]

        target_projection_counts = [
            len(select.expressions)
            for select in target_selects
        ]

        checks["projection_structure"] = (
            source_projection_counts == target_projection_counts
        )

        if not checks["projection_structure"]:
            failures.append(
                "SELECT projection counts changed: "
                f"source={source_projection_counts}, "
                f"target={target_projection_counts}"
            )

        # ----------------------------------------------------------
        # UNION structure
        # ----------------------------------------------------------

        source_unions = list(source.find_all(exp.Union))
        target_unions = list(target.find_all(exp.Union))

        checks["union_count"] = (
            len(source_unions) == len(target_unions)
        )

        if not checks["union_count"]:
            failures.append(
                "UNION count changed: "
                f"source={len(source_unions)}, "
                f"target={len(target_unions)}"
            )

        source_union_all = [
            union.args.get("distinct") is False
            for union in source_unions
        ]

        target_union_all = [
            union.args.get("distinct") is False
            for union in target_unions
        ]

        checks["union_all_semantics"] = (
            source_union_all == target_union_all
        )

        if not checks["union_all_semantics"]:
            failures.append(
                "UNION ALL semantics changed."
            )

        # ----------------------------------------------------------
        # Tables
        # ----------------------------------------------------------

        source_tables = sorted(
            {
                (
                    table.catalog or "",
                    table.db or "",
                    table.name,
                )
                for table in source.find_all(exp.Table)
            }
        )

        target_tables = sorted(
            {
                (
                    table.catalog or "",
                    table.db or "",
                    table.name,
                )
                for table in target.find_all(exp.Table)
            }
        )

        checks["source_tables"] = (
            source_tables == target_tables
        )

        if not checks["source_tables"]:
            failures.append(
                "Source tables changed: "
                f"source={source_tables}, "
                f"target={target_tables}"
            )

        # ----------------------------------------------------------
        # Required relational features
        # ----------------------------------------------------------

        if "UNION_ALL" in required_features:
            checks["required_union_all"] = (
                bool(source_unions)
                and bool(target_unions)
                and source_union_all == target_union_all
                and all(source_union_all)
                and all(target_union_all)
            )

            if not checks["required_union_all"]:
                failures.append(
                    "Required UNION ALL semantics were not preserved."
                )

        if "LEFT_JOIN" in required_features:
            source_left_joins = [
                join
                for join in source.find_all(exp.Join)
                if join.args.get("side") == "LEFT"
            ]

            target_left_joins = [
                join
                for join in target.find_all(exp.Join)
                if join.args.get("side") == "LEFT"
            ]

            checks["required_left_join"] = (
                len(source_left_joins)
                == len(target_left_joins)
            )

            if not checks["required_left_join"]:
                failures.append(
                    "Required LEFT JOIN semantics were not preserved."
                )

        if "COALESCE" in required_features:
            source_coalesce = list(
                source.find_all(exp.Coalesce)
            )

            target_coalesce = list(
                target.find_all(exp.Coalesce)
            )

            checks["required_coalesce"] = (
                len(source_coalesce)
                == len(target_coalesce)
            )

            if not checks["required_coalesce"]:
                failures.append(
                    "Required COALESCE expressions were not preserved."
                )

        if "INCREMENTAL_CUTOFF" in required_features:
            def extract_cutoff_predicates(expression: exp.Expression):
                predicates = []

                for node in expression.find_all(
                    (exp.GT, exp.GTE, exp.LT, exp.LTE)
                ):
                    left = node.this
                    right = node.expression

                    if not isinstance(left, exp.Column):
                        continue

                    if not isinstance(right, exp.Parameter):
                        continue

                    variable = right.find(exp.Var)

                    if variable is None:
                        continue

                    predicates.append(
                        (
                            left.sql(),
                            type(node).__name__,
                            variable.this,
                        )
                    )

                return predicates

            required_cutoff = {
                ("ct.LastEditedWhen", "GT", "LastCutoff"),
                ("ct.LastEditedWhen", "LTE", "NewCutoff"),
            }

            source_predicates = extract_cutoff_predicates(source)
            target_predicates = extract_cutoff_predicates(target)

            source_cutoff_ok = required_cutoff.issubset(
                set(source_predicates)
            )

            target_cutoff_ok = required_cutoff.issubset(
                set(target_predicates)
            )

            checks["incremental_cutoff"] = (
                source_cutoff_ok
                and target_cutoff_ok
            )

            if not checks["incremental_cutoff"]:
                failures.append(
                    "Incremental cutoff semantics were not preserved."
                )
        # ----------------------------------------------------------
        # Final result
        # ----------------------------------------------------------

        if failures:
            status = "FAIL"
        else:
            status = "PASS"

        return SQLSemanticValidation(
            status=status,
            checks=checks,
            failures=failures,
        )