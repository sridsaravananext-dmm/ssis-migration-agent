from __future__ import annotations

from typing import Any

from ssis_migration.analyzer.classifier import COMPONENT_TYPES


def build_data_flow_lineage(data_flow: dict[str, Any]) -> dict[str, Any]:
    components = data_flow.get("components", [])
    paths = data_flow.get("paths", [])
    column_mappings = data_flow.get("column_mappings", [])

    nodes = []
    component_roles: dict[str, str] = {}

    for component in components:
        name = component.get("name")
        if not name:
            continue

        component_type = (
            component.get("component_type")
            or component.get("type")
            or component.get("detected_type")
        )

        role = COMPONENT_TYPES.get(
            str(component_type).upper(),
            "UNKNOWN",
        )

        component_roles[name] = role

        nodes.append(
            {
                "component": name,
                "component_type": component_type,
                "role": role,
            }
        )

    edges = []

    for path in paths:
        start_id = path.get("start_id")
        end_id = path.get("end_id")

        source_component = _component_from_ref(
            start_id,
            component_roles,
        )
        destination_component = _component_from_ref(
            end_id,
            component_roles,
        )

        if not source_component or not destination_component:
            continue

        edges.append(
            {
                "from": source_component,
                "to": destination_component,
                "path": path.get("name"),
                "start_id": start_id,
                "end_id": end_id,
            }
        )

    column_lineage = []

    for mapping in column_mappings:
        destination_component = mapping.get("destination_component")
        source_lineage_id = mapping.get("source_lineage_id")
        destination_column = mapping.get("destination_column")

        if not destination_component or not source_lineage_id:
            continue

        source_component, source_column = _parse_column_ref(
            source_lineage_id
        )

        if not source_component or not source_column:
            continue

        column_lineage.append(
            {
                "source_component": source_component,
                "source_column": source_column,
                "destination_component": destination_component,
                "destination_column": destination_column,
                "lineage_type": "DIRECT",
                "source_lineage_id": source_lineage_id,
                "destination_column_ref": mapping.get(
                    "destination_column_ref"
                ),
            }
        )

    return {
        "data_flow": data_flow.get("name"),
        "lineage": {
            "nodes": nodes,
            "edges": edges,
            "column_lineage": column_lineage,
        },
    }


def _component_from_ref(
    ref_id: str | None,
    component_roles: dict[str, str],
) -> str | None:
    if not ref_id:
        return None

    candidates = sorted(
        component_roles,
        key=len,
        reverse=True,
    )

    for component in candidates:
        if component in ref_id:
            return component

    return None


def _parse_column_ref(
    ref_id: str,
) -> tuple[str | None, str | None]:
    marker = ".Columns["

    if marker not in ref_id:
        return None, None

    component_path, column_part = ref_id.split(
        marker,
        1,
    )

    column = column_part.rstrip("]")

    components = component_path.split("\\")

    if not components:
        return None, None

    component = components[-1]

    output_marker = ".Outputs["

    if output_marker in component:
        component = component.split(
            output_marker,
            1,
        )[0]

    return component, column
