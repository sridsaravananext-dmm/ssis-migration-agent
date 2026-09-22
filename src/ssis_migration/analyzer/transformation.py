from __future__ import annotations

from typing import Any


def analyze_component(
    component: dict[str, Any],
) -> dict[str, Any]:

    detected_type = (
        component.get("detected_type")
        or "UNKNOWN"
    )

    properties = component.get(
        "properties",
        {}
    )

    result = {
        "name": component.get("name"),
        "type": detected_type,
        "category": None,
        "properties": properties,
        "connections": component.get(
            "connection_manager_refs",
            []
        ),
        "inputs": len(
            component.get(
                "inputs",
                []
            )
        ),
        "outputs": len(
            component.get(
                "outputs",
                []
            )
        ),
    }

    source_types = {
        "OLE_DB_SOURCE",
        "FLAT_FILE_SOURCE",
    }

    destination_types = {
        "OLE_DB_DESTINATION",
        "FLAT_FILE_DESTINATION",
    }

    transformation_types = {
        "LOOKUP",
        "DERIVED_COLUMN",
        "CONDITIONAL_SPLIT",
        "AGGREGATE",
        "SORT",
        "MERGE",
        "MERGE_JOIN",
        "UNION_ALL",
        "DATA_CONVERSION",
        "MULTICAST",
        "SCRIPT",
    }

    if detected_type in source_types:
        result["category"] = "SOURCE"

    elif detected_type in destination_types:
        result["category"] = "DESTINATION"

    elif detected_type in transformation_types:
        result["category"] = "TRANSFORMATION"

    else:
        result["category"] = "UNKNOWN"

    return result