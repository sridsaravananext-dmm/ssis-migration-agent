from __future__ import annotations

from typing import Any

from ssis_migration.database.ir import DatabaseIR
from ssis_migration.analyzer.dependency_resolver import (
    resolve_database_dependencies,
)


def build_relevant_database_context(
    database_ir: DatabaseIR,
    ssis_dependencies: dict[str, Any],
    connection_reference: str | None = None,
) -> dict[str, Any]:

    root_objects: list[str] = []

    procedure_connections = ssis_dependencies.get(
        "procedure_connections",
        {},
    )

    for procedure, connections in procedure_connections.items():

        if (
            connection_reference is None
            or connection_reference in connections
        ):
            root_objects.append(procedure)

    object_connections = ssis_dependencies.get(
        "object_connections",
        {},
    )

    for database_object, connections in object_connections.items():

        if (
            connection_reference is None
            or connection_reference in connections
        ):

            normalized = (
                database_object
                .strip("[]")
                .replace("].[", ".")
            )

            if normalized not in root_objects:
                root_objects.append(normalized)

    resolved = resolve_database_dependencies(
        database_ir,
        root_objects,
    )

    return {
        "database": database_ir.database,
        "connection_reference": connection_reference,
        "root_objects": root_objects,
        "resolved_object_count": (
            resolved["resolved_object_count"]
        ),
        "objects": resolved["objects"],
        "edges": resolved["edges"],
    }