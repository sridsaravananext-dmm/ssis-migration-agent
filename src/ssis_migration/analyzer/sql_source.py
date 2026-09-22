from __future__ import annotations

import re
from typing import Any


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
){{0,3}}
"""


TABLE_REFERENCE_PATTERN = re.compile(
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
    (?P<object>{QUALIFIED_IDENTIFIER})
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_identifier(value: str) -> str:
    parts = []

    for part in value.strip().split("."):
        part = part.strip()

        if (
            len(part) >= 2
            and part.startswith("[")
            and part.endswith("]")
        ):
            part = part[1:-1]

        parts.append(part)

    return ".".join(parts)


def extract_sql_objects(
    sql: str,
) -> list[dict[str, str]]:

    if not sql:
        return []

    results = []

    for match in TABLE_REFERENCE_PATTERN.finditer(sql):
        object_name = match.group("object")

        if not object_name:
            continue

        text = match.group(0).strip().upper()

        if text.startswith("DELETE FROM"):
            usage = "DELETE"

        elif text.startswith("MERGE INTO"):
            usage = "MERGE"

        elif text.startswith("UPDATE"):
            usage = "UPDATE"

        elif text.startswith("INTO"):
            usage = "INTO"

        elif text.startswith("JOIN"):
            usage = "JOIN"

        else:
            usage = "FROM"

        results.append(
            {
                "object": normalize_identifier(object_name),
                "usage": usage,
            }
        )

    return results


def extract_sql_statements(sql: str) -> list[str]:
    if not sql:
        return []

    statements = []

    for statement in sql.split(";"):
        statement = statement.strip()

        if statement:
            statements.append(statement)

    return statements


def analyze_sql_source(
    sql: str,
) -> dict[str, Any]:

    if not sql:
        return {
            "has_sql": False,
            "objects": [],
            "statements": [],
        }

    objects = extract_sql_objects(sql)

    return {
        "has_sql": True,
        "objects": objects,
        "statements": extract_sql_statements(sql),
    }