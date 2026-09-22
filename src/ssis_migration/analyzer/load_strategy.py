from __future__ import annotations

from typing import Any


def _collect_sql(properties: dict[str, Any]) -> list[str]:
    sql_values = []

    for key in ("SqlCommand", "SqlCommandVariable"):
        value = properties.get(key)

        if value:
            sql_values.append(str(value))

    return sql_values


def _has_cutoff_parameter(properties: dict[str, Any]) -> bool:
    parameter_mapping = str(properties.get("ParameterMapping", "")).upper()

    return (
        "LASTCUTOFF" in parameter_mapping
        or "NEWCUTOFF" in parameter_mapping
        or "CUTOFF" in parameter_mapping
    )


def detect_load_strategy(data_flow: dict[str, Any]) -> dict[str, Any]:
    sources = data_flow.get("sources", [])
    destinations = data_flow.get("destinations", [])

    result = {
        "strategy": "UNKNOWN",
        "confidence": "LOW",
        "evidence": [],
    }

    source_sql = []
    destination_sql = []

    # ---------------------------------------
    # Collect source evidence
    # ---------------------------------------

    for source in sources:
        properties = source.get("properties", {})

        for sql in _collect_sql(properties):
            source_sql.append(sql)

            result["evidence"].append(
                {
                    "source": "SOURCE_SQL",
                    "component": source.get("name"),
                    "value": sql,
                }
            )

        if _has_cutoff_parameter(properties):
            result["evidence"].append(
                {
                    "source": "PARAMETER_MAPPING",
                    "component": source.get("name"),
                    "value": properties.get("ParameterMapping"),
                }
            )

    # ---------------------------------------
    # Collect destination evidence
    # ---------------------------------------

    for destination in destinations:
        properties = destination.get("properties", {})

        for sql in _collect_sql(properties):
            destination_sql.append(sql)

            result["evidence"].append(
                {
                    "source": "DESTINATION_SQL",
                    "component": destination.get("name"),
                    "value": sql,
                }
            )

    combined_sql = " ".join(
        source_sql + destination_sql
    ).upper()

    # ---------------------------------------
    # Detect explicit load semantics
    # ---------------------------------------

    has_merge = "MERGE " in combined_sql
    has_truncate = "TRUNCATE TABLE" in combined_sql
    has_delete = "DELETE FROM" in combined_sql
    has_cutoff_parameter = any(
        _has_cutoff_parameter(
            source.get("properties", {})
        )
        for source in sources
    )

    # ---------------------------------------
    # Strategy classification
    # ---------------------------------------

    if has_merge:
        result["strategy"] = "MERGE"
        result["confidence"] = "HIGH"

    elif has_truncate:
        result["strategy"] = "FULL_RELOAD"
        result["confidence"] = "HIGH"

    elif has_delete:
        result["strategy"] = "DELETE_AND_RELOAD"
        result["confidence"] = "HIGH"

    elif has_cutoff_parameter:
        result["strategy"] = "INCREMENTAL"
        result["confidence"] = "HIGH"

    elif source_sql:
        result["strategy"] = "EXTRACT_LOAD"
        result["confidence"] = "MEDIUM"

    return result