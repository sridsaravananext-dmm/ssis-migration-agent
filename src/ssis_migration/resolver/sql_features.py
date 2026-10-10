
from __future__ import annotations

import sqlglot
from sqlglot import exp


def detect_required_features(
    source_sql: str,
    *,
    source_dialect: str,
) -> set[str]:
    """
    Detect migration-relevant SQL features from the source AST.

    This function reports features present in the source SQL.
    It does not infer runtime behavior or target capabilities.
    """
    if not source_sql or not source_sql.strip():
        return set()

    expression = sqlglot.parse_one(
        source_sql,
        read=source_dialect,
    )

    features: set[str] = set()

    unions = list(expression.find_all(exp.Union))
    if any(union.args.get("distinct") is False for union in unions):
        features.add("UNION_ALL")

    if any(
        join.args.get("side") == "LEFT"
        for join in expression.find_all(exp.Join)
    ):
        features.add("LEFT_JOIN")

    if any(expression.find_all(exp.Coalesce)):
        features.add("COALESCE")

    # Identify cutoff-style predicates from their actual AST.
    # The validator's current cutoff logic is WWI-specific, so
    # broader validation will be handled in a separate change.
    parameter_names: set[str] = set()

    for node in expression.walk():
        if isinstance(node, exp.Parameter):
            variable = node.find(exp.Var)
            if variable is not None:
                parameter_names.add(variable.this.upper())

    has_lower_cutoff = any(
        name.endswith("LASTCUTOFF")
        for name in parameter_names
    )
    has_upper_cutoff = any(
        name.endswith("NEWCUTOFF")
        for name in parameter_names
    )

    if has_lower_cutoff and has_upper_cutoff:
        features.add("INCREMENTAL_CUTOFF")

    return features