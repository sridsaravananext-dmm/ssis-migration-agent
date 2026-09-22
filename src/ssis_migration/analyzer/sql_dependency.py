from __future__ import annotations

import re
from typing import Any


# Supports:
#
# dbo.Table
# schema.Table
# database.schema.Table
# [dbo].[Table]
# [database].[schema].[Table]
# [Table]
#
IDENTIFIER = r"""
(?:
    \[[^\]]+\]
    |
    [A-Za-z_][A-Za-z0-9_$#@]*
)
"""

QUALIFIED_IDENTIFIER = rf"""
{IDENTIFIER}
(?:
    \s*\.\s*{IDENTIFIER}
){{0,2}}
"""


SQL_OBJECT_PATTERN = re.compile(
    rf"""
    \b
    (?:
        FROM
        |
        JOIN
        |
        INTO
        |
        UPDATE
        |
        DELETE\s+FROM
        |
        MERGE\s+INTO
    )
    \s+
    (?P<object>
        {QUALIFIED_IDENTIFIER}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


EXEC_PATTERN = re.compile(
    rf"""
    \b
    (?:EXEC(?:UTE)?)
    \s+
    (?P<object>
        {QUALIFIED_IDENTIFIER}
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _normalize_identifier(value: str) -> str:
    value = value.strip()

    parts = [
        part.strip()
        for part in value.split(".")
    ]

    normalized = []

    for part in parts:
        part = part.strip()

        if (
            len(part) >= 2
            and part.startswith("[")
            and part.endswith("]")
        ):
            part = part[1:-1]

        normalized.append(part)

    return ".".join(normalized)


def _extract_sql_objects(
    sql: str,
) -> list[dict[str, str]]:
    objects: list[dict[str, str]] = []

    for match in SQL_OBJECT_PATTERN.finditer(sql):
        object_name = match.group("object")

        if not object_name:
            continue

        normalized = _normalize_identifier(
            object_name
        )

        usage_text = match.group(0).strip()

        if usage_text.upper().startswith("DELETE FROM"):
            usage = "DELETE"
        elif usage_text.upper().startswith("MERGE INTO"):
            usage = "MERGE"
        elif usage_text.upper().startswith("UPDATE"):
            usage = "UPDATE"
        elif usage_text.upper().startswith("INSERT"):
            usage = "INSERT"
        elif usage_text.upper().startswith("INTO"):
            usage = "INTO"
        elif usage_text.upper().startswith("JOIN"):
            usage = "JOIN"
        else:
            usage = "FROM"

        objects.append(
            {
                "object": normalized,
                "usage": usage,
            }
        )

    return objects


def _extract_executed_objects(
    sql: str,
) -> list[str]:
    objects: list[str] = []

    for match in EXEC_PATTERN.finditer(sql):
        object_name = match.group("object")

        if object_name:
            objects.append(
                _normalize_identifier(object_name)
            )

    return objects


def analyze_sql_dependency(
    component: dict[str, Any],
) -> dict[str, Any]:
    properties = component.get("properties") or {}

    sql_fragments: list[str] = []

    for key in (
        "SqlCommand",
        "SqlCommandVariable",
    ):
        value = properties.get(key)

        if value:
            sql_fragments.append(str(value))

    sql = "\n".join(sql_fragments)

    open_rowset = properties.get("OpenRowset")

    result = {
        "component": component.get("name"),
        "has_sql": bool(sql),
        "sql": sql or None,
        "stored_procedures": [],
        "objects": [],
        "target_object": open_rowset,
    }

    if sql:
        result["stored_procedures"] = sorted(
            set(_extract_executed_objects(sql))
        )

        result["objects"] = _extract_sql_objects(
            sql
        )

    return result