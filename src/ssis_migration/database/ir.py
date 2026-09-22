from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SQLObjectIR:
    schema: str
    name: str
    object_type: str
    definition: str | None = None
    parameters: list[dict[str, Any]] = field(
        default_factory=list
    )
    dependencies: list[str] = field(
        default_factory=list
    )
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass
class SQLTableIR(SQLObjectIR):
    columns: list[dict[str, Any]] = field(default_factory=list)

    primary_keys: list[str] = field(default_factory=list)

    foreign_keys: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DatabaseIR:
    database: dict[str, Any] = field(default_factory=dict)

    schemas: list[str] = field(default_factory=list)

    objects: list[SQLObjectIR] = field(default_factory=list)

    tables: list[SQLTableIR] = field(default_factory=list)

    dependency_graph: dict[str, list[str]] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "schemas": self.schemas,
            "objects": [
                {
                    "schema": obj.schema,
                    "name": obj.name,
                    "object_type": obj.object_type,
                    "definition": obj.definition,
                    "parameters": obj.parameters,
                    "dependencies": obj.dependencies,
                    "metadata": obj.metadata,
                }
                for obj in self.objects
            ],
            "tables": [
                {
                    "schema": table.schema,
                    "name": table.name,
                    "object_type": table.object_type,
                    "definition": table.definition,
                    "parameters": table.parameters,
                    "dependencies": table.dependencies,
                    "metadata": table.metadata,
                    "columns": table.columns,
                    "primary_keys": table.primary_keys,
                    "foreign_keys": table.foreign_keys,
                }
                for table in self.tables
            ],
            "dependency_graph": self.dependency_graph,
        }