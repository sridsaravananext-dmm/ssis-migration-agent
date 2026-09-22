from __future__ import annotations

from typing import Any


def analyze_dependencies(package: dict[str, Any]) -> dict[str, Any]:
    constraints = package.get("precedence_constraints", [])

    nodes = set()
    edges = []

    for constraint in constraints:
        attributes = constraint.get("attributes", {})

        source = (
            constraint.get("from_executable")
            or attributes.get("From")
        )

        target = (
            constraint.get("to_executable")
            or attributes.get("To")
        )

        if source:
            nodes.add(source)

        if target:
            nodes.add(target)

        edges.append(
            {
                "from": source,
                "to": target,
                "attributes": attributes,
            }
        )

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": sorted(nodes),
        "edges": edges,
    }