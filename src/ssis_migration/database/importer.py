from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ssis_migration.database.models import (
    DatabaseColumn,
    DatabaseDependency,
    DatabaseFunction,
    DatabaseMetadata,
    DatabaseProcedure,
    DatabaseTable,
    DatabaseView,
)


def load_database_metadata(
    file_path: str | Path,
) -> DatabaseMetadata:
    file_path = Path(file_path)

    with file_path.open("r", encoding="utf-8") as file:
        data: dict[str, Any] = json.load(file)

    tables = [
        DatabaseTable(
            schema=table["schema"],
            name=table["name"],
            columns=[
                DatabaseColumn(**column)
                for column in table.get("columns", [])
            ],
        )
        for table in data.get("tables", [])
    ]

    views = [
        DatabaseView(**view)
        for view in data.get("views", [])
    ]

    procedures = [
        DatabaseProcedure(**procedure)
        for procedure in data.get("procedures", [])
    ]

    functions = [
        DatabaseFunction(**function)
        for function in data.get("functions", [])
    ]

    dependencies = [
        DatabaseDependency(**dependency)
        for dependency in data.get("dependencies", [])
    ]

    return DatabaseMetadata(
        database=data.get("database", {}),
        schemas=data.get("schemas", []),
        tables=tables,
        views=views,
        procedures=procedures,
        functions=functions,
        dependencies=dependencies,
    )