from __future__ import annotations

import re

from .models import TargetLanguageDecision


PROCEDURAL_PATTERNS = {
    "CURSOR": r"\bCURSOR\b",
    "WHILE": r"\bWHILE\b",
    "LOOP": r"\bLOOP\b",
    "TRY_CATCH": r"\bBEGIN\s+TRY\b",
    "DYNAMIC_SQL": r"\bEXEC(?:UTE)?\s*\(",
    "TEMP_TABLE": r"(#\w+)",
}

SQL_PATTERNS = {
    "SELECT": r"\bSELECT\b",
    "JOIN": r"\bJOIN\b",
    "GROUP_BY": r"\bGROUP\s+BY\b",
    "WINDOW": r"\bOVER\s*\(",
    "CASE": r"\bCASE\b",
    "MERGE": r"\bMERGE\b",
    "CTE": r"\bWITH\s+\w+\s+AS\s*\(",
}


def _detect_patterns(
    sql: str,
    patterns: dict[str, str],
) -> list[str]:

    detected = []

    for name, pattern in patterns.items():
        if re.search(
            pattern,
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            detected.append(name)

    return detected


def select_databricks_language(
    sql: str | None,
    *,
    target_platform: str = "databricks",
) -> TargetLanguageDecision:

    if not sql:
        return TargetLanguageDecision(
            target_platform=target_platform,
            language="unknown",
            ai_required=True,
            confidence="low",
            reasons=[
                "No source SQL definition available"
            ],
        )

    sql = str(sql)

    procedural_features = _detect_patterns(
        sql,
        PROCEDURAL_PATTERNS,
    )

    relational_features = _detect_patterns(
        sql,
        SQL_PATTERNS,
    )

    if procedural_features:
        return TargetLanguageDecision(
            target_platform=target_platform,
            language="pyspark",
            ai_required=True,
            confidence="medium",
            reasons=[
                "Procedural SQL constructs detected"
            ],
            unsupported_features=procedural_features,
            evidence={
                "procedural_features": procedural_features,
                "relational_features": relational_features,
            },
        )

    if relational_features:
        return TargetLanguageDecision(
            target_platform=target_platform,
            language="spark_sql",
            ai_required=False,
            confidence="high",
            reasons=[
                "Relational SQL constructs detected",
                "No procedural constructs detected",
            ],
            evidence={
                "procedural_features": [],
                "relational_features": relational_features,
            },
        )

    return TargetLanguageDecision(
        target_platform=target_platform,
        language="spark_sql",
        ai_required=False,
        confidence="medium",
        reasons=[
            "SQL definition contains no detected "
            "procedural constructs"
        ],
        evidence={
            "procedural_features": [],
            "relational_features": [],
        },
    )
