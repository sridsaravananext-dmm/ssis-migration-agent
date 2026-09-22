from __future__ import annotations

import re
from typing import Any


RESULT_SET_PATTERN = re.compile(
    r"""
    WITH
    \s+
    RESULT
    \s+
    SETS
    \s*
    \(
        \s*
        \(
            (?P<columns>.*?)
        \)
        \s*
    \)
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


COLUMN_PATTERN = re.compile(
    r"""
    (?:
        \[
            (?P<bracketed_name>[^\]]+)
        \]
        |
        (?P<plain_name>
            [A-Za-z_][A-Za-z0-9 _$#@]*
        )
    )
    \s+
    (?P<data_type>
        [A-Za-z0-9_]+
        (?:
            \s*
            \(
                [^)]*
            \)
        )?
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_result_set(sql: str) -> list[dict[str, str]]:
    if not sql:
        return []

    match = RESULT_SET_PATTERN.search(sql)

    if not match:
        return []

    columns_text = match.group("columns")

    columns = []

    for column_match in COLUMN_PATTERN.finditer(columns_text):
        name = (
            column_match.group("bracketed_name")
            or column_match.group("plain_name")
        )

        data_type = column_match.group("data_type")

        if not name or not data_type:
            continue

        columns.append(
            {
                "name": name.strip(),
                "data_type": data_type.strip(),
            }
        )

    return columns


def analyze_result_set(
    component: dict[str, Any],
) -> dict[str, Any]:

    properties = component.get("properties") or {}

    sql = properties.get("SqlCommand")

    if not sql:
        return {
            "has_result_set": False,
            "column_count": 0,
            "columns": [],
        }

    columns = parse_result_set(str(sql))

    return {
        "has_result_set": bool(columns),
        "column_count": len(columns),
        "columns": columns,
    }