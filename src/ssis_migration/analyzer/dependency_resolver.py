from __future__ import annotations

from typing import Any

from ssis_migration.database.ir import DatabaseIR


def resolve_database_dependencies(
    database_ir: DatabaseIR,
    root_objects: list[str],
) -> dict[str, Any]:

    # Build one unified index across every SQL object type.
    object_index: dict[str, Any] = {}

    for obj in database_ir.objects:
        key = f"{obj.schema}.{obj.name}".lower()
        object_index[key] = obj

    for table in database_ir.tables:
        key = f"{table.schema}.{table.name}".lower()
        object_index[key] = table

    # Normalize dependency graph keys.
    graph: dict[str, list[str]] = {}

    for source, targets in database_ir.dependency_graph.items():
        source_key = source.lower()
        graph[source_key] = [
            target.lower()
            for target in targets
        ]

    # Normalize roots.
    normalized_roots = [
        root.strip("[]").replace("].[", ".").lower()
        for root in root_objects
    ]

    visited: set[str] = set()
    queue: list[str] = list(normalized_roots)

    resolved_objects: list[dict[str, Any]] = []
    resolved_edges: list[dict[str, str]] = []

    while queue:

        current = queue.pop(0)

        if current in visited:
            continue

        visited.add(current)

        obj = object_index.get(current)

        if obj is not None:

            resolved_objects.append(
                {
                    "schema": obj.schema,
                    "name": obj.name,
                    "object_type": obj.object_type,
                    "definition": getattr(
                        obj,
                        "definition",
                        None,
                    ),
                    "parameters": getattr(
                        obj,
                        "parameters",
                        [],
                    ),
                    "columns": getattr(
                        obj,
                        "columns",
                        [],
                    ),
                    "primary_keys": getattr(
                        obj,
                        "primary_keys",
                        [],
                    ),
                    "foreign_keys": getattr(
                        obj,
                        "foreign_keys",
                        [],
                    ),
                    "metadata": getattr(
                        obj,
                        "metadata",
                        {},
                    ),
                }
            )

        for target in graph.get(current, []):

            resolved_edges.append(
                {
                    "source": current,
                    "target": target,
                }
            )

            if target not in visited:
                queue.append(target)

    return {
        "root_objects": root_objects,
        "resolved_object_count": len(resolved_objects),
        "objects": resolved_objects,
        "edges": resolved_edges,
    }