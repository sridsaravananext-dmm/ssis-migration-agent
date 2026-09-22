from __future__ import annotations

from collections import defaultdict
from typing import Any


SEQUENCE_TYPES = {
    "MICROSOFT.SEQUENCE",
    "STOCK:SEQUENCE",
}


LOOP_TYPES = {
    "MICROSOFT.FOREACHLOOP",
    "MICROSOFT.FORLOOP",
    "MICROSOFT.WHILELOOP",
}


def _get_ref_id(executable: dict[str, Any]) -> str | None:
    return executable.get("ref_id")


def _get_type(executable: dict[str, Any]) -> str:
    return (
        executable.get("executable_type")
        or executable.get("creation_name")
        or ""
    ).upper()


def _classify_executable(
    executable: dict[str, Any],
) -> str:
    executable_type = _get_type(executable)

    if executable_type in SEQUENCE_TYPES:
        return "SEQUENCE_CONTAINER"

    if executable_type in LOOP_TYPES:
        return "LOOP_CONTAINER"

    if executable_type.endswith(":SEQUENCE"):
        return "SEQUENCE_CONTAINER"

    if "FOREACH" in executable_type:
        return "LOOP_CONTAINER"

    if "FORLOOP" in executable_type:
        return "LOOP_CONTAINER"

    if "WHILELOOP" in executable_type:
        return "LOOP_CONTAINER"

    return "TASK"


def _build_nodes(
    executables: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}

    for executable in executables:
        ref_id = _get_ref_id(executable)

        if not ref_id:
            continue

        parts = ref_id.split("\\")

        parent_ref_id = None

        if len(parts) > 1:
            parent_ref_id = "\\".join(parts[:-1])

        node_type = _classify_executable(executable)

        nodes[ref_id] = {
            "ref_id": ref_id,
            "name": executable.get("name"),
            "executable_type": executable.get(
                "executable_type"
            ),
            "creation_name": executable.get(
                "creation_name"
            ),
            "description": executable.get(
                "description"
            ),
            "node_type": node_type,
            "parent_ref_id": parent_ref_id,
        }

    return nodes


def _build_hierarchy(
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:

    children: dict[str, list[str]] = defaultdict(list)

    for ref_id, node in nodes.items():
        parent = node["parent_ref_id"]

        if parent and parent in nodes:
            children[parent].append(ref_id)

    for parent in children:
        children[parent].sort()

    def build_node(ref_id: str) -> dict[str, Any]:
        node = nodes[ref_id]

        return {
            "ref_id": node["ref_id"],
            "name": node["name"],
            "type": node["node_type"],
            "executable_type": node["executable_type"],
            "children": [
                build_node(child)
                for child in children.get(ref_id, [])
            ],
        }

    # The package executable is intentionally not part
    # of package["executables"] in our IR, so find the
    # top-level executable nodes.
    roots = [
        ref_id
        for ref_id, node in nodes.items()
        if node["parent_ref_id"] is None
        or node["parent_ref_id"] not in nodes
    ]

    roots.sort()

    return {
        "roots": [
            build_node(root)
            for root in roots
        ]
    }


def _build_execution_edges(
    constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    edges = []

    for constraint in constraints:
        attributes = constraint.get(
            "attributes",
            {},
        )

        source = (
            constraint.get("from_executable")
            or attributes.get("From")
        )

        target = (
            constraint.get("to_executable")
            or attributes.get("To")
        )

        if not source or not target:
            continue

        edges.append(
            {
                "from": source,
                "to": target,
                "logical_and": attributes.get(
                    "LogicalAnd"
                ),
                "object_name": attributes.get(
                    "ObjectName"
                ),
            }
        )

    return edges


def _flatten_hierarchy(
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:

    result = []

    def visit(node: dict[str, Any]) -> None:
        result.append(
            {
                "ref_id": node["ref_id"],
                "name": node["name"],
                "type": node["type"],
                "executable_type": node[
                    "executable_type"
                ],
            }
        )

        for child in node.get("children", []):
            visit(child)

    for root in hierarchy.get("roots", []):
        visit(root)

    return result


def analyze_control_flow(
    package: dict[str, Any],
) -> dict[str, Any]:

    executables = package.get(
        "executables",
        [],
    )

    constraints = package.get(
        "precedence_constraints",
        [],
    )

    nodes = _build_nodes(executables)

    hierarchy = _build_hierarchy(nodes)

    execution_edges = _build_execution_edges(
        constraints
    )

    flattened = _flatten_hierarchy(
        hierarchy
    )

    containers = [
        node
        for node in flattened
        if node["type"]
        in {
            "SEQUENCE_CONTAINER",
            "LOOP_CONTAINER",
        }
    ]

    tasks = [
        node
        for node in flattened
        if node["type"] == "TASK"
    ]

    return {
        "root": "Package",

        "node_count": len(nodes),

        "task_count": len(tasks),

        "container_count": len(containers),

        "sequence_container_count": sum(
            1
            for node in containers
            if node["type"]
            == "SEQUENCE_CONTAINER"
        ),

        "loop_container_count": sum(
            1
            for node in containers
            if node["type"]
            == "LOOP_CONTAINER"
        ),

        "edge_count": len(execution_edges),

        "hierarchy": hierarchy,

        "containers": containers,

        "tasks": tasks,

        "execution_edges": execution_edges,
    }