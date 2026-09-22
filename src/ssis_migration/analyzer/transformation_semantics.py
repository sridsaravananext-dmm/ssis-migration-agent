from __future__ import annotations

from typing import Any
from .sql_dependency import analyze_sql_dependency
from .result_set import analyze_result_set


SOURCE_TYPES = {
    "OLE_DB_SOURCE",
    "FLAT_FILE_SOURCE",
    "ADO_NET_SOURCE",
    "EXCEL_SOURCE",
    "RAW_FILE_SOURCE",
    "ODBC_SOURCE",
}

DESTINATION_TYPES = {
    "OLE_DB_DESTINATION",
    "FLAT_FILE_DESTINATION",
    "ADO_NET_DESTINATION",
    "EXCEL_DESTINATION",
    "RAW_FILE_DESTINATION",
    "ODBC_DESTINATION",
}

TRANSFORMATION_TYPES = {
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


def _properties(component: dict[str, Any]) -> dict[str, Any]:
    return component.get("properties") or {}


def _inputs(component: dict[str, Any]) -> list[dict[str, Any]]:
    return component.get("inputs") or []


def _outputs(component: dict[str, Any]) -> list[dict[str, Any]]:
    return component.get("outputs") or []


def _connections(component: dict[str, Any]) -> list[dict[str, Any]]:
    return component.get("connections") or []


def _property_evidence(component: dict[str, Any]) -> dict[str, Any]:
    """
    Preserve the original component properties as deterministic evidence.

    No business semantics are inferred here.
    """
    return {
        "properties": _properties(component),
        "connections": _connections(component),
    }


def _base_result(
    component: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    return {
        "component": component.get("name"),
        "type": component.get("detected_type"),
        "semantics": {
            "operation": operation,
        },
        "evidence": _property_evidence(component),
    }


def _analyze_source(component: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(
        component,
        "SOURCE",
    )

    properties = _properties(component)

    result["semantics"].update(
        {
            "access_mode": properties.get("AccessMode"),
            "sql_command": properties.get("SqlCommand"),
            "sql_command_variable": properties.get(
                "SqlCommandVariable"
            ),
            "open_rowset": properties.get("OpenRowset"),
            "parameter_mapping": properties.get(
                "ParameterMapping"
            ),
            "sql_dependencies": analyze_sql_dependency(
                component
            ),
            "result_set": analyze_result_set(component),
        }
    )

    return result

def _analyze_destination(
    component: dict[str, Any],
) -> dict[str, Any]:
    result = _base_result(
        component,
        "DESTINATION",
    )

    properties = _properties(component)

    result["semantics"].update(
        {
            "access_mode": properties.get("AccessMode"),
            "open_rowset": properties.get("OpenRowset"),
            "open_rowset_variable": properties.get(
                "OpenRowsetVariable"
            ),
            "fast_load_options": {
                key: value
                for key, value in properties.items()
                if key.lower().startswith("fastload")
            },
            "sql_dependencies": analyze_sql_dependency(
                component
            ),
        }
    )

    return result

def _analyze_lookup(component: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(
        component,
        "LOOKUP",
    )

    properties = _properties(component)

    result["semantics"].update(
        {
            "reference_connection": None,
            "reference_table": None,
            "key_mappings": {},
            "no_match_behavior": None,
            "cache_mode": None,
        }
    )

    # Preserve only facts explicitly present in the XML.
    result["semantics"]["reference_connection"] = (
        properties.get("Connection")
        or properties.get("ConnectionManager")
        or properties.get("ConnectionManagerID")
    )

    result["semantics"]["reference_table"] = (
        properties.get("Table")
        or properties.get("OpenRowset")
        or properties.get("SqlCommand")
    )

    result["semantics"]["cache_mode"] = properties.get("CacheMode")

    return result


def _analyze_derived_column(component: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(
        component,
        "DERIVED_COLUMN",
    )

    result["semantics"].update(
        {
            "expressions": {},
        }
    )

    # Expressions may be represented in component properties,
    # inputs, outputs, or custom XML depending on SSIS version/component.
    # Do not invent expressions when they are not explicitly available.
    properties = _properties(component)

    for key, value in properties.items():
        key_upper = str(key).upper()

        if "EXPRESSION" in key_upper:
            result["semantics"]["expressions"][key] = value

    return result


def _analyze_conditional_split(component: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(
        component,
        "CONDITIONAL_SPLIT",
    )

    result["semantics"].update(
        {
            "conditions": {},
        }
    )

    properties = _properties(component)

    for key, value in properties.items():
        key_upper = str(key).upper()

        if (
            "EXPRESSION" in key_upper
            or "CONDITION" in key_upper
            or "OUTPUT" in key_upper
        ):
            result["semantics"]["conditions"][key] = value

    return result


def _analyze_aggregate(component: dict[str, Any]) -> dict[str, Any]:
    result = _base_result(
        component,
        "AGGREGATE",
    )

    result["semantics"].update(
        {
            "group_by": [],
            "aggregations": {},
        }
    )

    properties = _properties(component)

    for key, value in properties.items():
        key_upper = str(key).upper()

        if "GROUP" in key_upper:
            result["semantics"]["group_by"].append(
                {
                    "property": key,
                    "value": value,
                }
            )

        elif (
            "AGGREGATE" in key_upper
            or "OPERATION" in key_upper
        ):
            result["semantics"]["aggregations"][key] = value

    return result


def _analyze_generic_transformation(
    component: dict[str, Any],
) -> dict[str, Any]:
    detected_type = str(
        component.get("detected_type") or "UNKNOWN"
    ).upper()

    result = _base_result(
        component,
        detected_type,
    )

    result["semantics"].update(
        {
            "input_count": len(_inputs(component)),
            "output_count": len(_outputs(component)),
        }
    )

    return result


def analyze_transformation_semantics(
    component: dict[str, Any],
) -> dict[str, Any]:
    """
    Deterministically extract transformation semantics from the IR.

    Important:
    - No LLM inference.
    - No business-domain assumptions.
    - Missing XML information remains None / empty / UNKNOWN.
    """

    detected_type = str(
        component.get("detected_type") or "UNKNOWN"
    ).upper()

    if detected_type in SOURCE_TYPES:
        return _analyze_source(component)

    if detected_type in DESTINATION_TYPES:
        return _analyze_destination(component)

    if detected_type == "LOOKUP":
        return _analyze_lookup(component)

    if detected_type == "DERIVED_COLUMN":
        return _analyze_derived_column(component)

    if detected_type == "CONDITIONAL_SPLIT":
        return _analyze_conditional_split(component)

    if detected_type == "AGGREGATE":
        return _analyze_aggregate(component)

    if detected_type in TRANSFORMATION_TYPES:
        return _analyze_generic_transformation(component)

    return _base_result(
        component,
        "UNKNOWN",
    )