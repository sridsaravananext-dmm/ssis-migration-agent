from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ssis_migration.analyzer.sql_dependency import (
    analyze_sql_dependency,
)

from ssis_migration.analyzer.sql_semantic import (
    analyze_sql,
)

from ssis_migration.ir.models import PackageIR


def extract_ssis_sql_dependencies(
    package_ir: PackageIR,
) -> dict[str, Any]:

    stored_procedures: set[str] = set()
    database_objects: set[str] = set()

    procedure_connections: dict[str, set[str]] = {}
    object_connections: dict[str, set[str]] = {}

    # =========================================================
    # DATA FLOW SQL DEPENDENCIES
    # =========================================================

    for data_flow in package_ir.data_flows:

        for component in data_flow.components:

            component_dict = asdict(component)

            result = analyze_sql_dependency(
                component_dict
            )

            connection_refs = {
                ref.connection_manager_ref_id
                for ref in component.connection_manager_refs
                if ref.connection_manager_ref_id
            }

            for procedure in result.get(
                "stored_procedures",
                [],
            ):

                stored_procedures.add(procedure)

                procedure_connections.setdefault(
                    procedure,
                    set(),
                ).update(connection_refs)

            target_object = result.get(
                "target_object"
            )

            if target_object:

                database_objects.add(
                    target_object
                )

                object_connections.setdefault(
                    target_object,
                    set(),
                ).update(connection_refs)

            for obj in result.get(
                "objects",
                [],
            ):

                # Objects may be dicts (from analyze_sql_dependency);
                # convert to a hashable string key.
                obj_key = (
                    f"{obj.get('schema', '')}.{obj.get('name', '')}"
                    if isinstance(obj, dict)
                    else str(obj)
                )

                database_objects.add(obj_key)

                object_connections.setdefault(
                    obj_key,
                    set(),
                ).update(connection_refs)

    # =========================================================
    # EXECUTE SQL TASK DEPENDENCIES
    # =========================================================

    for executable in package_ir.executables:

        if executable.executable_type != (
            "Microsoft.ExecuteSQLTask"
        ):
            continue

        properties = executable.properties or {}

        sql_statement = properties.get(
            "SqlStatementSource"
        )

        if not sql_statement:
            continue

        semantic = analyze_sql(
            sql_statement
        )

        connection_ref = properties.get(
            "connection_manager_ref_id"
        )

        if not connection_ref:
            connection_ref = properties.get(
                "Connection"
            )

        connection_refs = set()

        if connection_ref:
            connection_refs.add(
                connection_ref
            )

        for procedure in semantic.get(
            "stored_procedures",
            [],
        ):

            stored_procedures.add(
                procedure
            )

            procedure_connections.setdefault(
                procedure,
                set(),
            ).update(
                connection_refs
            )

        for obj in semantic.get(
            "referenced_objects",
            [],
        ):

            database_objects.add(
                obj
            )

            object_connections.setdefault(
                obj,
                set(),
            ).update(
                connection_refs
            )

    return {
        "stored_procedures": sorted(
            stored_procedures
        ),
        "database_objects": sorted(
            database_objects
        ),
        "stored_procedure_count": len(
            stored_procedures
        ),
        "database_object_count": len(
            database_objects
        ),
        "procedure_connections": {
            procedure: sorted(
                connections
            )
            for procedure, connections
            in procedure_connections.items()
        },
        "object_connections": {
            obj: sorted(
                connections
            )
            for obj, connections
            in object_connections.items()
        },
    }