from __future__ import annotations

from .ir import DatabaseIR, SQLObjectIR, SQLTableIR
from .models import DatabaseMetadata


def build_database_ir(metadata: DatabaseMetadata) -> DatabaseIR:

    database_ir = DatabaseIR(
        database=metadata.database,
        schemas=metadata.schemas,
    )

    # Tables
    for table in metadata.tables:

        table_ir = SQLTableIR(
            schema=table.schema,
            name=table.name,
            object_type="TABLE",
            columns=[
                {
                    "name": column.name,
                    "data_type": column.data_type,
                    "max_length": column.max_length,
                    "precision": column.precision,
                    "scale": column.scale,
                    "nullable": column.nullable,
                    "ordinal": column.ordinal,
                }
                for column in table.columns
            ],
        )

        database_ir.tables.append(table_ir)

    # Views
    for view in metadata.views:

        database_ir.objects.append(
            SQLObjectIR(
                schema=view.schema,
                name=view.name,
                object_type="VIEW",
                definition=view.definition,
            )
        )

    # Stored procedures
    for procedure in metadata.procedures:

        dependencies = []

        for dependency in procedure.dependencies:
            schema = dependency.get("schema")
            name = dependency.get("name")
            object_id = dependency.get("object_id")

            # Ignore unresolved SQL aliases / expressions returned by
            # SQL Server dependency metadata.
            if not object_id or not name:
                continue

            if schema:
                dependencies.append(f"{schema}.{name}")
            else:
                dependencies.append(name)

        database_ir.objects.append(
            SQLObjectIR(
                schema=procedure.schema,
                name=procedure.name,
                object_type="PROCEDURE",
                definition=procedure.definition,
                parameters=procedure.parameters,
                dependencies=dependencies,
                metadata={
                    "create_date": procedure.create_date,
                    "modify_date": procedure.modify_date,
                    "encrypted": procedure.encrypted,
                },
            )
        )

    # Functions
    for function in metadata.functions:

        database_ir.objects.append(
            SQLObjectIR(
                schema=function.schema,
                name=function.name,
                object_type="FUNCTION",
                definition=function.definition,
                parameters=function.parameters,
                metadata={
                    "function_type": function.function_type,
                },
            )
        )

    # Dependencies
    for dependency in metadata.dependencies:
        source_schema = dependency.referencing_schema
        source_name = dependency.referencing_object

        target_schema = dependency.referenced_schema
        target_name = dependency.referenced_object
        target_type = dependency.referenced_type

        # Ignore unresolved SQL aliases / expressions.
        # SQL Server can return entries such as:
        #   schema = NULL
        #   object_id = NULL
        #   referenced_object = "cc"
        #
        # These are not database objects.
        if not target_schema or not target_type:
            continue

        source = f"{source_schema}.{source_name}"
        target = f"{target_schema}.{target_name}"

        database_ir.dependency_graph.setdefault(
            source,
            []
        ).append(target)

    return database_ir