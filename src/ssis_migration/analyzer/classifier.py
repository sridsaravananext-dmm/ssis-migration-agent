from __future__ import annotations

from typing import Any


COMPONENT_TYPES = {
    "OLE_DB_SOURCE": "SOURCE",
    "OLE_DB_DESTINATION": "DESTINATION",
    "FLAT_FILE_SOURCE": "SOURCE",
    "FLAT_FILE_DESTINATION": "DESTINATION",
    "LOOKUP": "TRANSFORMATION",
    "DERIVED_COLUMN": "TRANSFORMATION",
    "CONDITIONAL_SPLIT": "TRANSFORMATION",
    "AGGREGATE": "TRANSFORMATION",
    "SORT": "TRANSFORMATION",
    "MERGE": "TRANSFORMATION",
    "MERGE_JOIN": "TRANSFORMATION",
    "UNION_ALL": "TRANSFORMATION",
    "DATA_CONVERSION": "TRANSFORMATION",
    "MULTICAST": "TRANSFORMATION",
    "SCRIPT": "TRANSFORMATION",
}


def classify_component(
    component: dict[str, Any],
) -> str:

    detected_type = (
        component.get("detected_type")
        or ""
    ).upper()

    return COMPONENT_TYPES.get(
        detected_type,
        "UNKNOWN",
    )