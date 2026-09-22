from __future__ import annotations

from typing import Any

from ..models import (
    DatabaseColumn,
    DatabaseDependency,
    DatabaseFunction,
    DatabaseMetadata,
    DatabaseProcedure,
    DatabaseTable,
    DatabaseView,
)

from .procedure_dependencies import (
    extract_procedure_dependencies,
)

from .connection import create_connection


class SQLServerMetadataExtractor:

    def __init__(
        self,
        server: str,
        database: str,
        username: str | None = None,
        password: str | None = None,
        trusted_connection: bool = True,
    ):
        self.server = server
        self.database = database
        self.username = username
        self.password = password
        self.trusted_connection = trusted_connection

    def extract(self) -> DatabaseMetadata:

        connection = create_connection(
            server=self.server,
            database=self.database,
            username=self.username,
            password=self.password,
            trusted_connection=self.trusted_connection,
        )

        try:
            metadata = DatabaseMetadata()

            metadata.database = self._extract_database(connection)
            metadata.schemas = self._extract_schemas(connection)
            metadata.tables = self._extract_tables(connection)
            metadata.views = self._extract_views(connection)
            metadata.procedures = self._extract_procedures(connection)
            metadata.functions = self._extract_functions(connection)
            metadata.dependencies = self._extract_dependencies(connection)

            return metadata

        finally:
            connection.close()

    # ---------------------------------------------------------
    # DATABASE
    # ---------------------------------------------------------

    def _extract_database(self, connection) -> dict[str, Any]:

        cursor = connection.cursor()

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

    # ---------------------------------------------------------
    # SCHEMAS
    # ---------------------------------------------------------

    def _extract_schemas(self, connection) -> list[str]:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT name
            FROM sys.schemas
            ORDER BY name
            """
        )

        return [row.name for row in cursor.fetchall()]

    # ---------------------------------------------------------
    # TABLES + COLUMNS
    # ---------------------------------------------------------

    def _extract_tables(self, connection) -> list[DatabaseTable]:

        cursor = connection.cursor()

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

        tables: dict[tuple[str, str], DatabaseTable] = {}

        for row in cursor.fetchall():

            key = (
                row.schema_name,
                row.table_name,
            )

            if key not in tables:
                tables[key] = DatabaseTable(
                    schema=row.schema_name,
                    name=row.table_name,
                )

            tables[key].columns.append(
                DatabaseColumn(
                    schema=row.schema_name,
                    table=row.table_name,
                    name=row.column_name,
                    data_type=row.data_type,
                    max_length=row.max_length,
                    precision=row.precision,
                    scale=row.scale,
                    nullable=bool(row.is_nullable),
                    ordinal=row.column_id,
                )
            )

        return list(tables.values())

    # ---------------------------------------------------------
    # VIEWS
    # ---------------------------------------------------------

    def _extract_views(self, connection) -> list[DatabaseView]:

        cursor = connection.cursor()

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
            DatabaseView(
                schema=row.schema_name,
                name=row.view_name,
                definition=row.definition,
            )
            for row in cursor.fetchall()
        ]

    # ---------------------------------------------------------
    # STORED PROCEDURES
    # ---------------------------------------------------------

    def _extract_procedures(
    self,
    connection,
    ) -> list[DatabaseProcedure]:

        cursor = connection.cursor()

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

            parameters = self._extract_procedure_parameters(
                connection,
                row.object_id,
            )

            dependencies = extract_procedure_dependencies(
                connection,
                row.schema_name,
                row.procedure_name,
            )

            procedures.append(
                DatabaseProcedure(
                    schema=row.schema_name,
                    name=row.procedure_name,
                    definition=row.definition,
                    parameters=parameters,
                    dependencies=dependencies,
                    create_date=(
                        row.create_date.isoformat()
                        if row.create_date
                        else None
                    ),
                    modify_date=(
                        row.modify_date.isoformat()
                        if row.modify_date
                        else None
                    ),
                    encrypted=row.definition is None,
                )
            )

        return procedures
    
    def _extract_procedure_parameters(
    self,
    connection,
    object_id: int,
    ) -> list[dict[str, Any]]:

        cursor = connection.cursor()

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
    # ---------------------------------------------------------
    # FUNCTIONS
    # ---------------------------------------------------------

    def _extract_functions(self, connection) -> list[DatabaseFunction]:

        cursor = connection.cursor()

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

        functions = []

        for row in cursor.fetchall():

            functions.append(
                DatabaseFunction(
                    schema=row.schema_name,
                    name=row.function_name,
                    function_type=row.function_type,
                    definition=row.definition,
                )
            )

        return functions

    # ---------------------------------------------------------
    # DEPENDENCIES
    # ---------------------------------------------------------

    def _extract_dependencies(
        self,
        connection,
    ) -> list[DatabaseDependency]:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema,
                OBJECT_NAME(d.referencing_id) AS referencing_object,
                referenced_schema_name AS referenced_schema,
                referenced_entity_name AS referenced_object,
                o.type_desc AS referenced_type
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

        dependencies = []

        for row in cursor.fetchall():

            dependencies.append(
                DatabaseDependency(
                    referencing_schema=row.referencing_schema,
                    referencing_object=row.referencing_object,
                    referenced_schema=row.referenced_schema,
                    referenced_object=row.referenced_object,
                    referenced_type=row.referenced_type,
                )
            )

        return dependencies