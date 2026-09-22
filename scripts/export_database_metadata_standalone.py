from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyodbc


def get_connection(server: str, database: str):
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )

    return pyodbc.connect(connection_string)


def extract_database(cursor):
    cursor.execute(
        """
        SELECT
            DB_NAME() AS database_name,
            DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS collation,
            DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model
        """
    )

    row = cursor.fetchone()

    return {
        "name": row.database_name,
        "collation": row.collation,
        "recovery_model": row.recovery_model,
    }


def extract_schemas(cursor):
    cursor.execute(
        """
        SELECT name
        FROM sys.schemas
        ORDER BY name
        """
    )

    return [row.name for row in cursor.fetchall()]


def extract_tables(cursor):
    cursor.execute(
        """
        SELECT
            s.name AS schema_name,
            t.name AS table_name,
            c.name AS column_name,
            ty.name AS data_type,
            c.max_length,
            c.precision,
            c.scale,
            c.is_nullable,
            c.column_id
        FROM sys.tables t
        INNER JOIN sys.schemas s
            ON t.schema_id = s.schema_id
        INNER JOIN sys.columns c
            ON t.object_id = c.object_id
        INNER JOIN sys.types ty
            ON c.user_type_id = ty.user_type_id
        WHERE t.is_ms_shipped = 0
        ORDER BY
            s.name,
            t.name,
            c.column_id
        """
    )

    tables = {}

    for row in cursor.fetchall():
        key = (row.schema_name, row.table_name)

        if key not in tables:
            tables[key] = {
                "schema": row.schema_name,
                "name": row.table_name,
                "columns": [],
            }

        tables[key]["columns"].append(
            {
                "schema": row.schema_name,
                "table": row.table_name,
                "name": row.column_name,
                "data_type": row.data_type,
                "max_length": row.max_length,
                "precision": row.precision,
                "scale": row.scale,
                "nullable": bool(row.is_nullable),
                "ordinal": row.column_id,
            }
        )

    return list(tables.values())


def extract_views(cursor):
    cursor.execute(
        """
        SELECT
            s.name AS schema_name,
            v.name AS view_name,
            OBJECT_DEFINITION(v.object_id) AS definition
        FROM sys.views v
        INNER JOIN sys.schemas s
            ON v.schema_id = s.schema_id
        WHERE v.is_ms_shipped = 0
        ORDER BY
            s.name,
            v.name
        """
    )

    return [
        {
            "schema": row.schema_name,
            "name": row.view_name,
            "definition": row.definition,
        }
        for row in cursor.fetchall()
    ]


def extract_procedure_parameters(cursor, object_id):
    cursor.execute(
        """
        SELECT
            parameter_id,
            name,
            TYPE_NAME(user_type_id) AS data_type,
            max_length,
            precision,
            scale,
            is_output
        FROM sys.parameters
        WHERE object_id = ?
        ORDER BY parameter_id
        """,
        object_id,
    )

    return [
        {
            "parameter_id": row.parameter_id,
            "name": row.name,
            "data_type": row.data_type,
            "max_length": row.max_length,
            "precision": row.precision,
            "scale": row.scale,
            "is_output": bool(row.is_output),
        }
        for row in cursor.fetchall()
    ]


def extract_procedure_dependencies(
    cursor,
    schema: str,
    procedure: str,
):
    object_name = f"{schema}.{procedure}"

    try:
        cursor.execute(
            """
            SELECT
                referenced_schema_name,
                referenced_entity_name,
                referenced_id,
                referenced_class_desc
            FROM sys.dm_sql_referenced_entities(
                ?,
                'OBJECT'
            )
            WHERE referenced_entity_name IS NOT NULL
            """,
            object_name,
        )

        dependencies = []

        for row in cursor.fetchall():
            dependencies.append(
                {
                    "schema": row.referenced_schema_name,
                    "name": row.referenced_entity_name,
                    "object_id": row.referenced_id,
                    "type": row.referenced_class_desc,
                }
            )

        return dependencies, None

    except Exception as exc:
        return [], str(exc)


def extract_procedures(cursor):
    cursor.execute(
        """
        SELECT
            s.name AS schema_name,
            p.name AS procedure_name,
            p.object_id,
            p.create_date,
            p.modify_date,
            m.definition
        FROM sys.procedures AS p
        INNER JOIN sys.schemas AS s
            ON p.schema_id = s.schema_id
        LEFT JOIN sys.sql_modules AS m
            ON p.object_id = m.object_id
        WHERE p.is_ms_shipped = 0
        ORDER BY
            s.name,
            p.name
        """
    )

    procedures = []

    for row in cursor.fetchall():
        parameters = extract_procedure_parameters(
            cursor,
            row.object_id,
        )

        dependencies, dependency_error = extract_procedure_dependencies(
            cursor,
            row.schema_name,
            row.procedure_name,
        )

        procedure = {
            "schema": row.schema_name,
            "name": row.procedure_name,
            "definition": row.definition,
            "parameters": parameters,
            "dependencies": dependencies,
            "create_date": (
                row.create_date.isoformat()
                if row.create_date
                else None
            ),
            "modify_date": (
                row.modify_date.isoformat()
                if row.modify_date
                else None
            ),
            "encrypted": row.definition is None,
        }

        if dependency_error:
            procedure["dependency_extraction_error"] = dependency_error

        procedures.append(procedure)

    return procedures


def extract_functions(cursor):
    cursor.execute(
        """
        SELECT
            s.name AS schema_name,
            o.name AS function_name,
            o.type_desc AS function_type,
            OBJECT_DEFINITION(o.object_id) AS definition
        FROM sys.objects o
        INNER JOIN sys.schemas s
            ON o.schema_id = s.schema_id
        WHERE
            o.type IN ('FN', 'IF', 'TF')
            AND o.is_ms_shipped = 0
        ORDER BY
            s.name,
            o.name
        """
    )

    return [
        {
            "schema": row.schema_name,
            "name": row.function_name,
            "function_type": row.function_type,
            "definition": row.definition,
            "parameters": [],
        }
        for row in cursor.fetchall()
    ]


def extract_dependencies(cursor):
    cursor.execute(
        """
        SELECT
            OBJECT_SCHEMA_NAME(d.referencing_id)
                AS referencing_schema,
            OBJECT_NAME(d.referencing_id)
                AS referencing_object,
            d.referenced_schema_name
                AS referenced_schema,
            d.referenced_entity_name
                AS referenced_object,
            o.type_desc
                AS referenced_type
        FROM sys.sql_expression_dependencies d
        LEFT JOIN sys.objects o
            ON d.referenced_id = o.object_id
        WHERE d.referencing_id IS NOT NULL
        ORDER BY
            referencing_schema,
            referencing_object,
            referenced_schema,
            referenced_object
        """
    )

    return [
        {
            "referencing_schema": row.referencing_schema,
            "referencing_object": row.referencing_object,
            "referenced_schema": row.referenced_schema,
            "referenced_object": row.referenced_object,
            "referenced_type": row.referenced_type,
        }
        for row in cursor.fetchall()
    ]


def extract_metadata(server: str, database: str):
    connection = get_connection(server, database)

    try:
        cursor = connection.cursor()

        metadata = {
            "database": extract_database(cursor),
            "schemas": extract_schemas(cursor),
            "tables": extract_tables(cursor),
            "views": extract_views(cursor),
            "procedures": extract_procedures(cursor),
            "functions": extract_functions(cursor),
            "dependencies": extract_dependencies(cursor),
        }

        return metadata

    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(
        description="Export SQL Server metadata to portable JSON."
    )

    parser.add_argument(
        "--server",
        required=True,
    )

    parser.add_argument(
        "--database",
        required=True,
    )

    parser.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    print(f"Connecting to: {args.server}")
    print(f"Database:      {args.database}")

    metadata = extract_metadata(
        server=args.server,
        database=args.database,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            metadata,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("Metadata extraction completed.")
    print(f"Stored procedures: {len(metadata['procedures'])}")
    print(f"Tables:            {len(metadata['tables'])}")
    print(f"Views:             {len(metadata['views'])}")
    print(f"Functions:         {len(metadata['functions'])}")
    print(f"Dependencies:      {len(metadata['dependencies'])}")
    print(f"Output:            {output_path}")


if __name__ == "__main__":
    main()