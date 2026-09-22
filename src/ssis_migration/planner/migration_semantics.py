from __future__ import annotations

import re
from typing import Any


def analyze_migration_semantics(
    procedure: dict[str, Any],
) -> dict[str, Any]:

    sql = procedure.get("definition") or ""

    normalized = re.sub(
        r"\s+",
        " ",
        sql.upper(),
    )

    operations: list[str] = []

    # ---------------------------------------------------------
    # SQL operations
    # ---------------------------------------------------------

    if re.search(r"\bSELECT\b", normalized):
        operations.append("SELECT")

    if re.search(r"\bINSERT\s+INTO\b", normalized):
        operations.append("INSERT")

    if re.search(r"\bUPDATE\b", normalized):
        operations.append("UPDATE")

    if re.search(r"\bDELETE\s+FROM\b", normalized):
        operations.append("DELETE")

    if re.search(r"\bMERGE\b", normalized):
        operations.append("MERGE")

    if re.search(r"\bTRUNCATE\s+TABLE\b", normalized):
        operations.append("TRUNCATE")

    # ---------------------------------------------------------
    # Relational features
    # ---------------------------------------------------------

    has_join = bool(
        re.search(
            r"\b(INNER|LEFT|RIGHT|FULL|CROSS)?\s*JOIN\b",
            normalized,
        )
    )

    has_union = bool(
        re.search(
            r"\bUNION(?:\s+ALL)?\b",
            normalized,
        )
    )

    has_case = bool(
        re.search(
            r"\bCASE\b",
            normalized,
        )
    )

    has_order_by = bool(
        re.search(
            r"\bORDER\s+BY\b",
            normalized,
        )
    )

    has_group_by = bool(
        re.search(
            r"\bGROUP\s+BY\b",
            normalized,
        )
    )

    has_cte = bool(
        re.search(
            r"\bWITH\s+[A-Z_][A-Z0-9_]*\s+AS\s*\(",
            normalized,
        )
    )

    # ---------------------------------------------------------
    # Temporary objects
    # ---------------------------------------------------------

    temporary_tables = sorted(
        set(
            re.findall(
                r"#([A-Z0-9_]+)",
                normalized,
            )
        )
    )

    # ---------------------------------------------------------
    # Procedural features
    # ---------------------------------------------------------

    has_cursor = bool(
        re.search(
            r"\bCURSOR\b",
            normalized,
        )
    )

    has_loop = bool(
        re.search(
            r"\bWHILE\b",
            normalized,
        )
    )

    # ---------------------------------------------------------
    # Incremental / cutoff logic
    # ---------------------------------------------------------

    has_cutoff_parameters = (
        "@LASTCUTOFF" in normalized
        or "@NEWCUTOFF" in normalized
    )

    has_cutoff_filter = bool(
        re.search(
            r"(LASTEDITEDWHEN|LASTMODIFIEDWHEN)"
            r".{0,150}"
            r"@LASTCUTOFF",
            normalized,
        )
    )

    has_temporal_logic = (
        "VALIDFROM" in normalized
        or "VALIDTO" in normalized
        or "VALID FROM" in normalized
        or "VALID TO" in normalized
    )

    has_point_in_time_lookup = (
        bool(
            re.search(
                r"VALID\s*FROM\s*<=",
                normalized,
            )
        )
        and
        bool(
            re.search(
                r"VALID\s*TO\s*>",
                normalized,
            )
        )
    )

    # ---------------------------------------------------------
    # Determine extraction pattern
    # ---------------------------------------------------------

    if has_temporal_logic:

        extraction_pattern = "TEMPORAL"

    elif has_cutoff_parameters and has_cutoff_filter:

        extraction_pattern = "INCREMENTAL_QUERY"

    elif has_cutoff_parameters:

        extraction_pattern = "INCREMENTAL_QUERY"

    else:

        extraction_pattern = "FULL_SNAPSHOT"

    # ---------------------------------------------------------
    # Determine relational complexity
    # ---------------------------------------------------------

    relational_features = []

    if has_join:
        relational_features.append("JOIN")

    if has_union:
        relational_features.append("UNION")

    if has_case:
        relational_features.append("CASE")

    if has_order_by:
        relational_features.append("ORDER_BY")

    if has_group_by:
        relational_features.append("GROUP_BY")

    if has_cte:
        relational_features.append("CTE")

    # ---------------------------------------------------------
    # Determine execution style
    # ---------------------------------------------------------

    if has_cursor or has_loop:

        execution_style = "PROCEDURAL"

    elif relational_features:

        execution_style = "RELATIONAL"

    else:

        execution_style = "SIMPLE_RELATIONAL"

    # ---------------------------------------------------------
    # Overall pattern
    # ---------------------------------------------------------

    if extraction_pattern == "TEMPORAL":

        pattern = "TEMPORAL"

    elif (
        extraction_pattern == "INCREMENTAL_QUERY"
        and has_union
    ):

        pattern = "INCREMENTAL_COMPOSITE"

    elif (
        extraction_pattern == "INCREMENTAL_QUERY"
        and relational_features
    ):

        pattern = "INCREMENTAL_RELATIONAL"

    elif extraction_pattern == "INCREMENTAL_QUERY":

        pattern = "INCREMENTAL_SIMPLE"

    elif has_union:

        pattern = "FULL_SNAPSHOT_COMPOSITE"

    elif relational_features:

        pattern = "FULL_SNAPSHOT_RELATIONAL"

    else:

        pattern = "FULL_SNAPSHOT_SIMPLE"

    return {
        "operations": operations,

        "pattern": pattern,

        "extraction_pattern": extraction_pattern,

        "execution_style": execution_style,

        "temporary_tables": temporary_tables,

        "has_cursor": has_cursor,

        "has_loop": has_loop,

        "has_cutoff_parameters": (
            has_cutoff_parameters
        ),

        "has_cutoff_filter": (
            has_cutoff_filter
        ),

        "has_temporal_logic": (
            has_temporal_logic
        ),

        "has_point_in_time_lookup": (
            has_point_in_time_lookup
        ),

        "relational_features": (
            relational_features
        ),
    }