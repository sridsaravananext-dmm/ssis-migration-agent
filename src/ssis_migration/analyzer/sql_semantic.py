from __future__ import annotations

import re
from typing import Any


PATTERNS = {
    "SELECT": r"\bSELECT\b",
    "INSERT": r"\bINSERT\b",
    "UPDATE": r"\bUPDATE\b",
    "DELETE": r"\bDELETE\b",
    "MERGE": r"\bMERGE\b",
    "EXEC": r"\bEXEC(?:UTE)?\b",
    "JOIN": r"\bJOIN\b",
    "GROUP_BY": r"\bGROUP\s+BY\b",
    "ORDER_BY": r"\bORDER\s+BY\b",
    "WINDOW": r"\bOVER\s*\(",
    "CASE": r"\bCASE\b",
    "CTE": r"\bWITH\s+\w+\s+AS\s*\(",
    "CURSOR": r"\bCURSOR\b",
    "WHILE": r"\bWHILE\b",
    "TRY_CATCH": r"\bBEGIN\s+TRY\b",
    "TEMP_TABLE": r"(?:CREATE\s+TABLE\s+#\w+|#\w+)",
    "TABLE_VARIABLE": r"\bDECLARE\s+@\w+\s+TABLE\b",
    "DYNAMIC_SQL": r"\bEXEC(?:UTE)?\s*\(",
}


def detect_features(sql: str) -> list[str]:
    if not sql:
        return []

    return [
        feature
        for feature, pattern in PATTERNS.items()
        if re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    ]


def extract_referenced_objects(sql: str) -> list[str]:
    if not sql:
        return []

    cleaned_sql = re.sub(
        r"--.*?$|/\*.*?\*/",
        "",
        sql,
        flags=re.MULTILINE | re.DOTALL,
    )

    qualified_pattern = r"""
        (?:
            \[([A-Za-z_][^\]]*)\]
            |
            ([A-Za-z_][A-Za-z0-9_$#@]*)
        )
        \s*\.\s*
        (?:
            \[([A-Za-z_][^\]]*)\]
            |
            ([A-Za-z_][A-Za-z0-9_$#@]*)
        )
    """

    references: set[str] = set()

    context_pattern = re.compile(
        rf"""
        \b
        (?:
            FROM
            |
            JOIN
            |
            UPDATE
            |
            INTO
            |
            USING
        )
        \s+
        (?P<object>{qualified_pattern})
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in context_pattern.finditer(cleaned_sql):
        qualified_text = match.group("object")

        qualified_match = re.fullmatch(
            qualified_pattern,
            qualified_text,
            flags=re.VERBOSE,
        )

        if not qualified_match:
            continue

        schema = qualified_match.group(1) or qualified_match.group(2)
        object_name = qualified_match.group(3) or qualified_match.group(4)

        if schema and object_name:
            references.add(f"{schema}.{object_name}")

    exec_pattern = re.compile(
        rf"""
        \bEXEC(?:UTE)?\s+
        (?P<object>{qualified_pattern})
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for match in exec_pattern.finditer(cleaned_sql):
        qualified_text = match.group("object")

        qualified_match = re.fullmatch(
            qualified_pattern,
            qualified_text,
            flags=re.VERBOSE,
        )

        if not qualified_match:
            continue

        schema = qualified_match.group(1) or qualified_match.group(2)
        object_name = qualified_match.group(3) or qualified_match.group(4)

        if schema and object_name:
            references.add(f"{schema}.{object_name}")

    return sorted(references)

def extract_stored_procedures(sql: str) -> list[str]:
    if not sql:
        return []

    pattern = r"""
        \bEXEC(?:UTE)?\s+
        (?:
            \[([A-Za-z_][^\]]*)\]
            |
            ([A-Za-z_][A-Za-z0-9_$#@]*)
        )
        \s*\.\s*
        (?:
            \[([A-Za-z_][^\]]*)\]
            |
            ([A-Za-z_][A-Za-z0-9_$#@]*)
        )
    """

    procedures: set[str] = set()

    for match in re.finditer(
        pattern,
        sql,
        flags=re.IGNORECASE | re.VERBOSE,
    ):
        schema = match.group(1) or match.group(2)
        procedure = match.group(3) or match.group(4)

        if schema and procedure:
            procedures.add(f"{schema}.{procedure}")

    return sorted(procedures)


def analyze_sql(sql: str | None) -> dict[str, Any]:
    if not sql:
        return {
            "has_sql": False,
            "features": [],
            "procedural_features": [],
            "relational_features": [],
            "referenced_objects": [],
            "stored_procedures": [],
            "classification": "UNKNOWN",
        }

    sql = str(sql)

    features = detect_features(sql)

    procedural_features = {
        "EXEC",
        "CURSOR",
        "WHILE",
        "TRY_CATCH",
        "TEMP_TABLE",
        "TABLE_VARIABLE",
        "DYNAMIC_SQL",
    }

    relational_features = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "JOIN",
        "GROUP_BY",
        "ORDER_BY",
        "WINDOW",
        "CASE",
        "CTE",
    }

    detected_procedural = [
        feature
        for feature in features
        if feature in procedural_features
    ]

    detected_relational = [
        feature
        for feature in features
        if feature in relational_features
    ]

    if detected_procedural:
        classification = "PROCEDURAL"
    elif detected_relational:
        classification = "RELATIONAL"
    else:
        classification = "UNKNOWN"

    return {
        "has_sql": True,
        "features": features,
        "procedural_features": detected_procedural,
        "relational_features": detected_relational,
        "referenced_objects": extract_referenced_objects(sql),
        "stored_procedures": extract_stored_procedures(sql),
        "classification": classification,
    }
