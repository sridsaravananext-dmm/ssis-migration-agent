from __future__ import annotations

from typing import Any


def extract_procedure_dependencies(
    connection,
    schema: str,
    procedure: str,
) -> list[dict[str, Any]]:

    cursor = connection.cursor()

    object_name = f"{schema}.{procedure}"

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

    return dependencies